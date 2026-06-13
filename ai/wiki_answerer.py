"""Grounded answer helpers for cached Pokémon GO Wiki/Fandom chunks."""

from __future__ import annotations

import re
from collections import OrderedDict
from typing import Any

from ai.openai_client import call_openai_chat


MAX_CONTEXT_CHUNKS = 8
MAX_CONTEXT_CHARS = 7000
MAX_DISCORD_CHARS = 1900


def _compact_text(value: Any, max_chars: int = 900) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text[:max_chars]


def _source_urls(chunks: list[dict[str, Any]], limit: int = 3) -> list[str]:
    urls: "OrderedDict[str, None]" = OrderedDict()
    for chunk in chunks:
        url = str(chunk.get("url") or "").strip()
        if url:
            urls.setdefault(url, None)
        if len(urls) >= limit:
            break
    return list(urls.keys())


def _source_line(chunks: list[dict[str, Any]]) -> str:
    urls = _source_urls(chunks, limit=3)
    return "Source: " + ", ".join(urls) if urls else "Source: cached Pokémon GO Wiki data."


def _compact_context(chunks: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    total = 0
    for index, chunk in enumerate(chunks[:MAX_CONTEXT_CHUNKS], start=1):
        part = (
            f"Wiki chunk {index}:\n"
            f"Page: {chunk.get('page_title') or 'Unknown'}\n"
            f"Section: {chunk.get('section_title') or 'Overview'}\n"
            f"URL: {chunk.get('url') or 'Unknown'}\n"
            f"Content: {_compact_text(chunk.get('content'))}"
        )
        if total + len(part) > MAX_CONTEXT_CHARS:
            break
        parts.append(part)
        total += len(part)
    return "\n\n".join(parts)


def format_wiki_search_fallback(query: str, chunks: list[dict[str, Any]]) -> str:
    """Return a deterministic compact answer from cached wiki chunks."""

    if not chunks:
        return "I couldn’t find that in the cached Pokémon GO Wiki data. Ask the bot owner to update the wiki cache or add that page to the seed list."

    first = chunks[0]
    page_title = first.get("page_title") or "Pokémon GO Wiki"
    content = _compact_text(first.get("content"), max_chars=420)
    if len(str(first.get("content") or "")) > len(content):
        content = content.rstrip(" .,;:") + "…"
    lines = [f"I found cached Pokémon GO Wiki info related to {query.strip() or page_title}:", "", f"**{page_title}:**", content]
    lines.append("")
    lines.append(_source_line(chunks))
    return "\n".join(lines)[:MAX_DISCORD_CHARS]


def _valid_wiki_answer(answer: str, chunks: list[dict[str, Any]]) -> bool:
    if not answer or len(answer) > MAX_DISCORD_CHARS:
        return False
    if "Source:" not in answer:
        return False
    source_urls = _source_urls(chunks, limit=3)
    return any(url in answer for url in source_urls) if source_urls else True


def answer_wiki_query_with_llm(query: str, chunks: list[dict[str, Any]]) -> str:
    """Answer a wiki knowledge query using only provided cached wiki chunks."""

    if not chunks:
        return format_wiki_search_fallback(query, chunks)
    system_prompt = (
        "You are a helpful Pokémon GO knowledge assistant inside Discord. "
        "Answer using only the provided cached Pokémon GO Wiki/Fandom chunks. "
        "Do not invent details, availability, dates, mechanics, or exceptions. "
        "If the chunks do not contain enough information, say the cached wiki data does not contain enough information. "
        "Keep the answer compact and Discord-friendly. Do not dump long excerpts. "
        "Include one final Source line with no more than 3 URLs from the chunks."
    )
    user_prompt = f"User question:\n{query}\n\nCached wiki chunks:\n{_compact_context(chunks)}"
    answer = call_openai_chat(
        [{"role": "system", "content": system_prompt}, {"role": "user", "content": user_prompt}],
        max_tokens=550,
    )
    if _valid_wiki_answer(answer, chunks):
        return answer[:MAX_DISCORD_CHARS]
    return format_wiki_search_fallback(query, chunks)
