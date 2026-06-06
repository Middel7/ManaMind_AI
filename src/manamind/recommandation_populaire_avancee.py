#!/usr/bin/env python3
"""
ManaMind — Recommend Deck Changes V2 (Corrigé)
================================================
Moteur hybride avec :
  - Filtre strict d'identité couleur Commander (DB PostgreSQL)
  - Filtre légalité Commander
  - Profil stratégique générique détecté automatiquement
  - Strategy score basé sur oracle_text
  - Seuils de qualité
  - Debug CSV des cartes rejetées
  - Profil JSON pour la page de résultats

Formule :
    final_score =
      0.25 * popularity_score
    + 0.20 * specificity_score
    + 0.20 * synergy_score
    + 0.20 * strategy_score
    + 0.10 * structure_score
    + 0.05 * similarity_score

Usage :
    python src/manamind/recommandation_populaire_avancee.py \
        --input uploads/my_deck.txt \
        --output outputs/recommendations_v2_my_deck.csv
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# ── PYTHONPATH ────────────────────────────────────────────────────────────────
_HERE = Path(__file__).resolve().parent
_ROOT = _HERE.parents[1]
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "src"))

from manamind.recommandation_populaire import (   # noqa: E402
    BASIC_LANDS,
    DECKLISTS_ROOT,
    DeckInfo,
    build_statistics,
    load_deck_dataset,
    normalize_name,
    parse_decklist_text,
)

# ═══════════════════════════════════════════════════════════════════════════════
# Constantes
# ═══════════════════════════════════════════════════════════════════════════════

WEIGHTS: dict[str, float] = {
    "popularity":  0.25,
    "specificity": 0.20,
    "synergy":     0.20,
    "strategy":    0.20,
    "structure":   0.10,
    "similarity":  0.05,
}

BASIC_LANDS_NORM: frozenset[str] = frozenset(normalize_name(c) for c in BASIC_LANDS)

# Thèmes stratégiques → mots-clés détectés dans oracle_text
THEME_KEYWORDS: dict[str, list[str]] = {
    "tribal_tokens":  ["token", "creature token", "create a", "populate", "enters the battlefield"],
    "artifacts":      ["artifact", "treasure", "equipment", "vehicle", "clue", "food"],
    "graveyard":      ["graveyard", "mill", "dies,", "return from your graveyard", "flashback"],
    "spellslinger":   ["instant or sorcery", "magecraft", "copy", "prowess", "noncreature spell"],
    "counters":       ["+1/+1 counter", "proliferate", "put a counter", "modified"],
    "lifegain":       ["gain life", "lifelink", "whenever you gain life"],
    "combat":         ["whenever a creature attacks", "combat damage", "double strike", "trample"],
    "lands":          ["land", "landfall", "search your library for a land"],
    "sacrifice":      ["sacrifice a creature", "sacrifice a permanent", "morbid"],
    "control":        ["counter target spell", "return target", "exile target permanent", "don't untap"],
    "reanimator":     ["return target creature card from your graveyard", "put onto the battlefield from a graveyard"],
}

# Rôles utilitaires génériques — ces cartes sont recommandables même partiellement hors thème
UTILITY_KEYWORDS: dict[str, list[str]] = {
    "ramp":       ["add {", "add one mana", "search your library for a basic land", "signet", "talisman"],
    "draw":       ["draw a card", "draw cards", "draw two", "draw three", "look at the top"],
    "removal":    ["destroy target", "exile target creature", "deal", "return target creature to its owner"],
    "board_wipe": ["destroy all", "exile all creatures", "deal damage to each creature"],
    "protection": ["hexproof", "indestructible", "shroud", "ward", "protection from"],
    "tutor":      ["search your library for a card", "search your library for an artifact"],
}

# Cibles structurelles
CATEGORY_TARGETS: dict[str, tuple[int, int]] = {
    "ramp":       (8, 12),
    "draw":       (8, 12),
    "removal":    (7, 10),
    "board_wipe": (2,  4),
}

# ═══════════════════════════════════════════════════════════════════════════════
# Dataclasses
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class CardDbData:
    """Données PostgreSQL pour une carte."""
    name: str
    color_identity: list[str] = field(default_factory=list)
    legal_commander: bool | None = None
    oracle_text: str = ""
    type_line: str = ""
    keywords: list[str] = field(default_factory=list)


@dataclass
class CommanderProfile:
    """Profil stratégique détecté pour le commandant."""
    name: str
    color_identity: list[str]
    primary_theme: str
    secondary_themes: list[str]
    preferred_subtypes: list[str]
    strategy_keywords: list[str]
    preferred_roles: list[str]
    source: str   # "database" | "dataset_inference" | "minimal_fallback"


@dataclass
class CandidateValidation:
    """Résultat de validation d'une carte candidate."""
    is_valid: bool
    rejection_reason: str | None
    warnings: list[str] = field(default_factory=list)


