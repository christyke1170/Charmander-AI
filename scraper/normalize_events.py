"""Helpers to normalize scraped event dictionaries before database storage."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

from dateutil import parser as date_parser


RAID_KEYWORDS = ("raid", "raid hour", "shadow raid", "elite raid", "mega raid")
COMMUNITY_DAY_KEYWORDS = ("community day", "community day classic")


def clean_text(value: str | None) -> str | None:
    """Collapse whitespace and strip a text field."""

    if not value:
        return None
    return re.sub(r"\s+", " ", value).strip()


def parse_datetime(value: str | None) -> str | None:
    """Parse a date/time string into ISO 8601 when possible.

    If a timezone is missing, UTC is assumed. Some sources publish local-time
    events; those rows can separately set local_time=True.
    """

    value = clean_text(value)
    if not value:
        return None

    try:
        parsed = date_parser.parse(value, fuzzy=True)
    except (ValueError, TypeError, OverflowError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat()


def infer_category(title: str | None, raw_text: str | None = None) -> str | None:
    """Infer a lightweight event category from title/body keywords."""

    haystack = f"{title or ''} {raw_text or ''}".lower()
    if any(keyword in haystack for keyword in COMMUNITY_DAY_KEYWORDS):
        return "Community Day"
    if any(keyword in haystack for keyword in RAID_KEYWORDS):
        return "Raids"
    if "spotlight hour" in haystack:
        return "Spotlight Hour"
    if "go battle" in haystack or "battle league" in haystack:
        return "GO Battle League"
    if "research" in haystack:
        return "Research"
    return None


def normalize_event(event: dict[str, Any]) -> dict[str, Any] | None:
    """Normalize one event into the database shape."""

    title = clean_text(event.get("title"))
    source = clean_text(event.get("source"))
    if not title or not source:
        return None

    raw_text = clean_text(event.get("raw_text"))
    summary = clean_text(event.get("summary"))
    start_time = event.get("start_time") or parse_datetime(event.get("start_date"))
    end_time = event.get("end_time") or parse_datetime(event.get("end_date"))
    category = clean_text(event.get("category")) or infer_category(title, raw_text or summary)

    return {
        "source": source,
        "title": title,
        "category": category,
        "start_time": start_time,
        "end_time": end_time,
        "local_time": bool(event.get("local_time", False)),
        "url": clean_text(event.get("url")),
        "summary": summary,
        "raw_text": raw_text,
        "scraped_at": event.get("scraped_at") or datetime.now(timezone.utc).isoformat(),
    }


def normalize_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Normalize and filter a list of scraped events."""

    normalized: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str | None]] = set()
    for event in events:
        item = normalize_event(event)
        if not item:
            continue
        key = (item["source"], item["title"], item.get("start_time"))
        if key in seen:
            continue
        seen.add(key)
        normalized.append(item)
    return normalized
