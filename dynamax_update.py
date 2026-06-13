"""Dynamax/Gigantamax attacker cache update entrypoint."""

from __future__ import annotations

import argparse
import logging
from typing import Any

from config import configure_logging
from database.cache_metadata import init_cache_metadata_table, update_cache_metadata
from database.db import init_db
from database.dynamax_attackers_db import (
    clear_dynamax_attackers_for_source,
    init_dynamax_attacker_tables,
    upsert_dynamax_attackers,
)
import dynamax_import
from dynamax_import import MANUAL_SOURCE
from scraper.dynamax_attacker_scraper import SOURCE_NAME, scrape_dynamax_attackers_per_type


logger = logging.getLogger(__name__)

CACHE_NAME = "dynamax_attackers"


def run_dynamax_update(limit: int = 10, force: bool = False, pause_browser: bool = False) -> tuple[int, dict[str, Any]]:
    """Refresh the Dynamax attacker cache and update metadata only on success."""

    init_db()
    init_cache_metadata_table()
    init_dynamax_attacker_tables()
    rows, stats = scrape_dynamax_attackers_per_type(limit_per_type=limit, pause_browser=pause_browser)
    stats["metadata_updated"] = False
    stats["update_source"] = "none"
    stats["csv_rows_read"] = 0
    stats["csv_imported"] = 0
    stats["csv_skipped"] = 0
    stats["csv_validation_errors"] = []
    stats["csv_path"] = None

    count = 0
    source_used = SOURCE_NAME
    if rows:
        clear_dynamax_attackers_for_source(MANUAL_SOURCE)
        clear_dynamax_attackers_for_source(SOURCE_NAME)
        count = upsert_dynamax_attackers(rows)
        stats["update_source"] = "live_scraper"
    elif dynamax_import.CSV_PATH.exists():
        import_result = dynamax_import.import_dynamax_csv(update_metadata_on_success=False)
        count = int(import_result.get("imported", 0) or 0)
        source_used = MANUAL_SOURCE
        stats.update(
            {
                "update_source": "manual_csv" if count > 0 else "manual_csv_zero_rows",
                "csv_rows_read": int(import_result.get("rows_read", 0) or 0),
                "csv_imported": count,
                "csv_skipped": int(import_result.get("skipped", 0) or 0),
                "csv_validation_errors": list(import_result.get("validation_errors", []) or []),
                "csv_path": import_result.get("path"),
                "csv_example_rows": int(import_result.get("example_rows", 0) or 0),
                "csv_example_data_rejected": bool(import_result.get("example_data_rejected")),
            }
        )

    if count > 0:
        update_cache_metadata(
            CACHE_NAME,
            source=source_used,
            notes=(
                f"Upserted {count} Dynamax attacker row(s); "
                f"update_source={stats.get('update_source')}; "
                f"stage={stats.get('scraper_stage')}; "
                f"rows_parsed={stats.get('rows_parsed', 0)}; "
                f"csv_imported={stats.get('csv_imported', 0)}; "
                f"pause_browser={int(pause_browser)}; "
                f"force={int(force)}"
            ),
        )
        stats["metadata_updated"] = True
        logger.info("Dynamax attacker update complete. Upserted %d row(s). Metadata marked fresh.", count)
    else:
        logger.warning("Dynamax attacker update returned zero rows. Existing cache kept; metadata not updated. Stats: %s", stats)
    return count, stats


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Refresh cached Dynamax/Gigantamax attacker rankings.")
    parser.add_argument("--force", action="store_true", help="Force a refresh attempt even if the cache may already be fresh.")
    parser.add_argument(
        "--pause-browser",
        action="store_true",
        help="Open the persistent headed Dynamax browser and pause so Cloudflare can be solved before parsing.",
    )
    args = parser.parse_args()
    configure_logging()
    total, scrape_stats = run_dynamax_update(force=args.force, pause_browser=args.pause_browser)
    print(f"Rows upserted: {total}")
    print(f"Type rows: {scrape_stats.get('type_rows', {})}")
    print(f"Parse failures: {scrape_stats.get('parse_failures', 0)}")
    print(f"Blocked: {scrape_stats.get('blocked', False)}")
    print(f"Error: {scrape_stats.get('error')}")
    print(f"Scraper stage: {scrape_stats.get('scraper_stage')}")
    print(f"Parser used: {scrape_stats.get('parser_used')}")
    print(f"DOM rows: {scrape_stats.get('dom_rows', 0)}")
    print(f"Text rows: {scrape_stats.get('text_rows', 0)}")
    print(f"DOM score rows: {scrape_stats.get('dom_score_rows', 0)}")
    print(f"Text score rows: {scrape_stats.get('text_score_rows', 0)}")
    print(f"Update source: {scrape_stats.get('update_source')}")
    print(f"CSV rows read: {scrape_stats.get('csv_rows_read', 0)}")
    print(f"CSV rows imported: {scrape_stats.get('csv_imported', 0)}")
    print(f"CSV rows skipped: {scrape_stats.get('csv_skipped', 0)}")
    print(f"CSV validation errors: {len(scrape_stats.get('csv_validation_errors', []) or [])}")
    print(f"Metadata updated: {scrape_stats.get('metadata_updated', False)}")
    if total == 0:
        print("No Dynamax attacker cache metadata was updated because zero ranking rows were upserted.")
