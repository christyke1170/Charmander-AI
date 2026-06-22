"""SQLite helpers for Pokémon GO event storage."""

from __future__ import annotations

import json
import logging
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

from dateutil import parser as date_parser

from config import DATABASE_PATH


logger = logging.getLogger(__name__)

SEARCH_COLUMNS = ("title", "category", "summary", "raw_text", "url")
RAID_TERMS = (
    "raid",
    "raid hour",
    "raid day",
    "mega raid",
    "shadow raid",
    "elite raid",
    "max battle",
    "gigantamax",
    "dynamax",
)
COMMUNITY_DAY_TERMS = (
    "community day",
    "communityday",
    "classic community day",
    "community day classic",
)


SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL,
    title TEXT NOT NULL,
    category TEXT,
    start_time TEXT,
    end_time TEXT,
    local_time BOOLEAN DEFAULT 0,
    url TEXT,
    summary TEXT,
    raw_text TEXT,
    scraped_at TEXT NOT NULL,
    UNIQUE(source, title, start_time)
);
"""

EVENT_DETAILS_SCHEMA = """
CREATE TABLE IF NOT EXISTS event_details (
    event_url TEXT PRIMARY KEY,
    event_title TEXT,
    fetched_at TEXT,
    summary_text TEXT,
    sections_json TEXT
);
"""


@contextmanager
def get_connection(db_path: Path | None = None):
    """Open a SQLite connection with row dictionaries enabled."""

    db_path = db_path or DATABASE_PATH
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    """Create the local database schema if it does not already exist."""

    with get_connection() as conn:
        conn.execute(SCHEMA)
        conn.execute(EVENT_DETAILS_SCHEMA)
    logger.info("Database initialized at %s", DATABASE_PATH)


def upsert_event(event: dict[str, Any]) -> None:
    """Insert or update a normalized event.

    Events are deduplicated by the schema's UNIQUE(source, title, start_time)
    constraint. If an event changes, the latest scraped fields replace old values.
    """

    required = ["source", "title", "scraped_at"]
    missing = [field for field in required if not event.get(field)]
    if missing:
        raise ValueError(f"Event is missing required fields: {', '.join(missing)}")

    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO events (
                source, title, category, start_time, end_time, local_time,
                url, summary, raw_text, scraped_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(source, title, start_time) DO UPDATE SET
                category = excluded.category,
                end_time = excluded.end_time,
                local_time = excluded.local_time,
                url = excluded.url,
                summary = excluded.summary,
                raw_text = excluded.raw_text,
                scraped_at = excluded.scraped_at
            """,
            (
                event.get("source"),
                event.get("title"),
                event.get("category"),
                event.get("start_time"),
                event.get("end_time"),
                int(bool(event.get("local_time", False))),
                event.get("url"),
                event.get("summary"),
                event.get("raw_text"),
                event.get("scraped_at"),
            ),
        )


def upsert_events(events: Iterable[dict[str, Any]]) -> int:
    """Upsert multiple events and return the number attempted."""

    count = 0
    for event in events:
        upsert_event(event)
        count += 1
    return count


