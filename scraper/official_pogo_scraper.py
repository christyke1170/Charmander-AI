"""Scraper for official Pokémon GO news at https://pokemongolive.com/news."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from config import REQUEST_HEADERS, REQUEST_TIMEOUT_SECONDS
from scraper.normalize_events import clean_text, infer_category, parse_datetime


logger = logging.getLogger(__name__)

SOURCE_NAME = "Pokémon GO Live"
NEWS_URL = "https://pokemongolive.com/news"


def scrape_events() -> list[dict[str, str | None]]:
    """Fetch official Pokémon GO news items.

    The official site is primarily a news feed rather than a structured event
    calendar, so dates may represent article publication dates when event dates
    are not available in static HTML.
    """

    logger.info("Scraping %s", NEWS_URL)
    response = requests.get(NEWS_URL, headers=REQUEST_HEADERS, timeout=REQUEST_TIMEOUT_SECONDS)
    response.raise_for_status()

    soup = BeautifulSoup(response.text, "html.parser")
    events: list[dict[str, str | None]] = []

    for link in soup.select("a[href*='/news/']"):
        href = link.get("href")
        url = urljoin(NEWS_URL, href or "")
        if url.rstrip("/") == NEWS_URL.rstrip("/"):
            continue

        container = link.find_parent(["article", "li", "div"]) or link
        raw_text = clean_text(container.get_text(" ", strip=True))
        title = clean_text(link.get_text(" ", strip=True))
        if not title:
            heading = container.find(["h1", "h2", "h3", "h4"])
            title = clean_text(heading.get_text(" ", strip=True) if heading else None)
        if not title or len(title) < 5:
            continue

        time_tag = container.find("time") if container else None
        published_time = parse_datetime(time_tag.get("datetime") if time_tag else None)

        events.append(
            {
                "source": SOURCE_NAME,
                "title": title,
                "category": infer_category(title, raw_text),
                "start_time": published_time,
                "end_time": None,
                "url": url,
                "summary": raw_text[:500] if raw_text else None,
                "raw_text": raw_text,
                "scraped_at": datetime.now(timezone.utc).isoformat(),
            }
        )

    logger.info("Found %d official Pokémon GO news candidates", len(events))
    return events
