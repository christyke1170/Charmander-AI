"""SQLite helpers for cached Pokémon GO Hub Pokémon/form pages."""

from __future__ import annotations

import json
import logging
from typing import Any, Iterable

from database.db import get_connection


logger = logging.getLogger(__name__)

SOURCE_NAME = "pokemongohub_pokemon_db"

POKEMON_GO_FORMS_SCHEMA = """
CREATE TABLE IF NOT EXISTS pokemon_go_forms (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL,
    dex_number INTEGER NOT NULL,
    pokemon_name TEXT NOT NULL,
    form TEXT,
    type_1 TEXT,
    type_2 TEXT,
    fast_moves TEXT,
    charged_moves TEXT,
    elite_fast_moves TEXT,
    elite_charged_moves TEXT,
    attack INTEGER,
    defense INTEGER,
    stamina INTEGER,
    max_cp INTEGER,
    is_shadow INTEGER DEFAULT 0,
    is_mega INTEGER DEFAULT 0,
    is_gigantamax INTEGER DEFAULT 0,
    url TEXT NOT NULL,
    scraped_at TEXT NOT NULL,
    UNIQUE(source, dex_number, pokemon_name, form, url)
);
"""

SEARCH_COLUMNS = (
    "pokemon_name",
    "form",
    "type_1",
    "type_2",
    "fast_moves",
    "charged_moves",
    "elite_fast_moves",
    "elite_charged_moves",
    "url",
)


def init_pokemon_go_forms_tables() -> None:
    """Create cached Pokémon GO form tables if needed."""

    with get_connection() as conn:
        conn.execute(POKEMON_GO_FORMS_SCHEMA)
    logger.info("Pokémon GO forms table initialized")


def _clean_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _clean_optional_json_list(value: Any) -> str | None:
    if value is None or value == "":
        return None
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            return json.dumps([part.strip() for part in text.split("|") if part.strip()], ensure_ascii=False)
        if isinstance(parsed, list):
            cleaned = [str(item).strip() for item in parsed if str(item).strip()]
            return json.dumps(cleaned, ensure_ascii=False) if cleaned else None
        return json.dumps([str(parsed).strip()], ensure_ascii=False)
    if isinstance(value, (list, tuple, set)):
        cleaned = [str(item).strip() for item in value if str(item).strip()]
        return json.dumps(cleaned, ensure_ascii=False) if cleaned else None
    return json.dumps([str(value).strip()], ensure_ascii=False)


def _clean_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return int(value)
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        logger.warning("Ignoring invalid integer value for pokemon_go_forms: %r", value)
        return None


def _normalize_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "source": _clean_text(row.get("source")) or SOURCE_NAME,
        "dex_number": _clean_int(row.get("dex_number")),
        "pokemon_name": _clean_text(row.get("pokemon_name") or row.get("name")),
        "form": _clean_text(row.get("form")),
        "type_1": _clean_text(row.get("type_1")),
        "type_2": _clean_text(row.get("type_2")),
        "fast_moves": _clean_optional_json_list(row.get("fast_moves")),
        "charged_moves": _clean_optional_json_list(row.get("charged_moves")),
        "elite_fast_moves": _clean_optional_json_list(row.get("elite_fast_moves")),
        "elite_charged_moves": _clean_optional_json_list(row.get("elite_charged_moves")),
        "attack": _clean_int(row.get("attack")),
        "defense": _clean_int(row.get("defense")),
        "stamina": _clean_int(row.get("stamina")),
        "max_cp": _clean_int(row.get("max_cp")),
        "is_shadow": 1 if bool(row.get("is_shadow")) else 0,
        "is_mega": 1 if bool(row.get("is_mega")) else 0,
        "is_gigantamax": 1 if bool(row.get("is_gigantamax")) else 0,
        "url": _clean_text(row.get("url")),
        "scraped_at": _clean_text(row.get("scraped_at")),
    }


