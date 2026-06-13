"""PvPoke PvP ranking cache update entrypoint."""

from __future__ import annotations

import logging
from typing import Any

from config import configure_logging
from database.cache_metadata import init_cache_metadata_table, update_cache_metadata
from database.db import init_db
from database.pvp_rankings_db import (
    SOURCE_NAME,
    clear_pvp_rankings_for_source,
    init_pvp_ranking_tables,
    upsert_pvp_rankings,
)
from scraper.pvpoke_scraper import scrape_pvpoke_rankings


logger = logging.getLogger(__name__)

CACHE_NAME = "pvp_rankings"


def run_pvp_update(limit_per_league: int = 200) -> tuple[int, dict[str, Any]]:
    """Refresh cached PvP rankings and update metadata only when rows are upserted."""

    init_db()
    init_cache_metadata_table()
    init_pvp_ranking_tables()
    rows, stats = scrape_pvpoke_rankings(limit_per_league=limit_per_league)
    stats["metadata_updated"] = False

    count = 0
    if rows:
        clear_pvp_rankings_for_source(SOURCE_NAME)
        count = upsert_pvp_rankings(rows)
        update_cache_metadata(
            CACHE_NAME,
            source=SOURCE_NAME,
            notes=(
                f"Upserted {count} PvP ranking row(s); "
                f"league_rows={stats.get('league_rows', {})}; "
                f"rows_parsed={stats.get('rows_parsed', 0)}; "
                f"stage={stats.get('scraper_stage')}"
            ),
        )
        stats["metadata_updated"] = True
        logger.info("PvP rankings update complete. Upserted %d row(s). Metadata marked fresh.", count)
    else:
        logger.warning("PvP rankings update returned zero rows. Existing cache kept; metadata not updated. Stats: %s", stats)
    return count, stats


if __name__ == "__main__":
    configure_logging()
    total, scrape_stats = run_pvp_update()
    print(f"Rows upserted: {total}")
    print(f"League rows: {scrape_stats.get('league_rows', {})}")
    print(f"Parse failures: {scrape_stats.get('parse_failures', 0)}")
    print(f"Scraper stage: {scrape_stats.get('scraper_stage')}")
    print(f"Errors: {scrape_stats.get('errors', [])}")
    print(f"Metadata updated: {scrape_stats.get('metadata_updated', False)}")
    if total == 0:
        print("No PvP cache metadata was updated because zero ranking rows were upserted.")
