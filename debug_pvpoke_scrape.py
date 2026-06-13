"""Debug one rendered PvPoke league page and save scrape artifacts.

This script is for manual validation only. Normal Discord question paths must use
the SQLite cache and never invoke browser scraping.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

from scraper.pvpoke_scraper import PVP_LEAGUES, _build_gamemaster_indexes, parse_pvpoke_ranking_json


DEBUG_DIR = Path(__file__).resolve().parent / "debug"


def _visible_excerpt(text: str, limit: int = 2000) -> str:
    return re.sub(r"\s+", " ", text or "").strip()[:limit]


def _ranking_like_text(text: str) -> bool:
    lowered = (text or "").lower()
    markers = ("rankings", "great league", "ultra league", "master league", "score", "moves", "battle rating")
    return any(marker in lowered for marker in markers)


def _parse_rows_from_page_json(page: Any, league: str) -> int:
    """Try to load PvPoke JSON assets through the rendered browser page."""

    info = PVP_LEAGUES[league]
    ranking_url = str(info["json_url"])
    try:
        ranking_data = page.evaluate(
            """async (url) => {
                const response = await fetch(url);
                if (!response.ok) throw new Error(`${response.status} ${response.statusText}`);
                return await response.json();
            }""",
            ranking_url,
        )
        gamemaster = page.evaluate(
            """async () => {
                const response = await fetch('/data/gamemaster.min.json');
                if (!response.ok) throw new Error(`${response.status} ${response.statusText}`);
                return await response.json();
            }"""
        )
        pokemon_index, move_index = _build_gamemaster_indexes(gamemaster)
        rows = parse_pvpoke_ranking_json(
            ranking_data,
            league=league,
            pokemon_index=pokemon_index,
            move_index=move_index,
            scraped_at="debug",
            limit=200,
        )
        return len(rows)
    except Exception:
        return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Debug rendered PvPoke rankings for one league.")
    parser.add_argument("--league", choices=sorted(PVP_LEAGUES), default="great")
    parser.add_argument("--headed", action="store_true", help="Run the browser visibly instead of headless.")
    args = parser.parse_args()

    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise SystemExit(f"Playwright is not installed: {exc}") from exc

    league = args.league
    url = str(PVP_LEAGUES[league]["page_url"])
    DEBUG_DIR.mkdir(parents=True, exist_ok=True)
    html_path = DEBUG_DIR / f"pvpoke_{league}.html"
    text_path = DEBUG_DIR / f"pvpoke_{league}.txt"
    png_path = DEBUG_DIR / f"pvpoke_{league}.png"

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=not args.headed)
        page = browser.new_page(viewport={"width": 1400, "height": 1000})
        try:
            page.goto(url, wait_until="networkidle", timeout=60_000)
            page.wait_for_timeout(2_000)
            final_url = page.url
            title = page.title()
            html = page.content()
            visible_text = page.locator("body").inner_text(timeout=10_000)
            parser_rows_found = _parse_rows_from_page_json(page, league)
            page.screenshot(path=str(png_path), full_page=True)
        finally:
            browser.close()

    html_path.write_text(html, encoding="utf-8")
    text_payload = {
        "final_url": final_url,
        "title": title,
        "ranking_like_text": _ranking_like_text(visible_text),
        "parser_rows_found": parser_rows_found,
        "first_2000_visible_characters": _visible_excerpt(visible_text),
    }
    text_path.write_text(json.dumps(text_payload, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"Final URL: {final_url}")
    print(f"Page title: {title}")
    print(f"Ranking-like text appears: {_ranking_like_text(visible_text)}")
    print("First 2000 visible characters:")
    print(_visible_excerpt(visible_text))
    print(f"Parser rows found: {parser_rows_found}")
    print(f"Saved HTML: {html_path}")
    print(f"Saved text: {text_path}")
    print(f"Saved screenshot: {png_path}")


if __name__ == "__main__":
    main()