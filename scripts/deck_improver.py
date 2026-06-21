#!/usr/bin/env python3
"""
deck_improver.py

Deck Improvement Engine — MTG Commander.

Analyse une decklist complète et produit :
  1. Profil stratégique du deck (deck_profile.json)
  2. Gap analysis vs profil moyen du commandant (deck_gap_analysis.csv)
  3. Top 30 cartes à ajouter (top30_additions.csv)
  4. Top 30 cartes à retirer (top30_cuts.csv)
  5. Paires de remplacement (replacement_pairs.csv)
  6. Explications détaillées (card_explanations.json)
  7. Rapport global (deck_improvement_report.md)

Usage :
  uv run python scripts/deck_improver.py

Decklists de test codées en bas du fichier (modifiables).
"""
from __future__ import annotations

import json
import logging
import sys
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd
from sklearn.naive_bayes import MultinomialNB
from sklearn.preprocessing import MultiLabelBinarizer
from sqlalchemy import text

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.manamind.db.engine import SessionLocal  # noqa: E402

# ── Logging ───────────────────────────────────────────────────────────────────
LOG_DIR = ROOT / "logs"
LOG_DIR.mkdir(exist_ok=True)
_h = logging.StreamHandler(sys.stdout)
_h.stream = open(sys.stdout.fileno(), mode="w", encoding="utf-8", buffering=1, closefd=False)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
    handlers=[_h, logging.FileHandler(LOG_DIR / "deck_improver.log", encoding="utf-8")],
)
log = logging.getLogger(__name__)

# ── Chemins ───────────────────────────────────────────────────────────────────
EMB_DIR   = ROOT / "data" / "embeddings"
ML_DIR    = ROOT / "data" / "ml"
MODEL_DIR = ROOT / "data" / "models"
CLUST_DIR = ROOT / "data" / "clustering"
TAG_DIR   = ROOT / "data" / "tag_cluster"
STATS_DIR = ROOT / "data" / "stats"
OUT_BASE  = ROOT / "data" / "deck_analysis"

# ── Staples / terrains protégés contre les cuts automatiques ─────────────────
# Cartes dont l'absence du TF-IDF reflète une lacune des données, pas un problème réel
_PROTECTED_FROM_CUT: frozenset[str] = frozenset({
    # Mana rocks universels
    "Sol Ring", "Arcane Signet", "Commander's Sphere", "Chromatic Lantern",
    "Talisman of Hierarchy", "Talisman of Progress", "Talisman of Unity",
    "Talisman of Dominance", "Talisman of Curiosity", "Talisman of Impulse",
    "Talisman of Conviction", "Talisman of Creativity", "Talisman of Indulgence",
    "Orzhov Signet", "Golgari Signet", "Dimir Signet", "Azorius Signet",
    "Gruul Signet", "Boros Signet", "Simic Signet", "Izzet Signet",
    "Rakdos Signet", "Selesnya Signet", "Boreas Charger",
    # Terrains utilitaires généraux
    "Command Tower", "Exotic Orchard", "Path of Ancestry",
    "Myriad Landscape", "Temple of the False God",
    # Terrains de base
    "Forest", "Island", "Plains", "Swamp", "Mountain",
    # Tuteurs universels
    "Demonic Tutor", "Vampiric Tutor", "Enlightened Tutor",
    "Worldly Tutor", "Mystical Tutor",
    # Removal universels
    "Swords to Plowshares", "Path to Exile", "Cyclonic Rift",
    "Beast Within", "Generous Gift", "Anguished Unmaking",
    "Vindicate",
    # Draw universels
    "Rhystic Study", "Sylvan Library", "Phyrexian Arena",
    # Protection universelle
    "Heroic Intervention", "Lightning Greaves", "Swiftfoot Boots",
})

# ── Poids scores ──────────────────────────────────────────────────────────────
# Addition score
W_ADD_HYBRID   = 0.40
W_ADD_IR       = 0.30
W_ADD_GAP      = 0.20
W_ADD_SYNERGY  = 0.10

# Hybrid score (interne)
W_TFIDF   = 0.40
W_COSINE  = 0.25
W_CLUSTER = 0.20
W_TAG     = 0.15

# Cut score
W_CUT_HYBRID    = 0.40
W_CUT_COHERENCE = 0.35
W_CUT_COSINE    = 0.25


# ── Structures de données ─────────────────────────────────────────────────────

@dataclass
class ClusterSummary:
    cluster_id: int
    name: str
    family: str
    card_count: int
    deck_share: float        # % dans le deck utilisateur
    meta_share: float        # % dans le méta commandant
    delta: float             # deck_share - meta_share

@dataclass
class DeckProfile:
    commander: str
    card_count: int
    deck_embedding: list[float]
    coherence_score: float           # [0,1] cohérence interne
    avg_cosine_to_commander: float   # proximité moyenne cartes → commandant
    cluster_distribution: dict       # cluster_id → {share, name, family}
    family_distribution: dict        # family → share
    tag_distribution: dict           # tag → count
    mana_curve: dict                 # mv → count
    color_distribution: dict         # couleur → count
    missing_in_corpus: list[str]     # cartes non trouvées dans Card2Vec

@dataclass
class AdditionCandidate:
    card_name: str
    addition_score: float
    hybrid_score: float
    predicted_ir: float
    cluster_gap_bonus: float
    deck_synergy: float
    cluster_id: Optional[int]
    cluster_name: str
    cluster_family: str
    explanation: str

@dataclass
class CutCandidate:
    card_name: str
    cut_score: float
    hybrid_score: float
    coherence_score: float
    deck_cosine: float
    reasons: list[str]


# ── Moteur principal ──────────────────────────────────────────────────────────

