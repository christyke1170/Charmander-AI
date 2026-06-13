"""Discord slash command registration for the Pokémon GO bot."""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any

import discord
from discord import app_commands

from ai.egg_answerer import (
    answer_egg_query_with_llm,
    format_egg_distance_response,
    format_egg_overview,
    format_egg_pool_response,
    format_egg_pokemon_response,
)
from ai.openai_client import is_openai_enabled
from ai.pokemon_answerer import answer_mixed_query_with_llm, answer_pokemon_query, answer_pokemon_query_with_llm
from ai.query_answerer import answer_query, answer_query_with_llm
from ai.dynamax_answerer import (
    answer_dynamax_query_with_llm,
    format_compact_dynamax_attacker_rows,
    is_compact_dynamax_response,
)
from ai.raid_attacker_answerer import (
    _format_compact_raid_attacker_rows,
    _is_compact_raid_attacker_response,
    _requested_row_count,
    answer_raid_attacker_query_with_llm,
)
from config import OPENAI_MODEL, RAID_ATTACKER_CACHE_MAX_AGE_DAYS
from database.db import (
    get_active_events,
    get_community_day_events,
    get_raid_events,
    get_upcoming_events,
    search_events,
)
from database.egg_pool_db import (
    count_egg_pool_rows,
    get_all_egg_pool_sections,
    get_egg_pools_by_distance,
    get_egg_pools_by_pool_name,
    search_egg_pools,
)
from database.cache_metadata import is_cache_stale
from database.dynamax_attackers_db import (
    count_dynamax_attackers,
    get_best_dynamax_attackers_across_types,
    get_top_dynamax_attackers_by_type,
    search_dynamax_attackers,
)
from database.pokemon_db import find_pokemon_mentions, get_pokemon_meta_candidates, search_pokemon_knowledge
from database.raid_attackers_db import (
    count_raid_attacker_rankings,
    get_best_raid_attackers_across_types,
    get_top_raid_attackers,
    get_top_raid_attackers_by_type,
    normalize_type_name,
    search_raid_attackers,
)
from pokemon_knowledge_import import import_pokemon_knowledge_seed
from pokemon_knowledge_update import _format_zero_row_warning, run_pokemon_knowledge_update
from raid_attacker_update import CACHE_NAME
from weekly_update import run_update


logger = logging.getLogger(__name__)

MAX_DISCORD_MESSAGE_LENGTH = 1900
POKEMON_TYPES = (
    "normal",
    "fire",
    "water",
    "electric",
    "grass",
    "ice",
    "fighting",
    "poison",
    "ground",
    "flying",
    "psychic",
    "bug",
    "rock",
    "ghost",
    "dragon",
    "dark",
    "steel",
    "fairy",
)
ATTACKER_WORD_PATTERN = re.compile(r"\b(?:attackers?|atackers?|attakers?|tchackers?)\b", re.IGNORECASE)
RANKING_LANGUAGE_PATTERN = re.compile(
    r"\b(?:best|top|strongest|meta|ranking|rankings|ranked|good|use|using|power\s+up|invest\s+in|types?|mons?|pokemon|pokémon)\b",
    re.IGNORECASE,
)
RAID_CONTEXT_PATTERN = re.compile(r"\b(?:raid|raids|raiding|pve)\b", re.IGNORECASE)
CURRENT_RAID_EVENT_PATTERN = re.compile(
    r"\b(?:current|active|available|boss|bosses|schedule|5\s*-?\s*star|five\s*-?\s*star|mega\s+raid|right\s+now|rn|today|this\s+week)\b|in\s+raids\s+this\s+week",
    re.IGNORECASE,
)
DYNAMAX_CONTEXT_PATTERN = re.compile(
    r"\b(?:dynamax|gigantamax|dmax|gmax|max\s+battles?|max\s+attackers?)\b",
    re.IGNORECASE,
)
RAID_ATTACKER_PHRASE_PATTERN = re.compile(
    r"\b(?:best\s+(?:pokemon|pokémon|mons?)\s+(?:to\s+use\s+)?(?:for|in)\s+raids?|"
    r"best\s+raiding\s+(?:pokemon|pokémon|mons?)|top\s+raiding\s+(?:pokemon|pokémon|mons?)|"
    r"best\s+raid\s+(?:pokemon|pokémon|mons?|attackers?)|raid\s+meta|pve\s+meta|"
    r"best\s+pve\s+(?:pokemon|pokémon|mons?)|(?:who|what)\s+should\s+i\s+power\s+up\s+for\s+raids?)\b",
    re.IGNORECASE,
)
FOLLOWUP_COUNT_REQUEST_PATTERN = re.compile(
    r"^\s*(?:"
    r"(?:top\s+\d{1,3})|"
    r"(?:can\s+you\s+give\s+me\s+(?:the\s+)?(?:top\s+)?\d{1,3})|"
    r"(?:give\s+me\s+(?:top\s+)?\d{1,3})|"
    r"(?:what\s+about\s+(?:top\s+)?\d{1,3})|"
    r"(?:can\s+i\s+get\s+\d{1,3})|"
    r"(?:list\s+\d{1,3})|"
    r"(?:show\s+\d{1,3})|"
    r"(?:show\s+more)|"
    r"(?:more)"
    r")\s*\??\s*$",
    re.IGNORECASE,
)
POKEMON_QUERY_TERMS = (
    "best attacker",
    "best fire",
    "best water",
    "counter",
    "counters",
    "moveset",
    "great league",
    "ultra league",
    "master league",
    "pvp",
    "pve",
    "raid attacker",
    "meta",
    "weakness",
    "resist",
)
EVENT_QUERY_TERMS = ("raid", "community day", "comm day", "today", "active", "right now", "currently", "shiny", "event", "this week")
EGG_DISTANCE_PATTERN = re.compile(r"\b(1|2|5|7|10|12)\s*km\b", re.IGNORECASE)
EGG_INTENT_PATTERN = re.compile(
    r"\b(?:eggs?|hatch(?:es|ed|ing)?|hatched|hatches|adventure\s+sync|route\s+gift)\b",
    re.IGNORECASE,
)


