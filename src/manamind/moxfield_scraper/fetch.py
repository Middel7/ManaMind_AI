"""Téléchargement du HTML des pages deck, en parallèle.

Rend le HTML au fil de l'eau (async generator) au lieu de l'écrire sur disque :
l'appelant peut parser et insérer en base sans jamais matérialiser de fichier.
"""

import asyncio
import time as _time
from collections.abc import AsyncIterator, Callable, Iterable

from playwright.async_api import (
    BrowserContext,
    TimeoutError as PlaywrightTimeout,
    async_playwright,
)

from .links import CONSENT_SELECTORS, USER_AGENT

# On attend la première carte réellement rendue par React — ne pas inclure
# script[type='application/ld+json'] ici : ce tag apparaît quasi-instantanément
# (avant le rendu React) et causerait une capture prématurée avec seulement 0–3 cartes.
READY_SELECTOR = "li[class*='decklist-card']"

Logger = Callable[[str], None]

# Sélecteur du bouton "Accept" de la CMP Sourcepoint (présent sur les pages de deck)
_CMP_ACCEPT = ", ".join([
    "button.sp-cc-accept",
    "button[title='Accept All']",
    "[data-sp-action='accept']",
    ".sp_choice_type_11",
    "button:has-text('Accept')",
    "button:has-text('Accept All')",
])


async def _dismiss_consent_async(page, *, quick: bool = False) -> bool:
    """Ferme la bannière CMP sur une page de deck (version async).

    `quick=True` : premier check limité à 500 ms pour les navigations courantes
    où la CMP est absente. Retourne True si la CMP a été trouvée et fermée.
    """
    first_timeout = 500 if quick else 1500
    try:
        btn = page.locator(_CMP_ACCEPT).first
        if await btn.count() > 0 and await btn.is_visible(timeout=first_timeout):
            await btn.click(timeout=3000)
            await page.wait_for_timeout(600)
            return True
    except Exception:
        pass

    # Tentative dans les iframes (Sourcepoint privacy dashboard)
    try:
        for frame in page.frames[1:]:
            try:
                btn = frame.locator(_CMP_ACCEPT).first
                if await btn.count() > 0 and await btn.is_visible(timeout=800):
                    await btn.click(timeout=2000)
                    await page.wait_for_timeout(600)
                    return True
            except Exception:
                continue
    except Exception:
        pass

    # Retrait direct du DOM si le clic a échoué
    try:
        await page.evaluate("""() => {
            for (const id of ['ncmp__tool', 'ncmp__banner']) {
                const el = document.getElementById(id);
                if (el) el.remove();
            }
            document.querySelectorAll(
                '[id^="sp_message_container"], .sp-cdn, #sp_cloud_wrapper, ' +
                '.message-overlay, [class*="sp-message"]'
            ).forEach(el => el.remove());
            document.body.style.overflow = '';
        }""")
    except Exception:
        pass
    return False


async def _download_page(page, deck_id: str, page_wait_ms: int) -> str:
    """Charge la page du deck et retourne son HTML. Raise si le contenu n'est pas prêt."""
    # "commit" retourne dès que le serveur répond (~0.5 s), sans attendre
    # l'exécution des scripts JS différés (DOMContentLoaded peut prendre 7+ s
    # sur Moxfield). wait_for_selector prend ensuite le relais pour détecter
    # le moment où le contenu React est réellement prêt.
    await page.goto(
        f"https://moxfield.com/decks/{deck_id}",
        timeout=45000,
        wait_until="commit",
    )
    await _dismiss_consent_async(page, quick=True)
    try:
        await page.wait_for_selector(READY_SELECTOR, timeout=page_wait_ms)
        # Courte attente de stabilisation : React peut rendre les cartes en plusieurs
        # micro-batchs après que le premier <li> apparaît. 300 ms suffisent
        # généralement pour que l'ensemble de la decklist soit dans le DOM.
        await page.wait_for_timeout(300)
    except PlaywrightTimeout:
        # La CMP bloque encore — tentative complète de fermeture
        await _dismiss_consent_async(page, quick=False)
        try:
            await page.wait_for_selector(READY_SELECTOR, timeout=3000)
            await page.wait_for_timeout(300)
        except PlaywrightTimeout:
            pass  # le parser rejettera si incomplet
    return await page.content()


