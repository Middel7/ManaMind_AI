"""Decks préconstruits officiels : import depuis MTGJSON et ajout aux decks d'un joueur.

Saisir cent cartes à la main décourage : la plupart des joueurs commencent
pourtant avec des decks Commander vendus tout faits, dont la liste est connue.
MTGJSON (licence MIT, citée dans les mentions légales) publie chacun d'eux, avec
l'identifiant Scryfall de chaque carte — le même que celui de notre catalogue,
si bien qu'aucun rapprochement par nom n'est nécessaire.

Deux usages :

  - l'import, lancé depuis l'administration (`python -m manamind.precons`),
    télécharge l'index des decks puis seulement ceux qui manquent en base ;
  - l'ajout, en un clic, crée un deck du joueur et peut verser les cartes de la
    boîte — impressions et foils exacts — dans sa collection.
"""
from __future__ import annotations

import argparse
import sys
import uuid
from collections import defaultdict
from collections.abc import Callable
from datetime import date

from sqlalchemy import Row, text
from sqlalchemy.orm import Session

from manamind.commanders import MAX_COMMANDERS, join_commanders
from manamind.db.engine import SessionLocal

MTGJSON_API = "https://mtgjson.com/api/v5"

# Le seul type retenu : l'application sert le format Commander. Les autres
# (Duel Decks, Starter Kits…) n'y auraient aucun commandant.
DECK_TYPE = "Commander Deck"

# Langue des exemplaires versés dans la collection : celle des listes MTGJSON.
_LANGUAGE = "en"


# ── Lecture d'un deck MTGJSON ────────────────────────────────────────────────

def _card_name(raw: str) -> str:
    """Le nom d'une carte, ramené à elle-même pour les cartes réversibles.

    Les Secret Lair « réversibles » impriment deux fois la même carte, une
    illustration par face : MTGJSON les écrit « Sol Ring // Sol Ring », nom
    qu'aucun écran ne reconnaît. C'est pourtant un Sol Ring.
    """
    faces = [face.strip() for face in raw.split(" // ")]
    return faces[0] if len(faces) == 2 and faces[0] == faces[1] else raw


def _card_line(card: dict, zone: str) -> dict:
    identifiers = card.get("identifiers") or {}
    return {
        "zone": zone,
        "name": _card_name(card.get("name") or ""),
        "scryfall_id": identifiers.get("scryfallId"),
        "set_code": (card.get("setCode") or "").upper() or None,
        "collector_number": card.get("number"),
        "quantity": max(1, int(card.get("count") or 1)),
        "is_foil": bool(card.get("isFoil")),
        "is_background": "Background" in (card.get("subtypes") or []),
    }


def parse_deck(data: dict) -> dict:
    """Un deck MTGJSON réduit à ce que l'application en garde.

    Fonction pure, testée sans réseau. Le ou les commandants viennent de la
    zone `commander` ; quand elle en porte plus que le format n'en admet (une
    boîte qui propose plusieurs commandants au choix), ceux mis en avant sur la
    boîte (`displayCommander`) l'emportent, et les autres rejoignent le deck.
    """
    commanders = [_card_line(c, "commander") for c in data.get("commander") or []]
    display = {c.get("name") for c in data.get("displayCommander") or []}
    main = [_card_line(c, "main") for c in data.get("mainBoard") or []]

    if len(commanders) > MAX_COMMANDERS:
        chosen = [c for c in commanders if c["name"] in display][:MAX_COMMANDERS] \
            or commanders[:1]
        for card in commanders:
            if card not in chosen:
                main.append({**card, "zone": "main"})
        commanders = chosen

    tokens = [_card_line(t, "token") for t in data.get("tokens") or []]
    return {
        "name": data.get("name") or "",
        "set_code": (data.get("code") or "").upper(),
        "release_date": data.get("releaseDate"),
        "commanders": commanders,
        "main": main,
        "tokens": tokens,
    }


# ── Import ───────────────────────────────────────────────────────────────────

def _catalogue_names(session: Session, scryfall_ids: list[str]) -> dict[str, str]:
    """Nom de carte du catalogue pour chaque impression.

    C'est sous ce nom que les écrans retrouvent une carte de deck : MTGJSON
    écrit parfois autrement une carte à deux faces ou à aventure.
    """
    if not scryfall_ids:
        return {}
    rows = session.execute(text("""
        SELECT p.scryfall_id::text AS sid, c.name
        FROM scryfall_card_printings p
        JOIN scryfall_cards c ON c.id = p.card_id
        WHERE p.scryfall_id::text = ANY(:ids)
    """), {"ids": scryfall_ids}).fetchall()
    return {r.sid: r.name for r in rows}