def _raid_cache_notice(is_update_running: bool) -> str:
    if is_update_running and is_cache_stale(CACHE_NAME, RAID_ATTACKER_CACHE_MAX_AGE_DAYS):
        return "\n\n_Note: cached raid attacker data may be updating in the background._"
    return ""


def _dynamax_cache_notice(is_update_running: bool) -> str:
    if is_update_running:
        return "\n\n_Note: cached Dynamax attacker data may be updating in the background._"
    return ""


def _format_event(event: dict[str, Any]) -> str:
    title = event.get("title") or "Untitled event"
    category = event.get("category")
    start = event.get("start_time")
    end = event.get("end_time")
    source = event.get("source") or "unknown source"
    url = event.get("url")

    details: list[str] = []
    if category:
        details.append(f"Category: {category}")
    if start:
        details.append(f"Start: {start}")
    if end:
        details.append(f"End: {end}")
    details.append(f"Source: {source}")

    line = f"**{title}**\n" + "\n".join(details)
    if url:
        line += f"\n<{url}>"
    return line


def build_event_response(heading: str, events: list[dict[str, Any]], empty_message: str) -> str:
    """Build a clean text Discord response with a distinct heading."""

    lines = [f"## {heading}"]
    if not lines:
        return empty_message
    if not events:
        lines.append(empty_message)
    else:
        lines.extend(_format_event(event) for event in events)
    return "\n\n".join(lines)[:MAX_DISCORD_MESSAGE_LENGTH]


def _is_pokemon_specific_query(query: str) -> bool:
    """Return whether a query should use cached Pokémon knowledge."""

    normalized = query.lower()
    if any(term in normalized for term in POKEMON_QUERY_TERMS):
        return True
    return bool(find_pokemon_mentions(query, limit=1))


def _detect_pokemon_type_from_query(query: str) -> str | None:
    """Return a canonical Pokémon type if the query names one as a word."""

    normalized = query.lower()
    for pokemon_type in POKEMON_TYPES:
        if re.search(rf"\b{re.escape(pokemon_type)}\b", normalized):
            return pokemon_type
    return None


def _is_current_raid_event_query(query: str) -> bool:
    """Return whether the query is asking for active/current raid boss/event data."""

    normalized = query.lower()
    if not re.search(r"\braids?\b", normalized):
        return False
    if not CURRENT_RAID_EVENT_PATTERN.search(normalized):
        return False

    # Ranking/meta language should still route to attacker rankings instead of
    # raid event fallback, even if the user mentions timing like "this week".
    has_type = _detect_pokemon_type_from_query(normalized) is not None
    has_attacker_word = bool(ATTACKER_WORD_PATTERN.search(normalized))
    has_ranking_language = bool(RANKING_LANGUAGE_PATTERN.search(normalized))
    if RAID_ATTACKER_PHRASE_PATTERN.search(normalized) or has_attacker_word or (has_type and has_ranking_language):
        return False
    return True


def _is_raid_attacker_query(query: str) -> bool:
    """Return whether a natural-language query should use raid attacker rankings."""

    normalized = query.lower().strip()
    if not normalized:
        return True
    if _is_dynamax_query(normalized):
        return False
    if _is_current_raid_event_query(normalized):
        return False

    detected_type = _detect_pokemon_type_from_query(normalized)
    has_type = detected_type is not None
    has_attacker_word = bool(ATTACKER_WORD_PATTERN.search(normalized))
    has_ranking_language = bool(RANKING_LANGUAGE_PATTERN.search(normalized))
    has_raid_context = bool(RAID_CONTEXT_PATTERN.search(normalized))

    if RAID_ATTACKER_PHRASE_PATTERN.search(normalized):
        return True
    if has_attacker_word and (has_ranking_language or has_raid_context or has_type):
        return True
    if has_raid_context and has_ranking_language:
        return True
    if has_type and has_ranking_language:
        return True
    return False


def _is_dynamax_query(query: str) -> bool:
    """Return whether a query should use cached Dynamax/Gigantamax rankings."""

    normalized = query.lower().strip()
    if not normalized:
        return False
    return bool(DYNAMAX_CONTEXT_PATTERN.search(normalized))


def _is_event_specific_query(query: str) -> bool:
    normalized = query.lower()
    return any(term in normalized for term in EVENT_QUERY_TERMS)


def _is_egg_pool_query(query: str) -> bool:
    """Return whether a query should use cached egg pool data."""

    normalized = query.lower().strip()
    if not normalized:
        return False
    if not EGG_INTENT_PATTERN.search(normalized):
        return False
    # Avoid stealing explicit raid/event questions that merely mention eggs as unrelated text.
    if _is_current_raid_event_query(normalized):
        return False
    return True


def _detect_egg_distance(query: str | None) -> int | None:
    match = EGG_DISTANCE_PATTERN.search(query or "")
    if not match:
        return None
    try:
        return int(match.group(1))
    except ValueError:
        return None


def _detect_egg_pool_type(query: str | None) -> str | None:
    normalized = (query or "").lower()
    if "adventure sync" in normalized:
        return "adventure_sync"
    if "route gift" in normalized or "from route" in normalized:
        return "route_gift"
    return None


def _egg_pokemon_candidate(query: str | None) -> str | None:
    """Extract a likely Pokémon name from an egg question, if one remains."""

    text = (query or "").replace("’", "'")
    text = re.sub(r"\b(?:1|2|5|7|10|12)\s*km\b", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"\b(?:adventure\s+sync|route\s+gift|from\s+route\s+gift|from\s+route)\b", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"[?!.:,;()\[\]{}]", " ", text)
    text = re.sub(
        r"\b(?:what|whats|what's|which|who|can|could|does|do|is|are|the|a|an|current|right|now|currently|from|in|into|have|has|with|listed|available|pool|pools|egg|eggs|hatch|hatches|hatched|hatching|hatchable|kms?|km)\b",
        " ",
        text,
        flags=re.IGNORECASE,
    )
    candidate = re.sub(r"\s+", " ", text).strip()
    if len(candidate) < 2:
        return None
    return candidate


