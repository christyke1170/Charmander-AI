"""Pokémon GO Wiki/Fandom knowledge cache update entrypoint."""

from __future__ import annotations

import argparse
import logging
from pathlib import Path
from typing import Any

from config import BASE_DIR, configure_logging
from database.cache_metadata import init_cache_metadata_table, update_cache_metadata
from database.db import init_db
from database.wiki_knowledge_db import (
    SOURCE_NAME,
    clear_wiki_knowledge_for_source,
    init_wiki_knowledge_tables,
    upsert_wiki_chunks,
    upsert_wiki_pages,
)
from scraper.pokemon_go_wiki_scraper import scrape_wiki_pages


logger = logging.getLogger(__name__)

CACHE_NAME = "wiki_knowledge"
DEFAULT_SEED_PATH = BASE_DIR / "data" / "wiki_pages_seed.txt"


def read_seed_page_titles(seed_path: str | Path = DEFAULT_SEED_PATH) -> list[str]:
    """Read wiki page titles from a newline-delimited seed file."""

    path = Path(seed_path)
    if not path.is_absolute():
        path = BASE_DIR / path
    if not path.exists():
        logger.warning("Wiki seed file not found: %s", path)
        return []
    titles: list[str] = []
    seen: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        title = line.strip()
        if not title or title.startswith("#"):
            continue
        key = title.casefold()
        if key in seen:
            continue
        seen.add(key)
        titles.append(title)
    return titles


def run_wiki_update(
    page_titles: list[str] | None = None,
    seed_path: str | Path = DEFAULT_SEED_PATH,
    keep_existing: bool = False,
) -> tuple[int, dict[str, Any]]:
    """Refresh cached wiki chunks and update metadata only when chunks are upserted."""

    init_db()
    init_cache_metadata_table()
    init_wiki_knowledge_tables()
    titles = page_titles if page_titles is not None else read_seed_page_titles(seed_path)
    page_rows, chunk_rows, stats = scrape_wiki_pages(titles)
    stats["metadata_updated"] = False
    stats["pages_upserted"] = 0

    count = 0
    if chunk_rows:
        if not keep_existing:
            clear_wiki_knowledge_for_source(SOURCE_NAME)
        stats["pages_upserted"] = upsert_wiki_pages(page_rows)
        count = upsert_wiki_chunks(chunk_rows)
        update_cache_metadata(
            CACHE_NAME,
            source=SOURCE_NAME,
            notes=(
                f"Fetched {stats.get('pages_fetched', 0)} page(s); "
                f"failed {stats.get('pages_failed', 0)} page(s); upserted {count} wiki chunk(s)."
            ),
        )
        stats["metadata_updated"] = True
        logger.info("Wiki knowledge update complete. Upserted %d chunk(s). Metadata marked fresh.", count)
    else:
        logger.warning("Wiki update returned zero chunks. Existing cache kept; metadata not updated. Stats: %s", stats)
    return count, stats


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Update cached Pokémon GO Wiki/Fandom knowledge.")
    parser.add_argument("--page", action="append", dest="pages", help="Update one page title. Can be specified multiple times.")
    parser.add_argument("--seed", default=str(DEFAULT_SEED_PATH), help="Path to newline-delimited wiki page seed list.")
    parser.add_argument("--keep-existing", action="store_true", help="Upsert fetched rows without clearing existing wiki cache rows first.")
    return parser.parse_args()


if __name__ == "__main__":
    configure_logging()
    args = _parse_args()
    total, update_stats = run_wiki_update(page_titles=args.pages, seed_path=args.seed, keep_existing=args.keep_existing)
    print(f"Pages fetched: {update_stats.get('pages_fetched', 0)}")
    print(f"Pages failed: {update_stats.get('pages_failed', 0)}")
    print(f"Chunks created: {update_stats.get('chunks_created', 0)}")
    print(f"Chunks upserted: {total}")
    print(f"Metadata updated: {update_stats.get('metadata_updated', False)}")
    print(f"Errors: {update_stats.get('errors', [])}")
    if total == 0:
        print("No wiki cache metadata was updated because zero chunks were fetched.")
