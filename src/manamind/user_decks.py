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
    return [dict(r._mapping) for r in rows]


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


def set_deck_cards(user_id: int, commander: str, cards: list[tuple[str, int]]) -> None:
    """Remplace toutes les cartes du deck pour ce commandant."""
    with SessionLocal() as sess:
        sess.execute(text("""
            DELETE FROM user_deck_cards
            WHERE user_id = :uid AND LOWER(TRIM(commander)) = LOWER(TRIM(:cmd))
        """), {"uid": user_id, "cmd": commander})
        if cards:
            sess.execute(text("""
                INSERT INTO user_deck_cards (user_id, commander, card_name, quantity)
                VALUES (:uid, :cmd, :name, :qty)
            """), [{"uid": user_id, "cmd": commander, "name": n, "qty": q} for n, q in cards])
        sess.commit()


def add_card_to_deck_db(user_id: int, commander: str, card_name: str) -> None:
    with SessionLocal() as sess:
        sess.execute(text("""
            INSERT INTO user_deck_cards (user_id, commander, card_name, quantity)
            VALUES (:uid, :cmd, :name, 1)
            ON CONFLICT (user_id, commander, card_name) DO UPDATE
              SET quantity = user_deck_cards.quantity + 1
        """), {"uid": user_id, "cmd": commander, "name": card_name})
        sess.commit()
    mark_locally_modified(user_id, _deck_id_for_commander(user_id, commander), True)


def remove_card_from_deck_db(user_id: int, commander: str, card_name: str) -> bool:
    with SessionLocal() as sess:
        row = sess.execute(text("""
            SELECT quantity FROM user_deck_cards
            WHERE user_id = :uid AND LOWER(TRIM(commander)) = LOWER(TRIM(:cmd))
              AND LOWER(TRIM(card_name)) = LOWER(TRIM(:name))
        """), {"uid": user_id, "cmd": commander, "name": card_name}).fetchone()
        if row is None:
            return False
        if row.quantity <= 1:
            sess.execute(text("""
                DELETE FROM user_deck_cards
                WHERE user_id = :uid AND LOWER(TRIM(commander)) = LOWER(TRIM(:cmd))
                  AND LOWER(TRIM(card_name)) = LOWER(TRIM(:name))
            """), {"uid": user_id, "cmd": commander, "name": card_name})
        else:
            sess.execute(text("""
                UPDATE user_deck_cards SET quantity = quantity - 1
                WHERE user_id = :uid AND LOWER(TRIM(commander)) = LOWER(TRIM(:cmd))
                  AND LOWER(TRIM(card_name)) = LOWER(TRIM(:name))
            """), {"uid": user_id, "cmd": commander, "name": card_name})
        sess.commit()
    deck_id = _deck_id_for_commander(user_id, commander)
    if deck_id:
        mark_locally_modified(user_id, deck_id, True)
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
