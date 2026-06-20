#!/usr/bin/env python3
"""
build_card2vec.py

Pipeline Card2Vec : entraîne un modèle Word2Vec sur les decklists MTG.
Chaque deck = une phrase, chaque carte = un mot.

Sorties principales (data/embeddings/) :
  card2vec.model              -- modèle Gensim complet (rechargeable)
  card_embeddings.npy         -- matrice float32 (N_cartes × vector_size)
  card_embeddings.csv         -- card_name + vecteur aplati (pour inspection)
  card_index.json             -- { card_name: index } pour retrouver une ligne dans .npy
  card_neighbors.csv          -- 20 voisins les plus proches pour chaque carte
  commander_embeddings.npy    -- vecteurs commandants pondérés TF-IDF (34 × vector_size)
  commander_embeddings.json   -- { commander: [vecteur float] }
"""
from __future__ import annotations

import csv
import json
import logging
import re
import sys
import unicodedata
from pathlib import Path
from typing import Iterator

import numpy as np
from gensim.models import Word2Vec
from tqdm import tqdm

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# ── Logging ──────────────────────────────────────────────────────────────────
LOG_DIR = ROOT / "logs"
LOG_DIR.mkdir(exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(LOG_DIR / "build_card2vec.log", encoding="utf-8"),
    ],
)
log = logging.getLogger(__name__)

# ── Chemins ──────────────────────────────────────────────────────────────────
DECKLISTS_DIR = ROOT / "data" / "Decklists"
TFIDF_CSV     = ROOT / "data" / "stats" / "commander_tfidf.csv"
OUT_DIR       = ROOT / "data" / "embeddings"
OUT_DIR.mkdir(parents=True, exist_ok=True)

# ── Hyperparamètres Word2Vec ──────────────────────────────────────────────────
VECTOR_SIZE   = 128    # dimension des embeddings
WINDOW        = 10     # fenêtre de contexte (deck entier ~ 88 cartes → large fenêtre)
MIN_COUNT     = 5      # ignorer les cartes présentes dans moins de 5 decks
WORKERS       = 4      # threads parallèles
EPOCHS        = 10     # passes sur le corpus
SEED          = 42
TOP_N         = 20     # voisins exportés par carte
# Sous-échantillonnage des cartes ultra-fréquentes (Sol Ring, Arcane Signet…).
# Gensim sous-échantillonne un token t avec proba 1 - sqrt(sample/freq(t)).
# 1e-4 sur un corpus de ~36k phrases : tokens apparaissant dans >35% des decks
# sont agressivement sous-échantillonnés, ce qui améliore leurs représentations.
SAMPLE        = 1e-4


# ── Normalisation ─────────────────────────────────────────────────────────────

def normalize(name: str) -> str:
    """
    Normalise un nom de carte en token unique sans espaces ni ponctuation.
    "Sol Ring" -> "sol_ring"
    "Aesi, Tyrant of Gyre Strait" -> "aesi_tyrant_of_gyre_strait"
    """
    name = name.strip()
    name = unicodedata.normalize("NFKD", name)
    name = "".join(c for c in name if not unicodedata.combining(c))
    name = name.lower()
    name = re.sub(r"[^a-z0-9\s]", "", name)
    name = re.sub(r"\s+", "_", name.strip())
    return name


# ── Lecture du corpus ─────────────────────────────────────────────────────────

def build_corpus_and_name_table(
    decklists_dir: Path,
) -> tuple[list[list[str]], dict[str, str]]:
    """
    Lit toutes les decklists en un seul passage.

    Retourne :
      sentences     -- liste de décks, chaque deck = liste de tokens
      token_to_name -- { token: nom_original } première occurrence gagnante
    """
    token_to_name: dict[str, str] = {}
    sentences: list[list[str]] = []
    csv_files = list(decklists_dir.rglob("*.csv"))

    for path in tqdm(csv_files, desc="Lecture corpus", unit="deck"):
        try:
            cards: list[str] = []
            with open(path, encoding="utf-8-sig", errors="replace") as f:
                reader = csv.DictReader(f, delimiter=";")
                for row in reader:
                    name = (row.get("Card Name") or "").strip()
                    if not name:
                        continue
                    qty_raw = (row.get("Quantity") or "1").strip()
                    try:
                        qty = max(1, int(qty_raw))
                    except ValueError:
                        qty = 1
                    token = normalize(name)
                    if not token:
                        continue
                    # Conserver le premier nom original rencontré pour ce token
                    if token not in token_to_name:
                        token_to_name[token] = name
                    # Répéter selon quantité (max 4 pour ne pas sur-peser les terrains)
                    cards.extend([token] * min(qty, 4))
            if cards:
                sentences.append(cards)
        except Exception as exc:
            log.warning("Erreur lecture %s : %s", path.name, exc)

    log.info(
        "Corpus : %d decklists | %d tokens uniques avant min_count",
        len(sentences),
        len(token_to_name),
    )
    return sentences, token_to_name


