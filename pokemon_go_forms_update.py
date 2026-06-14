"""Manual/local Pokémon GO Hub Pokémon/form cache update script."""

from __future__ import annotations

import argparse
import logging
import time
from typing import Any

from config import configure_logging
from database.cache_metadata import init_cache_metadata_table, update_cache_metadata
from database.db import get_connection, init_db
from database.pokemon_go_forms_db import SOURCE_NAME, init_pokemon_go_forms_tables, upsert_pokemon_go_form
from scraper.pokemon_go_hub_forms_scraper import DEFAULT_MAX_DEX, generate_pokemon_go_hub_candidates, scrape_pokemon_go_hub_form_page


logger = logging.getLogger(__name__)

CACHE_NAME = "pokemon_go_forms"


def _cached_url_exists(url: str) -> bool:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT 1 FROM pokemon_go_forms WHERE source = ? AND url = ? LIMIT 1",
            (SOURCE_NAME, url),
        ).fetchone()
    return row is not None


def _build_candidate_subset(
    *,
    max_dex: int,
    start_dex: int | None,
    end_dex: int | None,
    limit: int | None,
    include_forms: bool,
) -> list[dict[str, Any]]:
    candidates = generate_pokemon_go_hub_candidates(max_dex=max_dex, include_forms=include_forms)
    lower = max(int(start_dex or 1), 1)
    upper = min(int(end_dex or max_dex), max_dex)
    filtered = [candidate for candidate in candidates if lower <= int(candidate["dex_number"]) <= upper]
    if limit is not None and limit >= 0:
        return filtered[:limit]
    return filtered


