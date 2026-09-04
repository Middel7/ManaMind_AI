from __future__ import annotations

import json
import time
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CONFIG_FILE  = ROOT / "data" / "moxfield_decks.json"
CACHE_DIR    = ROOT / "data" / "moxfield_cache"
LOCAL_DIR    = ROOT / "data" / "My decks"
CACHE_DIR.mkdir(parents=True, exist_ok=True)
LOCAL_DIR.mkdir(parents=True, exist_ok=True)

def _normalize(name: str) -> str:
    name = unicodedata.normalize("NFKD", name)
    return "".join(c for c in name if not unicodedata.combining(c)).lower().strip()


# ── État local ────────────────────────────────────────────────────────────────

def is_locally_modified(deck_id: str, commander_name: str) -> bool:
    """
    Retourne True si le .txt local a été modifié APRÈS le dernier fetch Moxfield.
    Cela signifie que le deck a des changements locaux non synchronisés avec Moxfield.
    """
    txt_path   = _local_txt_path(commander_name)
    cache_path = CACHE_DIR / f"{deck_id}.json"
    if not txt_path.exists() or not cache_path.exists():
        return False
    return txt_path.stat().st_mtime > cache_path.stat().st_mtime


def mark_as_synced(deck_id: str, commander_name: str) -> bool:
    """
    Met à jour le mtime du cache JSON pour qu'il soit plus récent que le .txt.
    Appelé après que l'utilisateur a copié la decklist vers Moxfield.
    """
    import os, time
    cache_path = CACHE_DIR / f"{deck_id}.json"
    if not cache_path.exists():
        return False
    now = time.time()
    os.utime(cache_path, (now, now))
    return True


# ── Config ────────────────────────────────────────────────────────────────────

def load_config() -> list[dict]:
    """Retourne la liste des decks Moxfield configurés."""
    if not CONFIG_FILE.exists():
        return []
    try:
        return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    except Exception:
        return []


