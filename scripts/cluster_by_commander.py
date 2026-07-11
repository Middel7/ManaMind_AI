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


def fetch_card_types(conn, card_names: list[str]) -> dict[str, dict]:
    """Charge type_line + oracle_text depuis scryfall_cards pour une liste de cartes."""
    if not card_names:
        return {}
    with conn.cursor() as cur:
        cur.execute("""
            SELECT name, type_line, oracle_text FROM scryfall_cards
            WHERE name = ANY(%s)
        """, (card_names,))
        return {r[0]: {"type_line": r[1] or "", "oracle_text": r[2] or ""} for r in cur.fetchall()}


def write_results(conn, commander: str, clusters: dict[str, int],
                  card_stats: dict[str, tuple[int, int]], total_decks: int,
                  cluster_presence: dict[int, int],
                  card_data: dict[str, dict] | None = None) -> int:
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

        # Réutiliser card_data passé en paramètre (évite un second fetch)
        card_types = card_data or fetch_card_types(conn, list(clusters.keys()))

        # Générer les labels et fusionner les clusters de même label
        label_groups: dict[str, dict] = {}
        for cluster_id, cards in cluster_cards.items():
            top = sorted(cards, key=lambda x: -x[1])[:5]
            top_names = [c[0] for c in top]
            all_names = [c[0] for c in cards]
            label = _make_label(cluster_id, top_names, all_names, card_types)
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


TRIBES = [
    "Elf", "Zombie", "Vampire", "Dragon", "Goblin", "Merfolk", "Wizard",
    "Warrior", "Horror", "Elemental", "Spirit", "Soldier", "Angel", "Demon",
    "Beast", "Cat", "Bird", "Shaman", "Cleric", "Knight", "Scout",
    "Druid", "Ranger", "Faerie", "Sliver", "Dinosaur", "Pirate",
]

# Cartes-clés pour chaque stratégie
STRATEGY_KEYS: list[tuple[str, list[str]]] = [
    ("Mill", ["traumatize", "glimpse the unthinkable", "maddening cacophony",
              "mind funeral", "altar of dementia", "mesmeric orb",
              "trepanation blade", "consuming aberration", "brainstealer dragon",
              "nemesis of reason", "ruin crab", "teferi's tutelage"]),
    ("Reanimator", ["reanimate", "animate dead", "dread return", "exhume",
                    "entomb", "buried alive", "necromancy", "persist",
                    "unearth", "late to dinner", "victimize", "stitch together"]),
    ("Counterspells", ["counterspell", "negate", "swan song", "force of will",
                       "mana drain", "arcane denial", "delay", "remand",
                       "fierce guardianship", "dovin's veto"]),
    ("Sacrifice / Aristocrats", ["ashnod's altar", "phyrexian altar", "viscera seer",
                                  "blood artist", "zulaport cutthroat", "falkenrath noble",
                                  "grave pact", "dictate of erebos", "malevolent noble"]),
    ("Token Doublers", ["doubling season", "parallel lives", "primal vigor",
                        "anointed procession", "intangible virtue", "mondrak"]),
    ("Finishers / Overrun", ["craterhoof behemoth", "overwhelming stampede",
                              "beastmaster ascension", "triumph of the hordes",
                              "pathbreaker ibex", "concordant crossroads"]),
    ("Mana Rituals / Black Ramp", ["cabal coffers", "urborg, tomb of yawgmoth", "nykthos",
                                    "dark ritual", "culling the weak", "diabolic intent",
                                    "cabal stronghold"]),
    ("Mana Rocks", ["arcane signet", "chromatic lantern", "thought vessel", "mind stone",
                    "commander's sphere", "dimir signet", "simic signet", "izzet signet",
                    "golgari signet", "fellwar stone", "coalition relic", "talisman"]),
    ("Creature Tutors", ["natural order", "defense of the heart", "tooth and nail",
                          "finale of devastation", "chord of calling", "green sun's zenith",
                          "worldly tutor", "summoner's pact", "shared summons"]),
    ("Power / Toughness Matters", ["garruk's uprising", "rishkar's expertise",
                                    "return of the wildspeaker", "soul's majesty",
                                    "regal force", "greater good", "selvala's stampede",
                                    "herd baloth", "temur sabertooth", "kogla, the titan ape",
                                    "fierce empath", "ghalta, primal hunger"]),
    ("Card Draw / Engines", ["rhystic study", "necropotence", "windfall", "brainstorm",
                              "divination", "phyrexian arena", "mystic remora",
                              "sensei's divining top", "sylvan library", "harmonize",
                              "shamanic revelation", "wellspring", "prospect"]),
    ("Ramp / Land Search", ["cultivate", "kodama's reach", "three visits", "nature's lore",
                             "rampant growth", "sakura-tribe elder", "wood elves",
                             "farhaven elf", "springbloom druid", "harrow",
                             "skyshroud claim", "explosive vegetation", "farseek"]),
    ("Removal / Protection", ["heroic intervention", "cyclonic rift", "chaos warp",
                               "swords to plowshares", "path to exile", "beast within",
                               "generous gift", "nature's claim", "veil of summer",
                               "tamiyo's safekeeping", "tyvar's stand"]),
    ("Recursion / Graveyard", ["eternal witness", "regrowth", "noxious revival",
                                "pull from eternity", "life from the loam",
                                "archeomancer", "conjurer's closet"]),
]


