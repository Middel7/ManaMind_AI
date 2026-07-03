#!/usr/bin/env python3
"""
auto_annotate_clusters.py

Génère automatiquement des annotations pour les clusters sans annotation
en analysant les cartes présentes dans chaque cluster.

Les annotations existantes (generate_annotations.py) couvrent les IDs 0-102.
Ce script complète les IDs 103-199 et régénère cluster_annotations.json.

Usage:
    uv run python scripts/auto_annotate_clusters.py
    uv run python scripts/auto_annotate_clusters.py --dry-run   # aperçu sans écrire
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)-8s  %(message)s",
                    datefmt="%H:%M:%S", handlers=[logging.StreamHandler(sys.stdout)])
log = logging.getLogger(__name__)

CLUST_DIR  = ROOT / "data" / "clustering"
ANNOT_PATH = CLUST_DIR / "cluster_annotations.json"
SUMMARY    = CLUST_DIR / "cluster_summary.csv"

# ── Heuristiques basées sur les cartes ──────────────────────────────────────

# (pattern_dans_le_nom, strategy, family, tribe_ou_None)
CARD_RULES: list[tuple[str, str, str, str | None]] = [
    # Counterspell / contrôle bleu
    ("counterspell",      "Control",    "Control",     None),
    ("arcane denial",     "Control",    "Control",     None),
    ("cyclonic rift",     "Control",    "Control",     None),
    ("force of will",     "Control",    "Control",     None),
    ("mana drain",        "Control",    "Control",     None),
    # Draw / pioche
    ("brainstorm",        "Control",    "Control",     None),
    ("rhystic study",     "Control",    "Control",     None),
    ("mystic remora",     "Control",    "Control",     None),
    # Tutor noir
    ("demonic tutor",     "Control",    "Control",     None),
    ("dark ritual",       "Control",    "Control",     None),
    ("cabal stronghold",  "Control",    "Control",     None),
    # Sweeper rouge
    ("blasphemous act",   "Aggro",      "Aggro",       None),
    ("chain reaction",    "Aggro",      "Aggro",       None),
    # Ramp vert
    ("cultivate",         "Ramp",       "Ramp",        None),
    ("kodama's reach",    "Ramp",       "Ramp",        None),
    ("farseek",           "Ramp",       "Ramp",        None),
    ("three visits",      "Ramp",       "Ramp",        None),
    # Lands
    ("land tax",          "Control",    "Lands",       None),
    ("ghostly prison",    "Control",    "Control",     None),
    ("generous gift",     "Control",    "Control",     None),
    # Blanc aggro / weenie
    ("path to exile",     "Control",    "Control",     None),
    ("selfless spirit",   "Aggro",      "Aggro",       None),
    # Izzet
    ("izzet signet",      "Spellslinger","Spellslinger",None),
    ("high tide",         "Combo",      "Combo",       None),
    ("hullbreaker horror","Control",    "Control",     None),
    # Serpent / Snake
    ("seshiro the anointed","Tribal",   "Tribal",      "Snake"),
    ("sakura-tribe elder","Ramp",       "Ramp",        None),
    # Treasure / Groan
    ("unexpected windfall","Combo",     "Combo",       None),
    ("thrill of possibility","Aggro",   "Aggro",       None),
    ("vandalblast",       "Aggro",      "Aggro",       None),
    # Island package (mono bleu)
    ("island",            "Control",    "Control",     None),
    # Graveyard
    ("animate dead",      "Graveyard",  "Graveyard",   None),
    ("reanimate",         "Graveyard",  "Graveyard",   None),
    # Aristocrats
    ("blood artist",      "Aristocrats","Aristocrats", None),
    ("zulaport cutthroat","Aristocrats","Aristocrats", None),
    ("viscera seer",      "Aristocrats","Aristocrats", None),
    # Enchantments
    ("enchantress",       "Control",    "Enchantments",None),
    ("sythis",            "Control",    "Enchantments",None),
    ("sanctum weaver",    "Control",    "Enchantments",None),
    # Tokens
    ("intangible virtue", "Tokens",     "Tokens",      None),
    ("parallel lives",    "Tokens",     "Tokens",      None),
    ("doubling season",   "Combo",      "Tokens",      None),
    # Sliver
    ("sliver",            "Tribal",     "Tribal",      "Sliver"),
    # Merfolk
    ("merfolk",           "Tribal",     "Tribal",      "Merfolk"),
    ("lord of atlantis",  "Tribal",     "Tribal",      "Merfolk"),
    # Knight
    ("knight",            "Tribal",     "Tribal",      "Knight"),
    # Cat
    ("feline",            "Tribal",     "Tribal",      "Cat"),
    ("leonin",            "Tribal",     "Tribal",      "Cat"),
    # Pirate
    ("pirate",            "Tribal",     "Tribal",      "Pirate"),
    # Sailor
    ("malcolm",           "Tribal",     "Tribal",      "Pirate"),
    # Shaman
    ("shaman",            "Tribal",     "Tribal",      "Shaman"),
    # Druid
    ("druid",             "Tribal",     "Tribal",      "Druid"),
    # Spirit
    ("spirit",            "Tribal",     "Tribal",      "Spirit"),
    # Skeleton
    ("skeleton",          "Tribal",     "Tribal",      "Skeleton"),
    # Angel
    ("angel",             "Aggro",      "Tribal",      "Angel"),
    # Sphinx
    ("sphinx",            "Tribal",     "Tribal",      "Sphinx"),
    # Hydra
    ("hydra",             "Tribal",     "Tribal",      "Hydra"),
    # Eldrazi
    ("eldrazi",           "Combo",      "Combo",       None),
    ("emrakul",           "Combo",      "Combo",       None),
    # Storm
    ("storm",             "Combo",      "Combo",       None),
    ("grapeshot",         "Combo",      "Combo",       None),
    # Blink
    ("ephemerate",        "Blink",      "Blink",       None),
    ("momentary blink",   "Blink",      "Blink",       None),
    # Lands strategies
    ("crop rotation",     "Lands",      "Lands",       None),
    ("dark depths",       "Lands",      "Lands",       None),
]

# Règles sur les couleurs du top 10 (dominant = > 40% des cartes)
COLOR_INDICATORS = {
    "Island": "U", "Swamp": "B", "Mountain": "R", "Forest": "G", "Plains": "W",
    "Sol Ring": "C",
}


def _guess_colors_from_cards(cards: list[str]) -> list[str]:
    """Déduit les couleurs dominantes à partir des noms de cartes (heuristique grossière)."""
    color_hints: dict[str, int] = {"W": 0, "U": 0, "B": 0, "R": 0, "G": 0, "C": 0}
    kw_map = {
        "plains": "W", "white": "W", "angel": "W", "soldier": "W", "knight": "W",
        "island": "U", "blue": "U", "wizard": "U", "sphinx": "U", "merfolk": "U",
        "swamp": "B", "black": "B", "zombie": "B", "vampire": "B", "demon": "B",
        "mountain": "R", "red": "R", "goblin": "R", "dragon": "R", "orc": "R",
        "forest": "G", "green": "G", "elf": "G", "beast": "G", "druid": "G",
    }
    for c in cards:
        cl = c.lower()
        for kw, col in kw_map.items():
            if kw in cl:
                color_hints[col] += 1
    # garder les couleurs avec ≥ 1 occurrence
    return [c for c, v in sorted(color_hints.items(), key=lambda x: -x[1]) if v > 0][:3] or ["C"]


def _classify_cluster(top_cards: list[str]) -> dict:
    """
    Retourne dict avec strategy, family, tribe, name_hint depuis les cartes top.
    """
    cards_lower = [c.lower() for c in top_cards]
    strategy_votes: dict[str, int] = {}
    family_votes:   dict[str, int] = {}
    tribe_votes:    dict[str, int] = {}

    for pattern, strat, fam, tribe in CARD_RULES:
        for cl in cards_lower:
            if pattern in cl:
                strategy_votes[strat] = strategy_votes.get(strat, 0) + 1
                family_votes[fam]     = family_votes.get(fam, 0) + 1
                if tribe:
                    tribe_votes[tribe] = tribe_votes.get(tribe, 0) + 1

    best_strategy = max(strategy_votes, key=lambda k: strategy_votes[k]) if strategy_votes else "Autres"
    best_family   = max(family_votes,   key=lambda k: family_votes[k])   if family_votes   else "Autres"
    best_tribe    = max(tribe_votes,    key=lambda k: tribe_votes[k])    if tribe_votes    else None

    colors = _guess_colors_from_cards(top_cards)
    return {
        "strategy": best_strategy,
        "family":   best_family,
        "tribe":    best_tribe,
        "colors":   colors,
    }


def _make_name(cluster_id: int, top_cards: list[str], classification: dict) -> str:
    """Génère un nom lisible."""
    tribe = classification["tribe"]
    strat = classification["strategy"]
    colors = classification["colors"]

    color_labels = {"W": "White", "U": "Blue", "B": "Black", "R": "Red", "G": "Green", "C": "Colorless"}
    color_str = "/".join(color_labels.get(c, c) for c in colors[:2])

    if tribe:
        return f"{tribe} Tribal {color_str}".strip()
    if top_cards:
        return f"{top_cards[0][:25]} Package"
    return f"Cluster {cluster_id} – {strat}"


def load_cluster_cards(cluster_id: int) -> list[str]:
    """Charge les cartes d'un cluster depuis son fichier CSV."""
    path = CLUST_DIR / "clusters" / f"cluster_{cluster_id:03d}.csv"
    if not path.exists():
        return []
    df = pd.read_csv(path)
    if "card_name" not in df.columns:
        return []
    if "global_frequency" in df.columns:
        df = df.sort_values("global_frequency", ascending=False)
    return df["card_name"].tolist()


