#!/usr/bin/env python3
"""
scripts/import_scryfall_cards.py
Import des cartes Magic: The Gathering depuis Scryfall bulk data vers PostgreSQL.

Flux :
  1. GET https://api.scryfall.com/bulk-data  → download_uri du fichier default_cards
  2. Téléchargement → data/raw/scryfall/<filename>
  3. GET https://api.scryfall.com/sets       → upsert dans mtg_sets (FK obligatoire)
  4. Parsing streaming ijson → batches de 500 cartes :
       cards  /  card_faces  /  card_printings  /  card_prices
  5. Mise à jour import_runs (début, fin, compteurs, erreurs)

Usage :
  python scripts/import_scryfall_cards.py
  python scripts/import_scryfall_cards.py --force      # retélécharge même si fichier existant
  python scripts/import_scryfall_cards.py --dry-run    # parse et compte sans toucher la base
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import httpx
import ijson
from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session
from sqlalchemy.sql import func
from tqdm import tqdm

# ── PYTHONPATH : permet d'importer src.manamind.* depuis n'importe quel répertoire ──
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.manamind.db.engine import SessionLocal, check_connection
from src.manamind.db.models.card import Card, normalize_card_name
from src.manamind.db.models.card_face import CardFace
from src.manamind.db.models.card_price import CardPrice
from src.manamind.db.models.card_printing import CardPrinting
from src.manamind.db.models.import_run import ImportRun
from src.manamind.db.models.mtg_set import MtgSet

# ── Constantes ─────────────────────────────────────────────────────────────────
BULK_DATA_URL = "https://api.scryfall.com/bulk-data"
SETS_URL = "https://api.scryfall.com/sets"
RAW_DIR = ROOT / "data" / "raw" / "scryfall"

BATCH_SIZE = 500  # cartes traitées par transaction

# Scryfall exige un User-Agent identifiable
HTTP_HEADERS = {"User-Agent": "ManaMind/1.0 (educational project)"}

# ── Logging ────────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
    stream=sys.stdout,
)
log = logging.getLogger("import_scryfall")


# ══════════════════════════════════════════════════════════════════════════════
# 1. TÉLÉCHARGEMENT
# ══════════════════════════════════════════════════════════════════════════════

def fetch_bulk_metadata(client: httpx.Client) -> tuple[str, str, datetime]:
    """
    Appelle GET /bulk-data et retourne (download_uri, filename, updated_at)
    pour le type 'default_cards' (une carte par oracle_id, ~30 000 cartes).
    Lève ValueError si le type est introuvable.
    """
    resp = client.get(BULK_DATA_URL)
    resp.raise_for_status()

    for item in resp.json().get("data", []):
        if item.get("type") == "default_cards":
            # Scryfall retourne l'heure au format ISO 8601 avec "Z"
            updated_at = datetime.fromisoformat(
                item["updated_at"].replace("Z", "+00:00")
            )
            uri: str = item["download_uri"]
            filename = uri.rsplit("/", 1)[-1]
            return uri, filename, updated_at

    raise ValueError(
        "Type 'default_cards' introuvable dans l'API bulk-data Scryfall. "
        "Vérifie https://api.scryfall.com/bulk-data manuellement."
    )


def download_bulk_file(client: httpx.Client, url: str, dest: Path) -> None:
    """
    Télécharge le fichier JSON vers dest avec une barre de progression tqdm.
    Crée le répertoire parent si nécessaire.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)

    with client.stream("GET", url, follow_redirects=True) as resp:
        resp.raise_for_status()
        total = int(resp.headers.get("content-length", 0)) or None

        with (
            open(dest, "wb") as f,
            tqdm(total=total, unit="B", unit_scale=True, desc=dest.name) as bar,
        ):
            for chunk in resp.iter_bytes(chunk_size=65_536):
                f.write(chunk)
                bar.update(len(chunk))


# ══════════════════════════════════════════════════════════════════════════════
# 2. IMPORT DES ÉDITIONS (nécessaire avant les cartes pour la FK set_code)
# ══════════════════════════════════════════════════════════════════════════════

