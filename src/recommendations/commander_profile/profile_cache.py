"""
profile_cache.py
Gestion des chemins et de la persistance des profils commandant.
Règles de priorité :
  1. Profil manuel  → data/commander_profiles/manual/<slug>.json
  2. Profil généré  → data/commander_profiles/generated/<slug>.json
"""
from __future__ import annotations

import json
import unicodedata
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parents[3]  # ManaMind_AI/
MANUAL_DIR = _ROOT / "data" / "commander_profiles" / "manual"
GENERATED_DIR = _ROOT / "data" / "commander_profiles" / "generated"

# Un profil généré est considéré "frais" pendant ce nombre de jours
_FRESHNESS_DAYS = 30

BUILDER_VERSION = "commander_profile_builder_v1"


def normalize_slug(name: str) -> str:
    """Convertit un nom de commandant en slug snake_case ASCII sans accents."""
    name = unicodedata.normalize("NFKD", name)
    name = "".join(c for c in name if not unicodedata.combining(c))
    name = re.sub(r"[^a-zA-Z0-9\s]", "", name)
    name = re.sub(r"\s+", "_", name.strip())
    return name.lower()


def get_profile_paths(slug: str) -> dict[str, Path]:
    return {
        "manual": MANUAL_DIR / f"{slug}.json",
        "generated": GENERATED_DIR / f"{slug}.json",
    }


def _load_json(path: Path) -> dict[str, Any] | None:
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def load_manual_profile(slug: str) -> dict[str, Any] | None:
    """Charge le profil manuel s'il existe. Retourne None sinon."""
    path = MANUAL_DIR / f"{slug}.json"
    profile = _load_json(path)
    if profile:
        profile["source"] = "manual"
        print(f"[Profil] Profil manuel charge : {path}")
    return profile


def load_generated_profile(slug: str) -> dict[str, Any] | None:
    """Charge le profil généré s'il existe. Retourne None sinon."""
    path = GENERATED_DIR / f"{slug}.json"
    profile = _load_json(path)
    if profile:
        print(f"[Profil] Profil genere charge : {path}")
    return profile


def save_generated_profile(slug: str, profile: dict[str, Any]) -> Path:
    """Sauvegarde le profil généré. Crée le dossier si absent."""
    GENERATED_DIR.mkdir(parents=True, exist_ok=True)
    path = GENERATED_DIR / f"{slug}.json"
    profile["updated_at"] = datetime.now(timezone.utc).isoformat()
    with open(path, "w", encoding="utf-8") as f:
        json.dump(profile, f, indent=2, ensure_ascii=False)
    print(f"[Profil] Profil genere sauvegarde : {path}")
    return path


def is_generated_profile_fresh(profile: dict[str, Any]) -> bool:
    """
    Vérifie que le profil généré est encore à jour :
    - même version du builder
    - créé il y a moins de _FRESHNESS_DAYS jours
    """
    if profile.get("builder_version") != BUILDER_VERSION:
        return False
    updated_at = profile.get("updated_at") or profile.get("created_at")
    if not updated_at:
        return False
    try:
        dt = datetime.fromisoformat(updated_at)
        age = (datetime.now(timezone.utc) - dt).days
        return age < _FRESHNESS_DAYS
    except (ValueError, TypeError):
        return False
