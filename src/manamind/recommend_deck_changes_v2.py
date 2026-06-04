#!/usr/bin/env python3
"""
ManaMind — Recommend Deck Changes V2
======================================
Moteur de recommandation hybride basé sur cinq sous-scores :

    final_score = 0.40 * popularity
                + 0.25 * specificity
                + 0.20 * synergy
                + 0.10 * structure
                + 0.05 * similarity

Usage (CLI) :
    python src/manamind/recommend_deck_changes_v2.py \\
        --input  uploads/my_deck.txt \\
        --output outputs/recommendations_v2_my_deck.csv
"""

from __future__ import annotations

import argparse
import csv
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import NamedTuple

# ── Ré-utilisation des utilitaires V1 ──────────────────────────────────────
_HERE = Path(__file__).resolve().parent
_ROOT = _HERE.parents[1]           # project root  (src/manamind/ → ../../)
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "src"))

from manamind.recommend_deck_changes import (   # noqa: E402
    BASIC_LANDS,
    DECKLISTS_ROOT,
    DeckInfo,
    build_statistics,
    load_deck_dataset,
    normalize_name,
    parse_decklist_text,
)

# ── Constantes ──────────────────────────────────────────────────────────────
WEIGHTS: dict[str, float] = {
    "popularity":  0.40,
    "specificity": 0.25,
    "synergy":     0.20,
    "structure":   0.10,
    "similarity":  0.05,
}

BASIC_LANDS_NORM: frozenset[str] = frozenset(normalize_name(c) for c in BASIC_LANDS)

# Cibles structurelles indicatives (min, max recommandés pour un deck Commander)
CATEGORY_TARGETS: dict[str, tuple[int, int]] = {
    "ramp":    (8, 12),
    "draw":    (8, 12),
    "removal": (7, 10),
    "wipe":    (2,  4),
}

# Listes de cartes connues par catégorie (noms normalisés, minuscules)
_RAMP: frozenset[str] = frozenset({
    "sol ring", "arcane signet", "commander's sphere", "mind stone",
    "fellwar stone", "wayfarer's bauble", "mana crypt", "mana vault",
    "jeweled lotus", "dockside extortionist", "cultivate", "kodama's reach",
    "farseek", "rampant growth", "three visits", "nature's lore",
    "noble hierarch", "birds of paradise", "llanowar elves", "elvish mystic",
    "fyndhorn elves", "avacyn's pilgrim", "bloom tender",
    "selvala, heart of the wilds", "selvala, explorer returned",
    "high tide", "dark ritual", "cabal ritual", "pyretic ritual",
    "seething song", "talisman of dominance", "talisman of creativity",
    "talisman of conviction", "talisman of impulse", "talisman of resilience",
    "talisman of curiosity", "talisman of progress", "talisman of indulgence",
    "talisman of unity", "talisman of hierarchy", "talisman of vigilance",
    "dimir signet", "izzet signet", "golgari signet", "rakdos signet",
    "gruul signet", "selesnya signet", "orzhov signet", "boros signet",
    "simic signet", "azorius signet",
})

_DRAW: frozenset[str] = frozenset({
    "rhystic study", "mystic remora", "sylvan library", "phyrexian arena",
    "necropotence", "the one ring", "esper sentinel", "consecrated sphinx",
    "brainstorm", "ponder", "preordain", "night's whisper", "sign in blood",
    "divination", "read the bones", "painful truths", "ad nauseam",
    "windfall", "wheel of fortune", "notion thief", "timetwister",
    "treasure cruise", "dig through time", "frantic search",
    "pull from tomorrow", "fact or fiction", "gifts ungiven",
    "blue sun's zenith", "stroke of genius",
})

_REMOVAL: frozenset[str] = frozenset({
    "swords to plowshares", "path to exile", "beast within", "chaos warp",
    "reality shift", "pongify", "rapid hybridization", "cyclonic rift",
    "generous gift", "anguished unmaking", "vindicate", "counterspell",
    "force of will", "mana drain", "swan song", "delay", "negate",
    "mystical dispute", "dovin's veto", "fierce guardianship",
    "lightning bolt", "fatal push", "dismember", "hero's downfall",
    "go for the throat", "doom blade", "terminate", "abrupt decay",
    "assassin's trophy", "feed the swarm", "infernal grasp",
    "heartless act", "cut down", "deadly rollick",
    "song of the dryads", "darksteel mutation", "lignify",
    "imprisoned in the moon", "oblivion ring", "banishing light",
    "grasp of fate", "kenrith's transformation",
})

