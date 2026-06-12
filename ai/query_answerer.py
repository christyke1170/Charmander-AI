"""Local query answering helpers, including an optional OpenAI RAG layer."""

from __future__ import annotations

from typing import Any

from ai.openai_client import call_openai_chat


MAX_CONTEXT_EVENTS = 10
MAX_SNIPPET_CHARS = 500
MAX_CONTEXT_CHARS = 6500


def answer_query(query: str, events: list[dict[str, Any]]) -> str:
    """Return matching event snippets for a query without using an LLM."""

    if not events:
        return f"No local events matched: {query}"

    lines = [f"Top local matches for '{query}':"]
    for event in events[:5]:
        title = event.get("title", "Untitled event")
        start = event.get("start_time") or "date unknown"
        summary = event.get("summary") or event.get("raw_text") or "No summary available."
        url = event.get("url") or "No URL available"
        lines.append(f"- **{title}** ({start})\n  {summary[:220]}\n  {url}")
    return "\n".join(lines)


def _compact_event_context(events: list[dict[str, Any]]) -> str:
    """Convert retrieved event rows into compact context for the LLM."""

    chunks: list[str] = []
    total_chars = 0
    for index, event in enumerate(events[:MAX_CONTEXT_EVENTS], start=1):
        snippet = event.get("summary") or event.get("raw_text") or ""
        snippet = " ".join(str(snippet).split())[:MAX_SNIPPET_CHARS]
        lines = [
            f"Event {index}:",
            f"Title: {event.get('title') or 'Unknown'}",
            f"Category: {event.get('category') or 'Unknown'}",
            f"Start: {event.get('start_time') or 'Unknown'}",
            f"End: {event.get('end_time') or 'Unknown'}",
            f"Source: {event.get('source') or 'Unknown'}",
            f"URL: {event.get('url') or 'Unknown'}",
            f"Snippet: {snippet or 'No summary available.'}",
        ]
        chunk = "\n".join(lines)
        if total_chars + len(chunk) > MAX_CONTEXT_CHARS:
            break
        chunks.append(chunk)
        total_chars += len(chunk)
    return "\n\n".join(chunks)


def answer_query_with_llm(query: str, events: list[dict[str, Any]]) -> str:
    """Answer a user query using only retrieved local event context."""

    if not events:
        return "I couldn’t find matching local Pokémon GO event data for that. Try `/events` or ask the bot owner to run `/update`."

    context = _compact_event_context(events)
    system_prompt = (
        "You are a helpful Pokémon GO event assistant inside a private Discord server. "
        "You answer questions using only the provided local event/news context. "
        "Be concise, friendly, and practical. Focus on raids, event bonuses, "
        "Community Day, shiny hunting, PvP relevance, and what a player should care about. "
        "If the answer is not supported by the provided context, say you do not know "
        "based on the currently stored event data. Do not invent dates, bonuses, "
        "Pokémon, shiny availability, or raid bosses. The data may be stale; suggest `/update` when freshness matters. "
        "Include source URLs when useful. Keep the response under 1900 Discord characters."
    )
    user_prompt = (
        f"User question:\n{query}\n\n"
        f"Retrieved local Pokémon GO event/news context:\n{context}"
    )
    return call_openai_chat(
        [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        max_tokens=500,
    )
