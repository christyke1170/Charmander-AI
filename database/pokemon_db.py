"""SQLite helpers for cached Pokémon GO Hub knowledge."""

from __future__ import annotations

import logging
from typing import Any, Iterable

from database.db import get_connection


logger = logging.getLogger(__name__)


POKEMON_SCHEMA = """
CREATE TABLE IF NOT EXISTS pokemon_knowledge (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL,
    pokemon_id TEXT,
    name TEXT NOT NULL,
    form TEXT,
    types TEXT,
    max_cp TEXT,
    best_moveset TEXT,
    weaknesses TEXT,
    resistances TEXT,
    pve_summary TEXT,
    pvp_summary TEXT,
    raid_counter_summary TEXT,
    raw_text TEXT,
    url TEXT,
    scraped_at TEXT NOT NULL,
    UNIQUE(source, name, form)
);
"""

SEARCH_COLUMNS = (
    "name",
    "form",
    "types",
    "best_moveset",
    "weaknesses",
    "resistances",
    "pve_summary",
    "pvp_summary",
    "raid_counter_summary",
    "raw_text",
    "url",
)

META_TERMS = (
    "best attacker",
    "best fire",
    "best water",
    "counter",
    "counters",
    "moveset",
    "great league",
    "ultra league",
    "master league",
    "pvp",
    "pve",
    "raid attacker",
    "meta",
    "weakness",
    "resist",
)


def init_pokemon_tables() -> None:
    """Create Pokémon knowledge tables if needed."""

    with get_connection() as conn:
        conn.execute(POKEMON_SCHEMA)
    logger.info("Pokémon knowledge table initialized")


def upsert_pokemon_knowledge(row: dict[str, Any]) -> None:
    """Insert or update one cached Pokémon knowledge row."""

    if not row.get("source") or not row.get("name") or not row.get("scraped_at"):
        raise ValueError("Pokémon knowledge rows require source, name, and scraped_at")

    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO pokemon_knowledge (
                source, pokemon_id, name, form, types, max_cp, best_moveset,
                weaknesses, resistances, pve_summary, pvp_summary,
                raid_counter_summary, raw_text, url, scraped_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(source, name, form) DO UPDATE SET
                pokemon_id = excluded.pokemon_id,
                types = excluded.types,
                max_cp = excluded.max_cp,
                best_moveset = excluded.best_moveset,
                weaknesses = excluded.weaknesses,
                resistances = excluded.resistances,
                pve_summary = excluded.pve_summary,
                pvp_summary = excluded.pvp_summary,
                raid_counter_summary = excluded.raid_counter_summary,
                raw_text = excluded.raw_text,
                url = excluded.url,
                scraped_at = excluded.scraped_at
            """,
            (
                row.get("source"),
                row.get("pokemon_id"),
                row.get("name"),
                row.get("form"),
                row.get("types"),
                row.get("max_cp"),
                row.get("best_moveset"),
                row.get("weaknesses"),
                row.get("resistances"),
                row.get("pve_summary"),
                row.get("pvp_summary"),
                row.get("raid_counter_summary"),
                row.get("raw_text"),
                row.get("url"),
                row.get("scraped_at"),
            ),
        )


def upsert_pokemon_knowledge_rows(rows: Iterable[dict[str, Any]]) -> int:
    count = 0
    for row in rows:
        upsert_pokemon_knowledge(row)
        count += 1
    return count


def _fetch_all(query: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    with get_connection() as conn:
        rows = conn.execute(query, params).fetchall()
    return [dict(row) for row in rows]


def _build_like_filter(terms: Iterable[str]) -> tuple[str, tuple[str, ...]]:
    clauses: list[str] = []
    params: list[str] = []
    for term in terms:
        like_term = f"%{term.lower()}%"
        for column in SEARCH_COLUMNS:
            clauses.append(f"LOWER(COALESCE({column}, '')) LIKE ?")
            params.append(like_term)
    return " OR ".join(clauses), tuple(params)


def search_pokemon_knowledge(query: str, limit: int = 10) -> list[dict[str, Any]]:
    """Search cached Pokémon knowledge by name, type, moves, summaries, and URL."""

    query = query.strip().lower()
    if not query:
        return []
    terms = [query]
    where_clause, params = _build_like_filter(terms)
    return _fetch_all(
        f"""
        SELECT * FROM pokemon_knowledge
        WHERE {where_clause}
        ORDER BY
            CASE WHEN LOWER(name) = ? THEN 0 WHEN LOWER(name) LIKE ? THEN 1 ELSE 2 END,
            name ASC,
            form ASC
        LIMIT ?
        """,
        (*params, query, f"%{query}%", limit),
    )


def get_pokemon_by_name(name: str) -> list[dict[str, Any]]:
    """Return cached Pokémon rows matching a specific Pokémon name."""

    name_query = name.strip().lower()
    if not name_query:
        return []
    return _fetch_all(
        """
        SELECT * FROM pokemon_knowledge
        WHERE LOWER(name) = ? OR LOWER(name) LIKE ?
        ORDER BY CASE WHEN LOWER(name) = ? THEN 0 ELSE 1 END, form ASC
        LIMIT 10
        """,
        (name_query, f"%{name_query}%", name_query),
    )


def get_pokemon_by_type(type_name: str, limit: int = 20) -> list[dict[str, Any]]:
    """Return cached Pokémon rows matching a Pokémon type."""

    type_query = f"%{type_name.strip().lower()}%"
    return _fetch_all(
        """
        SELECT * FROM pokemon_knowledge
        WHERE LOWER(COALESCE(types, '')) LIKE ?
        ORDER BY name ASC, form ASC
        LIMIT ?
        """,
        (type_query, limit),
    )


def get_pokemon_meta_candidates(query: str, limit: int = 10) -> list[dict[str, Any]]:
    """Return likely gameplay/meta candidates for a query."""

    rows = find_pokemon_mentions(query, limit=limit)
    if rows:
        return rows
    rows = search_pokemon_knowledge(query, limit=limit)
    if rows:
        return rows
    type_rows = _get_type_rows_from_query(query, limit=limit)
    if type_rows:
        return type_rows
    matched_terms = [term for term in META_TERMS if term in query.lower()]
    if matched_terms:
        where_clause, params = _build_like_filter(matched_terms)
        return _fetch_all(
            f"""
            SELECT * FROM pokemon_knowledge
            WHERE {where_clause}
            ORDER BY name ASC, form ASC
            LIMIT ?
            """,
            (*params, limit),
        )
    return []


def find_pokemon_mentions(query: str, limit: int = 10) -> list[dict[str, Any]]:
    """Find cached Pokémon whose names appear inside a natural-language query."""

    normalized = f" {query.strip().lower()} "
    if not normalized.strip():
        return []
    return _fetch_all(
        """
        SELECT * FROM pokemon_knowledge
        WHERE ? LIKE '% ' || LOWER(name) || ' %'
           OR ? LIKE '% ' || LOWER(name) || '?%'
           OR ? LIKE '% ' || LOWER(name) || '!%'
           OR ? LIKE '% ' || LOWER(name) || '.%'
        ORDER BY LENGTH(name) DESC, name ASC, form ASC
        LIMIT ?
        """,
        (normalized, normalized, normalized, normalized, limit),
    )


def _get_type_rows_from_query(query: str, limit: int = 10) -> list[dict[str, Any]]:
    known_types = (
        "normal",
        "fire",
        "water",
        "grass",
        "electric",
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
    )
    lowered = query.lower()
    for type_name in known_types:
        if type_name in lowered:
            return get_pokemon_by_type(type_name, limit=limit)
    return []