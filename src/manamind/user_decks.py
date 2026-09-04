"""
user_decks.py — Couche DB pour les decks et collection par utilisateur.
Remplace les fichiers JSON/TXT de moxfield_client pour le multi-user.
"""
from __future__ import annotations

import json
import time
import unicodedata
from typing import Optional

from sqlalchemy import text

from manamind.db.engine import SessionLocal


def _normalize(name: str) -> str:
    name = unicodedata.normalize("NFKD", name)
    return "".join(c for c in name if not unicodedata.combining(c)).lower().strip()


# ── Config decks (remplace moxfield_decks.json) ───────────────────────────────

def load_config_for_user(user_id: int) -> list[dict]:
    with SessionLocal() as sess:
        rows = sess.execute(text("""
            SELECT deck_id, moxfield_url AS url, commander, name, locally_modified, fetched_at
            FROM user_moxfield_decks
            WHERE user_id = :uid
            ORDER BY commander
        """), {"uid": user_id}).fetchall()
        decks = [dict(r._mapping) for r in rows]

        # Commandants dans user_deck_cards sans entrée dans user_moxfield_decks
        known_commanders = {d["commander"] for d in decks}
        orphan_rows = sess.execute(text("""
            SELECT DISTINCT commander FROM user_deck_cards
            WHERE user_id = :uid AND commander IS NOT NULL
            ORDER BY commander
        """), {"uid": user_id}).fetchall()

        for r in orphan_rows:
            cmd = r[0]
            if cmd not in known_commanders:
                import uuid as _uuid
                deck_id = f"legacy-{_uuid.uuid5(_uuid.NAMESPACE_DNS, f'{user_id}-{cmd}').hex[:12]}"
                # Insérer dans user_moxfield_decks pour le rendre persistant
                sess.execute(text("""
                    INSERT INTO user_moxfield_decks (user_id, deck_id, moxfield_url, commander, name)
                    VALUES (:uid, :did, '', :cmd, :name)
                    ON CONFLICT (user_id, deck_id) DO NOTHING
                """), {"uid": user_id, "did": deck_id, "cmd": cmd, "name": cmd})
                decks.append({
                    "deck_id": deck_id, "url": "", "commander": cmd,
                    "name": cmd, "locally_modified": False, "fetched_at": None,
                })
        sess.commit()

    return sorted(decks, key=lambda d: d.get("commander") or "")


def save_deck_for_user(user_id: int, deck_id: str, url: str, commander: str, name: str) -> None:
    with SessionLocal() as sess:
        sess.execute(text("""
            INSERT INTO user_moxfield_decks (user_id, deck_id, moxfield_url, commander, name)
            VALUES (:uid, :did, :url, :cmd, :name)
            ON CONFLICT (user_id, deck_id) DO UPDATE
              SET moxfield_url = EXCLUDED.moxfield_url,
                  commander    = EXCLUDED.commander,
                  name         = EXCLUDED.name
        """), {"uid": user_id, "did": deck_id, "url": url, "cmd": commander, "name": name})
        sess.commit()


def remove_deck_for_user(user_id: int, deck_id: str) -> bool:
    with SessionLocal() as sess:
        result = sess.execute(text("""
            DELETE FROM user_moxfield_decks WHERE user_id = :uid AND deck_id = :did
        """), {"uid": user_id, "did": deck_id})
        sess.commit()
        return result.rowcount > 0


def mark_locally_modified(user_id: int, deck_id: str, modified: bool) -> None:
    with SessionLocal() as sess:
        sess.execute(text("""
            UPDATE user_moxfield_decks SET locally_modified = :m
            WHERE user_id = :uid AND deck_id = :did
        """), {"m": modified, "uid": user_id, "did": deck_id})
        sess.commit()


