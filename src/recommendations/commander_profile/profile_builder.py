"""
profile_builder.py
Module principal de construction du profil stratégique d'un commandant.

Flux :
  1. Chercher un profil manuel  → data/commander_profiles/manual/<slug>.json
  2. Chercher un profil généré frais → data/commander_profiles/generated/<slug>.json
  3. Sinon : extraire features Oracle → détecter archétypes → analyser decklists → construire profil
  4. Sauvegarder le profil généré
  5. Retourner le profil

Aucun commandant n'a de traitement spécial codé en dur.
Les profils manuels sont dans data/commander_profiles/manual/.
"""
from __future__ import annotations

import re
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .oracle_feature_extractor import extract_oracle_features
from .archetype_detector import detect_archetypes, merge_archetype_roles, compute_max_mana_value
from .decklist_strategy_analyzer import analyze_commander_decklists
from .profile_cache import (
    normalize_slug,
    load_manual_profile,
    load_generated_profile,
    save_generated_profile,
    is_generated_profile_fresh,
    BUILDER_VERSION,
)
from .profile_schema import (
    make_profile,
    empty_commander_info,
    empty_constraint,
    empty_evidence,
    default_score_weights,
    SCHEMA_VERSION,
)


# ── Helpers ────────────────────────────────────────────────────────────────────

def _build_commander_info(commander_card: dict[str, Any]) -> dict[str, Any]:
    """Construit le bloc commander_info depuis la carte DB."""
    return {
        "id": commander_card.get("id"),
        "oracle_id": commander_card.get("oracle_id"),
        "name": commander_card.get("name", ""),
        "normalized_name": normalize_slug(commander_card.get("name", "")),
        "mana_value": commander_card.get("mana_value") or 0,
        "power": commander_card.get("power"),
        "toughness": commander_card.get("toughness"),
        "type_line": commander_card.get("type_line", ""),
        "oracle_text": commander_card.get("oracle_text", ""),
        "keywords": commander_card.get("keywords") or [],
        "color_identity": commander_card.get("color_identity") or [],
    }