async def _worker(
    context: BrowserContext,
    queue: asyncio.Queue,
    results: asyncio.Queue,
    page_wait_ms: int,
    stop_event,          # threading.Event | None — vérifié avant chaque téléchargement
) -> None:
    """Une page Chromium réutilisée pour toute la file."""
    try:
        page = await context.new_page()
    except Exception as exc:
        try:
            deck_id = queue.get_nowait()
            await results.put((deck_id, None, f"new_page failed: {exc}"))
        except asyncio.QueueEmpty:
            pass
        return

    # Timeout dur asyncio par page : indépendant de l'état du browser.
    # Garantit que si Playwright se bloque (browser mort, connexion coupée),
    # le worker produit quand même un résultat dans les délais impartis.
    page_timeout = page_wait_ms / 1000 + 50

    try:
        while True:
            try:
                deck_id = queue.get_nowait()
            except asyncio.QueueEmpty:
                return

            # Arrêt propre : on draine la queue sans télécharger
            if stop_event is not None and stop_event.is_set():
                await results.put((deck_id, None, "arrêt demandé"))
                continue

            try:
                html = await asyncio.wait_for(
                    _download_page(page, deck_id, page_wait_ms),
                    timeout=page_timeout,
                )
                await results.put((deck_id, html))
            except asyncio.TimeoutError:
                await results.put((deck_id, None, f"timeout asyncio ({page_timeout:.0f}s) — browser bloqué"))
            except Exception as exc:
                await results.put((deck_id, None, str(exc)))
    finally:
        try:
            await asyncio.wait_for(page.close(), timeout=5.0)
        except Exception:
            pass


async def stream_deck_html(
    deck_ids: Iterable[str],
    *,
    concurrency: int = 7,
    page_wait_ms: int = 5000,
    headless: bool = True,
    log: Logger = print,
    stop_event=None,     # threading.Event | None — propagé aux workers
) -> AsyncIterator[tuple[str, str]]:
    """Produit (deck_id, html) dans l'ordre d'arrivée. Les échecs sont journalisés
    puis ignorés — un deck manquant ne doit pas interrompre un run de 100 000.

    Le browser est fermé dès que tous les workers ont terminé, avant de livrer
    les résultats au consumer. Cela évite que browser.close() (~3-4s sur Windows)
    allonge inutilement la durée visible des onglets.
    """
    ids = list(deck_ids)
    if not ids:
        return

    collected: list[tuple[str, str]] = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=headless)
        context = await browser.new_context(user_agent=USER_AGENT)
        try:
            queue: asyncio.Queue = asyncio.Queue()
            results: asyncio.Queue = asyncio.Queue()
            for deck_id in ids:
                queue.put_nowait(deck_id)

            workers = [
                asyncio.create_task(_worker(context, queue, results, page_wait_ms, stop_event))
                for _ in range(min(concurrency, len(ids)))
            ]

            failed = 0
            done = 0
            total = len(ids)
            # Timeout par item = page_wait_ms + marge réseau (45s goto + 5s buffer)
            item_timeout = (page_wait_ms / 1000) + 55

            # Si aucun résultat n'arrive pendant 2 × item_timeout consécutifs,
            # les workers sont bloqués (browser mort, réseau coupé) → abandon forcé.
            last_result_at = _time.monotonic()
            stall_limit = item_timeout * 2

            for _ in range(total):
                try:
                    item = await asyncio.wait_for(results.get(), timeout=item_timeout)
                    last_result_at = _time.monotonic()
                except asyncio.TimeoutError:
                    failed += 1
                    log(f"timeout global ({item_timeout:.0f}s) — worker probablement bloqué ({done}/{total} faits)")
                    if all(w.done() for w in workers):
                        log("tous les workers sont terminés prématurément — arrêt")
                        break
                    stalled = _time.monotonic() - last_result_at
                    if stalled > stall_limit:
                        log(f"ABANDON : {stalled:.0f}s sans résultat — annulation forcée des workers")
                        for w in workers:
                            w.cancel()
                        await asyncio.gather(*workers, return_exceptions=True)
                        break
                    continue

                done += 1
                if len(item) == 3:
                    failed += 1
                    log(f"échec {item[0]} : {item[2]}")
                    continue
                log(f"page {done}/{total} téléchargée")
                collected.append(item)

            await asyncio.gather(*workers, return_exceptions=True)
            if failed:
                log(f"{failed}/{total} pages non téléchargées")
        finally:
            try:
                await asyncio.wait_for(browser.close(), timeout=10.0)
            except Exception:
                pass

    # Browser fermé — on livre les résultats au consumer (parse + DB)
    for item in collected:
        yield item