def mark_synced_for_user(user_id: int, deck_id: str) -> bool:
    with SessionLocal() as sess:
        result = sess.execute(text("""
            UPDATE user_moxfield_decks SET locally_modified = false
            WHERE user_id = :uid AND deck_id = :did
        """), {"uid": user_id, "did": deck_id})
        sess.commit()
        return result.rowcount > 0


# ── Cartes des decks (remplace data/My decks/*.txt) ──────────────────────────

def get_deck_cards(user_id: int, commander: str) -> list[tuple[str, int]]:
    """Retourne [(card_name, qty), ...] pour ce commandant et cet utilisateur."""
    with SessionLocal() as sess:
        rows = sess.execute(text("""
            SELECT card_name, quantity FROM user_deck_cards
            WHERE user_id = :uid AND LOWER(TRIM(commander)) = LOWER(TRIM(:cmd))
        """), {"uid": user_id, "cmd": commander}).fetchall()
    return [(r.card_name, r.quantity) for r in rows]


def get_deck_cards_by_id(user_id: int, deck_id: str) -> list[tuple[str, int]]:
    """Cartes d'un deck precis, quand plusieurs partagent un commandant."""
    with SessionLocal() as sess:
        rows = sess.execute(text("""
            SELECT card_name, quantity FROM user_deck_cards
            WHERE user_id = :uid AND deck_id = :did
        """), {"uid": user_id, "did": deck_id}).fetchall()
    return [(r.card_name, r.quantity) for r in rows]


def set_deck_cards(user_id: int, commander: str, cards: list[tuple[str, int]],
                   deck_id: str | None = None) -> None:
    """Remplace toutes les cartes d'un deck (opération atomique)."""
    target = resolve_deck(user_id, deck_id, commander)
    if target is None:
        raise ValueError("Deck introuvable")
    did, cmd = target
    with SessionLocal() as sess:
        with sess.begin():  # transaction atomique — rollback auto si exception
            sess.execute(text("""
                DELETE FROM user_deck_cards WHERE user_id = :uid AND deck_id = :did
            """), {"uid": user_id, "did": did})
            if cards:
                for card_name, qty in cards:
                    sess.execute(text("""
                        INSERT INTO user_deck_cards
                               (user_id, deck_id, commander, card_name, quantity)
                        VALUES (:uid, :did, :cmd, :name, :qty)
                    """), {"uid": user_id, "did": did, "cmd": cmd,
                             "name": card_name, "qty": qty})
        # commit automatique à la sortie du with sess.begin()


def resolve_deck(user_id: int, deck_id: str | None, commander: str | None) -> tuple[str, str] | None:
    """(deck_id, commandant) du deck vise, ou None s'il n'existe pas.

    Le deck_id prime : plusieurs decks peuvent partager un commandant depuis que
    les cartes portent l'identifiant de leur deck. Le commandant reste accepte
    pour les appels qui ne connaissent que lui, et vise alors le deck le plus
    ancien qui le joue.
    """
    with SessionLocal() as sess:
        if deck_id:
            row = sess.execute(text("""
                SELECT deck_id, commander FROM user_moxfield_decks
                WHERE user_id = :uid AND deck_id = :did
            """), {"uid": user_id, "did": deck_id}).fetchone()
        else:
            row = sess.execute(text("""
                SELECT deck_id, commander FROM user_moxfield_decks
                WHERE user_id = :uid
                  AND mm_normalize_name(commander) = mm_normalize_name(:cmd)
                ORDER BY id
                LIMIT 1
            """), {"uid": user_id, "cmd": commander or ""}).fetchone()
    return (row.deck_id, row.commander) if row else None


def add_card_to_deck_db(user_id: int, commander: str, card_name: str,
                        deck_id: str | None = None) -> None:
    target = resolve_deck(user_id, deck_id, commander)
    if target is None:
        raise ValueError("Deck introuvable")
    did, cmd = target
    with SessionLocal() as sess:
        sess.execute(text("""
            INSERT INTO user_deck_cards (user_id, deck_id, commander, card_name, quantity)
            VALUES (:uid, :did, :cmd, :name, 1)
            ON CONFLICT (user_id, deck_id, card_name) DO UPDATE
              SET quantity = user_deck_cards.quantity + 1
        """), {"uid": user_id, "did": did, "cmd": cmd, "name": card_name})
        sess.commit()
    mark_locally_modified(user_id, _deck_id_for_commander(user_id, commander), True)