@dataclass
class RejectedCard:
    """Carte rejetée — pour le debug CSV."""
    name: str
    rejection_reason: str
    card_color_identity: list[str]
    commander_color_identity: list[str]
    type_line: str
    oracle_excerpt: str
    strategy_score: float | None
    detected_role: str


@dataclass
class CardScoreV2:
    """Scores détaillés d'une carte recommandée."""
    name: str
    popularity: float = 0.0
    specificity: float = 0.0
    synergy: float = 0.0
    strategy: float = 0.0
    structure: float = 0.0
    similarity: float = 0.0
    final: float = 0.0
    reason: str = ""

    def compute_final(self) -> None:
        self.final = (
            WEIGHTS["popularity"]  * self.popularity
            + WEIGHTS["specificity"] * self.specificity
            + WEIGHTS["synergy"]     * self.synergy
            + WEIGHTS["strategy"]    * self.strategy
            + WEIGHTS["structure"]   * self.structure
            + WEIGHTS["similarity"]  * self.similarity
        )


# ═══════════════════════════════════════════════════════════════════════════════
# Normalisation DB
# ═══════════════════════════════════════════════════════════════════════════════

def _db_normalize(name: str) -> str:
    """Normalise un nom pour la recherche en base (minuscules, sans accents)."""
    nfd = unicodedata.normalize("NFD", name)
    return "".join(c for c in nfd if unicodedata.category(c) != "Mn").strip().lower()


# ═══════════════════════════════════════════════════════════════════════════════
# Accès base de données (optionnel)
# ═══════════════════════════════════════════════════════════════════════════════

def _get_session_factory() -> Any:
    """Retourne SessionLocal ou None si la DB est indisponible."""
    try:
        from src.manamind.db.engine import SessionLocal   # type: ignore
        return SessionLocal
    except Exception:
        return None


def fetch_commander_from_db(name: str) -> CardDbData | None:
    """Charge les données du commandant depuis PostgreSQL."""
    SessionLocal = _get_session_factory()
    if not SessionLocal:
        return None
    try:
        from sqlalchemy import select   # type: ignore
        from src.manamind.db.models.card import Card   # type: ignore
        with SessionLocal() as session:
            row = session.execute(
                select(Card).where(Card.normalized_name == _db_normalize(name))
            ).scalar_one_or_none()
            if row:
                return CardDbData(
                    name=row.name,
                    color_identity=row.color_identity or [],
                    legal_commander=row.legal_commander,
                    oracle_text=row.oracle_text or "",
                    type_line=row.type_line or "",
                    keywords=row.keywords or [],
                )
    except Exception as exc:
        print(f"[V2] DB commandant '{name}': {exc}", file=sys.stderr)
    return None


def fetch_cards_from_db(card_names: list[str]) -> dict[str, CardDbData]:
    """Charge les données de cartes en batch depuis PostgreSQL."""
    SessionLocal = _get_session_factory()
    if not SessionLocal or not card_names:
        return {}
    try:
        from sqlalchemy import select   # type: ignore
        from src.manamind.db.models.card import Card   # type: ignore

        norm_to_orig: dict[str, str] = {_db_normalize(n): n for n in card_names}
        results: dict[str, CardDbData] = {}
        norms = list(norm_to_orig.keys())
        CHUNK = 500

        with SessionLocal() as session:
            for i in range(0, len(norms), CHUNK):
                chunk = norms[i : i + CHUNK]
                rows = session.execute(
                    select(
                        Card.normalized_name, Card.name, Card.color_identity,
                        Card.legal_commander, Card.oracle_text, Card.type_line, Card.keywords,
                    ).where(Card.normalized_name.in_(chunk))
                ).all()
                for row in rows:
                    orig = norm_to_orig.get(row.normalized_name, row.name)
                    results[orig] = CardDbData(
                        name=row.name,
                        color_identity=row.color_identity or [],
                        legal_commander=row.legal_commander,
                        oracle_text=row.oracle_text or "",
                        type_line=row.type_line or "",
                        keywords=row.keywords or [],
                    )
        return results
    except Exception as exc:
        print(f"[V2] DB batch cartes: {exc}", file=sys.stderr)
        return {}


