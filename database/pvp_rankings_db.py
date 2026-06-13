"""SQLite helpers for cached PvPoke PvP ranking rows."""

from __future__ import annotations

import logging
import re
from typing import Any, Iterable

from database.db import get_connection


logger = logging.getLogger(__name__)

SOURCE_NAME = "pvpoke_rankings"
LEAGUE_CP = {"great": 1500, "ultra": 2500, "master": 10000}
LEAGUE_ALIASES = {
    "great": "great",
    "great league": "great",
    "gl": "great",
    "ultra": "ultra",
    "ultra league": "ultra",
    "ul": "ultra",
    "master": "master",
    "master league": "master",
    "ml": "master",
}

PVP_RANKINGS_SCHEMA = """
CREATE TABLE IF NOT EXISTS pvp_rankings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL,
    league TEXT NOT NULL,
    league_cp INTEGER NOT NULL,
    rank INTEGER,
    pokemon_name TEXT NOT NULL,
    form TEXT,
    type_1 TEXT,
    type_2 TEXT,
    fast_move TEXT,
    charged_move_1 TEXT,
    charged_move_2 TEXT,
    score TEXT,
    url TEXT,
    scraped_at TEXT NOT NULL,
    UNIQUE(source, league, pokemon_name, form, rank)
);
"""

SEARCH_COLUMNS = (
    "league",
    "pokemon_name",
    "form",
    "type_1",
    "type_2",
    "fast_move",
    "charged_move_1",
    "charged_move_2",
    "score",
    "url",
)


def init_pvp_ranking_tables() -> None:
    """Create PvP ranking cache tables if needed."""

    with get_connection() as conn:
        conn.execute(PVP_RANKINGS_SCHEMA)
    logger.info("PvP rankings table initialized")


def normalize_pvp_league(query: str | None) -> str | None:
    """Return canonical PvP league key from natural-language text."""

    normalized = re.sub(r"\s+", " ", (query or "").strip().lower())
    if not normalized:
        return None
    if normalized in LEAGUE_ALIASES:
        return LEAGUE_ALIASES[normalized]
    for alias, league in sorted(LEAGUE_ALIASES.items(), key=lambda item: len(item[0]), reverse=True):
        if re.search(rf"\b{re.escape(alias)}\b", normalized):
            return league
    return None


def _clean_text(value: Any) -> str | None:
    if value is None:
        return None
    text = re.sub(r"\s+", " ", str(value)).strip()
    return text or None


