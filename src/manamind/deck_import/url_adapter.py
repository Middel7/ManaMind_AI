"""
Adaptateurs d'import par URL publique.
Protection SSRF intégrée : liste blanche de domaines, blocage des adresses privées.
"""

from __future__ import annotations

import ipaddress
import logging
import re
import socket
from abc import ABC, abstractmethod
from dataclasses import dataclass
from urllib.parse import urlparse

from .models import CanonicalDeckImport, ImportSource
from .parsers.registry import parse

log = logging.getLogger(__name__)

# ── Configuration sécurité ────────────────────────────────────────────────────

_REQUEST_TIMEOUT = 10  # secondes
_MAX_REDIRECTS = 3
_MAX_RESPONSE_BYTES = 5 * 1024 * 1024  # 5 Mo

# Réseaux privés bloqués (protection SSRF)
_PRIVATE_NETWORKS = [
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("169.254.0.0/16"),  # link-local / AWS metadata
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),
    ipaddress.ip_network("fe80::/10"),
]


def _is_private_ip(hostname: str) -> bool:
    """Vérifie si un hostname résout vers une IP privée."""
    try:
        addr = socket.getaddrinfo(hostname, None)[0][4][0]
        ip = ipaddress.ip_address(addr)
        return any(ip in net for net in _PRIVATE_NETWORKS)
    except (socket.gaierror, ValueError):
        return True  # En cas de doute, bloquer


def _validate_url(url: str) -> tuple[bool, str]:
    """Valide l'URL avant d'émettre la requête. Retourne (ok, erreur)."""
    try:
        parsed = urlparse(url)
    except Exception:
        return False, "Invalid URL"

    if parsed.scheme not in ("http", "https"):
        return False, "Only http/https URLs are allowed"

    hostname = parsed.hostname or ""
    if not hostname:
        return False, "No hostname in URL"

    # Bloquer localhost et équivalents
    if hostname in ("localhost", "127.0.0.1", "::1", "0.0.0.0"):
        return False, "Requests to localhost are not allowed"

    # Bloquer les métadonnées cloud (169.254.169.254)
    if hostname == "169.254.169.254":
        return False, "Requests to cloud metadata endpoint are not allowed"

    if _is_private_ip(hostname):
        return False, f"Requests to private/internal addresses are not allowed ({hostname})"

    return True, ""


@dataclass
class UrlImportResult:
    ok: bool
    deck: CanonicalDeckImport | None = None
    error: str | None = None
    fallback_message: str | None = None


class UrlAdapter(ABC):
    """Interface commune pour les adaptateurs d'import par URL."""

    name: str = "unknown"
    domains: list[str] = []
    available: bool = True  # False = pas d'API fiable, fallback texte

    @abstractmethod
    def can_handle(self, url: str) -> bool:
        ...

    @abstractmethod
    def fetch(self, url: str) -> UrlImportResult:
        ...

    def _unavailable(self, url: str) -> UrlImportResult:
        return UrlImportResult(
            ok=False,
            error=f"Import automatique depuis {self.name} non disponible.",
            fallback_message=(
                f"L'import direct depuis {self.name} n'est pas pris en charge. "
                "Veuillez exporter votre decklist au format texte depuis le site, "
                "puis collez-la dans l'onglet 'Coller une liste'."
            ),
        )


class MoxfieldAdapter(UrlAdapter):
    """Adaptateur Moxfield via API officielle."""

    name = "Moxfield"
    domains = ["moxfield.com", "www.moxfield.com"]
    available = True

    _RE_ID = re.compile(r"moxfield\.com/decks/([A-Za-z0-9_\-]+)")

    def can_handle(self, url: str) -> bool:
        return bool(self._RE_ID.search(url))

    def fetch(self, url: str) -> UrlImportResult:
        m = self._RE_ID.search(url)
        if not m:
            return UrlImportResult(ok=False, error="Could not extract Moxfield deck ID from URL")

        deck_id = m.group(1)
        api_url = f"https://api2.moxfield.com/v3/decks/all/{deck_id}"

        ok, err = _validate_url(api_url)
        if not ok:
            return UrlImportResult(ok=False, error=err)

        try:
            import httpx

            resp = httpx.get(
                api_url,
                timeout=_REQUEST_TIMEOUT,
                follow_redirects=True,
                headers={"User-Agent": "ManaMind/1.0 deck-import"},
            )
        except Exception as exc:
            return UrlImportResult(ok=False, error=f"HTTP error: {exc}")

        if resp.status_code == 404:
            return UrlImportResult(ok=False, error="Deck not found or deleted")
        if resp.status_code == 403:
            return UrlImportResult(
                ok=False,
                error="Deck is private",
                fallback_message=(
                    "Ce deck est privé. Rendez-le public sur Moxfield ou exportez "
                    "la decklist au format texte et collez-la."
                ),
            )
        if resp.status_code != 200:
            return UrlImportResult(ok=False, error=f"HTTP {resp.status_code} from Moxfield API")

        try:
            data = resp.json()
        except Exception:
            return UrlImportResult(ok=False, error="Invalid JSON from Moxfield API")

        # Conversion en format texte → pipeline de parsing
        text_lines = _moxfield_json_to_text(data)
        deck = parse("\n".join(text_lines))
        deck.source = ImportSource.MOXFIELD
        deck.source_format = "Moxfield (API)"
        return UrlImportResult(ok=True, deck=deck)