# ═══════════════════════════════════════════════════════════════════════════════
# Construction du profil stratégique
# ═══════════════════════════════════════════════════════════════════════════════

def _extract_subtypes(type_line: str) -> list[str]:
    """Extrait les sous-types depuis la type_line ('Creature — Human Cleric' → ['Human','Cleric'])."""
    sep = "—" if "—" in type_line else ("-" if "-" in type_line else None)
    if sep:
        return [s.strip() for s in type_line.split(sep, 1)[1].split()]
    return []


def _detect_themes(text: str) -> list[str]:
    """Retourne les thèmes détectés dans un texte, triés par nombre de correspondances."""
    t = text.lower()
    scores: dict[str, int] = {
        theme: sum(1 for kw in kws if kw in t)
        for theme, kws in THEME_KEYWORDS.items()
    }
    return [th for th in sorted(scores, key=lambda x: scores[x], reverse=True) if scores[th] > 0]


def _detect_utility_role(oracle: str, type_line: str) -> str:
    """Retourne le rôle utilitaire principal d'une carte."""
    if "land" in type_line.lower():
        return "land"
    ol = oracle.lower()
    for role, kws in UTILITY_KEYWORDS.items():
        if any(kw in ol for kw in kws):
            return role
    return "other"


def _infer_from_dataset(cmd_decks: list[DeckInfo]) -> tuple[str, list[str]]:
    """Déduit le thème depuis les noms des cartes populaires du commandant."""
    if not cmd_decks:
        return "generic", []
    freq: Counter[str] = Counter()
    for d in cmd_decks:
        freq.update(d.cards)
    combined = " ".join(n for n, _ in freq.most_common(60)).lower()
    themes = _detect_themes(combined)
    return (themes[0] if themes else "generic"), themes[1:4]


def build_commander_profile(
    name: str,
    db_data: CardDbData | None,
    cmd_decks: list[DeckInfo],
    deck_frequency: Counter[str],
) -> CommanderProfile:
    """Construit le profil stratégique. Priorité : DB > dataset > minimal."""
    if db_data:
        themes = _detect_themes(db_data.oracle_text)
        primary = themes[0] if themes else "generic"
        secondary = themes[1:4]

        # Compléter avec le dataset si l'oracle est peu informatif
        if primary == "generic":
            ds_p, ds_s = _infer_from_dataset(cmd_decks)
            if ds_p != "generic":
                primary, secondary = ds_p, ds_s

        subtypes = _extract_subtypes(db_data.type_line)
        strat_kws = THEME_KEYWORDS.get(primary, [])
        roles = list(dict.fromkeys([primary] + secondary + ["draw", "ramp", "removal", "board_wipe", "protection"]))

        return CommanderProfile(
            name=name, color_identity=db_data.color_identity,
            primary_theme=primary, secondary_themes=secondary,
            preferred_subtypes=subtypes, strategy_keywords=strat_kws,
            preferred_roles=roles, source="database",
        )

    # Fallback dataset
    primary, secondary = _infer_from_dataset(cmd_decks)
    return CommanderProfile(
        name=name, color_identity=[],
        primary_theme=primary, secondary_themes=secondary,
        preferred_subtypes=[], strategy_keywords=THEME_KEYWORDS.get(primary, []),
        preferred_roles=list(dict.fromkeys([primary] + secondary + ["draw", "ramp", "removal"])),
        source="dataset_inference",
    )


