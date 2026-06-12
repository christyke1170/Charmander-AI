"""SQLite helpers for cached raid attacker ranking rows."""

from __future__ import annotations

import logging
from typing import Any, Iterable

from database.db import get_connection


logger = logging.getLogger(__name__)


RAID_ATTACKER_RANKINGS_SCHEMA = """
CREATE TABLE IF NOT EXISTS raid_attacker_rankings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL,
    ranking_scope TEXT NOT NULL,
    pokemon_name TEXT NOT NULL,
    form TEXT,
    pokemon_type TEXT,
    rank INTEGER,
    fast_move TEXT,
    charged_move TEXT,
    score TEXT,
    dps TEXT,
    tdo TEXT,
    summary TEXT,
    url TEXT,
    scraped_at TEXT NOT NULL,
    UNIQUE(source, ranking_scope, pokemon_name, form, pokemon_type, rank)
);
"""

SEARCH_COLUMNS = (
    "ranking_scope",
    "pokemon_name",
    "form",
    "pokemon_type",
    "fast_move",
    "charged_move",
    "score",
    "dps",
    "tdo",
    "summary",
    "url",
)

POKEMON_TYPES = {
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
}


def normalize_type_name(type_name: str | None) -> str | None:
    """Return a canonical Pokémon type name when the text names one."""

    if not type_name:
        return None
    lowered = type_name.strip().lower()
    for known_type in POKEMON_TYPES:
        if lowered == known_type or f" {known_type} " in f" {lowered} ":
            return known_type
    return None


def init_raid_attacker_tables() -> None:
    """Create raid attacker ranking tables if needed."""

    with get_connection() as conn:
        conn.execute(RAID_ATTACKER_RANKINGS_SCHEMA)
    logger.info("Raid attacker rankings table initialized")


