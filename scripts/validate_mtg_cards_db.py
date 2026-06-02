#!/usr/bin/env python3
"""
scripts/validate_mtg_cards_db.py
Valide le contenu de la base de données MTG après import Scryfall.

Usage :
  python scripts/validate_mtg_cards_db.py
"""
from __future__ import annotations

import sys
from pathlib import Path

# ── PYTHONPATH ─────────────────────────────────────────────────────────────────
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sqlalchemy import func, select, text

from src.manamind.db.engine import SessionLocal, check_connection
from src.manamind.db.models.card import Card, normalize_card_name
from src.manamind.db.models.card_face import CardFace
from src.manamind.db.models.card_price import CardPrice
from src.manamind.db.models.card_printing import CardPrinting
from src.manamind.db.models.import_run import ImportRun
from src.manamind.db.models.mtg_set import MtgSet

# ── Cartes de référence à vérifier ────────────────────────────────────────────
REFERENCE_CARDS = [
    "Sol Ring",
    "Arcane Signet",
    "Command Tower",
    "Cyclonic Rift",
    "Swords to Plowshares",
    "Path to Exile",
    "Llanowar Elves",
    "Counterspell",
    "Rhystic Study",
    "Smothering Tithe",
]

# ── Helpers d'affichage ────────────────────────────────────────────────────────
SEP = "-" * 52
SEP_THICK = "=" * 52

def _header(title: str) -> None:
    print(f"\n{SEP_THICK}")
    print(f"  {title}")
    print(SEP_THICK)

def _row(label: str, value: object, width: int = 30) -> None:
    print(f"  {label:<{width}} {value}")

def _ok(msg: str) -> None:
    print(f"  [OK]  {msg}")

def _warn(msg: str) -> None:
    print(f"  [!!]  {msg}")


# ══════════════════════════════════════════════════════════════════════════════
# VALIDATIONS
# ══════════════════════════════════════════════════════════════════════════════