def import_sets(client: httpx.Client, session: Session) -> int:
    """
    Récupère toutes les éditions MTG depuis l'API Scryfall
    et les upsert dans mtg_sets.
    Doit être appelé AVANT import_cards pour satisfaire la FK card_printings.set_code.
    """
    resp = client.get(SETS_URL)
    resp.raise_for_status()
    sets_data = resp.json().get("data", [])

    rows: list[dict] = []
    for s in sets_data:
        released = None
        if raw_date := s.get("released_at"):
            try:
                released = date.fromisoformat(raw_date)
            except ValueError:
                pass

        rows.append({
            "code": s["code"],
            "name": s["name"],
            "set_type": s.get("set_type"),
            "released_at": released,
            "block": s.get("block"),
            "parent_set_code": s.get("parent_set_code"),
            "card_count": s.get("card_count"),
            "icon_svg_uri": s.get("icon_svg_uri"),
        })

    if not rows:
        return 0

    stmt = pg_insert(MtgSet).values(rows)
    stmt = stmt.on_conflict_do_update(
        index_elements=["code"],
        set_={
            "name": stmt.excluded.name,
            "set_type": stmt.excluded.set_type,
            "released_at": stmt.excluded.released_at,
            "block": stmt.excluded.block,
            "parent_set_code": stmt.excluded.parent_set_code,
            "card_count": stmt.excluded.card_count,
            "icon_svg_uri": stmt.excluded.icon_svg_uri,
        },
    )
    session.execute(stmt)
    session.commit()
    return len(rows)


# ══════════════════════════════════════════════════════════════════════════════
# 3. PARSEURS  (dict Scryfall → dict pour SQLAlchemy)
# ══════════════════════════════════════════════════════════════════════════════

def _parse_card_row(raw: dict[str, Any]) -> dict[str, Any] | None:
    """
    Construit le dict pour la table 'cards' depuis un objet Scryfall.

    Points d'attention :
    - oracle_id absent → carte invalide, retourne None
    - mana_cost absent au niveau racine sur les DFC → fallback sur la face 0
    - legalities.commander == "legal" → legal_commander = True
    """
    oracle_id = raw.get("oracle_id")
    if not oracle_id:
        return None

    name = raw.get("name", "")
    legalities = raw.get("legalities") or {}

    # Sur les DFC (ex: Delver of Secrets // Insectile Aberration),
    # mana_cost est absent à la racine — il faut le lire depuis la première face
    mana_cost = raw.get("mana_cost") or None
    if not mana_cost:
        faces = raw.get("card_faces") or []
        if faces:
            mana_cost = faces[0].get("mana_cost") or None

    return {
        "oracle_id": oracle_id,
        "name": name,
        "normalized_name": normalize_card_name(name),
        "mana_cost": mana_cost,
        "mana_value": raw.get("cmc"),
        "type_line": raw.get("type_line"),
        "oracle_text": raw.get("oracle_text"),
        "power": raw.get("power"),
        "toughness": raw.get("toughness"),
        "loyalty": raw.get("loyalty"),
        "defense": raw.get("defense"),
        "colors": raw.get("colors") or [],
        "color_identity": raw.get("color_identity") or [],
        "keywords": raw.get("keywords") or [],
        "legal_commander": legalities.get("commander") == "legal",
        "edhrec_rank": raw.get("edhrec_rank"),
    }


def _parse_face_rows(raw: dict[str, Any], card_id: int) -> list[dict[str, Any]]:
    """
    Parse le tableau 'card_faces' d'un objet Scryfall.
    Retourne [] si la carte n'est pas multi-face.
    Les faces n'ont pas de clé stable → elles sont supprimées et réinsérées à chaque import.
    """
    faces_data = raw.get("card_faces") or []
    rows = []
    for face in faces_data:
        img = face.get("image_uris") or {}
        rows.append({
            "card_id": card_id,
            "face_name": face.get("name", ""),
            "mana_cost": face.get("mana_cost") or None,
            "type_line": face.get("type_line"),
            "oracle_text": face.get("oracle_text"),
            "power": face.get("power"),
            "toughness": face.get("toughness"),
            "loyalty": face.get("loyalty"),
            "defense": face.get("defense"),
            "colors": face.get("colors") or [],
            "image_small": img.get("small"),
            "image_normal": img.get("normal"),
            "image_large": img.get("large"),
        })
    return rows


