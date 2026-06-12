"""Playwright browser scraper for Pokémon GO Hub DB best-per-type pages.

This module is only used by explicit/manual raid attacker cache update flows.
Normal Discord questions must continue to read from SQLite and never scrape live.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import config
from scraper.raid_attacker_scraper import (
    BLOCK_MARKERS,
    POKEMON_TYPES,
    build_best_per_type_url,
    parse_best_per_type_page,
)


logger = logging.getLogger(__name__)

BROWSER_SOURCE_NAME = "pokemongohub_best_per_type_browser"
TABLE_HEADERS = ("#", "Name", "Fast Attack", "Charged Attack", "DPS", "TDO", "Score")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


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


def _is_blocked_text(text: str | None) -> bool:
    lowered = (text or "").lower()
    return any(marker in lowered for marker in BLOCK_MARKERS)


def _with_browser_source(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    deduped: list[dict[str, Any]] = []
    seen: set[tuple[Any, ...]] = set()
    for row in rows:
        pokemon_name = str(row.get("pokemon_name") or "").strip()
        if not pokemon_name:
            continue
        browser_row = {**row, "source": BROWSER_SOURCE_NAME}
        dedupe_key = (
            browser_row.get("ranking_scope"),
            pokemon_name,
            browser_row.get("rank"),
            browser_row.get("fast_move"),
            browser_row.get("charged_move"),
        )
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        deduped.append(browser_row)
    return deduped


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
                if (required.every((header) => headers.includes(header))) {
                    return table.outerHTML;
                }
            }
            return '';
        }
        """,
        list(TABLE_HEADERS),
    )


def _wait_for_best_per_type_table(page: Any, timeout_ms: int) -> str:
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
    return _extract_matching_table_html(page)


def _launch_context(playwright: Any) -> tuple[Any, Any | None]:
    launch_options = {
        "headless": bool(config.RAID_ATTACKER_BROWSER_HEADLESS),
        "slow_mo": int(config.RAID_ATTACKER_BROWSER_SLOW_MO_MS or 0),
    }
    profile_dir = (config.RAID_ATTACKER_BROWSER_PROFILE_DIR or "").strip()
    if profile_dir:
        user_data_dir = Path(profile_dir)
        if not user_data_dir.is_absolute():
            user_data_dir = config.BASE_DIR / user_data_dir
        context = playwright.chromium.launch_persistent_context(str(user_data_dir), **launch_options)
        return context, None

    browser = playwright.chromium.launch(**launch_options)
    context = browser.new_context()
    return context, browser


def scrape_best_attackers_per_type_with_browser(limit_per_type: int = 50) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Scrape all best-per-type ranking tables through Playwright Chromium."""

    stats = _empty_browser_stats()
    if not config.RAID_ATTACKER_USE_BROWSER_SCRAPER:
        stats["browser_enabled"] = False
        return [], stats

    try:
        from playwright.sync_api import Error as PlaywrightError
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        logger.warning("Playwright is not installed; browser raid attacker scraper skipped: %s", exc)
        stats["browser_enabled"] = False
        stats["browser_pages_failed"] = len(POKEMON_TYPES)
        stats["browser_failed_types"] = list(POKEMON_TYPES)
        return [], stats

    timeout_ms = int(config.RAID_ATTACKER_BROWSER_TIMEOUT_SECONDS or 45) * 1000
    rows: list[dict[str, Any]] = []

    with sync_playwright() as playwright:
        context, browser = _launch_context(playwright)
        try:
            context.set_default_timeout(timeout_ms)
            context.set_default_navigation_timeout(timeout_ms)
            for type_name in POKEMON_TYPES:
                url = build_best_per_type_url(type_name)
                stats["browser_pages_checked"] += 1
                stats["browser_type_rows"][type_name] = 0
                page = context.new_page()
                try:
                    page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
                    try:
                        page.wait_for_load_state("networkidle", timeout=min(timeout_ms, 3_000))
                    except PlaywrightTimeoutError:
                        pass
                    stats["browser_pages_loaded"] += 1

                    table_html = _wait_for_best_per_type_table(page, timeout_ms)
                    page_rows = _with_browser_source(
                        parse_best_per_type_page(
                            table_html or page.content(),
                            type_name,
                            url=url,
                            limit_per_type=limit_per_type,
                            scraped_at=_utc_now(),
                        )
                    )
                    stats["browser_type_rows"][type_name] = len(page_rows)
                    if page_rows:
                        rows.extend(page_rows)
                        stats["browser_pages_parsed"] += 1
                        stats["browser_rows_parsed"] += len(page_rows)
                        stats["browser_parsed_types"].append(type_name)
                    else:
                        stats["browser_pages_failed"] += 1
                        stats["browser_failed_types"].append(type_name)
                except PlaywrightTimeoutError:
                    page_text = ""
                    page_html = ""
                    try:
                        page_text = page.locator("body").inner_text(timeout=2_000)
                        page_html = page.content()
                    except PlaywrightError:
                        pass
                    if _is_blocked_text(page_text) or _is_blocked_text(page_html):
                        stats["browser_pages_blocked"] += 1
                        stats["browser_blocked_types"].append(type_name)
                    else:
                        stats["browser_pages_failed"] += 1
                        stats["browser_failed_types"].append(type_name)
                except PlaywrightError as exc:
                    logger.warning("Browser scrape failed for %s (%s): %s", type_name, url, exc)
                    stats["browser_pages_failed"] += 1
                    stats["browser_failed_types"].append(type_name)
                finally:
                    page.close()
        finally:
            context.close()
            if browser is not None:
                browser.close()

    logger.info("Pokémon GO Hub browser best-per-type scrape stats: %s", stats)
    return rows, stats