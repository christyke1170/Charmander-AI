"""General conversational answer helpers with a light Charmander personality."""

from __future__ import annotations

import random
import re
from typing import Callable

from ai.openai_client import call_openai_chat, is_openai_enabled


GENERAL_CHAT_OPENAI_DISABLED_FALLBACK = (
    "I can chat normally once OpenAI is configured, but my cached Pokémon GO tools are still available."
)

CHARMANDER_SYSTEM_PROMPT = (
    "You are Charmander, a friendly Pokémon-themed AI assistant in a Discord server. "
    "You are helpful, warm, practical, and lightly playful. Your favorite Pokémon is Charmander, "
    "you love spicy food and hotpot, and you hate rain. You can mention these quirks occasionally, "
    "but do not force them. You are not rude, edgy, or FFXIV-themed. Answer the user's question clearly. "
    "Keep responses Discord-friendly and concise by default, usually 1–3 short paragraphs unless the user asks for detail. "
    "If the user asks for code or technical help, answer normally and use code blocks when useful. "
    "If the user asks for general factual questions that may require current information, be honest that you may not have live web access unless a web/search provider is configured. "
    "For Pokémon GO-specific facts, prefer the bot's cached data/tools when provided; otherwise be honest if you do not know."
)

SERIOUS_DISCLAIMER_PATTERN = re.compile(
    r"\b(?:medical|doctor|legal|lawyer|attorney|financial|finance|investment|safety|emergency|911|self-harm|suicide)\b",
    re.IGNORECASE,
)
STRUCTURED_OUTPUT_LINE_PATTERN = re.compile(r"^\s*(?:\d+\.\s|[-*]\s|\|)")


def _should_add_charmander_suffix(randrange: Callable[[int], int] | None = None) -> bool:
    """Return whether to append the Charmander catchphrase.

    Uses a 3/5 chance and keeps randomness isolated for easy test patching.
    """

    chooser = randrange or random.randrange
    return chooser(5) < 3


def _looks_like_structured_output(text: str) -> bool:
    lines = [line for line in text.splitlines() if line.strip()]
    if len(lines) < 5:
        return False
    structured_lines = sum(1 for line in lines if STRUCTURED_OUTPUT_LINE_PATTERN.match(line))
    return structured_lines >= 5


def maybe_add_charmander_suffix(text: str, *, allow_suffix: bool = True) -> str:
    """Append a small Charmander catchphrase when appropriate."""

    if not allow_suffix or not text:
        return text

    stripped = text.rstrip()
    if not stripped:
        return text
    if "```" in stripped:
        return text
    if stripped.endswith("Char~!"):
        return text
    if SERIOUS_DISCLAIMER_PATTERN.search(stripped):
        return text
    if _looks_like_structured_output(stripped):
        return text
    if not _should_add_charmander_suffix():
        return text

    trailing_whitespace = text[len(stripped) :]
    return f"{stripped} Char~!{trailing_whitespace}"


def answer_general_chat_query(query: str, context: str | None = None) -> str:
    """Answer a normal conversational question using OpenAI when available."""

    if not is_openai_enabled():
        return GENERAL_CHAT_OPENAI_DISABLED_FALLBACK

    context_block = f"Additional instructions/context:\n{context}\n\n" if context else ""
    user_prompt = f"{context_block}User question:\n{query}"
    return call_openai_chat(
        [
            {"role": "system", "content": CHARMANDER_SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        max_tokens=450,
    )