def _parse_printing_row(raw: dict[str, Any], card_id: int) -> dict[str, Any]:
    """
    Construit le dict pour la table 'card_printings'.

    Pour les DFC, image_uris est absent à la racine → on prend les images de la face 0.
    Pour les cartes sans image (digitales, tokens...) tous les champs image_* sont None.
    """
    img = raw.get("image_uris") or {}
    if not img:
        # DFC : images par face uniquement
        faces = raw.get("card_faces") or []
        if faces:
            img = faces[0].get("image_uris") or {}

    released_at = None
    if raw_date := raw.get("released_at"):
        try:
            released_at = date.fromisoformat(raw_date)
        except ValueError:
            pass

    return {
        "scryfall_id": raw["id"],
        "oracle_id": raw.get("oracle_id", ""),
        "card_id": card_id,
        "set_code": raw.get("set"),          # FK vers mtg_sets.code (nullable)
        "collector_number": raw.get("collector_number"),
        "lang": raw.get("lang"),
        "rarity": raw.get("rarity"),
        "released_at": released_at,
        "artist": raw.get("artist"),
        "border_color": raw.get("border_color"),
        "frame": raw.get("frame"),
        "full_art": bool(raw.get("full_art")),
        "promo": bool(raw.get("promo")),
        "reprint": bool(raw.get("reprint")),
        "digital": bool(raw.get("digital")),
        "image_small": img.get("small"),
        "image_normal": img.get("normal"),
        "image_large": img.get("large"),
        "scryfall_uri": raw.get("scryfall_uri"),
    }


def _parse_price_rows(
    prices: dict[str, Any], printing_id: int, today: date
) -> list[dict[str, Any]]:
    """
    Extrait les prix depuis le dict 'prices' d'un objet Scryfall.
    Ignore silencieusement les prix null ou non convertibles en float.
    """
    rows = []
    candidates = [
        ("eur", "regular", prices.get("eur")),
        ("eur", "foil",    prices.get("eur_foil")),
        ("usd", "regular", prices.get("usd")),
        ("usd", "foil",    prices.get("usd_foil")),
        ("tix", "regular", prices.get("tix")),
    ]
    for currency, price_type, price_str in candidates:
        if not price_str:
            continue
        try:
            rows.append({
                "printing_id": printing_id,
                "source": "scryfall",
                "currency": currency,
                "price_type": price_type,
                "price": float(price_str),
                "date": today,
            })
        except (ValueError, TypeError):
            pass
    return rows


# ══════════════════════════════════════════════════════════════════════════════
# 4. UPSERTS EN BASE (fonctions de bas niveau)
# ══════════════════════════════════════════════════════════════════════════════

def _upsert_cards(session: Session, rows: list[dict]) -> dict[str, int]:
    """
    INSERT ... ON CONFLICT (oracle_id) DO UPDATE.
    Retourne {oracle_id: card.id} — indispensable pour résoudre les FK faces/printings.
    updated_at est mis à jour explicitement (onupdate= ne fonctionne pas sur les bulk inserts).
    """
    stmt = pg_insert(Card).values(rows)
    stmt = stmt.on_conflict_do_update(
        index_elements=["oracle_id"],
        set_={
            "name":             stmt.excluded.name,
            "normalized_name":  stmt.excluded.normalized_name,
            "mana_cost":        stmt.excluded.mana_cost,
            "mana_value":       stmt.excluded.mana_value,
            "type_line":        stmt.excluded.type_line,
            "oracle_text":      stmt.excluded.oracle_text,
            "power":            stmt.excluded.power,
            "toughness":        stmt.excluded.toughness,
            "loyalty":          stmt.excluded.loyalty,
            "defense":          stmt.excluded.defense,
            "colors":           stmt.excluded.colors,
            "color_identity":   stmt.excluded.color_identity,
            "keywords":         stmt.excluded.keywords,
            "legal_commander":  stmt.excluded.legal_commander,
            "edhrec_rank":      stmt.excluded.edhrec_rank,
            "updated_at":       func.now(),
        },
    )
    session.execute(stmt)

    # Récupère les IDs des cartes qu'on vient d'insérer/mettre à jour
    oracle_ids = [r["oracle_id"] for r in rows]
    result = session.execute(
        select(Card.id, Card.oracle_id).where(Card.oracle_id.in_(oracle_ids))
    )
    return {row.oracle_id: row.id for row in result}


