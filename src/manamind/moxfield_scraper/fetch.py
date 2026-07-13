"""Téléchargement du HTML des pages deck, en parallèle.

Rend le HTML au fil de l'eau (async generator) au lieu de l'écrire sur disque :
l'appelant peut parser et insérer en base sans jamais matérialiser de fichier.
"""

import asyncio
from collections.abc import AsyncIterator, Callable, Iterable

from playwright.async_api import (
    BrowserContext,
    TimeoutError as PlaywrightTimeout,
    async_playwright,
)

from .links import USER_AGENT

# Présent dès que la decklist est rendue — sert de signal "la page est prête".
READY_SELECTOR = "li[class*='decklist-card'], script[type='application/ld+json']"

Logger = Callable[[str], None]


async def _worker(
    context: BrowserContext,
    queue: asyncio.Queue,
    results: asyncio.Queue,
    page_wait_ms: int,
) -> None:
    """Une page Chromium réutilisée pour toute la file : ouvrir/fermer une page
    par deck coûte plus cher que la navigation elle-même."""
    page = await context.new_page()
    try:
        while True:
            try:
                deck_id = queue.get_nowait()
            except asyncio.QueueEmpty:
                return

            try:
                await page.goto(
                    f"https://moxfield.com/decks/{deck_id}",
                    timeout=30000,
                    wait_until="domcontentloaded",
                )
                try:
                    await page.wait_for_selector(READY_SELECTOR, timeout=page_wait_ms)
                except PlaywrightTimeout:
                    pass  # on parse quand même : le parser rejettera si incomplet
                await results.put((deck_id, await page.content()))
            except Exception as exc:
                await results.put((deck_id, None, str(exc)))
    finally:
        await page.close()


async def stream_deck_html(
    deck_ids: Iterable[str],
    *,
    concurrency: int = 7,
    page_wait_ms: int = 8000,
    headless: bool = True,
    log: Logger = print,
) -> AsyncIterator[tuple[str, str]]:
    """Produit (deck_id, html) dans l'ordre d'arrivée. Les échecs sont journalisés
    puis ignorés — un deck manquant ne doit pas interrompre un run de 100 000."""
    ids = list(deck_ids)
    if not ids:
        return

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=headless)
        context = await browser.new_context(user_agent=USER_AGENT)
        try:
            queue: asyncio.Queue = asyncio.Queue()
            results: asyncio.Queue = asyncio.Queue()
            for deck_id in ids:
                queue.put_nowait(deck_id)

            workers = [
                asyncio.create_task(_worker(context, queue, results, page_wait_ms))
                for _ in range(min(concurrency, len(ids)))
            ]

            failed = 0
            for _ in range(len(ids)):
                item = await results.get()
                if len(item) == 3:
                    failed += 1
                    log(f"échec {item[0]} : {item[2]}")
                    continue
                yield item

            await asyncio.gather(*workers)
            if failed:
                log(f"{failed}/{len(ids)} pages non téléchargées")
        finally:
            await browser.close()