def _rows_for_egg_pool_type(pool_type: str) -> list[dict[str, Any]]:
    if pool_type == "adventure_sync":
        return get_egg_pools_by_pool_name("Adventure Sync")
    if pool_type == "route_gift":
        return get_egg_pools_by_pool_name("Route Gift") or get_egg_pools_by_pool_name("From Route")
    return search_egg_pools(pool_type, limit=100)


def _rows_for_egg_pokemon(candidate: str, limit: int = 100) -> list[dict[str, Any]]:
    rows = search_egg_pools(candidate, limit=limit)
    normalized = candidate.lower().strip()
    return [row for row in rows if normalized in str(row.get("pokemon_name") or "").lower()]


def build_egg_response(query: str | None = None) -> str:
    """Return a Discord-ready answer from cached egg pool data only."""

    normalized = (query or "").strip()
    if not normalized:
        return format_egg_overview(get_all_egg_pool_sections())
    if count_egg_pool_rows() == 0:
        return "The egg pool cache is empty. Ask the bot owner to run `/updateeggs`."

    distance = _detect_egg_distance(normalized)
    pool_type = _detect_egg_pool_type(normalized)
    if distance is not None:
        rows = get_egg_pools_by_distance(distance, pool_type=pool_type)
        fallback = format_egg_distance_response(distance, rows, pool_type=pool_type)
        if is_openai_enabled() and rows:
            return answer_egg_query_with_llm(normalized, rows, fallback)[:MAX_DISCORD_MESSAGE_LENGTH]
        return fallback[:MAX_DISCORD_MESSAGE_LENGTH]

    if pool_type:
        rows = _rows_for_egg_pool_type(pool_type)
        fallback = format_egg_pool_response(rows, normalized)
        if is_openai_enabled() and rows:
            return answer_egg_query_with_llm(normalized, rows, fallback)[:MAX_DISCORD_MESSAGE_LENGTH]
        return fallback[:MAX_DISCORD_MESSAGE_LENGTH]

    candidate = _egg_pokemon_candidate(normalized)
    if candidate:
        rows = _rows_for_egg_pokemon(candidate)
        fallback = format_egg_pokemon_response(candidate, rows)
        if is_openai_enabled() and rows:
            return answer_egg_query_with_llm(normalized, rows, fallback)[:MAX_DISCORD_MESSAGE_LENGTH]
        return fallback[:MAX_DISCORD_MESSAGE_LENGTH]

    return format_egg_overview(get_all_egg_pool_sections())[:MAX_DISCORD_MESSAGE_LENGTH]


def _event_context(events: list[dict[str, Any]]) -> str:
    return "\n\n".join(_format_event(event) for event in events[:10])[:6000]


def _detect_raid_attacker_type(query: str | None) -> str | None:
    return _detect_pokemon_type_from_query(query or "") or normalize_type_name(query or "")


def _detect_dynamax_attacker_type(query: str | None) -> str | None:
    return _detect_pokemon_type_from_query(query or "") or normalize_type_name(query or "")


def infer_raid_attacker_route_from_bot_message(content: str) -> str | None:
    """Infer a raid attacker route from a previous compact bot answer."""

    normalized = re.sub(r"\s+", " ", content or "").strip().lower()
    if not normalized:
        return None

    for pokemon_type in POKEMON_TYPES:
        if re.search(rf"\btop\s+(?:cached\s+)?{re.escape(pokemon_type)}\s*-\s*type\s+raid\s+attackers\b", normalized):
            return f"type:{pokemon_type}"

    has_raid_attacker_heading = bool(re.search(r"\btop\b.*\braid\s+attackers\b", normalized))
    if "top raid attackers, derived from cached per-type rankings" in normalized:
        return "derived_overall"
    if has_raid_attacker_heading and "source: cached pokémon go hub best-per-type tables." in normalized:
        return "derived_overall"
    if has_raid_attacker_heading and "source: cached pokemon go hub best-per-type tables." in normalized:
        return "derived_overall"
    return None


def infer_dynamax_route_from_bot_message(content: str) -> str | None:
    """Infer a Dynamax attacker route from a previous compact bot answer."""

    normalized = re.sub(r"\s+", " ", content or "").strip().lower()
    if not normalized:
        return None
    for pokemon_type in POKEMON_TYPES:
        if re.search(rf"\btop\s+(?:cached\s+)?{re.escape(pokemon_type)}\s*-\s*type\s+dynamax\s+attackers\b", normalized):
            return f"type:{pokemon_type}"
    if "top dynamax attackers, derived from cached per-type rankings" in normalized:
        return "derived_overall"
    if "source: cached pokémon go hub dynamax attacker tables." in normalized:
        return "derived_overall"
    if "source: cached pokemon go hub dynamax attacker tables." in normalized:
        return "derived_overall"
    return None


def is_followup_count_request(query: str) -> bool:
    """Return whether a short reply is asking for more/top-N rows from context."""

    return bool(FOLLOWUP_COUNT_REQUEST_PATTERN.match(query or ""))


def _get_raid_attacker_rows_for_route(route: str, limit: int) -> list[dict[str, Any]]:
    if route == "derived_overall":
        return get_best_raid_attackers_across_types(limit=limit)
    if route.startswith("type:"):
        return get_top_raid_attackers_by_type(route[5:], limit=limit)
    return []


def _get_dynamax_rows_for_route(route: str, limit: int) -> list[dict[str, Any]]:
    if route == "derived_overall":
        return get_best_dynamax_attackers_across_types(limit=limit)
    if route.startswith("type:"):
        return get_top_dynamax_attackers_by_type(route[5:], limit=limit)
    return []


def get_raid_attacker_rows_for_query(query: str | None, limit: int = 10) -> tuple[list[dict[str, Any]], str]:
    """Return ranking rows for a slash/mention query and the route used."""

    normalized = (query or "").strip().lower()
    detected_type = _detect_raid_attacker_type(normalized)
    if detected_type:
        return get_top_raid_attackers_by_type(detected_type, limit=limit), f"type:{detected_type}"
    if not normalized or _is_raid_attacker_query(normalized):
        return get_best_raid_attackers_across_types(limit=limit), "derived_overall"
    if RANKING_LANGUAGE_PATTERN.search(normalized):
        return get_best_raid_attackers_across_types(limit=limit), "derived_overall"
    return search_raid_attackers(normalized, limit=limit), "search"