def _replace_faces(
    session: Session, face_rows: list[dict], card_ids: list[int]
) -> None:
    """
    Supprime toutes les faces existantes pour ces card_ids puis réinsère.
    Stratégie 'delete + insert' car Scryfall n'a pas de clé stable par face.
    """
    session.execute(delete(CardFace).where(CardFace.card_id.in_(card_ids)))
    if face_rows:
        session.execute(pg_insert(CardFace).values(face_rows))


def _upsert_printings(session: Session, rows: list[dict]) -> dict[str, int]:
    """
    INSERT ... ON CONFLICT (scryfall_id) DO UPDATE.
    Retourne {scryfall_id: printing.id} pour l'insertion des prix.
    """
    update_cols = [
        "oracle_id", "card_id", "set_code", "collector_number", "lang",
        "rarity", "released_at", "artist", "border_color", "frame",
        "full_art", "promo", "reprint", "digital",
        "image_small", "image_normal", "image_large", "scryfall_uri",
    ]
    stmt = pg_insert(CardPrinting).values(rows)
    stmt = stmt.on_conflict_do_update(
        index_elements=["scryfall_id"],
        set_={col: getattr(stmt.excluded, col) for col in update_cols},
    )
    session.execute(stmt)

    scryfall_ids = [r["scryfall_id"] for r in rows]
    result = session.execute(
        select(CardPrinting.id, CardPrinting.scryfall_id)
        .where(CardPrinting.scryfall_id.in_(scryfall_ids))
    )
    return {row.scryfall_id: row.id for row in result}


def _insert_prices(session: Session, rows: list[dict]) -> None:
    """
    INSERT ... ON CONFLICT DO NOTHING (idempotent grâce à uq_card_prices_printing_date_type).
    Permet de relancer l'import le même jour sans créer de doublons de prix.
    """
    if not rows:
        return
    stmt = pg_insert(CardPrice).values(rows)
    stmt = stmt.on_conflict_do_nothing()
    session.execute(stmt)


# ══════════════════════════════════════════════════════════════════════════════
# 5. TRAITEMENT PAR BATCH
# ══════════════════════════════════════════════════════════════════════════════

def _flush_batch(
    session: Session,
    card_rows: list[dict],
    raw_cards: list[dict[str, Any]],
    today: date,
) -> tuple[int, int]:
    """
    Traite un batch complet dans une seule transaction :
      upsert cards → replace faces → upsert printings → insert prices → commit.
    Retourne (n_cards, n_printings).
    """
    # Dédoublonnage par oracle_id au sein du batch
    # (default_cards peut contenir des doublons d'oracle_id pour certaines cartes)
    seen: dict[str, int] = {}
    for i, row in enumerate(card_rows):
        seen[row["oracle_id"]] = i
    dedup_idx = sorted(seen.values())
    card_rows = [card_rows[i] for i in dedup_idx]
    raw_cards = [raw_cards[i] for i in dedup_idx]

    # Dédoublonnage par scryfall_id pour les impressions
    seen_sid: dict[str, int] = {}
    for i, raw in enumerate(raw_cards):
        seen_sid[raw["id"]] = i
    raw_cards = [raw_cards[i] for i in sorted(seen_sid.values())]

    # 1. Cartes (récupère les IDs pour les FK)
    oracle_to_id = _upsert_cards(session, card_rows)

    # 2. Prépare faces, impressions et prix pour tout le batch
    face_rows: list[dict] = []
    printing_rows: list[dict] = []
    card_ids_with_faces: list[int] = []
    raw_prices: dict[str, dict] = {}      # scryfall_id → prices dict

    for raw in raw_cards:
        oracle_id = raw.get("oracle_id")
        card_id = oracle_to_id.get(oracle_id)
        if card_id is None:
            continue  # ne devrait pas arriver, mais on protège

        faces = _parse_face_rows(raw, card_id)
        if faces:
            face_rows.extend(faces)
            card_ids_with_faces.append(card_id)

        printing_rows.append(_parse_printing_row(raw, card_id))
        raw_prices[raw["id"]] = raw.get("prices") or {}

    # 3. Faces (delete + insert)
    if card_ids_with_faces:
        _replace_faces(session, face_rows, card_ids_with_faces)

    # 4. Impressions (upsert) → récupère les IDs pour les prix
    scryfall_to_printing_id = _upsert_printings(session, printing_rows)

    # 5. Prix (append-only, idempotent)
    price_rows: list[dict] = []
    for scryfall_id, prices_dict in raw_prices.items():
        pid = scryfall_to_printing_id.get(scryfall_id)
        if pid is not None:
            price_rows.extend(_parse_price_rows(prices_dict, pid, today))
    _insert_prices(session, price_rows)

    session.commit()
    return len(card_rows), len(printing_rows)