def _infer_deckbuilding_constraints(
    features: dict[str, Any],
    commander_card: dict[str, Any],
) -> list[dict[str, Any]]:
    """
    Extrait les contraintes de deckbuilding exploitables depuis les features Oracle.
    Chaque contrainte a un nom, une importance (0-1), une description, et des preuves.
    """
    constraints: list[dict[str, Any]] = []
    feat_constraints = set(features.get("constraints", []))
    feat_actions = set(features.get("actions", []))
    feat_triggers = set(features.get("triggers", []))
    feat_meta = features.get("metadata", {})
    oracle_text = (commander_card.get("oracle_text") or "").lower()

    # ── Contrainte : puissance inférieure au commandant ────────────────────────
    if "lesser_power" in feat_constraints or "power_less_than_commander" in feat_constraints:
        power_val = feat_meta.get("power")
        desc = "Met en jeu des créatures dont la puissance est inférieure à celle du commandant"
        if power_val is not None:
            desc += f" (puissance du commandant : {power_val})"
        constraints.append({
            "constraint": "creature_power_less_than_commander_power",
            "importance": 0.9,
            "description": desc,
            "evidence": ["oracle:lesser_power"],
        })
        constraints.append({
            "constraint": "wants_creatures_in_hand",
            "importance": 0.8,
            "description": "Nécessite des créatures en main pour exploiter la capacité",
            "evidence": ["oracle:from_hand", "oracle:lesser_power"],
        })

    # ── Contrainte : mana value explicite ────────────────────────────────────
    numeric = features.get("numeric_constraints", [])
    mv_constraints = [n for n in numeric if n.startswith("mana_value_le_")]
    for mvc in mv_constraints:
        val = mvc.split("_")[-1]
        constraints.append({
            "constraint": "creature_mana_value_less_or_equal",
            "importance": 0.85,
            "description": f"Ne peut mettre en jeu que des créatures de valeur de mana {val} ou moins",
            "evidence": [f"oracle:mana_value_le_{val}"],
        })

    # ── Contrainte : nécessite d'attaquer ────────────────────────────────────
    if "attack_required" in feat_constraints or "attacks" in feat_triggers:
        constraints.append({
            "constraint": "needs_commander_to_attack",
            "importance": 0.85,
            "description": "La capacité se déclenche uniquement quand le commandant attaque",
            "evidence": ["oracle:attack_required"],
        })
        constraints.append({
            "constraint": "needs_haste_or_vigilance_support",
            "importance": 0.7,
            "description": "Le commandant bénéficie de cartes lui donnant haste ou vigilance",
            "evidence": ["oracle:attack_required"],
        })

    # ── Contrainte : cartes en main ───────────────────────────────────────────
    if "cheat_from_hand" in feat_actions or "from_hand" in feat_constraints:
        constraints.append({
            "constraint": "wants_creatures_in_hand",
            "importance": 0.75,
            "description": "Nécessite d'avoir des créatures en main à chaque attaque",
            "evidence": ["oracle:from_hand"],
        })
        constraints.append({
            "constraint": "wants_card_draw_triggers",
            "importance": 0.65,
            "description": "La pioche permet d'alimenter la main en créatures à mettre en jeu",
            "evidence": ["oracle:from_hand"],
        })

    # ── Contrainte : cheat creatures ─────────────────────────────────────────
    if "cheat_from_hand" in feat_actions or "without_paying_mana_cost" in feat_actions:
        constraints.append({
            "constraint": "wants_high_impact_creatures",
            "importance": 0.8,
            "description": "Met en jeu gratuitement → favoriser les grosses créatures à fort impact",
            "evidence": ["oracle:cheat_from_hand"],
        })

    # ── Contrainte : ETB ─────────────────────────────────────────────────────
    if "creature_enters" in feat_triggers or "enters_the_battlefield" in feat_triggers:
        constraints.append({
            "constraint": "wants_creatures_to_enter_battlefield",
            "importance": 0.8,
            "description": "Génère de la valeur quand les créatures entrent en jeu",
            "evidence": ["oracle:creature_enters"],
        })

    # ── Contrainte : tokens ───────────────────────────────────────────────────
    if "create_tokens" in feat_actions or "creates_token" in feat_triggers:
        constraints.append({
            "constraint": "wants_tokens",
            "importance": 0.75,
            "description": "Crée des tokens et bénéficie des cartes qui synergisent avec eux",
            "evidence": ["oracle:create_tokens"],
        })

    # ── Contrainte : cimetière ────────────────────────────────────────────────
    if "from_graveyard" in feat_constraints or "reanimate" in feat_actions:
        constraints.append({
            "constraint": "wants_graveyard_filled",
            "importance": 0.7,
            "description": "Le cimetière est une ressource stratégique",
            "evidence": ["oracle:from_graveyard"],
        })

    # ── Contrainte : instants/rituels ─────────────────────────────────────────
    if "casts_instant_or_sorcery" in feat_triggers or "noncreature_only" in feat_constraints:
        constraints.append({
            "constraint": "wants_instant_sorcery_density",
            "importance": 0.75,
            "description": "Bénéficie d'une haute densité d'instants et rituels",
            "evidence": ["oracle:casts_instant_or_sorcery"],
        })

    # ── Contrainte : équipements/auras ───────────────────────────────────────
    if "voltron" in (commander_card.get("type_line") or "").lower() or \
       "equip" in oracle_text or ("aura" in oracle_text and "enchant" in oracle_text):
        constraints.append({
            "constraint": "wants_equipment_or_aura",
            "importance": 0.8,
            "description": "Bénéficie d'équipements ou d'auras pour renforcer les créatures",
            "evidence": ["oracle:equip_or_aura"],
        })

    # ── Contrainte : sacrifice ────────────────────────────────────────────────
    if "sacrifices" in feat_triggers or "sacrifice_outlet" in feat_actions:
        constraints.append({
            "constraint": "wants_sacrifice_fodder",
            "importance": 0.75,
            "description": "Nécessite des permanents sacrifiables régulièrement",
            "evidence": ["oracle:sacrifice_outlet"],
        })

    # ── Contrainte : pioche déclenchée ────────────────────────────────────────
    if "draws_card" in feat_triggers:
        constraints.append({
            "constraint": "wants_card_draw_triggers",
            "importance": 0.7,
            "description": "Se déclenche ou bénéficie quand des cartes sont piochées",
            "evidence": ["oracle:draws_card"],
        })

    # Dédupliquer par nom de contrainte (garder importance max)
    seen: dict[str, dict[str, Any]] = {}
    for c in constraints:
        key = c["constraint"]
        if key not in seen or c["importance"] > seen[key]["importance"]:
            seen[key] = c
    return list(seen.values())


