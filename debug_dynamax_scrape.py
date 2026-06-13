"""Debug the Pokémon GO Hub Dynamax attackers page through Playwright.

This script writes page dumps only and never writes to SQLite.
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path
from typing import Any

import config
from scraper.dynamax_attacker_scraper import (
    DYNAMAX_ATTACKERS_URL,
    has_real_dynamax_content,
    is_dynamax_blocked_content,
    parse_dynamax_attackers_page,
    parse_dynamax_attackers_text,
)
from scraper.raid_attacker_scraper import BLOCK_MARKERS, POKEMON_TYPES


DEBUG_DIR = config.BASE_DIR / "debug"


def _open_context(playwright: Any) -> tuple[Any, Any | None]:
    launch_options = {
        "headless": bool(config.DYNAMAX_BROWSER_HEADLESS),
        "slow_mo": int(config.DYNAMAX_BROWSER_SLOW_MO_MS or 0),
    }
    profile_dir = (config.DYNAMAX_BROWSER_PROFILE_DIR or "").strip()
    if profile_dir:
        path = Path(profile_dir)
        if not path.is_absolute():
            path = config.BASE_DIR / path
        path.mkdir(parents=True, exist_ok=True)
        context = playwright.chromium.launch_persistent_context(str(path), **launch_options)
        return context, None
    browser = playwright.chromium.launch(**launch_options)
    return browser.new_context(), browser


def _is_blocked(text: str) -> bool:
    lowered = text.lower()
    return any(marker in lowered for marker in BLOCK_MARKERS)


def _has_table_like_rows(text: str) -> bool:
    return bool(
        re.search(r"(?im)^\s*#?\s*1\b.+\b(?:dps|tdo|score|max|attack|move)\b", text)
        or re.search(r"(?is)\b(?:dps|tdo|score)\b.+\b(?:dps|tdo|score)\b", text)
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Debug Playwright parsing of the Pokémon GO Hub Dynamax attackers page.")
    parser.add_argument(
        "--pause",
        action="store_true",
        help="Pause after opening the page so you can solve Cloudflare in the browser before dumping/parsing.",
    )
    args = parser.parse_args()

    try:
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise SystemExit(f"Playwright is not installed: {exc}") from exc

    timeout_ms = int(config.DYNAMAX_BROWSER_TIMEOUT_SECONDS or 60) * 1000
    DEBUG_DIR.mkdir(parents=True, exist_ok=True)
    html_path = DEBUG_DIR / "dynamax_page.html"
    text_path = DEBUG_DIR / "dynamax_page.txt"
    screenshot_path = DEBUG_DIR / "dynamax_screenshot.png"

    final_url = DYNAMAX_ATTACKERS_URL
    title = ""
    html = ""
    visible_text = ""

    with sync_playwright() as playwright:
        context, browser = _open_context(playwright)
        try:
            context.set_default_timeout(timeout_ms)
            context.set_default_navigation_timeout(timeout_ms)
            page = context.new_page()
            try:
                page.goto(DYNAMAX_ATTACKERS_URL, wait_until="domcontentloaded", timeout=timeout_ms)
                try:
                    page.wait_for_load_state("networkidle", timeout=min(timeout_ms, 5_000))
                except PlaywrightTimeoutError:
                    pass
                if args.pause:
                    print("Solve Cloudflare in the opened browser, then press Enter here to continue.")
                    input()
                    try:
                        page.wait_for_load_state("networkidle", timeout=min(timeout_ms, 5_000))
                    except PlaywrightTimeoutError:
                        pass
                final_url = page.url
                title = page.title()
                html = page.content()
                visible_text = page.locator("body").inner_text(timeout=min(timeout_ms, 5_000))
                page.screenshot(path=str(screenshot_path), full_page=True)
            finally:
                page.close()
        finally:
            context.close()
            if browser is not None:
                browser.close()

    html_path.write_text(html, encoding="utf-8")
    text_path.write_text(visible_text, encoding="utf-8")
    dom_rows, dom_type_rows = parse_dynamax_attackers_page(html)
    text_rows, text_type_rows = parse_dynamax_attackers_text(visible_text)
    rows, type_rows = (dom_rows, dom_type_rows) if dom_rows else (text_rows, text_type_rows)
    text_lower = visible_text.lower()
    expected_types_present = {type_name: bool(re.search(rf"\b{re.escape(type_name)}\b", text_lower)) for type_name in POKEMON_TYPES}
    combined_content = f"{title}\n{visible_text}\n{html}"
    real_content_detected = has_real_dynamax_content(combined_content)
    blocked_after_override = is_dynamax_blocked_content(f"{visible_text}\n{html}", title=title)

    print(f"Final URL: {final_url}")
    print(f"Page title: {title}")
    print(f"Cloudflare/block text detected: {_is_blocked(html) or _is_blocked(visible_text)}")
    print(f"Real Dynamax content detected: {real_content_detected}")
    print(f"Blocked after real-content override: {blocked_after_override}")
    print(f"Expected type names present: {expected_types_present}")
    print(f"Any expected type names appear: {any(expected_types_present.values())}")
    print(f"Table-like row text appears: {_has_table_like_rows(visible_text)}")
    print(f"DOM parser rows: {len(dom_rows)}")
    print(f"Text parser rows: {len(text_rows)}")
    print(f"Parser rows per type: {type_rows}")
    print(f"Parser total rows: {len(rows)}")
    print("First 2000 characters of visible text:")
    print(visible_text[:2000])
    print(f"Saved HTML: {html_path}")
    print(f"Saved text: {text_path}")
    print(f"Saved screenshot: {screenshot_path}")


if __name__ == "__main__":
    main()