def remove_card_from_deck_db(user_id: int, commander: str, card_name: str,
                             deck_id: str | None = None,
                             all_copies: bool = False) -> bool:
    """Retire un exemplaire — ou la carte entiere si all_copies.

    Les terrains de base vont par dizaines : sans all_copies, vider une ligne
    demanderait autant d'appels que d'exemplaires.
    """
    target = resolve_deck(user_id, deck_id, commander)
    if target is None:
        return False
    did, _ = target
    with SessionLocal() as sess:
        qty = sess.execute(text("""
            SELECT quantity FROM user_deck_cards
            WHERE user_id = :uid AND deck_id = :did
              AND mm_normalize_name(card_name) = mm_normalize_name(:name)
        """), {"uid": user_id, "did": did, "name": card_name}).scalar()
        if qty is None:
            return False
        if all_copies or qty <= 1:
            sess.execute(text("""
                DELETE FROM user_deck_cards
                WHERE user_id = :uid AND deck_id = :did
                  AND mm_normalize_name(card_name) = mm_normalize_name(:name)
            """), {"uid": user_id, "did": did, "name": card_name})
        else:
            sess.execute(text("""
                UPDATE user_deck_cards SET quantity = quantity - 1
                WHERE user_id = :uid AND deck_id = :did
                  AND mm_normalize_name(card_name) = mm_normalize_name(:name)
            """), {"uid": user_id, "did": did, "name": card_name})
        sess.commit()
    return True

def get_deck_txt_content(user_id: int, commander: str) -> Optional[str]:
    """Retourne le contenu texte de la decklist (format Moxfield/EDHREC).
    Le commandant est placé dans une section séparée par une ligne vide
    pour que recommandation_populaire.py puisse l'identifier."""
    cards = get_deck_cards(user_id, commander)
    if not cards:
        return None
    # Exclure le(s) commandant(s) du corps du deck
    cmd_parts = {part.strip().lower() for part in commander.split("+")}
    body_lines = [
        f"{qty} {name}"
        for name, qty in sorted(cards, key=lambda x: x[0])
        if name.strip().lower() not in cmd_parts
    ]
    cmd_lines = [f"1 {part.strip()}" for part in commander.split("+")]
    # Ligne vide entre le corps et le commandant = section détectée par le parser
    return "\n".join(body_lines) + "\n\n" + "\n".join(cmd_lines)


def _deck_id_for_commander(user_id: int, commander: str) -> Optional[str]:
    with SessionLocal() as sess:
        row = sess.execute(text("""
            SELECT deck_id FROM user_moxfield_decks
            WHERE user_id = :uid AND LOWER(TRIM(commander)) = LOWER(TRIM(:cmd))
        """), {"uid": user_id, "cmd": commander}).fetchone()
    return row[0] if row else None


def get_all_commanders_for_user(user_id: int) -> list[str]:
    with SessionLocal() as sess:
        rows = sess.execute(text("""
            SELECT commander FROM user_moxfield_decks
            WHERE user_id = :uid AND commander IS NOT NULL
            ORDER BY commander
        """), {"uid": user_id}).fetchall()
    return [r[0] for r in rows]


# ── Collection (remplace user_collection sans user_id) ────────────────────────

def get_collection_card_names(user_id: int) -> set[str]:
    """Retourne l'ensemble normalisé des noms de cartes en collection."""
    with SessionLocal() as sess:
        rows = sess.execute(text("""
            SELECT card_name FROM user_collection WHERE user_id = :uid
        """), {"uid": user_id}).fetchall()
    return {_normalize(r[0]) for r in rows}
