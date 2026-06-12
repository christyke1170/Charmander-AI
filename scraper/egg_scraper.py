"""Scraper for cached Pokémon GO egg pools from https://leekduck.com/eggs/."""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Any

import requests
from bs4 import BeautifulSoup, Tag

from config import REQUEST_HEADERS, REQUEST_TIMEOUT_SECONDS


logger = logging.getLogger(__name__)

EGGS_URL = "https://leekduck.com/eggs/"
SOURCE_NAME = "leekduck_eggs"
EGG_HEADING_PATTERN = re.compile(r"\bEggs\b", re.IGNORECASE)
DISTANCE_PATTERN = re.compile(r"\b(\d{1,2})\s*km\b", re.IGNORECASE)


def _clean_text(value: Any) -> str | None:
    if value is None:
        return None
    text = re.sub(r"\s+", " ", str(value)).strip()
    return text or None


def detect_egg_distance_km(pool_name: str | None) -> int | None:
    """Return the egg distance in km from a pool heading when present."""

    match = DISTANCE_PATTERN.search(pool_name or "")
    if not match:
        return None
    try:
        return int(match.group(1))
    except ValueError:
        return None


def detect_pool_type(pool_name: str | None) -> str:
    """Infer the stable pool_type label from a LeekDuck egg pool heading."""

    normalized = (pool_name or "").lower()
    if "adventure sync" in normalized:
        return "adventure_sync"
    if "route gift" in normalized or "from route" in normalized:
        return "route_gift"
    if "event" in normalized:
        return "event"
    if "eggs" in normalized:
        return "standard"
    return "unknown"


def is_blocked_page(html: str, status_code: int | None = None) -> bool:
    """Return whether the response looks like a block/challenge page instead of egg HTML."""

    lowered = (html or "").lower()
    if status_code in {403, 429, 503}:
        return True
    blocked_markers = ("cloudflare", "challenge-platform", "just a moment", "access denied")
    return any(marker in lowered for marker in blocked_markers) and "egg-grid" not in lowered


def _is_egg_heading(tag: Tag) -> bool:
    if tag.name not in {"h1", "h2", "h3", "h4", "h5", "h6"}:
        return False
    text = _clean_text(tag.get_text(" ", strip=True)) or ""
    return bool(EGG_HEADING_PATTERN.search(text)) and text.lower() != "current eggs hatches"


def _find_next_egg_grid(heading: Tag) -> Tag | None:
    for sibling in heading.next_siblings:
        if not isinstance(sibling, Tag):
            continue
        if _is_egg_heading(sibling):
            return None
        if sibling.name == "ul" and "egg-grid" in (sibling.get("class") or []):
            return sibling
        nested = sibling.select_one("ul.egg-grid")
        if nested:
            return nested
    return None


def _find_section_notes(heading: Tag) -> str | None:
    """Return notes/description immediately between an egg heading and its egg grid."""

    for sibling in heading.next_siblings:
        if not isinstance(sibling, Tag):
            continue
        if _is_egg_heading(sibling):
            return None
        if sibling.name == "ul" and "egg-grid" in (sibling.get("class") or []):
            return None
        if sibling.select_one("ul.egg-grid"):
            return None
        if "egg-section" in (sibling.get("class") or []):
            return _clean_text(sibling.get_text(" ", strip=True))
    return None


def _extract_cp_text(card: Tag) -> str | None:
    cp_node = card.select_one(".cp-range")
    if not cp_node:
        return None
    text = _clean_text(cp_node.get_text(" ", strip=True))
    if not text:
        return None
    text = re.sub(r"^CP\s*", "CP ", text, flags=re.IGNORECASE)
    return text


def _extract_rarity_text(card: Tag) -> str | None:
    rarity = card.select_one(".rarity")
    if not rarity:
        return None
    egg_count = len(rarity.select("svg.mini-egg, .mini-egg"))
    if egg_count <= 0:
        return _clean_text(rarity.get_text(" ", strip=True))
    return f"{egg_count} egg" if egg_count == 1 else f"{egg_count} eggs"


