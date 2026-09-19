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


# DFC dont les deux faces n'ont pas le même premier mot — liste explicite pour
# éviter les faux positifs (ex. "The Fourteenth Doctor & Clara Oswald" = partners).
_KNOWN_DFC_FRONTS = frozenset([
    "cosima, god of the voyage",    # // The Omenkeel
    "bruce banner",                 # // The Incredible Hulk
    "tony stark",                   # // The Invincible Iron Man
    "urabrask",                     # // The Great Work
    "esika, god of the tree",       # // The Prismatic Bridge
    "tergrid, god of fright",       # // Tergrid's Lantern
])


def _norm_word(w: str) -> str:
    return re.sub(r"[^a-zA-Z0-9']", "", w).lower()


def is_dfc_commander(raw: str) -> bool:
    """Heuristique : commandant double-face.

    Règle 1 — même premier mot  (ex. "Kefka, Court Mage & Kefka, Ruler of Ruin").
    Règle 2 — même premier mot sauf apostrophe-s  (ex. "Tergrid & Tergrid's Lantern").
    Règle 3 — dernier mot de la face 0 = premier mot de la face 1 :
               capture les DFC où le nom partagé est en fin de face 0
               (ex. "Esper Terra & Terra, Magical Adept" — "Terra" commun).
    Règle 4 — face avant connue dans _KNOWN_DFC_FRONTS (liste explicite pour
               les cas non détectables par heuristique).
    """
    if "&" not in raw:
        return False
    parts = [p.strip() for p in raw.split("&", 1)]
    if len(parts) != 2 or not parts[0] or not parts[1]:
        return False
    words0 = parts[0].split()
    words1 = parts[1].split()
    w0_first = _norm_word(words0[0])
    w1_first = _norm_word(words1[0])
    w0_last  = _norm_word(words0[-1])
    if w0_first == w1_first:
        return True
    if w0_first == w1_first.rstrip("s").rstrip("'"):
        return True
    if w0_last and w0_last == w1_first:
        return True
    return parts[0].lower() in _KNOWN_DFC_FRONTS


def normalize_commander_sep(raw: str) -> str:
    """Normalise le séparateur :
    - & → // pour les DFC
    - & reste pour les partners, mais les noms sont triés alphabétiquement
      pour garantir un ordre canonique indépendant du deckbuilder.
    """
    if "&" not in raw:
        return raw
    if is_dfc_commander(raw):
        return re.sub(r"\s*&\s*", " // ", raw, count=1)
    # Partners : ordre canonique alphabétique
    # Garde-fou : si un nom contient lui-même un '&' (ex. "Leo, Chaos & Order"),
    # le tri créerait une ambiguïté de parsing — on conserve l'ordre d'origine.
    parts = [p.strip() for p in raw.split("&", 1)]
    if "&" in parts[0] or "&" in parts[1]:
        return raw
    parts.sort()
    return " & ".join(parts)


def commander_names(raw: str | None) -> list[str]:
    """Sépare les noms de commandants (DFC '// ' ou partners '&')."""
    if not raw:
        return []
    return [p.strip() for p in re.split(r"\s*(?://|/|&)\s*", raw) if p.strip()]


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


# Correspond UNIQUEMENT aux intitulés de section purs ("Maybeboard", "Considering (5)"),
# PAS aux phrases ("cards I'm considering…") dans les descriptions de decks.
# \b seul était trop large : il matchait du texte libre → tout le deck était exclu.
_EXCL_SECTION = re.compile(
    r"^\s*(maybeboard|considering|scratch\s*pad)\s*(?:\(\d+\))?\s*$", re.I
)


def _excluded_li_ids(soup) -> set[int]:
    """Retourne les id() des <li class=_CARD_LI> dans des sections exclues
    (Maybeboard, Considering…) en remontant depuis le heading jusqu'au conteneur.
    Heuristique : conteneur valide si ≤50 <li> (section isolée).
    Le seuil 50 évite d'exclure accidentellement un deck principal de ~100 cartes."""
    excluded: set[int] = set()
    for text_node in soup.find_all(string=_EXCL_SECTION):
        ancestor = text_node.parent
        for _ in range(6):
            if ancestor is None or ancestor.name in ("body", "html"):
                break
            lis = ancestor.find_all("li", class_=_CARD_LI)
            if lis:
                if len(lis) <= 50:
                    excluded.update(id(li) for li in lis)
                break  # arrêter qu'on marque ou non
            ancestor = ancestor.parent
    return excluded


def parse_deck_html(
    html: str,
    deck_id: str,
    *,
    min_cards: int = MIN_CARDS,
    log=None,
) -> Deck | None:
    """Retourne None si la page est incomplète (decklist tronquée ou absente).

    `log` : callable optionnel appelé avec une chaîne d'explication si le deck est rejeté.
    """
    soup = BeautifulSoup(html, "html.parser")

    commander: str | None = None
    deck_type = ""
    for meta in soup.find_all("meta", property="og:title"):
        content = meta.get("content", "")
        m = re.search(r"Commander\s*[\(\[](.+?)[\)\]]", content)
        if m:
            # Moxfield utilise & pour DFC et partners — on normalise vers // pour les DFC
            commander = normalize_commander_sep(m.group(1).strip())
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

    excl = _excluded_li_ids(soup)
    decklist: dict[str, int] = {}
    _diag_no_name   = 0
    _diag_no_qty    = 0
    _diag_parse_err = 0
    _diag_qty_zero  = 0
    all_lis = soup.find_all("li", class_=_CARD_LI)
    for li in all_lis:
        if id(li) in excl:
            continue
        name_tag = li.find("a", class_=_CARD_LINK)
        qty_tag = li.find("div", style=_QTY_DIV)
        if not name_tag:
            _diag_no_name += 1
            continue
        if not qty_tag:
            _diag_no_qty += 1
            continue
        try:
            quantity = int(qty_tag.get_text(strip=True))
        except ValueError:
            _diag_parse_err += 1
            continue
        name = fix_card_name(name_tag.get_text(strip=True))
        if not name or not quantity:
            _diag_qty_zero += 1
            continue
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
        if log is not None:
            parts = [f"{len(all_lis)} <li> dans le DOM", f"{len(excl)} exclus"]
            if _diag_no_name:
                parts.append(f"{_diag_no_name} sans lien-nom")
            if _diag_no_qty:
                parts.append(f"{_diag_no_qty} sans div-qté")
            if _diag_parse_err:
                parts.append(f"{_diag_parse_err} qté non-entière")
            if _diag_qty_zero:
                parts.append(f"{_diag_qty_zero} qté=0/nom vide")
            log(f"{len(decklist)} cartes trouvées (minimum {min_cards}) — {', '.join(parts)}")
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