class DeckImprovementEngine:
    """
    Charge les artefacts une fois, analyse N decklists sans rechargement.
    """

    def __init__(self) -> None:
        t0 = time.perf_counter()
        log.info("Chargement du Deck Improvement Engine...")

        # ── Embeddings ────────────────────────────────────────────────────────
        raw = np.load(EMB_DIR / "card_embeddings.npy").astype(np.float32)
        norms = np.linalg.norm(raw, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        self.card_matrix = raw / norms                         # L2-normalisé

        cmd_raw = np.load(EMB_DIR / "commander_embeddings.npy").astype(np.float32)
        cnorms = np.linalg.norm(cmd_raw, axis=1, keepdims=True)
        cnorms[cnorms == 0] = 1.0
        self.cmd_matrix = cmd_raw / cnorms

        self.card_index: dict[str, int] = json.loads(
            (EMB_DIR / "card_index.json").read_text("utf-8")
        )
        self.index_card: dict[int, str] = {v: k for k, v in self.card_index.items()}

        cmd_meta = json.loads((EMB_DIR / "commander_embeddings.json").read_text("utf-8"))
        self.commanders: list[str]   = cmd_meta["commanders"]
        self.cmd_idx: dict[str, int] = cmd_meta["commander_to_index"]

        log.info("  Embeddings : %d cartes × %d  |  %d commandants",
                 self.card_matrix.shape[0], self.card_matrix.shape[1], len(self.commanders))

        # ── XGBoost ───────────────────────────────────────────────────────────
        try:
            import xgboost as xgb
            self.xgb = xgb.XGBRegressor()
            self.xgb.load_model(MODEL_DIR / "xgb_card2vec.json")
            self.xgb_features: list[str] = self.xgb.get_booster().feature_names
            log.info("  XGBoost OK  features=%s", self.xgb_features)
        except Exception as e:
            log.warning("  XGBoost indisponible : %s", e)
            self.xgb = None
            self.xgb_features = []

        # ── TF-IDF ───────────────────────────────────────────────────────────
        tfidf_df = pd.read_csv(STATS_DIR / "commander_tfidf.csv", encoding="utf-8")
        self.tfidf_lookup: dict[tuple[str, str], dict] = {}
        for _, row in tfidf_df.iterrows():
            self.tfidf_lookup[(row["commander"], row["card_name"])] = {
                "inclusion_rate": float(row["inclusion_rate"]),
                "idf":            float(row["idf"]),
                "tfidf_norm":     float(row["tfidf_norm"]),
            }
        self.card_idf: dict[str, float] = (
            tfidf_df.groupby("card_name")["idf"].first().to_dict()
        )
        # Cartes connues par commandant (pour construire le pool de candidats)
        self.cmd_known_cards: dict[str, set[str]] = (
            tfidf_df.groupby("commander")["card_name"].apply(set).to_dict()
        )
        log.info("  TF-IDF : %d paires", len(self.tfidf_lookup))

        # ── Métadonnées cartes ────────────────────────────────────────────────
        train_df = pd.read_csv(ML_DIR / "train.csv", encoding="utf-8")
        self.global_freq: dict[str, float] = (
            train_df.groupby("card_name")["global_frequency"].first().to_dict()
        )
        self.card_mana_value: dict[str, float] = (
            train_df.groupby("card_name")["mana_value"].first().to_dict()
        )
        self.card_color_compat: dict[tuple[str, str], int] = {
            (row["commander"], row["card_name"]): int(row["color_identity_compat"])
            for _, row in train_df.iterrows()
        }
        # Métadonnées depuis DB (color_identity Scryfall)
        self.cmd_colors: dict[str, frozenset]  = {}
        self.card_colors: dict[str, frozenset] = {}
        self._load_db_metadata()
        log.info("  Metadata : %d cartes connues", len(self.global_freq))

        # ── Clusters ──────────────────────────────────────────────────────────
        ann_list = json.loads((CLUST_DIR / "cluster_annotations.json").read_text("utf-8"))
        self.annotations: dict[int, dict] = {a["cluster_id"]: a for a in ann_list}

        taxonomy = json.loads((CLUST_DIR / "cluster_taxonomy.json").read_text("utf-8"))
        # cluster_id → family
        self.cluster_family: dict[int, str] = {}
        for family, fdata in taxonomy["families"].items():
            for c in fdata["clusters"]:
                self.cluster_family[c["cluster_id"]] = family

        self.card_cluster: dict[str, int] = {}
        for path in sorted((CLUST_DIR / "clusters").glob("cluster_*.csv")):
            df = pd.read_csv(path, encoding="utf-8")
            for _, row in df.iterrows():
                self.card_cluster[row["card_name"]] = int(row["cluster_id"])

        # Profil de clusters du commandant (poids = Σ tfidf_norm par cluster)
        self.cmd_cluster_profile: dict[str, dict[int, float]] = (
            self._build_cmd_cluster_profiles(tfidf_df)
        )
        log.info("  Clusters : %d cartes clustérisées", len(self.card_cluster))

        # ── Tags → Cluster (Naive Bayes) ─────────────────────────────────────
        self._t2c_pivot, self._nb, self._mlb, self._nb_classes = self._build_tag_model()
        # Tags par carte (depuis tag_cluster_dataset)
        self.card_tags: dict[str, list[str]] = {}
        tag_ds = pd.read_csv(TAG_DIR / "tag_cluster_dataset.csv", encoding="utf-8")
        for card, grp in tag_ds.groupby("card_name"):
            self.card_tags[card] = grp["tag"].tolist()
        log.info("  Tags : %d cartes avec tags", len(self.card_tags))

        # ── Voisins Card2Vec ──────────────────────────────────────────────────
        nb_df = pd.read_csv(EMB_DIR / "card_neighbors.csv", encoding="utf-8")
        self.card_neighbors: dict[str, list[str]] = {}
        for card, grp in nb_df.groupby("card_name"):
            self.card_neighbors[card] = grp.sort_values("rank")["neighbor"].tolist()

        elapsed = time.perf_counter() - t0
        log.info("  Moteur prêt en %.1fs", elapsed)

    # ── Loaders DB ────────────────────────────────────────────────────────────

    def _load_db_metadata(self) -> None:
        try:
            with SessionLocal() as s:
                rows = s.execute(text(
                    "SELECT name, color_identity FROM scryfall_cards WHERE legal_commander = true"
                )).fetchall()
            for r in rows:
                self.cmd_colors[r[0]] = frozenset(r[1] or [])
            rows2 = s.execute(text(
                "SELECT name, color_identity FROM scryfall_cards"
            )).fetchall()
            for r in rows2:
                self.card_colors[r[0]] = frozenset(r[1] or [])
        except Exception as e:
            log.warning("  DB metadata indisponible : %s", e)

    def _build_cmd_cluster_profiles(self, tfidf_df: pd.DataFrame) -> dict[str, dict[int, float]]:
        result: dict[str, dict[int, float]] = {}
        for cmd, grp in tfidf_df.groupby("commander"):
            weights: dict[int, float] = {}
            for _, row in grp.iterrows():
                cid = self.card_cluster.get(row["card_name"])
                if cid is not None:
                    weights[cid] = weights.get(cid, 0.0) + float(row["tfidf_norm"])
            total = sum(weights.values()) or 1.0
            result[cmd] = {cid: w / total for cid, w in weights.items()}
        return result

    def _build_tag_model(self):
        t2c = pd.read_csv(TAG_DIR / "tag_to_cluster.csv", encoding="utf-8")
        dataset = pd.read_csv(TAG_DIR / "tag_cluster_dataset.csv", encoding="utf-8")
        pivot = t2c.pivot_table(
            index="tag", columns="cluster_id", values="probability", fill_value=0.0
        )
        pivot.columns = pivot.columns.astype(int)

        card_cluster = dataset.drop_duplicates("card_name").set_index("card_name")["cluster_id"]
        cards = dataset["card_name"].unique()
        card_tag_dict = dataset.groupby("card_name")["tag"].apply(list).to_dict()

        mlb = MultiLabelBinarizer()
        X = mlb.fit_transform([card_tag_dict.get(c, []) for c in cards])
        y = card_cluster.reindex(cards).fillna(-1).astype(int).values
        mask = y >= 0

        nb = MultinomialNB(alpha=0.5)
        nb.fit(X[mask], y[mask])
        return pivot, nb, mlb, list(nb.classes_)

    # ── Signal helpers ─────────────────────────────────────────────────────────

    def _vec(self, card_name: str) -> Optional[np.ndarray]:
        i = self.card_index.get(card_name)
        return self.card_matrix[i] if i is not None else None

    def _cosine_cmd(self, card_name: str, commander: str) -> float:
        ci = self.card_index.get(card_name)
        mi = self.cmd_idx.get(commander)
        if ci is None or mi is None:
            return 0.0
        return float(np.dot(self.card_matrix[ci], self.cmd_matrix[mi]))

    def _cluster_score(self, card_name: str, commander: str) -> float:
        cid = self.card_cluster.get(card_name)
        if cid is None:
            return 0.0
        return self.cmd_cluster_profile.get(commander, {}).get(cid, 0.0)

    def _tag_score(self, card_name: str, commander: str) -> float:
        tags = self.card_tags.get(card_name, [])
        if not tags:
            return 0.0
        cmd_weights = self.cmd_cluster_profile.get(commander, {})
        total = 0.0
        for tag in tags:
            if tag in self._t2c_pivot.index:
                for cid_col, prob in self._t2c_pivot.loc[tag].items():
                    cid = int(cid_col)
                    total += float(prob) * cmd_weights.get(cid, 0.0)
        return min(total / max(len(tags), 1), 1.0)

    def _predict_ir(
        self, card_name: str, commander: str,
        cosine: float, tfidf_norm: float, idf: float,
    ) -> float:
        if self.xgb is None:
            return tfidf_norm * 100.0
        ci_compat = self.card_color_compat.get((commander, card_name), 1)
        mv = self.card_mana_value.get(card_name, 3.0)
        gf = self.global_freq.get(card_name, 0.0)
        vals = {
            "tfidf_norm": tfidf_norm,
            "global_frequency": gf,
            "idf": idf,
            "mana_value": mv,
            "color_identity_compat": float(ci_compat),
            "cosine_similarity": cosine,
        }
        X = pd.DataFrame([[vals[f] for f in self.xgb_features]], columns=self.xgb_features)
        return float(np.expm1(self.xgb.predict(X)[0]))

    def _hybrid_score(self, card_name: str, commander: str) -> tuple[float, float, float]:
        """Retourne (hybrid_score, predicted_ir, cosine)."""
        td = self.tfidf_lookup.get((commander, card_name), {})
        tfidf_norm = td.get("tfidf_norm", 0.0)
        idf        = td.get("idf", self.card_idf.get(card_name, 0.0))
        cosine     = self._cosine_cmd(card_name, commander)
        cluster_s  = self._cluster_score(card_name, commander)
        tag_s      = self._tag_score(card_name, commander)
        score = (
            W_TFIDF   * tfidf_norm
            + W_COSINE  * cosine
            + W_CLUSTER * cluster_s
            + W_TAG     * tag_s
        )
        ir = self._predict_ir(card_name, commander, cosine, tfidf_norm, idf)
        return round(min(score, 1.0), 4), round(ir, 2), round(cosine, 4)

    # ── Étape 1 : Analyse du deck ─────────────────────────────────────────────

    def analyze_deck(self, commander: str, decklist: list[str]) -> DeckProfile:
        """
        Calcule le profil complet d'un deck.
        decklist = liste des 99 cartes (sans le commandant).
        """
        missing: list[str] = []
        vecs: list[np.ndarray] = []
        cluster_counts: Counter = Counter()
        tag_counts: Counter = Counter()
        mv_counts: Counter = Counter()
        color_counts: Counter = Counter()

        for card in decklist:
            v = self._vec(card)
            if v is not None:
                vecs.append(v)
            else:
                missing.append(card)

            cid = self.card_cluster.get(card)
            if cid is not None:
                cluster_counts[cid] += 1

            for tag in self.card_tags.get(card, []):
                tag_counts[tag] += 1

            mv = self.card_mana_value.get(card)
            if mv is not None:
                mv_counts[int(mv)] += 1

            for c in self.card_colors.get(card, frozenset()):
                color_counts[c] += 1

        # Deck embedding = moyenne normalisée
        if vecs:
            deck_vec = np.mean(vecs, axis=0).astype(np.float32)
            norm = np.linalg.norm(deck_vec)
            if norm > 0:
                deck_vec /= norm
        else:
            deck_vec = np.zeros(self.card_matrix.shape[1], dtype=np.float32)

        # Score de cohérence interne = cosine moyen entre chaque carte et le deck embedding
        coherence = 0.0
        if vecs and np.any(deck_vec):
            cosines = np.dot(np.array(vecs), deck_vec)
            coherence = float(np.mean(cosines))

        # Cosine moyen carte → commandant
        mi = self.cmd_idx.get(commander)
        avg_cosine = 0.0
        if mi is not None and vecs:
            cmd_vec = self.cmd_matrix[mi]
            avg_cosine = float(np.mean(np.dot(np.array(vecs), cmd_vec)))

        # Distributions
        total = len(decklist)
        cluster_dist: dict = {}
        for cid, cnt in cluster_counts.most_common():
            ann = self.annotations.get(cid, {})
            cluster_dist[cid] = {
                "name":   ann.get("name", f"C{cid}"),
                "family": self.cluster_family.get(cid, "Autres"),
                "count":  cnt,
                "share":  round(cnt / total, 4),
            }

        family_counts: Counter = Counter()
        for cid, cnt in cluster_counts.items():
            family_counts[self.cluster_family.get(cid, "Autres")] += cnt
        family_dist = {
            fam: round(cnt / total, 4)
            for fam, cnt in family_counts.most_common()
        }

        return DeckProfile(
            commander=commander,
            card_count=len(decklist),
            deck_embedding=deck_vec.tolist(),
            coherence_score=round(coherence, 4),
            avg_cosine_to_commander=round(avg_cosine, 4),
            cluster_distribution=cluster_dist,
            family_distribution=family_dist,
            tag_distribution=dict(tag_counts.most_common(30)),
            mana_curve=dict(sorted(mv_counts.items())),
            color_distribution=dict(color_counts.most_common()),
            missing_in_corpus=missing,
        )

    # ── Étape 2 : Gap analysis ────────────────────────────────────────────────

    def gap_analysis(
        self, commander: str, deck_profile: DeckProfile
    ) -> tuple[list[ClusterSummary], float]:
        """
        Retourne (cluster_summaries, distance_to_meta).
        distance = distance euclidienne entre vecteur cluster du deck et vecteur méta.
        """
        meta = self.cmd_cluster_profile.get(commander, {})
        deck_dist = {
            cid: v["share"]
            for cid, v in deck_profile.cluster_distribution.items()
        }

        all_cids = sorted(set(list(meta.keys()) + list(deck_dist.keys())))
        summaries: list[ClusterSummary] = []
        for cid in all_cids:
            ann = self.annotations.get(cid, {})
            d_share = deck_dist.get(cid, 0.0)
            m_share = meta.get(cid, 0.0)
            summaries.append(ClusterSummary(
                cluster_id=cid,
                name=ann.get("name", f"C{cid}"),
                family=self.cluster_family.get(cid, "Autres"),
                card_count=deck_profile.cluster_distribution.get(cid, {}).get("count", 0),
                deck_share=round(d_share, 4),
                meta_share=round(m_share, 4),
                delta=round(d_share - m_share, 4),
            ))

        # Distance euclidienne normalisée
        d_vec = np.array([deck_dist.get(cid, 0.0) for cid in all_cids])
        m_vec = np.array([meta.get(cid, 0.0)      for cid in all_cids])
        dist  = float(np.linalg.norm(d_vec - m_vec))

        summaries.sort(key=lambda s: -abs(s.delta))
        return summaries, round(dist, 4)

    # ── Étape 3+4 : Candidats d'ajout ────────────────────────────────────────

    def generate_additions(
        self,
        commander: str,
        decklist: list[str],
        deck_profile: DeckProfile,
        gap_summaries: list[ClusterSummary],
        top_n: int = 30,
    ) -> list[AdditionCandidate]:
        """
        Score d'ajout = 0.40×hybrid + 0.30×IR_norm + 0.20×gap_bonus + 0.10×synergy
        """
        deck_set = set(decklist) | {commander}

        # Clusters sous-représentés (delta négatif) → bonus
        under_clusters: dict[int, float] = {
            s.cluster_id: abs(s.delta)
            for s in gap_summaries
            if s.delta < -0.02
        }
        max_under = max(under_clusters.values(), default=1.0) or 1.0

        # Pool de candidats = cartes connues dans TF-IDF du commandant
        # + toutes les cartes dans card_index (si color_identity OK)
        candidate_pool: set[str] = self.cmd_known_cards.get(commander, set()) - deck_set

        # Filtrer par compatibilité couleur si disponible
        cmd_col = self.cmd_colors.get(commander, frozenset())
        if cmd_col:
            candidate_pool = {
                c for c in candidate_pool
                if not self.card_colors.get(c)
                or self.card_colors.get(c, frozenset()) <= cmd_col
            }

        # Deck embedding pour synergy
        deck_vec = np.array(deck_profile.deck_embedding, dtype=np.float32)

        results: list[AdditionCandidate] = []
        for card in candidate_pool:
            hybrid, ir, cosine = self._hybrid_score(card, commander)

            # IR normalisé [0,1] (on sait que max ≈ 100%)
            ir_norm = min(ir / 100.0, 1.0)

            # Gap bonus : carte dans un cluster sous-représenté
            cid = self.card_cluster.get(card)
            gap_bonus = 0.0
            if cid is not None and cid in under_clusters:
                gap_bonus = under_clusters[cid] / max_under

            # Synergy deck = cosine carte × deck embedding
            synergy = 0.0
            ci = self.card_index.get(card)
            if ci is not None and np.any(deck_vec):
                synergy = float(np.dot(self.card_matrix[ci], deck_vec))
                synergy = max(synergy, 0.0)

            add_score = (
                W_ADD_HYBRID  * hybrid
                + W_ADD_IR    * ir_norm
                + W_ADD_GAP   * gap_bonus
                + W_ADD_SYNERGY * synergy
            )

            ann = self.annotations.get(cid or -1, {})
            expl = self._explain_addition(
                card, commander, hybrid, ir, gap_bonus, cid, ann, deck_profile
            )

            results.append(AdditionCandidate(
                card_name=card,
                addition_score=round(add_score, 4),
                hybrid_score=hybrid,
                predicted_ir=round(ir, 2),
                cluster_gap_bonus=round(gap_bonus, 4),
                deck_synergy=round(synergy, 4),
                cluster_id=cid,
                cluster_name=ann.get("name", "—"),
                cluster_family=self.cluster_family.get(cid or -1, "—") if cid is not None else "—",
                explanation=expl,
            ))

        results.sort(key=lambda r: -r.addition_score)
        return results[:top_n]

    def _explain_addition(
        self, card, commander, hybrid, ir, gap_bonus, cid, ann, profile
    ) -> str:
        parts = []
        td = self.tfidf_lookup.get((commander, card), {})
        real_ir = td.get("inclusion_rate", 0.0)
        if real_ir > 5:
            parts.append(f"jouée dans {real_ir:.0f}% des decks {commander}")
        if cid is not None and ann:
            family = self.cluster_family.get(cid, "")
            parts.append(
                f"appartient au cluster « {ann.get('name', '')} » ({family})"
            )
            deck_share = profile.cluster_distribution.get(cid, {}).get("share", 0.0)
            meta_share = self.cmd_cluster_profile.get(commander, {}).get(cid, 0.0)
            if meta_share > 0.05:
                parts.append(f"ce cluster représente {meta_share*100:.0f}% du méta {commander}")
            if gap_bonus > 0.3:
                parts.append(f"renforce un cluster sous-représenté dans ce deck (+{gap_bonus:.2f})")
        neighbors = self.card_neighbors.get(card, [])[:3]
        deck_set = set(profile.cluster_distribution)
        deck_neighbors = [n for n in neighbors if n in self.card_index]
        if deck_neighbors:
            parts.append(f"proche de : {', '.join(deck_neighbors[:2])}")
        parts.append(f"IR prédit : {ir:.0f}%")
        if not parts:
            parts.append("carte compatible avec l'identité couleur")
        return " | ".join(parts)

    # ── Étape 5 : Cartes à retirer ────────────────────────────────────────────

    def generate_cuts(
        self,
        commander: str,
        decklist: list[str],
        deck_profile: DeckProfile,
        top_n: int = 30,
    ) -> list[CutCandidate]:
        """
        Cut score = 0.40×(1-hybrid) + 0.35×(1-coherence) + 0.25×(1-deck_cosine)
        Plus le score est élevé, plus la carte est candidate au retrait.
        """
        deck_vec = np.array(deck_profile.deck_embedding, dtype=np.float32)
        results: list[CutCandidate] = []

        for card in decklist:
            # Ne jamais suggérer de couper les staples universels ou les terrains de base
            if card in _PROTECTED_FROM_CUT:
                continue

            hybrid, ir, cosine = self._hybrid_score(card, commander)

            # Cohérence avec le deck embedding
            ci = self.card_index.get(card)
            deck_cos = 0.0
            if ci is not None and np.any(deck_vec):
                deck_cos = float(np.dot(self.card_matrix[ci], deck_vec))
                deck_cos = max(deck_cos, 0.0)

            # Score de cohérence cluster = le cluster de la carte est-il présent dans le méta ?
            cid = self.card_cluster.get(card)
            meta_weight = self.cmd_cluster_profile.get(commander, {}).get(cid or -1, 0.0)
            coherence = min(meta_weight * 5, 1.0)  # [0,1], plateau à 0.2 du méta

            cut_score = (
                W_CUT_HYBRID    * (1.0 - hybrid)
                + W_CUT_COHERENCE * (1.0 - coherence)
                + W_CUT_COSINE    * (1.0 - deck_cos)
            )

            reasons: list[str] = []
            td = self.tfidf_lookup.get((commander, card), {})
            real_ir = td.get("inclusion_rate", 0.0)
            if real_ir < 5 and real_ir > 0:
                reasons.append(f"faible inclusion historique ({real_ir:.1f}%)")
            elif real_ir == 0:
                reasons.append("absente du méta de ce commandant")
            if hybrid < 0.08:
                reasons.append(f"score hybride faible ({hybrid:.3f})")
            if deck_cos < 0.3:
                reasons.append(f"peu synergique avec le deck ({deck_cos:.3f})")
            if cid is not None:
                ann = self.annotations.get(cid, {})
                if meta_weight < 0.01:
                    reasons.append(f"cluster « {ann.get('name','')} » absent du méta du commandant")
            if not reasons:
                reasons.append("profil globalement moins adapté")

            results.append(CutCandidate(
                card_name=card,
                cut_score=round(cut_score, 4),
                hybrid_score=hybrid,
                coherence_score=round(coherence, 4),
                deck_cosine=round(deck_cos, 4),
                reasons=reasons,
            ))

        results.sort(key=lambda r: -r.cut_score)
        return results[:top_n]

    # ── Étape 6 : Paires de remplacement ─────────────────────────────────────

    def generate_replacements(
        self,
        cuts: list[CutCandidate],
        additions: list[AdditionCandidate],
    ) -> list[dict]:
        """Apparie cuts[i] ↔ additions[i] avec gain estimé."""
        pairs = []
        n = min(len(cuts), len(additions))
        for i in range(n):
            cut = cuts[i]
            add = additions[i]
            gain = round((add.addition_score - (1.0 - cut.cut_score)) * 100, 1)
            pairs.append({
                "rank":        i + 1,
                "cut_card":    cut.card_name,
                "add_card":    add.card_name,
                "cut_score":   cut.cut_score,
                "add_score":   add.addition_score,
                "gain_delta":  gain,
                "cut_reasons": " | ".join(cut.reasons[:2]),
                "add_explanation": add.explanation[:120],
            })
        return pairs

    # ── Étape 7 : Explications détaillées ─────────────────────────────────────

    def generate_explanations(
        self,
        commander: str,
        additions: list[AdditionCandidate],
        deck_profile: DeckProfile,
    ) -> list[dict]:
        explanations = []
        for add in additions:
            ann = self.annotations.get(add.cluster_id or -1, {})
            mechanics = ann.get("mechanics", [])

            # Voisins dans le deck (proximité Card2Vec)
            neighbors_in_deck = [
                n for n in self.card_neighbors.get(add.card_name, [])[:10]
                if n in set(deck_profile.cluster_distribution)
            ]

            td = self.tfidf_lookup.get((commander, add.card_name), {})
            real_ir = td.get("inclusion_rate", 0.0)

            explanations.append({
                "card_name":          add.card_name,
                "addition_score":     add.addition_score,
                "predicted_ir":       add.predicted_ir,
                "real_ir_historical": round(real_ir, 1),
                "cluster":            add.cluster_name,
                "cluster_family":     add.cluster_family,
                "cluster_mechanics":  mechanics,
                "cluster_meta_share": round(
                    self.cmd_cluster_profile.get(commander, {}).get(add.cluster_id or -1, 0.0) * 100, 1
                ),
                "cluster_gap_bonus":  add.cluster_gap_bonus,
                "deck_synergy":       add.deck_synergy,
                "hybrid_score":       add.hybrid_score,
                "summary":            add.explanation,
                "role_strategique":   ann.get("description", "")[:200] if ann else "",
            })
        return explanations

    # ── Analyse complète d'un deck ─────────────────────────────────────────────

    def improve_deck(
        self,
        commander: str,
        decklist: list[str],
        output_dir: Optional[Path] = None,
    ) -> dict:
        """
        Pipeline complet. Retourne un dict résumé + écrit les fichiers dans output_dir.
        """
        t0 = time.perf_counter()
        if output_dir is None:
            safe = commander.replace(",", "").replace(" ", "_").replace("'", "")
            output_dir = OUT_BASE / safe
        output_dir.mkdir(parents=True, exist_ok=True)

        log.info("Analyse : %s (%d cartes)", commander, len(decklist))

        # Étape 1
        profile = self.analyze_deck(commander, decklist)
        (output_dir / "deck_profile.json").write_text(
            json.dumps(asdict(profile), indent=2, ensure_ascii=False), encoding="utf-8"
        )
        log.info("  Cohérence interne : %.4f  |  Cosine cmd : %.4f",
                 profile.coherence_score, profile.avg_cosine_to_commander)

        # Étape 2
        gap_summaries, dist_meta = self.gap_analysis(commander, profile)
        gap_df = pd.DataFrame([asdict(s) for s in gap_summaries])
        gap_df.to_csv(output_dir / "deck_gap_analysis.csv", index=False, encoding="utf-8")
        log.info("  Distance au méta : %.4f  |  Clusters analysés : %d",
                 dist_meta, len(gap_summaries))

        # Étapes 3+4
        additions = self.generate_additions(commander, decklist, profile, gap_summaries)
        add_df = pd.DataFrame([{
            "rank":                  i + 1,
            "card_name":             a.card_name,
            "addition_score":        a.addition_score,
            "predicted_inclusion_%": a.predicted_ir,
            "cluster":               a.cluster_name,
            "family":                a.cluster_family,
            "hybrid_score":          a.hybrid_score,
            "gap_bonus":             a.cluster_gap_bonus,
            "deck_synergy":          a.deck_synergy,
            "explanation":           a.explanation,
        } for i, a in enumerate(additions)])
        add_df.to_csv(output_dir / "top30_additions.csv", index=False, encoding="utf-8")
        log.info("  Top addition : %s (%.4f)", additions[0].card_name if additions else "—",
                 additions[0].addition_score if additions else 0)

        # Étape 5
        cuts = self.generate_cuts(commander, decklist, profile)
        cut_df = pd.DataFrame([{
            "rank":         i + 1,
            "card_name":    c.card_name,
            "cut_score":    c.cut_score,
            "hybrid_score": c.hybrid_score,
            "deck_cosine":  c.deck_cosine,
            "reason":       " | ".join(c.reasons),
        } for i, c in enumerate(cuts)])
        cut_df.to_csv(output_dir / "top30_cuts.csv", index=False, encoding="utf-8")
        log.info("  Top cut : %s (%.4f)", cuts[0].card_name if cuts else "—",
                 cuts[0].cut_score if cuts else 0)

        # Étape 6
        replacements = self.generate_replacements(cuts, additions)
        repl_df = pd.DataFrame(replacements)
        repl_df.to_csv(output_dir / "replacement_pairs.csv", index=False, encoding="utf-8")

        # Étape 7
        explanations = self.generate_explanations(commander, additions, profile)
        (output_dir / "card_explanations.json").write_text(
            json.dumps(explanations, indent=2, ensure_ascii=False), encoding="utf-8"
        )

        # Score global = moyenne pondérée
        global_score = round(
            0.40 * profile.coherence_score
            + 0.35 * profile.avg_cosine_to_commander
            + 0.25 * max(0.0, 1.0 - dist_meta),
            4,
        )

        elapsed = time.perf_counter() - t0
        log.info("  Score global : %.4f  |  Temps : %.2fs", global_score, elapsed)

        result = {
            "commander":            commander,
            "global_score":         global_score,
            "coherence":            profile.coherence_score,
            "avg_cosine_commander": profile.avg_cosine_to_commander,
            "distance_to_meta":     dist_meta,
            "top_additions":        [a.card_name for a in additions[:5]],
            "top_cuts":             [c.card_name for c in cuts[:5]],
            "elapsed_s":            round(elapsed, 2),
            "output_dir":           str(output_dir),
        }

        # Étape 8 : rapport markdown
        self._write_report(
            commander, profile, gap_summaries, dist_meta,
            additions, cuts, replacements, global_score,
            output_dir,
        )
        return result

    # ── Étape 8 : Rapport global ───────────────────────────────────────────────

    def _write_report(
        self, commander, profile, gap_summaries, dist_meta,
        additions, cuts, replacements, global_score, output_dir,
    ) -> None:

        # Clusters sur/sous-représentés
        over  = [s for s in gap_summaries if s.delta > 0.02][:5]
        under = [s for s in gap_summaries if s.delta < -0.02][:5]
        absent = [
            s for s in gap_summaries
            if s.deck_share == 0.0 and s.meta_share > 0.05
        ][:5]

        def cluster_table(rows):
            return "\n".join(
                f"| {s.cluster_id} | {s.name} | {s.family} "
                f"| {s.deck_share*100:.1f}% | {s.meta_share*100:.1f}% "
                f"| {s.delta*100:+.1f}% |"
                for s in rows
            )

        def add_table(items, n=30):
            return "\n".join(
                f"| {i+1} | {a.card_name} | {a.addition_score:.4f} "
                f"| {a.predicted_ir:.1f}% | {a.cluster_name} | {a.cluster_family} |"
                for i, a in enumerate(items[:n])
            )

        def cut_table(items, n=30):
            return "\n".join(
                f"| {i+1} | {c.card_name} | {c.cut_score:.4f} "
                f"| {' | '.join(c.reasons[:2])} |"
                for i, c in enumerate(items[:n])
            )

        def repl_table(items, n=30):
            return "\n".join(
                f"| {r['rank']} | {r['cut_card']} | {r['add_card']} "
                f"| {r['gain_delta']:+.1f} | {r['cut_reasons'][:50]} |"
                for r in items[:n]
            )

        # Top tags du deck
        top_tags = list(profile.tag_distribution.items())[:10]
        tags_str = " | ".join(f"`{t}`×{c}" for t, c in top_tags)

        # Mana curve
        mv_str = "  ".join(f"MV{mv}:{cnt}" for mv, cnt in sorted(profile.mana_curve.items()))

        # Familles
        fam_str = "\n".join(
            f"| {fam} | {share*100:.1f}% |"
            for fam, share in sorted(profile.family_distribution.items(), key=lambda x: -x[1])
        )

        # Score badge
        badge = "Excellent" if global_score > 0.7 else ("Bon" if global_score > 0.5 else "À améliorer")

        report = f"""# Deck Improvement Report — {commander}
> Généré par deck_improver.py

## Score global : {global_score:.4f}/1.0 — {badge}

| Métrique | Valeur |
|---|---|
| Cohérence interne | {profile.coherence_score:.4f} |
| Proximité moyenne au commandant | {profile.avg_cosine_to_commander:.4f} |
| Distance au profil méta | {dist_meta:.4f} |
| Cartes analysées | {profile.card_count} |
| Cartes absentes du corpus Card2Vec | {len(profile.missing_in_corpus)} |

---

## 1. Profil stratégique du deck

**Familles :**

| Famille | Part |
|---|---|
{fam_str}

**Mana curve :** {mv_str}

**Tags dominants :** {tags_str}

**Couleurs :** {' '.join(f'{c}:{n}' for c, n in sorted(profile.color_distribution.items()))}

---

## 2. Comparaison au méta du commandant

**Distance au profil moyen :** {dist_meta:.4f}
*(0 = identique au méta, 1 = complètement différent)*

### Clusters surreprésentés (forces)

| ID | Cluster | Famille | Deck | Méta | Delta |
|---|---|---|---|---|---|
{cluster_table(over) if over else "| — | Aucun cluster significativement surreprésenté | | | | |"}

### Clusters sous-représentés (opportunités)

| ID | Cluster | Famille | Deck | Méta | Delta |
|---|---|---|---|---|---|
{cluster_table(under) if under else "| — | Aucun cluster significativement sous-représenté | | | | |"}

### Clusters absents (manqués)

| ID | Cluster | Famille | Deck | Méta | Delta |
|---|---|---|---|---|---|
{cluster_table(absent) if absent else "| — | Aucun cluster important absent | | | | |"}

---

## 3. Top 30 cartes à ajouter

| Rang | Carte | Score | IR prédit | Cluster | Famille |
|---|---|---|---|---|---|
{add_table(additions)}

---

## 4. Top 30 cartes à retirer

| Rang | Carte | Score coupe | Raisons |
|---|---|---|---|
{cut_table(cuts)}

---

## 5. Top 30 remplacements recommandés

| Rang | Retirer | Ajouter | Gain | Raison coupe |
|---|---|---|---|---|
{repl_table(replacements)}

---

## 6. Explications détaillées (top 5 ajouts)

{self._format_top5_explanations(commander, additions[:5])}

---

## 7. Forces et faiblesses du deck

### Forces
{chr(10).join(f"- **{s.name}** ({s.family}) : {s.deck_share*100:.1f}% du deck vs {s.meta_share*100:.1f}% du méta — archétype maîtrisé." for s in over[:3]) if over else "- Le deck est globalement équilibré par rapport au méta."}

### Faiblesses
{chr(10).join(f"- **{s.name}** ({s.family}) : absent ou sous-représenté ({s.deck_share*100:.1f}% vs {s.meta_share*100:.1f}% attendu)." for s in (under + absent)[:3]) if (under or absent) else "- Aucune faiblesse majeure détectée."}

---

## 8. Cartes absentes du corpus Card2Vec

{', '.join(profile.missing_in_corpus) if profile.missing_in_corpus else "Toutes les cartes sont dans le corpus."}

---

## 9. Fichiers produits

| Fichier | Contenu |
|---|---|
| `deck_profile.json` | Profil complet du deck |
| `deck_gap_analysis.csv` | Delta deck vs méta par cluster |
| `top30_additions.csv` | Top 30 cartes à ajouter |
| `top30_cuts.csv` | Top 30 cartes à retirer |
| `replacement_pairs.csv` | Paires retrait/ajout |
| `card_explanations.json` | Explications détaillées |
| `deck_improvement_report.md` | Ce rapport |
"""
        (output_dir / "deck_improvement_report.md").write_text(report, encoding="utf-8")
        log.info("  Rapport écrit : %s", output_dir / "deck_improvement_report.md")

    def _format_top5_explanations(self, commander: str, additions: list[AdditionCandidate]) -> str:
        blocks = []
        for add in additions:
            ann = self.annotations.get(add.cluster_id or -1, {})
            td = self.tfidf_lookup.get((commander, add.card_name), {})
            real_ir = td.get("inclusion_rate", 0.0)
            neighbors = self.card_neighbors.get(add.card_name, [])[:3]
            block = (
                f"### {add.card_name}\n\n"
                f"**Score d'ajout :** {add.addition_score:.4f} | "
                f"**IR historique :** {real_ir:.1f}% | "
                f"**IR prédit :** {add.predicted_ir:.1f}%\n\n"
                f"**Cluster :** {add.cluster_name} ({add.cluster_family})\n\n"
                f"**Pourquoi :** {add.explanation}\n\n"
            )
            if ann.get("description"):
                block += f"**Rôle stratégique :** {ann['description'][:200]}\n\n"
            if neighbors:
                block += f"**Voisins Card2Vec :** {', '.join(neighbors)}\n"
            blocks.append(block)
        return "\n---\n".join(blocks)


# ── Decklists de test ─────────────────────────────────────────────────────────

DECKLISTS: dict[str, list[str]] = {
    "Galadriel, Light of Valinor": [
        # Elfes et tokens
        "Lathril, Blade of the Elves", "Ezuri, Renegade Leader", "Joraga Warcaller",
        "Imperious Perfect", "Elvish Archdruid", "Llanowar Elves", "Fyndhorn Elves",
        "Elvish Mystic", "Elvish Champion", "Gaea's Cradle", "Marwyn, the Nurturer",
        "Priest of Titania", "Wellwisher", "Sylvan Messenger", "Nath of the Gilt-Leaf",
        "Reach of Branches", "Freyalise, Llanowar's Fury", "Chorus of the Conclave",
        "Hunting Triad", "Elvish Guidance", "Timberwatch Elf", "Wirewood Hivemaster",
        "Titania's Chosen", "Elvish Promenade", "Ambush Commander",
        # Terres
        "Cavern of Souls", "Yavimaya, Cradle of Growth", "Shaman of Forgotten Ways",
        "Crop Rotation", "Planar Bridge",
        # Staples
        "Sol Ring", "Arcane Signet", "Commander's Sphere", "Swiftfoot Boots",
        "Lightning Greaves", "Heroic Intervention", "Return of the Wildspeaker",
        "Overrun", "Triumph of the Hordes", "Beast Within",
        "Nature's Claim", "Kodama's Reach", "Cultivate", "Skyshroud Claim",
        "Three Visits", "Rampant Growth",
        # Draw
        "Sylvan Library", "Guardian Project", "Lifecrafter's Bestiary",
        "Shamanic Revelation", "Collective Unconscious", "Harmonize",
        "Zendikar Resurgent", "Elemental Bond",
        # Removal
        "Krosan Grip", "Song of the Dryads", "Reclamation Sage",
        "Caustic Caterpillar", "Acidic Slime",
        # Finishers
        "Craterhoof Behemoth", "End-Raze Forerunners", "Pathbreaker Ibex",
        "Elvish Soultiller",
        # Terres basiques
        "Forest", "Forest", "Forest", "Forest", "Forest",
        "Forest", "Forest", "Forest", "Forest", "Forest",
        "Forest", "Forest", "Forest", "Forest", "Forest",
        "Forest", "Forest", "Forest", "Forest",
        "Mosswort Bridge", "Tranquil Thicket", "Wirewood Lodge",
    ],

    "Atraxa, Praetors' Voice": [
        # Proliferate / Counters
        "Atraxa, Praetors' Voice",   # ignore — utilisé comme test de robustesse
        "Crystalline Crawler", "Wanderer", "Evolution Sage", "Inexorable Tide",
        "Thrummingbird", "Contagion Clasp", "Contagion Engine", "Viral Drake",
        "Tezzeret's Gambit", "Flux Channeler", "Merciless Executioner",
        "Tekuthal, Inquiry Dominus",
        # Planeswalkers
        "Atraxa, Praetors' Voice",  # doublon intentionnel test
        "Doubling Season", "Vorinclex, Monstrous Raider",
        "Jace, Architect of Thought", "Liliana of the Veil",
        "Elspeth, Sun's Champion", "Garruk Wildspeaker",
        "Vivien of the Arkbow", "Vraska, Relic Seeker",
        "Tamiyo, Collector of Tales", "Nissa, Who Shakes the World",
        # Fixing mana
        "Sol Ring", "Arcane Signet", "Azorius Signet", "Dimir Signet",
        "Golgari Signet", "Simic Signet", "Command Tower",
        "Exotic Orchard", "Murmuring Bosk",
        "Breeding Pool", "Overgrown Tomb", "Watery Grave", "Temple Garden",
        "Godless Shrine", "Hallowed Fountain",
        # Draw / Control
        "Cyclonic Rift", "Swords to Plowshares", "Path to Exile",
        "Counterspell", "Swan Song", "Negate",
        "Rhystic Study", "Phyrexian Arena", "Necropotence",
        "Toxic Deluge", "Supreme Verdict",
        # Ramp
        "Cultivate", "Kodama's Reach", "Farseek", "Nature's Lore",
        # Tech
        "Deepglow Skate", "Fathom Mage", "Champion of Lambholt",
        "Ishai, Ojutai Dragonspeaker", "Reyhan, Last of the Abzan",
        "Skatewing Spy",
        # Terres
        "Forest", "Island", "Plains", "Swamp",
        "Forest", "Island", "Plains", "Swamp",
        "Forest", "Island",
    ],

    "Meren of Clan Nel Toth": [
        # Graveyard / Sacrifice engine
        "Viscera Seer", "Carrion Feeder", "Ashnod's Altar", "Phyrexian Altar",
        "Birthing Pod", "Yawgmoth, Thran Physician", "Grave Pact",
        "Dictate of Erebos", "Butcher of Malakir",
        "Blood Artist", "Zulaport Cutthroat", "Vindictive Vampire",
        "Bastion of Remembrance",
        # Recursion
        "Animate Dead", "Reanimate", "Necromancy", "Dance of the Dead",
        "Sheoldred, Whispering One", "Ever After", "Return to Dust",
        "Deathreap Ritual",
        # ETB value
        "Eternal Witness", "Reclamation Sage", "Shriekmaw", "Ravenous Chupacabra",
        "Terastodon", "Woodfall Primus", "Acidic Slime",
        "Plaguecrafter", "Merciless Executioner", "Fleshbag Marauder",
        # Tutors
        "Demonic Tutor", "Diabolic Intent", "Vampiric Tutor",
        "Natural Order", "Eldritch Evolution",
        # Draw
        "Phyrexian Arena", "Erebos, God of the Dead", "Grim Haruspex",
        "Midnight Reaper", "Skullclamp", "Greater Good",
        # Ramp
        "Sol Ring", "Arcane Signet", "Golgari Signet",
        "Cultivate", "Kodama's Reach", "Farseek",
        "Llanowar Elves", "Elvish Mystic",
        # Finishers
        "Avenger of Zendikar", "Craterhoof Behemoth",
        # Terres
        "Command Tower", "Overgrown Tomb", "Woodland Cemetery",
        "Golgari Rot Farm", "Temple of Malady",
        "Forest", "Forest", "Forest", "Forest", "Forest",
        "Swamp", "Swamp", "Swamp", "Swamp", "Swamp",
        "Forest", "Forest",
    ],

    "The Ur-Dragon": [
        # Dragons
        "Lathliss, Dragon Queen", "Dragon Tempest", "Utvara Hellkite",
        "Scourge of Valkas", "Glorybringer", "Niv-Mizzet, Parun",
        "Miirym, Sentinel Wyrm", "Bladewing the Risen", "Balefire Dragon",
        "Hellkite Tyrant", "Broodmother Dragon", "Dragonlord Atarka",
        "Dragonlord Dromoka", "Dragonlord Kolaghan", "Dragonlord Ojutai",
        "Dragonlord Silumgar",
        # Tribal support
        "Kindred Discovery", "Kolaghan, the Storm's Fury",
        "Haven of the Spirit Dragon", "Dragon's Hoard",
        "Dragonspeaker Shaman", "Dragonmaster Outcast",
        "Sarkhan the Masterless", "Sarkhan Unbroken",
        # Ramp
        "Sol Ring", "Arcane Signet", "Chromatic Lantern",
        "Commander's Sphere", "Farseek", "Nature's Lore",
        "Skyshroud Claim", "Selvala, Heart of the Wilds",
        # Fixing mana
        "Command Tower", "Ketria Triome", "Zagoth Triome",
        "Raugrin Triome", "Savai Triome", "Indatha Triome",
        "Stomping Ground", "Sacred Foundry", "Blood Crypt",
        "Breeding Pool", "Overgrown Tomb",
        # Removal / Protection
        "Swords to Plowshares", "Cyclonic Rift", "Chaos Warp",
        "Heroic Intervention", "Swiftfoot Boots", "Lightning Greaves",
        # Finisher
        "Overwhelming Stampede",
        # Terres basiques
        "Forest", "Mountain", "Plains", "Island", "Swamp",
        "Forest", "Mountain",
    ],

    "Teysa Karlov": [
        # Aristocrats core
        "Blood Artist", "Zulaport Cutthroat", "Bastion of Remembrance",
        "Vindictive Vampire", "Cruel Celebrant",
        # Sacrifice outlets
        "Ashnod's Altar", "Phyrexian Altar", "Viscera Seer",
        "Carrion Feeder", "Razaketh, the Foulblooded",
        # Token generators
        "Luminous Broodmoth", "Requiem Angel", "Haunted Crossroads",
        "Midnight Haunting", "Captain of the Watch", "Sermon of Saint Traft",
        "Hallowed Spiritkeeper",
        # Death triggers
        "Grave Pact", "Dictate of Erebos", "Butcher of Malakir",
        "Deathreap Ritual", "Midnight Reaper", "Grim Haruspex",
        # Recursion
        "Reanimate", "Animate Dead", "Necromancy", "Victimize",
        "Sun Titan", "Reveillark",
        # Tutors
        "Demonic Tutor", "Enlightened Tutor", "Vampiric Tutor",
        # Removal
        "Swords to Plowshares", "Path to Exile", "Generous Gift",
        "Anguished Unmaking", "Vindicate",
        # Ramp / Fixing
        "Sol Ring", "Arcane Signet", "Orzhov Signet",
        "Talisman of Hierarchy", "Command Tower",
        "Godless Shrine", "Isolated Chapel", "Concealed Courtyard",
        # Terres basiques
        "Plains", "Plains", "Plains", "Plains", "Plains",
        "Swamp", "Swamp", "Swamp", "Swamp", "Swamp",
        "Plains", "Plains", "Swamp", "Swamp",
    ],
}


# ── Point d'entrée ────────────────────────────────────────────────────────────

def main() -> None:
    log.info("=== deck_improver.py ===")

    engine = DeckImprovementEngine()
    log.info("")

    results_summary: list[dict] = []

    for commander, decklist in DECKLISTS.items():
        log.info("=" * 60)
        # Dédupliquer si commandant glissé dans la liste par accident
        deck = [c for c in decklist if c != commander]
        result = engine.improve_deck(commander, deck)
        results_summary.append(result)
        log.info("  >> Score %.4f | Adds: %s | Cuts: %s | %.2fs",
                 result["global_score"],
                 result["top_additions"][:2],
                 result["top_cuts"][:2],
                 result["elapsed_s"])
        log.info("")

    # Rapport de synthèse multi-decks
    log.info("=== SYNTHÈSE ===")
    for r in results_summary:
        log.info(
            "  %-35s  score=%.4f  dist_meta=%.4f  t=%.2fs",
            r["commander"], r["global_score"], r["distance_to_meta"], r["elapsed_s"]
        )

    # CSV synthèse
    OUT_BASE.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(results_summary).to_csv(
        OUT_BASE / "validation_summary.csv", index=False, encoding="utf-8"
    )
    log.info("")
    log.info("=== Terminé — sorties dans %s ===", OUT_BASE)


if __name__ == "__main__":
    main()
