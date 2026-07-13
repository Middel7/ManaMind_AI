"""Orchestration : collecte -> téléchargement -> parsing -> base.

C'est le seul module qui fait se rencontrer Moxfield et la base. Aucun fichier
intermédiaire : le HTML transite en mémoire, sauf si `html_cache` est fourni —
auquel cas les pages sont aussi écrites sur disque, ce qui permet de rejouer un
parsing (après un changement de markup Moxfield) sans re-scraper.
"""

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy.engine import Engine

from . import db
from .fetch import stream_deck_html
from .links import collect_deck_ids
from .models import Deck
from .parser import parse_deck_html

Logger = Callable[[str], None]


@dataclass
class ScrapeStats:
    seen: int = 0        # URLs collectées sur Moxfield
    skipped: int = 0     # URLs ignorées car deck déjà en base (skip_known=True)
    fetched: int = 0     # pages HTML téléchargées et parsées
    incomplete: int = 0  # pages rejetées par le parser (decklist tronquée)
    created: int = 0     # decks créés en base (nouveaux)
    updated: int = 0     # decks mis à jour en base (re-scrapés avec --refresh)

    @property
    def saved(self) -> int:
        return self.created + self.updated

    def __str__(self) -> str:
        return (
            f"{self.seen} URLs | {self.skipped} ignorés | {self.fetched} HTML parsés | "
            f"{self.incomplete} incomplets | "
            f"{self.created} créés | {self.updated} mis à jour"
        )


async def _consume(
    engine: Engine,
    deck_ids: list[str],
    stats: ScrapeStats,
    *,
    concurrency: int,
    page_wait_ms: int,
    headless: bool,
    batch_size: int,
    html_cache: Path | None,
    log: Logger,
) -> None:
    buffer: list[Deck] = []

    async def flush() -> None:
        if not buffer:
            return
        # to_thread : l'écriture SQL est bloquante, on ne veut pas geler les
        # workers Playwright pendant la transaction.
        c, u = await asyncio.to_thread(db.upsert_decks, engine, list(buffer))
        stats.created += c
        stats.updated += u
        buffer.clear()
        log(f"  {stats.saved}/{len(deck_ids)} decks enregistrés")

    async for deck_id, html in stream_deck_html(
        deck_ids,
        concurrency=concurrency,
        page_wait_ms=page_wait_ms,
        headless=headless,
        log=lambda m: log(f"  {m}"),
    ):
        stats.fetched += 1

        if html_cache:
            html_cache.mkdir(parents=True, exist_ok=True)
            (html_cache / f"{deck_id}.html").write_text(html, encoding="utf-8")

        deck = parse_deck_html(html, deck_id)
        if deck is None:
            stats.incomplete += 1
            continue

        buffer.append(deck)
        if len(buffer) >= batch_size:
            await flush()

    await flush()


def scrape(
    engine: Engine,
    *,
    limit: int,
    commander: str | None = None,
    concurrency: int = 7,
    page_wait_ms: int = 8000,
    headless: bool = True,
    batch_size: int = 50,
    html_cache: Path | None = None,
    skip_known: bool = True,
    log: Logger = print,
) -> ScrapeStats:
    """Scrape `limit` decks (filtrés sur `commander` si fourni) et les écrit en base.

    `skip_known=True` : les decks déjà présents en base ne sont pas retéléchargés.
    Le passer à False force un rafraîchissement (prix, bracket, decklist modifiée).
    """
    stats = ScrapeStats()

    log(f"Collecte des liens{f' — {commander}' if commander else ''} (objectif {limit})")
    candidates = collect_deck_ids(
        limit=limit,
        commander=commander,
        headless=headless,
        log=lambda m: log(f"  {m}"),
    )
    stats.seen = len(candidates)

    if skip_known:
        known = db.known_deck_ids(engine, candidates)
        stats.skipped = len(known)
        candidates = [d for d in candidates if d not in known]
        if known:
            log(f"  {len(known)} decks déjà en base — ignorés")

    if not candidates:
        log("Rien de neuf à télécharger.")
        return stats

    log(f"Téléchargement de {len(candidates)} decks ({concurrency} pages en parallèle)")
    asyncio.run(
        _consume(
            engine,
            candidates,
            stats,
            concurrency=concurrency,
            page_wait_ms=page_wait_ms,
            headless=headless,
            batch_size=batch_size,
            html_cache=html_cache,
            log=log,
        )
    )

    log(str(stats))
    return stats


def reparse_cache(
    engine: Engine,
    html_cache: Path,
    *,
    batch_size: int = 200,
    log: Logger = print,
) -> ScrapeStats:
    """Re-parse un cache HTML et met la base à jour, sans toucher au réseau.
    À utiliser après avoir corrigé le parser suite à un changement de markup."""
    stats = ScrapeStats()
    buffer: list[Deck] = []

    files = sorted(html_cache.glob("*.html"))
    log(f"{len(files)} pages en cache à re-parser")

    for path in files:
        stats.fetched += 1
        deck = parse_deck_html(path.read_text(encoding="utf-8"), path.stem)
        if deck is None:
            stats.incomplete += 1
            continue
        buffer.append(deck)
        if len(buffer) >= batch_size:
            c, u = db.upsert_decks(engine, buffer)
            stats.created += c; stats.updated += u
            buffer.clear()

    if buffer:
        c, u = db.upsert_decks(engine, buffer)
        stats.created += c; stats.updated += u

    log(str(stats))
    return stats
