#!/usr/bin/env python3
"""
cluster_by_commander.py

Pour chaque commandant de la DB :
  1. Charge ses decks depuis deck_cards
  2. Construit une matrice de co-occurrence carte x carte
  3. Réduit avec UMAP puis clustérise avec HDBSCAN
  4. Écrit les résultats dans commander_clusters + commander_cluster_meta

Usage :
    uv run python scripts/cluster_by_commander.py
    uv run python scripts/cluster_by_commander.py --commander "Captain N'ghathrod"
    uv run python scripts/cluster_by_commander.py --min-decks 50   # ignorer les commandants < 50 decks
    uv run python scripts/cluster_by_commander.py --reset
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger(__name__)

MIN_INCLUSION  = 3.0    # % minimum pour inclure une carte
MIN_CARDS      = 15     # cartes minimum pour clustériser
MIN_DECKS      = 20     # decks minimum pour clustériser
HDBSCAN_PARAMS = dict(min_cluster_size=4, min_samples=2, metric="euclidean", cluster_selection_method="eom")
UMAP_PARAMS    = dict(n_components=5, n_neighbors=10, min_dist=0.05, metric="cosine", random_state=42)

# Cartes staples génériques à exclure du clustering (présentes dans >60% des decks toutes couleurs)
# Elles n'apportent aucun signal stratégique — elles apparaissent partout
GENERIC_STAPLES: set[str] = {
    "Sol Ring", "Arcane Signet", "Commander's Sphere", "Mind Stone",
    "Thought Vessel", "Fellwar Stone", "Chromatic Lantern", "Coalition Relic",
    "Swiftfoot Boots", "Lightning Greaves", "Swords to Plowshares",
    "Counterspell", "Cultivate", "Kodama's Reach", "Farseek",
    "Rampant Growth", "Three Visits", "Nature's Lore",
}

# Terrains de base et terrains utilitaires génériques à exclure du clustering
LANDS_EXCLUDE: set[str] = {
    # Terrains de base
    "Plains", "Island", "Swamp", "Mountain", "Forest",
    "Snow-Covered Plains", "Snow-Covered Island", "Snow-Covered Swamp",
    "Snow-Covered Mountain", "Snow-Covered Forest",
    # Terrains incolores génériques
    "Command Tower", "Exotic Orchard", "Myriad Landscape",
    "Terramorphic Expanse", "Evolving Wilds", "Fabled Passage",
    "Path of Ancestry", "Reflecting Pool", "Arcane Sanctum",
    "Reliquary Tower", "Temple of the False God", "Ash Barrens",
    "Buried Ruin", "Cryptic Caves", "Field of the Dead",
    "Ghost Quarter", "Homeward Path", "Maze of Ith",
    "Memorial to Genius", "Mikokoro, Center of the Sea",
    "Myriad Landscape", "Rogue's Passage", "Scavenger Grounds",
    "Shefet Dunes", "Slippery Karst", "Strip Mine", "Wasteland",
}


# ── DB helpers ───────────────────────────────────────────────────────────────

def get_conn():
    import os
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
    import psycopg2
    url = os.environ["DATABASE_URL"].replace("postgresql+psycopg2://", "postgresql://").replace("postgresql+asyncpg://", "postgresql://")
    return psycopg2.connect(url)


def fetch_commanders(conn, min_decks: int) -> list[tuple[str, int]]:
    with conn.cursor() as cur:
        cur.execute("""
            SELECT commander, COUNT(DISTINCT deck_id) as n
            FROM deck_cards
            GROUP BY commander
            HAVING COUNT(DISTINCT deck_id) >= %s
            ORDER BY n DESC
        """, (min_decks,))
        return cur.fetchall()


def fetch_done(conn) -> set[str]:
    with conn.cursor() as cur:
        cur.execute("SELECT commander FROM commander_cluster_progress")
        return {r[0] for r in cur.fetchall()}


def fetch_land_names(conn) -> set[str]:
    """Charge tous les noms de terrains depuis scryfall_cards."""
    with conn.cursor() as cur:
        cur.execute("""
            SELECT name FROM scryfall_cards
            WHERE type_line ILIKE '%Land%'
        """)
        return {r[0] for r in cur.fetchall()} | LANDS_EXCLUDE


def fetch_decks(conn, commander: str, land_names: set[str]) -> dict[str, list[str]]:
    """Retourne {deck_id: [card_name, ...]} pour un commandant, sans terrains ni commandant."""
    with conn.cursor() as cur:
        cur.execute("""
            SELECT deck_id, card_name
            FROM deck_cards
            WHERE commander = %s AND is_commander = false
        """, (commander,))
        rows = cur.fetchall()
    decks: dict[str, list[str]] = {}
    exclude = land_names | GENERIC_STAPLES
    for deck_id, card_name in rows:
        if card_name not in exclude:
            decks.setdefault(deck_id, []).append(card_name)
    return decks


def write_results(conn, commander: str, clusters: dict[str, int],
                  card_stats: dict[str, tuple[int, int]], total_decks: int,
                  cluster_presence: dict[int, int]) -> int:
    """
    Écrit les résultats dans commander_clusters et commander_cluster_meta.
    Retourne le nombre de clusters.
    """
    with conn.cursor() as cur:
        # Nettoyer les anciens résultats
        cur.execute("DELETE FROM commander_clusters WHERE commander = %s", (commander,))
        cur.execute("DELETE FROM commander_cluster_meta WHERE commander = %s", (commander,))
        cur.execute("DELETE FROM commander_cluster_progress WHERE commander = %s", (commander,))

        # Construire les métadonnées par cluster HDBSCAN brut
        cluster_cards: dict[int, list[tuple[str, int]]] = {}
        for card_name, cluster_id in clusters.items():
            deck_count = card_stats.get(card_name, (0, 0))[0]
            cluster_cards.setdefault(cluster_id, []).append((card_name, deck_count))

        # Générer les labels et fusionner les clusters de même label
        label_groups: dict[str, dict] = {}
        for cluster_id, cards in cluster_cards.items():
            top = sorted(cards, key=lambda x: -x[1])[:5]
            top_names = [c[0] for c in top]
            label = _make_label(cluster_id, top_names)
            presence = cluster_presence.get(cluster_id, 0)

            if label not in label_groups:
                label_groups[label] = {
                    "cards":    list(cards),
                    "presence": presence,
                    "raw_ids":  [cluster_id],
                }
            else:
                label_groups[label]["cards"].extend(cards)
                # Présence fusionnée = union des decks (approximée par le max, conservateur)
                label_groups[label]["presence"] = max(label_groups[label]["presence"], presence)
                label_groups[label]["raw_ids"].append(cluster_id)

        # Renuméroter les clusters fusionnés 0-based et mettre à jour `clusters`
        label_to_new_id = {label: i for i, label in enumerate(sorted(label_groups))}

        # Mettre à jour la table commander_clusters avec les nouveaux IDs fusionnés
        new_cluster_map: dict[int, int] = {}
        for label, group in label_groups.items():
            new_id = label_to_new_id[label]
            for old_id in group["raw_ids"]:
                new_cluster_map[old_id] = new_id

        # Réécrire les lignes commander_clusters avec les IDs fusionnés
        cur.execute("DELETE FROM commander_clusters WHERE commander = %s", (commander,))
        rows_cc2 = []
        for card_name, old_cid in clusters.items():
            new_cid = new_cluster_map.get(old_cid, old_cid)
            deck_count, _ = card_stats.get(card_name, (0, total_decks))
            inclusion = round(deck_count / total_decks * 100, 4) if total_decks > 0 else 0.0
            rows_cc2.append((commander, new_cid, card_name, deck_count, total_decks, inclusion))
        cur.executemany("""
            INSERT INTO commander_clusters (commander, cluster_id, card_name, deck_count, total_decks, inclusion_rate)
            VALUES (%s, %s, %s, %s, %s, %s)
        """, rows_cc2)

        rows_meta = []
        for label, group in sorted(label_groups.items(), key=lambda x: -x[1]["presence"]):
            new_id = label_to_new_id[label]
            all_cards = sorted(group["cards"], key=lambda x: -x[1])
            top_names = [c[0] for c in all_cards[:5]]
            presence = group["presence"]
            presence_rate = round(presence / total_decks * 100, 2) if total_decks > 0 else 0.0
            rows_meta.append((
                commander, new_id, label, len(all_cards),
                presence, total_decks, presence_rate,
                json.dumps(top_names, ensure_ascii=False),
            ))

        cur.executemany("""
            INSERT INTO commander_cluster_meta
                (commander, cluster_id, cluster_label, card_count, deck_presence, total_decks, presence_rate, top_cards)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        """, rows_meta)

        n_clusters = len(label_groups)
        cur.execute("""
            INSERT INTO commander_cluster_progress (commander, n_clusters, computed_at)
            VALUES (%s, %s, %s)
        """, (commander, n_clusters, datetime.now(timezone.utc)))

    conn.commit()
    return n_clusters


def _make_label(cluster_id: int, top_cards: list[str]) -> str:
    cards_lower = " ".join(top_cards).lower()
    if any(k in cards_lower for k in ["traumatize", "mill", "glimpse", "maddening", "mind funeral", "altar of dementia", "mesmeric orb"]):
        return "Mill"
    if any(k in cards_lower for k in ["horror", "aberration", "nemesis of reason", "brainstealer"]):
        return "Horrors Tribal"
    if any(k in cards_lower for k in ["zombie", "gravecrawler", "undead", "wilhelt", "diregraf"]):
        return "Zombies Tribal"
    if any(k in cards_lower for k in ["reanimate", "animate dead", "dread return", "exhume", "entomb"]):
        return "Reanimator"
    if any(k in cards_lower for k in ["counterspell", "negate", "swan song", "force of will", "mana drain"]):
        return "Counterspells"
    if any(k in cards_lower for k in ["sol ring", "arcane signet", "dimir signet", "dark ritual", "cabal coffers"]):
        return "Mana Rocks / Ramp"
    if any(k in cards_lower for k in ["rhystic study", "phyrexian arena", "necropotence", "windfall", "brainstorm"]):
        return "Card Draw"
    if any(k in cards_lower for k in ["island", "swamp", "underground sea", "watery grave", "drowned catacomb"]):
        return "Lands"
    if any(k in cards_lower for k in ["lightning greaves", "swiftfoot boots", "commander's sphere"]):
        return "Utility / Protection"
    if top_cards:
        return f"{top_cards[0][:30]} Package"
    return f"Cluster {cluster_id}"


# ── Clustering ────────────────────────────────────────────────────────────────

def build_cooccurrence_matrix(decks: dict[str, list[str]], min_inclusion: float
                               ) -> tuple[pd.DataFrame, dict[str, tuple[int, int]]]:
    """
    Construit une matrice binaire decks x cartes (1 = carte présente dans ce deck).
    Filtre les cartes sous le seuil d'inclusion.
    Retourne (matrice DataFrame, {card: (deck_count, total_decks)}).
    """
    total = len(decks)
    # Compter les occurrences par carte
    card_counts: dict[str, int] = {}
    for cards in decks.values():
        for c in set(cards):
            card_counts[c] = card_counts.get(c, 0) + 1

    # Filtrer par inclusion_rate
    kept = [c for c, n in card_counts.items() if n / total * 100 >= min_inclusion]
    if len(kept) < MIN_CARDS:
        return pd.DataFrame(), {}

    kept_set = set(kept)
    card_stats = {c: (card_counts[c], total) for c in kept}

    # Matrice binaire decks x cartes
    deck_ids = list(decks.keys())
    card_idx = {c: i for i, c in enumerate(kept)}
    matrix = np.zeros((len(deck_ids), len(kept)), dtype=np.float32)
    for di, deck_id in enumerate(deck_ids):
        for card in decks[deck_id]:
            if card in kept_set:
                matrix[di, card_idx[card]] = 1.0

    # Transposer : cartes x decks, puis normaliser chaque carte (TF-IDF-like)
    # On travaille sur la matrice cartes x cartes via corrélation cosine
    card_matrix = matrix.T  # (n_cards, n_decks)

    # Normaliser par ligne
    norms = np.linalg.norm(card_matrix, axis=1, keepdims=True)
    card_matrix_norm = card_matrix / (norms + 1e-9)

    df = pd.DataFrame(card_matrix_norm, index=kept)
    return df, card_stats


def cluster_commander(decks: dict[str, list[str]]) -> tuple[dict[str, int], dict[str, tuple[int, int]], dict[int, int]]:
    """
    Clustérise les cartes d'un commandant par K-Means sur la matrice de co-occurrence.
    K est déterminé automatiquement : sqrt(n_cards / 8), borné entre 3 et 12.
    Retourne:
      - clusters: {card_name: cluster_id}
      - card_stats: {card_name: (deck_count, total_decks)}
      - cluster_presence: {cluster_id: nb_decks_avec_au_moins_1_carte_du_cluster}
    """
    from sklearn.cluster import KMeans
    from sklearn.decomposition import PCA

    df, card_stats = build_cooccurrence_matrix(decks, MIN_INCLUSION)
    if df.empty:
        return {}, {}, {}

    matrix = df.values  # (n_cards, n_decks)
    n_cards = matrix.shape[0]

    # K automatique : ~1 cluster par 8 cartes, borné entre 3 et 12
    k = max(3, min(12, int(n_cards ** 0.5 // 2)))

    # Réduction PCA avant K-Means (plus stable que UMAP pour des corpus petits)
    n_components = min(20, n_cards - 1, matrix.shape[1] - 1)
    if n_components >= 2:
        pca = PCA(n_components=n_components, random_state=42)
        reduced = pca.fit_transform(matrix)
    else:
        reduced = matrix

    km = KMeans(n_clusters=k, random_state=42, n_init=10)
    labels = km.fit_predict(reduced)

    card_names = df.index.tolist()
    clusters = {name: int(label) for name, label in zip(card_names, labels)}

    # Calculer la présence de chaque cluster (nb decks contenant ≥1 carte du cluster)
    cluster_cards_set: dict[int, set[str]] = {}
    for card, cid in clusters.items():
        cluster_cards_set.setdefault(cid, set()).add(card)

    cluster_presence: dict[int, int] = {}
    for cid, cset in cluster_cards_set.items():
        count = sum(1 for cards in decks.values() if any(c in cset for c in cards))
        cluster_presence[cid] = count

    return clusters, card_stats, cluster_presence


# ── Main ─────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--commander", default=None, help="Traiter un seul commandant")
    parser.add_argument("--min-decks", type=int, default=MIN_DECKS, help="Decks minimum (défaut: 20)")
    parser.add_argument("--reset",     action="store_true", help="Recalculer même si déjà fait")
    args = parser.parse_args()

    log.info("=== cluster_by_commander.py ===")
    conn = get_conn()

    log.info("Chargement des noms de terrains...")
    land_names = fetch_land_names(conn)
    log.info("  %d terrains exclus du clustering", len(land_names))

    done = fetch_done(conn)
    log.info("Commandants déjà clustérisés : %d", len(done))

    if args.commander:
        commanders = [(args.commander, 0)]
    else:
        commanders = fetch_commanders(conn, args.min_decks)
        log.info("Commandants eligibles (>= %d decks) : %d", args.min_decks, len(commanders))

    t0 = time.time()
    ok = skipped = errors = 0

    for i, (commander, n_decks) in enumerate(commanders):
        if commander in done and not args.reset:
            skipped += 1
            continue

        try:
            decks = fetch_decks(conn, commander, land_names)
            if len(decks) < args.min_decks:
                skipped += 1
                continue

            clusters, card_stats, cluster_presence = cluster_commander(decks)

            if not clusters:
                # Pas assez de cartes — marquer quand même pour ne pas retenter
                with conn.cursor() as cur:
                    cur.execute("""
                        INSERT INTO commander_cluster_progress (commander, n_clusters, computed_at)
                        VALUES (%s, 0, %s) ON CONFLICT (commander) DO NOTHING
                    """, (commander, datetime.now(timezone.utc)))
                conn.commit()
                skipped += 1
                continue

            n_clusters = write_results(conn, commander, clusters, card_stats, len(decks), cluster_presence)
            ok += 1

            if (i + 1) % 100 == 0 or args.commander:
                elapsed = time.time() - t0
                rate = ok / elapsed if elapsed > 0 else 0
                log.info("  [%d/%d] %s -> %d clusters | total OK=%d skip=%d err=%d | %.1f cmd/s",
                         i + 1, len(commanders), commander[:35], n_clusters, ok, skipped, errors, rate)

        except Exception as e:
            errors += 1
            log.warning("  ERREUR %s : %s", commander[:40], e)
            try:
                conn.rollback()
            except Exception:
                conn = get_conn()

    elapsed = time.time() - t0
    log.info("=== Terminé en %.1f min ===", elapsed / 60)
    log.info("  OK=%d  skippés=%d  erreurs=%d", ok, skipped, errors)
    conn.close()


if __name__ == "__main__":
    main()