def build_auto_annotation(cluster_id: int, summary_row: pd.Series) -> dict:
    cards = load_cluster_cards(cluster_id)
    top10 = cards[:10]
    top5  = cards[:5]
    classification = _classify_cluster(top10)

    name = _make_name(cluster_id, top5, classification)
    strategy = classification["strategy"]
    family   = classification["family"]
    tribe    = classification["tribe"]
    colors   = classification["colors"]

    desc_cards = ", ".join(top5[:3]) if top5 else "—"
    description = (
        f"Cluster auto-annoté (ID {cluster_id}). "
        f"Cartes représentatives : {desc_cards}. "
        f"Stratégie détectée : {strategy}."
    )

    return {
        "cluster_id":          cluster_id,
        "name":                name,
        "confidence":          0.50,
        "primary_strategy":    strategy,
        "mechanics":           [],
        "dominant_colors":     colors,
        "dominant_types":      [],
        "tribe":               tribe,
        "description":         description,
        "representative_cards": top5,
        "cluster_size":        int(summary_row.get("cluster_size", 0)),
        "avg_frequency":       round(float(summary_row.get("avg_frequency", 0.0)), 4),
        "avg_idf":             round(float(summary_row.get("avg_idf", 0.0)), 4),
        "auto_generated":      True,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="Afficher sans écrire")
    args = parser.parse_args()

    log.info("=== auto_annotate_clusters.py ===")

    # Charger les annotations existantes
    existing_raw: list[dict] = json.loads(ANNOT_PATH.read_text("utf-8"))
    annotated_ids: set[int] = {a["cluster_id"] for a in existing_raw}
    log.info("Annotations existantes : %d clusters", len(annotated_ids))

    # Charger le summary
    summary_df = pd.read_csv(SUMMARY)
    summary_df["cluster_id"] = summary_df["cluster_id"].astype(int)
    summary_map = {int(row["cluster_id"]): row for _, row in summary_df.iterrows()}

    # Trouver les clusters sans annotation
    all_ids = sorted(summary_map.keys())
    missing_ids = [cid for cid in all_ids if cid not in annotated_ids]
    log.info("Clusters sans annotation : %d (IDs %d–%d)",
             len(missing_ids), min(missing_ids) if missing_ids else 0, max(missing_ids) if missing_ids else 0)

    if not missing_ids:
        log.info("Tous les clusters sont annotés. Rien à faire.")
        return

    # Générer les annotations automatiques
    new_annotations: list[dict] = []
    for cid in missing_ids:
        row = summary_map.get(cid, {})
        ann = build_auto_annotation(cid, row)
        new_annotations.append(ann)
        log.info("  C%-3d  %-35s  strategy=%-12s  top=%s",
                 cid, ann["name"][:35], ann["primary_strategy"],
                 (ann["representative_cards"][0] if ann["representative_cards"] else "—")[:30])

    if args.dry_run:
        log.info("Dry-run : rien écrit.")
        return

    # Fusionner et trier
    all_annotations = existing_raw + new_annotations
    all_annotations.sort(key=lambda x: x["cluster_id"])

    ANNOT_PATH.write_text(json.dumps(all_annotations, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info("Écrit : cluster_annotations.json (%d clusters)", len(all_annotations))
    log.info("=== Terminé ===")


if __name__ == "__main__":
    main()