def validate(session) -> bool:
    """
    Lance toutes les vérifications et retourne True si tout est OK.
    Retourne False si des anomalies critiques sont détectées.
    """
    all_ok = True

    # ── 1. Statistiques globales ───────────────────────────────────────────────
    _header("1. Statistiques globales")

    n_cards = session.scalar(select(func.count()).select_from(Card))
    n_printings = session.scalar(select(func.count()).select_from(CardPrinting))
    n_faces = session.scalar(select(func.count()).select_from(CardFace))
    n_sets = session.scalar(select(func.count()).select_from(MtgSet))
    n_prices = session.scalar(select(func.count()).select_from(CardPrice))

    _row("Cartes logiques (oracle)", f"{n_cards:,}")
    _row("Impressions (printings)", f"{n_printings:,}")
    _row("Faces (double-face)", f"{n_faces:,}")
    _row("Editions (sets)", f"{n_sets:,}")
    _row("Lignes de prix", f"{n_prices:,}")

    if n_cards == 0:
        _warn("Aucune carte en base — as-tu lancé import_scryfall_cards.py ?")
        all_ok = False

    # ── 2. Légalité Commander ─────────────────────────────────────────────────
    _header("2. Légalité Commander")

    n_legal = session.scalar(
        select(func.count()).select_from(Card).where(Card.legal_commander.is_(True))
    )
    n_illegal = (n_cards or 0) - (n_legal or 0)
    pct = (n_legal / n_cards * 100) if n_cards else 0

    _row("Légales en Commander", f"{n_legal:,}  ({pct:.1f} %)")
    _row("Non légales", f"{n_illegal:,}")

    if n_legal == 0 and n_cards > 0:
        _warn("Aucune carte légale en Commander — problème de parsing legalities ?")
        all_ok = False

    # ── 3. Couverture images ──────────────────────────────────────────────────
    _header("3. Couverture images")

    # Cartes avec au moins une impression ayant une image
    n_with_image = session.scalar(
        select(func.count(func.distinct(CardPrinting.card_id)))
        .where(CardPrinting.image_normal.isnot(None))
    )
    n_without_image = (n_cards or 0) - (n_with_image or 0)
    pct_img = (n_with_image / n_cards * 100) if n_cards else 0

    _row("Cartes avec image", f"{n_with_image:,}  ({pct_img:.1f} %)")
    _row("Cartes sans image", f"{n_without_image:,}  (tokens, digitales...)")

    # ── 4. Couverture prix ────────────────────────────────────────────────────
    _header("4. Couverture prix")

    # Impressions avec au moins un prix EUR
    n_with_eur = session.scalar(
        select(func.count(func.distinct(CardPrice.printing_id)))
        .where(CardPrice.currency == "eur")
    )
    # Impressions avec au moins un prix USD
    n_with_usd = session.scalar(
        select(func.count(func.distinct(CardPrice.printing_id)))
        .where(CardPrice.currency == "usd")
    )
    pct_eur = (n_with_eur / n_printings * 100) if n_printings else 0

    _row("Impressions avec prix EUR", f"{n_with_eur:,}  ({pct_eur:.1f} %)")
    _row("Impressions avec prix USD", f"{n_with_usd:,}")

    # ── 5. Dernier import ─────────────────────────────────────────────────────
    _header("5. Dernier import Scryfall")

    last_run = session.scalar(
        select(ImportRun).order_by(ImportRun.id.desc())
    )
    if last_run:
        elapsed = None
        if last_run.finished_at and last_run.started_at:
            elapsed = int(
                (last_run.finished_at - last_run.started_at).total_seconds()
            )
        _row("Run ID", last_run.id)
        _row("Statut", last_run.status)
        _row("Source", (last_run.source_file or "")[-60:])
        _row("Démarré le", last_run.started_at.strftime("%Y-%m-%d %H:%M UTC") if last_run.started_at else "—")
        _row("Durée", f"{elapsed}s" if elapsed else "—")
        _row("Cartes importées", f"{last_run.cards_imported:,}")
        _row("Erreurs", last_run.errors_count)

        if last_run.status != "success":
            _warn(f"Le dernier run n'est pas en succès : {last_run.status}")
            if last_run.error_message:
                _warn(f"  Erreur : {last_run.error_message[:120]}")
            all_ok = False
    else:
        _warn("Aucun run d'import enregistré.")
        all_ok = False

    # ── 6. Cartes de référence ────────────────────────────────────────────────
    _header("6. Cartes de référence")

    found = 0
    missing_cards = []

    for card_name in REFERENCE_CARDS:
        normalized = normalize_card_name(card_name)
        card = session.scalar(
            select(Card).where(Card.normalized_name == normalized)
        )
        if card:
            found += 1
            # Récupère l'image et le prix EUR de l'impression par défaut
            printing = session.scalar(
                select(CardPrinting).where(CardPrinting.card_id == card.id).limit(1)
            )
            price_row = None
            if printing:
                price_row = session.scalar(
                    select(CardPrice)
                    .where(
                        CardPrice.printing_id == printing.id,
                        CardPrice.currency == "eur",
                        CardPrice.price_type == "regular",
                    )
                    .order_by(CardPrice.date.desc())
                    .limit(1)
                )

            price_str = f"EUR {price_row.price:.2f}" if price_row and price_row.price else "prix N/D"
            has_img = "img OK" if (printing and printing.image_normal) else "sans img"
            legal = "Commander OK" if card.legal_commander else "non légale"
            print(
                f"  [OK]  {card.name:<25} | {card.mana_cost or '—':>10} "
                f"| {price_str:>10} | {has_img} | {legal}"
            )
        else:
            missing_cards.append(card_name)

    for card_name in missing_cards:
        _warn(f"Introuvable : {card_name}")
        all_ok = False

    print(f"\n  {found}/{len(REFERENCE_CARDS)} cartes de référence trouvées.")

    # ── Résumé final ──────────────────────────────────────────────────────────
    _header("Résumé")
    if all_ok:
        print("  La base est valide. Toutes les vérifications sont OK.")
    else:
        print("  Des anomalies ont été détectées (voir [!!] ci-dessus).")
    print()

    return all_ok


# ══════════════════════════════════════════════════════════════════════════════
# POINT D'ENTRÉE
# ══════════════════════════════════════════════════════════════════════════════

def main() -> None:
    if not check_connection():
        print("[ERREUR] Connexion PostgreSQL impossible.")
        print("  Vérifier DATABASE_URL dans .env et que PostgreSQL est démarré.")
        sys.exit(1)

    print(SEP_THICK)
    print("  Validation de la base MTG — ManaMind AI")
    print(SEP_THICK)

    with SessionLocal() as session:
        ok = validate(session)

    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
