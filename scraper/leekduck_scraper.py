"""Scraper for https://leekduck.com/events/."""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from config import REQUEST_HEADERS, REQUEST_TIMEOUT_SECONDS
from scraper.normalize_events import clean_text, infer_category, parse_datetime


logger = logging.getLogger(__name__)

SOURCE_NAME = "Leek Duck"
EVENTS_URL = "https://leekduck.com/events/"
DETAIL_SECTION_ALIASES = {
    "bonuses": "bonuses",
    "event bonuses": "bonuses",
    "features": "features",
    "event features": "features",
    "spawns": "spawns",
    "wild encounters": "spawns",
    "featured wild encounter rotations": "spawns",
    "raids": "raids",
    "special raid bosses": "raids",
    "super mega raids": "raids",
    "research": "research",
    "special research": "research",
    "timed research": "research",
    "shiny": "shiny",
    "shiny pokemon": "shiny",
    "featured attacks": "featured_attacks",
    "incense encounters": "incense",
    "eggs": "eggs",
    "egg spawns": "eggs",
    "sales": "sales",
    "costumed pokemon": "features",
    "costumed pokémon": "features",
    "hourly habitats": "habitats",
}
DETAIL_PRIORITY_KEYS = (
    "features",
    "bonuses",
    "raids",
    "spawns",
    "incense",
    "research",
    "featured_attacks",
    "shiny",
    "eggs",
    "sales",
    "habitats",
)


def _normalize_detail_heading(value: str | None) -> str:
    text = clean_text(value)
    if not text:
        return ""
    text = text.replace("Pokémon", "Pokemon")
    text = re.sub(r"[^a-zA-Z0-9 ]+", " ", text.lower())
    return re.sub(r"\s+", " ", text).strip()


def _map_detail_section_name(heading: str | None) -> str | None:
    normalized = _normalize_detail_heading(heading)
    if not normalized:
        return None
    if normalized in DETAIL_SECTION_ALIASES:
        return DETAIL_SECTION_ALIASES[normalized]
    for key, mapped in DETAIL_SECTION_ALIASES.items():
        if normalized == key or normalized in key or key in normalized:
            return mapped
    return None


