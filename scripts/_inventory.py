import json, numpy as np
from pathlib import Path
import sys
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

files = {
    "card_embeddings.npy":       "data/embeddings/card_embeddings.npy",
    "card_index.json":           "data/embeddings/card_index.json",
    "commander_embeddings.npy":  "data/embeddings/commander_embeddings.npy",
    "commander_embeddings.json": "data/embeddings/commander_embeddings.json",
    "xgb_card2vec.json":         "data/models/xgb_card2vec.json",
    "cluster_annotations.json":  "data/clustering/cluster_annotations.json",
    "tag_to_cluster.csv":        "data/tag_cluster/tag_to_cluster.csv",
    "cluster_to_tag.csv":        "data/tag_cluster/cluster_to_tag.csv",
    "commander_tfidf.csv":       "data/stats/commander_tfidf.csv",
    "train.csv":                 "data/ml/train.csv",
    "feature_info.json":         "data/ml/feature_info.json",
    "card_neighbors.csv":        "data/embeddings/card_neighbors.csv",
    "token_to_name.json":        "data/embeddings/token_to_name.json",
}
for name, path in files.items():
    p = ROOT / path
    ok = p.exists()
    sz = f"{p.stat().st_size // 1024}KB" if ok else "MANQUANT"
    status = "OK" if ok else "!!"
    print(f"  {status}  {name:35s}  {sz}")

print()
ce = np.load(ROOT / "data/embeddings/card_embeddings.npy")
me = np.load(ROOT / "data/embeddings/commander_embeddings.npy")
ci = json.loads((ROOT / "data/embeddings/card_index.json").read_text("utf-8"))
cm = json.loads((ROOT / "data/embeddings/commander_embeddings.json").read_text("utf-8"))
print(f"card_embeddings:     {ce.shape}  dtype={ce.dtype}")
print(f"commander_embeds:    {me.shape}  dtype={me.dtype}")
print(f"card_index entries:  {len(ci)}")
print(f"commanders:          {len(cm['commanders'])}")
print(f"commanders list:     {cm['commanders'][:5]}")

fi = json.loads((ROOT / "data/ml/feature_info.json").read_text("utf-8"))
print(f"\nML features:  {fi['features']}")
print(f"label:        {fi['label_log']}")
print(f"train cmds: {len(fi['commanders_train'])}  |  test cmds: {len(fi['commanders_test'])}")
print(f"test commanders: {fi['commanders_test']}")

ann = json.loads((ROOT / "data/clustering/cluster_annotations.json").read_text("utf-8"))
print(f"\nAnnotations: {len(ann)} clusters")
print(f"Sample: {ann[0]['cluster_id']} - {ann[0]['name']} ({ann[0]['primary_strategy']})")

import pandas as pd
tfidf = pd.read_csv(ROOT / "data/stats/commander_tfidf.csv")
print(f"\ntfidf: {tfidf.shape}  cols={list(tfidf.columns)}")
print(f"commanders in tfidf: {tfidf['commander'].nunique()}")
print(f"commanders: {sorted(tfidf['commander'].unique())[:5]}")