def _store_deck(session: Session, precon_id: str, deck: dict, source_url: str | None) -> int:
    """Écrit (ou réécrit) un précon et ses cartes. Retourne le nombre de cartes."""
    lines = deck["commanders"] + deck["main"] + deck["tokens"]
    names = _catalogue_names(session, [ln["scryfall_id"] for ln in lines if ln["scryfall_id"]])
    for line in lines:
        line["name"] = _card_name(names.get(line["scryfall_id"] or "", line["name"]))

    commander = join_commanders(c["name"] for c in deck["commanders"])
    # Illustration du précon : son commandant principal, pas un Background.
    lead = next((c for c in deck["commanders"] if not c["is_background"]),
                deck["commanders"][0] if deck["commanders"] else None)
    card_count = sum(ln["quantity"] for ln in deck["commanders"] + deck["main"])

    session.execute(text("DELETE FROM precon_decks WHERE id = :id"), {"id": precon_id})
    session.execute(text("""
        INSERT INTO precon_decks (id, name, set_code, release_date, commander,
                                  lead_scryfall_id, card_count, source_url)
        VALUES (:id, :name, :set, :released, :cmd, :lead, :count, :src)
    """), {
        "id": precon_id, "name": deck["name"], "set": deck["set_code"],
        "released": date.fromisoformat(deck["release_date"]) if deck["release_date"] else None,
        "cmd": commander, "lead": lead["scryfall_id"] if lead else None,
        "count": card_count, "src": source_url,
    })
    session.execute(text("""
        INSERT INTO precon_deck_cards (precon_id, zone, card_name, scryfall_id,
                                       set_code, collector_number, quantity, is_foil)
        VALUES (:pid, :zone, :name, :sid, :set, :num, :qty, :foil)
    """), [{
        "pid": precon_id, "zone": ln["zone"], "name": ln["name"], "sid": ln["scryfall_id"],
        "set": ln["set_code"], "num": ln["collector_number"], "qty": ln["quantity"],
        "foil": ln["is_foil"],
    } for ln in lines if ln["name"]])
    return card_count


def refresh(*, force: bool = False, log: Callable[[str], None] = print) -> dict:
    """Télécharge les précons Commander absents de la base (tous avec `force`)."""
    import httpx

    with httpx.Client(timeout=60, follow_redirects=True,
                      headers={"User-Agent": "ManaMind (manamind.eu)"}) as client:
        log("Lecture de l'index des decks MTGJSON…")
        index = client.get(f"{MTGJSON_API}/DeckList.json").raise_for_status().json()["data"]
        wanted = [d for d in index if d.get("type") == DECK_TYPE]

        with SessionLocal() as session:
            known = {r[0] for r in session.execute(text("SELECT id FROM precon_decks"))}
        todo = wanted if force else [d for d in wanted if d["fileName"] not in known]
        log(f"{len(wanted)} decks Commander publiés, {len(known)} déjà en base, "
            f"{len(todo)} à télécharger.")

        done, failed = 0, []
        for i, entry in enumerate(todo, 1):
            file_name = entry["fileName"]
            try:
                payload = client.get(f"{MTGJSON_API}/decks/{file_name}.json") \
                    .raise_for_status().json()["data"]
                deck = parse_deck(payload)
                if not deck["commanders"]:
                    log(f"[{i}/{len(todo)}] {entry['name']} : aucun commandant, ignoré.")
                    continue
                with SessionLocal() as session:
                    count = _store_deck(session, file_name, deck, entry.get("source"))
                    session.commit()
                done += 1
                log(f"[{i}/{len(todo)}] {deck['name']} ({deck['set_code']}) : {count} cartes.")
            except Exception as exc:  # noqa: BLE001 — un deck en échec n'arrête pas les autres
                failed.append(file_name)
                log(f"[{i}/{len(todo)}] {entry['name']} : échec ({exc}).")

    log(f"Terminé : {done} decks importés, {len(failed)} en échec.")
    return {"published": len(wanted), "imported": done, "failed": failed}


# ── Ajout aux decks d'un joueur ──────────────────────────────────────────────

