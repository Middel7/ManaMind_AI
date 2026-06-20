#!/usr/bin/env python3
"""
annotate_clusters.py

Identification automatique des archétypes MTG Commander dans les 103 clusters Card2Vec.
Utilise Claude Haiku via l'API Anthropic (traitement par lots de 10 clusters).

Sorties :
  data/clustering/cluster_annotations.json
  data/clustering/cluster_taxonomy.json
"""
from __future__ import annotations

import json
import logging
import re
import sys
import time
from pathlib import Path

import anthropic
import pandas as pd
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Charger ANTHROPIC_API_KEY depuis .env si présente
load_dotenv(ROOT / ".env")

# ── Logging ───────────────────────────────────────────────────────────────────
LOG_DIR = ROOT / "logs"
LOG_DIR.mkdir(exist_ok=True)
_stdout_handler = logging.StreamHandler(sys.stdout)
_stdout_handler.stream = open(
    sys.stdout.fileno(), mode="w", encoding="utf-8", buffering=1, closefd=False
)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
    handlers=[
        _stdout_handler,
        logging.FileHandler(LOG_DIR / "annotate_clusters.log", encoding="utf-8"),
    ],
)
log = logging.getLogger(__name__)

# ── Chemins ───────────────────────────────────────────────────────────────────
CLUST_DIR    = ROOT / "data" / "clustering" / "clusters"
SUMMARY_CSV  = ROOT / "data" / "clustering" / "cluster_summary.csv"
OUT_ANNOT    = ROOT / "data" / "clustering" / "cluster_annotations.json"
OUT_TAXO     = ROOT / "data" / "clustering" / "cluster_taxonomy.json"

# ── Paramètres ────────────────────────────────────────────────────────────────
MODEL          = "claude-haiku-4-5-20251001"
TOP_N_CARDS    = 30   # cartes envoyées au LLM par cluster
BATCH_SIZE     = 10   # clusters par appel API
RETRY_DELAY    = 5    # secondes entre retries
MAX_RETRIES    = 3

TAXONOMY_FAMILIES = [
    "Tribal", "Graveyard", "Blink", "Aristocrats", "Tokens",
    "Lands", "Artifacts", "Enchantments", "Spellslinger", "Combo",
    "Ramp", "Control", "Aggro", "Autres",
]


# ── Chargement des données ────────────────────────────────────────────────────

def load_cluster_data(summary: pd.DataFrame) -> list[dict]:
    """Charge les TOP_N_CARDS cartes les plus fréquentes pour chaque cluster."""
    clusters = []
    for _, row in summary.iterrows():
        cid  = int(row["cluster_id"])
        size = int(row["cluster_size"])
        path = CLUST_DIR / f"cluster_{cid:03d}.csv"
        if not path.exists():
            log.warning("Fichier manquant : %s", path.name)
            continue

        df    = pd.read_csv(path, encoding="utf-8")
        top_n = df.nlargest(TOP_N_CARDS, "global_frequency")["card_name"].tolist()

        clusters.append({
            "cluster_id":          cid,
            "cluster_size":        size,
            "avg_frequency":       float(row["avg_frequency"]),
            "avg_idf":             float(row["avg_idf"]),
            "top_frequent":        row["top_frequent"],
            "representative_card": row["representative_card"],
            "cards":               top_n,
        })

    log.info("Clusters charges : %d", len(clusters))
    return clusters


# ── Prompt ────────────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """Tu es un expert MTG Commander qui analyse des clusters de cartes pour identifier des archétypes.
Ces clusters ont été découverts automatiquement par un modèle Word2Vec entraîné sur 36 000 decklists.
Les cartes d'un même cluster sont jouées ensemble dans les mêmes decks — pas de liste préétablie, les données parlent d'elles-mêmes.

