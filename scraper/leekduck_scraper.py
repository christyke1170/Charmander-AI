"""Scraper for https://leekduck.com/events/."""

from __future__ import annotations

import json
import logging
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