def upsert_event_detail(detail: dict[str, Any]) -> None:
    """Insert or update cached detail content for one event URL."""

    event_url = str(detail.get("event_url") or "").strip()
    if not event_url:
        raise ValueError("Event detail is missing required field: event_url")

    sections = detail.get("sections_json")
    if isinstance(sections, (dict, list)):
        sections_json = json.dumps(sections, ensure_ascii=False)
    else:
        sections_json = sections

    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO event_details (
                event_url, event_title, fetched_at, summary_text, sections_json
            ) VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(event_url) DO UPDATE SET
                event_title = excluded.event_title,
                fetched_at = excluded.fetched_at,
                summary_text = excluded.summary_text,
                sections_json = excluded.sections_json
            """,
            (
                event_url,
                detail.get("event_title"),
                detail.get("fetched_at"),
                detail.get("summary_text"),
                sections_json,
            ),
        )


def get_event_detail(event_url: str | None) -> dict[str, Any] | None:
    """Return cached detail content for a specific event URL."""

    normalized_url = str(event_url or "").strip()
    if not normalized_url:
        return None
    rows = _fetch_all("SELECT * FROM event_details WHERE event_url = ? LIMIT 1", (normalized_url,))
    if not rows:
        return None
    detail = rows[0]
    raw_sections = detail.get("sections_json")
    if isinstance(raw_sections, str) and raw_sections.strip():
        try:
            detail["sections_json"] = json.loads(raw_sections)
        except json.JSONDecodeError:
            detail["sections_json"] = {}
    elif not isinstance(raw_sections, dict):
        detail["sections_json"] = {}
    return detail


def should_refresh_event_detail(event_url: str | None, max_age_hours: int = 24) -> bool:
    """Return whether a cached detail row is missing or stale enough to refresh."""

    detail = get_event_detail(event_url)
    if not detail:
        return True
    fetched_at = _parse_stored_datetime(detail.get("fetched_at"), field_name="fetched_at")
    if fetched_at is None:
        return True
    age_limit = datetime.now(timezone.utc) - timedelta(hours=max(1, int(max_age_hours)))
    return fetched_at < age_limit


def _fetch_all(query: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    with get_connection() as conn:
        rows = conn.execute(query, params).fetchall()
    return [dict(row) for row in rows]


def _normalize_datetime(value: datetime) -> datetime:
    """Return a timezone-aware UTC datetime for safe comparisons."""

    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _parse_stored_datetime(value: str | None, event_id: int | None = None, field_name: str = "datetime") -> datetime | None:
    """Parse a stored datetime without letting malformed rows break queries."""

    if not value:
        return None
    try:
        parsed = date_parser.parse(value)
    except (ValueError, TypeError, OverflowError) as exc:
        logger.warning("Skipping event %s: could not parse %s=%r (%s)", event_id, field_name, value, exc)
        return None
    return _normalize_datetime(parsed)


def _event_text(event: dict[str, Any]) -> str:
    return " ".join(str(event.get(column) or "") for column in SEARCH_COLUMNS).lower()


def _raw_text_implies_multi_day(event: dict[str, Any]) -> bool:
    """Heuristic for undated-end events that appear to span multiple days."""

    text = _event_text(event)
    multi_day_markers = (
        "multi-day",
        "multiple days",
        "week-long",
        "all week",
        "this week",
        "through",
        "until",
        "from ",
        "season",
        "during the event",
    )
    return any(marker in text for marker in multi_day_markers)


def _build_like_filter(terms: Iterable[str]) -> tuple[str, tuple[str, ...]]:
    """Build a LOWER(column) LIKE ? filter across event text columns."""

    clauses: list[str] = []
    params: list[str] = []
    for term in terms:
        like_term = f"%{term.lower()}%"
        for column in SEARCH_COLUMNS:
            clauses.append(f"LOWER(COALESCE({column}, '')) LIKE ?")
            params.append(like_term)
    return " OR ".join(clauses), tuple(params)


def _order_and_limit_query(where_clause: str, limit: int) -> str:
    return f"""
        SELECT * FROM events
        WHERE {where_clause}
        ORDER BY
            CASE WHEN start_time IS NULL THEN 1 ELSE 0 END,
            start_time ASC,
            scraped_at DESC
        LIMIT {int(limit)}
        """


def get_upcoming_events(limit: int = 10) -> list[dict[str, Any]]:
    """Return general upcoming events ordered by start time."""

    now = datetime.now(timezone.utc).isoformat()
    return _fetch_all(
        """
        SELECT * FROM events
        WHERE start_time IS NULL OR start_time >= ? OR (end_time IS NOT NULL AND end_time >= ?)
        ORDER BY
            CASE WHEN start_time IS NULL THEN 1 ELSE 0 END,
            start_time ASC,
            scraped_at DESC
        LIMIT ?
        """,
        (now, now, limit),
    )


def get_active_events(now: datetime | None = None) -> list[dict[str, Any]]:
    """Return events active right now using parsed datetime comparisons.

    Active rules:
    - start_time and end_time: start_time <= now <= end_time
    - start_time only: active only on the start date, unless text implies a
      multi-day event. For implied multi-day rows without an end date, keep a
      conservative 14-day activity window to avoid showing stale old news.
    - end_time only: active if now <= end_time
    """

    current = _normalize_datetime(now or datetime.now(timezone.utc))
    rows = _fetch_all(
        """
        SELECT * FROM events
        WHERE start_time IS NOT NULL OR end_time IS NOT NULL
        ORDER BY
            CASE WHEN start_time IS NULL THEN 1 ELSE 0 END,
            start_time ASC,
            scraped_at DESC
        """
    )

    active: list[dict[str, Any]] = []
    for event in rows:
        event_id = event.get("id")
        start = _parse_stored_datetime(event.get("start_time"), event_id, "start_time")
        end = _parse_stored_datetime(event.get("end_time"), event_id, "end_time")

        if start and end and start <= current <= end:
            active.append(event)
        elif start and not end:
            if current.date() == start.date():
                active.append(event)
            elif _raw_text_implies_multi_day(event) and start <= current <= start + timedelta(days=14):
                active.append(event)
        elif end and not start and current <= end:
            active.append(event)

    return active


def get_active_raid_events(now: datetime | None = None, limit: int = 10) -> list[dict[str, Any]]:
    """Return raid-related events active right now using explicit start/end bounds.

    For current raid queries we require a concrete date range so future schedule
    rows or undated/end-only rows do not appear as active today.
    """

    current = _normalize_datetime(now or datetime.now(timezone.utc))
    active_events = []
    for event in _fetch_all(
        """
        SELECT * FROM events
        WHERE start_time IS NOT NULL AND end_time IS NOT NULL
        ORDER BY
            CASE WHEN start_time IS NULL THEN 1 ELSE 0 END,
            start_time ASC,
            scraped_at DESC
        """
    ):
        event_id = event.get("id")
        start = _parse_stored_datetime(event.get("start_time"), event_id, "start_time")
        end = _parse_stored_datetime(event.get("end_time"), event_id, "end_time")
        if start and end and start <= current <= end:
            active_events.append(event)

    raid_terms = tuple(term.lower() for term in RAID_TERMS)
    raid_events = [
        event for event in active_events if any(term in _event_text(event) for term in raid_terms)
    ]
    return raid_events[: max(1, int(limit))]


def search_events(query: str, limit: int = 10) -> list[dict[str, Any]]:
    """Search events by title, category, summary, raw text, or URL."""

    query = query.strip().lower()
    if not query:
        return []
    where_clause, params = _build_like_filter((query,))
    return _fetch_all(_order_and_limit_query(where_clause, limit), params)


def get_events_by_category(category: str) -> list[dict[str, Any]]:
    """Return events matching a category or common text keyword."""

    return search_events(category, limit=10)


def get_raid_events(limit: int = 10) -> list[dict[str, Any]]:
    """Return raid-related events using explicit raid terms."""

    where_clause, params = _build_like_filter(RAID_TERMS)
    return _fetch_all(_order_and_limit_query(where_clause, limit), params)


def get_community_day_events(limit: int = 10) -> list[dict[str, Any]]:
    """Return Community Day events using explicit Community Day terms."""

    where_clause, params = _build_like_filter(COMMUNITY_DAY_TERMS)
    return _fetch_all(_order_and_limit_query(where_clause, limit), params)