def upsert_pokemon_go_form(row: dict[str, Any]) -> None:
    """Insert or update one cached Pokémon GO Hub Pokémon/form row."""

    normalized = _normalize_row(row)
    required = ("source", "dex_number", "pokemon_name", "url", "scraped_at")
    missing = [field for field in required if not normalized.get(field)]
    if missing:
        raise ValueError(
            "Pokémon GO forms rows require "
            f"{', '.join(required)}; missing {', '.join(missing)}"
        )

    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO pokemon_go_forms (
                source, dex_number, pokemon_name, form, type_1, type_2,
                fast_moves, charged_moves, elite_fast_moves, elite_charged_moves,
                attack, defense, stamina, max_cp,
                is_shadow, is_mega, is_gigantamax,
                url, scraped_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(source, dex_number, pokemon_name, form, url) DO UPDATE SET
                type_1 = excluded.type_1,
                type_2 = excluded.type_2,
                fast_moves = excluded.fast_moves,
                charged_moves = excluded.charged_moves,
                elite_fast_moves = excluded.elite_fast_moves,
                elite_charged_moves = excluded.elite_charged_moves,
                attack = excluded.attack,
                defense = excluded.defense,
                stamina = excluded.stamina,
                max_cp = excluded.max_cp,
                is_shadow = excluded.is_shadow,
                is_mega = excluded.is_mega,
                is_gigantamax = excluded.is_gigantamax,
                scraped_at = excluded.scraped_at
            """,
            (
                normalized.get("source"),
                normalized.get("dex_number"),
                normalized.get("pokemon_name"),
                normalized.get("form"),
                normalized.get("type_1"),
                normalized.get("type_2"),
                normalized.get("fast_moves"),
                normalized.get("charged_moves"),
                normalized.get("elite_fast_moves"),
                normalized.get("elite_charged_moves"),
                normalized.get("attack"),
                normalized.get("defense"),
                normalized.get("stamina"),
                normalized.get("max_cp"),
                normalized.get("is_shadow"),
                normalized.get("is_mega"),
                normalized.get("is_gigantamax"),
                normalized.get("url"),
                normalized.get("scraped_at"),
            ),
        )


def upsert_pokemon_go_forms(rows: Iterable[dict[str, Any]]) -> int:
    count = 0
    for row in rows:
        upsert_pokemon_go_form(row)
        count += 1
    return count


def _fetch_all(query: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    with get_connection() as conn:
        rows = conn.execute(query, params).fetchall()
    return [dict(row) for row in rows]


def count_pokemon_go_forms() -> int:
    with get_connection() as conn:
        row = conn.execute("SELECT COUNT(*) AS count FROM pokemon_go_forms").fetchone()
    return int(row["count"] if row else 0)


def get_pokemon_go_forms_by_name(name: str) -> list[dict[str, Any]]:
    normalized = name.strip().lower()
    if not normalized:
        return []
    return _fetch_all(
        """
        SELECT * FROM pokemon_go_forms
        WHERE LOWER(pokemon_name) = ? OR LOWER(pokemon_name) LIKE ?
        ORDER BY CASE WHEN LOWER(pokemon_name) = ? THEN 0 ELSE 1 END, dex_number ASC, form ASC
        LIMIT 50
        """,
        (normalized, f"%{normalized}%", normalized),
    )


def get_pokemon_go_forms_by_dex(dex_number: int) -> list[dict[str, Any]]:
    return _fetch_all(
        """
        SELECT * FROM pokemon_go_forms
        WHERE dex_number = ?
        ORDER BY pokemon_name ASC, form ASC, url ASC
        """,
        (dex_number,),
    )


def search_pokemon_go_forms(query: str, limit: int = 20) -> list[dict[str, Any]]:
    normalized = query.strip().lower()
    if not normalized:
        return []
    clauses: list[str] = []
    params: list[str] = []
    like = f"%{normalized}%"
    for column in SEARCH_COLUMNS:
        clauses.append(f"LOWER(COALESCE({column}, '')) LIKE ?")
        params.append(like)
    where_clause = " OR ".join(clauses)
    return _fetch_all(
        f"""
        SELECT * FROM pokemon_go_forms
        WHERE {where_clause}
        ORDER BY
            CASE
                WHEN LOWER(pokemon_name) = ? THEN 0
                WHEN LOWER(pokemon_name) LIKE ? THEN 1
                ELSE 2
            END,
            dex_number ASC,
            pokemon_name ASC,
            form ASC
        LIMIT ?
        """,
        (*params, normalized, like, limit),
    )