"""Scraper for PvPoke league rankings.

This module is only used by explicit/manual/background cache update flows. Normal
Discord questions should read from SQLite and never scrape live.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from typing import Any

import requests

from config import REQUEST_HEADERS, REQUEST_TIMEOUT_SECONDS
from database.pvp_rankings_db import LEAGUE_CP, SOURCE_NAME


logger = logging.getLogger(__name__)

PVP_LEAGUES = {
    "great": {
        "cp": 1500,
        "page_url": "https://pvpoke.com/rankings/all/1500/overall/",
        "json_url": "https://pvpoke.com/data/rankings/all/overall/rankings-1500.json",
    },
    "ultra": {
        "cp": 2500,
        "page_url": "https://pvpoke.com/rankings/all/2500/overall/",
        "json_url": "https://pvpoke.com/data/rankings/all/overall/rankings-2500.json",
    },
    "master": {
        "cp": 10000,
        "page_url": "https://pvpoke.com/rankings/all/10000/overall/",
        "json_url": "https://pvpoke.com/data/rankings/all/overall/rankings-10000.json",
    },
}
GAMEMASTER_URL = "https://pvpoke.com/data/gamemaster.min.json"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _empty_stats(stage: str = "requests_json") -> dict[str, Any]:
    return {
        "pages_checked": 0,
        "pages_parsed": 0,
        "pages_blocked": 0,
        "rows_parsed": 0,
        "league_rows": {league: 0 for league in PVP_LEAGUES},
        "parse_failures": 0,
        "scraper_stage": stage,
        "errors": [],
    }


def _clean_text(value: Any) -> str | None:
    if value is None:
        return None
    text = re.sub(r"\s+", " ", str(value)).strip()
    return text or None


def _title_type(value: Any) -> str | None:
    text = _clean_text(value)
    if not text or text.lower() == "none":
        return None
    return text.title()


def _fetch_json(url: str) -> Any:
    response = requests.get(url, headers=REQUEST_HEADERS, timeout=REQUEST_TIMEOUT_SECONDS)
    response.raise_for_status()
    return response.json()


def _fetch_site_version() -> str | None:
    try:
        response = requests.get(PVP_LEAGUES["great"]["page_url"], headers=REQUEST_HEADERS, timeout=REQUEST_TIMEOUT_SECONDS)
        response.raise_for_status()
    except requests.RequestException:
        return None
    match = re.search(r"siteVersion\s*=\s*[\"']([^\"']+)[\"']", response.text)
    return match.group(1) if match else None


def _versioned(url: str, site_version: str | None) -> str:
    return f"{url}?v={site_version}" if site_version else url


def _build_gamemaster_indexes(gamemaster: dict[str, Any]) -> tuple[dict[str, dict[str, Any]], dict[str, str]]:
    pokemon_index = {str(row.get("speciesId")): row for row in gamemaster.get("pokemon", []) if row.get("speciesId")}
    move_index = {str(row.get("moveId")): str(row.get("name") or row.get("moveId")) for row in gamemaster.get("moves", []) if row.get("moveId")}
    return pokemon_index, move_index


def _move_name(move_id: Any, move_index: dict[str, str]) -> str | None:
    if not move_id:
        return None
    return move_index.get(str(move_id), str(move_id).replace("_", " ").title())


def _ranking_url(league: str, species_id: str | None = None) -> str:
    url = str(PVP_LEAGUES[league]["page_url"])
    if species_id:
        return f"{url}{species_id}/"
    return url


def parse_pvpoke_ranking_json(
    ranking_data: list[dict[str, Any]],
    *,
    league: str,
    pokemon_index: dict[str, dict[str, Any]],
    move_index: dict[str, str],
    scraped_at: str,
    limit: int,
) -> list[dict[str, Any]]:
    """Parse one PvPoke ranking JSON payload into normalized cache rows.

    This parser is intentionally separate from fetching so tests can validate the
    extraction logic with a small fixture and normal Discord paths can continue
    to read only from SQLite.
    """

    rows: list[dict[str, Any]] = []
    seen: set[tuple[str, int]] = set()
    for index, ranking in enumerate(ranking_data[: max(limit, 0)], start=1):
        species_id = str(ranking.get("speciesId") or "")
        pokemon = pokemon_index.get(species_id, {})
        name = _clean_text(ranking.get("speciesName") or pokemon.get("speciesName"))
        if not name:
            continue
        dedupe_key = (species_id or name.lower(), index)
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        types = list(pokemon.get("types") or [])
        moveset = list(ranking.get("moveset") or [])
        rows.append(
            {
                "source": SOURCE_NAME,
                "league": league,
                "league_cp": LEAGUE_CP[league],
                "rank": index,
                "pokemon_name": name,
                "form": "",
                "type_1": _title_type(types[0] if len(types) > 0 else None),
                "type_2": _title_type(types[1] if len(types) > 1 else None),
                "fast_move": _move_name(moveset[0], move_index) if len(moveset) > 0 else None,
                "charged_move_1": _move_name(moveset[1], move_index) if len(moveset) > 1 else None,
                "charged_move_2": _move_name(moveset[2], move_index) if len(moveset) > 2 else None,
                "score": _clean_text(ranking.get("score")),
                "url": _ranking_url(league, species_id),
                "scraped_at": scraped_at,
            }
        )
    return rows


def _rows_from_ranking_json(
    ranking_data: list[dict[str, Any]],
    *,
    league: str,
    pokemon_index: dict[str, dict[str, Any]],
    move_index: dict[str, str],
    scraped_at: str,
    limit: int,
) -> list[dict[str, Any]]:
    return parse_pvpoke_ranking_json(
        ranking_data,
        league=league,
        pokemon_index=pokemon_index,
        move_index=move_index,
        scraped_at=scraped_at,
        limit=limit,
    )


def _scrape_with_static_json(limit_per_league: int) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    stats = _empty_stats("requests_json")
    rows: list[dict[str, Any]] = []
    site_version = _fetch_site_version()
    try:
        stats["pages_checked"] += 1
        gamemaster = _fetch_json(_versioned(GAMEMASTER_URL, site_version))
        pokemon_index, move_index = _build_gamemaster_indexes(gamemaster)
        stats["pages_parsed"] += 1
    except Exception as exc:
        stats["parse_failures"] += 1
        stats["errors"].append(f"gamemaster: {exc}")
        return [], stats

    timestamp = _utc_now()
    for league, info in PVP_LEAGUES.items():
        try:
            stats["pages_checked"] += 1
            ranking_data = _fetch_json(_versioned(str(info["json_url"]), site_version))
            if not isinstance(ranking_data, list):
                raise ValueError("ranking JSON was not a list")
            league_rows = _rows_from_ranking_json(
                ranking_data,
                league=league,
                pokemon_index=pokemon_index,
                move_index=move_index,
                scraped_at=timestamp,
                limit=limit_per_league,
            )
            rows.extend(league_rows)
            stats["league_rows"][league] = len(league_rows)
            if league_rows:
                stats["pages_parsed"] += 1
            else:
                stats["parse_failures"] += 1
        except requests.HTTPError as exc:
            response = getattr(exc, "response", None)
            if response is not None and response.status_code in {403, 429, 503}:
                stats["pages_blocked"] += 1
            stats["parse_failures"] += 1
            stats["errors"].append(f"{league}: {exc}")
        except Exception as exc:
            stats["parse_failures"] += 1
            stats["errors"].append(f"{league}: {exc}")

    stats["rows_parsed"] = len(rows)
    return rows, stats


def _scrape_with_browser(limit_per_league: int) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    stats = _empty_stats("browser_json")
    try:
        from playwright.sync_api import Error as PlaywrightError
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        stats["errors"].append(f"playwright-not-installed: {exc}")
        return [], stats

    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page()
            try:
                def browser_json(url: str) -> Any:
                    stats["pages_checked"] += 1
                    page.goto(url, wait_until="domcontentloaded", timeout=45_000)
                    text = page.locator("body").inner_text(timeout=10_000)
                    return json.loads(text)

                gamemaster = browser_json(GAMEMASTER_URL)
                pokemon_index, move_index = _build_gamemaster_indexes(gamemaster)
                stats["pages_parsed"] += 1
                timestamp = _utc_now()
                rows: list[dict[str, Any]] = []
                for league, info in PVP_LEAGUES.items():
                    ranking_data = browser_json(str(info["json_url"]))
                    if not isinstance(ranking_data, list):
                        stats["parse_failures"] += 1
                        continue
                    league_rows = _rows_from_ranking_json(
                        ranking_data,
                        league=league,
                        pokemon_index=pokemon_index,
                        move_index=move_index,
                        scraped_at=timestamp,
                        limit=limit_per_league,
                    )
                    rows.extend(league_rows)
                    stats["league_rows"][league] = len(league_rows)
                    if league_rows:
                        stats["pages_parsed"] += 1
                    else:
                        stats["parse_failures"] += 1
                stats["rows_parsed"] = len(rows)
                return rows, stats
            finally:
                browser.close()
    except PlaywrightError as exc:
        stats["errors"].append(str(exc))
    except Exception as exc:
        stats["errors"].append(str(exc))
    return [], stats


def scrape_pvpoke_rankings(limit_per_league: int = 200) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Scrape top PvPoke rankings for Great, Ultra, and Master League."""

    rows, stats = _scrape_with_static_json(limit_per_league)
    expected_min = max(1, min(limit_per_league, 1)) * len(PVP_LEAGUES)
    if len(rows) >= expected_min and all(stats["league_rows"].get(league, 0) > 0 for league in PVP_LEAGUES):
        logger.info("PvPoke static JSON scrape stats: %s", stats)
        return rows, stats

    browser_rows, browser_stats = _scrape_with_browser(limit_per_league)
    if browser_rows:
        logger.info("PvPoke browser scrape stats: %s", browser_stats)
        return browser_rows, browser_stats

    stats["browser_stats"] = browser_stats
    stats["errors"].extend(browser_stats.get("errors", []))
    logger.warning("PvPoke scrape returned zero/incomplete rows. Stats: %s", stats)
    return rows, stats
