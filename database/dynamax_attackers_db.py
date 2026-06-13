"""SQLite helpers for cached Dynamax/Gigantamax attacker ranking rows."""

from __future__ import annotations

import logging
from typing import Any, Iterable

from database.db import get_connection
from database.raid_attackers_db import POKEMON_TYPES, normalize_type_name


logger = logging.getLogger(__name__)


DYNAMAX_ATTACKERS_SCHEMA = """
CREATE TABLE IF NOT EXISTS dynamax_attackers (
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


def init_dynamax_attacker_tables() -> None:
    """Create Dynamax attacker ranking tables if needed."""

    with get_connection() as conn:
        conn.execute(DYNAMAX_ATTACKERS_SCHEMA)
    logger.info("Dynamax attacker rankings table initialized")


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
        logger.warning("Ignoring invalid Dynamax attacker rank value: %r", value)
        return None


def _normalize_dynamax_row(row: dict[str, Any]) -> dict[str, Any]:
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


def clear_dynamax_attackers_for_source(source: str) -> None:
    """Delete all Dynamax attacker rows for a source after a successful scrape."""

    cleaned_source = _clean_text(source)
    if not cleaned_source:
        raise ValueError("source is required when clearing Dynamax attackers")
    with get_connection() as conn:
        conn.execute("DELETE FROM dynamax_attackers WHERE source = ?", (cleaned_source,))


def upsert_dynamax_attacker(row: dict[str, Any]) -> None:
    """Insert or update one cached Dynamax attacker ranking row."""

    normalized = _normalize_dynamax_row(row)
    required = ("source", "ranking_scope", "pokemon_name", "scraped_at")
    missing = [field for field in required if not normalized.get(field)]
    if missing:
        raise ValueError(f"Dynamax attacker rows require {', '.join(required)}; missing {', '.join(missing)}")

    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO dynamax_attackers (
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


def upsert_dynamax_attackers(rows: Iterable[dict[str, Any]]) -> int:
    count = 0
    for row in rows:
        upsert_dynamax_attacker(row)
        count += 1
    return count


def count_dynamax_attackers() -> int:
    with get_connection() as conn:
        row = conn.execute("SELECT COUNT(*) AS count FROM dynamax_attackers").fetchone()
    return int(row["count"] if row else 0)


def _fetch_all(query: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    with get_connection() as conn:
        rows = conn.execute(query, params).fetchall()
    return [dict(row) for row in rows]


def _rank_order_clause() -> str:
    return "CASE WHEN rank IS NULL THEN 1 ELSE 0 END, rank ASC, pokemon_name ASC, form ASC"


def _numeric_metric_expression(column: str) -> str:
    return f"CAST(NULLIF(TRIM({column}), '') AS REAL)"


def _dedupe_dynamax_rows(rows: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
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


def get_top_dynamax_attackers_by_type(type_name: str, limit: int = 10) -> list[dict[str, Any]]:
    """Return top cached Dynamax/Gigantamax attackers for a Pokémon type."""

    normalized_type = normalize_type_name(type_name)
    if not normalized_type:
        return []
    return _fetch_all(
        f"""
        SELECT * FROM dynamax_attackers
        WHERE ranking_scope = ?
        ORDER BY {_rank_order_clause()}
        LIMIT ?
        """,
        (f"type:{normalized_type}", limit),
    )


def get_best_dynamax_attackers_across_types(limit: int = 10) -> list[dict[str, Any]]:
    """Return a derived cross-type Dynamax list sorted by Score, DPS, then TDO."""

    fetch_limit = max(limit * 5, limit, 50)
    rows = _fetch_all(
        f"""
        SELECT * FROM dynamax_attackers
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
    return _dedupe_dynamax_rows(rows, limit)


def _build_like_filter(terms: Iterable[str]) -> tuple[str, tuple[str, ...]]:
    clauses: list[str] = []
    params: list[str] = []
    for term in terms:
        like_term = f"%{term.lower()}%"
        for column in SEARCH_COLUMNS:
            clauses.append(f"LOWER(COALESCE({column}, '')) LIKE ?")
            params.append(like_term)
    return " OR ".join(clauses), tuple(params)


def search_dynamax_attackers(query: str, limit: int = 20) -> list[dict[str, Any]]:
    """Search cached Dynamax attacker rows while preserving rank order when possible."""

    normalized = query.strip().lower()
    if not normalized:
        return get_best_dynamax_attackers_across_types(limit=limit)

    detected_type = normalize_type_name(normalized)
    if detected_type:
        type_rows = get_top_dynamax_attackers_by_type(detected_type, limit=limit)
        if type_rows:
            return type_rows

    where_clause, params = _build_like_filter((normalized,))
    return _fetch_all(
        f"""
        SELECT * FROM dynamax_attackers
        WHERE {where_clause}
        ORDER BY
            CASE WHEN ranking_scope LIKE 'type:%' THEN 0 ELSE 1 END,
            {_rank_order_clause()}
        LIMIT ?
        """,
        (*params, limit),
    )