def _classify_card(card: str, card_data: dict) -> str:
    """Classifie une carte selon son oracle_text et type_line."""
    oracle = card_data.get("oracle_text", "").lower()
    tl     = card_data.get("type_line", "").lower()
    name   = card.lower()

    # Mana dork : créature qui ajoute du mana via tap
    if "creature" in tl and ("{t}: add" in oracle or "{t}: add {" in oracle):
        return "mana_dork"
    # Enchantement de terrain qui ajoute du mana
    if ("enchantment" in tl and "enchant land" in oracle and "add {" in oracle):
        return "mana_dork"
    # Mill explicite
    if "mill" in oracle or "mills" in oracle:
        return "mill"
    # Draw
    if "draw" in oracle and ("card" in oracle or "cards" in oracle):
        return "draw"
    # Ramp land search
    if "search your library" in oracle and ("land" in oracle or "forest" in oracle):
        return "ramp"
    # Removal
    if any(k in oracle for k in ["exile target", "destroy target", "return target"]):
        return "removal"
    return "other"


def _make_label(cluster_id: int, top_cards: list[str],
                all_cards: list[str] | None = None,
                card_types: dict[str, dict] | None = None) -> str:
    """
    Génère un label thématique pour un cluster via :
    1. Vote tribal (si ≥40% des cartes partagent un type créature)
    2. Vote fonctionnel via oracle_text (mana_dork, mill, draw, ramp…)
    3. Matching par cartes-clés sur tous les noms du cluster
    4. Fallback : nom de la carte la plus présente
    """
    from collections import Counter
    card_list = all_cards or top_cards
    all_lower = " ".join(card_list).lower()
    ct = card_types or {}

    # ── 1. Vote tribal ────────────────────────────────────────────────────────
    tribe_votes: Counter = Counter()
    for card in card_list:
        data = ct.get(card, {})
        tl = data.get("type_line", "").lower() if isinstance(data, dict) else str(data).lower()
        if "creature" not in tl:
            continue
        for tribe in TRIBES:
            if tribe.lower() in tl:
                tribe_votes[tribe] += 1
    if tribe_votes:
        best_tribe, best_count = tribe_votes.most_common(1)[0]
        if best_count >= max(4, len(card_list) * 0.40):
            return f"{best_tribe}s Tribal"

    # ── 2. Vote fonctionnel via oracle_text ───────────────────────────────────
    if ct:
        func_votes: Counter = Counter()
        for card in card_list:
            data = ct.get(card, {})
            if isinstance(data, dict):
                func_votes[_classify_card(card, data)] += 1
        total = len(card_list)
        # mana dorks dominants → Elves / Mana Dorks
        if func_votes.get("mana_dork", 0) >= max(3, total * 0.30):
            return "Elves / Mana Dorks"
        # Mill dominant
        if func_votes.get("mill", 0) >= max(2, total * 0.20):
            return "Mill"
        # Ramp dominant (et peu de draw)
        ramp_n = func_votes.get("ramp", 0)
        draw_n = func_votes.get("draw", 0)
        if ramp_n >= max(3, total * 0.25) and ramp_n > draw_n:
            return "Ramp / Land Search"

    # ── 3. Matching cartes-clés sur les noms ─────────────────────────────────
    for label, keys in STRATEGY_KEYS:
        if any(k in all_lower for k in keys):
            return label

    # ── 4. Fallback ───────────────────────────────────────────────────────────
    if top_cards:
        name = top_cards[0]
        return f"{name[:28]} Package" if len(name) > 28 else f"{name} Package"
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


def pre_assign_functional_groups(card_list: list[str], card_data: dict[str, dict]) -> dict[str, str]:
    """
    Pré-assigne chaque carte à un groupe fonctionnel via oracle_text.
    Retourne {card_name: group} où group est "mana_dork", "removal", "other", etc.
    """
    return {card: _classify_card(card, card_data.get(card, {})) for card in card_list}


def cluster_commander(decks: dict[str, list[str]], card_data: dict[str, dict] | None = None) -> tuple[dict[str, int], dict[str, tuple[int, int]], dict[int, int]]:
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

    card_names = df.index.tolist()

    # Séparer les mana dorks avant le K-Means — ils forment toujours leur propre cluster
    MANA_DORK_GROUP = -99  # ID réservé
    if card_data:
        dork_mask = [_classify_card(c, card_data.get(c, {})) == "mana_dork" for c in card_names]
        dork_cards = [c for c, is_dork in zip(card_names, dork_mask) if is_dork]
        non_dork_cards = [c for c, is_dork in zip(card_names, dork_mask) if not is_dork]
    else:
        dork_cards = []
        non_dork_cards = card_names

    # K-Means uniquement sur les cartes non-dork
    if non_dork_cards:
        idx_map = {c: i for i, c in enumerate(card_names)}
        nd_indices = [idx_map[c] for c in non_dork_cards]
        matrix = df.values[nd_indices]  # (n_non_dork, n_decks)
        n_cards = matrix.shape[0]
        k = max(3, min(15, int(round(n_cards ** 0.5 / 1.5))))
        n_components = min(20, n_cards - 1, matrix.shape[1] - 1)
        if n_components >= 2:
            pca = PCA(n_components=n_components, random_state=42)
            reduced = pca.fit_transform(matrix)
        else:
            reduced = matrix
        km = KMeans(n_clusters=k, random_state=42, n_init=10)
        nd_labels = km.fit_predict(reduced)
        clusters = {name: int(label) for name, label in zip(non_dork_cards, nd_labels)}
    else:
        clusters = {}

    # Ajouter les mana dorks dans leur propre cluster (ID = max_label + 1)
    if dork_cards:
        next_id = max(clusters.values(), default=-1) + 1
        for c in dork_cards:
            clusters[c] = next_id

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

            all_card_names_pre = list({c for cards in decks.values() for c in cards})
            card_data_pre = fetch_card_types(conn, all_card_names_pre)
            clusters, card_stats, cluster_presence = cluster_commander(decks, card_data_pre)

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

            n_clusters = write_results(conn, commander, clusters, card_stats, len(decks), cluster_presence, card_data_pre)
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