def import_cards(file_path: Path, session: Session) -> tuple[int, int, int]:
    """
    Parse le fichier JSON Scryfall en streaming (ijson) et insère par batches.

    Garanties :
    - Une erreur de parsing sur une carte individuelle ne plante pas l'import.
    - Une erreur SQL sur un batch est loguée ; le batch est ignoré et l'import continue.
    - L'import est relançable sans créer de doublons (upserts).

    Retourne (cards_imported, printings_imported, errors_count).
    """
    today = date.today()
    cards_imported = 0
    printings_imported = 0
    errors_count = 0

    card_rows_buf: list[dict] = []
    raw_cards_buf: list[dict] = []

    file_size_mb = file_path.stat().st_size / 1_048_576
    log.info(f"Fichier : {file_path.name} ({file_size_mb:.0f} Mo)")

    with open(file_path, "rb") as f:
        for raw_card in ijson.items(f, "item"):

            # ── Parsing individuel (protégé par try/except) ──────────────────
            try:
                card_row = _parse_card_row(raw_card)
                if card_row is None:
                    continue
                card_rows_buf.append(card_row)
                raw_cards_buf.append(raw_card)
            except Exception as exc:
                errors_count += 1
                log.warning(
                    f"[PARSE ERROR] '{raw_card.get('name', '?')}' "
                    f"({raw_card.get('id', '?')}): {exc}"
                )
                continue

            # ── Flush du batch toutes les BATCH_SIZE cartes ──────────────────
            if len(card_rows_buf) >= BATCH_SIZE:
                try:
                    c, p = _flush_batch(session, card_rows_buf, raw_cards_buf, today)
                    cards_imported += c
                    printings_imported += p
                except Exception as exc:
                    log.error(
                        f"[BATCH ERROR] cards {cards_imported}–"
                        f"{cards_imported + BATCH_SIZE}: {exc}"
                    )
                    session.rollback()
                    errors_count += len(card_rows_buf)
                finally:
                    card_rows_buf = []
                    raw_cards_buf = []

                # Progression toutes les 2 000 cartes
                if cards_imported > 0 and cards_imported % 2_000 == 0:
                    log.info(
                        f"  -> {cards_imported:>6,} cartes  |  "
                        f"{printings_imported:>6,} impressions  |  "
                        f"{errors_count} erreurs"
                    )

    # ── Dernier batch (inférieur à BATCH_SIZE) ────────────────────────────────
    if card_rows_buf:
        try:
            c, p = _flush_batch(session, card_rows_buf, raw_cards_buf, today)
            cards_imported += c
            printings_imported += p
        except Exception as exc:
            log.error(f"[BATCH ERROR] dernier batch : {exc}")
            session.rollback()
            errors_count += len(card_rows_buf)

    return cards_imported, printings_imported, errors_count