def _clean_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _clean_rank(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        logger.warning("Ignoring invalid raid attacker rank value: %r", value)
        return None


def _normalize_ranking_row(row: dict[str, Any]) -> dict[str, Any]:
    normalized = {
        "source": _clean_text(row.get("source")),
        "ranking_scope": _clean_text(row.get("ranking_scope")),
        "pokemon_name": _clean_text(row.get("pokemon_name") or row.get("name")),
        "form": _clean_text(row.get("form")) or "",
        "pokemon_type": normalize_type_name(_clean_text(row.get("pokemon_type") or row.get("type"))),
        "rank": _clean_rank(row.get("rank")),
        "fast_move": _clean_text(row.get("fast_move")),
        "charged_move": _clean_text(row.get("charged_move")),
        "score": _clean_text(row.get("score")),
        "dps": _clean_text(row.get("dps")),
        "tdo": _clean_text(row.get("tdo")),
        "summary": _clean_text(row.get("summary")),
        "url": _clean_text(row.get("url")),
        "scraped_at": _clean_text(row.get("scraped_at")),
    }
    if not normalized["ranking_scope"] and normalized["pokemon_type"]:
        normalized["ranking_scope"] = f"type:{normalized['pokemon_type']}"
    if normalized["ranking_scope"]:
        normalized["ranking_scope"] = normalized["ranking_scope"].lower()
    if normalized["ranking_scope"] and normalized["ranking_scope"].startswith("type:"):
        scope_type = normalized["ranking_scope"][5:].strip().lower()
        normalized["ranking_scope"] = f"type:{normalize_type_name(scope_type) or scope_type}"
    return normalized


def upsert_raid_attacker_ranking(row: dict[str, Any]) -> None:
    """Insert or update one cached raid attacker ranking row."""

    normalized = _normalize_ranking_row(row)
    required = ("source", "ranking_scope", "pokemon_name", "scraped_at")
    missing = [field for field in required if not normalized.get(field)]
    if missing:
        raise ValueError(f"Raid attacker ranking rows require {', '.join(required)}; missing {', '.join(missing)}")

    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO raid_attacker_rankings (
                source, ranking_scope, pokemon_name, form, pokemon_type, rank,
                fast_move, charged_move, score, dps, tdo, summary, url, scraped_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(source, ranking_scope, pokemon_name, form, pokemon_type, rank) DO UPDATE SET
                fast_move = excluded.fast_move,
                charged_move = excluded.charged_move,
                score = excluded.score,
                dps = excluded.dps,
                tdo = excluded.tdo,
                summary = excluded.summary,
                url = excluded.url,
                scraped_at = excluded.scraped_at
            """,
            (
                normalized.get("source"),
                normalized.get("ranking_scope"),
                normalized.get("pokemon_name"),
                normalized.get("form"),
                normalized.get("pokemon_type"),
                normalized.get("rank"),
                normalized.get("fast_move"),
                normalized.get("charged_move"),
                normalized.get("score"),
                normalized.get("dps"),
                normalized.get("tdo"),
                normalized.get("summary"),
                normalized.get("url"),
                normalized.get("scraped_at"),
            ),
        )


def upsert_raid_attacker_rankings(rows: Iterable[dict[str, Any]]) -> int:
    count = 0
    for row in rows:
        upsert_raid_attacker_ranking(row)
        count += 1
    return count


def count_raid_attacker_rankings() -> int:
    with get_connection() as conn:
        row = conn.execute("SELECT COUNT(*) AS count FROM raid_attacker_rankings").fetchone()
    return int(row["count"] if row else 0)


def _fetch_all(query: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    with get_connection() as conn:
        rows = conn.execute(query, params).fetchall()
    return [dict(row) for row in rows]


def _rank_order_clause() -> str:
    return "CASE WHEN rank IS NULL THEN 1 ELSE 0 END, rank ASC, pokemon_name ASC, form ASC"


def _numeric_metric_expression(column: str) -> str:
    """Return a SQLite expression that safely sorts text metrics numerically."""

    return f"CAST(NULLIF(TRIM({column}), '') AS REAL)"


def _dedupe_raid_attacker_rows(rows: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    """Keep the first row for each exact Pokémon/form/moves tuple."""

    deduped: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, str]] = set()
    for row in rows:
        key = (
            str(row.get("pokemon_name") or "").strip().lower(),
            str(row.get("form") or "").strip().lower(),
            str(row.get("fast_move") or "").strip().lower(),
            str(row.get("charged_move") or "").strip().lower(),
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(row)
        if len(deduped) >= limit:
            break
    return deduped


def get_top_raid_attackers(limit: int = 200) -> list[dict[str, Any]]:
    """Return top overall raid attackers ordered by ranking position."""

    return _fetch_all(
        f"""
        SELECT * FROM raid_attacker_rankings
        WHERE ranking_scope = 'overall'
        ORDER BY {_rank_order_clause()}
        LIMIT ?
        """,
        (limit,),
    )


def get_top_raid_attackers_by_type(type_name: str, limit: int = 50) -> list[dict[str, Any]]:
    """Return top raid attackers for a Pokémon type ordered by ranking position."""

    normalized_type = normalize_type_name(type_name)
    if not normalized_type:
        return []
    return _fetch_all(
        f"""
        SELECT * FROM raid_attacker_rankings
        WHERE ranking_scope = ?
        ORDER BY {_rank_order_clause()}
        LIMIT ?
        """,
        (f"type:{normalized_type}", limit),
    )


def get_best_raid_attackers_across_types(limit: int = 10) -> list[dict[str, Any]]:
    """Return a derived cross-type list from cached type-specific attacker tables.

    This is not an official overall ranking. It sorts cached best-per-type rows by
    numeric Score, then DPS, then TDO, and keeps the source type scope on each row.
    """

    fetch_limit = max(limit * 5, limit, 50)
    rows = _fetch_all(
        f"""
        SELECT * FROM raid_attacker_rankings
        WHERE ranking_scope LIKE 'type:%'
        ORDER BY
            {_numeric_metric_expression("score")} DESC,
            {_numeric_metric_expression("dps")} DESC,
            {_numeric_metric_expression("tdo")} DESC,
            {_rank_order_clause()}
        LIMIT ?
        """,
        (fetch_limit,),
    )
    return _dedupe_raid_attacker_rows(rows, limit)


def _build_like_filter(terms: Iterable[str]) -> tuple[str, tuple[str, ...]]:
    clauses: list[str] = []
    params: list[str] = []
    for term in terms:
        like_term = f"%{term.lower()}%"
        for column in SEARCH_COLUMNS:
            clauses.append(f"LOWER(COALESCE({column}, '')) LIKE ?")
            params.append(like_term)
    return " OR ".join(clauses), tuple(params)


def search_raid_attackers(query: str, limit: int = 20) -> list[dict[str, Any]]:
    """Search rankings while preserving rank order when possible."""

    normalized = query.strip().lower()
    if not normalized:
        return get_top_raid_attackers(limit=limit)

    detected_type = normalize_type_name(normalized)
    if detected_type:
        type_rows = get_top_raid_attackers_by_type(detected_type, limit=limit)
        if type_rows:
            return type_rows

    where_clause, params = _build_like_filter((normalized,))
    return _fetch_all(
        f"""
        SELECT * FROM raid_attacker_rankings
        WHERE {where_clause}
        ORDER BY
            CASE
                WHEN ranking_scope = 'overall' THEN 0
                WHEN ranking_scope LIKE 'type:%' THEN 1
                ELSE 2
            END,
            {_rank_order_clause()}
        LIMIT ?
        """,
        (*params, limit),
    )