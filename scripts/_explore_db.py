import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from sqlalchemy import text
from src.manamind.db.engine import SessionLocal

with SessionLocal() as s:
    # Jointure tags + card name
    sample = s.execute(text("""
        SELECT sc.name, sct.tag_name
        FROM scryfall_card_tags sct
        JOIN scryfall_cards sc ON sc.id = sct.card_id
        WHERE sc.name = 'Zimone and Dina'
        LIMIT 20
    """)).fetchall()
    print("Zimone and Dina tags:")
    for r in sample:
        print(" ", r)

    # Combien de cartes ont des tags ?
    n_cards = s.execute(text(
        "SELECT COUNT(DISTINCT card_id) FROM scryfall_card_tags"
    )).scalar()
    n_tags = s.execute(text("SELECT COUNT(DISTINCT tag_name) FROM scryfall_card_tags")).scalar()
    print(f"\nCartes avec tags: {n_cards}  |  Tags distincts: {n_tags}")

    # Top 20 tags les plus frequents
    top = s.execute(text("""
        SELECT tag_name, COUNT(*) as cnt
        FROM scryfall_card_tags
        GROUP BY tag_name
        ORDER BY cnt DESC
        LIMIT 20
    """)).fetchall()
    print("\nTop 20 tags:")
    for r in top:
        print(f"  {r[0]:40s} {r[1]}")
