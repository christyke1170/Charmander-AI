"""Simple rule-based summaries.

This module intentionally does not call an LLM yet. A future version could send
retrieved event rows to an LLM to produce richer summaries.
"""

from __future__ import annotations

from typing import Any


def summarize_events(events: list[dict[str, Any]]) -> str:
    """Return a short rule-based summary for a list of events."""

    if not events:
        return "No matching Pokémon GO events were found in the local database."


    lines = [f"Found {len(events)} event(s):"]
    for event in events[:10]:
        title = event.get("title", "Untitled event")
        start = event.get("start_time") or "date unknown"
        source = event.get("source") or "unknown source"
        lines.append(f"- {title} ({start}) — {source}")
    return "\n".join(lines)
