"""PvP answer helpers for cached PvPoke ranking rows."""

from __future__ import annotations

import re
from typing import Any

from ai.openai_client import call_openai_chat


DEFAULT_PVP_DISPLAY_ROWS = 10
MAX_PVP_DISPLAY_ROWS = 20
MAX_PVP_CONTEXT_ROWS = 20
MAX_PVP_CONTEXT_CHARS = 6500
MAX_DISCORD_CHARS = 1900

_COUNT_REQUEST_PATTERN = re.compile(
    r"\b(?:top|give\s+me|show|list|get|can\s+i\s+get|can\s+you\s+give\s+me)?\s*(\d{1,3})\b",
    re.IGNORECASE,
)

_LEAGUE_NAMES = {"great": "Great League", "ultra": "Ultra League", "master": "Master League"}
_LEAGUE_URLS = {
    "great": "https://pvpoke.com/rankings/all/1500/overall/",
    "ultra": "https://pvpoke.com/rankings/all/2500/overall/",
    "master": "https://pvpoke.com/rankings/all/10000/overall/",
}


def _requested_pvp_row_count(query: str | None) -> int:
    """Return requested PvP row count, capped for one Discord response."""

    match = _COUNT_REQUEST_PATTERN.search(query or "")
    if not match:
        return DEFAULT_PVP_DISPLAY_ROWS
    try:
        requested = int(match.group(1))
    except ValueError:
        return DEFAULT_PVP_DISPLAY_ROWS
    if requested < 1:
        return DEFAULT_PVP_DISPLAY_ROWS
    return min(requested, MAX_PVP_DISPLAY_ROWS)


def _raw_requested_pvp_row_count(query: str | None) -> int | None:
    match = _COUNT_REQUEST_PATTERN.search(query or "")
    if not match:
        return None
    try:
        return int(match.group(1))
    except ValueError:
        return None


def _pvp_request_exceeds_max_rows(query: str | None) -> bool:
    requested = _raw_requested_pvp_row_count(query)
    return requested is not None and requested > MAX_PVP_DISPLAY_ROWS


def _format_pvp_league_name(league: str) -> str:
    """Return a user-facing PvP league name."""

    return _LEAGUE_NAMES.get((league or "").strip().lower(), (league or "PvP").strip().title())


def _source_line_for_pvp(rows: list[dict], league: str | None) -> str:
    """Return exactly one PvP source line for a compact Discord answer."""

    normalized_league = (league or "").strip().lower()
    if normalized_league in _LEAGUE_URLS:
        return f"Source: {_LEAGUE_URLS[normalized_league]}"
    return "Source: cached PvPoke rankings."


def _compact_value(value: Any, fallback: str = "Unknown") -> str:
    text = str(value).strip() if value is not None else ""
    text = re.sub(r"\s+", " ", text)
    return text or fallback


def _pokemon_display_name(row: dict[str, Any]) -> str:
    name = _compact_value(row.get("pokemon_name"), "Unknown Pokémon")
    form = _compact_value(row.get("form"), "")
    if form and form.lower() not in name.lower():
        return f"{name} ({form})"
    return name


def _type_text(row: dict[str, Any]) -> str:
    type_1 = _compact_value(row.get("type_1"), "")
    type_2 = _compact_value(row.get("type_2"), "")
    if type_1 and type_2:
        return f"{type_1}/{type_2}"
    return type_1 or type_2 or "Type unknown"


def _move_text(row: dict[str, Any]) -> str:
    fast = _compact_value(row.get("fast_move"), "Fast move unknown")
    charged_1 = _compact_value(row.get("charged_move_1"), "")
    charged_2 = _compact_value(row.get("charged_move_2"), "")
    charged = " / ".join(move for move in (charged_1, charged_2) if move)
    if charged:
        return f"{fast} | {charged}"
    return fast


def _score_text(row: dict[str, Any]) -> str:
    return f"Score {_compact_value(row.get('score'), 'unknown')}"


def _format_league_row(index: int, row: dict[str, Any]) -> str:
    rank = row.get("rank")
    prefix = f"{rank}." if rank is not None else f"{index}."
    return f"{prefix} {_pokemon_display_name(row)} — {_type_text(row)} — {_move_text(row)} — {_score_text(row)}"


def _format_pokemon_row(row: dict[str, Any]) -> str:
    league = _format_pvp_league_name(str(row.get("league") or "PvP"))
    rank = f"#{row.get('rank')}" if row.get("rank") is not None else "rank unknown"
    return f"- {league}: {rank} — {_type_text(row)} — {_move_text(row)} — {_score_text(row)}"


def _overview_response() -> str:
    return (
        "PvP rankings are cached for Great, Ultra, and Master League.\n"
        "Try `/pvp great`, `/pvp ultra`, `/pvp master`, or `/pvp Azumarill great`.\n"
        "Source: cached PvPoke rankings."
    )