def get_dynamax_attacker_rows_for_query(query: str | None, limit: int = 10) -> tuple[list[dict[str, Any]], str]:
    """Return cached Dynamax/Gigantamax rows for a slash/mention query and route used."""

    normalized = (query or "").strip().lower()
    detected_type = _detect_dynamax_attacker_type(normalized)
    if detected_type:
        return get_top_dynamax_attackers_by_type(detected_type, limit=limit), f"type:{detected_type}"
    if not normalized or _is_dynamax_query(normalized) or RANKING_LANGUAGE_PATTERN.search(normalized):
        return get_best_dynamax_attackers_across_types(limit=limit), "derived_overall"
    return search_dynamax_attackers(normalized, limit=limit), "search"


def build_dynamax_attacker_response(
    query: str | None,
    rows: list[dict[str, Any]],
    is_update_running: bool = False,
    route: str = "search",
) -> str:
    """Return a user-facing response from cached Dynamax attacker data only."""

    if not rows:
        if count_dynamax_attackers() == 0:
            return "The Dynamax attacker ranking cache is empty. Ask the bot owner to run `/updatedynamax`."
        scope_text = f" for `{route}`" if route != "search" else ""
        return f"No matching cached Dynamax attacker rankings were found{scope_text}." + _dynamax_cache_notice(is_update_running)

    notice = _dynamax_cache_notice(is_update_running)
    max_body_length = MAX_DISCORD_MESSAGE_LENGTH - len(notice)
    compact_answer = format_compact_dynamax_attacker_rows(
        rows,
        route,
        query,
        max_rows=_requested_row_count(query),
        max_chars=max_body_length,
    )

    if is_openai_enabled():
        llm_answer = answer_dynamax_query_with_llm(query or "best dynamax attackers", rows, route)
        if is_compact_dynamax_response(llm_answer, query, _requested_row_count(query), max_body_length):
            return llm_answer + notice
        logger.debug("Dynamax LLM response did not satisfy compact Discord format; using compact fallback formatter")

    return compact_answer + notice


def build_raid_attacker_response(
    query: str | None,
    rows: list[dict[str, Any]],
    is_update_running: bool = False,
    route: str = "search",
) -> str:
    """Return a user-facing response from cached raid attacker data only."""

    if not rows:
        if count_raid_attacker_rankings() == 0:
            return (
                "The raid attacker ranking cache is empty. `/updateraidattackers` ran successfully but found no real ranking rows. "
                "Add a real `data/raid_attackers_seed.csv` or implement a live ranking scraper, then run `/updateraidattackers` again."
            )
        scope_text = f" for `{route}`" if route != "search" else ""
        return f"No matching cached raid attacker rankings were found{scope_text}." + _raid_cache_notice(is_update_running)

    notice = _raid_cache_notice(is_update_running)
    max_body_length = MAX_DISCORD_MESSAGE_LENGTH - len(notice)
    compact_answer = _format_compact_raid_attacker_rows(
        rows,
        route,
        query,
        max_rows=_requested_row_count(query),
        max_chars=max_body_length,
    )

    if is_openai_enabled():
        llm_answer = answer_raid_attacker_query_with_llm(query or "best raid attackers", rows, route)
        if _is_compact_raid_attacker_response(llm_answer, query, _requested_row_count(query), max_body_length):
            return llm_answer + notice
        logger.debug("Raid attacker LLM response did not satisfy compact Discord format; using compact fallback formatter")

    return compact_answer + notice


def route_mention_query(query: str) -> tuple[str, str, list[dict[str, Any]]]:
    """Route a bot mention question to local database helpers.

    TODO: Replace or augment this with an LLM/RAG answer layer after retrieving
    local event rows. The future LLM should answer only from retrieved context.
    """

    normalized = query.lower().strip()
    if _is_current_raid_event_query(normalized) or "raid" in normalized:
        events = get_raid_events(limit=10)
        return "raids", "Raid-Related Pokémon GO Events", events
    if "community day" in normalized or "comm day" in normalized:
        events = get_community_day_events(limit=10)
        return "community", "Community Day Events", events
    if any(term in normalized for term in ("today", "active", "right now", "currently")):
        events = get_active_events()
        return "today", "Active Pokémon GO Events Right Now", events
    if "shiny" in normalized:
        events = search_events("shiny", limit=10)
        return "shiny", "Local Events Matching “shiny”", events
    if any(term in normalized for term in ("this week", "week", "care about", "worth doing", "upcoming", "next")):
        events = get_upcoming_events(limit=10)
        return "upcoming", "Upcoming Pokémon GO Events", events

    events = search_events(query, limit=10)
    return "search", "Local Pokémon GO Event Search Results", events


