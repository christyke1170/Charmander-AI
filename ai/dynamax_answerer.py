"""Dynamax/Gigantamax attacker answer helpers for cached Pokémon GO Hub rows."""

from __future__ import annotations

from typing import Any

from ai.raid_attacker_answerer import (
    MAX_DISPLAY_ROWS,
    _compact_value,
    _is_compact_raid_attacker_response,
    _pokemon_display_name,
    _requested_row_count,
    _request_exceeds_max_rows,
)
from ai.openai_client import call_openai_chat


def _route_heading(route: str, query: str | None) -> str:
    if route == "derived_overall":
        return "Top Dynamax attackers, derived from cached per-type rankings:"
    if route.startswith("type:"):
        return f"Top cached {route[5:].title()}-type Dynamax attackers:"
    return f"Cached Dynamax attacker ranking matches for '{query or 'dynamax attackers'}':"


def _source_line(rows: list[dict[str, Any]], route: str) -> str:
    if route == "derived_overall":
        return "Source: cached Pokémon GO Hub Dynamax attacker tables."
    source_urls = sorted({str(row.get("url")).strip() for row in rows if str(row.get("url") or "").strip()})
    if len(source_urls) == 1:
        return f"Source: {source_urls[0]}"
    return "Source: cached Pokémon GO Hub Dynamax attacker tables."


def _format_compact_row(index: int, row: dict[str, Any]) -> str:
    base = (
        f"{index}. {_pokemon_display_name(row)} — "
        f"{_compact_value(row.get('fast_move'), 'fast move unknown')} / "
        f"{_compact_value(row.get('charged_move'), 'charged move unknown')}"
    )
    if not row.get("score"):
        return base
    return f"{base} — Max Move Damage {_compact_value(row.get('score'), 'unknown')}"


def format_compact_dynamax_attacker_rows(
    rows: list[dict[str, Any]],
    route: str,
    query: str | None,
    max_rows: int,
    max_chars: int = 1900,
) -> str:
    """Format Dynamax attacker rows as compact one-line Discord ranking rows."""

    requested_rows = max(0, min(max_rows, MAX_DISPLAY_ROWS, len(rows)))
    row_count = requested_rows
    heading = _route_heading(route, query)
    capped_note = "Showing top 20 to keep the Discord message readable." if _request_exceeds_max_rows(query) else ""

    while row_count > 0:
        displayed_rows = rows[:row_count]
        lines = [heading]
        lines.extend(_format_compact_row(index, row) for index, row in enumerate(displayed_rows, start=1))
        if row_count < requested_rows:
            lines.append(f"Showing top {row_count} because of Discord message length.")
        elif capped_note:
            lines.append(capped_note)
        lines.append(_source_line(displayed_rows, route))
        response = "\n".join(lines)
        if len(response) <= max_chars:
            return response
        row_count -= 1

    return "I found cached Dynamax attacker rankings, but the compact answer was still too long to fit in Discord."


def _compact_context(rows: list[dict[str, Any]]) -> str:
    chunks: list[str] = []
    for index, row in enumerate(rows[:20], start=1):
        chunks.append(
            "\n".join(
                [
                    f"Dynamax attacker row {index}:",
                    f"Rank: {row.get('rank') if row.get('rank') is not None else 'Unknown'}",
                    f"Pokemon: {row.get('pokemon_name') or 'Unknown'}",
                    f"Type: {row.get('pokemon_type') or 'Unknown'}",
                    f"Ranking scope: {row.get('ranking_scope') or 'Unknown'}",
                    f"Fast move: {row.get('fast_move') or 'Unknown'}",
                    f"Charged/Max move: {row.get('charged_move') or 'Unknown'}",
                    f"Max Move Damage: {row.get('score') or 'Unknown'}",
                    f"DPS: {row.get('dps') or 'Unknown'}",
                    f"TDO: {row.get('tdo') or 'Unknown'}",
                    f"URL: {row.get('url') or 'Unknown'}",
                ]
            )
        )
    return "\n\n".join(chunks)[:6500]


def answer_dynamax_query_with_llm(query: str, rows: list[dict[str, Any]], route: str) -> str:
    """Answer a Dynamax attacker query using only cached ranking rows."""

    if not rows:
        return "I couldn’t find matching cached Dynamax attacker rankings for that. Ask the bot owner to run `/updatedynamax`."
    max_rows = _requested_row_count(query)
    source_urls = sorted({str(row.get("url")) for row in rows[:max_rows] if row.get("url")})
    if route != "derived_overall" and len(source_urls) == 1:
        source_instruction = f"Include one source line at the bottom exactly as: Source: {source_urls[0]}"
    else:
        source_instruction = "Include one source line at the bottom exactly as: Source: cached Pokémon GO Hub Dynamax attacker tables."
    system_prompt = (
        "You are a helpful Pokémon GO Dynamax/Gigantamax attacker assistant inside Discord. "
        "Answer using only the provided cached Pokémon GO Hub Dynamax attacker rows. "
        "Do not invent Pokémon, rankings, moves, DPS, TDO, Max Move Damage, sources, bosses, or meta data. "
        "Use compact Discord format: heading, numbered one-line rows, then one Source line. "
        "Use this row shape: `1. Pokémon — Fast Move / Max Move — Max Move Damage 284.85`. "
        "Do not add Markdown tables, blank lines, or source URLs per row. "
        f"Show at most {max_rows} rows, never more than 20. {source_instruction} Keep under 1900 characters."
    )
    return call_openai_chat(
        [{"role": "system", "content": system_prompt}, {"role": "user", "content": f"Question:\n{query}\n\nRows:\n{_compact_context(rows[:max_rows])}"}],
        max_tokens=1000,
    )


def is_compact_dynamax_response(response: str, query: str | None, max_rows: int, max_chars: int = 1900) -> bool:
    return _is_compact_raid_attacker_response(response, query, max_rows, max_chars) and "dynamax" in response.lower()