Pour chaque cluster, réponds UNIQUEMENT avec un objet JSON valide, sans texte avant ni après.
Ne jamais inventer de mécanique absente des cartes listées.
Si un cluster est ambigu, choisis l'archétype dominant et diminue la confiance."""

def build_user_prompt(batch: list[dict]) -> str:
    blocks = []
    for c in batch:
        cards_str = "\n".join(f"  - {card}" for card in c["cards"])
        blocks.append(
            f"### Cluster {c['cluster_id']} (taille={c['cluster_size']}, "
            f"IDF_moyen={c['avg_idf']:.2f})\n"
            f"Cartes (triées par fréquence globale) :\n{cards_str}"
        )

    clusters_text = "\n\n".join(blocks)

    return f"""Analyse ces {len(batch)} clusters MTG Commander.

{clusters_text}

Réponds avec un tableau JSON contenant exactement {len(batch)} objets, un par cluster, dans le même ordre.
Chaque objet doit avoir cette structure EXACTE :

{{
  "cluster_id": <int>,
  "name": "<nom court de l'archétype en anglais, max 3 mots>",
  "confidence": <float entre 0.0 et 1.0>,
  "primary_strategy": "<une des familles : Tribal|Graveyard|Blink|Aristocrats|Tokens|Lands|Artifacts|Enchantments|Spellslinger|Combo|Ramp|Control|Aggro|Autres>",
  "mechanics": ["<mécanique 1>", "<mécanique 2>", ...],
  "dominant_colors": ["W"|"U"|"B"|"R"|"G"|"C"],
  "dominant_types": ["Creature"|"Instant"|"Sorcery"|"Enchantment"|"Artifact"|"Land"|"Planeswalker"],
  "tribe": "<nom de la tribu si Tribal, sinon null>",
  "description": "<1-2 phrases décrivant la stratégie>",
  "representative_cards": ["<carte 1>", "<carte 2>", "<carte 3>"]
}}

Retourne UNIQUEMENT le tableau JSON, sans commentaires."""


# ── Appel API ─────────────────────────────────────────────────────────────────

def call_claude(client: anthropic.Anthropic, batch: list[dict]) -> list[dict]:
    """Appelle Claude sur un batch et retourne la liste d'annotations parsées."""
    prompt = build_user_prompt(batch)
    expected_ids = {c["cluster_id"] for c in batch}

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            response = client.messages.create(
                model=MODEL,
                max_tokens=4096,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": prompt}],
            )
            raw = response.content[0].text.strip()

            # Extraire le JSON même si le modèle ajoute du texte autour
            json_match = re.search(r"\[.*\]", raw, re.DOTALL)
            if not json_match:
                raise ValueError("Pas de tableau JSON dans la reponse")

            parsed: list[dict] = json.loads(json_match.group())

            # Validation basique
            returned_ids = {p["cluster_id"] for p in parsed}
            missing = expected_ids - returned_ids
            if missing:
                log.warning("Clusters manquants dans la reponse : %s", missing)

            return parsed

        except (json.JSONDecodeError, ValueError, anthropic.APIError) as e:
            log.warning("Tentative %d/%d echouee : %s", attempt, MAX_RETRIES, e)
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_DELAY * attempt)

    log.error("Batch %s : echec apres %d tentatives", [c["cluster_id"] for c in batch], MAX_RETRIES)
    return []


# ── Traitement par lots ────────────────────────────────────────────────────────