def save_config(decks: list[dict]) -> None:
    CONFIG_FILE.write_text(
        json.dumps(decks, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def remove_deck(deck_id: str) -> bool:
    decks = load_config()
    new = [d for d in decks if d["deck_id"] != deck_id]
    if len(new) == len(decks):
        return False
    save_config(new)
    cache = CACHE_DIR / f"{deck_id}.json"
    if cache.exists():
        cache.unlink()
    return True


def _local_txt_path(commander_name: str) -> Path:
    """Retourne le chemin du .txt local pour un commandant."""
    safe = _normalize(commander_name).replace(" ", "-")
    return LOCAL_DIR / f"{safe}.txt"


def _write_local_txt(commander_name: str, cards: list[tuple[str, int]]) -> None:
    """Écrit la decklist au format Moxfield dans le .txt local.
    Pour les decks Partner ("Cmd1 + Cmd2"), écrit une ligne par commandant."""
    lines = [f"{qty} {name}" for name, qty in sorted(cards, key=lambda x: x[0])]
    cmd_lines = "\n".join(f"1 {n.strip()}" for n in commander_name.split("+"))
    lines.append(f"\n{cmd_lines}")
    _local_txt_path(commander_name).write_text("\n".join(lines), encoding="utf-8")


def _read_local_txt(commander_name: str) -> list[tuple[str, int]]:
    """Lit le .txt local et retourne [(name, qty), ...] sans le(s) commandant(s)."""
    path = _local_txt_path(commander_name)
    if not path.exists():
        return []
    # Exclure chaque partie du nom (gère "Cmd1 + Cmd2")
    cmd_norms = {_normalize(n.strip()) for n in commander_name.split("+")}
    result = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split(None, 1)
        if len(parts) == 2 and parts[0].isdigit():
            name = parts[1]
            if _normalize(name) not in cmd_norms:
                result.append((name, int(parts[0])))
    return result


def add_card_to_deck(commander_name: str, card_name: str, user_id: int = 1) -> None:
    """Ajoute un exemplaire au deck de ce commandant.

    Delegue a user_decks, qui rattache la carte au deck par son identifiant :
    depuis que plusieurs decks peuvent partager un commandant, ecrire ici une
    ligne sans deck_id violerait la contrainte de la table.
    """
    from manamind.user_decks import add_card_to_deck_db
    add_card_to_deck_db(user_id, commander_name, card_name)


def remove_card_from_deck(commander_name: str, card_name: str, user_id: int = 1) -> bool:
    """Retire un exemplaire. False si la carte est absente."""
    from manamind.user_decks import remove_card_from_deck_db
    return remove_card_from_deck_db(user_id, commander_name, card_name)


def get_local_txt_content(commander_name: str, user_id: int = 1) -> str | None:
    """Retourne le contenu texte de la decklist, depuis DB en priorité puis .txt local."""
    # Lecture depuis DB
    try:
        from manamind.user_decks import get_deck_txt_content
        content = get_deck_txt_content(user_id, commander_name)
        if content:
            return content
    except Exception:
        pass
    # Fallback .txt local (TODO: supprimer après migration)
    path = _local_txt_path(commander_name)
    if not path.exists():
        return None
    return path.read_text(encoding="utf-8")


def get_decklist_for_commander_db(commander_name: str, user_id: int = 1) -> list[tuple[str, int]] | None:
    """Retourne la decklist depuis user_deck_cards (DB), ou None si absente."""
    try:
        from manamind.user_decks import get_deck_cards
        entries = get_deck_cards(user_id, commander_name)
        return entries if entries else None
    except Exception:
        return None


# ── Récupération decklist ────────────────────────────────────────────────────

def get_decklist_for_commander(commander_name: str, user_id: int = 1) -> list[tuple[str, int]] | None:
    """
    Retourne la decklist du commandant sous forme [(card_name, qty), ...].
    Priorité : DB user_deck_cards > .txt local > cache JSON Moxfield.
    Retourne None si le commandant est introuvable dans toutes les sources.
    """
    # 1. DB user_deck_cards (source principale après migration)
    db_entries = get_decklist_for_commander_db(commander_name, user_id)
    if db_entries is not None:
        return db_entries

    # 2. Config Moxfield (legacy JSON) : .txt local puis cache JSON
    norm = _normalize(commander_name)
    decks = load_config()
    entry = next((d for d in decks if _normalize(d["commander"]) == norm), None)
    if not entry:
        return None

    # Priorité au .txt local (peut avoir été modifié via +/−)
    local = _read_local_txt(commander_name)
    if local:
        return local

    # Fallback : cache JSON
    data = _load_cache(entry["deck_id"])
    if data is None:
        return None
    return _parse_cards(data)


def get_all_moxfield_commanders() -> list[str]:
    """Retourne la liste des noms de commandants configurés dans Moxfield."""
    return [d["commander"] for d in load_config() if d.get("commander")]


# ── Interne ───────────────────────────────────────────────────────────────────

def _write_cache(deck_id: str, data: dict) -> None:
    payload = {"fetched_at": time.time(), "data": data}
    (CACHE_DIR / f"{deck_id}.json").write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
    )


def _load_cache(deck_id: str) -> dict | None:
    path = CACHE_DIR / f"{deck_id}.json"
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload.get("data")
    except Exception:
        return None


def _extract_commander(data: dict) -> str:
    """Extrait le(s) nom(s) du/des commandant(s) depuis la réponse JSON Moxfield.
    Pour les decks Partner, retourne "Cmd1 + Cmd2" (trié alphabétiquement)."""
    commanders = (
        data.get("boards", {})
            .get("commanders", {})
            .get("cards", {})
    )
    names = sorted(
        card_data.get("card", {}).get("name", "")
        for card_data in commanders.values()
        if card_data.get("card", {}).get("name", "")
    )
    if names:
        return " + ".join(names)
    return data.get("name", "Commandant inconnu")


_ACTIVE_BOARDS = {"mainboard", "sideboard"}

def _parse_cards(data: dict) -> list[tuple[str, int]]:
    """Extrait les cartes du mainboard et sideboard (hors commandant et maybeboard)."""
    result: list[tuple[str, int]] = []
    boards = data.get("boards", {})
    for board_name, board in boards.items():
        if board_name not in _ACTIVE_BOARDS:
            continue
        for card_data in board.get("cards", {}).values():
            name = card_data.get("card", {}).get("name", "")
            qty  = card_data.get("quantity", 1)
            if name:
                result.append((name, qty))
    return result
