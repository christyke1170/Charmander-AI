"""Small OpenAI client wrapper for grounded Pokémon GO answers."""

from __future__ import annotations

import logging

from openai import OpenAI

from config import OPENAI_API_KEY, OPENAI_MODEL


logger = logging.getLogger(__name__)


def is_openai_enabled() -> bool:
    """Return whether an OpenAI API key is configured."""

    enabled = bool(OPENAI_API_KEY)
    logger.info("OpenAI enabled: %s; model=%s", enabled, OPENAI_MODEL)
    return enabled


def get_openai_client() -> OpenAI | None:
    """Create an OpenAI client if configured, otherwise return None."""

    if not OPENAI_API_KEY:
        logger.info("OpenAI API key is not configured; using local fallback responses.")
        return None
    return OpenAI(api_key=OPENAI_API_KEY)


def call_openai_chat(messages: list[dict[str, str]], max_tokens: int = 500) -> str:
    """Call OpenAI chat completions with safe fallbacks.

    The API key is never logged or returned.
    """

    client = get_openai_client()
    if client is None:
        return "OpenAI is not configured yet. Add OPENAI_API_KEY to .env to enable AI answers."

    try:
        response = client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=messages,
            max_tokens=max_tokens,
            temperature=0.2,
        )
    except Exception as exc:  # The OpenAI SDK raises several API/network subclasses.
        logger.exception("OpenAI chat request failed: %s", exc)
        return "I had trouble reaching OpenAI just now. Please try again later, or use `/events` for local event listings."

    content = response.choices[0].message.content if response.choices else None
    if not content:
        return "OpenAI returned an empty response. Try again, or use `/events` for local event listings."
    return content.strip()