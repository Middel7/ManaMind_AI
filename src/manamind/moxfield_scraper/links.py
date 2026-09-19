"""Collecte des identifiants de decks sur moxfield.com/decks/public.

Fusionne les deux modes de l'ancien projet : liste des decks récents (tous
commandants) et liste filtrée sur un commandant précis. Retourne des deck_id,
n'écrit rien sur disque — la persistance est la responsabilité de l'appelant.
"""

import re
import time
from collections.abc import Callable

from playwright.sync_api import Page, sync_playwright

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

BASE_URL = "https://moxfield.com/decks/public"
LIST_URL = f"{BASE_URL}?fmt=commander&sortType=updated&sortDirection=Descending"

DECK_ID = re.compile(r"/decks/([a-zA-Z0-9_-]{22})")
_DFC_SEP     = re.compile(r"\s*//\s*")   # double face → un seul champ Commander
_PARTNER_SEP = re.compile(r"\s*&\s*")    # partners / Background → Commander + Partner

# Moxfield charge le CMP "ncmp", dont la bannière se superpose à la page et
# avale les clics sur "View more". Il faut la fermer avant toute interaction.
CONSENT_SELECTORS = [
    # ncmp (bannière simple)
    "button.ncmp__btn:has-text('Accept')",
    "#ncmp__tool button:has-text('Accept')",
    # Sourcepoint IAB CMP v3 (privacy dashboard complet)
    "button.sp-cc-accept",
    "button[title='Accept All']",
    "[data-sp-action='accept']",
    ".sp_choice_type_11",
    # Générique — couvre la plupart des CMP
    "button:has-text('Accept All')",
    "button:has-text('Accept all')",
    "button:has-text('I Accept')",
    "button:has-text('Consent')",
    "button:has-text('Agree')",
]

Logger = Callable[[str], None]


def split_commander(commander: str) -> tuple[str, str | None, bool]:
    """Décompose un nom de commandant en (face_principale, face2_ou_partner, is_dfc).

    '// ' → DFC  : is_dfc=True  → remplir uniquement le champ Commander.
    ' & ' → Partners/Background : is_dfc=False → remplir Commander + Partner.
    """
    if "//" in commander:
        parts = _DFC_SEP.split(commander, maxsplit=1)
        return parts[0].strip(), parts[1].strip() if len(parts) > 1 else None, True
    if "&" in commander:
        parts = _PARTNER_SEP.split(commander, maxsplit=1)
        return parts[0].strip(), parts[1].strip() if len(parts) > 1 else None, False
    return commander.strip(), None, False


# Compatibilité ascendante pour les appels existants hors filtre
def split_partners(commander: str) -> tuple[str, str | None]:
    main, partner, _ = split_commander(commander)
    return main, partner


def _extract_ids(page: Page) -> set[str]:
    return set(DECK_ID.findall(page.content()))


def _dismiss_consent(page: Page, log: Logger) -> None:
    for selector in CONSENT_SELECTORS:
        try:
            btn = page.locator(selector).first
            if btn.count() > 0 and btn.is_visible(timeout=3000):
                btn.click(timeout=5000)
                page.wait_for_timeout(800)
                log("bannière de consentement acceptée")
                return
        except Exception:
            continue

    # Si le CMP est dans un <iframe> (cas Sourcepoint privacy dashboard), essayer dans le frame.
    try:
        for frame in page.frames[1:]:  # ignorer le frame principal
            for sel in CONSENT_SELECTORS:
                try:
                    btn = frame.locator(sel).first
                    if btn.count() > 0 and btn.is_visible(timeout=1500):
                        btn.click(timeout=3000)
                        page.wait_for_timeout(800)
                        log("bannière de consentement acceptée (iframe)")
                        return
                except Exception:
                    continue
    except Exception:
        pass

    # Le clic a pu être intercepté : on retire la bannière du DOM.
    try:
        removed = page.evaluate(
            """() => {
                let hit = false;
                // ncmp (bannière simple Moxfield)
                for (const id of ['ncmp__tool', 'ncmp__banner']) {
                    const el = document.getElementById(id);
                    if (el) { el.remove(); hit = true; }
                }
                document.querySelectorAll('.ncmp__banner, .ncmp__banner-inner, .ncmp__normalise')
                    .forEach(el => { el.remove(); hit = true; });
                // Sourcepoint IAB CMP v3 (privacy dashboard complet)
                document.querySelectorAll(
                    '[id^="sp_message_container"], .sp-cdn, #sp_cloud_wrapper, ' +
                    '.message-overlay, #sp_privacy_manager, [class*="sp-message"]'
                ).forEach(el => { el.remove(); hit = true; });
                // Supprimer le scroll-lock et le backdrop si présents
                document.body.style.overflow = '';
                document.querySelectorAll('.sp_overlay, .sp-backdrop, .message-overlay-backdrop')
                    .forEach(el => { el.remove(); });
                return hit;
            }"""
        )
        if removed:
            log("bannière de consentement retirée du DOM")
    except Exception:
        pass