# ═══════════════════════════════════════════════════════════════════════════════
# Validation
# ═══════════════════════════════════════════════════════════════════════════════

def is_color_identity_legal(card_ci: list[str], commander_ci: list[str]) -> bool:
    """card_color_identity ⊆ commander_color_identity. Incolore toujours OK."""
    if not commander_ci:
        return True
    return set(card_ci).issubset(set(commander_ci))


def validate_candidate(
    card_name: str,
    card_data: CardDbData | None,
    profile: CommanderProfile,
) -> CandidateValidation:
    """Valide une carte avant scoring. Retourne is_valid=False si rejet définitif."""
    if card_data is None:
        return CandidateValidation(
            is_valid=True, rejection_reason=None,
            warnings=["Absent de la base — identité couleur non vérifiée"],
        )

    if profile.color_identity:
        if not is_color_identity_legal(card_data.color_identity, profile.color_identity):
            return CandidateValidation(
                is_valid=False,
                rejection_reason="Hors identité couleur du commandant",
            )

    if card_data.legal_commander is False:
        return CandidateValidation(is_valid=False, rejection_reason="Non légale en Commander")

    return CandidateValidation(is_valid=True, rejection_reason=None)


# ═══════════════════════════════════════════════════════════════════════════════
# Scoring stratégique
# ═══════════════════════════════════════════════════════════════════════════════

def compute_strategy_score(
    card_data: CardDbData | None,
    profile: CommanderProfile,
) -> tuple[float, str]:
    """Retourne (strategy_score 0-1, reason)."""
    if card_data is None:
        return 0.30, "Données oracle indisponibles — support supposé."

    oracle = (card_data.oracle_text or "").lower()
    type_line = card_data.type_line or ""
    subtypes = _extract_subtypes(type_line)
    is_creature = "creature" in type_line.lower() and "land" not in type_line.lower()

    score = 0.0
    reasons: list[str] = []

    # Thème principal
    primary_kws = THEME_KEYWORDS.get(profile.primary_theme, [])
    if any(kw in oracle for kw in primary_kws):
        score += 0.40
        reasons.append("Correspond au thème principal du commandant.")

    # Thèmes secondaires
    sec_bonus = 0.0
    for theme in profile.secondary_themes[:3]:
        if any(kw in oracle for kw in THEME_KEYWORDS.get(theme, [])):
            sec_bonus = min(sec_bonus + 0.10, 0.20)
            reasons.append("Correspond à un thème secondaire du deck.")
    score += sec_bonus

    # Subtype préféré
    if profile.preferred_subtypes:
        pref_low = {s.lower() for s in profile.preferred_subtypes}
        if any(s.lower() in pref_low for s in subtypes):
            score += 0.20
            reasons.append("Partage un subtype important avec la stratégie.")

    # Mots-clés stratégiques
    kw_bonus = 0.0
    for kw in profile.strategy_keywords:
        if kw in oracle:
            kw_bonus = min(kw_bonus + 0.10, 0.20)
    if kw_bonus > 0:
        score += kw_bonus
        reasons.append("Mentionne un mot-clé stratégique du commandant.")

    # Rôle utilitaire — floor 0.30
    role = _detect_utility_role(card_data.oracle_text, type_line)
    role_labels = {
        "draw": "Carte de support utile : pioche.",
        "ramp": "Carte de support utile : ramp.",
        "removal": "Carte de support utile : removal.",
        "board_wipe": "Carte de support utile : board wipe.",
        "protection": "Carte de support utile : protection.",
        "tutor": "Carte de support utile : tutor.",
        "land": "Terrain.",
    }
    if role in role_labels:
        score = max(score, 0.30)
        if not reasons:
            reasons.append(role_labels[role])

    # Pénalité créature hors thème
    if is_creature and score < 0.25 and role == "other":
        no_sub = not profile.preferred_subtypes or not any(
            s.lower() in {p.lower() for p in profile.preferred_subtypes} for s in subtypes
        )
        if no_sub:
            score *= 0.40

    score = min(score, 1.0)
    reason = " ".join(dict.fromkeys(reasons)) if reasons else "Fréquente dans les decks similaires."
    return score, reason