def _build_wants_from_features(features: dict[str, Any], primary_archetype: str) -> list[str]:
    """Génère la liste descriptive 'wants' depuis les features et l'archétype."""
    wants: list[str] = []
    actions = set(features.get("actions", []))
    triggers = set(features.get("triggers", []))
    constraints = set(features.get("constraints", []))

    mapping = {
        "creature_enters": "creatures entering the battlefield",
        "create_tokens": "token creation",
        "draw_cards": "repeatable card draw",
        "ramp_lands": "mana ramp",
        "add_mana": "mana acceleration",
        "put_counters": "+1/+1 counters",
        "blink": "blink effects",
        "cheat_from_hand": "powerful creatures to cheat into play",
        "cheat_from_library": "high-impact creatures from library",
        "without_paying_mana_cost": "casting without paying mana costs",
        "reanimate": "reanimation targets",
        "buff_creatures": "anthem effects",
        "protect_board": "protection effects",
        "sacrifice_outlet": "sacrifice outlets",
        "attacks": "attacking creatures",
        "lesser_power": "creatures with lesser power than commander",
        "from_hand": "creatures ready in hand",
    }
    for feat, description in mapping.items():
        if feat in actions or feat in triggers or feat in constraints:
            if description not in wants:
                wants.append(description)

    # Ajouter des wants génériques selon l'archétype primaire
    archetype_wants = {
        "cheat_creatures": ["mana ramp", "card draw to refill hand"],
        "attack_trigger": ["protection for commander", "haste enablers"],
        "creature_etb_value": ["creature-based value", "ETB triggers"],
        "token_strategy": ["token generators", "wide board presence"],
        "counter_strategy": ["proliferate effects", "counter doublers"],
        "graveyard_recursion": ["self-mill", "recursion spells"],
        "spellslinger": ["instant and sorcery density", "spell copying"],
        "landfall": ["additional land drops", "fetch lands"],
        "good_stuff": ["efficient threats", "interaction"],
    }
    for want in archetype_wants.get(primary_archetype, []):
        if want not in wants:
            wants.append(want)

    return wants


def _build_evidence(
    features: dict[str, Any],
    hypotheses: list[dict[str, Any]],
    decklist_analysis: dict[str, Any],
) -> dict[str, Any]:
    """Construit le bloc evidence du profil."""
    oracle_signals = features.get("raw_signals", [])
    decklist_signals = decklist_analysis.get("decklist_signals", [])
    archetype_signals = [
        f"{h['strategy']}(conf={h['confidence']:.2f})"
        for h in hypotheses
    ]
    warnings: list[str] = []

    if not oracle_signals:
        warnings.append("Aucun signal Oracle détecté — profil très générique")
    if not decklist_signals and decklist_analysis.get("confidence", 0) == 0:
        warnings.append("Aucune decklist disponible — score decklist sera neutre")
    if hypotheses and hypotheses[0]["strategy"] == "good_stuff":
        warnings.append("Aucun archétype fort détecté — profil good_stuff par défaut")
    if hypotheses and hypotheses[0]["confidence"] < 0.3:
        warnings.append(f"Confiance faible ({hypotheses[0]['confidence']:.2f}) — profil manuel recommandé")

    return {
        "oracle_signals": oracle_signals[:30],  # limiter
        "decklist_signals": decklist_signals,
        "archetype_signals": archetype_signals,
        "warnings": warnings,
    }


# ── Fonction principale ────────────────────────────────────────────────────────