class SentenceList:
    """Wrapper itérable sur une liste déjà chargée en mémoire."""

    def __init__(self, sentences: list[list[str]]) -> None:
        self._sentences = sentences

    def __len__(self) -> int:
        return len(self._sentences)

    def __iter__(self) -> Iterator[list[str]]:
        yield from self._sentences


# ── Entraînement ──────────────────────────────────────────────────────────────

def train_model(sentences: list[list[str]]) -> Word2Vec:
    corpus = SentenceList(sentences)
    log.info("Construction du vocabulaire...")
    model = Word2Vec(
        vector_size=VECTOR_SIZE,
        window=WINDOW,
        min_count=MIN_COUNT,
        workers=WORKERS,
        seed=SEED,
        sg=1,        # Skip-Gram : meilleur pour vocabulaires moyens avec mots rares
        hs=0,        # Negative sampling
        negative=10,
        sample=SAMPLE,
    )
    model.build_vocab(corpus)
    log.info(
        "Vocabulaire : %d tokens (min_count=%d)",
        len(model.wv),
        MIN_COUNT,
    )

    log.info("Entraînement Word2Vec (%d epochs)...", EPOCHS)
    model.train(
        corpus,
        total_examples=model.corpus_count,
        epochs=EPOCHS,
        report_delay=30,
    )
    return model


# ── Export embeddings ─────────────────────────────────────────────────────────

