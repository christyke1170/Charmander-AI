"""Scraper for Pokémon GO Hub DB best-per-type raid attacker tables.

This module is only used by explicit/manual cache update flows. Normal Discord
questions should read from the local SQLite cache and never scrape live pages.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Any

import requests
from bs4 import BeautifulSoup, Tag

from config import REQUEST_HEADERS, REQUEST_TIMEOUT_SECONDS


logger = logging.getLogger(__name__)

POKEMON_TYPES = [
    "normal",
    "fire",
    "water",
    "electric",
    "grass",
    "ice",
    "fighting",
    "poison",
    "ground",
    "flying",
    "psychic",
    "bug",
    "rock",
    "ghost",
    "dragon",
    "dark",
    "steel",
    "fairy",
]

BEST_PER_TYPE_URL_TEMPLATE = "https://db.pokemongohub.net/pokemon-list/best-per-type/{type_name}"
SOURCE_NAME = "pokemongohub_best_per_type"
REQUESTED_TABLE_HEADERS = ("#", "Name", "Fast Attack", "Charged Attack", "DPS", "TDO", "Score")
REQUEST_DELAY_SECONDS = 0.75

BLOCK_MARKERS = (
    "cf-browser-verification",
    "cloudflare",
    "checking your browser",
    "just a moment",
    "attention required",
    "challenge-platform",
)


def build_best_per_type_url(type_name: str) -> str:
    """Return the Pokémon GO Hub DB best-per-type URL for a type."""

    return BEST_PER_TYPE_URL_TEMPLATE.format(type_name=type_name.strip().lower())


def is_blocked_page(html: str | None, status_code: int | None = None) -> bool:
    """Return True when a response appears to be a Cloudflare/challenge block."""

    lowered = (html or "").lower()
    has_marker = any(marker in lowered for marker in BLOCK_MARKERS)
    if has_marker:
        return True
    return bool(status_code in {403, 429, 503} and has_marker)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _clean_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, Tag):
        text = value.get_text(" ", strip=True)
    else:
        text = str(value)
    return re.sub(r"\s+", " ", text).strip()


def _normalize_header(value: str) -> str:
    text = _clean_text(value).lower().replace("#", "ranknumber")
    return re.sub(r"[^a-z0-9]+", "", text)


def _rank_from_text(value: str) -> int | None:
    match = re.search(r"\d+", value or "")
    if not match:
        return None
    try:
        return int(match.group(0))
    except ValueError:
        return None


def _table_headers(table: Tag) -> list[str]:
    header_row = table.find("tr")
    if header_row is None:
        return []
    header_cells = header_row.find_all("th")
    if not header_cells:
        header_cells = header_row.find_all(["th", "td"])
    return [_clean_text(cell) for cell in header_cells]


def _header_index_map(headers: list[str]) -> dict[str, int]:
    normalized_to_index = {_normalize_header(header): index for index, header in enumerate(headers)}
    aliases = {
        "rank": ("ranknumber", "rank", ""),
        "pokemon_name": ("name", "pokemon", "pokemonname"),
        "fast_move": ("fastattack", "fastmove"),
        "charged_move": ("chargedattack", "chargedmove", "chargeattack", "chargemove"),
        "dps": ("dps",),
        "tdo": ("tdo",),
        "score": ("score",),
    }
    index_map: dict[str, int] = {}
    for field, possible_headers in aliases.items():
        for possible_header in possible_headers:
            if possible_header in normalized_to_index:
                index_map[field] = normalized_to_index[possible_header]
                break
    return index_map


def _is_requested_table(headers: list[str]) -> bool:
    index_map = _header_index_map(headers)
    return all(field in index_map for field in ("rank", "pokemon_name", "fast_move", "charged_move", "dps", "tdo", "score"))


def _row_cells(row: Tag) -> list[str]:
    cells = row.find_all("td")
    if not cells:
        cells = row.find_all(["th", "td"])
    return [_clean_text(cell) for cell in cells]


def _cell(cells: list[str], index_map: dict[str, int], field: str) -> str:
    index = index_map.get(field)
    if index is None or index >= len(cells):
        return ""
    return cells[index]


def _make_row(
    *,
    type_name: str,
    url: str,
    scraped_at: str,
    rank: int | None,
    pokemon_name: str,
    fast_move: str,
    charged_move: str,
    dps: str,
    tdo: str,
    score: str,
) -> dict[str, Any]:
    type_label = type_name.title()
    rank_label = f"#{rank}" if rank is not None else "unranked"
    return {
        "source": SOURCE_NAME,
        "ranking_scope": f"type:{type_name}",
        "pokemon_name": pokemon_name,
        "form": "",
        "pokemon_type": type_name,
        "rank": rank,
        "fast_move": fast_move or None,
        "charged_move": charged_move or None,
        "score": score or None,
        "dps": dps or None,
        "tdo": tdo or None,
        "summary": f"Rank {rank_label} {type_label}-type raid attacker on Pokémon GO Hub best-per-type list.",
        "url": url,
        "scraped_at": scraped_at,
    }


def parse_best_per_type_table(
    html: str,
    type_name: str,
    url: str | None = None,
    limit_per_type: int = 50,
    scraped_at: str | None = None,
) -> list[dict[str, Any]]:
    """Parse the exact #/Name/Fast Attack/Charged Attack/DPS/TDO/Score table."""

    if not html or is_blocked_page(html):
        return []

    normalized_type = type_name.strip().lower()
    page_url = url or build_best_per_type_url(normalized_type)
    timestamp = scraped_at or _utc_now()
    soup = BeautifulSoup(html, "html.parser")

    for table in soup.find_all("table"):
        if not isinstance(table, Tag):
            continue
        headers = _table_headers(table)
        if not _is_requested_table(headers):
            continue

        index_map = _header_index_map(headers)
        body = table.find("tbody")
        rows = body.find_all("tr") if isinstance(body, Tag) else table.find_all("tr")[1:]
        parsed_rows: list[dict[str, Any]] = []
        seen: set[tuple[str, str, int | None, str, str]] = set()

        for row in rows:
            if not isinstance(row, Tag):
                continue
            cells = _row_cells(row)
            if not cells:
                continue

            pokemon_name = _cell(cells, index_map, "pokemon_name")
            if not pokemon_name:
                continue
            rank = _rank_from_text(_cell(cells, index_map, "rank"))
            fast_move = _cell(cells, index_map, "fast_move")
            charged_move = _cell(cells, index_map, "charged_move")
            dps = _cell(cells, index_map, "dps")
            tdo = _cell(cells, index_map, "tdo")
            score = _cell(cells, index_map, "score")

            dedupe_key = (f"type:{normalized_type}", pokemon_name, rank, fast_move, charged_move)
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)
            parsed_rows.append(
                _make_row(
                    type_name=normalized_type,
                    url=page_url,
                    scraped_at=timestamp,
                    rank=rank,
                    pokemon_name=pokemon_name,
                    fast_move=fast_move,
                    charged_move=charged_move,
                    dps=dps,
                    tdo=tdo,
                    score=score,
                )
            )
            if len(parsed_rows) >= limit_per_type:
                break
        return parsed_rows

    return []


