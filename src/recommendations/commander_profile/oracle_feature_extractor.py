"""
oracle_feature_extractor.py
Extrait des features structurées depuis le texte oracle d'un commandant.
Produit une représentation intermédiaire utilisée par l'archetype_detector.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

_CONFIG_DIR = Path(__file__).resolve().parents[3] / "data" / "recommendation_config"
_PATTERNS_FILE = _CONFIG_DIR / "oracle_patterns.json"

_patterns_cache: dict[str, Any] | None = None


def _load_patterns() -> dict[str, Any]:
    global _patterns_cache
    if _patterns_cache is None:
        try:
            with open(_PATTERNS_FILE, encoding="utf-8") as f:
                _patterns_cache = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError) as e:
            print(f"[OracleExtractor] ATTENTION : impossible de charger oracle_patterns.json : {e}")
            _patterns_cache = {"triggers": {}, "actions": {}, "constraints": {}, "zones": {}, "tribal_keywords": []}
    return _patterns_cache


def _text_contains(text: str, patterns: list[str]) -> bool:
    """Vérifie si le texte contient au moins un des patterns."""
    return any(p.lower() in text for p in patterns)


def _normalize_text(text: str) -> str:
    """Normalise le texte oracle pour la détection."""
    # Remplacer le nom du commandant par ~ pour simplifier les patterns
    return text.lower().strip()


def _try_parse_power(power: Any) -> float | None:
    """Parse la puissance d'une créature (peut être '*', '1+*', etc.)."""
    if power is None:
        return None
    try:
        return float(power)
    except (ValueError, TypeError):
        return None


