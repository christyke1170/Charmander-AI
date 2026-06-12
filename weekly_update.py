"""Manual update script for scraping and storing Pokémon GO events locally."""

from __future__ import annotations

import logging

from config import configure_logging
from database.db import init_db, upsert_events
from scraper.leekduck_scraper import scrape_events as scrape_leekduck_events
from scraper.normalize_events import normalize_events
from scraper.official_pogo_scraper import scrape_events as scrape_official_events


logger = logging.getLogger(__name__)


def run_update() -> int:
    """Run all scrapers once and store normalized events.

    This is intentionally manual. Discord commands read from SQLite and do not
    scrape live websites on every request.
    """

    init_db()
    scraped_events = []

    for source_name, scraper in (
        ("Leek Duck", scrape_leekduck_events),
        ("Pokémon GO Live", scrape_official_events),
    ):
        try:
            scraped_events.extend(scraper())
        except Exception as exc:  # Keep one broken source from stopping updates.
            logger.exception("Failed to scrape %s: %s", source_name, exc)

    normalized = normalize_events(scraped_events)
    count = upsert_events(normalized)
    logger.info("Weekly update complete. Upserted %d event(s).", count)
    return count


if __name__ == "__main__":
    configure_logging()
    total = run_update()
    print(f"Upserted {total} Pokémon GO event(s).")