def annotate_all(clusters: list[dict]) -> list[dict]:
    client = anthropic.Anthropic()
    annotations: list[dict] = []

    batches = [clusters[i : i + BATCH_SIZE] for i in range(0, len(clusters), BATCH_SIZE)]
    log.info("Traitement de %d clusters en %d batches de %d", len(clusters), len(batches), BATCH_SIZE)

    for i, batch in enumerate(batches, 1):
        ids = [c["cluster_id"] for c in batch]
        log.info("Batch %d/%d — clusters %s", i, len(batches), ids)

        results = call_claude(client, batch)

        # Enrichir avec les métadonnées cluster
        size_map = {c["cluster_id"]: c for c in batch}
        for ann in results:
            cid  = ann["cluster_id"]
            meta = size_map.get(cid, {})
            ann["cluster_size"]   = meta.get("cluster_size", 0)
            ann["avg_frequency"]  = round(meta.get("avg_frequency", 0.0), 4)
            ann["avg_idf"]        = round(meta.get("avg_idf", 0.0), 4)
            annotations.append(ann)

        log.info("  -> %d annotations recues", len(results))
        # Petite pause entre batches pour respecter les rate limits
        if i < len(batches):
            time.sleep(1.5)

    # Trier par cluster_id
    annotations.sort(key=lambda x: x["cluster_id"])
    return annotations


# ── Taxonomie ─────────────────────────────────────────────────────────────────

def build_taxonomy(annotations: list[dict]) -> dict:
    """
    Regroupe les clusters par famille de stratégies.
    Appelle Claude une fois pour valider et affiner les regroupements.
    """
    # Regroupement simple par primary_strategy
    family_map: dict[str, list[dict]] = {f: [] for f in TAXONOMY_FAMILIES}

    for ann in annotations:
        strategy = ann.get("primary_strategy", "Autres")
        if strategy not in family_map:
            strategy = "Autres"
        family_map[strategy].append({
            "cluster_id":     ann["cluster_id"],
            "name":           ann.get("name", ""),
            "cluster_size":   ann.get("cluster_size", 0),
            "confidence":     ann.get("confidence", 0.0),
            "tribe":          ann.get("tribe"),
            "mechanics":      ann.get("mechanics", []),
            "description":    ann.get("description", ""),
        })

    # Stats par famille
    taxonomy = {
        "total_clusters": len(annotations),
        "total_cards_assigned": sum(a.get("cluster_size", 0) for a in annotations),
        "families": {},
    }

    for family in TAXONOMY_FAMILIES:
        members = family_map[family]
        if not members:
            continue
        members_sorted = sorted(members, key=lambda x: x["cluster_size"], reverse=True)
        taxonomy["families"][family] = {
            "cluster_count":  len(members),
            "total_cards":    sum(m["cluster_size"] for m in members),
            "avg_confidence": round(
                sum(m["confidence"] for m in members) / len(members), 3
            ),
            "clusters":       members_sorted,
        }

    # Résumé console
    log.info("--- Taxonomie ---")
    for fam, data in taxonomy["families"].items():
        log.info("  %-15s  %3d clusters  %5d cartes  conf=%.2f",
                 fam, data["cluster_count"], data["total_cards"], data["avg_confidence"])

    return taxonomy


# ── Point d'entrée ────────────────────────────────────────────────────────────

def main() -> None:
    log.info("=== annotate_clusters.py ===")

    # Chargement
    summary   = pd.read_csv(SUMMARY_CSV, encoding="utf-8")
    clusters  = load_cluster_data(summary)

    # Annotation LLM
    annotations = annotate_all(clusters)
    log.info("Annotations totales : %d / %d", len(annotations), len(clusters))

    # Sauvegarde annotations
    OUT_ANNOT.write_text(
        json.dumps(annotations, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    log.info("Ecrit : cluster_annotations.json")

    # Taxonomie
    taxonomy = build_taxonomy(annotations)
    OUT_TAXO.write_text(
        json.dumps(taxonomy, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    log.info("Ecrit : cluster_taxonomy.json")

    # Résumé final
    log.info("=== Termine ===")
    log.info("  Annotations : %d clusters", len(annotations))
    fam_counts = sorted(
        [(f, d["cluster_count"]) for f, d in taxonomy["families"].items()],
        key=lambda x: -x[1],
    )
    log.info("  Top familles : %s",
             " | ".join(f"{f}={n}" for f, n in fam_counts[:6]))


if __name__ == "__main__":
    main()