def build_mention_response(
    query: str,
    is_raid_update_running: bool = False,
    is_dynamax_update_running: bool = False,
) -> tuple[str, str, int]:
    """Return response text, route name, and result count for a mention query."""

    requested_count = _requested_row_count(query)
    if _is_dynamax_query(query):
        dynamax_rows, route = get_dynamax_attacker_rows_for_query(query, limit=requested_count)
        return build_dynamax_attacker_response(query, dynamax_rows, is_dynamax_update_running, route), "dynamax_attackers", len(dynamax_rows)

    if _is_raid_attacker_query(query):
        raid_rows, route = get_raid_attacker_rows_for_query(query, limit=requested_count)
        return build_raid_attacker_response(query, raid_rows, is_raid_update_running, route), "raid_attackers", len(raid_rows)

    if _is_egg_pool_query(query):
        return build_egg_response(query), "eggs", count_egg_pool_rows()

    if _is_current_raid_event_query(query):
        route, heading, events = route_mention_query(query)
        if not events:
            return (
                "I couldn’t find current local raid event data for that yet. Try `/raids` or run `/update` if you are the bot owner.",
                route,
                0,
            )
        if is_openai_enabled():
            return answer_query_with_llm(query, events)[:MAX_DISCORD_MESSAGE_LENGTH], route, len(events)
        return build_event_response(heading, events, "No matching local raid event data found."), route, len(events)

    pokemon_specific = _is_pokemon_specific_query(query)
    event_specific = _is_event_specific_query(query)
    if pokemon_specific:
        pokemon_rows = get_pokemon_meta_candidates(query, limit=10)
        if event_specific:
            _, _, events = route_mention_query(query)
            if is_openai_enabled():
                answer = answer_mixed_query_with_llm(query, _event_context(events), pokemon_rows)
                return answer[:MAX_DISCORD_MESSAGE_LENGTH], "mixed", len(events) + len(pokemon_rows)
        if not pokemon_rows:
            return "I couldn’t find matching local Pokémon knowledge for that. Ask the bot owner to run `/updatepokemon`.", "pokemon", 0
        if is_openai_enabled():
            return answer_pokemon_query_with_llm(query, pokemon_rows)[:MAX_DISCORD_MESSAGE_LENGTH], "pokemon", len(pokemon_rows)
        return answer_pokemon_query(query, pokemon_rows)[:MAX_DISCORD_MESSAGE_LENGTH], "pokemon", len(pokemon_rows)

    route, heading, events = route_mention_query(query)
    if not events:
        return (
            "I couldn’t find matching local Pokémon GO event data for that yet. "
            "Try `/events` to see the latest stored events, or run `/update` if you are the bot owner.",
            route,
            0,
        )
    if is_openai_enabled():
        return answer_query_with_llm(query, events)[:MAX_DISCORD_MESSAGE_LENGTH], route, len(events)
    return build_event_response(heading, events, "No matching local event data found."), route, len(events)


def build_contextual_mention_response(
    query: str,
    previous_bot_message_content: str | None = None,
    is_raid_update_running: bool = False,
    is_dynamax_update_running: bool = False,
) -> tuple[str, str, int]:
    """Return a mention/reply response while preserving previous bot answer context."""

    previous_route = infer_raid_attacker_route_from_bot_message(previous_bot_message_content or "")
    previous_dynamax_route = infer_dynamax_route_from_bot_message(previous_bot_message_content or "")
    contextual_query = (
        f"Previous bot answer: {previous_bot_message_content}\nUser follow-up: {query}"
        if previous_bot_message_content
        else query
    )

    if previous_dynamax_route and is_followup_count_request(query):
        requested_count = _requested_row_count(query)
        rows = _get_dynamax_rows_for_route(previous_dynamax_route, requested_count)
        return build_dynamax_attacker_response(query, rows, is_dynamax_update_running, previous_dynamax_route), "dynamax_attackers", len(rows)

    if previous_dynamax_route and _is_dynamax_query(contextual_query):
        requested_count = _requested_row_count(query)
        rows = _get_dynamax_rows_for_route(previous_dynamax_route, requested_count)
        return build_dynamax_attacker_response(query, rows, is_dynamax_update_running, previous_dynamax_route), "dynamax_attackers", len(rows)

    if previous_route and is_followup_count_request(query):
        requested_count = _requested_row_count(query)
        rows = _get_raid_attacker_rows_for_route(previous_route, requested_count)
        return build_raid_attacker_response(query, rows, is_raid_update_running, previous_route), "raid_attackers", len(rows)

    if previous_route and _is_raid_attacker_query(contextual_query):
        requested_count = _requested_row_count(query)
        rows = _get_raid_attacker_rows_for_route(previous_route, requested_count)
        return build_raid_attacker_response(query, rows, is_raid_update_running, previous_route), "raid_attackers", len(rows)

    response, route, count = build_mention_response(query, is_raid_update_running, is_dynamax_update_running)
    if route == "raid_attackers" and previous_route:
        requested_count = _requested_row_count(query)
        rows = _get_raid_attacker_rows_for_route(previous_route, requested_count)
        return build_raid_attacker_response(query, rows, is_raid_update_running, previous_route), route, len(rows)
    return response, route, count


