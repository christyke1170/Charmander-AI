"""Debug one Pokémon GO Hub Pokémon/form page through the shared requests/browser scraper flow.

This script writes page dumps only and never writes to SQLite.
"""

from __future__ import annotations

import argparse

import config
from scraper.pokemon_go_hub_forms_scraper import load_pokemon_go_hub_form_page


DEBUG_DIR = config.BASE_DIR / "debug"


def _safe_print(value: str) -> None:
    text = str(value)
    try:
        print(text)
    except UnicodeEncodeError:
        print(text.encode("ascii", errors="replace").decode("ascii"))


def main() -> None:
    parser = argparse.ArgumentParser(description="Debug scraping of a Pokémon GO Hub Pokémon/form page.")
    parser.add_argument("--url", required=True, help="Exact Pokémon GO Hub Pokémon page URL to open.")
    parser.add_argument("--dex", required=True, type=int, help="Dex number to associate with the page.")
    parser.add_argument("--form-hint", default=None, help="Optional form hint such as Shadow or Mega_X.")
    parser.add_argument(
        "--pause",
        action="store_true",
        help="Pause after opening the browser so you can solve Cloudflare before parsing continues.",
    )
    args = parser.parse_args()

    DEBUG_DIR.mkdir(parents=True, exist_ok=True)
    result = load_pokemon_go_hub_form_page(
        args.url,
        args.dex,
        args.form_hint,
        use_browser=True,
        pause_browser=args.pause,
        include_debug_artifacts=True,
    )
    row = result.get("row")
    stats = result.get("stats") or {}
    html = str(stats.get("html") or "")
    visible_text = str(stats.get("visible_text") or "")
    screenshot_bytes = stats.get("screenshot_bytes")

    html_path = DEBUG_DIR / f"pokemon_form_{args.dex}.html"
    text_path = DEBUG_DIR / f"pokemon_form_{args.dex}.txt"
    screenshot_path = DEBUG_DIR / f"pokemon_form_{args.dex}.png"
    html_path.write_text(html, encoding="utf-8")
    text_path.write_text(visible_text, encoding="utf-8")
    if isinstance(screenshot_bytes, (bytes, bytearray)):
        screenshot_path.write_bytes(bytes(screenshot_bytes))
    else:
        screenshot_path.write_bytes(b"")

    _safe_print(f"Final URL: {stats.get('final_url') or stats.get('url') or args.url}")
    _safe_print(f"Page title: {stats.get('page_title', '')}")
    _safe_print(f"Blocked detected: {bool(stats.get('blocked'))}")
    _safe_print(f"Real Pokémon page content: {bool(stats.get('real_content_detected'))}")
    _safe_print(f"Parsed row: {row}")
    _safe_print("First 2000 visible characters:")
    _safe_print(visible_text[:2000])
    _safe_print(f"Saved HTML: {html_path}")
    _safe_print(f"Saved text: {text_path}")
    _safe_print(f"Saved screenshot: {screenshot_path}")


if __name__ == "__main__":
    main()