def parse_leekduck_egg_html(html: str, scraped_at: str | None = None) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Parse LeekDuck egg page HTML into cache rows and parser stats."""

    scraped_at = scraped_at or datetime.now(timezone.utc).isoformat()
    stats: dict[str, Any] = {
        "sections_found": 0,
        "rows_parsed": 0,
        "parse_failures": 0,
        "pool_names": [],
    }
    rows: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    soup = BeautifulSoup(html or "", "html.parser")
    headings = [tag for tag in soup.find_all(["h1", "h2", "h3", "h4", "h5", "h6"]) if _is_egg_heading(tag)]

    for heading in headings:
        pool_name = _clean_text(heading.get_text(" ", strip=True))
        if not pool_name:
            continue
        grid = _find_next_egg_grid(heading)
        if not grid:
            stats["parse_failures"] += 1
            logger.warning("No egg-grid found for LeekDuck egg heading %r", pool_name)
            continue

        stats["sections_found"] += 1
        stats["pool_names"].append(pool_name)
        distance = detect_egg_distance_km(pool_name)
        pool_type = detect_pool_type(pool_name)
        notes = _find_section_notes(heading)

        for card in grid.select("li.pokemon-card"):
            try:
                name_node = card.select_one(".name")
                image = card.select_one("img[alt]")
                pokemon_name = _clean_text(name_node.get_text(" ", strip=True) if name_node else None) or _clean_text(
                    image.get("alt") if image else None
                )
                if not pokemon_name:
                    stats["parse_failures"] += 1
                    continue
                dedupe_key = (pool_name.lower(), pokemon_name.lower())
                if dedupe_key in seen:
                    continue
                seen.add(dedupe_key)
                rows.append(
                    {
                        "source": SOURCE_NAME,
                        "pool_name": pool_name,
                        "egg_distance_km": distance,
                        "pool_type": pool_type,
                        "pokemon_name": pokemon_name,
                        "cp_text": _extract_cp_text(card),
                        "shiny_available": 1 if card.select_one(".shiny-icon, svg[class*='shiny']") else 0,
                        "rarity_text": _extract_rarity_text(card),
                        "notes": notes,
                        "url": EGGS_URL,
                        "scraped_at": scraped_at,
                    }
                )
            except Exception as exc:  # Keep a single changed card from losing the whole cache update.
                stats["parse_failures"] += 1
                logger.warning("Failed to parse LeekDuck egg card under %r: %s", pool_name, exc)

    stats["rows_parsed"] = len(rows)
    return rows, stats


def scrape_leekduck_eggs() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Fetch and parse LeekDuck egg pool data.

    Returns zero rows with diagnostic stats on network, blocking, or parse failures.
    Callers should only mark cache metadata fresh when the returned row list is non-empty.
    """

    stats: dict[str, Any] = {
        "status_code": None,
        "sections_found": 0,
        "rows_parsed": 0,
        "parse_failures": 0,
        "pool_names": [],
        "blocked": False,
        "error": None,
    }
    try:
        logger.info("Scraping %s", EGGS_URL)
        response = requests.get(EGGS_URL, headers=REQUEST_HEADERS, timeout=REQUEST_TIMEOUT_SECONDS)
        stats["status_code"] = response.status_code
        if is_blocked_page(response.text, response.status_code):
            stats["blocked"] = True
            stats["error"] = f"LeekDuck eggs page appears blocked or challenged (status {response.status_code})"
            logger.warning(stats["error"])
            return [], stats
        response.raise_for_status()
        rows, parse_stats = parse_leekduck_egg_html(response.text)
        stats.update(parse_stats)
        if not rows:
            stats["error"] = "Parsed zero egg rows from LeekDuck eggs page"
            logger.warning("LeekDuck egg scrape parsed zero rows. Stats: %s", stats)
        else:
            logger.info("Parsed %d LeekDuck egg rows across %d section(s)", len(rows), stats["sections_found"])
        return rows, stats
    except Exception as exc:
        stats["error"] = str(exc)
        logger.exception("Failed to scrape LeekDuck egg pools: %s", exc)
        return [], stats