def register_commands(
    tree: app_commands.CommandTree,
    owner_id: int | None,
    raid_cache_manager: Any | None = None,
    egg_cache_manager: Any | None = None,
    dynamax_cache_manager: Any | None = None,
) -> None:
    """Register all application commands on a command tree."""

    @tree.command(name="events", description="Show upcoming Pokémon GO events from the local database.")
    async def events_command(interaction: discord.Interaction):
        logger.info("Slash command invoked: /events by user_id=%s", interaction.user.id)
        events = get_upcoming_events(limit=10)
        logger.info("/events returned %d event(s)", len(events))
        await interaction.response.send_message(
            build_event_response("Upcoming Pokémon GO Events", events, "No upcoming events found. Run /update first."),
            ephemeral=False,
            suppress_embeds=True,
        )

    @tree.command(name="today", description="Show Pokémon GO events active right now.")
    async def today_command(interaction: discord.Interaction):
        logger.info("Slash command invoked: /today by user_id=%s", interaction.user.id)
        events = get_active_events()
        logger.info("/today returned %d event(s)", len(events))
        await interaction.response.send_message(
            build_event_response("Active Pokémon GO Events Right Now", events, "No active events found right now."),
            ephemeral=False,
            suppress_embeds=True,
        )

    @tree.command(name="raids", description="Show raid-related Pokémon GO events.")
    async def raids_command(interaction: discord.Interaction):
        logger.info("Slash command invoked: /raids by user_id=%s", interaction.user.id)
        events = get_raid_events(limit=10)
        logger.info("/raids returned %d event(s)", len(events))
        await interaction.response.send_message(
            build_event_response("Raid-Related Pokémon GO Events", events, "No raid-related events found."),
            ephemeral=False,
            suppress_embeds=True,
        )

    @tree.command(name="raidattackers", description="Ask about cached monthly raid attacker rankings/data.")
    @app_commands.describe(query="Example: best fire attacker, Kyogre counters, raid attacker")
    async def raid_attackers_command(interaction: discord.Interaction, query: str = ""):
        logger.info("Slash command invoked: /raidattackers by user_id=%s", interaction.user.id)
        await interaction.response.defer(ephemeral=False, thinking=True)
        rows, route = get_raid_attacker_rows_for_query(query, limit=_requested_row_count(query))
        logger.info("/raidattackers returned %d row(s) for query=%r via route=%s", len(rows), query, route)
        is_running = bool(raid_cache_manager and raid_cache_manager.is_update_running)
        answer = await asyncio.to_thread(build_raid_attacker_response, query, rows, is_running, route)
        await interaction.followup.send(answer, ephemeral=False, suppress_embeds=True)

    @tree.command(name="dynamax", description="Ask about cached Dynamax/Gigantamax attacker rankings.")
    @app_commands.describe(query="Example: fire, best fighting gmax attackers, top 10 dmax pokemon")
    async def dynamax_command(interaction: discord.Interaction, query: str = ""):
        logger.info("Slash command invoked: /dynamax by user_id=%s", interaction.user.id)
        await interaction.response.defer(ephemeral=False, thinking=True)
        rows, route = get_dynamax_attacker_rows_for_query(query, limit=_requested_row_count(query))
        logger.info("/dynamax returned %d row(s) for query=%r via route=%s", len(rows), query, route)
        is_running = bool(dynamax_cache_manager and dynamax_cache_manager.is_update_running)
        answer = await asyncio.to_thread(build_dynamax_attacker_response, query, rows, is_running, route)
        await interaction.followup.send(answer, ephemeral=False, suppress_embeds=True)

    @tree.command(name="eggs", description="Ask about cached current Pokémon GO egg pools.")
    @app_commands.describe(query="Example: 1km, 10km adventure sync, route gift, Larvesta")
    async def eggs_command(interaction: discord.Interaction, query: str = ""):
        logger.info("Slash command invoked: /eggs by user_id=%s", interaction.user.id)
        await interaction.response.defer(ephemeral=False, thinking=True)
        answer = await asyncio.to_thread(build_egg_response, query)
        await interaction.followup.send(answer, ephemeral=False, suppress_embeds=True)

    @tree.command(name="communityday", description="Show upcoming Community Day related events.")
    async def community_day_command(interaction: discord.Interaction):
        logger.info("Slash command invoked: /communityday by user_id=%s", interaction.user.id)
        events = get_community_day_events(limit=10)
        logger.info("/communityday returned %d event(s)", len(events))
        await interaction.response.send_message(
            build_event_response("Community Day Events", events, "No Community Day events found."),
            ephemeral=False,
            suppress_embeds=True,
        )

    @tree.command(name="update", description="Admin-only: manually scrape sources and update the local database.")
    async def update_command(interaction: discord.Interaction):
        logger.info("Slash command invoked: /update by user_id=%s", interaction.user.id)
        # TODO: For multi-server deployments, consider Discord permissions/roles in addition to owner ID.
        if owner_id is None or interaction.user.id != owner_id:
            await interaction.response.send_message("This admin-only command can only be run by the configured bot owner.", ephemeral=True, suppress_embeds=True)
            return

        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            count = await asyncio.to_thread(run_update)
        except Exception as exc:
            logger.exception("Discord-triggered update failed: %s", exc)
            await interaction.followup.send(f"Update failed: {exc}", ephemeral=True, suppress_embeds=True)
            return
        await interaction.followup.send(f"Update complete. Upserted {count} event(s).", ephemeral=True, suppress_embeds=True)

    @tree.command(name="ask", description="Ask a Pokémon GO event question using local data and optional AI.")
    @app_commands.describe(query="What do you want to search for?")
    async def ask_command(interaction: discord.Interaction, query: str):
        logger.info("Slash command invoked: /ask by user_id=%s", interaction.user.id)
        await interaction.response.defer(ephemeral=False, thinking=True)
        if _is_dynamax_query(query):
            dynamax_rows, route = get_dynamax_attacker_rows_for_query(query, limit=_requested_row_count(query))
            logger.info("/ask routed to Dynamax attacker rankings and returned %d row(s) for query=%r via route=%s", len(dynamax_rows), query, route)
            is_running = bool(dynamax_cache_manager and dynamax_cache_manager.is_update_running)
            answer = await asyncio.to_thread(build_dynamax_attacker_response, query, dynamax_rows, is_running, route)
            await interaction.followup.send(answer, ephemeral=False, suppress_embeds=True)
            return

        if _is_raid_attacker_query(query):
            raid_rows, route = get_raid_attacker_rows_for_query(query, limit=_requested_row_count(query))
            logger.info("/ask routed to raid attacker rankings and returned %d row(s) for query=%r via route=%s", len(raid_rows), query, route)
            is_running = bool(raid_cache_manager and raid_cache_manager.is_update_running)
            answer = await asyncio.to_thread(build_raid_attacker_response, query, raid_rows, is_running, route)
            await interaction.followup.send(answer, ephemeral=False, suppress_embeds=True)
            return

        if _is_egg_pool_query(query):
            logger.info("/ask routed to cached egg pools for query=%r", query)
            answer = await asyncio.to_thread(build_egg_response, query)
            await interaction.followup.send(answer[:MAX_DISCORD_MESSAGE_LENGTH], ephemeral=False, suppress_embeds=True)
            return

        if _is_current_raid_event_query(query):
            route, _heading, events = route_mention_query(query)
            logger.info("/ask routed to current raid event data via route=%s and returned %d row(s) for query=%r", route, len(events), query)
            if is_openai_enabled():
                answer = await asyncio.to_thread(answer_query_with_llm, query, events)
            else:
                answer = answer_query(query, events)
            await interaction.followup.send(answer[:MAX_DISCORD_MESSAGE_LENGTH], ephemeral=False, suppress_embeds=True)
            return

        if _is_pokemon_specific_query(query):
            pokemon_rows = get_pokemon_meta_candidates(query, limit=10)
            logger.info("/ask routed to Pokémon knowledge and returned %d row(s) for query=%r", len(pokemon_rows), query)
            if is_openai_enabled():
                answer = await asyncio.to_thread(answer_pokemon_query_with_llm, query, pokemon_rows)
            else:
                answer = answer_pokemon_query(query, pokemon_rows)
            await interaction.followup.send(answer[:MAX_DISCORD_MESSAGE_LENGTH], ephemeral=False, suppress_embeds=True)
            return

        events = search_events(query, limit=10)
        logger.info("/ask returned %d event(s) for query=%r", len(events), query)
        if is_openai_enabled():
            answer = await asyncio.to_thread(answer_query_with_llm, query, events)
        else:
            answer = answer_query(query, events)
        await interaction.followup.send(answer[:MAX_DISCORD_MESSAGE_LENGTH], ephemeral=False, suppress_embeds=True)

    @tree.command(name="aistatus", description="Show whether OpenAI AI answers are configured.")
    async def ai_status_command(interaction: discord.Interaction):
        logger.info("Slash command invoked: /aistatus by user_id=%s", interaction.user.id)
        enabled = is_openai_enabled()
        status = "enabled" if enabled else "disabled"
        await interaction.response.send_message(
            f"OpenAI is **{status}**. Model: `{OPENAI_MODEL}`. API key configured: **{'yes' if enabled else 'no'}**.",
            ephemeral=True,
            suppress_embeds=True,
        )

    @tree.command(name="pokemon", description="Ask about cached Pokémon GO Hub Pokémon knowledge.")
    @app_commands.describe(query="Example: Charizard, best fire attacker, great league Annihilape, Kyogre counters")
    async def pokemon_command(interaction: discord.Interaction, query: str):
        logger.info("Slash command invoked: /pokemon by user_id=%s", interaction.user.id)
        await interaction.response.defer(ephemeral=False, thinking=True)
        if _is_dynamax_query(query):
            dynamax_rows, route = get_dynamax_attacker_rows_for_query(query, limit=_requested_row_count(query))
            logger.info("/pokemon routed to Dynamax attacker rankings and returned %d row(s) for query=%r via route=%s", len(dynamax_rows), query, route)
            is_running = bool(dynamax_cache_manager and dynamax_cache_manager.is_update_running)
            answer = await asyncio.to_thread(build_dynamax_attacker_response, query, dynamax_rows, is_running, route)
            await interaction.followup.send(answer, ephemeral=False, suppress_embeds=True)
            return

        if _is_raid_attacker_query(query):
            raid_rows, route = get_raid_attacker_rows_for_query(query, limit=_requested_row_count(query))
            logger.info("/pokemon routed to raid attacker rankings and returned %d row(s) for query=%r via route=%s", len(raid_rows), query, route)
            is_running = bool(raid_cache_manager and raid_cache_manager.is_update_running)
            answer = await asyncio.to_thread(build_raid_attacker_response, query, raid_rows, is_running, route)
            await interaction.followup.send(answer, ephemeral=False, suppress_embeds=True)
            return

        pokemon_rows = get_pokemon_meta_candidates(query, limit=10) or search_pokemon_knowledge(query, limit=10)
        logger.info("/pokemon returned %d row(s) for query=%r", len(pokemon_rows), query)
        if is_openai_enabled():
            answer = await asyncio.to_thread(answer_pokemon_query_with_llm, query, pokemon_rows)
        else:
            answer = answer_pokemon_query(query, pokemon_rows)
        await interaction.followup.send(answer[:MAX_DISCORD_MESSAGE_LENGTH], ephemeral=False, suppress_embeds=True)

    @tree.command(name="updatepokemon", description="Owner-only: update cached Pokémon GO Hub knowledge.")
    async def update_pokemon_command(interaction: discord.Interaction):
        logger.info("Slash command invoked: /updatepokemon by user_id=%s", interaction.user.id)
        if owner_id is None or interaction.user.id != owner_id:
            await interaction.response.send_message("This owner-only command can only be run by the configured bot owner.", ephemeral=True, suppress_embeds=True)
            return

        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            count, stats = await asyncio.to_thread(run_pokemon_knowledge_update)
        except Exception as exc:
            logger.exception("Discord-triggered Pokémon knowledge update failed: %s", exc)
            await interaction.followup.send(f"Pokémon knowledge update failed: {exc}", ephemeral=True, suppress_embeds=True)
            return
        message = (
            f"Pokémon knowledge update complete. Upserted {count} row(s). "
            f"Discovered links: {stats.get('discovered_links', 0)}, "
            f"pages scraped: {stats.get('pages_scraped', 0)}, "
            f"link matches: {stats.get('pokemon_link_matches', 0)}, "
            f"parse failures: {stats.get('parse_failures', 0)}."
        )
        if count == 0:
            message += f" {_format_zero_row_warning(stats)} No bypass or browser automation was attempted."
        await interaction.followup.send(message[:MAX_DISCORD_MESSAGE_LENGTH], ephemeral=True, suppress_embeds=True)

    @tree.command(name="updateraidattackers", description="Owner-only: force refresh cached raid attacker data.")
    async def update_raid_attackers_command(interaction: discord.Interaction):
        logger.info("Slash command invoked: /updateraidattackers by user_id=%s", interaction.user.id)
        if owner_id is None or interaction.user.id != owner_id:
            await interaction.response.send_message("This owner-only command can only be run by the configured bot owner.", ephemeral=True, suppress_embeds=True)
            return
        if raid_cache_manager is not None and raid_cache_manager.is_update_running:
            await interaction.response.send_message("A raid attacker update is already in progress. Please try again in a few minutes.", ephemeral=True, suppress_embeds=True)
            return
        if raid_cache_manager is None:
            await interaction.response.send_message("Raid attacker cache manager is not available.", ephemeral=True, suppress_embeds=True)
            return

        await interaction.response.defer(ephemeral=True, thinking=True)
        result = await raid_cache_manager.force_refresh(reason="manual", wait_for_lock=False)
        if result.updated:
            await interaction.followup.send(f"Raid attacker update complete. Upserted {result.count} row(s).", ephemeral=True, suppress_embeds=True)
        elif result.reason == "zero-rows":
            await interaction.followup.send(
                "Raid attacker update finished but returned zero rows. Existing cached data was kept and cache metadata was not marked fresh. "
                "If you are using a local seed file, make sure `data/raid_attackers_seed.csv` exists and contains real rankings, not placeholder example rows.",
                ephemeral=True,
                suppress_embeds=True,
            )
        elif result.reason == "already-running":
            await interaction.followup.send("A raid attacker update is already in progress. Please try again in a few minutes.", ephemeral=True, suppress_embeds=True)
        else:
            await interaction.followup.send("Raid attacker update failed. Existing cached data was kept.", ephemeral=True, suppress_embeds=True)

    @tree.command(name="updatedynamax", description="Owner-only: force refresh cached Dynamax attacker data.")
    async def update_dynamax_command(interaction: discord.Interaction):
        logger.info("Slash command invoked: /updatedynamax by user_id=%s", interaction.user.id)
        if owner_id is None or interaction.user.id != owner_id:
            await interaction.response.send_message("This owner-only command can only be run by the configured bot owner.", ephemeral=True, suppress_embeds=True)
            return
        if dynamax_cache_manager is not None and dynamax_cache_manager.is_update_running:
            await interaction.response.send_message("A Dynamax attacker update is already in progress. Please try again in a few minutes.", ephemeral=True, suppress_embeds=True)
            return
        if dynamax_cache_manager is None:
            await interaction.response.send_message("Dynamax cache manager is not available.", ephemeral=True, suppress_embeds=True)
            return

        await interaction.response.defer(ephemeral=True, thinking=True)
        result = await dynamax_cache_manager.force_refresh(reason="manual", wait_for_lock=False)
        type_rows = result.stats.get("type_rows", {}) if result.stats else {}
        metadata_updated = result.stats.get("metadata_updated", False) if result.stats else result.updated
        error = result.stats.get("error") if result.stats else None
        update_source = result.stats.get("update_source", "unknown") if result.stats else "unknown"
        source_label = {"live_scraper": "live scraper", "manual_csv": "manual CSV fallback"}.get(update_source, update_source)
        if result.updated:
            await interaction.followup.send(
                f"Dynamax update complete via {source_label}. Upserted {result.count} row(s). Type rows: {type_rows}. Metadata updated: {metadata_updated}.",
                ephemeral=True,
                suppress_embeds=True,
            )
        elif result.reason == "zero-rows":
            await interaction.followup.send(
                f"Dynamax update finished with no available rows from live scraper or manual CSV fallback. Existing cached data was kept and metadata was not marked fresh. Type rows: {type_rows}. Error: {error}.",
                ephemeral=True,
                suppress_embeds=True,
            )
        elif result.reason == "already-running":
            await interaction.followup.send("A Dynamax attacker update is already in progress. Please try again in a few minutes.", ephemeral=True, suppress_embeds=True)
        else:
            await interaction.followup.send("Dynamax attacker update failed. Existing cached data was kept.", ephemeral=True, suppress_embeds=True)

    @tree.command(name="updateeggs", description="Owner-only: force refresh cached egg pools.")
    async def update_eggs_command(interaction: discord.Interaction):
        logger.info("Slash command invoked: /updateeggs by user_id=%s", interaction.user.id)
        if owner_id is None or interaction.user.id != owner_id:
            await interaction.response.send_message("This owner-only command can only be run by the configured bot owner.", ephemeral=True, suppress_embeds=True)
            return
        if egg_cache_manager is not None and egg_cache_manager.is_update_running:
            await interaction.response.send_message("An egg pool update is already in progress. Please try again in a few minutes.", ephemeral=True, suppress_embeds=True)
            return
        if egg_cache_manager is None:
            await interaction.response.send_message("Egg pool cache manager is not available.", ephemeral=True, suppress_embeds=True)
            return

        await interaction.response.defer(ephemeral=True, thinking=True)
        result = await egg_cache_manager.force_refresh(reason="manual", wait_for_lock=False)
        sections = result.stats.get("sections_found", 0) if result.stats else 0
        metadata_updated = result.stats.get("metadata_updated", False) if result.stats else result.updated
        if result.updated:
            await interaction.followup.send(
                f"Egg pool update complete. Upserted {result.count} row(s). Sections found: {sections}. Metadata updated: {metadata_updated}.",
                ephemeral=True,
                suppress_embeds=True,
            )
        elif result.reason == "zero-rows":
            await interaction.followup.send(
                f"Egg pool update finished but returned zero rows. Existing cached data was kept and metadata was not marked fresh. Sections found: {sections}.",
                ephemeral=True,
                suppress_embeds=True,
            )
        elif result.reason == "already-running":
            await interaction.followup.send("An egg pool update is already in progress. Please try again in a few minutes.", ephemeral=True, suppress_embeds=True)
        else:
            await interaction.followup.send("Egg pool update failed. Existing cached data was kept.", ephemeral=True, suppress_embeds=True)

    @tree.command(name="importpokemon", description="Owner-only: import local Pokémon knowledge seed CSV/JSON.")
    async def import_pokemon_command(interaction: discord.Interaction):
        logger.info("Slash command invoked: /importpokemon by user_id=%s", interaction.user.id)
        if owner_id is None or interaction.user.id != owner_id:
            await interaction.response.send_message("This owner-only command can only be run by the configured bot owner.", ephemeral=True, suppress_embeds=True)
            return

        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            result = await asyncio.to_thread(import_pokemon_knowledge_seed)
        except Exception as exc:
            logger.exception("Discord-triggered Pokémon knowledge import failed: %s", exc)
            await interaction.followup.send(f"Pokémon knowledge import failed: {exc}", ephemeral=True, suppress_embeds=True)
            return

        message = (
            f"Pokémon knowledge import complete. Imported/upserted {result.get('imported', 0)} row(s). "
            f"Skipped {result.get('skipped', 0)} row(s). "
            f"File type used: {result.get('file_type') or 'none'}."
        )
        path = result.get("path")
        if path:
            message += f" Source file: `{path}`."
        else:
            message += f" {result.get('message', 'No local seed file was found.')}"
        await interaction.followup.send(message[:MAX_DISCORD_MESSAGE_LENGTH], ephemeral=True, suppress_embeds=True)