def _clean_rank(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        logger.warning("Ignoring invalid PvP rank value: %r", value)
        return None


def _normalize_row(row: dict[str, Any]) -> dict[str, Any]:
    league = normalize_pvp_league(_clean_text(row.get("league")) or "")
    league_cp = row.get("league_cp") or (LEAGUE_CP.get(league or "") if league else None)
    try:
        league_cp_int = int(league_cp) if league_cp is not None and league_cp != "" else None
    except (TypeError, ValueError):
        league_cp_int = None
    normalized = {
        "source": _clean_text(row.get("source")) or SOURCE_NAME,
        "league": league,
        "league_cp": league_cp_int,
        "rank": _clean_rank(row.get("rank")),
        "pokemon_name": _clean_text(row.get("pokemon_name") or row.get("name")),
        "form": _clean_text(row.get("form")) or "",
        "type_1": _clean_text(row.get("type_1")),
        "type_2": _clean_text(row.get("type_2")),
        "fast_move": _clean_text(row.get("fast_move")),
        "charged_move_1": _clean_text(row.get("charged_move_1") or row.get("charge_move_1")),
        "charged_move_2": _clean_text(row.get("charged_move_2") or row.get("charge_move_2")),
        "score": _clean_text(row.get("score")),
        "url": _clean_text(row.get("url")),
        "scraped_at": _clean_text(row.get("scraped_at")),
    }
    if normalized["type_2"] and str(normalized["type_2"]).lower() == "none":
        normalized["type_2"] = None
    return normalized


def clear_pvp_rankings_for_source(source: str) -> None:
    """Delete cached PvP ranking rows for one source."""

    with get_connection() as conn:
        conn.execute("DELETE FROM pvp_rankings WHERE source = ?", (source,))


def upsert_pvp_ranking(row: dict[str, Any]) -> None:
    """Insert or update one cached PvP ranking row."""

    normalized = _normalize_row(row)
    required = ("source", "league", "league_cp", "pokemon_name", "scraped_at")
    missing = [field for field in required if normalized.get(field) is None or normalized.get(field) == ""]
    if missing:
        raise ValueError(f"PvP ranking rows require {', '.join(required)}; missing {', '.join(missing)}")

    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO pvp_rankings (
                source, league, league_cp, rank, pokemon_name, form, type_1, type_2,
                fast_move, charged_move_1, charged_move_2, score, url, scraped_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(source, league, pokemon_name, form, rank) DO UPDATE SET
                league_cp = excluded.league_cp,
                type_1 = excluded.type_1,
                type_2 = excluded.type_2,
                fast_move = excluded.fast_move,
                charged_move_1 = excluded.charged_move_1,
                charged_move_2 = excluded.charged_move_2,
                score = excluded.score,
                url = excluded.url,
                scraped_at = excluded.scraped_at
            """,
            (
                normalized.get("source"),
                normalized.get("league"),
                normalized.get("league_cp"),
                normalized.get("rank"),
                normalized.get("pokemon_name"),
                normalized.get("form"),
                normalized.get("type_1"),
                normalized.get("type_2"),
                normalized.get("fast_move"),
                normalized.get("charged_move_1"),
                normalized.get("charged_move_2"),
                normalized.get("score"),
                normalized.get("url"),
                normalized.get("scraped_at"),
            ),
        )


def upsert_pvp_rankings(rows: Iterable[dict[str, Any]]) -> int:
    """Upsert multiple PvP ranking rows and return the number attempted."""

    count = 0
    for row in rows:
        upsert_pvp_ranking(row)
        count += 1
    return count


def count_pvp_rankings() -> int:
    with get_connection() as conn:
        row = conn.execute("SELECT COUNT(*) AS count FROM pvp_rankings").fetchone()
    return int(row["count"] if row else 0)


def _fetch_all(query: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    with get_connection() as conn:
        rows = conn.execute(query, params).fetchall()
    return [dict(row) for row in rows]


def _rank_order_clause() -> str:
    return "CASE WHEN rank IS NULL THEN 1 ELSE 0 END, rank ASC, pokemon_name ASC, form ASC"


def get_top_pvp_rankings(league: str, limit: int = 20) -> list[dict[str, Any]]:
    """Return top cached PvP rankings for a league."""

    normalized_league = normalize_pvp_league(league)
    if not normalized_league:
        return []
    return _fetch_all(
        f"""
        SELECT * FROM pvp_rankings
        WHERE league = ?
        ORDER BY {_rank_order_clause()}
        LIMIT ?
        """,
        (normalized_league, int(limit)),
    )


def search_pvp_rankings(query: str, league: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
    """Search cached PvP rankings by Pokémon, league, moves, types, score, or URL."""

    normalized_query = (query or "").strip().lower()
    normalized_league = normalize_pvp_league(league or "") if league else None
    if not normalized_query and normalized_league:
        return get_top_pvp_rankings(normalized_league, limit=limit)
    if not normalized_query:
        return []

    clauses = [f"LOWER(COALESCE({column}, '')) LIKE ?" for column in SEARCH_COLUMNS]
    params: list[Any] = [f"%{normalized_query}%" for _ in SEARCH_COLUMNS]
    where = f"({' OR '.join(clauses)})"
    if normalized_league:
        where += " AND league = ?"
        params.append(normalized_league)
    return _fetch_all(
        f"""
        SELECT * FROM pvp_rankings
        WHERE {where}
        ORDER BY league_cp ASC, {_rank_order_clause()}
        LIMIT ?
        """,
        (*params, int(limit)),
    )