def _moxfield_json_to_text(data: dict) -> list[str]:
    """Convertit la réponse JSON Moxfield en texte structuré parseable."""
    lines: list[str] = []

    deck_name = data.get("name", "")
    if deck_name:
        lines.append(f"// {deck_name}")

    def _emit_zone(zone_data: dict | None, zone_label: str) -> None:
        if not zone_data:
            return
        cards = zone_data if isinstance(zone_data, dict) else {}
        if not cards:
            return
        lines.append(zone_label)
        for card_name, card_info in cards.items():
            qty = card_info.get("quantity", 1)
            printing = card_info.get("card", {})
            set_code = printing.get("set", "")
            col_num = printing.get("collector_number", "")
            if set_code and col_num:
                lines.append(f"{qty} {card_name} ({set_code.upper()}) {col_num}")
            else:
                lines.append(f"{qty} {card_name}")

    commanders = data.get("commanders", {})
    if commanders:
        lines.append("Commander")
        for card_name, card_info in commanders.items():
            qty = card_info.get("quantity", 1)
            printing = card_info.get("card", {})
            set_code = printing.get("set", "")
            col_num = printing.get("collector_number", "")
            if set_code and col_num:
                lines.append(f"{qty} {card_name} ({set_code.upper()}) {col_num}")
            else:
                lines.append(f"{qty} {card_name}")

    _emit_zone(data.get("mainboard"), "Deck")
    _emit_zone(data.get("sideboard"), "Sideboard")
    _emit_zone(data.get("maybeboard"), "Maybeboard")
    _emit_zone(data.get("companions"), "Companion")

    return lines


class UnavailableAdapter(UrlAdapter):
    """Adaptateur générique pour les sources sans API fiable."""

    available = False

    def __init__(self, name: str, domains: list[str]) -> None:
        self.name = name
        self.domains = domains

    def can_handle(self, url: str) -> bool:
        return any(d in url for d in self.domains)

    def fetch(self, url: str) -> UrlImportResult:
        return self._unavailable(url)


# ── Registre des adaptateurs ─────────────────────────────────────────────────

_ADAPTERS: list[UrlAdapter] = [
    MoxfieldAdapter(),
    # Sources sans API officielle fiable → fallback texte
    UnavailableAdapter("Archidekt", ["archidekt.com"]),
    UnavailableAdapter("ManaBox", ["manabox.app"]),
    UnavailableAdapter("Deckstats", ["deckstats.net"]),
    UnavailableAdapter("TappedOut", ["tappedout.net"]),
    UnavailableAdapter("Aetherhub", ["aetherhub.com"]),
    UnavailableAdapter("MTGTop8", ["mtgtop8.com"]),
    UnavailableAdapter("Scryfall", ["scryfall.com"]),
    UnavailableAdapter("TCGplayer", ["tcgplayer.com"]),
    UnavailableAdapter("Untapped.gg", ["untapped.gg"]),
]


def import_from_url(url: str) -> UrlImportResult:
    """Sélectionne l'adaptateur et importe une decklist depuis une URL."""
    ok, err = _validate_url(url)
    if not ok:
        return UrlImportResult(ok=False, error=err)

    for adapter in _ADAPTERS:
        if adapter.can_handle(url):
            log.info("URL import: %s via adapter %s", url, adapter.name)
            return adapter.fetch(url)

    return UrlImportResult(
        ok=False,
        error="Unknown URL source",
        fallback_message=(
            "Ce site n'est pas pris en charge pour l'import automatique. "
            "Exportez votre decklist au format texte et collez-la."
        ),
    )
