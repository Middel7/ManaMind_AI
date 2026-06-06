"""
archetype_detector.py
Détecte les archétypes MTG d'un commandant en comparant ses features Oracle
aux templates d'archétypes définis dans archetype_templates.json.
Retourne une liste d'hypothèses classées par score de confiance.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

_CONFIG_DIR = Path(__file__).resolve().parents[3] / "data" / "recommendation_config"
_TEMPLATES_FILE = _CONFIG_DIR / "archetype_templates.json"

_templates_cache: dict[str, Any] | None = None


def _load_templates() -> dict[str, Any]:
    global _templates_cache
    if _templates_cache is None:
        try:
            with open(_TEMPLATES_FILE, encoding="utf-8") as f:
                _templates_cache = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError) as e:
            print(f"[ArchetypeDetector] ATTENTION : impossible de charger archetype_templates.json : {e}")
            _templates_cache = {"archetypes": {}}
    return _templates_cache


def _all_feature_signals(features: dict[str, Any]) -> set[str]:
    """Aplatit toutes les features en un set de signaux."""
    signals: set[str] = set()
    for key in ("triggers", "actions", "constraints", "zones", "tribal_signals"):
        for item in features.get(key, []):
            signals.add(item)
    # Ajouter aussi les raw_signals nettoyés
    for raw in features.get("raw_signals", []):
        # "action:cheat_from_hand" → "cheat_from_hand"
        signals.add(raw.split(":")[-1])
    return signals


def _score_archetype(
    archetype_cfg: dict[str, Any],
    feature_signals: set[str],
    decklist_analysis: dict[str, Any] | None,
) -> tuple[float, float, float, list[str]]:
    """
    Calcule le score oracle et decklist pour un archétype.
    Retourne (total_score, oracle_score, decklist_score, evidence).
    """
    signals = archetype_cfg.get("signals", [])
    helpful = archetype_cfg.get("helpful_oracle_features", [])
    all_signals = list(dict.fromkeys(signals + helpful))  # union dédupliquée

    if not all_signals:
        return 0.0, 0.0, 0.0, []

    evidence: list[str] = []
    hits = 0
    for sig in all_signals:
        if sig in feature_signals:
            hits += 1
            evidence.append(f"oracle_signal:{sig}")

    # Score oracle : ratio de signaux matchés, avec bonus pour signaux requis
    oracle_score = hits / len(all_signals) if all_signals else 0.0

    # Bonus si plusieurs signaux forts sont présents (non-linéaire)
    if hits >= 3:
        oracle_score = min(1.0, oracle_score * 1.2)
    if hits >= 5:
        oracle_score = min(1.0, oracle_score * 1.1)

    # ── Score decklist ────────────────────────────────────────────────────────
    decklist_score = 0.0
    if decklist_analysis and decklist_analysis.get("confidence", 0) > 0.1:
        decklist_sigs = decklist_analysis.get("decklist_signals", [])
        archetype_name = archetype_cfg.get("archetype", "")
        # Chercher si le nom de l'archétype ou ses rôles voulus apparaissent dans les signaux decklists
        wanted = set(archetype_cfg.get("wanted_roles", []))
        decklist_role_dist = decklist_analysis.get("role_distribution", {})

        role_hits = 0
        total_weight = 0.0
        for role in wanted:
            freq = decklist_role_dist.get(role, 0.0)
            if freq > 0.1:
                role_hits += 1
                total_weight += freq
                evidence.append(f"decklist_role:{role}={freq:.2f}")

        if wanted:
            decklist_score = min(1.0, total_weight / len(wanted))

        # Bonus si l'archétype est explicitement signalé dans les decklist_signals
        if any(archetype_name in sig for sig in decklist_sigs):
            decklist_score = min(1.0, decklist_score + 0.2)
            evidence.append(f"decklist_signal:{archetype_name}")

    # ── Score total ────────────────────────────────────────────────────────────
    # Pondération : oracle 70%, decklist 30% (si disponible)
    if decklist_analysis and decklist_analysis.get("confidence", 0) > 0.1:
        total = 0.70 * oracle_score + 0.30 * decklist_score
    else:
        total = oracle_score

    return round(total, 4), round(oracle_score, 4), round(decklist_score, 4), evidence


def detect_archetypes(
    features: dict[str, Any],
    decklist_analysis: dict[str, Any] | None = None,
    min_confidence: float = 0.05,
    top_n: int = 5,
) -> list[dict[str, Any]]:
    """
    Détecte les archétypes les plus probables pour un commandant.

    Args:
        features: Résultat de extract_oracle_features().
        decklist_analysis: Résultat de analyze_commander_decklists() (optionnel).
        min_confidence: Seuil minimal de confiance pour inclure un archétype.
        top_n: Nombre maximum d'hypothèses retournées.

    Returns:
        Liste d'hypothèses triées par confiance décroissante.
    """
    templates = _load_templates()
    archetypes_cfg = templates.get("archetypes", {})

    feature_signals = _all_feature_signals(features)

    hypotheses: list[dict[str, Any]] = []

    for archetype_name, cfg in archetypes_cfg.items():
        if archetype_name == "good_stuff":
            continue  # good_stuff est le fallback, traité séparément

        total, oracle_score, decklist_score, evidence = _score_archetype(
            cfg, feature_signals, decklist_analysis
        )

        if total >= min_confidence:
            hypotheses.append({
                "strategy": archetype_name,
                "confidence": total,
                "oracle_score": oracle_score,
                "decklist_score": decklist_score,
                "evidence": evidence,
            })

    # Trier par confiance décroissante
    hypotheses.sort(key=lambda h: h["confidence"], reverse=True)

    # Si aucune hypothèse forte, ajouter good_stuff comme fallback
    if not hypotheses or hypotheses[0]["confidence"] < 0.2:
        hypotheses.append({
            "strategy": "good_stuff",
            "confidence": max(0.1, 0.25 - (hypotheses[0]["confidence"] if hypotheses else 0.0)),
            "oracle_score": 0.0,
            "decklist_score": 0.0,
            "evidence": ["fallback: no strong archetype detected"],
        })

    return hypotheses[:top_n]


def merge_archetype_roles(
    hypotheses: list[dict[str, Any]],
    templates_cfg: dict[str, Any],
    top_n_merge: int = 3,
) -> tuple[list[str], list[str], list[str], dict[str, float]]:
    """
    Fusionne les rôles voulus/évités des archétypes détectés (top N),
    pondérés par leur confiance.

    Retourne (wanted_roles, avoided_roles, preferred_card_types, role_weights).
    """
    templates = _load_templates()
    archetypes_cfg = templates.get("archetypes", {})

    wanted_weighted: dict[str, float] = {}
    avoided_weighted: dict[str, float] = {}
    type_weighted: dict[str, float] = {}
    role_weights: dict[str, float] = {}

    top = hypotheses[:top_n_merge]

    for hyp in top:
        name = hyp["strategy"]
        conf = hyp["confidence"]
        cfg = archetypes_cfg.get(name, {})

        for role in cfg.get("wanted_roles", []):
            wanted_weighted[role] = wanted_weighted.get(role, 0.0) + conf
            # Accumuler les role_weights depuis le template
            base_w = cfg.get("base_role_weights", {}).get(role, 1.0)
            role_weights[role] = max(role_weights.get(role, 0.0), base_w * conf)

        for role in cfg.get("avoided_roles", []):
            avoided_weighted[role] = avoided_weighted.get(role, 0.0) + conf

        for ctype in cfg.get("preferred_card_types", []):
            type_weighted[ctype] = type_weighted.get(ctype, 0.0) + conf

    # Trier par poids décroissant
    wanted_roles = sorted(wanted_weighted, key=lambda r: wanted_weighted[r], reverse=True)
    avoided_roles = sorted(avoided_weighted, key=lambda r: avoided_weighted[r], reverse=True)
    preferred_types = sorted(type_weighted, key=lambda t: type_weighted[t], reverse=True)

    # Toujours inclure les fondamentaux si pas déjà présents
    for role in ("ramp", "card_draw", "protection", "removal"):
        if role not in wanted_roles:
            wanted_roles.append(role)

    return wanted_roles, avoided_roles, preferred_types, role_weights


def compute_max_mana_value(
    features: dict[str, Any],
    primary_archetype: str,
    decklist_analysis: dict[str, Any] | None = None,
) -> int:
    """
    Calcule la mana value maximale préférée selon l'archétype,
    les features Oracle et les données decklists.
    """
    templates = _load_templates()
    archetypes_cfg = templates.get("archetypes", {})
    archetype_cfg = archetypes_cfg.get(primary_archetype, {})
    curve_cfg = archetype_cfg.get("typical_mana_curve", {})

    base_max = curve_cfg.get("max_preferred", 5)

    # Override par contraintes Oracle spécifiques
    constraints = features.get("constraints", [])
    actions = features.get("actions", [])
    meta = features.get("metadata", {})
    cmd_mv = meta.get("mana_value", 0)

    if "cheat_from_hand" in actions or "without_paying_mana_cost" in actions:
        base_max = max(base_max, 8)
    if "cheat_from_library" in actions:
        base_max = max(base_max, 8)
    if "mana_value_less_or_equal" in constraints:
        # Contrainte explicite dans le texte → utiliser la valeur numérique si détectée
        numeric = features.get("numeric_constraints", [])
        mv_vals = [int(n.split("_")[-1]) for n in numeric if n.startswith("mana_value_le_")]
        if mv_vals:
            base_max = min(base_max, min(mv_vals))

    # Ajustement basé sur la MV du commandant lui-même
    if cmd_mv >= 7:
        base_max = max(base_max, 7)
    elif cmd_mv <= 2 and primary_archetype not in ("cheat_creatures", "big_mana"):
        base_max = min(base_max, 4)

    # Ajustement depuis la courbe réelle des decklists
    if decklist_analysis and decklist_analysis.get("confidence", 0) > 0.3:
        avg_curve = decklist_analysis.get("average_mana_curve", {})
        avg_mv = avg_curve.get("average", None)
        if avg_mv is not None:
            # Si la courbe réelle est très basse, abaisser le seuil
            if avg_mv < 2.5:
                base_max = min(base_max, 4)
            elif avg_mv > 4.5:
                base_max = max(base_max, base_max)

    return int(base_max)
