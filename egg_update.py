"""Egg pool cache update entrypoint."""

from __future__ import annotations

import argparse
import logging
from typing import Any

from config import configure_logging
from database.cache_metadata import init_cache_metadata_table, update_cache_metadata
from database.db import init_db
from database.egg_pool_db import clear_egg_pools_for_source, init_egg_pool_tables, upsert_egg_pool_rows
from scraper.egg_scraper import SOURCE_NAME, scrape_leekduck_eggs


logger = logging.getLogger(__name__)

CACHE_NAME = "egg_pools"


def run_egg_update(force: bool = False) -> tuple[int, dict[str, Any]]:
    """Refresh the egg pool cache and update metadata only on non-zero success."""

    init_db()
    init_cache_metadata_table()
    init_egg_pool_tables()
    rows, stats = scrape_leekduck_eggs()

    count = 0
    metadata_updated = False
    if rows:
        clear_egg_pools_for_source(SOURCE_NAME)
        count = upsert_egg_pool_rows(rows)

    if count > 0:
        update_cache_metadata(
            CACHE_NAME,
            source=SOURCE_NAME,
            notes=(
                f"Upserted {count} egg pool row(s); "
                f"sections={stats.get('sections_found', 0)}; "
                f"rows_parsed={stats.get('rows_parsed', 0)}; "
                f"parse_failures={stats.get('parse_failures', 0)}; "
                f"force={int(force)}"
            ),
        )
        metadata_updated = True
        logger.info("Egg pool update complete. Upserted %d row(s). Metadata marked fresh.", count)
    else:
        logger.warning("Egg pool update returned zero rows. Existing cache kept; metadata not updated. Stats: %s", stats)

    stats["metadata_updated"] = metadata_updated
    return count, stats


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Refresh cached LeekDuck egg pools.")
    parser.add_argument("--force", action="store_true", help="Force a refresh attempt even if the cache may already be fresh.")
    args = parser.parse_args()
    configure_logging()
    total, scrape_stats = run_egg_update(force=args.force)
    print(f"Rows upserted: {total}")
    print(f"Sections found: {scrape_stats.get('sections_found', 0)}")
    print(f"Pool names: {scrape_stats.get('pool_names', [])}")
    print(f"Rows parsed: {scrape_stats.get('rows_parsed', 0)}")
    print(f"Parse failures: {scrape_stats.get('parse_failures', 0)}")
    print(f"Status code: {scrape_stats.get('status_code')}")
    print(f"Blocked: {scrape_stats.get('blocked', False)}")
    print(f"Error: {scrape_stats.get('error')}")
    print(f"Metadata updated: {scrape_stats.get('metadata_updated', False)}")
    if total == 0:
        print("No egg pool cache metadata was updated because zero rows were upserted.")