# ═══════════════════════════════════════════════════════════════════════════════
# Structure score
# ═══════════════════════════════════════════════════════════════════════════════

def _structure_deficits(user_data: dict[str, CardDbData]) -> dict[str, float]:
    """Retourne le déficit structurel du deck (0=bien couvert, 1=absent)."""
    counts: Counter[str] = Counter(
        _detect_utility_role(d.oracle_text, d.type_line) for d in user_data.values()
    )
    mapping = {"ramp": "ramp", "draw": "draw", "removal": "removal", "board_wipe": "board_wipe"}
    return {
        cat: max(0.0, (mn - counts.get(mapping.get(cat, cat), 0)) / mn)
        for cat, (mn, _) in CATEGORY_TARGETS.items()
    }


# ═══════════════════════════════════════════════════════════════════════════════
# Similarité Jaccard
# ═══════════════════════════════════════════════════════════════════════════════

def _jaccard(a: set[str], b: set[str]) -> float:
    union = len(a | b)
    return len(a & b) / union if union else 0.0


# ═══════════════════════════════════════════════════════════════════════════════
# Seuils de qualité
# ═══════════════════════════════════════════════════════════════════════════════

def meets_quality_thresholds(cs: CardScoreV2, commander_rate: float) -> bool:
    if cs.final < 0.20:
        return False
    if cs.strategy < 0.20 and cs.popularity < 0.10:
        return False
    if commander_rate < 0.02 and cs.synergy < 0.10 and cs.strategy < 0.40:
        return False
    return True


# ═══════════════════════════════════════════════════════════════════════════════
# Recommandations
# ═══════════════════════════════════════════════════════════════════════════════

def recommend_additions_v2(
    user_cards: set[str],
    commander: str | None,
    decks: list[DeckInfo],
    deck_frequency: Counter[str],
    commander_decks: dict[str, list[DeckInfo]],
    cooccurrence: dict[str, Counter[str]],
    profile: CommanderProfile,
    limit: int = 20,
) -> tuple[list[CardScoreV2], list[RejectedCard]]:
    """Génère les additions V2 avec validation et scoring complet."""
    total_decks = len(decks)
    if total_decks == 0:
        return [], []

    # Decks du commandant
    cmd_decks: list[DeckInfo] = commander_decks.get(commander or "", [])
    if not cmd_decks and commander:
        cmd_lower = commander.lower()
        for key, val in commander_decks.items():
            if key.lower() == cmd_lower:
                cmd_decks = val
                break
    if not cmd_decks:
        cmd_decks = decks
    n_cmd = len(cmd_decks)

    # Fréquence dans les decks du commandant
    cmd_freq: Counter[str] = Counter()
    for d in cmd_decks:
        cmd_freq.update(d.cards)

    # Candidats
    user_norm: set[str] = {normalize_name(c) for c in user_cards}
    candidates: set[str] = {
        card for card, freq in cmd_freq.items()
        if card not in user_norm
        and normalize_name(card) not in BASIC_LANDS_NORM
        and freq >= 2
    }
    if not candidates:
        candidates = {
            c for c in deck_frequency
            if c not in user_norm and normalize_name(c) not in BASIC_LANDS_NORM
        }

    # Fetch DB
    card_db = fetch_cards_from_db(list(candidates))
    user_db = fetch_cards_from_db(list(user_norm))
    deficits = _structure_deficits(user_db)

    # Scores de base
    max_cmd_freq = max(cmd_freq.values(), default=1) or 1

    raw_spec: dict[str, float] = {
        card: (cmd_freq.get(card, 0) / n_cmd) / max((deck_frequency.get(card, 0) / total_decks), 0.01)
        for card in candidates
    }
    max_spec = max(raw_spec.values(), default=1.0) or 1.0

    sim_weights: Counter[str] = Counter()
    for d in cmd_decks:
        j = _jaccard(user_norm, d.cards)
        for card in d.cards:
            if card in candidates:
                sim_weights[card] += j
    max_sim = max(sim_weights.values(), default=1.0) or 1.0

    synergy_raw: dict[str, float] = {
        card: float(sum(cooccurrence.get(u, Counter()).get(card, 0) for u in user_norm))
        for card in candidates
    }
    max_syn = max(synergy_raw.values(), default=1.0) or 1.0

    # Scoring + validation
    results: list[CardScoreV2] = []
    rejected: list[RejectedCard] = []

    for card in candidates:
        data = card_db.get(card)
        val = validate_candidate(card, data, profile)

        if not val.is_valid:
            rejected.append(RejectedCard(
                name=card, rejection_reason=val.rejection_reason or "Rejeté",
                card_color_identity=data.color_identity if data else [],
                commander_color_identity=profile.color_identity,
                type_line=data.type_line if data else "",
                oracle_excerpt=(data.oracle_text[:120] if data else ""),
                strategy_score=None,
                detected_role="",
            ))
            continue

        strat, strat_reason = compute_strategy_score(data, profile)
        role = _detect_utility_role(data.oracle_text if data else "", data.type_line if data else "")
        cat = role if role in CATEGORY_TARGETS else "other"

        cs = CardScoreV2(
            name=card,
            popularity=cmd_freq.get(card, 0) / max_cmd_freq,
            specificity=raw_spec.get(card, 0.0) / max_spec,
            synergy=synergy_raw.get(card, 0.0) / max_syn,
            strategy=strat,
            structure=deficits.get(cat, 0.0),
            similarity=sim_weights.get(card, 0.0) / max_sim,
        )
        cs.compute_final()
        cs.reason = strat_reason

        commander_rate = cmd_freq.get(card, 0) / n_cmd
        if not meets_quality_thresholds(cs, commander_rate):
            rejected.append(RejectedCard(
                name=card,
                rejection_reason=f"Seuil de qualité non atteint (score={cs.final:.3f})",
                card_color_identity=data.color_identity if data else [],
                commander_color_identity=profile.color_identity,
                type_line=data.type_line if data else "",
                oracle_excerpt=(data.oracle_text[:120] if data else ""),
                strategy_score=strat,
                detected_role=role,
            ))
            continue

        results.append(cs)

    results.sort(key=lambda x: x.final, reverse=True)
    return results[:limit], rejected


