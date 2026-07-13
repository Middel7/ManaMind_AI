"""HTML d'une page deck Moxfield -> Deck.

Fonction pure : aucune I/O, aucun réseau. C'est le cœur fragile du projet —
les sélecteurs ci-dessous dépendent du markup de Moxfield et casseront le jour
où ils refondront leur front. Les tests de tests/test_parser.py rejouent des
fixtures HTML : les garder verts est la seule façon de détecter la casse.
"""

import json
import re
from datetime import datetime
from decimal import Decimal, InvalidOperation

from bs4 import BeautifulSoup

from .models import Card, Deck

BUDGET_KEYWORDS = {"budget", "cheap", "afford", "casual"}

# Un deck Commander légal fait 100 cartes. En dessous de ce seuil, la page a
# presque toujours été capturée avant la fin du rendu de la decklist.
MIN_CARDS = 70

# Moxfield obfusque ses classes CSS au build : "XIi4jFys2lGhYwseGpBo" est le nom
# généré pour les <li> de decklist, doublé de la classe stable "decklist-card".
_CARD_LI = re.compile(r"decklist-card|XIi4jFys2lGhYwseGpBo")
_CARD_LINK = re.compile(r"table-deck-row-link")
_QTY_DIV = re.compile(r"width:\s*20px")
_BRACKET_DIV = re.compile(
    r"d-inline-block.*text-nowrap.*cursor-pointer|cursor-pointer.*text-nowrap.*d-inline-block"
)
_PRICE_SPAN = re.compile(r"cursor-pointer.*ms-4|ms-4.*cursor-pointer")


def fix_card_name(raw: str) -> str:
    """Restaure les espaces mangés par le rendu ("LightningBolt" -> "Lightning Bolt")."""
    name = re.sub(r"([a-z])([A-Z])", r"\1 \2", raw)
    name = re.sub(r"([^\s]),([^\s])", r"\1, \2", name)
    return name.strip()


def normalize(name: str) -> str:
    return re.sub(r"\s+", " ", name).strip().lower()


def commander_names(raw: str | None) -> list[str]:
    """Un deck à partenaires expose "A // B" — on veut les deux noms séparément."""
    if not raw:
        return []
    return [p.strip() for p in re.split(r"\s*(?://|/)\s*", raw) if p.strip()]


def _parse_datetime(value: str) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _parse_price(raw: str) -> tuple[Decimal | None, str]:
    m = re.search(r"([€$£])\s*([\d,.\s]+)", raw)
    if not m:
        return None, ""
    currency, amount = m.group(1), m.group(2).strip()

    # "1,234.56" -> la virgule sépare les milliers ; "1234,56" -> elle est décimale.
    if "," in amount and "." in amount:
        amount = amount.replace(",", "")
    elif "," in amount:
        amount = amount.replace(",", ".")
    amount = amount.replace(" ", "")

    try:
        return Decimal(amount), currency
    except InvalidOperation:
        return None, currency


def parse_deck_html(html: str, deck_id: str, *, min_cards: int = MIN_CARDS) -> Deck | None:
    """Retourne None si la page est incomplète (decklist tronquée ou absente)."""
    soup = BeautifulSoup(html, "html.parser")

    commander: str | None = None
    deck_type = ""
    for meta in soup.find_all("meta", property="og:title"):
        content = meta.get("content", "")
        m = re.search(r"Commander\s*[\(\[](.+?)[\)\]]", content)
        if m:
            commander = m.group(1).strip()
        lowered = content.lower()
        if "cedh" in lowered:
            deck_type = "CEDH"
        elif any(w in lowered for w in BUDGET_KEYWORDS):
            deck_type = "BUDGET"

    date_created = date_modified = None
    json_ld = soup.find("script", {"type": "application/ld+json"})
    if json_ld and json_ld.string:
        try:
            data = json.loads(json_ld.string)
            date_created = _parse_datetime(data.get("datePublished", ""))
            date_modified = _parse_datetime(data.get("dateModified", ""))
        except (json.JSONDecodeError, AttributeError):
            pass

    decklist: dict[str, int] = {}
    for li in soup.find_all("li", class_=_CARD_LI):
        name_tag = li.find("a", class_=_CARD_LINK)
        qty_tag = li.find("div", style=_QTY_DIV)
        if not name_tag or not qty_tag:
            continue
        try:
            quantity = int(qty_tag.get_text(strip=True))
        except ValueError:
            continue
        name = fix_card_name(name_tag.get_text(strip=True))
        if name and quantity:
            decklist[name] = quantity

    # Le commandant n'apparaît pas toujours dans la liste principale.
    for name in commander_names(commander):
        if not any(normalize(k) == normalize(name) for k in decklist):
            decklist[name] = 1

    # Secours : certaines pages ne rendent que les images des cartes.
    if not decklist:
        for name, _qty in re.findall(r'alt="([^"]+)"[^<]+<.*?x(\d+)<', html, re.DOTALL):
            decklist.setdefault(name.strip(), 1)

    if len(decklist) < min_cards:
        return None

    bracket: int | None = None
    bracket_div = soup.find("div", class_=_BRACKET_DIV)
    if bracket_div:
        m = re.search(r"Bracket\s*(\d+)", bracket_div.get_text(strip=True))
        if m:
            bracket = int(m.group(1))

    price, currency = None, ""
    price_tag = soup.find("span", class_=_PRICE_SPAN)
    if price_tag:
        price, currency = _parse_price(price_tag.get_text(strip=True))

    commander_set = {normalize(n) for n in commander_names(commander)}
    cards = [
        Card(name=name, quantity=qty, is_commander=normalize(name) in commander_set)
        for name, qty in sorted(decklist.items())
    ]

    return Deck(
        deck_id=deck_id,
        commander=commander,
        deck_type=deck_type,
        date_created=date_created,
        date_modified=date_modified,
        bracket=bracket,
        price=price,
        currency=currency,
        cards=cards,
    )