def _scroll_and_collect(page: Page, seen: set[str]) -> int:
    """Scrolle la page de haut en bas en accumulant les ids croisés au passage.

    Indispensable : la liste est virtualisée, les decks hors écran sont retirés
    du DOM. Une extraction unique ne verrait que la fenêtre visible et perdrait
    silencieusement la majorité des résultats.
    """
    before = len(seen)
    seen |= _extract_ids(page)

    pos = 0
    height = page.evaluate("() => document.body.scrollHeight")
    while pos < height:
        page.evaluate(f"() => window.scrollTo(0, {pos})")
        page.wait_for_timeout(120)
        seen |= _extract_ids(page)
        pos += 800
        height = page.evaluate("() => document.body.scrollHeight")

    # Redescendre pour garder "View more" atteignable.
    page.evaluate("() => window.scrollTo(0, document.body.scrollHeight)")
    page.wait_for_timeout(200)
    seen |= _extract_ids(page)
    return len(seen) - before


def _fill_card_field(page: Page, field_id: str, card_name: str) -> None:
    """Remplit un champ carte via l'autocomplétion Moxfield et sélectionne la première suggestion."""
    page.wait_for_selector(f"#{field_id}", timeout=10000)
    page.fill(f"#{field_id}", card_name)
    page.wait_for_timeout(2000)
    suggestion = page.locator(
        f"xpath=//input[@id='{field_id}']/following-sibling::*//li[1]"
        f" | //input[@id='{field_id}']/..//li[1]"
        f" | //input[@id='{field_id}']/../..//li[1]"
    ).first
    try:
        suggestion.wait_for(state="visible", timeout=3000)
        suggestion.click()
    except Exception:
        page.click(f"text={card_name}")
    page.wait_for_timeout(1000)


def _apply_commander_filter(
    page: Page, commander: str, partner: str | None, is_dfc: bool, log: Logger
) -> None:
    """Passe par l'UI "More Filters" : les paramètres d'URL seuls ne suffisent pas,
    Moxfield attend l'id interne de la carte, pas son nom.

    is_dfc=True  → commandant double-face : remplir uniquement le champ Commander
                   (l'autocomplete sélectionne les deux faces d'un coup).
    is_dfc=False → partners / Background : remplir Commander puis Partner.
    """
    _dismiss_consent(page, log)
    page.evaluate("() => window.scrollTo(0, 0)")
    page.wait_for_timeout(500)

    for label in ("Filters", "More Filters"):
        btn = page.locator(f"button.btn-outline-primary:has-text('{label}')").first
        if btn.count() > 0:
            btn.click(timeout=5000)
            break
    else:
        raise RuntimeError("Bouton Filters introuvable sur la page")
    page.wait_for_timeout(1500)

    _fill_card_field(page, "commanderCardId", commander)
    if partner and not is_dfc:
        _fill_card_field(page, "partnerCardId", partner)

    page.wait_for_timeout(1500)
    page.click("button.btn-primary:has-text('Save Filters')")
    page.wait_for_timeout(2000)
    sep = " // " if is_dfc else " & "
    log(f"filtre appliqué : {commander}" + (f"{sep}{partner}" if partner else ""))


def collect_deck_ids(
    *,
    limit: int,
    commander: str | None = None,
    headless: bool = True,
    known: set[str] | None = None,
    log: Logger = print,
) -> list[str]:
    """Collecte jusqu'à `limit` deck_id, en excluant ceux de `known`.

    `known` sert à ne pas re-télécharger ce que la base contient déjà : les ids
    connus comptent quand même dans l'objectif atteint, mais ne sont pas renvoyés.
    """
    known = known or set()
    seen: set[str] = set()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        context = browser.new_context(user_agent=USER_AGENT)
        page = context.new_page()

        try:
            page.goto(BASE_URL, timeout=60000)
            page.wait_for_timeout(3000)
            _dismiss_consent(page, log)

            page.goto(LIST_URL, timeout=60000)
            page.wait_for_timeout(4000)
            _dismiss_consent(page, log)  # elle peut réapparaître après navigation

            if commander:
                cmd, partner, is_dfc = split_commander(commander)
                _apply_commander_filter(page, cmd, partner, is_dfc, log)
                page.wait_for_timeout(2000)

            clicks = 0
            stagnant = 0
            failures = 0

            while True:
                added = _scroll_and_collect(page, seen)
                count = len(seen)
                log(f"view-more={clicks:<4} decks={count}/{limit}")

                if count >= limit:
                    break

                # Fin réelle de liste : plusieurs cycles sans aucun nouvel id.
                if added == 0:
                    stagnant += 1
                    if stagnant >= 3:
                        log(f"fin de liste atteinte ({count} decks)")
                        break
                else:
                    stagnant = 0

                try:
                    btn = page.locator("button:has-text('View more')").first
                    if btn.count() == 0 or not btn.is_visible(timeout=5000):
                        log(f"bouton 'View more' absent — fin de liste ({count} decks)")
                        break

                    btn.scroll_into_view_if_needed(timeout=3000)
                    try:
                        btn.click(timeout=5000)
                    except Exception:
                        _dismiss_consent(page, log)
                        btn.evaluate("el => el.click()")

                    try:
                        page.wait_for_load_state("networkidle", timeout=8000)
                    except Exception:
                        page.wait_for_timeout(1000)

                    clicks += 1
                    failures = 0
                except Exception as exc:
                    failures += 1
                    log(f"échec du clic 'View more' ({failures}/3) : {exc}")
                    if failures >= 3:
                        break
                    _dismiss_consent(page, log)
                    time.sleep(2)

            _scroll_and_collect(page, seen)
        finally:
            browser.close()

    fresh = sorted(seen - known)
    log(f"{len(seen)} decks vus, {len(fresh)} nouveaux (hors base)")
    return fresh[:limit]
