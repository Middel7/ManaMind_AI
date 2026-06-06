"""
profile_schema.py
Définit la structure (schéma) d'un profil commandant V2.
Fournit des helpers pour créer des structures vides et valider les profils.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


SCHEMA_VERSION = "2.0"
BUILDER_VERSION = "commander_profile_builder_v1"


def empty_commander_info() -> dict[str, Any]:
    return {
        "id": None,
        "oracle_id": None,
        "name": "",
        "normalized_name": "",
        "mana_value": 0,
        "power": None,
        "toughness": None,
        "type_line": "",
        "oracle_text": "",
        "keywords": [],
        "color_identity": [],
    }


def empty_hypothesis() -> dict[str, Any]:
    return {
        "strategy": "",
        "confidence": 0.0,
        "oracle_score": 0.0,
        "decklist_score": 0.0,
        "evidence": [],
    }


def empty_constraint() -> dict[str, Any]:
    return {
        "constraint": "",
        "importance": 0.5,
        "description": "",
        "evidence": [],
    }


def empty_evidence() -> dict[str, Any]:
    return {
        "oracle_signals": [],
        "decklist_signals": [],
        "archetype_signals": [],
        "warnings": [],
    }


def empty_mana_curve_preference() -> dict[str, int]:
    return {
        "low": 1,
        "ideal_min": 2,
        "ideal_max": 5,
        "high": 7,
    }


def default_card_type_weights() -> dict[str, float]:
    return {
        "Creature": 1.0,
        "Instant": 1.0,
        "Sorcery": 1.0,
        "Enchantment": 1.0,
        "Artifact": 1.0,
        "Land": 0.5,
        "Planeswalker": 0.8,
    }


def default_role_weights() -> dict[str, float]:
    return {
        "ramp": 1.0,
        "card_draw": 1.0,
        "protection": 1.0,
        "removal": 1.0,
        "targeted_removal": 1.0,
        "boardwipe": 1.0,
    }


def default_score_weights() -> dict[str, float]:
    return {
        "decklist_popularity": 0.25,
        "strategic_role": 0.25,
        "commander_synergy": 0.20,
        "vector_similarity": 0.15,
        "edhrec": 0.10,
        "mana_curve": 0.03,
        "card_quality": 0.02,
    }


def make_profile(
    commander_info: dict[str, Any],
    primary_strategy: str = "good_stuff",
    strategy_confidence: float = 0.3,
    secondary_strategies: list[str] | None = None,
    strategy_hypotheses: list[dict[str, Any]] | None = None,
    wanted_roles: list[str] | None = None,
    avoided_roles: list[str] | None = None,
    preferred_card_types: list[str] | None = None,
    deckbuilding_constraints: list[dict[str, Any]] | None = None,
    max_preferred_mana_value: int = 5,
    mana_curve_preference: dict[str, int] | None = None,
    card_type_weights: dict[str, float] | None = None,
    role_weights: dict[str, float] | None = None,
    score_weights: dict[str, float] | None = None,
    evidence: dict[str, Any] | None = None,
    source: str = "generated",
    wants: list[str] | None = None,
) -> dict[str, Any]:
    """Construit un profil commandant complet avec toutes les clés attendues."""
    now = datetime.now(timezone.utc).isoformat()
    return {
        "schema_version": SCHEMA_VERSION,
        "builder_version": BUILDER_VERSION,
        "source": source,
        "commander": commander_info,
        # Compatibilité avec l'ancien format (accès direct par le script V2)
        "commander_name": commander_info.get("name", ""),
        "primary_strategy": primary_strategy,
        "strategy_confidence": round(strategy_confidence, 3),
        "secondary_strategies": secondary_strategies or [],
        "strategy_hypotheses": strategy_hypotheses or [],
        "wants": wants or [],
        "wanted_roles": wanted_roles or ["ramp", "card_draw", "protection", "removal"],
        "avoided_roles": avoided_roles or ["high_mana_low_impact"],
        "preferred_card_types": preferred_card_types or ["Creature", "Instant", "Sorcery", "Enchantment"],
        "deckbuilding_constraints": deckbuilding_constraints or [],
        "max_preferred_mana_value": max_preferred_mana_value,
        "mana_curve_preference": mana_curve_preference or empty_mana_curve_preference(),
        "card_type_weights": card_type_weights or default_card_type_weights(),
        "role_weights": role_weights or default_role_weights(),
        "score_weights": score_weights or default_score_weights(),
        "evidence": evidence or empty_evidence(),
        "created_at": now,
        "updated_at": now,
    }


def is_valid_profile(profile: dict[str, Any]) -> bool:
    """Vérifie qu'un profil a les clés minimales requises."""
    required = {"primary_strategy", "wanted_roles", "avoided_roles", "preferred_card_types"}
    return required.issubset(profile.keys())
