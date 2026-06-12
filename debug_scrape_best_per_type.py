"""Debug one Pokémon GO Hub DB best-per-type raid attacker page.

This script fetches and parses a single page. It prints diagnostics only and
does not write to SQLite.
"""

from __future__ import annotations

import argparse
import re

from bs4 import BeautifulSoup

from config import configure_logging
from scraper.raid_attacker_scraper import (
    POKEMON_TYPES,
    build_best_per_type_url,
    fetch_best_per_type_page,
    is_blocked_page,
    parse_best_per_type_table,
)


def _clean_text(value: str | None) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def _detected_table_headers(html: str) -> list[list[str]]:
    soup = BeautifulSoup(html, "html.parser")
    detected: list[list[str]] = []
    for table in soup.find_all("table"):
        first_row = table.find("tr")
        if not first_row:
            continue
        cells = first_row.find_all("th") or first_row.find_all(["th", "td"])
        detected.append([_clean_text(cell.get_text(" ", strip=True)) for cell in cells])
    return detected


def _page_title(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    if soup.title:
        return _clean_text(soup.title.get_text(" ", strip=True))
    heading = soup.find("h1")
    return _clean_text(heading.get_text(" ", strip=True) if heading else "")


def _print_likely_snippets(html: str) -> None:
    soup = BeautifulSoup(html, "html.parser")
    text = _clean_text(soup.get_text(" ", strip=True))
    lowered = text.lower()
    keywords = ("fast attack", "charged attack", "dps", "tdo", "score", "rank", "best")
    printed = False
    for keyword in keywords:
        index = lowered.find(keyword)
        if index == -1:
            continue
        start = max(index - 300, 0)
        end = min(index + 700, len(text))
        print(f"\nSnippet around '{keyword}':")
        print(text[start:end])
        printed = True
    if not printed:
        print("\nNo likely ranking/card/table text snippets found.")
        print(text[:1000])


def main() -> None:
    parser = argparse.ArgumentParser(description="Debug Pokémon GO Hub DB best-per-type raid attacker parsing.")
    parser.add_argument("type", nargs="?", default="fire", help="Pokémon type to fetch; defaults to fire.")
    args = parser.parse_args()
    type_name = args.type.strip().lower()
    if type_name not in POKEMON_TYPES:
        raise SystemExit(f"Unknown type '{args.type}'. Expected one of: {', '.join(POKEMON_TYPES)}")

    configure_logging()
    expected_url = build_best_per_type_url(type_name)
    url, status_code, html = fetch_best_per_type_page(type_name)
    html = html or ""
    blocked = is_blocked_page(html, status_code)
    rows = [] if blocked else parse_best_per_type_table(html, type_name, url=expected_url, limit_per_type=50)

    print(f"URL: {url}")
    print(f"HTTP status: {status_code}")
    print(f"Cloudflare/block detected: {blocked}")
    print(f"Page title: {_page_title(html) if html else ''}")
    print(f"Detected table headers: {_detected_table_headers(html) if html else []}")
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

    if html and not rows:
        _print_likely_snippets(html)


if __name__ == "__main__":
    main()
