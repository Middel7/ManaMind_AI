from decimal import Decimal

from manamind.moxfield_scraper.parser import fix_card_name, parse_deck_html


def _page(cards: list[tuple[str, int]], *, commander="The Ur-Dragon", extras="") -> str:
    lis = "".join(
        f'<li class="decklist-card XIi4jFys2lGhYwseGpBo">'
        f'<div style="width: 20px">{qty}</div>'
        f'<a class="table-deck-row-link" href="#">{name}</a></li>'
        for name, qty in cards
    )
    return f"""
    <html><head>
      <meta property="og:title" content="Dragons cEDH — Commander ({commander})">
      <script type="application/ld+json">
        {{"datePublished": "2024-03-01T10:00:00Z", "dateModified": "2025-06-12T08:30:00Z"}}
      </script>
    </head><body><ul>{lis}</ul>{extras}</body></html>
    """


def _basic_cards(n=80):
    return [(f"Card {i}", 1) for i in range(n)]


def test_parse_extrait_les_metadonnees():
    deck = parse_deck_html(_page(_basic_cards()), "abc123")

    assert deck is not None
    assert deck.deck_id == "abc123"
    assert deck.commander == "The Ur-Dragon"
    assert deck.deck_type == "CEDH"
    assert deck.date_created.year == 2024
    assert deck.date_modified.month == 6


def test_le_commandant_est_ajoute_et_marque():
    deck = parse_deck_html(_page(_basic_cards()), "abc123")

    commanders = [c for c in deck.cards if c.is_commander]
    assert [c.name for c in commanders] == ["The Ur-Dragon"]


def test_partenaires_les_deux_sont_commandants():
    html = _page(_basic_cards(), commander="Krark, the Thumbless // Sakashima of a Thousand Faces")
    deck = parse_deck_html(html, "abc123")

    names = {c.name for c in deck.cards if c.is_commander}
    assert names == {"Krark, the Thumbless", "Sakashima of a Thousand Faces"}


def test_deck_incomplet_rejete():
    assert parse_deck_html(_page(_basic_cards(10)), "abc123") is None


def test_bracket_et_prix():
    extras = (
        '<div class="d-inline-block text-nowrap cursor-pointer">Bracket 4</div>'
        '<span class="cursor-pointer ms-4">$1,234.56</span>'
    )
    deck = parse_deck_html(_page(_basic_cards(), extras=extras), "abc123")

    assert deck.bracket == 4
    assert deck.price == Decimal("1234.56")
    assert deck.currency == "$"


def test_fix_card_name_restaure_les_espaces():
    assert fix_card_name("LightningBolt") == "Lightning Bolt"
    assert fix_card_name("Y'shtola,Night's Blessed") == "Y'shtola, Night's Blessed"
