"""Raid attacker cache update entrypoint."""

from __future__ import annotations

import argparse
import logging
from typing import Any

import config
from config import POKEMON_DB_SCRAPE_LIMIT, configure_logging
from database.cache_metadata import init_cache_metadata_table, update_cache_metadata
from database.db import init_db
from database.raid_attackers_db import init_raid_attacker_tables, upsert_raid_attacker_rankings
from raid_attacker_import import import_raid_attacker_seed
from scraper.raid_attacker_scraper import scrape_best_attackers_per_type


logger = logging.getLogger(__name__)

CACHE_NAME = "raid_attackers"
SOURCE_NAME = "raid_attacker_rankings"


def scrape_raid_attacker_rankings(limit: int = POKEMON_DB_SCRAPE_LIMIT) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Scrape Pokémon GO Hub DB best-per-type raid attacker rows."""

    return scrape_best_attackers_per_type(limit_per_type=limit)


def scrape_raid_attacker_rankings_with_browser(limit: int = POKEMON_DB_SCRAPE_LIMIT) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Scrape Pokémon GO Hub DB best-per-type rows through Playwright when enabled."""

    from scraper.raid_attacker_browser_scraper import scrape_best_attackers_per_type_with_browser

    return scrape_best_attackers_per_type_with_browser(limit_per_type=limit)


def _empty_browser_stats() -> dict[str, Any]:
    return {
        "browser_enabled": bool(config.RAID_ATTACKER_USE_BROWSER_SCRAPER),
        "browser_headless": bool(config.RAID_ATTACKER_BROWSER_HEADLESS),
        "browser_pages_checked": 0,
        "browser_pages_loaded": 0,
        "browser_pages_blocked": 0,
        "browser_pages_failed": 0,
        "browser_pages_parsed": 0,
        "browser_rows_parsed": 0,
        "browser_type_rows": {},
        "browser_blocked_types": [],
        "browser_failed_types": [],
        "browser_parsed_types": [],
    }


def run_raid_attacker_update(limit: int = POKEMON_DB_SCRAPE_LIMIT, force: bool = False) -> tuple[int, dict[str, Any]]:
    """Refresh the raid attacker cache and update metadata only on success."""

    init_db()
    init_cache_metadata_table()
    init_raid_attacker_tables()
    request_rows, request_stats = scrape_raid_attacker_rankings(limit=limit)
    stats: dict[str, Any] = {
        **request_stats,
        **_empty_browser_stats(),
        "seed_imported": 0,
        "seed_skipped": 0,
        "seed_file_found": 0,
        "seed_example_rows": 0,
        "seed_example_data_rejected": 0,
        "update_stage": "requests",
    }

    count = 0
    if request_rows:
        count = upsert_raid_attacker_rankings(request_rows)
    else:
        browser_rows: list[dict[str, Any]] = []
        if config.RAID_ATTACKER_USE_BROWSER_SCRAPER:
            browser_rows, browser_stats = scrape_raid_attacker_rankings_with_browser(limit=limit)
            stats.update(browser_stats)
            stats["update_stage"] = "browser"
        else:
            stats["browser_enabled"] = False

        if browser_rows:
            count = upsert_raid_attacker_rankings(browser_rows)
        else:
            stats["update_stage"] = "seed"
            seed_result = import_raid_attacker_seed(allow_example_data=False)
            count = int(seed_result.get("imported", 0) or 0)
            stats.update(
                {
                    "seed_imported": count,
                    "seed_skipped": int(seed_result.get("skipped", 0) or 0),
                    "seed_file_found": 1 if seed_result.get("path") else 0,
                    "seed_example_rows": int(seed_result.get("example_rows", 0) or 0),
                    "seed_example_data_rejected": 1 if seed_result.get("example_data_rejected") else 0,
                }
            )

    if count > 0 and stats.get("update_stage") == "requests":
        stats["update_stage"] = "requests"
    elif count > 0 and stats.get("browser_rows_parsed", 0):
        stats["update_stage"] = "browser"
    elif count > 0:
        stats["update_stage"] = "seed"

    if count > 0:
        update_cache_metadata(
            CACHE_NAME,
            source=SOURCE_NAME,
            notes=(
                f"Upserted {count} raid attacker ranking row(s); "
                f"stage={stats.get('update_stage')}; "
                f"requests_rows={stats.get('rows_parsed', stats.get('scraped_rows', 0))}; "
                f"browser_rows={stats.get('browser_rows_parsed', 0)}; "
                f"seed_imported={stats.get('seed_imported', 0)}; "
                f"force={int(force)}"
            ),
        )
        logger.info("Raid attacker update complete. Upserted %d ranking row(s). Metadata marked fresh.", count)
    else:
        logger.warning("Raid attacker update returned zero ranking rows. Existing cache kept; metadata not updated. Stats: %s", stats)
    return count, stats


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Refresh cached raid attacker rankings.")
    parser.add_argument("--force", action="store_true", help="Force a refresh attempt even if the cache may already be fresh.")
    args = parser.parse_args()
    configure_logging()
    total, scrape_stats = run_raid_attacker_update(force=args.force)
    print(f"Rows upserted: {total}")
    print(f"Pages checked: {scrape_stats.get('pages_checked', 0)}")
    print(f"Pages blocked: {scrape_stats.get('pages_blocked', 0)}")
    print(f"Pages parsed: {scrape_stats.get('pages_parsed', 0)}")
    print(f"Rows parsed: {scrape_stats.get('rows_parsed', scrape_stats.get('scraped_rows', 0))}")
    print(f"Type rows: {scrape_stats.get('type_rows', {})}")
    print(f"Parse failures: {scrape_stats.get('parse_failures', 0)}")
    print(f"Status codes: {scrape_stats.get('status_codes', {})}")
    print(f"Blocked types: {scrape_stats.get('blocked_types', [])}")
    print(f"Parsed types: {scrape_stats.get('parsed_types', [])}")
    print(f"Browser enabled: {scrape_stats.get('browser_enabled', False)}")
    print(f"Browser headless: {scrape_stats.get('browser_headless', True)}")
    print(f"Browser pages checked: {scrape_stats.get('browser_pages_checked', 0)}")
    print(f"Browser pages loaded: {scrape_stats.get('browser_pages_loaded', 0)}")
    print(f"Browser pages blocked: {scrape_stats.get('browser_pages_blocked', 0)}")
    print(f"Browser pages failed: {scrape_stats.get('browser_pages_failed', 0)}")
    print(f"Browser pages parsed: {scrape_stats.get('browser_pages_parsed', 0)}")
    print(f"Browser rows parsed: {scrape_stats.get('browser_rows_parsed', 0)}")
    print(f"Browser type rows: {scrape_stats.get('browser_type_rows', {})}")
    print(f"Browser blocked types: {scrape_stats.get('browser_blocked_types', [])}")
    print(f"Browser failed types: {scrape_stats.get('browser_failed_types', [])}")
    print(f"Browser parsed types: {scrape_stats.get('browser_parsed_types', [])}")
    print(f"Seed rows imported: {scrape_stats.get('seed_imported', 0)}")
    print(f"Seed rows skipped: {scrape_stats.get('seed_skipped', 0)}")
    if scrape_stats.get("seed_example_data_rejected"):
        print("Seed file appears to contain example placeholder data. Replace it with real raid attacker rankings before importing.")
    if total == 0:
        print("No raid attacker cache metadata was updated because zero ranking rows were upserted.")