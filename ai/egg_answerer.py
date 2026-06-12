"""Egg pool answer helpers for cached LeekDuck egg rows."""

from __future__ import annotations

import re
from collections import OrderedDict
from typing import Any

from ai.openai_client import call_openai_chat


SOURCE_URL = "https://leekduck.com/eggs/"
MAX_CONTEXT_ROWS = 80
MAX_CONTEXT_CHARS = 6500


def _compact_value(value: Any, fallback: str = "") -> str:
    text = str(value).strip() if value is not None else ""
    text = re.sub(r"\s+", " ", text)
    return text or fallback


def group_egg_rows_by_pool(rows: list[dict[str, Any]]) -> "OrderedDict[str, list[dict[str, Any]]]":
    grouped: "OrderedDict[str, list[dict[str, Any]]]" = OrderedDict()
    for row in rows:
        pool_name = _compact_value(row.get("pool_name"), "Unknown Egg Pool")
        grouped.setdefault(pool_name, []).append(row)
    return grouped


def _pokemon_names(rows: list[dict[str, Any]]) -> str:
    names: list[str] = []
    seen: set[str] = set()
    for row in rows:
        name = _compact_value(row.get("pokemon_name"), "Unknown Pokémon")
        key = name.lower()
        if key in seen:
            continue
        seen.add(key)
        names.append(name)
    return ", ".join(names)


def _source_line() -> str:
    return f"Source: {SOURCE_URL}"


def format_egg_overview(sections: list[str]) -> str:
    """Return compact overview of cached egg pool sections."""

    if not sections:
        return "The egg pool cache is empty. Ask the bot owner to run `/updateeggs`."
    lines = ["Cached egg pools:"]
    lines.extend(f"- {section}" for section in sections)
    lines.append("")
    lines.append("Ask things like `/eggs 10km`, `/eggs adventure sync`, or `/eggs Larvesta`.")
    lines.append(_source_line())
    return "\n".join(lines)


def format_egg_distance_response(distance_km: int, rows: list[dict[str, Any]], pool_type: str | None = None) -> str:
    """Return compact grouped rows for a distance/special-pool query."""

    if not rows:
        suffix = f" {pool_type.replace('_', ' ')}" if pool_type else ""
        return f"No cached {distance_km} km{suffix} egg rows were found. Ask the bot owner to run `/updateeggs`."
    lines: list[str] = []
    grouped = group_egg_rows_by_pool(rows)
    for index, (pool_name, pool_rows) in enumerate(grouped.items()):
        heading = pool_name if index else f"Current {pool_name}:"
        if not heading.endswith(":"):
            heading += ":"
        lines.append(heading)
        lines.append(_pokemon_names(pool_rows))
        lines.append("")
    lines.append(_source_line())
    return "\n".join(lines).strip()


def format_egg_pool_response(rows: list[dict[str, Any]], query: str) -> str:
    """Return compact grouped rows for a pool-name or special-pool query."""

    if not rows:
        return f"No cached egg pool rows matched `{query}`. Ask the bot owner to run `/updateeggs`."
    lines: list[str] = []
    for pool_name, pool_rows in group_egg_rows_by_pool(rows).items():
        lines.append(f"{pool_name}:")
        lines.append(_pokemon_names(pool_rows))
        lines.append("")
    lines.append(_source_line())
    return "\n".join(lines).strip()


def format_egg_pokemon_response(pokemon_query: str, rows: list[dict[str, Any]]) -> str:
    """Return compact response listing which pools contain a Pokémon."""

    display_name = _compact_value(rows[0].get("pokemon_name"), pokemon_query.strip().title()) if rows else pokemon_query.strip().title()
    if not rows:
        return f"{display_name} is not currently listed in cached egg pools.\n{_source_line()}"
    pools: list[str] = []
    seen: set[str] = set()
    for row in rows:
        pool_name = _compact_value(row.get("pool_name"), "Unknown Egg Pool")
        key = pool_name.lower()
        if key not in seen:
            seen.add(key)
            pools.append(pool_name)
    lines = [f"{display_name} is currently listed in:"]
    lines.extend(f"- {pool}" for pool in pools)
    lines.append(_source_line())
    return "\n".join(lines)


def _compact_context(rows: list[dict[str, Any]]) -> str:
    chunks: list[str] = []
    total_chars = 0
    for index, row in enumerate(rows[:MAX_CONTEXT_ROWS], start=1):
        chunk = (
            f"Egg row {index}: pool={row.get('pool_name')}; distance={row.get('egg_distance_km')}; "
            f"type={row.get('pool_type')}; pokemon={row.get('pokemon_name')}; cp={row.get('cp_text')}; "
            f"shiny={row.get('shiny_available')}; rarity={row.get('rarity_text')}; notes={row.get('notes')}; url={row.get('url')}"
        )
        if total_chars + len(chunk) > MAX_CONTEXT_CHARS:
            break
        chunks.append(chunk)
        total_chars += len(chunk)
    return "\n".join(chunks)


def _valid_compact_egg_response(response: str, max_chars: int = 1900) -> bool:
    if not response or len(response) > max_chars:
        return False
    if response.count(SOURCE_URL) != 1:
        return False
    if len([line for line in response.splitlines() if line.startswith("Source:")]) != 1:
        return False
    return True


def answer_egg_query_with_llm(query: str, rows: list[dict[str, Any]], fallback_answer: str) -> str:
    """Answer an egg query using only cached egg rows, with compact fallback validation."""

    if not rows:
        return fallback_answer
    system_prompt = (
        "You are a Pokémon GO egg pool assistant inside Discord. Answer using only the provided cached LeekDuck egg rows. "
        "Do not invent hatch pools, Pokémon, CP values, rarity, shiny availability, or sources. Keep the answer compact. "
        "Group by pool name for pool/distance questions. For Pokémon questions, list only pool names that contain that Pokémon. "
        f"Include exactly one final source line: Source: {SOURCE_URL}. Keep under 1900 Discord characters."
    )
    user_prompt = f"User question:\n{query}\n\nCached egg rows:\n{_compact_context(rows)}"
    answer = call_openai_chat(
        [{"role": "system", "content": system_prompt}, {"role": "user", "content": user_prompt}],
        max_tokens=700,
    )
    if _valid_compact_egg_response(answer):
        return answer
    return fallback_answer