def _unique_preserve_order(items: list[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for item in items:
        cleaned = clean_text(item)
        if not cleaned:
            continue
        marker = cleaned.casefold()
        if marker in seen:
            continue
        seen.add(marker)
        ordered.append(cleaned)
    return ordered


def _extract_list_text(container: BeautifulSoup | Any) -> list[str]:
    items: list[str] = []
    for item in container.select("li"):
        text = clean_text(item.get_text(" ", strip=True))
        if text:
            items.append(text)
    if items:
        return _unique_preserve_order(items)
    text = clean_text(container.get_text(" ", strip=True))
    return [text] if text else []


def _extract_named_grid_items(container: BeautifulSoup | Any) -> list[str]:
    items: list[str] = []
    for node in container.select("img[alt], [title]"):
        text = clean_text(node.get("alt") or node.get("title"))
        if text and len(text) <= 80:
            items.append(text)
    return _unique_preserve_order(items)


def _collect_section_items(heading: Any) -> list[str]:
    items: list[str] = []
    sibling = heading.find_next_sibling()
    steps = 0
    while sibling is not None and steps < 8:
        if getattr(sibling, "name", None) in {"h1", "h2", "h3", "h4"}:
            break
        if getattr(sibling, "name", None) in {"ul", "ol"}:
            items.extend(_extract_list_text(sibling))
        elif getattr(sibling, "name", None) in {"div", "section"}:
            items.extend(_extract_list_text(sibling))
            items.extend(_extract_named_grid_items(sibling))
        elif getattr(sibling, "name", None) == "p":
            text = clean_text(sibling.get_text(" ", strip=True))
            if text:
                items.append(text)
        sibling = sibling.find_next_sibling()
        steps += 1
    return _unique_preserve_order(items)


def _build_summary_text(title: str | None, sections: dict[str, list[str]]) -> str:
    lines: list[str] = []
    if title:
        lines.append(title)
    for key in DETAIL_PRIORITY_KEYS:
        values = _unique_preserve_order(sections.get(key, []))
        if not values:
            continue
        label = key.replace("_", " ").title()
        lines.append(f"{label}:")
        lines.extend(f"- {value}" for value in values[:12])
    return "\n".join(lines).strip()


def _extract_json_ld_events(soup: BeautifulSoup) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for script in soup.find_all("script", type="application/ld+json"):
        raw = script.string or script.get_text(strip=True)
        if not raw:
            continue
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue

        candidates = data if isinstance(data, list) else [data]
        for candidate in candidates:
            if isinstance(candidate, dict) and "@graph" in candidate:
                candidates.extend(candidate.get("@graph") or [])
                continue
            if not isinstance(candidate, dict):
                continue
            event_type = candidate.get("@type")
            if event_type != "Event" and not (isinstance(event_type, list) and "Event" in event_type):
                continue
            title = clean_text(candidate.get("name"))
            if not title:
                continue
            description = clean_text(candidate.get("description"))
            events.append(
                {
                    "source": SOURCE_NAME,
                    "title": title,
                    "category": infer_category(title, description),
                    "start_time": parse_datetime(candidate.get("startDate")),
                    "end_time": parse_datetime(candidate.get("endDate")),
                    "url": urljoin(EVENTS_URL, candidate.get("url") or ""),
                    "summary": description,
                    "raw_text": description,
                    "scraped_at": datetime.now(timezone.utc).isoformat(),
                }
            )
    return events


def _extract_event_cards(soup: BeautifulSoup) -> list[dict[str, Any]]:
    """Fallback parser for visible Leek Duck event cards.

    Leek Duck exposes useful event metadata on each
    ``.event-header-item-wrapper`` element. Reading those wrappers is much more
    reliable than scraping every ``/events/`` link because it keeps each title,
    category, URL, start date, and end date scoped to one card.
    """

    events: list[dict[str, Any]] = []
    cards = soup.select(".event-header-item-wrapper")
    for card in cards:
        link = card.select_one("a[href*='/events/']")
        if not link:
            continue

        href = link.get("href")
        url = urljoin(EVENTS_URL, href or "")
        if url.rstrip("/") == EVENTS_URL.rstrip("/"):
            continue

        raw_text = clean_text(card.get_text(" ", strip=True))
        heading = card.select_one("h2")
        title = clean_text(heading.get_text(" ", strip=True) if heading else None)
        if not title or len(title) < 3:
            continue

        badge = card.select_one(".event-tag-badge")
        category = clean_text(badge.get_text(" ", strip=True) if badge else None)
        local_time = str(card.get("data-event-local-time", "")).lower() == "true"
        start_time = parse_datetime(card.get("data-event-start-date-check"))
        end_time = parse_datetime(card.get("data-event-end-date") or card.get("data-event-date-sort"))

        events.append(
            {
                "source": SOURCE_NAME,
                "title": title,
                "category": category or infer_category(title, raw_text),
                "start_time": start_time,
                "end_time": end_time,
                "local_time": local_time,
                "url": url,
                "summary": raw_text[:500] if raw_text else None,
                "raw_text": raw_text,
                "scraped_at": datetime.now(timezone.utc).isoformat(),
            }
        )
    return events


def scrape_events() -> list[dict[str, Any]]:
    """Fetch and parse Leek Duck event data."""

    logger.info("Scraping %s", EVENTS_URL)
    response = requests.get(EVENTS_URL, headers=REQUEST_HEADERS, timeout=REQUEST_TIMEOUT_SECONDS)
    response.raise_for_status()

    soup = BeautifulSoup(response.text, "html.parser")
    events = _extract_json_ld_events(soup)
    if not events:
        events = _extract_event_cards(soup)

    logger.info("Found %d Leek Duck event candidates", len(events))
    return events


def parse_event_detail_html(html: str, event_url: str, event_title: str | None = None) -> dict[str, Any] | None:
    """Parse a LeekDuck event detail page into cached summary text and sections."""

    soup = BeautifulSoup(html, "html.parser")
    page_title = clean_text(event_title)
    if not page_title:
        title_node = soup.select_one("h1") or soup.select_one("title")
        page_title = clean_text(title_node.get_text(" ", strip=True) if title_node else None)

    sections: dict[str, list[str]] = {}
    for heading in soup.select("h2, h3, h4"):
        section_key = _map_detail_section_name(heading.get_text(" ", strip=True))
        if not section_key:
            continue
        items = _collect_section_items(heading)
        if not items:
            continue
        sections.setdefault(section_key, [])
        sections[section_key].extend(items)
        sections[section_key] = _unique_preserve_order(sections[section_key])

    if not sections:
        return None

    fetched_at = datetime.now(timezone.utc).isoformat()
    summary_text = _build_summary_text(page_title, sections)
    return {
        "event_url": event_url,
        "event_title": page_title,
        "fetched_at": fetched_at,
        "summary_text": summary_text,
        "sections_json": sections,
    }


def scrape_event_detail(event_url: str, event_title: str | None = None) -> dict[str, Any] | None:
    """Fetch and parse one LeekDuck event detail page."""

    logger.info("Scraping Leek Duck event detail: %s", event_url)
    response = requests.get(event_url, headers=REQUEST_HEADERS, timeout=REQUEST_TIMEOUT_SECONDS)
    response.raise_for_status()
    return parse_event_detail_html(response.text, event_url=event_url, event_title=event_title)