def extract_oracle_features(commander_card: dict[str, Any]) -> dict[str, Any]:
    """
    Extrait des features structurées depuis les données d'un commandant.

    Retourne un dict avec :
    - triggers, actions, objects, zones, constraints, timing
    - costs, rewards, risks, numeric_constraints
    - tribal_signals, raw_signals
    - metadata (mana_value, power, toughness, color_identity, type_line)
    """
    patterns = _load_patterns()

    oracle_text = _normalize_text(commander_card.get("oracle_text") or "")
    name = (commander_card.get("name") or "").lower()
    type_line = (commander_card.get("type_line") or "").lower()
    keywords = [k.lower() for k in (commander_card.get("keywords") or [])]
    mana_value = commander_card.get("mana_value") or 0
    power_raw = commander_card.get("power")
    power = _try_parse_power(power_raw)
    color_identity = commander_card.get("color_identity") or []

    features: dict[str, list[str]] = {
        "triggers": [],
        "actions": [],
        "objects": [],
        "zones": [],
        "constraints": [],
        "timing": [],
        "costs": [],
        "rewards": [],
        "risks": [],
        "numeric_constraints": [],
        "tribal_signals": [],
        "raw_signals": [],
    }

    # ── 1. TRIGGERS ────────────────────────────────────────────────────────────
    trigger_cfg = patterns.get("triggers", {})
    for trigger_name, cfg in trigger_cfg.items():
        pats = cfg.get("patterns", [])
        if _text_contains(oracle_text, pats):
            for feat in cfg.get("features", []):
                if feat not in features["triggers"]:
                    features["triggers"].append(feat)
            features["raw_signals"].append(f"trigger:{trigger_name}")

    # ── 2. ACTIONS ─────────────────────────────────────────────────────────────
    action_cfg = patterns.get("actions", {})
    for action_name, cfg in action_cfg.items():
        pats = cfg.get("patterns", [])
        if cfg.get("combined"):
            # Nécessite que TOUS les patterns soient présents
            if all(p.lower() in oracle_text for p in pats):
                for feat in cfg.get("features", []):
                    if feat not in features["actions"]:
                        features["actions"].append(feat)
                features["raw_signals"].append(f"action:{action_name}")
        else:
            if _text_contains(oracle_text, pats):
                for feat in cfg.get("features", []):
                    if feat not in features["actions"]:
                        features["actions"].append(feat)
                features["raw_signals"].append(f"action:{action_name}")

    # ── 3. CONTRAINTES ────────────────────────────────────────────────────────
    constraint_cfg = patterns.get("constraints", {})
    for cname, cfg in constraint_cfg.items():
        pats = cfg.get("patterns", [])
        if cfg.get("combined"):
            if all(p.lower() in oracle_text for p in pats):
                for feat in cfg.get("features", []):
                    if feat not in features["constraints"]:
                        features["constraints"].append(feat)
                features["raw_signals"].append(f"constraint:{cname}")
        else:
            if _text_contains(oracle_text, pats):
                for feat in cfg.get("features", []):
                    if feat not in features["constraints"]:
                        features["constraints"].append(feat)
                features["raw_signals"].append(f"constraint:{cname}")

    # ── 4. ZONES ──────────────────────────────────────────────────────────────
    zone_cfg = patterns.get("zones", {})
    for zone_name, zone_pats in zone_cfg.items():
        if _text_contains(oracle_text, zone_pats):
            if zone_name not in features["zones"]:
                features["zones"].append(zone_name)

    # ── 5. KEYWORDS DE LA CARTE ───────────────────────────────────────────────
    keyword_features = {
        "haste": "grant_keywords",
        "flying": "grant_keywords",
        "trample": "grant_keywords",
        "lifelink": "gain_life",
        "vigilance": "combat_support",
        "deathtouch": "combat_support",
        "first strike": "combat_support",
        "double strike": "combat_support",
        "menace": "combat_support",
        "hexproof": "protect_board",
        "indestructible": "protect_board",
        "partner": "partner",
    }
    for kw, feat in keyword_features.items():
        if kw in keywords or kw in oracle_text:
            if feat not in features["actions"]:
                features["actions"].append(feat)
            features["raw_signals"].append(f"keyword:{kw}")

    # ── 6. CONTRAINTES NUMÉRIQUES ─────────────────────────────────────────────
    # Détecter "mana value X or less", "power X or less", etc.
    mv_matches = re.findall(r"mana value (?:of |)(\d+) or less", oracle_text)
    mv_matches += re.findall(r"converted mana cost (\d+) or less", oracle_text)
    mv_matches += re.findall(r"mana value less than or equal to (\d+)", oracle_text)
    for mv in mv_matches:
        features["numeric_constraints"].append(f"mana_value_le_{mv}")
        features["constraints"].append("mana_value_less_or_equal")

    power_matches = re.findall(r"power (?:of |)(\d+) or less", oracle_text)
    for pw in power_matches:
        features["numeric_constraints"].append(f"power_le_{pw}")
        features["constraints"].append("power_less_than_value")

    # "lesser power" → contrainte relative à la puissance du commandant
    if "lesser power" in oracle_text:
        features["constraints"].append("lesser_power")
        features["constraints"].append("power_less_than_commander")
        if "creature" in oracle_text:
            features["constraints"].append("creature_only")
        features["raw_signals"].append("constraint:lesser_power_detected")

    # ── 7. SIGNAUX TRIBAUX ────────────────────────────────────────────────────
    tribal_keywords = patterns.get("tribal_keywords", [])
    for tribe in tribal_keywords:
        # Chercher dans le type_line ET dans l'oracle_text
        if tribe in type_line or tribe in oracle_text or tribe + "s" in oracle_text:
            if tribe not in features["tribal_signals"]:
                features["tribal_signals"].append(tribe)

    # ── 8. OBJETS DÉTECTÉS ────────────────────────────────────────────────────
    object_patterns = {
        "creature": ["creature card", "creature spell", "creature you control", "creatures you control", "creature enters"],
        "token": ["token", "create a", "1/1", "2/2", "3/3"],
        "artifact": ["artifact", "equipment", "vehicle"],
        "enchantment": ["enchantment", "aura"],
        "instant": ["instant", "instant spell"],
        "sorcery": ["sorcery", "sorcery spell"],
        "land": ["land", "basic land", "nonbasic land"],
        "counter": ["+1/+1 counter", "-1/-1 counter", "loyalty counter", "put a counter"],
        "equipment": ["equip", "equipment you control", "equipped"],
        "aura": ["aura", "enchant creature", "enchant permanent"],
    }
    for obj_name, obj_pats in object_patterns.items():
        if _text_contains(oracle_text, obj_pats):
            if obj_name not in features["objects"]:
                features["objects"].append(obj_name)

    # ── 9. TIMING ─────────────────────────────────────────────────────────────
    timing_patterns = {
        "combat": ["during combat", "combat phase", "beginning of combat", "tapped and attacking"],
        "upkeep": ["upkeep", "beginning of your upkeep"],
        "end_step": ["end step", "beginning of your end step"],
        "main_phase": ["main phase", "sorcery speed"],
        "instant_speed": ["instant speed", "any time", "flash"],
    }
    for timing_name, timing_pats in timing_patterns.items():
        if _text_contains(oracle_text, timing_pats):
            if timing_name not in features["timing"]:
                features["timing"].append(timing_name)

    # ── 10. RISQUES ───────────────────────────────────────────────────────────
    if "attack_required" in features["constraints"] or "attacks" in features["triggers"]:
        features["risks"].append("requires_commander_to_attack")
    if "combat_damage_required" in features["constraints"]:
        features["risks"].append("requires_combat_damage")
    if "once_each_turn" in features["constraints"]:
        features["risks"].append("limited_once_per_turn")
    if "sacrifice" in oracle_text:
        features["risks"].append("requires_sacrifice")

    # ── 11. RÉCOMPENSES ───────────────────────────────────────────────────────
    if "draw_cards" in features["actions"]:
        features["rewards"].append("card_advantage")
    if "create_tokens" in features["actions"]:
        features["rewards"].append("board_presence")
    if "put_counters" in features["actions"]:
        features["rewards"].append("permanent_buffs")
    if "cheat_from_hand" in features["actions"] or "cheat_from_library" in features["actions"]:
        features["rewards"].append("mana_efficiency")
    if "without_paying_mana_cost" in features["actions"]:
        features["rewards"].append("mana_efficiency")
    if "ramp_lands" in features["actions"] or "add_mana" in features["actions"]:
        features["rewards"].append("mana_acceleration")

    # ── 12. METADATA ──────────────────────────────────────────────────────────
    features["metadata"] = {
        "mana_value": mana_value,
        "power": power,
        "power_raw": power_raw,
        "color_identity": color_identity,
        "type_line": type_line,
        "keywords": keywords,
        "is_legendary": "legendary" in type_line,
        "is_creature": "creature" in type_line,
        "is_planeswalker": "planeswalker" in type_line,
    }

    return features