def list_precons(user_id: int) -> list[dict]:
    """Les précons, du plus récent au plus ancien, avec ce qu'en sait le joueur."""
    with SessionLocal() as session:
        rows = session.execute(text("""
            SELECT pd.id, pd.name, pd.set_code, pd.release_date, pd.commander,
                   pd.lead_scryfall_id, pd.card_count,
                   ms.name AS set_name,
                   lead.color_identity,
                   EXISTS (
                       SELECT 1 FROM user_moxfield_decks d
                       WHERE d.user_id = :uid AND d.name = pd.name
                         AND d.commander = pd.commander
                   ) AS added
            FROM precon_decks pd
            LEFT JOIN scryfall_mtg_sets ms ON LOWER(ms.code) = LOWER(pd.set_code)
            LEFT JOIN LATERAL (
                SELECT c.color_identity
                FROM scryfall_card_printings p
                JOIN scryfall_cards c ON c.id = p.card_id
                WHERE p.scryfall_id::text = pd.lead_scryfall_id
                LIMIT 1
            ) lead ON TRUE
            ORDER BY pd.release_date DESC NULLS LAST, pd.name
        """), {"uid": user_id}).fetchall()
    return [{
        "id": r.id,
        "name": r.name,
        "set_code": r.set_code,
        "set_name": r.set_name or r.set_code,
        "release_date": r.release_date.isoformat() if r.release_date else None,
        "commander": r.commander,
        "lead_scryfall_id": r.lead_scryfall_id,
        "colors": list(r.color_identity or []),
        "card_count": r.card_count,
        "added": bool(r.added),
    } for r in rows]


def add_precon(user_id: int, precon_id: str, *, add_to_collection: bool) -> dict | None:
    """Crée un deck du joueur à partir d'un précon. None si le précon est inconnu.

    Tout se fait dans une transaction : un deck à moitié créé, ou une
    collection garnie d'un deck qui n'existe pas, serait pire qu'un échec net.

    Avec `add_to_collection`, les cartes entrent dans la collection sous leur
    impression exacte (édition, numéro, foil), et les jetons de la boîte sont
    marqués comme mis de côté pour ce deck.
    """
    from manamind.collection_store import invalidate_stats

    with SessionLocal() as session:
        precon = session.execute(text("""
            SELECT id, name, commander FROM precon_decks WHERE id = :id
        """), {"id": precon_id}).fetchone()
        if precon is None:
            return None
        lines = session.execute(text("""
            SELECT zone, card_name, scryfall_id, set_code, collector_number,
                   quantity, is_foil
            FROM precon_deck_cards WHERE precon_id = :id ORDER BY id
        """), {"id": precon_id}).fetchall()

        deck_id = f"precon-{uuid.uuid4().hex[:12]}"
        session.execute(text("""
            INSERT INTO user_moxfield_decks (user_id, deck_id, moxfield_url, commander, name)
            VALUES (:uid, :did, '', :cmd, :name)
        """), {"uid": user_id, "did": deck_id, "cmd": precon.commander, "name": precon.name})

        # Une ligne par nom de carte, comme l'impose user_deck_cards : les
        # terrains de base d'un précon arrivent souvent en plusieurs
        # impressions, qui ne font qu'une carte pour le deck.
        per_name: dict[str, int] = defaultdict(int)
        for line in lines:
            if line.zone != "token":
                per_name[line.card_name] += line.quantity
        session.execute(text("""
            INSERT INTO user_deck_cards (user_id, deck_id, commander, card_name, quantity)
            VALUES (:uid, :did, :cmd, :name, :qty)
        """), [{"uid": user_id, "did": deck_id, "cmd": precon.commander,
                "name": name, "qty": qty} for name, qty in per_name.items()])

        copies = 0
        if add_to_collection:
            copies = _add_to_collection(session, user_id,
                                        [ln for ln in lines if ln.zone != "token"])
            _set_aside_tokens(session, user_id, deck_id,
                              [ln for ln in lines if ln.zone == "token"])
        session.commit()

    if add_to_collection:
        invalidate_stats(user_id)
    return {"deck_id": deck_id, "name": precon.name, "cards": sum(per_name.values()),
            "collection_copies": copies}