def parse_best_per_type_fallback(
    html: str,
    type_name: str,
    url: str | None = None,
    limit_per_type: int = 50,
    scraped_at: str | None = None,
) -> list[dict[str, Any]]:
    """Defensive fallback parser.

    Currently returns rows only for confidently detected table data. It does not
    invent rankings from unrelated navigation/card links.
    """

    # Keep this intentionally conservative: without the exact known table, the
    # site markup must be inspected before extracting partial card/list data.
    return []


def parse_best_per_type_page(
    html: str,
    type_name: str,
    url: str | None = None,
    limit_per_type: int = 50,
    scraped_at: str | None = None,
) -> list[dict[str, Any]]:
    """Parse one best-per-type page using exact table parsing, then fallback."""

    rows = parse_best_per_type_table(html, type_name, url, limit_per_type, scraped_at)
    if rows:
        return rows
    return parse_best_per_type_fallback(html, type_name, url, limit_per_type, scraped_at)


def fetch_best_per_type_page(type_name: str) -> tuple[str, int | None, str | None]:
    """Fetch a best-per-type page and return (url, status_code, html)."""

    url = build_best_per_type_url(type_name)
    try:
        response = requests.get(url, headers=REQUEST_HEADERS, timeout=REQUEST_TIMEOUT_SECONDS)
    except requests.RequestException as exc:
        logger.warning("Failed to fetch Pokémon GO Hub best-per-type URL %s: %s", url, exc)
        return url, None, None
    return url, response.status_code, response.text


def _empty_stats() -> dict[str, Any]:
    return {
        "pages_checked": 0,
        "pages_blocked": 0,
        "pages_parsed": 0,
        "rows_parsed": 0,
        "scraped_rows": 0,
        "type_rows": {},
        "parse_failures": 0,
        "status_codes": {},
        "blocked_types": [],
        "parsed_types": [],
    }


def scrape_best_attackers_per_type(limit_per_type: int = 50) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Scrape Pokémon GO Hub best-per-type ranking tables for all 18 types."""

    rows: list[dict[str, Any]] = []
    stats = _empty_stats()

    for type_name in POKEMON_TYPES:
        url, status_code, html = fetch_best_per_type_page(type_name)
        stats["pages_checked"] += 1
        stats["status_codes"][type_name] = status_code

        if html is None:
            stats["parse_failures"] += 1
            stats["type_rows"][type_name] = 0
            continue

        if is_blocked_page(html, status_code):
            stats["pages_blocked"] += 1
            stats["blocked_types"].append(type_name)
            stats["type_rows"][type_name] = 0
            continue

        if status_code is not None and status_code >= 400:
            stats["parse_failures"] += 1
            stats["type_rows"][type_name] = 0
            continue

        page_rows = parse_best_per_type_page(
            html,
            type_name,
            url=url,
            limit_per_type=limit_per_type,
            scraped_at=_utc_now(),
        )
        stats["type_rows"][type_name] = len(page_rows)
        if page_rows:
            rows.extend(page_rows)
            stats["pages_parsed"] += 1
            stats["rows_parsed"] += len(page_rows)
            stats["parsed_types"].append(type_name)
        else:
            stats["parse_failures"] += 1

    stats["scraped_rows"] = len(rows)
    logger.info("Pokémon GO Hub best-per-type scrape stats: %s", stats)
    return rows, stats