def load_or_create_commander_profile(
    commander_card: dict[str, Any],
    force_rebuild: bool = False,
    decklist_dir: Path | None = None,
) -> dict[str, Any]:
    """
    Charge ou crée le profil stratégique d'un commandant.

    Priorité :
      1. Profil manuel dans data/commander_profiles/manual/<slug>.json
      2. Profil généré frais dans data/commander_profiles/generated/<slug>.json
      3. Génération automatique

    Args:
        commander_card: Dict issu de la base de données (champs cards).
        force_rebuild: Ignorer le cache et regénérer.
        decklist_dir: Dossier de decklists (optionnel, détecté automatiquement).

    Returns:
        Profil complet conforme au schéma V2.
    """
    name = commander_card.get("name") or "Unknown"
    slug = normalize_slug(name)

    print(f"[Profil] Construction du profil pour : {name}")

    # ── 1. Profil manuel ──────────────────────────────────────────────────────
    manual = load_manual_profile(slug)
    if manual:
        # S'assurer que les clés minimales sont présentes pour compatibilité
        manual.setdefault("source", "manual")
        manual.setdefault("score_weights", default_score_weights())
        manual.setdefault("wanted_roles", ["ramp", "card_draw", "protection", "removal"])
        manual.setdefault("avoided_roles", ["high_mana_low_impact"])
        manual.setdefault("preferred_card_types", ["Creature", "Instant", "Sorcery", "Enchantment"])
        manual.setdefault("max_preferred_mana_value", 5)
        manual.setdefault("deckbuilding_constraints", [])
        manual.setdefault("wants", [])
        manual.setdefault("strategy_confidence", 1.0)
        return manual

    # ── 2. Profil généré en cache ─────────────────────────────────────────────
    if not force_rebuild:
        generated = load_generated_profile(slug)
        if generated and is_generated_profile_fresh(generated):
            generated.setdefault("score_weights", default_score_weights())
            return generated
        elif generated:
            print(f"[Profil] Profil genere obsolete — reconstruction...")

    # ── 3. Génération automatique ─────────────────────────────────────────────
    print(f"[Profil] Generation automatique du profil...")

    commander_info = _build_commander_info(commander_card)

    # 3a. Extraire les features Oracle
    features = extract_oracle_features(commander_card)
    oracle_signal_count = len(features.get("raw_signals", []))
    print(f"[Profil]   {oracle_signal_count} signaux Oracle detectes : {features.get('raw_signals', [])[:8]}")

    # 3b. Analyser les decklists
    decklist_analysis = analyze_commander_decklists(
        name, slug, force_rebuild=force_rebuild
    )

    # 3c. Détecter les archétypes
    hypotheses = detect_archetypes(features, decklist_analysis)
    print(f"[Profil]   Archetypes detectes :")
    for h in hypotheses[:4]:
        print(f"[Profil]     - {h['strategy']:<30} conf={h['confidence']:.3f} (oracle={h['oracle_score']:.2f}, decklist={h['decklist_score']:.2f})")

    # 3d. Fusionner les rôles des archétypes top
    wanted_roles, avoided_roles, preferred_types, role_weights = merge_archetype_roles(
        hypotheses, {}, top_n_merge=3
    )

    # 3e. Archétype primaire et confiance
    primary = hypotheses[0]["strategy"] if hypotheses else "good_stuff"
    primary_conf = hypotheses[0]["confidence"] if hypotheses else 0.1
    secondary = [h["strategy"] for h in hypotheses[1:4] if h["confidence"] >= 0.15]

    # 3f. Calcul max_preferred_mana_value
    max_mv = compute_max_mana_value(features, primary, decklist_analysis)
    print(f"[Profil]   max_preferred_mana_value = {max_mv}")

    # 3g. Contraintes de deckbuilding
    constraints = _infer_deckbuilding_constraints(features, commander_card)
    if constraints:
        print(f"[Profil]   {len(constraints)} contraintes detectees : {[c['constraint'] for c in constraints]}")

    # 3h. Types préférés dynamiques
    if not preferred_types:
        preferred_types = ["Creature", "Instant", "Sorcery", "Enchantment"]

    # 3i. Mana curve preference
    mana_curve_pref = {
        "low": 1,
        "ideal_min": 2,
        "ideal_max": max(3, max_mv - 2),
        "high": max_mv + 1,
    }

    # 3j. Wants descriptifs
    wants = _build_wants_from_features(features, primary)

    # 3k. Evidence
    evidence = _build_evidence(features, hypotheses, decklist_analysis)

    if evidence["warnings"]:
        for w in evidence["warnings"]:
            print(f"[Profil]   AVERTISSEMENT : {w}")

    # 3l. Card type weights dynamiques
    card_type_weights: dict[str, float] = {
        "Creature": 1.0, "Instant": 1.0, "Sorcery": 1.0,
        "Enchantment": 1.0, "Artifact": 1.0, "Land": 0.5, "Planeswalker": 0.8,
    }
    type_priority_bonus = 0.3
    for i, ctype in enumerate(preferred_types[:3]):
        if ctype in card_type_weights:
            card_type_weights[ctype] += type_priority_bonus * (1.0 - i * 0.3)

    # ── 4. Construire le profil complet ───────────────────────────────────────
    profile = make_profile(
        commander_info=commander_info,
        primary_strategy=primary,
        strategy_confidence=primary_conf,
        secondary_strategies=secondary,
        strategy_hypotheses=hypotheses,
        wanted_roles=wanted_roles,
        avoided_roles=avoided_roles,
        preferred_card_types=preferred_types,
        deckbuilding_constraints=constraints,
        max_preferred_mana_value=max_mv,
        mana_curve_preference=mana_curve_pref,
        card_type_weights=card_type_weights,
        role_weights=role_weights,
        score_weights=default_score_weights(),
        evidence=evidence,
        source="generated",
        wants=wants,
    )

    # Ajouter builder_version pour la vérification de fraîcheur
    profile["builder_version"] = BUILDER_VERSION

    # ── 5. Sauvegarder ───────────────────────────────────────────────────────
    save_generated_profile(slug, profile)

    return profile