def recommend_removals_v2(
    user_cards: set[str],
    commander: str | None,
    commander_decks: dict[str, list[DeckInfo]],
    deck_frequency: Counter[str],
    cooccurrence: dict[str, Counter[str]],
    limit: int = 20,
) -> list[tuple[str, int, float]]:
    """Retourne les cartes du deck les moins synergiques (candidates au retrait)."""
    user_norm = {normalize_name(c) for c in user_cards}
    cmd_norm = normalize_name(commander).lower() if commander else None
    cmd_ref = commander_decks.get(commander or "", [])

    scored: list[tuple[str, int, float]] = []
    for card in user_norm:
        if normalize_name(card) in BASIC_LANDS_NORM:
            continue
        if cmd_norm and card.lower() == cmd_norm:
            continue
        support = (
            sum(1 for d in cmd_ref if card in d.cards)
            if cmd_ref
            else sum(cooccurrence.get(o, Counter()).get(card, 0) for o in user_norm if o != card)
        )
        scored.append((card, int(support), float(deck_frequency.get(card, 0))))

    scored.sort(key=lambda x: (x[1], x[2]))
    return scored[:limit]


# ═══════════════════════════════════════════════════════════════════════════════
# Sorties
# ═══════════════════════════════════════════════════════════════════════════════