_WIPE: frozenset[str] = frozenset({
    "wrath of god", "damnation", "supreme verdict", "blasphemous act",
    "austere command", "toxic deluge", "farewell", "day of judgment",
    "hour of revelation", "vanquish the horde", "crux of fate",
    "devastation tide", "rout", "cleansing nova", "planar cleansing",
    "merciless eviction", "flood of tears", "martial coup",
    "settle the wreckage", "kirtar's wrath",
})

# Suffixes de signet / talisman pour détection générique
_RAMP_SUFFIXES: tuple[str, ...] = ("signet", "talisman of")


# ── Structures de données ────────────────────────────────────────────────────
@dataclass
class CardScore:
    """Scores détaillés pour une carte candidate."""

    name: str
    popularity:  float = 0.0
    specificity: float = 0.0
    synergy:     float = 0.0
    structure:   float = 0.0
    similarity:  float = 0.0
    final:       float = 0.0
    reason:      str   = ""

    def compute_final(self) -> None:
        """Calcule le score final pondéré et génère la raison dominante."""
        self.final = (
            WEIGHTS["popularity"]  * self.popularity
            + WEIGHTS["specificity"] * self.specificity
            + WEIGHTS["synergy"]     * self.synergy
            + WEIGHTS["structure"]   * self.structure
            + WEIGHTS["similarity"]  * self.similarity
        )
        self.reason = _dominant_reason(self)


def _dominant_reason(cs: CardScore) -> str:
    """Retourne une explication courte basée sur le sous-score dominant."""
    scores = {
        "popularity":  cs.popularity,
        "specificity": cs.specificity,
        "synergy":     cs.synergy,
        "structure":   cs.structure,
        "similarity":  cs.similarity,
    }
    dominant = max(scores, key=lambda k: scores[k])
    messages = {
        "popularity":  "Très présente dans les decks similaires.",
        "specificity": "Carte spécifique à ce commandant.",
        "synergy":     "Bonne synergie avec plusieurs cartes du deck.",
        "structure":   "Corrige un manque structural du deck.",
        "similarity":  "Présente dans les decks les plus proches du vôtre.",
    }
    return messages.get(dominant, "")


# ── Catégorisation structurelle ─────────────────────────────────────────────
def _card_category(name: str) -> str:
    """
    Catégorise une carte à partir de son nom normalisé.
    Renvoie : 'ramp' | 'draw' | 'removal' | 'wipe' | 'land' | 'other'.
    """
    n = normalize_name(name).lower()
    if n in BASIC_LANDS_NORM or n in {"island", "plains", "swamp", "mountain", "forest", "wastes"}:
        return "land"
    if n in _RAMP or any(n.endswith(s) for s in _RAMP_SUFFIXES):
        return "ramp"
    if n in _DRAW:
        return "draw"
    if n in _REMOVAL:
        return "removal"
    if n in _WIPE:
        return "wipe"
    return "other"


def _structure_deficits(user_cards: set[str]) -> dict[str, float]:
    """
    Retourne pour chaque catégorie un score de déficit entre 0 et 1.
    1.0 = catégorie absente, 0.0 = catégorie bien couverte.
    """
    counts: Counter[str] = Counter(_card_category(c) for c in user_cards)
    deficits: dict[str, float] = {}
    for cat, (min_t, _max_t) in CATEGORY_TARGETS.items():
        current = counts.get(cat, 0)
        deficits[cat] = max(0.0, (min_t - current) / min_t) if min_t > 0 else 0.0
    return deficits


# ── Similarité Jaccard ───────────────────────────────────────────────────────
def _jaccard(a: set[str], b: set[str]) -> float:
    """Similarité de Jaccard entre deux ensembles de cartes."""
    union = len(a | b)
    return len(a & b) / union if union else 0.0


