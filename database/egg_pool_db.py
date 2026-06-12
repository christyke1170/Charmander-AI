"""SQLite helpers for cached Pokémon GO egg pool rows."""

from __future__ import annotations

import logging
from typing import Any, Iterable

from database.db import get_connection


logger = logging.getLogger(__name__)


EGG_POOLS_SCHEMA = """
CREATE TABLE IF NOT EXISTS egg_pools (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL,
    pool_name TEXT NOT NULL,
    egg_distance_km INTEGER,
    pool_type TEXT NOT NULL,
    pokemon_name TEXT NOT NULL,
    cp_text TEXT,
    shiny_available INTEGER DEFAULT 0,
    rarity_text TEXT,
    notes TEXT,
    url TEXT,
    scraped_at TEXT NOT NULL,
    UNIQUE(source, pool_name, pokemon_name)
);
"""

SEARCH_COLUMNS = ("pool_name", "pool_type", "pokemon_name", "cp_text", "rarity_text", "notes", "url")


def init_egg_pool_tables() -> None:
    """Create egg pool cache tables if needed."""

    with get_connection() as conn:
        conn.execute(EGG_POOLS_SCHEMA)
    logger.info("Egg pools table initialized")


def _clean_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _clean_distance(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        logger.warning("Ignoring invalid egg distance value: %r", value)
        return None


def _normalize_pool_type(value: Any) -> str:
    text = _clean_text(value)
    if text in {"standard", "adventure_sync", "route_gift", "event", "unknown"}:
        return text
    return "unknown"


def _normalize_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "source": _clean_text(row.get("source")),
        "pool_name": _clean_text(row.get("pool_name")),
        "egg_distance_km": _clean_distance(row.get("egg_distance_km")),
        "pool_type": _normalize_pool_type(row.get("pool_type")),
        "pokemon_name": _clean_text(row.get("pokemon_name") or row.get("name")),
        "cp_text": _clean_text(row.get("cp_text")),
        "shiny_available": 1 if row.get("shiny_available") else 0,
        "rarity_text": _clean_text(row.get("rarity_text")),
        "notes": _clean_text(row.get("notes")),
        "url": _clean_text(row.get("url")),
        "scraped_at": _clean_text(row.get("scraped_at")),
    }


def clear_egg_pools_for_source(source: str) -> None:
    """Delete cached egg rows for one source."""

    with get_connection() as conn:
        conn.execute("DELETE FROM egg_pools WHERE source = ?", (source,))


def upsert_egg_pool_row(row: dict[str, Any]) -> None:
    """Insert or update one cached egg pool row."""

    normalized = _normalize_row(row)
    required = ("source", "pool_name", "pool_type", "pokemon_name", "scraped_at")
    missing = [field for field in required if not normalized.get(field)]
    if missing:
        raise ValueError(f"Egg pool rows require {', '.join(required)}; missing {', '.join(missing)}")

    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO egg_pools (
                source, pool_name, egg_distance_km, pool_type, pokemon_name,
                cp_text, shiny_available, rarity_text, notes, url, scraped_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(source, pool_name, pokemon_name) DO UPDATE SET
                egg_distance_km = excluded.egg_distance_km,
                pool_type = excluded.pool_type,
                cp_text = excluded.cp_text,
                shiny_available = excluded.shiny_available,
                rarity_text = excluded.rarity_text,
                notes = excluded.notes,
                url = excluded.url,
                scraped_at = excluded.scraped_at
            """,
            (
                normalized.get("source"),
                normalized.get("pool_name"),
                normalized.get("egg_distance_km"),
                normalized.get("pool_type"),
                normalized.get("pokemon_name"),
                normalized.get("cp_text"),
                normalized.get("shiny_available"),
                normalized.get("rarity_text"),
                normalized.get("notes"),
                normalized.get("url"),
                normalized.get("scraped_at"),
            ),
        )


def upsert_egg_pool_rows(rows: Iterable[dict[str, Any]]) -> int:
    """Upsert multiple egg pool rows and return the number attempted."""

    count = 0
    for row in rows:
        upsert_egg_pool_row(row)
        count += 1
    return count


def count_egg_pool_rows() -> int:
    with get_connection() as conn:
        row = conn.execute("SELECT COUNT(*) AS count FROM egg_pools").fetchone()
    return int(row["count"] if row else 0)


def _fetch_all(query: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    with get_connection() as conn:
        rows = conn.execute(query, params).fetchall()
    return [dict(row) for row in rows]


def _order_clause() -> str:
    return "CASE WHEN egg_distance_km IS NULL THEN 1 ELSE 0 END, egg_distance_km ASC, pool_name ASC, pokemon_name ASC"


def get_egg_pools_by_distance(distance_km: int, pool_type: str | None = None) -> list[dict[str, Any]]:
    """Return egg pool rows for a distance, optionally restricted by pool type."""

    normalized_type = _normalize_pool_type(pool_type) if pool_type else None
    if normalized_type:
        return _fetch_all(
            f"""
            SELECT * FROM egg_pools
            WHERE egg_distance_km = ? AND pool_type = ?
            ORDER BY {_order_clause()}
            """,
            (int(distance_km), normalized_type),
        )
    return _fetch_all(
        f"""
        SELECT * FROM egg_pools
        WHERE egg_distance_km = ?
        ORDER BY {_order_clause()}
        """,
        (int(distance_km),),
    )


def get_egg_pools_by_pool_name(pool_name_query: str) -> list[dict[str, Any]]:
    """Return rows whose pool name contains the provided text."""

    query = (pool_name_query or "").strip().lower()
    if not query:
        return []
    return _fetch_all(
        f"""
        SELECT * FROM egg_pools
        WHERE LOWER(pool_name) LIKE ?
        ORDER BY {_order_clause()}
        """,
        (f"%{query}%",),
    )


def search_egg_pools(query: str, limit: int = 50) -> list[dict[str, Any]]:
    """Search cached egg rows by pool, Pokémon name, notes, CP, rarity, or source URL."""

    normalized = (query or "").strip().lower()
    if not normalized:
        return []
    clauses = [f"LOWER(COALESCE({column}, '')) LIKE ?" for column in SEARCH_COLUMNS]
    params = tuple(f"%{normalized}%" for _ in SEARCH_COLUMNS)
    return _fetch_all(
        f"""
        SELECT * FROM egg_pools
        WHERE {' OR '.join(clauses)}
        ORDER BY {_order_clause()}
        LIMIT ?
        """,
        (*params, int(limit)),
    )


def get_all_egg_pool_sections() -> list[str]:
    """Return distinct cached egg pool section names in display order."""

    rows = _fetch_all(
        f"""
        SELECT pool_name, MIN(egg_distance_km) AS egg_distance_km
        FROM egg_pools
        GROUP BY pool_name
        ORDER BY CASE WHEN egg_distance_km IS NULL THEN 1 ELSE 0 END, egg_distance_km ASC, pool_name ASC
        """
    )
    return [str(row["pool_name"]) for row in rows]