def format_compact_pvp_rankings(query: str | None, rows: list[dict], league: str | None, route: str, max_rows: int = 10) -> str:
    """Format cached PvPoke rows as a compact Discord answer without live scraping."""

    if route == "overview":
        return _overview_response()

    requested_rows = max(0, min(max_rows, MAX_PVP_DISPLAY_ROWS, len(rows)))
    if requested_rows == 0:
        return "No matching cached PvPoke rankings were found.\nSource: cached PvPoke rankings."

    row_count = requested_rows
    capped_note = "Showing top 20 to keep the Discord message readable." if _pvp_request_exceeds_max_rows(query) else ""
    is_pokemon_route = route == "pokemon_search"

    while row_count > 0:
        displayed_rows = rows[:row_count]
        if is_pokemon_route:
            heading_name = _pokemon_display_name(displayed_rows[0])
            lines = [f"{heading_name} in cached PvPoke rankings:"]
            lines.extend(_format_pokemon_row(row) for row in displayed_rows)
            source_line = "Source: cached PvPoke rankings."
        else:
            heading_league = _format_pvp_league_name(league or str(displayed_rows[0].get("league") or "PvP"))
            lines = [f"Top {heading_league} PvP rankings:"]
            lines.extend(_format_league_row(index, row) for index, row in enumerate(displayed_rows, start=1))
            source_line = _source_line_for_pvp(displayed_rows, league)

        if row_count < requested_rows:
            lines.append(f"Showing top {row_count} because of Discord message length.")
        elif capped_note:
            lines.append(capped_note)
        lines.append(source_line)
        response = "\n".join(lines)
        if len(response) <= MAX_DISCORD_CHARS:
            return response
        row_count -= 1

    return "I found cached PvPoke rankings, but the compact answer was still too long to fit in Discord."


def _compact_pvp_context(rows: list[dict[str, Any]]) -> str:
    chunks: list[str] = []
    total_chars = 0
    for index, row in enumerate(rows[:MAX_PVP_CONTEXT_ROWS], start=1):
        chunk = "\n".join(
            [
                f"PvP row {index}:",
                f"League: {_format_pvp_league_name(str(row.get('league') or ''))}",
                f"Rank: {row.get('rank') if row.get('rank') is not None else 'Unknown'}",
                f"Pokemon: {row.get('pokemon_name') or 'Unknown'}",
                f"Form: {row.get('form') or 'None'}",
                f"Types: {_type_text(row)}",
                f"Fast move: {row.get('fast_move') or 'Unknown'}",
                f"Charged move 1: {row.get('charged_move_1') or 'Unknown'}",
                f"Charged move 2: {row.get('charged_move_2') or 'Unknown'}",
                f"Score: {row.get('score') or 'Unknown'}",
                f"URL: {row.get('url') or 'Unknown'}",
            ]
        )
        if total_chars + len(chunk) > MAX_PVP_CONTEXT_CHARS:
            break
        chunks.append(chunk)
        total_chars += len(chunk)
    return "\n\n".join(chunks)


def answer_pvp_query_with_llm(query: str, rows: list[dict[str, Any]], league: str | None, route: str, fallback: str) -> str:
    """Answer a PvP query using only cached PvPoke rows as grounding context."""

    if not rows or route == "overview":
        return fallback
    max_rows = _requested_pvp_row_count(query)
    if route == "pokemon_search":
        source_instruction = "Include one source line at the bottom exactly as: Source: cached PvPoke rankings."
        row_shape = "Use bullet rows like `- Great League: #24 — Water/Fairy — Bubble | Ice Beam / Play Rough — Score 88.7`."
    else:
        source_instruction = f"Include one source line at the bottom exactly as: {_source_line_for_pvp(rows, league)}"
        row_shape = "Use numbered rows like `1. Lickilicky — Normal — Rollout | Body Slam / Shadow Ball — Score 95.4`."
    system_prompt = (
        "You are a helpful Pokémon GO PvP assistant inside Discord. "
        "Answer using only the provided cached PvPoke ranking rows. Do not scrape live data. "
        "Do not invent Pokémon, leagues, ranks, moves, scores, or sources. "
        "Be compact: heading, one line per row, then exactly one Source line. "
        "Do not add Markdown tables, blank lines, source URLs per row, or explanatory paragraphs. "
        f"Show at most {max_rows} rows and never more than 20. {row_shape} {source_instruction} "
        "Keep the response under 1900 Discord characters."
    )
    user_prompt = f"User question:\n{query}\n\nCached PvPoke rows:\n{_compact_pvp_context(rows[:max_rows])}"
    return call_openai_chat(
        [{"role": "system", "content": system_prompt}, {"role": "user", "content": user_prompt}],
        max_tokens=1000,
    )


def is_compact_pvp_response(response: str, query: str | None, max_rows: int, max_chars: int = MAX_DISCORD_CHARS) -> bool:
    """Return whether an LLM response obeys the compact PvP Discord format."""

    if not response or len(response) > max_chars:
        return False
    lines = response.splitlines()
    if any(not line.strip() for line in lines):
        return False
    source_lines = [line for line in lines if line.startswith("Source:")]
    if len(source_lines) != 1:
        return False
    if len(lines) < 3 or re.match(r"^(?:\d+\. |- )", lines[0]) or not lines[0].endswith(":"):
        return False
    numbered_rows = [line for line in lines if re.match(r"^\d+\. ", line)]
    bullet_rows = [line for line in lines if line.startswith("- ")]
    row_lines = numbered_rows or bullet_rows
    if not row_lines or len(row_lines) > min(max_rows, MAX_PVP_DISPLAY_ROWS):
        return False
    if _pvp_request_exceeds_max_rows(query) and "Showing top 20 to keep the Discord message readable." not in response:
        return False
    for line in row_lines:
        if " — " not in line or "Score " not in line:
            return False
        if line.count("http://") or line.count("https://"):
            return False
    return True