def export_embeddings(
    model: Word2Vec,
    token_to_name: dict[str, str],
) -> tuple[np.ndarray, dict[str, int]]:
    """
    Retourne :
      matrix       -- float32 (N × VECTOR_SIZE), trié alphabétiquement par token
      token_to_idx -- { token: index_dans_matrix }
    """
    vocab = sorted(model.wv.index_to_key)
    matrix = np.array([model.wv[t] for t in vocab], dtype=np.float32)
    token_to_idx = {t: i for i, t in enumerate(vocab)}

    def display_name(token: str) -> str:
        return token_to_name.get(token, token.replace("_", " ").title())

    # card_embeddings.npy
    npy_path = OUT_DIR / "card_embeddings.npy"
    np.save(npy_path, matrix)
    log.info("Sauvegarde : %s  shape=%s", npy_path.name, matrix.shape)

    # card_index.json  { nom_lisible: index }
    index_path = OUT_DIR / "card_index.json"
    name_to_idx = {display_name(t): i for t, i in token_to_idx.items()}
    index_path.write_text(
        json.dumps(name_to_idx, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    log.info("Sauvegarde : %s  (%d cartes)", index_path.name, len(name_to_idx))

    # card_embeddings.csv  (card_name, v0, v1, ..., v127)
    csv_path = OUT_DIR / "card_embeddings.csv"
    headers = ["card_name"] + [f"v{i}" for i in range(VECTOR_SIZE)]
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(headers)
        for token, idx in token_to_idx.items():
            writer.writerow([display_name(token)] + matrix[idx].tolist())
    log.info("Sauvegarde : %s", csv_path.name)

    return matrix, token_to_idx


# ── Voisins les plus proches ──────────────────────────────────────────────────

def nearest_neighbors(
    card_name: str,
    model: Word2Vec,
    token_to_name: dict[str, str],
    top_n: int = TOP_N,
) -> list[tuple[str, float]]:
    """
    Retourne les top_n cartes les plus proches de card_name.
    Accepte un nom lisible ("Sol Ring") ou un token normalisé ("sol_ring").
    """
    token = normalize(card_name)
    if token not in model.wv:
        raise KeyError(f"Carte inconnue : '{card_name}' (token='{token}')")
    results = model.wv.most_similar(token, topn=top_n)
    return [
        (token_to_name.get(t, t.replace("_", " ").title()), float(sim))
        for t, sim in results
    ]


def export_neighbors(model: Word2Vec, token_to_name: dict[str, str]) -> None:
    """Écrit card_neighbors.csv : pour chaque carte, ses top N voisins."""
    vocab = sorted(model.wv.index_to_key)
    neighbors_path = OUT_DIR / "card_neighbors.csv"

    with open(neighbors_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["card_name", "rank", "neighbor", "similarity"])
        for token in tqdm(vocab, desc="Voisins", unit="carte"):
            card_display = token_to_name.get(token, token.replace("_", " ").title())
            neighbors = model.wv.most_similar(token, topn=TOP_N)
            for rank, (n_token, sim) in enumerate(neighbors, start=1):
                n_display = token_to_name.get(n_token, n_token.replace("_", " ").title())
                writer.writerow([card_display, rank, n_display, round(float(sim), 6)])

    log.info(
        "Sauvegarde : %s  (%d cartes x %d voisins)",
        neighbors_path.name,
        len(vocab),
        TOP_N,
    )


# ── Commander Embeddings pondérés TF-IDF ─────────────────────────────────────

def build_commander_embeddings(model: Word2Vec, tfidf_path: Path) -> None:
    """
    Construit un vecteur par commandant par moyenne pondérée TF-IDF.

    v(commander) = sum(tfidf_norm(card) × v(card)) / sum(tfidf_norm)

    Sorties :
      commander_embeddings.npy  -- float32 (nb_commandants × VECTOR_SIZE)
      commander_embeddings.json -- { commander: [float, ...] }
    """
    if not tfidf_path.exists():
        log.warning("commander_tfidf.csv introuvable — skip commander embeddings")
        return

    import pandas as pd
    log.info("Chargement TF-IDF depuis %s...", tfidf_path.name)
    df = pd.read_csv(tfidf_path, encoding="utf-8")

    # Pré-calculer les tokens une seule fois
    df["token"] = df["card_name"].map(normalize)

    # Filtrer les tokens hors vocabulaire
    in_vocab = df["token"].isin(model.wv.key_to_index)
    log.info(
        "Cartes avec embedding : %d / %d (%.1f%%)",
        in_vocab.sum(), len(df), 100 * in_vocab.mean(),
    )
    df = df[in_vocab].copy()

    # Récupérer tous les vecteurs d'un coup (vectorisé, pas de .iterrows())
    vectors = np.array(
        [model.wv[t] for t in df["token"]], dtype=np.float64
    )
    weights = df["tfidf_norm"].to_numpy(dtype=np.float64)

    commanders = sorted(df["commander"].unique())
    log.info("%d commandants a vectoriser...", len(commanders))

    cmd_index = {cmd: i for i, cmd in enumerate(commanders)}
    matrix_rows: list[np.ndarray] = []
    result: dict[str, list[float]] = {}

    for commander in tqdm(commanders, desc="Commander embeddings", unit="cmd"):
        mask = (df["commander"] == commander).to_numpy()
        w = weights[mask]
        v = vectors[mask]
        total = w.sum()
        vec = ((v * w[:, None]).sum(axis=0) / total).astype(np.float32) if total > 0 else np.zeros(VECTOR_SIZE, dtype=np.float32)
        result[commander] = vec.tolist()
        matrix_rows.append(vec)

    cmd_matrix = np.array(matrix_rows, dtype=np.float32)

    npy_path = OUT_DIR / "commander_embeddings.npy"
    np.save(npy_path, cmd_matrix)
    log.info("Sauvegarde : %s  shape=%s", npy_path.name, cmd_matrix.shape)

    json_path = OUT_DIR / "commander_embeddings.json"
    payload = {
        "commanders": commanders,
        "commander_to_index": cmd_index,
        "vector_size": VECTOR_SIZE,
        "embeddings": result,
    }
    json_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    log.info("Sauvegarde : %s", json_path.name)


# ── Point d'entrée ────────────────────────────────────────────────────────────

def main() -> None:
    log.info("=== build_card2vec.py ===")
    log.info(
        "Hyperparametres : vector_size=%d  window=%d  min_count=%d  epochs=%d  sg=1  sample=%s",
        VECTOR_SIZE, WINDOW, MIN_COUNT, EPOCHS, SAMPLE,
    )

    # Un seul passage disque pour lire corpus + construire table des noms
    sentences, token_to_name = build_corpus_and_name_table(DECKLISTS_DIR)

    # Entraînement
    model = train_model(sentences)

    # Sauvegarde du modèle complet
    model_path = OUT_DIR / "card2vec.model"
    model.save(str(model_path))
    log.info("Modele sauvegarde : %s", model_path.name)

    # Sauvegarder la table token→nom original pour les réutilisations futures
    names_path = OUT_DIR / "token_to_name.json"
    names_path.write_text(
        json.dumps(token_to_name, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    log.info("Sauvegarde : %s  (%d entrees)", names_path.name, len(token_to_name))

    # Export matrice + index
    _matrix, _token_idx = export_embeddings(model, token_to_name)

    # Voisins
    export_neighbors(model, token_to_name)

    # Commander embeddings pondérés TF-IDF
    build_commander_embeddings(model, TFIDF_CSV)

    # Aperçu qualitatif
    log.info("--- Apercu nearest_neighbors ---")
    for sample_card in ["Sol Ring", "Arcane Signet", "Cultivate", "Atraxa, Praetors' Voice", "Krenko, Mob Boss"]:
        try:
            neighbors = nearest_neighbors(sample_card, model, token_to_name, top_n=5)
            log.info("  %s :", sample_card)
            for name, sim in neighbors:
                log.info("    %.4f  %s", sim, name)
        except KeyError as e:
            log.warning("  %s", e)

    log.info("=== Termine ===")
    log.info("  Cartes dans le vocabulaire : %d", len(model.wv))
    log.info("  Sorties dans               : %s", OUT_DIR)


if __name__ == "__main__":
    main()
