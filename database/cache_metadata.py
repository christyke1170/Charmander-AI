"""SQLite helpers for cache freshness metadata."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from dateutil import parser as date_parser

from database.db import get_connection


logger = logging.getLogger(__name__)


CACHE_METADATA_SCHEMA = """
CREATE TABLE IF NOT EXISTS cache_metadata (
    cache_name TEXT PRIMARY KEY,
    last_updated TEXT,
    source TEXT,
    notes TEXT,
    updated_at TEXT NOT NULL
);
"""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def init_cache_metadata_table() -> None:
    """Create cache metadata storage if needed."""

    with get_connection() as conn:
        conn.execute(CACHE_METADATA_SCHEMA)
    logger.info("Cache metadata table initialized")


def get_cache_metadata(cache_name: str) -> dict[str, Any] | None:
    """Return metadata for a named cache, if present."""

    with get_connection() as conn:
        row = conn.execute("SELECT * FROM cache_metadata WHERE cache_name = ?", (cache_name,)).fetchone()
    return dict(row) if row else None


def update_cache_metadata(cache_name: str, source: str | None = None, notes: str | None = None) -> None:
    """Mark a cache as successfully refreshed right now."""

    now = _utc_now()
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO cache_metadata (cache_name, last_updated, source, notes, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(cache_name) DO UPDATE SET
                last_updated = excluded.last_updated,
                source = excluded.source,
                notes = excluded.notes,
                updated_at = excluded.updated_at
            """,
            (cache_name, now, source, notes, now),
        )


def _parse_last_updated(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = date_parser.parse(value)
    except (ValueError, TypeError, OverflowError) as exc:
        logger.warning("Invalid cache_metadata.last_updated value %r: %s", value, exc)
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def get_cache_last_updated(cache_name: str) -> datetime | None:
    """Return a named cache's last successful update timestamp."""

    metadata = get_cache_metadata(cache_name)
    if not metadata:
        return None
    return _parse_last_updated(metadata.get("last_updated"))


def is_cache_stale(cache_name: str, max_age_days: int) -> bool:
    """Return True if the named cache is missing, invalid, or older than max_age_days."""

    last_updated = get_cache_last_updated(cache_name)
    if last_updated is None:
        return True
    max_age = timedelta(days=max(max_age_days, 0))
    return datetime.now(timezone.utc) - last_updated >= max_age