# ── Algorithme V2 ────────────────────────────────────────────────────────────
def recommend_additions_v2(
    user_cards: set[str],
    commander: str | None,
    decks: list[DeckInfo],
    deck_frequency: Counter[str],
    commander_decks: dict[str, list[DeckInfo]],
    cooccurrence: dict[str, Counter[str]],
    limit: int = 20,
) -> list[CardScore]:
    """
    Calcule les recommandations d'ajout avec le scoring hybride V2.

    Parameters
    ----------
    user_cards :
        Cartes du deck utilisateur (noms normalisés).
    commander :
        Nom normalisé du commandant (peut être None).
    decks :
        Ensemble complet des decks de référence.
    deck_frequency :
        Fréquence globale de chaque carte dans le dataset.
    commander_decks :
        Index commandant → liste de DeckInfo.
    cooccurrence :
        Matrice de co-occurrence carte → Counter.
    limit :
        Nombre de recommandations à retourner.

    Returns
    -------
    list[CardScore]
        Cartes triées par score final décroissant.
    """
    total_decks = len(decks)
    if total_decks == 0:
        return []

    # ── Restreindre aux decks du commandant si possible ──────────────────────
    cmd_decks: list[DeckInfo] = []
    if commander:
        cmd_decks = commander_decks.get(commander, [])
        # Fallback insensible à la casse
        if not cmd_decks:
            cmd_lower = commander.lower()
            for key, val in commander_decks.items():
                if key.lower() == cmd_lower:
                    cmd_decks = val
                    break
    if not cmd_decks:
        cmd_decks = decks   # fallback global

    n_cmd = len(cmd_decks)

    # ── Fréquence dans les decks du commandant ───────────────────────────────
    cmd_freq: Counter[str] = Counter()
    for d in cmd_decks:
        for card in d.cards:
            cmd_freq[card] += 1

    # ── Candidats : présents dans le dataset, absents du deck utilisateur ────
    user_norm = {normalize_name(c) for c in user_cards}
    candidates: set[str] = {
        card
        for card, freq in cmd_freq.items()
        if card not in user_norm
        and normalize_name(card) not in BASIC_LANDS_NORM
        and freq >= 2
    }
    if not candidates:
        candidates = {
            card
            for card in deck_frequency
            if card not in user_norm
            and normalize_name(card) not in BASIC_LANDS_NORM
        }
    if not candidates:
        return []

    # ── 1. Popularité ────────────────────────────────────────────────────────
    max_cmd_freq = max((cmd_freq.get(c, 0) for c in candidates), default=1) or 1

    # ── 2. Spécificité ───────────────────────────────────────────────────────
    raw_spec: dict[str, float] = {}
    for card in candidates:
        cmd_rate    = cmd_freq.get(card, 0) / n_cmd
        global_rate = deck_frequency.get(card, 0) / total_decks
        raw_spec[card] = cmd_rate / max(global_rate, 0.01)
    max_spec = max(raw_spec.values(), default=1.0) or 1.0

    # ── 3. Similarité ────────────────────────────────────────────────────────
    sim_weights: Counter[str] = Counter()
    total_sim = 0.0
    for d in cmd_decks:
        j = _jaccard(user_norm, d.cards)
        total_sim += j
        for card in d.cards:
            if card in candidates:
                sim_weights[card] += j
    max_sim = max(sim_weights.values(), default=1.0) or 1.0

    # ── 4. Synergie ──────────────────────────────────────────────────────────
    synergy_raw: dict[str, float] = {
        card: float(sum(
            cooccurrence.get(u, Counter()).get(card, 0) for u in user_norm
        ))
        for card in candidates
    }
    max_syn = max(synergy_raw.values(), default=1.0) or 1.0

    # ── 5. Structure ─────────────────────────────────────────────────────────
    deficits = _structure_deficits(user_norm)

    def _struct(card: str) -> float:
        return deficits.get(_card_category(card), 0.0)

    # ── Assemblage ───────────────────────────────────────────────────────────
    results: list[CardScore] = []
    for card in candidates:
        cs = CardScore(name=card)
        cs.popularity  = cmd_freq.get(card, 0) / max_cmd_freq
        cs.specificity = raw_spec.get(card, 0.0) / max_spec
        cs.similarity  = sim_weights.get(card, 0.0) / max_sim
        cs.synergy     = synergy_raw.get(card, 0.0) / max_syn
        cs.structure   = _struct(card)
        cs.compute_final()
        results.append(cs)

    results.sort(key=lambda x: x.final, reverse=True)
    return results[:limit]


def recommend_removals_v2(
    user_cards: set[str],
    commander: str | None,
    commander_decks: dict[str, list[DeckInfo]],
    deck_frequency: Counter[str],
    cooccurrence: dict[str, Counter[str]],
    limit: int = 20,
) -> list[tuple[str, int, float]]:
    """
    Suggère les cartes à retirer du deck utilisateur.

    Trie par support croissant (faible synergie = priorité de retrait),
    puis par fréquence globale croissante (carte rare/peu jouée).

    Returns
    -------
    list of (card_name, support, deck_frequency)
    """
    user_norm = {normalize_name(c) for c in user_cards}
    cmd_norm  = normalize_name(commander) if commander else None

    candidates = [
        c for c in user_norm
        if c not in BASIC_LANDS_NORM and c != cmd_norm
    ]

    scored: list[tuple[str, int, float]] = []
    cmd_ref = commander_decks.get(commander or "", [])

    for card in candidates:
        if cmd_ref:
            support = sum(1 for d in cmd_ref if card in d.cards)
        else:
            support = sum(
                cooccurrence.get(other, Counter()).get(card, 0)
                for other in user_norm if other != card
            )
        freq = deck_frequency.get(card, 0)
        scored.append((card, int(support), float(freq)))

    scored.sort(key=lambda x: (x[1], x[2]))
    return scored[:limit]


