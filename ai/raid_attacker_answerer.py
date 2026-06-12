"""Raid attacker answer helpers for cached Pokémon GO Hub best-per-type rows."""

from __future__ import annotations

import re
from typing import Any

from ai.openai_client import call_openai_chat


MAX_CONTEXT_ROWS = 20
MAX_CONTEXT_CHARS = 6500
DEFAULT_DISPLAY_ROWS = 5
MAX_DISPLAY_ROWS = 20

_COUNT_REQUEST_PATTERN = re.compile(
    r"\b(?:top|give\s+me|show|list|get|can\s+i\s+get|can\s+you\s+give\s+me)?\s*(\d{1,3})\b",
    re.IGNORECASE,
)
_TDO_QUERY_PATTERN = re.compile(
    r"\b(?:tdo|bulk|bulky|survivability|survive|survives|surviving|staying\s+alive|stay\s+alive|stays\s+alive)\b",
    re.IGNORECASE,
)


def _user_requested_tdo(query: str | None) -> bool:
    """Return whether the user asked about bulk/survivability/TDO."""

    return bool(_TDO_QUERY_PATTERN.search(query or ""))


def _requested_row_count(query: str | None) -> int:
    """Return requested raid attacker row count, capped for one Discord response."""

    match = _COUNT_REQUEST_PATTERN.search(query or "")
    if not match:
        return DEFAULT_DISPLAY_ROWS
    try:
        requested = int(match.group(1))
    except ValueError:
        return DEFAULT_DISPLAY_ROWS
    if requested < 1:
        return DEFAULT_DISPLAY_ROWS
    return min(requested, MAX_DISPLAY_ROWS)


def _raw_requested_row_count(query: str | None) -> int | None:
    match = _COUNT_REQUEST_PATTERN.search(query or "")
    if not match:
        return None
    try:
        return int(match.group(1))
    except ValueError:
        return None


def _request_exceeds_max_rows(query: str | None) -> bool:
    requested = _raw_requested_row_count(query)
    return requested is not None and requested > MAX_DISPLAY_ROWS


def _compact_value(value: Any, fallback: str = "Unknown") -> str:
    text = str(value).strip() if value is not None else ""
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"\s+\*", "*", text)
    return text or fallback


def _pokemon_display_name(row: dict[str, Any]) -> str:
    name = _compact_value(row.get("pokemon_name"), "Unknown Pokémon")
    form = _compact_value(row.get("form"), "")
    if form and form.lower() not in name.lower():
        return f"{name} ({form})"
    return name


def _route_heading(route: str, query: str | None) -> str:
    if route == "derived_overall":
        return "Top raid attackers, derived from cached per-type rankings:"
    if route.startswith("type:"):
        return f"Top cached {route[5:].title()}-type raid attackers:"
    return f"Cached raid attacker ranking matches for '{query or 'raid attackers'}':"


def _source_line(rows: list[dict[str, Any]], route: str) -> str:
    if route == "derived_overall":
        return "Source: cached Pokémon GO Hub best-per-type tables."

    source_urls = sorted({str(row.get("url")).strip() for row in rows if str(row.get("url") or "").strip()})
    if len(source_urls) == 1:
        return f"Source: {source_urls[0]}"
    return "Source: cached Pokémon GO Hub best-per-type tables."


def _format_compact_row(index: int, row: dict[str, Any], include_tdo: bool) -> str:
    metrics = [
        f"Score {_compact_value(row.get('score'), 'unknown')}",
        f"DPS {_compact_value(row.get('dps'), 'unknown')}",
    ]
    if include_tdo:
        metrics.append(f"TDO {_compact_value(row.get('tdo'), 'unknown')}")
    return (
        f"{index}. {_pokemon_display_name(row)} — "
        f"{_compact_value(row.get('fast_move'), 'fast move unknown')} / "
        f"{_compact_value(row.get('charged_move'), 'charged move unknown')} — "
        f"{', '.join(metrics)}"
    )


def _format_compact_raid_attacker_rows(
    rows: list[dict[str, Any]],
    route: str,
    query: str | None,
    max_rows: int,
    max_chars: int = 1900,
) -> str:
    """Format raid attacker rows as compact one-line Discord ranking rows."""

    requested_rows = max(0, min(max_rows, MAX_DISPLAY_ROWS, len(rows)))
    row_count = requested_rows
    include_tdo = _user_requested_tdo(query)
    heading = _route_heading(route, query)
    capped_note = "Showing top 20 to keep the Discord message readable." if _request_exceeds_max_rows(query) else ""

    while row_count > 0:
        displayed_rows = rows[:row_count]
        lines = [heading]
        lines.extend(_format_compact_row(index, row, include_tdo) for index, row in enumerate(displayed_rows, start=1))
        if row_count < requested_rows:
            lines.append(f"Showing top {row_count} because of Discord message length.")
        elif capped_note:
            lines.append(capped_note)
        lines.append(_source_line(displayed_rows, route))
        response = "\n".join(lines)
        if len(response) <= max_chars:
            return response
        row_count -= 1

    return "I found cached raid attacker rankings, but the compact answer was still too long to fit in Discord."


