"""Pokémon knowledge answer helpers for cached Pokémon GO Hub rows."""

from __future__ import annotations

from typing import Any

from ai.openai_client import call_openai_chat


MAX_CONTEXT_ROWS = 10
MAX_SNIPPET_CHARS = 600
MAX_CONTEXT_CHARS = 7500


def answer_pokemon_query(query: str, pokemon_rows: list[dict[str, Any]]) -> str:
    """Return compact cached Pokémon snippets without using an LLM."""

    if not pokemon_rows:
        return "No local Pokémon knowledge was found. Ask the bot owner to run `/updatepokemon`."

    lines = [f"Top cached Pokémon matches for '{query}':"]
    for row in pokemon_rows[:5]:
        name = row.get("name") or "Unknown Pokémon"
        form = f" ({row.get('form')})" if row.get("form") else ""
        types = row.get("types") or "types unknown"
        moves = row.get("best_moveset") or "moveset not cached"
        summary = row.get("pve_summary") or row.get("pvp_summary") or row.get("raw_text") or "No summary cached."
        url = row.get("url") or "No URL cached"
        lines.append(f"- **{name}{form}** — {types}\n  Moves: {str(moves)[:180]}\n  {str(summary)[:220]}\n  {url}")
    return "\n".join(lines)


def compact_pokemon_context(pokemon_rows: list[dict[str, Any]]) -> str:
    """Convert cached Pokémon rows into compact context for OpenAI."""

    chunks: list[str] = []
    total_chars = 0
    for index, row in enumerate(pokemon_rows[:MAX_CONTEXT_ROWS], start=1):
        snippet = row.get("raw_text") or ""
        snippet = " ".join(str(snippet).split())[:MAX_SNIPPET_CHARS]
        lines = [
            f"Pokemon row {index}:",
            f"Name: {row.get('name') or 'Unknown'}",
            f"Form: {row.get('form') or 'Unknown'}",
            f"Pokemon ID: {row.get('pokemon_id') or 'Unknown'}",
            f"Types: {row.get('types') or 'Unknown'}",
            f"Max CP: {row.get('max_cp') or 'Unknown'}",
            f"Best moveset: {row.get('best_moveset') or 'Unknown'}",
            f"Weaknesses: {row.get('weaknesses') or 'Unknown'}",
            f"Resistances: {row.get('resistances') or 'Unknown'}",
            f"PvE summary: {row.get('pve_summary') or 'Unknown'}",
            f"PvP summary: {row.get('pvp_summary') or 'Unknown'}",
            f"Counter summary: {row.get('raid_counter_summary') or 'Unknown'}",
            f"Source: {row.get('source') or 'Unknown'}",
            f"URL: {row.get('url') or 'Unknown'}",
            f"Raw snippet: {snippet or 'No raw text cached.'}",
        ]
        chunk = "\n".join(lines)
        if total_chars + len(chunk) > MAX_CONTEXT_CHARS:
            break
        chunks.append(chunk)
        total_chars += len(chunk)
    return "\n\n".join(chunks)


def answer_pokemon_query_with_llm(query: str, pokemon_rows: list[dict[str, Any]]) -> str:
    """Answer Pokémon gameplay questions from cached Pokémon GO Hub context only."""

    if not pokemon_rows:
        return "I couldn’t find matching local Pokémon knowledge for that. Ask the bot owner to run `/updatepokemon`."

    system_prompt = (
        "You are a helpful Pokémon GO gameplay assistant inside a private Discord server. "
        "Answer using only the provided cached Pokémon GO Hub database context. "
        "Be concise and practical. For PvE, focus on raid/gym attacker relevance, types, movesets, "
        "DPS/TDO-style usefulness if available, and counters. For PvP, mention Great League, Ultra League, "
        "or Master League only if supported by the context. If the context does not support the answer, "
        "say you do not know based on cached Pokémon data and suggest `/updatepokemon`. "
        "Do not invent rankings, moves, stats, raid bosses, PvP rankings, or shiny availability. "
        "Keep the response under 1900 Discord characters."
    )
    user_prompt = f"User question:\n{query}\n\nRetrieved cached Pokémon GO Hub rows:\n{compact_pokemon_context(pokemon_rows)}"
    return call_openai_chat(
        [{"role": "system", "content": system_prompt}, {"role": "user", "content": user_prompt}],
        max_tokens=500,
    )


def answer_mixed_query_with_llm(query: str, event_context: str, pokemon_rows: list[dict[str, Any]]) -> str:
    """Answer an ambiguous question using both event and Pokémon contexts."""

    if not event_context and not pokemon_rows:
        return "I couldn’t find matching local event or Pokémon knowledge. Try `/events`, `/pokemon`, or ask the owner to run `/update` and `/updatepokemon`."
    system_prompt = (
        "You are a helpful Pokémon GO assistant. Answer using only the provided local contexts: "
        "cached event/news rows and cached Pokémon GO Hub rows. If a claim is not supported, say you do not know. "
        "Do not invent live data, rankings, moves, counters, dates, bonuses, or shiny availability. Be concise and practical."
    )
    user_prompt = (
        f"User question:\n{query}\n\n"
        f"Local event/news context:\n{event_context or 'None'}\n\n"
        f"Cached Pokémon GO Hub context:\n{compact_pokemon_context(pokemon_rows) if pokemon_rows else 'None'}"
    )
    return call_openai_chat(
        [{"role": "system", "content": system_prompt}, {"role": "user", "content": user_prompt}],
        max_tokens=500,
    )