# ── Écriture CSV ─────────────────────────────────────────────────────────────
def save_recommendations_v2(
    output_path: Path,
    commander: str | None,
    additions: list[CardScore],
    removals: list[tuple[str, int, float]],
) -> None:
    """
    Écrit le CSV de résultats V2.

    Les 5 premières colonnes sont identiques au format V1 (compatibles avec
    l'affichage existant). Les colonnes supplémentaires contiennent le détail
    des sous-scores et la raison, ignorés par l'affichage actuel.
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([
            "Section", "Card Name", "Score", "Support", "Deck Frequency",
            "Popularity", "Specificity", "Synergy", "Structure", "Similarity", "Reason",
        ])
        writer.writerow(["Commander", commander or "unknown", "", "", "", "", "", "", "", "", ""])
        writer.writerow([])

        writer.writerow(["Additions", "", "", "", "", "", "", "", "", "", ""])
        for cs in additions:
            # Score affiché dans la même plage que V1 (~0–2000) pour cohérence visuelle
            display_score = round(cs.final * 2000)
            writer.writerow([
                "add", cs.name, display_score, "", "",
                round(cs.popularity, 4),
                round(cs.specificity, 4),
                round(cs.synergy, 4),
                round(cs.structure, 4),
                round(cs.similarity, 4),
                cs.reason,
            ])
        writer.writerow([])

        writer.writerow(["Removals", "", "", "", "", "", "", "", "", "", ""])
        for card, support, freq in removals:
            writer.writerow(["remove", card, "", support, round(freq), "", "", "", "", "", ""])


# ── Pipeline principal ───────────────────────────────────────────────────────
def run(input_path: Path, output_path: Path, limit: int = 20) -> None:
    """
    Lance le pipeline complet V2 : parsing → statistiques → scoring → CSV.

    Parameters
    ----------
    input_path  : Chemin vers le fichier .txt de la decklist utilisateur.
    output_path : Chemin de sortie du CSV de recommandations.
    limit       : Nombre maximum de recommandations par catégorie.
    """
    # 1. Parsing de la decklist utilisateur
    try:
        cards_dict, commander = parse_decklist_text(input_path)
    except Exception as exc:
        print(f"[V2] Erreur de parsing : {exc}", file=sys.stderr)
        save_recommendations_v2(output_path, None, [], [])
        return

    user_cards: set[str] = set(cards_dict.keys())

    if not user_cards:
        print("[V2] Decklist vide ou mal formatée.", file=sys.stderr)
        save_recommendations_v2(output_path, commander, [], [])
        return

    # 2. Chargement du dataset de référence
    data_dir = DECKLISTS_ROOT
    if not data_dir.exists():
        print(f"[V2] Dossier de decklists introuvable : {data_dir}", file=sys.stderr)
        save_recommendations_v2(output_path, commander, [], [])
        return

    decks = load_deck_dataset(data_dir)
    if not decks:
        print("[V2] Aucun deck de référence trouvé.", file=sys.stderr)
        save_recommendations_v2(output_path, commander, [], [])
        return

    # 3. Statistiques globales
    deck_frequency, commander_decks, cooccurrence = build_statistics(decks)

    # 4. Scoring V2
    additions = recommend_additions_v2(
        user_cards, commander, decks,
        deck_frequency, commander_decks, cooccurrence, limit,
    )
    removals = recommend_removals_v2(
        user_cards, commander,
        commander_decks, deck_frequency, cooccurrence, limit,
    )

    # 5. Sauvegarde
    save_recommendations_v2(output_path, commander, additions, removals)
    print(
        f"[V2] {len(additions)} ajouts et {len(removals)} retraits "
        f"écrits dans {output_path}"
    )


# ── CLI ──────────────────────────────────────────────────────────────────────
def main() -> None:
    parser = argparse.ArgumentParser(
        description="ManaMind — Recommandations V2 (score hybride)"
    )
    parser.add_argument("--input",  required=True, type=Path, help="Decklist utilisateur (.txt)")
    parser.add_argument("--output", required=True, type=Path, help="Fichier CSV de sortie")
    parser.add_argument("--limit",  type=int, default=20,    help="Nombre de recommandations par catégorie")
    args = parser.parse_args()
    run(args.input, args.output, args.limit)


if __name__ == "__main__":
    main()
