# Importe tous les modèles ici pour qu'Alembic les détecte via Base.metadata
from src.manamind.db.models.card import Card
from src.manamind.db.models.card_face import CardFace
from src.manamind.db.models.card_price import CardPrice
from src.manamind.db.models.card_printing import CardPrinting
from src.manamind.db.models.import_run import ImportRun
from src.manamind.db.models.mtg_set import MtgSet

__all__ = ["Card", "CardFace", "CardPrinting", "MtgSet", "CardPrice", "ImportRun"]
