"""Debug one Pokémon GO Hub DB best-per-type page through Playwright.

This script prints diagnostics only and never writes to SQLite.
"""

from __future__ import annotations

import argparse
import os
import re
from pathlib import Path
from typing import Any

import config
from scraper.raid_attacker_browser_scraper import TABLE_HEADERS
from scraper.raid_attacker_scraper import POKEMON_TYPES, build_best_per_type_url, parse_best_per_type_table


def _detect_headers(page: Any) -> list[list[str]]:
    return page.evaluate(
        """
        () => Array.from(document.querySelectorAll('table')).map((table) => {
            const row = table.querySelector('tr');
            if (!row) return [];
            return Array.from(row.querySelectorAll('th,td')).map((cell) => cell.innerText.trim().replace(/\s+/g, ' '));
        }).filter((headers) => headers.length > 0)
        """
    )


def _extract_matching_table_html(page: Any) -> str:
    return page.evaluate(
        """
        (requiredHeaders) => {
            const normalize = (value) => (value || '').trim().toLowerCase().replace(/#/g, 'ranknumber').replace(/[^a-z0-9]+/g, '');
            const required = requiredHeaders.map(normalize);
            for (const table of Array.from(document.querySelectorAll('table'))) {
                const row = table.querySelector('tr');
                if (!row) continue;
                const headers = Array.from(row.querySelectorAll('th,td')).map((cell) => normalize(cell.innerText));
                if (required.every((header) => headers.includes(header))) return table.outerHTML;
            }
            return '';
        }
        """,
        list(TABLE_HEADERS),
    )


def _open_context(playwright: Any, *, headless: bool, slow_mo: int) -> tuple[Any, Any | None]:
    profile_dir = (config.RAID_ATTACKER_BROWSER_PROFILE_DIR or "").strip()
    launch_options = {"headless": headless, "slow_mo": slow_mo}
    if profile_dir:
        path = Path(profile_dir)
        if not path.is_absolute():
            path = config.BASE_DIR / path
        context = playwright.chromium.launch_persistent_context(str(path), **launch_options)
        return context, None
    browser = playwright.chromium.launch(**launch_options)
    return browser.new_context(), browser


def main() -> None:
    parser = argparse.ArgumentParser(description="Debug Playwright parsing of Pokémon GO Hub DB best-per-type pages.")
    parser.add_argument("type", help="Pokémon type to open, e.g. dark or fire.")
    parser.add_argument("--headed", action="store_true", help="Launch a visible browser instead of headless mode.")
    args = parser.parse_args()

    type_name = args.type.strip().lower()
    if type_name not in POKEMON_TYPES:
        raise SystemExit(f"Unknown type '{args.type}'. Expected one of: {', '.join(POKEMON_TYPES)}")

    if args.headed:
        os.environ["RAID_ATTACKER_BROWSER_HEADLESS"] = "false"

    from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
    from playwright.sync_api import sync_playwright

    url = build_best_per_type_url(type_name)
    headless = False if args.headed else bool(config.RAID_ATTACKER_BROWSER_HEADLESS)
    timeout_ms = int(config.RAID_ATTACKER_BROWSER_TIMEOUT_SECONDS or 45) * 1000
    slow_mo = int(config.RAID_ATTACKER_BROWSER_SLOW_MO_MS or 0)
    table_found = False
    detected_headers: list[list[str]] = []
    rows: list[dict[str, Any]] = []

    with sync_playwright() as playwright:
        context, browser = _open_context(playwright, headless=headless, slow_mo=slow_mo)
        try:
            context.set_default_timeout(timeout_ms)
            page = context.new_page()
            page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
            try:
                page.wait_for_load_state("networkidle", timeout=min(timeout_ms, 3_000))
            except PlaywrightTimeoutError:
                pass
            try:
                page.wait_for_function(
                    """
                    (requiredHeaders) => {
                        const normalize = (value) => (value || '').trim().toLowerCase().replace(/#/g, 'ranknumber').replace(/[^a-z0-9]+/g, '');
                        const required = requiredHeaders.map(normalize);
                        return Array.from(document.querySelectorAll('table')).some((table) => {
                            const row = table.querySelector('tr');
                            if (!row) return false;
                            const headers = Array.from(row.querySelectorAll('th,td')).map((cell) => normalize(cell.innerText));
                            return required.every((header) => headers.includes(header));
                        });
                    }
                    """,
                    arg=list(TABLE_HEADERS),
                    timeout=timeout_ms,
                )
                table_found = True
            except PlaywrightTimeoutError:
                table_found = False

            detected_headers = _detect_headers(page)
            table_html = _extract_matching_table_html(page)
            if table_html:
                rows = parse_best_per_type_table(table_html, type_name, url=url, limit_per_type=50)
            page.close()
        finally:
            context.close()
            if browser is not None:
                browser.close()

    print(f"URL: {url}")
    print(f"Mode: {'headless' if headless else 'headed'}")
    print(f"Table found: {table_found}")
    print(f"Detected headers: {detected_headers}")
    print(f"Parsed rows: {len(rows)}")
    for row in rows[:10]:
        print(
            {
                "rank": row.get("rank"),
                "pokemon_name": row.get("pokemon_name"),
                "fast_move": row.get("fast_move"),
                "charged_move": row.get("charged_move"),
                "dps": row.get("dps"),
                "tdo": row.get("tdo"),
                "score": row.get("score"),
            }
        )


if __name__ == "__main__":
    main()