def save_recommendations_v2(
    output_path: Path,
    commander: str | None,
    additions: list[CardScoreV2],
    removals: list[tuple[str, int, float]],
) -> None:
    """CSV compatible V1 (5 colonnes base) + 7 colonnes V2 supplémentaires."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow([
            "Section", "Card Name", "Score", "Support", "Deck Frequency",
            "Popularity", "Specificity", "Synergy", "Strategy",
            "Structure", "Similarity", "Reason",
        ])
        w.writerow(["Commander", commander or "unknown", "", "", "", "", "", "", "", "", "", ""])
        w.writerow([])
        w.writerow(["Additions", "", "", "", "", "", "", "", "", "", "", ""])
        for cs in additions:
            w.writerow([
                "add", cs.name, round(cs.final * 2000), "", "",
                round(cs.popularity, 4), round(cs.specificity, 4),
                round(cs.synergy, 4), round(cs.strategy, 4),
                round(cs.structure, 4), round(cs.similarity, 4),
                cs.reason,
            ])
        w.writerow([])
        w.writerow(["Removals", "", "", "", "", "", "", "", "", "", "", ""])
        for card, support, freq in removals:
            w.writerow(["remove", card, "", support, round(freq), "", "", "", "", "", "", ""])


def save_debug_rejected(output_path: Path, rejected: list[RejectedCard]) -> None:
    """Écrit outputs/<stem>_rejected_debug.csv avec les cartes rejetées."""
    if not rejected:
        return
    debug = output_path.parent / (output_path.stem + "_rejected_debug.csv")
    with debug.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow([
            "Card Name", "Rejection Reason", "Card Color Identity",
            "Commander Color Identity", "Type Line",
            "Oracle Excerpt", "Strategy Score", "Detected Role",
        ])
        for r in rejected:
            w.writerow([
                r.name, r.rejection_reason,
                "|".join(r.card_color_identity), "|".join(r.commander_color_identity),
                r.type_line, r.oracle_excerpt,
                f"{r.strategy_score:.3f}" if r.strategy_score is not None else "",
                r.detected_role,
            ])


def save_profile_json(output_path: Path, profile: CommanderProfile) -> None:
    """Sauvegarde le profil stratégique en JSON pour la page de résultats."""
    ppath = output_path.parent / (output_path.stem + "_profile.json")
    ppath.write_text(
        json.dumps({
            "name": profile.name,
            "color_identity": profile.color_identity,
            "primary_theme": profile.primary_theme,
            "secondary_themes": profile.secondary_themes,
            "preferred_subtypes": profile.preferred_subtypes,
            "strategy_keywords": profile.strategy_keywords[:8],
            "preferred_roles": profile.preferred_roles,
            "source": profile.source,
        }, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


# ═══════════════════════════════════════════════════════════════════════════════
# Pipeline
# ═══════════════════════════════════════════════════════════════════════════════

def run(input_path: Path, output_path: Path, limit: int = 20) -> None:
    """Pipeline complet V2."""
    try:
        cards_dict, commander = parse_decklist_text(input_path)
    except Exception as exc:
        print(f"[V2] Erreur parsing : {exc}", file=sys.stderr)
        return

    user_cards: set[str] = set(cards_dict.keys())
    if not user_cards:
        print("[V2] Decklist vide.", file=sys.stderr)
        return

    if not DECKLISTS_ROOT.exists():
        print(f"[V2] Dataset introuvable : {DECKLISTS_ROOT}", file=sys.stderr)
        return

    decks = load_deck_dataset(DECKLISTS_ROOT)
    if not decks:
        print("[V2] Aucun deck de référence.", file=sys.stderr)
        return

    deck_frequency, commander_decks, cooccurrence = build_statistics(decks)

    # Profil du commandant
    cmd_db = fetch_commander_from_db(commander) if commander else None
    cmd_specific = commander_decks.get(commander or "", decks[:20])
    profile = build_commander_profile(commander or "Unknown", cmd_db, cmd_specific, deck_frequency)

    print(
        f"[V2] Profil : {profile.primary_theme} | "
        f"CI: {profile.color_identity} | source: {profile.source}"
    )

    additions, rejected = recommend_additions_v2(
        user_cards, commander, decks, deck_frequency,
        commander_decks, cooccurrence, profile, limit,
    )
    removals = recommend_removals_v2(
        user_cards, commander, commander_decks, deck_frequency, cooccurrence, limit,
    )

    save_recommendations_v2(output_path, commander, additions, removals)
    save_debug_rejected(output_path, rejected)
    save_profile_json(output_path, profile)

    print(f"[V2] {len(additions)} ajouts | {len(rejected)} rejetés | {len(removals)} retraits")
    print(f"[V2] → {output_path}")


def main() -> None:
    parser = argparse.ArgumentParser(description="ManaMind V2 — score hybride corrigé")
    parser.add_argument("--input",  required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--limit",  type=int, default=20)
    args = parser.parse_args()
    run(args.input, args.output, args.limit)


if __name__ == "__main__":
    main()