def _is_compact_raid_attacker_response(response: str, query: str | None, max_rows: int, max_chars: int = 1900) -> bool:
    """Return whether an LLM response obeys the compact Discord raid attacker format."""

    if not response or len(response) > max_chars:
        return False
    lines = response.splitlines()
    if any(not line.strip() for line in lines):
        return False
    if len(lines) < 3 or re.match(r"^\d+\. ", lines[0]) or not lines[0].endswith(":"):
        return False
    numbered_rows = [line for line in lines if re.match(r"^\d+\. ", line)]
    if not numbered_rows or len(numbered_rows) > min(max_rows, MAX_DISPLAY_ROWS):
        return False
    include_tdo = _user_requested_tdo(query)
    for line in numbered_rows:
        if " — " not in line or " / " not in line or "Score " not in line or "DPS " not in line:
            return False
        if not include_tdo and "TDO" in line.upper():
            return False
    banned_patterns = (
        r"^\s*[-*•]\s+",
        r"^\s+(?:Fast Move|Charged Move|Score|DPS|TDO)\s*:",
        r"(?:Fast Move|Charged Move)\s*:",
    )
    if any(re.search(pattern, response, re.IGNORECASE | re.MULTILINE) for pattern in banned_patterns):
        return False
    source_lines = [line for line in lines if line.startswith("Source:")]
    if len(source_lines) != 1:
        return False
    if _request_exceeds_max_rows(query) and "Showing top 20 to keep the Discord message readable." not in response:
        return False
    urls_in_rows = sum(line.count("http://") + line.count("https://") for line in numbered_rows)
    return urls_in_rows == 0


def _compact_raid_attacker_context(rows: list[dict[str, Any]]) -> str:
    """Convert cached raid attacker rows into compact context for OpenAI."""

    chunks: list[str] = []
    total_chars = 0
    for index, row in enumerate(rows[:MAX_CONTEXT_ROWS], start=1):
        lines = [
            f"Raid attacker row {index}:",
            f"Rank: {row.get('rank') if row.get('rank') is not None else 'Unknown'}",
            f"Pokemon: {row.get('pokemon_name') or 'Unknown'}",
            f"Form: {row.get('form') or 'None'}",
            f"Type list: {row.get('pokemon_type') or 'Unknown'}",
            f"Ranking scope: {row.get('ranking_scope') or 'Unknown'}",
            f"Fast move: {row.get('fast_move') or 'Unknown'}",
            f"Charged move: {row.get('charged_move') or 'Unknown'}",
            f"Score: {row.get('score') or 'Unknown'}",
            f"DPS: {row.get('dps') or 'Unknown'}",
            f"TDO: {row.get('tdo') or 'Unknown'}",
            f"Summary: {row.get('summary') or 'No summary cached.'}",
            f"URL: {row.get('url') or 'Unknown'}",
        ]
        chunk = "\n".join(lines)
        if total_chars + len(chunk) > MAX_CONTEXT_CHARS:
            break
        chunks.append(chunk)
        total_chars += len(chunk)
    return "\n\n".join(chunks)


def _route_instruction(route: str) -> str:
    if route.startswith("type:"):
        pokemon_type = route[5:].title()
        return f"Tell the user these are cached {pokemon_type}-type raid attackers."
    if route == "derived_overall":
        return (
            "Tell the user this is derived from cached per-type ranking rows, sorted by Score/DPS/TDO, "
            "and is not a separate official overall list."
        )
    return "Tell the user these are cached raid attacker ranking matches."


def answer_raid_attacker_query_with_llm(query: str, rows: list[dict[str, Any]], route: str) -> str:
    """Answer a raid attacker query using only cached ranking rows."""

    if not rows:
        return "I couldn’t find matching cached raid attacker rankings for that. Ask the bot owner to run `/updateraidattackers`."

    max_rows = _requested_row_count(query)
    include_tdo = _user_requested_tdo(query)
    source_urls = sorted({str(row.get("url")) for row in rows[:max_rows] if row.get("url")})
    if route != "derived_overall" and len(source_urls) == 1:
        source_instruction = f"Include one source line at the bottom exactly as: Source: {source_urls[0]}"
    else:
        source_instruction = "Include one source line at the bottom exactly as: Source: cached Pokémon GO Hub best-per-type tables."
    tdo_instruction = "Include TDO after DPS on each row." if include_tdo else "Do not include TDO."

    system_prompt = (
        "You are a helpful Pokémon GO raid attacker assistant inside a private Discord server. "
        "Answer using only the provided cached Pokémon GO Hub best-per-type ranking rows. "
        "Do not invent Pokémon, rankings, moves, DPS, TDO, Score, sources, raid bosses, or meta data. "
        "Be concise and Discord-friendly. Start with only the compact heading, then ranked rows, then one Source line. "
        "Use exactly one line per Pokémon unless absolutely impossible. "
        "Use this exact compact row shape: `1. Pokémon — Fast Move / Charged Move — Score 28.04, DPS 31.98`. "
        "Do not add intro prose, explanatory paragraphs, Markdown tables, or blank lines. "
        "Do not use nested bullets, sub-bullets, paragraphs per Pokémon, or separate lines for Fast Move, Charged Move, Score, DPS, or TDO. "
        f"Show at most {max_rows} ranked rows. Never show more than 20 rows. {tdo_instruction} Do not repeat source URLs per row. "
        "If the user asked for more than 20, include this exact note before the Source line: Showing top 20 to keep the Discord message readable. "
        f"{_route_instruction(route)} {source_instruction} Keep the response under 1900 Discord characters."
    )
    user_prompt = f"User question:\n{query}\n\nCached raid attacker rows:\n{_compact_raid_attacker_context(rows[:max_rows])}"
    return call_openai_chat(
        [{"role": "system", "content": system_prompt}, {"role": "user", "content": user_prompt}],
        max_tokens=1000,
    )