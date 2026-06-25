from __future__ import annotations

import json
import time
import unicodedata
import urllib.request
import urllib.error
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CONFIG_FILE  = ROOT / "data" / "moxfield_decks.json"
CACHE_DIR    = ROOT / "data" / "moxfield_cache"
LOCAL_DIR    = ROOT / "data" / "My decks"
CACHE_DIR.mkdir(parents=True, exist_ok=True)
LOCAL_DIR.mkdir(parents=True, exist_ok=True)

MOXFIELD_API = "https://api2.moxfield.com/v3/decks/all/{deck_id}"
HEADERS = {
    "User-Agent": "ManaMind/1.0 (personal collection tool)",
    "Accept": "application/json",
}


def _normalize(name: str) -> str:
    name = unicodedata.normalize("NFKD", name)
    return "".join(c for c in name if not unicodedata.combining(c)).lower().strip()


def _deck_id_from_url(url: str) -> str | None:
    """Extrait l'ID du deck depuis une URL Moxfield."""
    url = url.strip().rstrip("/")
    parts = url.split("/")
    if "decks" in parts:
        idx = parts.index("decks")
        if idx + 1 < len(parts):
            return parts[idx + 1]
    return None


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


def add_or_update_deck(url: str) -> dict:
    """
    Ajoute ou met à jour un deck depuis son URL Moxfield.
    Appelle l'API pour récupérer le commandant. Retourne l'entrée créée.
    """
    deck_id = _deck_id_from_url(url)
    if not deck_id:
        raise ValueError(f"URL Moxfield invalide : {url}")

    data = _fetch_from_api(deck_id)
    commander = _extract_commander(data)
    name = data.get("name", "")

    decks = load_config()
    existing = next((d for d in decks if d["deck_id"] == deck_id), None)
    entry = {
        "deck_id":   deck_id,
        "url":       url.strip(),
        "commander": commander,
        "name":      name,
    }
    if existing:
        existing.update(entry)
    else:
        decks.append(entry)
    save_config(decks)
    _write_cache(deck_id, data)
    _write_local_txt(commander, _parse_cards(data))
    return entry


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


def refresh_deck(deck_id: str) -> dict:
    """Re-télécharge le deck depuis Moxfield, met à jour le cache et le .txt local."""
    decks = load_config()
    entry = next((d for d in decks if d["deck_id"] == deck_id), None)
    if not entry:
        raise ValueError(f"Deck {deck_id} introuvable dans la config")
    data = _fetch_from_api(deck_id)
    entry["commander"] = _extract_commander(data)
    entry["name"] = data.get("name", entry.get("name", ""))
    save_config(decks)
    _write_cache(deck_id, data)
    _write_local_txt(entry["commander"], _parse_cards(data))
    return entry


def _local_txt_path(commander_name: str) -> Path:
    """Retourne le chemin du .txt local pour un commandant."""
    safe = _normalize(commander_name).replace(" ", "-")
    return LOCAL_DIR / f"{safe}.txt"


def _write_local_txt(commander_name: str, cards: list[tuple[str, int]]) -> None:
    """Écrit la decklist au format Moxfield (1 Card Name) dans le .txt local."""
    lines = [f"{qty} {name}" for name, qty in sorted(cards, key=lambda x: x[0])]
    lines.append(f"\n1 {commander_name}")
    _local_txt_path(commander_name).write_text("\n".join(lines), encoding="utf-8")


def _read_local_txt(commander_name: str) -> list[tuple[str, int]]:
    """Lit le .txt local et retourne [(name, qty), ...]."""
    path = _local_txt_path(commander_name)
    if not path.exists():
        return []
    result = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split(None, 1)
        if len(parts) == 2 and parts[0].isdigit():
            result.append((parts[1], int(parts[0])))
    return result


def add_card_to_deck(commander_name: str, card_name: str) -> None:
    """Ajoute une carte (qty 1) au .txt local du commandant."""
    cards = _read_local_txt(commander_name)
    norm_new = _normalize(card_name)
    for i, (name, qty) in enumerate(cards):
        if _normalize(name) == norm_new:
            cards[i] = (name, qty + 1)
            break
    else:
        cards.append((card_name, 1))
    _write_local_txt(commander_name, cards)


def remove_card_from_deck(commander_name: str, card_name: str) -> None:
    """Retire une carte du .txt local du commandant."""
    cards = _read_local_txt(commander_name)
    norm_target = _normalize(card_name)
    new_cards = []
    for name, qty in cards:
        if _normalize(name) == norm_target:
            if qty > 1:
                new_cards.append((name, qty - 1))
        else:
            new_cards.append((name, qty))
    _write_local_txt(commander_name, new_cards)


def get_local_txt_content(commander_name: str) -> str | None:
    """Retourne le contenu brut du .txt local, ou None si inexistant."""
    path = _local_txt_path(commander_name)
    if not path.exists():
        return None
    return path.read_text(encoding="utf-8")


# ── Récupération decklist ────────────────────────────────────────────────────

def get_decklist_for_commander(commander_name: str) -> list[tuple[str, int]] | None:
    """
    Retourne la decklist du commandant sous forme [(card_name, qty), ...].
    Priorité : .txt local (modifiable) > cache JSON Moxfield.
    Retourne None si le commandant n'est pas dans la config Moxfield.
    """
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

def _fetch_from_api(deck_id: str) -> dict:
    url = MOXFIELD_API.format(deck_id=deck_id)
    req = urllib.request.Request(url, headers=HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise ValueError(f"Erreur Moxfield HTTP {e.code} pour le deck {deck_id}")
    except Exception as e:
        raise ValueError(f"Impossible de contacter Moxfield : {e}")


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
    """Extrait le nom du commandant depuis la réponse JSON Moxfield."""
    # boards.commanders.cards est un dict {card_id: {card: {name: ...}}}
    commanders = (
        data.get("boards", {})
            .get("commanders", {})
            .get("cards", {})
    )
    for card_data in commanders.values():
        name = card_data.get("card", {}).get("name", "")
        if name:
            return name
    # fallback : nom du deck
    return data.get("name", "Commandant inconnu")


def _parse_cards(data: dict) -> list[tuple[str, int]]:
    """Extrait toutes les cartes (hors commandant) de la réponse Moxfield."""
    result: list[tuple[str, int]] = []
    boards = data.get("boards", {})
    for board_name, board in boards.items():
        if board_name == "commanders":
            continue
        for card_data in board.get("cards", {}).values():
            name = card_data.get("card", {}).get("name", "")
            qty  = card_data.get("quantity", 1)
            if name:
                result.append((name, qty))
    return result