# ══════════════════════════════════════════════════════════════════════════════
# 6. POINT D'ENTRÉE
# ══════════════════════════════════════════════════════════════════════════════

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Importe les cartes MTG depuis Scryfall bulk data vers PostgreSQL.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Exemples :
  python scripts/import_scryfall_cards.py
  python scripts/import_scryfall_cards.py --force
  python scripts/import_scryfall_cards.py --dry-run
        """,
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Retélécharge le fichier Scryfall même s'il existe déjà localement.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Télécharge et parse sans insérer en base. Ne nécessite pas PostgreSQL.",
    )
    args = parser.parse_args()

    # ── Vérification connexion (sauf dry-run) ─────────────────────────────────
    if not args.dry_run and not check_connection():
        log.error(
            "Connexion PostgreSQL impossible.\n"
            "  1. Vérifier que PostgreSQL est démarré\n"
            "  2. Vérifier DATABASE_URL dans .env\n"
            "  3. Consulter docs/DATABASE_SETUP.md"
        )
        sys.exit(1)

    with httpx.Client(
        timeout=httpx.Timeout(30.0, read=300.0),
        headers=HTTP_HEADERS,
        follow_redirects=True,
    ) as client:

        # ── 1. Métadonnées ────────────────────────────────────────────────────
        log.info("Récupération des métadonnées Scryfall bulk-data...")
        try:
            download_uri, filename, source_updated_at = fetch_bulk_metadata(client)
        except Exception as exc:
            log.error(f"Impossible de contacter l'API Scryfall : {exc}")
            sys.exit(1)

        log.info(f"  Source     : {filename}")
        log.info(f"  Scryfall   : mis à jour le {source_updated_at.strftime('%Y-%m-%d %H:%M UTC')}")

        # ── 2. Téléchargement ─────────────────────────────────────────────────
        dest = RAW_DIR / filename
        if dest.exists() and not args.force:
            log.info(
                f"Fichier déjà présent : {dest.name} "
                f"({dest.stat().st_size / 1_048_576:.0f} Mo). "
                "Utilise --force pour retélécharger."
            )
        else:
            log.info(f"Téléchargement vers {dest} ...")
            download_bulk_file(client, download_uri, dest)

        # ── 3. Dry-run : on parse et on compte, sans base ─────────────────────
        if args.dry_run:
            log.info("[DRY-RUN] Comptage des objets dans le fichier (sans insertion)...")
            count = 0
            with open(dest, "rb") as f:
                for _ in ijson.items(f, "item"):
                    count += 1
                    if count % 5_000 == 0:
                        log.info(f"  {count:,} objets parsés...")
            log.info(f"[DRY-RUN] Total : {count:,} objets. Aucune insertion effectuée.")
            return

        # ── 4. Import en base ─────────────────────────────────────────────────
        with SessionLocal() as session:

            # Création du run d'import pour la traçabilité
            run = ImportRun(
                source="scryfall",
                source_file=download_uri,
                source_updated_at=source_updated_at,
                status="running",
            )
            session.add(run)
            session.commit()
            session.refresh(run)
            log.info(f"Import run #{run.id} démarré.")

            started_at = datetime.now(timezone.utc)
            try:
                # ── Éditions (FK obligatoire avant les impressions) ────────────
                log.info("Import des éditions MTG depuis Scryfall...")
                n_sets = import_sets(client, session)
                log.info(f"  {n_sets} éditions importées/mises à jour.")

                # ── Cartes (streaming) ─────────────────────────────────────────
                log.info("Import des cartes (streaming, batches de 500)...")
                cards_n, printings_n, errors_n = import_cards(dest, session)

                # ── Finalisation du run ────────────────────────────────────────
                elapsed = int(
                    (datetime.now(timezone.utc) - started_at).total_seconds()
                )
                run.status = "success"
                run.finished_at = datetime.now(timezone.utc)
                run.cards_imported = cards_n
                run.printings_imported = printings_n
                run.errors_count = errors_n
                session.commit()

                # ── Résumé final ───────────────────────────────────────────────
                log.info("")
                log.info("=" * 47)
                log.info("  Import Scryfall termine avec succes")
                log.info("=" * 47)
                log.info(f"  Cartes importees   : {cards_n:>10,}")
                log.info(f"  Impressions        : {printings_n:>10,}")
                log.info(f"  Editions           : {n_sets:>10,}")
                log.info(f"  Erreurs ignorees   : {errors_n:>10}")
                log.info(f"  Duree totale       : {elapsed:>9}s")
                log.info(f"  Import run ID      : {run.id:>10}")
                log.info("=" * 47)

            except Exception as exc:
                # Erreur fatale non récupérée → on marque le run comme failed
                elapsed = int(
                    (datetime.now(timezone.utc) - started_at).total_seconds()
                )
                log.error(f"Erreur fatale apres {elapsed}s : {exc}", exc_info=True)
                run.status = "failed"
                run.finished_at = datetime.now(timezone.utc)
                run.error_message = str(exc)
                session.commit()
                sys.exit(1)


if __name__ == "__main__":
    main()