def _add_to_collection(session: Session, user_id: int, lines: list[Row]) -> int:
    """Verse les cartes dans la collection, impression par impression.

    Même clé de fusion que l'ajout manuel (collection_store.add_item) : une
    carte déjà possédée dans cette impression voit sa quantité augmenter.
    """
    per_printing: dict[tuple[str, bool], int] = defaultdict(int)
    for line in lines:
        if line.scryfall_id:
            per_printing[(line.scryfall_id, bool(line.is_foil))] += line.quantity
    if not per_printing:
        return 0

    printings = {
        r.sid: r for r in session.execute(text("""
            SELECT p.scryfall_id::text AS sid, p.id AS printing_id, p.card_id,
                   UPPER(p.set_code) AS set_code, p.collector_number, c.name
            FROM scryfall_card_printings p
            JOIN scryfall_cards c ON c.id = p.card_id
            WHERE p.scryfall_id::text = ANY(:ids)
        """), {"ids": list({sid for sid, _ in per_printing})})
    }

    copies = 0
    for (sid, foil), qty in per_printing.items():
        printing = printings.get(sid)
        if printing is None:
            continue
        session.execute(text("""
            INSERT INTO user_collection (
                user_id, card_name, quantity, set_code, collector_number,
                finish, language, scryfall_id, card_id, printing_id, raw_line,
                added_at, updated_at
            ) VALUES (
                :uid, :name, :qty, :set, :num, :finish, :lang, :sid,
                :card_id, :printing_id, :raw, NOW(), NOW()
            )
            ON CONFLICT (user_id, LOWER(TRIM(card_name)), COALESCE(set_code, ''),
                         COALESCE(collector_number, ''), finish, language,
                         COALESCE(condition, ''))
            DO UPDATE SET quantity   = user_collection.quantity + EXCLUDED.quantity,
                          updated_at = NOW(),
                          scryfall_id = COALESCE(user_collection.scryfall_id, EXCLUDED.scryfall_id),
                          card_id     = COALESCE(user_collection.card_id, EXCLUDED.card_id),
                          printing_id = COALESCE(user_collection.printing_id, EXCLUDED.printing_id)
        """), {
            "uid": user_id, "name": printing.name, "qty": qty,
            "set": printing.set_code, "num": printing.collector_number,
            "finish": "foil" if foil else "nonfoil", "lang": _LANGUAGE,
            "sid": sid, "card_id": printing.card_id, "printing_id": printing.printing_id,
            "raw": f"{qty} {printing.name} ({printing.set_code})",
        })
        copies += qty
    return copies


def _set_aside_tokens(session: Session, user_id: int, deck_id: str, lines: list[Row]) -> None:
    """Marque les jetons fournis dans la boîte comme mis de côté pour ce deck.

    La clé d'un jeton est son oracle_id, comme dans l'écran des jetons du deck :
    c'est ainsi qu'il y apparaît « ajouté » face au jeton que réclame la carte.
    """
    per_key: dict[str, dict] = {}
    for line in lines:
        row = session.execute(text("""
            SELECT c.oracle_id::text AS oracle_id, c.name, c.type_line
            FROM scryfall_card_printings p
            JOIN scryfall_cards c ON c.id = p.card_id
            WHERE p.scryfall_id::text = :sid
        """), {"sid": line.scryfall_id or ""}).fetchone()
        key = row.oracle_id if row and row.oracle_id else line.card_name.lower()
        entry = per_key.setdefault(key, {
            "name": row.name if row else line.card_name,
            "type_line": row.type_line if row else None,
            "qty": 0,
        })
        entry["qty"] += line.quantity
    for key, entry in per_key.items():
        session.execute(text("""
            INSERT INTO user_deck_tokens
                (user_id, deck_id, token_key, token_name, token_type_line, quantity)
            VALUES (:uid, :did, :key, :name, :type_line, :qty)
            ON CONFLICT (user_id, deck_id, token_key) DO UPDATE
               SET quantity = EXCLUDED.quantity
        """), {"uid": user_id, "did": deck_id, "key": key, "name": entry["name"],
               "type_line": entry["type_line"], "qty": min(entry["qty"], 99)})


def main() -> None:
    parser = argparse.ArgumentParser(description="Importe les decks Commander préconstruits")
    parser.add_argument("--force", action="store_true",
                        help="Retélécharge aussi les decks déjà en base")
    args = parser.parse_args()
    result = refresh(force=args.force, log=lambda m: print(m, flush=True))
    sys.exit(1 if result["failed"] and not result["imported"] else 0)


if __name__ == "__main__":
    main()