def run_pokemon_go_forms_update(
    *,
    max_dex: int = DEFAULT_MAX_DEX,
    start_dex: int | None = None,
    end_dex: int | None = None,
    delay_seconds: float = 0.75,
    force: bool = False,
    limit: int | None = None,
    progress_every: int = 25,
    pause_browser: bool = False,
    include_forms: bool = False,
    manual_cloudflare: bool = False,
) -> tuple[int, dict[str, Any]]:
    """Run the manual/local Pokémon GO Hub forms cache refresh."""

    init_db()
    init_cache_metadata_table()
    init_pokemon_go_forms_tables()

    candidates = _build_candidate_subset(
        max_dex=max_dex,
        start_dex=start_dex,
        end_dex=end_dex,
        limit=limit,
        include_forms=include_forms,
    )
    stats: dict[str, Any] = {
        "candidates_total": len(candidates),
        "checked": 0,
        "skipped_cached": 0,
        "rows_upserted": 0,
        "invalid_pages": 0,
        "blocked_pages": 0,
        "request_errors": 0,
        "status_codes": {},
        "delay_seconds": delay_seconds,
        "force": bool(force),
        "pause_browser": bool(pause_browser),
        "manual_cloudflare": bool(manual_cloudflare or pause_browser),
        "include_forms": bool(include_forms),
    }

    candidate_label = "form-inclusive" if include_forms else "base Pokédex"
    print(f"Generated {len(candidates)} {candidate_label} candidates. Include forms: {str(bool(include_forms)).lower()}")
    print(
        f"Manual Cloudflare prompt: {'enabled' if stats['manual_cloudflare'] else 'disabled'}"
    )

    for index, candidate in enumerate(candidates, start=1):
        url = str(candidate["url"])
        if not force and _cached_url_exists(url):
            stats["skipped_cached"] += 1
            continue

        row, page_stats = scrape_pokemon_go_hub_form_page(
            url=url,
            dex_number=int(candidate["dex_number"]),
            form_hint=candidate.get("form"),
            pause_browser=bool(manual_cloudflare or pause_browser),
        )
        stats["checked"] += 1
        status_code = page_stats.get("status_code")
        if status_code is not None:
            status_key = str(status_code)
            stats["status_codes"][status_key] = int(stats["status_codes"].get(status_key, 0)) + 1
        if page_stats.get("blocked"):
            stats["blocked_pages"] += 1
        elif page_stats.get("error"):
            stats["request_errors"] += 1
        elif row is None:
            stats["invalid_pages"] += 1
        else:
            upsert_pokemon_go_form(row)
            stats["rows_upserted"] += 1

        if progress_every > 0 and index % progress_every == 0:
            print(
                f"Progress: {index}/{len(candidates)} candidates | "
                f"checked={stats['checked']} upserted={stats['rows_upserted']} "
                f"cached={stats['skipped_cached']} invalid={stats['invalid_pages']} blocked={stats['blocked_pages']}"
            )

        if index < len(candidates):
            time.sleep(max(delay_seconds, 0.0))

    if stats["rows_upserted"] > 0:
        update_cache_metadata(
            CACHE_NAME,
            source=SOURCE_NAME,
            notes=(
                f"Upserted {stats['rows_upserted']} Pokémon GO Hub form row(s); "
                f"checked={stats['checked']}; skipped_cached={stats['skipped_cached']}; "
                f"invalid={stats['invalid_pages']}; blocked={stats['blocked_pages']}; force={int(force)}; "
                f"include_forms={int(include_forms)}; manual_cloudflare={int(stats['manual_cloudflare'])}"
            ),
        )
        stats["metadata_updated"] = True
    else:
        stats["metadata_updated"] = False
    logger.info("Pokémon GO forms update complete. Stats: %s", stats)
    return int(stats["rows_upserted"]), stats


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Refresh cached Pokémon GO Hub Pokémon/form pages.")
    parser.add_argument("--max-dex", type=int, default=DEFAULT_MAX_DEX, help="Maximum Pokédex number to consider.")
    parser.add_argument("--start-dex", type=int, default=None, help="Optional starting Pokédex number.")
    parser.add_argument("--end-dex", type=int, default=None, help="Optional ending Pokédex number.")
    parser.add_argument("--delay-seconds", type=float, default=0.75, help="Delay between candidate requests.")
    parser.add_argument("--force", action="store_true", help="Re-scrape even when a URL is already cached.")
    parser.add_argument("--limit", type=int, default=None, help="Limit total candidates for testing.")
    parser.add_argument(
        "--include-forms",
        action="store_true",
        help="Include alternate form suffix candidates instead of scraping only base Pokédex entries.",
    )
    parser.add_argument(
        "--pause-browser",
        action="store_true",
        help="Backward-compatible alias: allow a manual pause only when Cloudflare/manual intervention is required.",
    )
    parser.add_argument(
        "--manual-cloudflare",
        action="store_true",
        help="Allow a manual browser pause only when Cloudflare/manual intervention is required.",
    )
    args = parser.parse_args()

    configure_logging()
    total, stats = run_pokemon_go_forms_update(
        max_dex=args.max_dex,
        start_dex=args.start_dex,
        end_dex=args.end_dex,
        delay_seconds=args.delay_seconds,
        force=args.force,
        limit=args.limit,
        include_forms=args.include_forms,
        pause_browser=args.pause_browser,
        manual_cloudflare=args.manual_cloudflare,
    )
    print(f"Candidates total: {stats.get('candidates_total', 0)}")
    print(f"Include forms: {str(bool(stats.get('include_forms'))).lower()}")
    print(f"Manual Cloudflare prompt: {'enabled' if stats.get('manual_cloudflare') else 'disabled'}")
    print(f"Checked: {stats.get('checked', 0)}")
    print(f"Skipped cached: {stats.get('skipped_cached', 0)}")
    print(f"Invalid pages: {stats.get('invalid_pages', 0)}")
    print(f"Blocked pages: {stats.get('blocked_pages', 0)}")
    print(f"Request errors: {stats.get('request_errors', 0)}")
    print(f"Rows upserted: {total}")
    print(f"Status codes: {stats.get('status_codes', {})}")
    if not total:
        print("No pokemon_go_forms cache metadata was updated because zero rows were upserted.")