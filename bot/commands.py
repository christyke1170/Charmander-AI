"""Discord slash command registration for the Pokémon GO bot."""

from __future__ import annotations

import asyncio
from datetime import datetime
import json
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
from ai.general_chat_answerer import answer_general_chat_query, maybe_add_charmander_suffix
from ai.openai_client import is_openai_enabled
from ai.pokemon_answerer import answer_mixed_query_with_llm, answer_pokemon_query, answer_pokemon_query_with_llm
from ai.pvp_answerer import (
    _requested_pvp_row_count,
    answer_pvp_query_with_llm,
    format_compact_pvp_rankings,
    is_compact_pvp_response,
)
from ai.query_answerer import answer_query, answer_query_with_llm
from ai.wiki_answerer import answer_wiki_query_with_llm, format_wiki_search_fallback
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
    get_event_detail,
    get_active_raid_events,
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
from database.pokemon_go_forms_db import (
    get_pokemon_go_forms_by_dex,
    get_pokemon_go_forms_by_name,
    search_pokemon_go_forms,
)
from database.pvp_rankings_db import (
    count_pvp_rankings,
    get_top_pvp_rankings,
    normalize_pvp_league,
    search_pvp_rankings,
)
from database.wiki_knowledge_db import count_wiki_chunks, search_wiki_chunks
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
from raid_attacker_update import CACHE_NAME as RAID_ATTACKER_CACHE_NAME
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
OWNED_LIST_PATTERN = re.compile(
    r"\b(?:i\s+have|i\s+don't\s+have|i\s+dont\s+have|out\s+of|which\s+of\s+these|from\s+my\s+list|from\s+these)\b",
    re.IGNORECASE,
)
RECOMMENDATION_LANGUAGE_PATTERN = re.compile(
    r"\b(?:who\s+should\s+i\s+use|what\s+should\s+i\s+use|should\s+i\s+use|who\s+is\s+best|which\s+is\s+best|best\s+for\s+raids?|best|power\s+up|worth\s+using|good\s+for\s+raids?|use\s+for\s+raids?)\b",
    re.IGNORECASE,
)
SINGLE_POKEMON_RECOMMENDATION_PATTERN = re.compile(
    r"\b(?:worth\s+using|good\s+for\s+raids?|use\s+for\s+raids?|power\s+up|should\s+i\s+use)\b",
    re.IGNORECASE,
)
SPECIFIC_RAID_POKEMON_PATTERN = re.compile(
    r"\b(?:"
    r"how\s+strong|how\s+good|is\s+.+\s+good|worth\s+investing\s+in|worth\s+it|"
    r"should\s+i\s+invest\s+in|do\s+you\s+recommend(?:\s+me)?\s+invest(?:ing)?\s+in|"
    r"recommend(?:\s+me)?\s+invest(?:ing)?\s+in|good\s+as\s+a?n?\s+\w+\s+attacker|"
    r"worth\s+using\s+for\s+\w+\s+raids?|good\s+for\s+\w+\s+raids?|should\s+i\s+power\s+up|"
    r"power\s+it\s+up|power\s+up"
    r")\b",
    re.IGNORECASE,
)
SPECIFIC_RAID_FOLLOWUP_PATTERN = re.compile(
    r"\b(?:do\s+you\s+recommend(?:\s+me)?\s+invest(?:ing)?\s+in\s+it|should\s+i\s+power\s+it\s+up|is\s+it\s+worth\s+it|"
    r"should\s+i\s+invest\s+in\s+it|do\s+you\s+recommend\s+it|worth\s+it)\b",
    re.IGNORECASE,
)
RAID_ATTACKER_INTENT_PATTERN = re.compile(
    r"\b(?:raid|raids|raiding|pve|attackers?|atackers?|attakers?|tchackers?|attack|counter|counters|best|top|rank|ranking|strongest|dps|tdo)\b",
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
PVP_CONTEXT_PATTERN = re.compile(
    r"\b(?:pvp|pvpoke|great\s+league|ultra\s+league|master\s+league|gl|ul|ml|battle\s+league|go\s+battle\s+league|gbl|league\s+rankings|best\s+pvp\s+pokemon|best\s+great\s+league\s+pokemon|good\s+in\s+great\s+league|good\s+in\s+ultra\s+league|good\s+in\s+master\s+league)\b",
    re.IGNORECASE,
)
PVP_RANKING_INTENT_PATTERN = re.compile(r"\b(?:best|top|rankings?|ranked|good|meta|pokemon|pokémon)\b", re.IGNORECASE)
WIKI_KNOWLEDGE_PATTERN = re.compile(
    r"\b(?:"
    r"shin(?:y|ies)|shiny\s+pokemon|shiny\s+pokémon|lucky\s+pokemon|lucky\s+pokémon|lucky\s+trade|lucky\s+friends?|"
    r"shadow\s+pokemon|shadow\s+pokémon|purified\s+pokemon|purified\s+pokémon|mega\s+evolution|"
    r"dynamax|gigantamax|max\s+battles?|trading|buddy(?:\s+pokemon|\s+pokémon)?|adventure\s+sync|routes?|"
    r"team\s+go\s+rocket|team\s+rocket|rocket|go\s+battle\s+league|gbl|community\s+day|"
    r"field\s+research|special\s+research|timed\s+research|showcases?|pokestop\s+showcase|pokéstop\s+showcase"
    r")\b",
    re.IGNORECASE,
)
WIKI_EXPLANATION_PATTERN = re.compile(r"\b(?:what|how|why|can|does|do|tell|details?|explain|work|works)\b", re.IGNORECASE)
GENERIC_EVENT_SEARCH_PATTERN = re.compile(
    r"\b(?:community\s+day|comm\s+day|spotlight\s+hour|event|events|season|raid|raids)\b",
    re.IGNORECASE,
)
EVENT_TIMING_PATTERN = re.compile(
    r"\b(?:today|active|currently|right\s+now|upcoming|next|this\s+week|weekend|schedule|when)\b",
    re.IGNORECASE,
)
REVERSE_UPCOMING_EVENT_PATTERN = re.compile(
    r"\b(?:furthest\s+out|farthest\s+out|latest\s+first|last\s+first|reverse\s+chronological)\b",
    re.IGNORECASE,
)
NAMED_EVENT_HINT_PATTERN = re.compile(
    r"\b(?:go\s+fest|pok[eé]mon\s+go\s+fest|community\s+day|comm\s+day|spotlight\s+hour|road\s+of\s+legends|tour|safari\s+zone|wild\s+area|unova|johto|sinnoh|hoenn|galar|paldea)\b",
    re.IGNORECASE,
)
POKEMON_GO_TOPIC_PATTERN = re.compile(
    r"\b(?:pokemon\s+go|pokémon\s+go|community\s+day|comm\s+day|raids?|raid\s+hour|raid\s+day|mega\s+evolution|eggs?|hatch(?:es|ed|ing)?|great\s+league|ultra\s+league|master\s+league|gbl|go\s+battle\s+league|battle\s+league|team\s+go\s+rocket|team\s+rocket|rocket|buddy|routes?|field\s+research|special\s+research|timed\s+research|pokestop|pokéstop|gym|dynamax|gigantamax|max\s+battles?|shiny)\b",
    re.IGNORECASE,
)
POKEMON_GO_GENERAL_CHAT_PREFIX = (
    "I don't have that cached yet. I can answer generally, but for Pokémon GO-specific facts I’m more reliable when the wiki/table cache has the topic."
)
POKEMON_GO_FORMS_DEX_PATTERN = re.compile(r"\b(?:pokemon|pokémon|dex|pokedex|pokédex)\s*#?\s*(\d{1,4})\b", re.IGNORECASE)
POKEMON_GO_FORMS_INFO_PATTERN = re.compile(
    r"\b(?:info|pokedex|pokédex|dex|type|types|move|moves|moveset|what\s+type|what\s+moves|stats|cp)\b",
    re.IGNORECASE,
)
PVP_STOP_WORDS = {
    "a",
    "about",
    "are",
    "battle",
    "best",
    "can",
    "for",
    "gbl",
    "give",
    "gl",
    "go",
    "good",
    "great",
    "in",
    "is",
    "league",
    "master",
    "me",
    "ml",
    "pokemon",
    "pokémon",
    "pvp",
    "pvpoke",
    "rankings",
    "ranking",
    "show",
    "should",
    "the",
    "top",
    "ul",
    "ultra",
    "what",
    "which",
}
EVENT_QUERY_FILLER_WORDS = {
    "a",
    "about",
    "an",
    "details",
    "event",
    "events",
    "info",
    "me",
    "tell",
    "the",
}
EVENT_FOLLOWUP_SECTION_PATTERN = re.compile(
    r"\b(?:this\s+event|the\s+event|during\s+this|for\s+this\s+event|that\s+event)\b",
    re.IGNORECASE,
)
EVENT_DETAIL_SECTION_PATTERNS: dict[str, re.Pattern[str]] = {
    "raids": re.compile(r"\b(?:raids?|super\s+mega\s+raids?|raid\s+bosses?)\b", re.IGNORECASE),
    "bonuses": re.compile(r"\b(?:bonuses|bonus)\b", re.IGNORECASE),
    "features": re.compile(r"\b(?:features|highlights?|details?)\b", re.IGNORECASE),
    "spawns": re.compile(r"\b(?:spawns?|wild\s+encounters?|habitats?)\b", re.IGNORECASE),
    "incense": re.compile(r"\b(?:incense(?:\s+spawns?|\s+encounters?)?)\b", re.IGNORECASE),
    "research": re.compile(r"\b(?:research|special\s+research|timed\s+research)\b", re.IGNORECASE),
    "featured_attacks": re.compile(r"\b(?:featured\s+attacks?|attacks?)\b", re.IGNORECASE),
    "shiny": re.compile(r"\b(?:shiny|shinies)\b", re.IGNORECASE),
    "eggs": re.compile(r"\b(?:eggs?|egg\s+spawns?)\b", re.IGNORECASE),
    "sales": re.compile(r"\b(?:sales?|tickets?|store)\b", re.IGNORECASE),
}


def _raid_cache_notice(is_update_running: bool) -> str:
    if is_update_running and is_cache_stale(RAID_ATTACKER_CACHE_NAME, RAID_ATTACKER_CACHE_MAX_AGE_DAYS):
        return "\n\n_Note: cached raid attacker data may be updating in the background._"
    return ""


def _dynamax_cache_notice(is_update_running: bool) -> str:
    if is_update_running:
        return "\n\n_Note: cached Dynamax attacker data may be updating in the background._"
    return ""


def _format_event_date(value: Any) -> Any:
    if not isinstance(value, str) or not value:
        return value
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return value
    return f"{parsed.strftime('%B')} {parsed.day}, {parsed.year}"


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
        details.append(f"Start: {_format_event_date(start)}")
    if end:
        details.append(f"End: {_format_event_date(end)}")
    details.append(f"Source: {source}")

    line = f"**{title}**\n" + "\n".join(details)
    if url:
        line += f"\n<{url}>"
    return line


def _event_detail_sections(detail: dict[str, Any] | None) -> dict[str, list[str]]:
    if not detail:
        return {}
    raw_sections = detail.get("sections_json")
    if isinstance(raw_sections, dict):
        return {
            str(key): [str(item).strip() for item in value if str(item).strip()]
            for key, value in raw_sections.items()
            if isinstance(value, list)
        }
    return {}


def _event_detail_date_line(event: dict[str, Any]) -> str | None:
    start = event.get("start_time")
    end = event.get("end_time")
    if start and end:
        return f"Date/Time: {_format_event_date(start)} to {_format_event_date(end)}"
    if start:
        return f"Start: {_format_event_date(start)}"
    if end:
        return f"End: {_format_event_date(end)}"
    return None


def _section_label(section_key: str) -> str:
    labels = {
        "featured_attacks": "Featured attacks",
        "incense": "Incense encounters",
        "spawns": "Spawns",
        "raids": "Raids",
        "research": "Research",
        "bonuses": "Bonuses",
        "features": "Major features",
        "shiny": "Shiny info",
        "eggs": "Eggs",
        "sales": "Sales",
        "habitats": "Habitats",
    }
    return labels.get(section_key, section_key.replace("_", " ").title())


def _format_event_detail_section(section_key: str, items: list[str], max_items: int = 8) -> list[str]:
    if not items:
        return []
    lines = [f"{_section_label(section_key)}:"]
    lines.extend(f"- {item}" for item in items[:max_items])
    return lines


def _detect_requested_event_detail_sections(query: str | None) -> list[str]:
    normalized = query or ""
    matches = [key for key, pattern in EVENT_DETAIL_SECTION_PATTERNS.items() if pattern.search(normalized)]
    if matches:
        return matches
    return []


def _format_named_event_detail_response(event: dict[str, Any], query: str | None = None) -> str:
    title = str(event.get("title") or "Untitled event").strip()
    detail = get_event_detail(event.get("url"))
    if not detail:
        return _format_event(event)[:MAX_DISCORD_MESSAGE_LENGTH]

    sections = _event_detail_sections(detail)
    requested_sections = _detect_requested_event_detail_sections(query)
    lines = [f"For {title}:"]
    date_line = _event_detail_date_line(event)
    if date_line:
        lines.append(date_line)

    if requested_sections:
        added_any = False
        for key in requested_sections:
            items = sections.get(key, [])
            if items:
                lines.append("")
                lines.extend(_format_event_detail_section(key, items))
                added_any = True
        if not added_any and detail.get("summary_text"):
            lines.append("")
            lines.append(str(detail.get("summary_text")))
    else:
        for key in ("features", "bonuses", "raids", "spawns", "incense", "research"):
            items = sections.get(key, [])
            if items:
                lines.append("")
                lines.extend(_format_event_detail_section(key, items, max_items=6 if key != "spawns" else 5))

    lines.append("")
    lines.append("Source: Leek Duck cached event details.")
    if event.get("url"):
        lines.append(f"<{event.get('url')}>")
    return "\n".join(lines)[:MAX_DISCORD_MESSAGE_LENGTH]


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


def _is_casual_type_chat(query: str) -> bool:
    """Return whether a query is casual preference/opinion chat about types or Pokémon."""

    normalized = query.lower().strip().replace("’", "'")
    if not normalized:
        return False

    has_type = _detect_pokemon_type_from_query(normalized) is not None
    has_type_or_pokemon = has_type or bool(re.search(r"\b(?:pokemon|pokémon|types?)\b", normalized))
    if not has_type_or_pokemon:
        return False

    casual_patterns = (
        r"\bwhat\s+if\s+i\s+do\s*not\s+like\b",
        r"\bwhat\s+if\s+i\s+don't\s+like\b",
        r"\bwhat\s+if\s+i\s+dont\s+like\b",
        r"\bi\s+do\s*not\s+like\b",
        r"\bi\s+don't\s+like\b",
        r"\bi\s+dont\s+like\b",
        r"\bdo\s+you\s+like\b",
        r"\bwhat\s+is\s+your\s+favorite\b",
        r"\bmy\s+favorite\b",
        r"\bi\s+like\b",
        r"\bi\s+hate\b",
        r"\bare\s+.+\s+cool\b",
        r"\b.+\s+are\s+cool\b",
    )
    return any(re.search(pattern, normalized) for pattern in casual_patterns)


def _is_raid_attacker_query(query: str) -> bool:
    """Return whether a natural-language query should use raid attacker rankings."""

    normalized = query.lower().strip()
    if not normalized:
        return True
    if _is_dynamax_query(normalized):
        return False
    if _is_casual_type_chat(normalized):
        return False
    if _is_current_raid_event_query(normalized):
        return False
    if _is_specific_raid_pokemon_query(normalized):
        return True

    detected_type = _detect_pokemon_type_from_query(normalized)
    has_type = detected_type is not None
    has_attacker_word = bool(ATTACKER_WORD_PATTERN.search(normalized))
    has_ranking_language = bool(RANKING_LANGUAGE_PATTERN.search(normalized))
    has_raid_context = bool(RAID_CONTEXT_PATTERN.search(normalized))
    has_explicit_raid_attacker_intent = bool(RAID_ATTACKER_INTENT_PATTERN.search(normalized) or "use in raids" in normalized)
    has_explicit_type_ranking_request = bool(re.search(r"\b(?:top\s+\d{1,3}|best\s+\w+\s+types?|top\s+\w+\s+types?)\b", normalized))

    if RAID_ATTACKER_PHRASE_PATTERN.search(normalized):
        return True
    if has_attacker_word and (has_ranking_language or has_raid_context or has_type):
        return True
    if has_raid_context and has_ranking_language:
        return True
    if has_type and has_explicit_type_ranking_request:
        return True
    if has_type and has_explicit_raid_attacker_intent and (has_attacker_word or has_raid_context or "counter" in normalized or "use in raids" in normalized):
        return True
    if _is_owned_raid_recommendation_query(normalized):
        return True
    return False


def _detect_pokemon_types_from_query(query: str | None) -> list[str]:
    """Return all canonical Pokémon types mentioned in the query, preserving order."""

    normalized = (query or "").lower()
    matches: list[tuple[int, str]] = []
    seen: set[str] = set()
    for pokemon_type in POKEMON_TYPES:
        match = re.search(rf"\b{re.escape(pokemon_type)}\b", normalized)
        if match and pokemon_type not in seen:
            matches.append((match.start(), pokemon_type))
            seen.add(pokemon_type)
    matches.sort(key=lambda item: item[0])
    return [pokemon_type for _, pokemon_type in matches]


def _extract_specific_raid_subject(query: str | None) -> str | None:
    """Extract the likely Pokémon/form subject for specific raid evaluation questions."""

    text = (query or "").strip()
    if not text:
        return None
    try:
        mentions = find_pokemon_mentions(text, limit=5)
    except Exception:
        logger.debug("Could not extract Pokémon mentions for raid evaluation parsing", exc_info=True)
        mentions = []

    candidates: list[str] = []
    for mention in mentions:
        if isinstance(mention, dict):
            name = mention.get("name") or mention.get("pokemon_name")
            if name:
                candidates.append(str(name).strip())
        elif isinstance(mention, str):
            candidates.append(mention.strip())
    if not candidates:
        return None
    return max((candidate for candidate in candidates if candidate), key=len, default=None)


def _is_specific_raid_pokemon_query(query: str | None) -> bool:
    """Return whether a raid query is asking to evaluate one specific Pokémon."""

    normalized = (query or "").lower().strip().replace("’", "'")
    if not normalized or _is_casual_type_chat(normalized) or _is_current_raid_event_query(normalized):
        return False
    if not _extract_specific_raid_subject(query):
        return False
    if SPECIFIC_RAID_POKEMON_PATTERN.search(normalized):
        return True
    if re.search(r"\b(?:how\s+strong|how\s+good|good\s+for\s+raids?|worth\s+using)\b", normalized) and (
        RAID_CONTEXT_PATTERN.search(normalized) or _detect_pokemon_types_from_query(normalized)
    ):
        return True
    return False


def _normalize_subject_name(value: str | None) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (value or "").lower()).strip()


def _row_subject_keys(row: dict[str, Any]) -> set[str]:
    name = str(row.get("pokemon_name") or "").strip()
    form = str(row.get("form") or "").strip()
    keys = {_normalize_subject_name(name)} if name else set()
    if form:
        full_name = name if form.lower() in name.lower() else f"{form} {name}".strip()
        keys.add(_normalize_subject_name(full_name))
    return {key for key in keys if key}


def _filter_rows_for_specific_subject(rows: list[dict[str, Any]], subject: str | None) -> list[dict[str, Any]]:
    subject_key = _normalize_subject_name(subject)
    if not subject_key:
        return []
    matched: list[dict[str, Any]] = []
    for row in rows:
        keys = _row_subject_keys(row)
        if any(subject_key == key or subject_key in key or key in subject_key for key in keys):
            matched.append(row)
    return matched


def _best_specific_row(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not rows:
        return None
    return sorted(
        rows,
        key=lambda row: (
            row.get("rank") is None,
            row.get("rank") or 9999,
            -_metric_as_float(row.get("score")),
            -_metric_as_float(row.get("dps")),
            str(row.get("pokemon_type") or ""),
        ),
    )[0]


def _format_specific_raid_metric_line(pokemon_type: str, row: dict[str, Any]) -> str:
    moveset = (
        f"{str(row.get('fast_move') or 'fast move unknown').strip()} / "
        f"{str(row.get('charged_move') or 'charged move unknown').strip()}"
    )
    pieces = [f"{pokemon_type.title()}: ranked #{row.get('rank')} among cached {pokemon_type.title()}-type raid attackers with {moveset}"]
    metrics: list[str] = []
    if row.get("score"):
        metrics.append(f"Score {row.get('score')}")
    if row.get("dps"):
        metrics.append(f"DPS {row.get('dps')}")
    if metrics:
        pieces.append("— " + ", ".join(metrics) + ".")
    else:
        pieces.append(".")
    return " ".join(pieces)


def _format_specific_raid_response(query: str | None, rows: list[dict[str, Any]], max_chars: int) -> str:
    subject = _extract_specific_raid_subject(query) or "That Pokémon"
    subject_rows = _filter_rows_for_specific_subject(rows, subject)
    if not subject_rows:
        return f"I couldn’t find cached raid attacker rankings for {subject}.\nSource: cached raid attacker rankings."[:max_chars]

    requested_types = _detect_pokemon_types_from_query(query)
    is_investment = bool(re.search(r"\b(?:invest|investment|worth\s+it|power\s+up|recommend)\b", query or "", re.IGNORECASE))
    best_row = _best_specific_row(subject_rows)
    best_type = str(best_row.get("pokemon_type") or "raid").title() if best_row else "raid"

    if is_investment:
        lines = [f"Yes — based on the cached {best_type} rankings, {subject} looks like a strong investment for {best_type} raids."]
    else:
        lines = [f"{subject} looks strong from the cached raid rankings."]

    if requested_types:
        for pokemon_type in requested_types:
            type_rows = [row for row in subject_rows if str(row.get("pokemon_type") or "").lower() == pokemon_type]
            best_type_row = _best_specific_row(type_rows)
            if best_type_row:
                lines.append("")
                lines.append(_format_specific_raid_metric_line(pokemon_type, best_type_row))
            else:
                lines.append("")
                lines.append(
                    f"{pokemon_type.title()}: I do not see {subject} in the cached {pokemon_type.title()}-type top rankings."
                )
    elif best_row:
        lines.append("")
        lines.append(_format_specific_raid_metric_line(str(best_row.get("pokemon_type") or "raid"), best_row))

    if best_row:
        verdict_type = str(best_row.get("pokemon_type") or "raid").title()
        move_name = str(best_row.get("charged_move") or "its best charged move").strip()
        if is_investment:
            lines.append("")
            lines.append(f"I’d invest if you get a good one, especially if you can run {move_name}.")
        else:
            lines.append("")
            lines.append(f"Verdict: as a {verdict_type} attacker, it looks like a high-priority raid investment from this cache.")

    lines.append("Source: cached raid attacker rankings.")
    return "\n".join(lines)[:max_chars]


def _infer_specific_raid_subject_from_bot_message(content: str | None) -> str | None:
    """Recover the previously discussed Pokémon subject from a specific-evaluation bot reply."""

    text = (content or "").strip()
    if not text:
        return None
    patterns = (
        r"^([A-Z][A-Za-z0-9' .-]+?) looks",
        r"cached [A-Za-z]+ rankings, ([A-Z][A-Za-z0-9' .-]+?) looks",
        r"I do not see ([A-Z][A-Za-z0-9' .-]+?) in the cached",
    )
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.MULTILINE)
        if match:
            return match.group(1).strip(" .")
    return None


def _is_specific_raid_followup_without_subject(query: str | None) -> bool:
    normalized = (query or "").lower().strip().replace("’", "'")
    if not normalized or _extract_specific_raid_subject(query):
        return False
    return bool(SPECIFIC_RAID_FOLLOWUP_PATTERN.search(normalized))


def _inject_subject_into_followup_query(query: str, subject: str) -> str:
    normalized = query.strip()
    if re.search(r"\bit\b", normalized, flags=re.IGNORECASE):
        return re.sub(r"\bit\b", subject, normalized, flags=re.IGNORECASE)
    return f"{normalized} {subject}".strip()


def _is_dynamax_query(query: str) -> bool:
    """Return whether a query should use cached Dynamax/Gigantamax rankings."""

    normalized = query.lower().strip()
    if not normalized:
        return False
    if re.search(r"\b(?:what\s+(?:is|are)|how\s+(?:does|do)|tell\s+me\s+about|explain|details?\s+about)\b", normalized):
        has_ranking_intent = bool(ATTACKER_WORD_PATTERN.search(normalized) or RANKING_LANGUAGE_PATTERN.search(normalized))
        if re.search(r"\b(?:dynamax|gigantamax|max\s+battles?)\b", normalized) and not has_ranking_intent:
            return False
    return bool(DYNAMAX_CONTEXT_PATTERN.search(normalized))


def _is_event_specific_query(query: str) -> bool:
    normalized = query.lower()
    return any(term in normalized for term in EVENT_QUERY_TERMS)


def _normalize_event_match_text(value: str | None) -> str:
    text = (value or "").lower()
    text = text.replace("pokémon", "pokemon")
    text = re.sub(r"\bpokemon\s+go\s+fest\b", "go fest", text)
    text = re.sub(r"\bcomm\s+day\b", "community day", text)
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _event_query_meaningful_tokens(query: str | None) -> list[str]:
    normalized = _normalize_event_match_text(query)
    if not normalized:
        return []
    return [token for token in normalized.split() if token and token not in EVENT_QUERY_FILLER_WORDS]


def _event_query_search_phrases(query: str) -> list[str]:
    normalized = _normalize_event_match_text(query)
    if not normalized:
        return []

    phrases: list[str] = []

    def add_phrase(value: str) -> None:
        candidate = _normalize_event_match_text(value)
        if candidate and candidate not in phrases:
            phrases.append(candidate)

    add_phrase(normalized)
    stripped = re.sub(
        r"\b(?:tell\s+me\s+about|what\s+is|what\s+are|details?\s+(?:for|about)|about|the|a|an|info|details?|event|events|pokemon\s+go)\b",
        " ",
        normalized,
        flags=re.IGNORECASE,
    )
    add_phrase(stripped)
    if "go fest" in normalized:
        add_phrase("go fest")
        add_phrase(normalized.replace("go fest", "pokemon go fest"))
        add_phrase(stripped.replace("go fest", "pokemon go fest"))
        tokens = _event_query_meaningful_tokens(query)
        year_tokens = [token for token in tokens if re.fullmatch(r"20\d{2}", token)]
        non_year_tokens = [token for token in tokens if token not in year_tokens]
        if non_year_tokens:
            add_phrase(" ".join(non_year_tokens))
        if year_tokens:
            add_phrase(f"go fest {' '.join(year_tokens)}")
            add_phrase(f"go fest {' '.join(non_year_tokens + year_tokens)}")
            add_phrase(f"go fest {' '.join(year_tokens + [token for token in non_year_tokens if token not in {'go', 'fest'}])}")
    return phrases


def _score_named_event_match(query: str, event: dict[str, Any]) -> tuple[int, int, int, str]:
    phrases = _event_query_search_phrases(query)
    meaningful_tokens = _event_query_meaningful_tokens(query)
    title = _normalize_event_match_text(event.get("title"))
    category = _normalize_event_match_text(event.get("category"))
    summary = _normalize_event_match_text(event.get("summary"))
    raw_text = _normalize_event_match_text(event.get("raw_text"))
    title_tokens = set(title.split())

    title_score = 0
    text_score = 0
    matched_length = 0
    for phrase in phrases:
        if not phrase:
            continue
        phrase_len = len(phrase)
        if phrase == title:
            title_score = max(title_score, 400)
            matched_length = max(matched_length, phrase_len)
        elif phrase and phrase in title:
            title_score = max(title_score, 320)
            matched_length = max(matched_length, phrase_len)

        phrase_tokens = [token for token in phrase.split() if token]
        if phrase_tokens and all(token in title.split() for token in phrase_tokens):
            title_score = max(title_score, 280)
            matched_length = max(matched_length, phrase_len)
        if phrase and phrase in category:
            text_score = max(text_score, 120)
            matched_length = max(matched_length, phrase_len)
        if phrase and (phrase in summary or phrase in raw_text):
            text_score = max(text_score, 80)
            matched_length = max(matched_length, phrase_len)

    if meaningful_tokens and all(token in title_tokens for token in meaningful_tokens):
        title_score = max(title_score, 360)
        matched_length = max(matched_length, len(" ".join(meaningful_tokens)))

    year_bonus = 0
    year_match = re.search(r"\b(20\d{2})\b", _normalize_event_match_text(query))
    if year_match and year_match.group(1) in title:
        year_bonus = 30

    return title_score, text_score + year_bonus, matched_length, title


def _lookup_named_event(query: str, limit: int = 1) -> list[dict[str, Any]]:
    phrases = _event_query_search_phrases(query)
    if not phrases:
        return []

    candidates: dict[tuple[Any, ...], dict[str, Any]] = {}
    for phrase in phrases[:4]:
        for event in search_events(phrase, limit=10):
            key = (
                event.get("id"),
                event.get("source"),
                event.get("title"),
                event.get("start_time"),
            )
            candidates[key] = event

    ranked: list[tuple[tuple[int, int, int, str], dict[str, Any]]] = []
    for event in candidates.values():
        score = _score_named_event_match(query, event)
        if score[0] <= 0:
            continue
        ranked.append((score, event))

    ranked.sort(key=lambda item: item[0], reverse=True)
    return [event for _, event in ranked[: max(1, int(limit))]]


def _infer_named_event_from_bot_message(content: str | None) -> dict[str, Any] | None:
    text = (content or "").strip()
    if not text:
        return None

    url_match = re.search(r"<(https?://[^>]+)>", text)
    if url_match:
        detail_event = next(iter(search_events(url_match.group(1), limit=1)), None)
        if detail_event:
            return detail_event

    title_match = re.search(r"(?:\*\*(.+?)\*\*|^For\s+(.+?):)", text, flags=re.MULTILINE)
    if title_match:
        title = title_match.group(1) or title_match.group(2)
        title = str(title).strip()
        if title:
            matches = _lookup_named_event(title, limit=1)
            if matches:
                return matches[0]
    return None


def _is_event_detail_followup_query(query: str | None) -> bool:
    normalized = (query or "").strip()
    if not normalized:
        return False
    return bool(EVENT_FOLLOWUP_SECTION_PATTERN.search(normalized) or _detect_requested_event_detail_sections(normalized))


def _sort_upcoming_events(events: list[dict[str, Any]], query: str | None) -> list[dict[str, Any]]:
    normalized = (query or "").lower()
    if not normalized:
        return events
    if "starting from furthest out" in normalized or REVERSE_UPCOMING_EVENT_PATTERN.search(normalized):
        return sorted(
            events,
            key=lambda event: (
                event.get("start_time") is None,
                event.get("start_time") or "",
                event.get("scraped_at") or "",
            ),
            reverse=True,
        )
    return events


def _should_try_named_event_lookup(query: str) -> bool:
    normalized = (query or "").strip()
    if not normalized:
        return False
    if _is_current_raid_event_query(normalized) or _is_raid_attacker_query(normalized):
        return False
    return bool(NAMED_EVENT_HINT_PATTERN.search(normalized))


def _should_try_generic_event_search(query: str) -> bool:
    """Return whether a query should fall through to cached event/news search."""

    normalized = query.lower().strip()
    if not normalized:
        return False
    if _is_current_raid_event_query(normalized):
        return True
    if not GENERIC_EVENT_SEARCH_PATTERN.search(normalized):
        return False
    return bool(EVENT_TIMING_PATTERN.search(normalized) or _is_event_specific_query(normalized))


def _is_pokemon_go_topic_query(query: str) -> bool:
    """Return whether a query looks Pokémon GO-specific even if no cache matched."""

    normalized = query.lower().strip()
    if not normalized:
        return False
    if _is_dynamax_query(normalized) or _is_raid_attacker_query(normalized) or _is_egg_pool_query(normalized) or _is_pvp_query(normalized):
        return True
    if _is_current_raid_event_query(normalized) or _is_wiki_knowledge_query(normalized):
        return True
    return bool(POKEMON_GO_TOPIC_PATTERN.search(normalized))


def _event_cache_miss_message(query: str) -> str:
    if _is_current_raid_event_query(query):
        return "I couldn’t find current local raid event data for that yet. Try `/raids` or run `/update` if you are the bot owner."
    return "I couldn’t find matching local Pokémon GO event data for that yet. Try `/events` to see the latest stored events, or run `/update` if you are the bot owner."


def _parse_cached_move_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return []
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            parsed = [part.strip() for part in text.split("|") if part.strip()]
    elif isinstance(value, (list, tuple, set)):
        parsed = list(value)
    else:
        parsed = [value]
    if not isinstance(parsed, list):
        parsed = [parsed]
    return [str(item).strip() for item in parsed if str(item).strip()]


def _pokemon_go_forms_display_name(row: dict[str, Any]) -> str:
    name = str(row.get("pokemon_name") or "Unknown Pokémon").strip()
    form = str(row.get("form") or "").strip()
    if not form:
        return name
    if form.lower() in name.lower():
        return name
    return f"{name} ({form})"


def _pokemon_go_forms_sort_key(row: dict[str, Any], preferred_name: str | None = None) -> tuple[Any, ...]:
    name = str(row.get("pokemon_name") or "").strip().lower()
    form = str(row.get("form") or "").strip().lower()
    preferred = (preferred_name or "").strip().lower()
    return (
        0 if preferred and name == preferred else 1,
        0 if not form else 1,
        int(bool(row.get("is_shadow"))),
        int(bool(row.get("is_mega"))),
        int(bool(row.get("is_gigantamax"))),
        row.get("dex_number") or 9999,
        name,
        form,
    )


def _best_pokemon_go_forms_row(rows: list[dict[str, Any]], preferred_name: str | None = None) -> dict[str, Any] | None:
    if not rows:
        return None
    return sorted(rows, key=lambda row: _pokemon_go_forms_sort_key(row, preferred_name))[0]


def format_pokemon_go_forms_row(row: dict[str, Any]) -> str:
    lines: list[str] = []
    dex_number = row.get("dex_number")
    display_name = _pokemon_go_forms_display_name(row)
    if dex_number:
        lines.append(f"Pokémon #{dex_number} is {display_name}.")
    else:
        lines.append(f"Pokémon GO entry: {display_name}.")

    type_values = [str(row.get("type_1") or "").strip(), str(row.get("type_2") or "").strip()]
    types = [value for value in type_values if value]
    if types:
        lines.append("")
        lines.append(f"Type: {' / '.join(types)}")

    fast_moves = _parse_cached_move_list(row.get("fast_moves"))
    if fast_moves:
        lines.append(f"Fast moves: {', '.join(fast_moves)}")

    charged_moves = _parse_cached_move_list(row.get("charged_moves"))
    if charged_moves:
        lines.append(f"Charged moves: {', '.join(charged_moves)}")

    attack = row.get("attack")
    defense = row.get("defense")
    stamina = row.get("stamina")
    if any(value is not None for value in (attack, defense, stamina)):
        stat_parts: list[str] = []
        if attack is not None:
            stat_parts.append(f"{attack} Atk")
        if defense is not None:
            stat_parts.append(f"{defense} Def")
        if stamina is not None:
            stat_parts.append(f"{stamina} Sta")
        if stat_parts:
            lines.append(f"Stats: {' / '.join(stat_parts)}")

    if row.get("max_cp") is not None:
        lines.append(f"Max CP: {row.get('max_cp')}")

    lines.append("")
    lines.append("Source: Pokémon GO Hub cached Pokédex.")
    return "\n".join(lines)[:MAX_DISCORD_MESSAGE_LENGTH]


def _extract_pokemon_go_forms_dex_number(query: str | None) -> int | None:
    match = POKEMON_GO_FORMS_DEX_PATTERN.search(query or "")
    if not match:
        return None
    try:
        return int(match.group(1))
    except ValueError:
        return None


def _pokemon_go_forms_name_candidate(query: str | None) -> str | None:
    text = (query or "").strip()
    if not text:
        return None

    try:
        mentions = find_pokemon_mentions(text, limit=5)
    except Exception:
        logger.debug("Could not extract Pokémon mentions for Pokémon GO forms parsing", exc_info=True)
        mentions = []

    candidates: list[str] = []
    for mention in mentions:
        if isinstance(mention, dict):
            name = mention.get("pokemon_name") or mention.get("name")
            if name:
                candidates.append(str(name).strip())
        elif isinstance(mention, str):
            candidates.append(mention.strip())
    if candidates:
        return max((candidate for candidate in candidates if candidate), key=len, default=None)

    cleaned = re.sub(r"\b(?:pokemon\s+go|pokémon\s+go|give\s+me|tell\s+me|what|which|does|do|is|are|the|a|an|info|pokedex|pokédex|dex|for|on|about|in|type|types|move|moves|moveset|stats|cp|have)\b", " ", text, flags=re.IGNORECASE)
    cleaned = re.sub(r"\b\d{1,4}\b", " ", cleaned)
    cleaned = re.sub(r"[?!.:,;()\[\]{}#/_-]+", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned or None


def _should_try_pokemon_go_forms_query(query: str | None) -> bool:
    normalized = (query or "").lower().strip()
    if not normalized:
        return False
    if _is_dynamax_query(normalized) or _is_raid_attacker_query(normalized) or _is_egg_pool_query(normalized) or _is_pvp_query(normalized):
        return False
    if _is_current_raid_event_query(normalized) or _is_wiki_knowledge_query(normalized):
        return False
    if _extract_pokemon_go_forms_dex_number(normalized) is not None:
        return True
    name_candidate = _pokemon_go_forms_name_candidate(query)
    if not name_candidate:
        return False
    if POKEMON_GO_FORMS_INFO_PATTERN.search(normalized):
        return True
    return bool(re.search(r"\b(?:pokemon\s+go|pokémon\s+go)\b", normalized))


def _lookup_pokemon_go_forms_row(query: str | None) -> dict[str, Any] | None:
    dex_number = _extract_pokemon_go_forms_dex_number(query)
    if dex_number is not None:
        return _best_pokemon_go_forms_row(get_pokemon_go_forms_by_dex(dex_number))

    candidate = _pokemon_go_forms_name_candidate(query)
    if not candidate:
        return None

    rows = get_pokemon_go_forms_by_name(candidate)
    if not rows:
        rows = search_pokemon_go_forms(candidate, limit=10)
    return _best_pokemon_go_forms_row(rows, preferred_name=candidate)


def _build_pokemon_go_forms_response(query: str | None) -> str | None:
    if not _should_try_pokemon_go_forms_query(query):
        return None
    row = _lookup_pokemon_go_forms_row(query)
    if not row:
        return None
    return format_pokemon_go_forms_row(row)


def _build_general_chat_response(query: str, *, allow_suffix: bool = True) -> str:
    """Return a conversational response with Charmander flavor and safe GO honesty."""

    context: str | None = None
    pokemon_go_topic = _is_pokemon_go_topic_query(query)
    if pokemon_go_topic:
        context = (
            "This query appears Pokémon GO-specific, but no cached table/wiki route produced a result. "
            "Be honest about the cache miss, do not invent exact current Pokémon GO data, and do not claim live web access unless a web/search provider is configured."
        )
    answer = answer_general_chat_query(query, context=context)
    if pokemon_go_topic and POKEMON_GO_GENERAL_CHAT_PREFIX not in answer:
        answer = f"{POKEMON_GO_GENERAL_CHAT_PREFIX}\n\n{answer}"
    return maybe_add_charmander_suffix(answer[:MAX_DISCORD_MESSAGE_LENGTH], allow_suffix=allow_suffix)[:MAX_DISCORD_MESSAGE_LENGTH]


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


def _is_pvp_query(query: str) -> bool:
    """Return whether a natural-language query should use cached PvPoke rankings."""

    normalized = query.lower().strip()
    if not normalized:
        return False
    if re.search(r"\b(?:what\s+(?:is|are)|how\s+(?:does|do)|tell\s+me\s+about|explain|details?\s+about)\b", normalized):
        if re.search(r"\b(?:go\s+battle\s+league|gbl|battle\s+league)\b", normalized):
            return False
    if _is_dynamax_query(normalized) or _is_raid_attacker_query(normalized) or _is_egg_pool_query(normalized):
        return False
    if _is_current_raid_event_query(normalized):
        return False
    if "raid" in normalized and not PVP_CONTEXT_PATTERN.search(normalized):
        return False
    if PVP_CONTEXT_PATTERN.search(normalized):
        return True
    league = normalize_pvp_league(normalized)
    return bool(league and PVP_RANKING_INTENT_PATTERN.search(normalized))


def _is_wiki_knowledge_query(query: str) -> bool:
    """Return whether a query should use cached Pokémon GO Wiki/Fandom knowledge."""

    normalized = query.lower().strip()
    if not normalized:
        return False
    if _is_dynamax_query(normalized) or _is_raid_attacker_query(normalized) or _is_egg_pool_query(normalized) or _is_pvp_query(normalized):
        return False
    if _is_current_raid_event_query(normalized):
        return False
    if not WIKI_KNOWLEDGE_PATTERN.search(normalized):
        return False
    if WIKI_EXPLANATION_PATTERN.search(normalized):
        return True
    # Slash /wiki should always search. Mention routing should accept concise topic-only queries too.
    return len(normalized.split()) <= 5


def _pvp_pokemon_candidate(query: str | None) -> str | None:
    """Extract a likely Pokémon name from a PvP question."""

    if not query:
        return None
    try:
        mentions = find_pokemon_mentions(query, limit=1)
    except Exception:
        logger.debug("Could not use Pokémon knowledge mentions for PvP query parsing; falling back to text cleanup", exc_info=True)
        mentions = []
    if mentions:
        mention = mentions[0]
        if isinstance(mention, dict):
            name = mention.get("pokemon_name") or mention.get("name")
            if name:
                return str(name)
        if isinstance(mention, str):
            return mention

    text = query.replace("’", "'")
    text = re.sub(r"\b(?:great|ultra|master)\s+league\b", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"\b(?:go\s+battle\s+league|battle\s+league|league\s+rankings)\b", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"\btop\s+\d{1,3}\b", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"\b\d{1,3}\b", " ", text)
    text = re.sub(r"[?!.:,;()\[\]{}]", " ", text)
    tokens = [token for token in re.split(r"\s+", text.strip()) if token]
    candidate_tokens = [token for token in tokens if token.lower() not in PVP_STOP_WORDS]
    candidate = re.sub(r"\s+", " ", " ".join(candidate_tokens)).strip()
    return candidate if len(candidate) >= 2 else None


def get_pvp_rows_for_query(query: str, limit: int = 20) -> tuple[list[dict], str, str | None]:
    """Return cached PvPoke rows for a query, route used, and normalized league."""

    normalized = (query or "").strip().lower()
    league = normalize_pvp_league(normalized)
    safe_limit = max(1, min(int(limit), 20))
    if not normalized:
        return [], "overview", None

    candidate = _pvp_pokemon_candidate(normalized)
    if candidate:
        rows = search_pvp_rankings(candidate, league=league, limit=safe_limit)
        if rows:
            return rows, "pokemon_search", league

    if league:
        return get_top_pvp_rankings(league, limit=safe_limit), f"league:{league}", league

    if "pvp" in normalized or "pvpoke" in normalized or "gbl" in normalized or "battle league" in normalized:
        return [], "overview", None

    if candidate:
        rows = search_pvp_rankings(candidate, league=None, limit=safe_limit)
        if rows:
            return rows, "pokemon_search", None
    return [], "overview", None


def build_pvp_response(query: str | None, rows: list[dict], route: str, league: str | None) -> str:
    """Return a user-facing response from cached PvPoke data only."""

    if count_pvp_rankings() == 0:
        return "The PvP ranking cache is empty. Ask the bot owner to run `/updatepvp`."
    if route != "overview" and not rows:
        return "No matching cached PvPoke rankings were found.\nSource: cached PvPoke rankings."

    requested_count = _requested_pvp_row_count(query)
    compact_answer = format_compact_pvp_rankings(query, rows, league, route, max_rows=requested_count)
    if is_openai_enabled() and rows:
        llm_answer = answer_pvp_query_with_llm(query or "pvp rankings", rows, league, route, compact_answer)
        if is_compact_pvp_response(llm_answer, query, requested_count, MAX_DISCORD_MESSAGE_LENGTH):
            return llm_answer[:MAX_DISCORD_MESSAGE_LENGTH]
        logger.debug("PvP LLM response did not satisfy compact Discord format; using compact fallback formatter")
    return compact_answer[:MAX_DISCORD_MESSAGE_LENGTH]


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


def build_wiki_response(query: str | None = None) -> str:
    """Return a Discord-ready answer from cached Pokémon GO Wiki chunks only."""

    normalized = (query or "").strip()
    if not normalized:
        return "Ask a Pokémon GO Wiki question like `/wiki shiny pokemon`, `/wiki lucky pokemon`, or `/wiki how does mega evolution work`."
    chunks = search_wiki_chunks(normalized, limit=8)
    if not chunks:
        return "I couldn’t find that in the cached Pokémon GO Wiki data. Ask the bot owner to update the wiki cache or add that page to the seed list."
    if is_openai_enabled():
        return answer_wiki_query_with_llm(normalized, chunks)[:MAX_DISCORD_MESSAGE_LENGTH]
    return format_wiki_search_fallback(normalized, chunks)[:MAX_DISCORD_MESSAGE_LENGTH]


def _event_context(events: list[dict[str, Any]]) -> str:
    return "\n\n".join(_format_event(event) for event in events[:10])[:6000]


def _detect_raid_attacker_type(query: str | None) -> str | None:
    return _detect_pokemon_type_from_query(query or "") or normalize_type_name(query or "")


def _normalize_candidate_name(value: str | None) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (value or "").lower()).strip()


def _clean_candidate_fragment(value: str) -> str:
    cleaned = value.strip(" \t\n\r.,!?;:-")
    cleaned = re.sub(
        r"\b(?:who\s+should\s+i\s+use|what\s+should\s+i\s+use|who\s+is\s+best|which\s+is\s+best|should\s+i\s+power\s+up|should\s+i\s+use|best\s+for\s+raids?|for\s+\w+\s+raids?|for\s+raids?|raid\s+attackers?|worth\s+using)\b",
        " ",
        cleaned,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" \t\n\r.,!?;:-")
    return cleaned


def _extract_owned_pokemon_candidates(query: str | None, limit: int = 8) -> list[str]:
    """Extract likely user-owned Pokémon candidates from a natural-language query."""

    text = (query or "").replace("’", "'")
    candidates: list[str] = []
    seen: set[str] = set()

    def add_candidate(value: str) -> None:
        cleaned = _clean_candidate_fragment(value)
        if not cleaned:
            return
        normalized = _normalize_candidate_name(cleaned)
        if not normalized or normalized in seen:
            return
        if normalized in {"fire", "water", "grass", "electric", "raids", "raid", "pokemon", "pokémon", "these", "list"}:
            return
        seen.add(normalized)
        candidates.append(cleaned)

    clause_patterns = (
        r"\bi\s+don't\s+have\s+(.+?)(?:(?:\.|\?|!|$)|\bi\s+have\b)",
        r"\bi\s+dont\s+have\s+(.+?)(?:(?:\.|\?|!|$)|\bi\s+have\b)",
        r"\bi\s+have\s+(.+?)(?:[.?!]|$)",
        r"\bout\s+of\s+(.+?)(?:(?:,?\s+(?:who|which|what)\b)|[.?!]|$)",
        r"\bwhich\s+of\s+these(?:\s+should\s+i\s+(?:use|power\s+up))?\s*:\s*(.+?)(?:[.?!]|$)",
        r"\bfrom\s+(?:my\s+list|these)\s*:\s*(.+?)(?:[.?!]|$)",
    )
    for pattern in clause_patterns:
        for match in re.finditer(pattern, text, flags=re.IGNORECASE):
            clause = match.group(1)
            for piece in re.split(r",|\band\b|\bor\b|/", clause, flags=re.IGNORECASE):
                add_candidate(piece)

    try:
        mentions = find_pokemon_mentions(text, limit=limit)
    except Exception:
        logger.debug("Could not extract Pokémon mentions for owned recommendation parsing", exc_info=True)
        mentions = []
    for mention in mentions:
        if isinstance(mention, dict):
            add_candidate(str(mention.get("name") or mention.get("pokemon_name") or ""))
        elif isinstance(mention, str):
            add_candidate(mention)

    return candidates[:limit]


def _is_owned_raid_recommendation_query(query: str | None) -> bool:
    normalized = (query or "").lower().strip().replace("’", "'")
    if not normalized or _is_casual_type_chat(normalized):
        return False

    candidates = _extract_owned_pokemon_candidates(normalized)
    if not candidates:
        return False

    detected_type = _detect_raid_attacker_type(normalized)
    has_raid_context = bool(RAID_CONTEXT_PATTERN.search(normalized))
    has_owned_list = bool(OWNED_LIST_PATTERN.search(normalized))
    has_recommendation_language = bool(RECOMMENDATION_LANGUAGE_PATTERN.search(normalized))
    has_mega_context = "mega" in normalized

    if len(candidates) >= 2 and has_owned_list and (has_recommendation_language or has_raid_context or detected_type or has_mega_context):
        return True
    if len(candidates) >= 2 and has_recommendation_language and (has_raid_context or detected_type or has_mega_context):
        return True
    if len(candidates) == 1 and SINGLE_POKEMON_RECOMMENDATION_PATTERN.search(normalized) and (has_raid_context or detected_type):
        return True
    return False


def _metric_as_float(value: Any) -> float:
    try:
        return float(str(value).strip())
    except (TypeError, ValueError, AttributeError):
        return float("-inf")


def _owned_recommendation_heading(route: str, query: str | None) -> str:
    if route.startswith("owned:type:"):
        route_type = route.split(":", 2)[2].title()
        return f"From your list for {route_type} raids:"
    detected_type = _detect_raid_attacker_type(query or "")
    if detected_type:
        return f"From your list for {detected_type.title()} raids:"
    return "From your list for raids:"


def _build_owned_raid_attacker_response(query: str | None, rows: list[dict[str, Any]], route: str, max_chars: int) -> str:
    candidates = _extract_owned_pokemon_candidates(query)
    if not candidates:
        return _format_compact_raid_attacker_rows(rows, route.replace("owned:", "", 1), query, max_rows=_requested_row_count(query), max_chars=max_chars)

    row_by_name: dict[str, dict[str, Any]] = {}
    for row in rows:
        name_key = _normalize_candidate_name(row.get("pokemon_name"))
        if name_key and name_key not in row_by_name:
            row_by_name[name_key] = row

    matched: list[tuple[str, dict[str, Any]]] = []
    missing: list[str] = []
    for candidate in candidates:
        matched_row = row_by_name.get(_normalize_candidate_name(candidate))
        if matched_row:
            matched.append((candidate, matched_row))
        else:
            missing.append(candidate)

    if route.startswith("owned:type:"):
        matched.sort(key=lambda item: ((item[1].get("rank") is None), item[1].get("rank") or 9999, -_metric_as_float(item[1].get("score")), -_metric_as_float(item[1].get("dps")), item[0].lower()))
    else:
        matched.sort(key=lambda item: (-_metric_as_float(item[1].get("score")), -_metric_as_float(item[1].get("dps")), (item[1].get("rank") is None), item[1].get("rank") or 9999, item[0].lower()))

    lines = [_owned_recommendation_heading(route, query)]
    index = 1
    for candidate, row in matched:
        rank = row.get("rank")
        parts: list[str] = []
        if rank is not None and route.startswith("owned:type:"):
            route_type = route.split(":", 2)[2].title()
            parts.append(f"ranked #{rank} in cached {route_type} raid attackers")
        score = row.get("score")
        dps = row.get("dps")
        if score and dps:
            parts.append(f"Score {score}, DPS {dps}")
        elif score:
            parts.append(f"Score {score}")
        elif dps:
            parts.append(f"DPS {dps}")
        detail = "; ".join(parts) if parts else "found in cached raid attacker rankings"
        lines.append(f"{index}. {candidate} — {detail}.")
        index += 1

    for candidate in missing:
        lines.append(f"{index}. {candidate} — filler/unknown; I do not see it in the top cached raid attacker rankings.")
        index += 1

    lines.append("Source: cached raid attacker rankings.")
    response = "\n".join(lines)
    return response[:max_chars]


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
    if _is_specific_raid_pokemon_query(query):
        subject = _extract_specific_raid_subject(query)
        if subject:
            return _filter_rows_for_specific_subject(search_raid_attackers(subject, limit=max(limit, 100)), subject), "specific_eval"
    if _is_owned_raid_recommendation_query(normalized):
        detected_type = _detect_raid_attacker_type(normalized)
        if detected_type:
            return get_top_raid_attackers_by_type(detected_type, limit=max(limit, 100)), f"owned:type:{detected_type}"
        return get_best_raid_attackers_across_types(limit=max(limit, 100)), "owned:derived_overall"
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
    if route == "specific_eval":
        return _format_specific_raid_response(query, rows, max_body_length) + notice
    if route.startswith("owned:"):
        return _build_owned_raid_attacker_response(query, rows, route, max_body_length) + notice
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
    if _is_current_raid_event_query(normalized):
        events = get_active_raid_events(limit=10)
        return "raids", "Here are the raid events active today:", events
    if "raid" in normalized and any(term in normalized for term in ("this week", "week", "care about", "worth doing", "upcoming", "next", "future", "schedule")):
        events = _sort_upcoming_events(get_upcoming_events(limit=10), query)
        return "upcoming", "Upcoming Pokémon GO Events", events
    if "raid" in normalized:
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
    if any(term in normalized for term in ("this week", "week", "care about", "worth doing", "upcoming", "next", "future", "schedule")):
        events = _sort_upcoming_events(get_upcoming_events(limit=10), query)
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

    if _is_pvp_query(query):
        pvp_count = _requested_pvp_row_count(query)
        rows, route, league = get_pvp_rows_for_query(query, limit=pvp_count)
        return build_pvp_response(query, rows, route, league), "pvp", len(rows)

    if _is_current_raid_event_query(query):
        route, heading, events = route_mention_query(query)
        if not events:
            return (
                "I couldn’t find current local raid event data for that yet. Try `/raids` or run `/update` if you are the bot owner.",
                route,
                0,
            )
        return build_event_response(heading, events, "No matching local raid event data found."), route, len(events)

    forms_answer = _build_pokemon_go_forms_response(query)
    if forms_answer:
        return forms_answer, "pokemon_go_forms", 1

    if _should_try_named_event_lookup(query):
        named_events = _lookup_named_event(query, limit=1)
        if named_events:
            return _format_named_event_detail_response(named_events[0], query), "named_event", len(named_events)

    pokemon_specific = _is_pokemon_specific_query(query)
    event_search_query = _should_try_generic_event_search(query)
    if pokemon_specific:
        pokemon_rows = get_pokemon_meta_candidates(query, limit=10)
        if event_search_query:
            _, _, events = route_mention_query(query)
            if pokemon_rows and events and is_openai_enabled():
                answer = answer_mixed_query_with_llm(query, _event_context(events), pokemon_rows)
                return answer[:MAX_DISCORD_MESSAGE_LENGTH], "mixed", len(events) + len(pokemon_rows)
        if pokemon_rows:
            if is_openai_enabled():
                return answer_pokemon_query_with_llm(query, pokemon_rows)[:MAX_DISCORD_MESSAGE_LENGTH], "pokemon", len(pokemon_rows)
            return answer_pokemon_query(query, pokemon_rows)[:MAX_DISCORD_MESSAGE_LENGTH], "pokemon", len(pokemon_rows)

    if _is_wiki_knowledge_query(query):
        chunks = search_wiki_chunks(query, limit=8)
        if chunks:
            if is_openai_enabled():
                return answer_wiki_query_with_llm(query, chunks)[:MAX_DISCORD_MESSAGE_LENGTH], "wiki", len(chunks)
            return format_wiki_search_fallback(query, chunks)[:MAX_DISCORD_MESSAGE_LENGTH], "wiki", len(chunks)

    if event_search_query:
        route, heading, events = route_mention_query(query)
        if not events:
            return _event_cache_miss_message(query), route, 0
        if is_openai_enabled():
            return answer_query_with_llm(query, events)[:MAX_DISCORD_MESSAGE_LENGTH], route, len(events)
        return build_event_response(heading, events, "No matching local event data found."), route, len(events)

    return _build_general_chat_response(query), "general_chat", 1


def build_contextual_mention_response(
    query: str,
    previous_bot_message_content: str | None = None,
    is_raid_update_running: bool = False,
    is_dynamax_update_running: bool = False,
) -> tuple[str, str, int]:
    """Return a mention/reply response while preserving previous bot answer context."""

    previous_route = infer_raid_attacker_route_from_bot_message(previous_bot_message_content or "")
    previous_dynamax_route = infer_dynamax_route_from_bot_message(previous_bot_message_content or "")
    previous_specific_subject = _infer_specific_raid_subject_from_bot_message(previous_bot_message_content or "")
    previous_named_event = _infer_named_event_from_bot_message(previous_bot_message_content or "")
    contextual_query = (
        f"Previous bot answer: {previous_bot_message_content}\nUser follow-up: {query}"
        if previous_bot_message_content
        else query
    )

    if previous_named_event and _is_event_detail_followup_query(query):
        return _format_named_event_detail_response(previous_named_event, query), "named_event_followup", 1

    if previous_specific_subject and _is_specific_raid_followup_without_subject(query):
        rewritten_query = _inject_subject_into_followup_query(query, previous_specific_subject)
        return build_mention_response(rewritten_query, is_raid_update_running, is_dynamax_update_running)

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
    pvp_cache_manager: Any | None = None,
    wiki_cache_manager: Any | None = None,
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

    @tree.command(name="pvp", description="Ask about cached PvPoke Great/Ultra/Master League rankings.")
    @app_commands.describe(query="Example: great, ultra top 20, skarmory, is azumarill good in great league")
    async def pvp_command(interaction: discord.Interaction, query: str = ""):
        logger.info("Slash command invoked: /pvp by user_id=%s", interaction.user.id)
        await interaction.response.defer(ephemeral=False, thinking=True)
        rows, route, league = get_pvp_rows_for_query(query, limit=_requested_pvp_row_count(query))
        logger.info("/pvp returned %d row(s) for query=%r via route=%s league=%s", len(rows), query, route, league)
        answer = await asyncio.to_thread(build_pvp_response, query, rows, route, league)
        await interaction.followup.send(answer, ephemeral=False, suppress_embeds=True)

    @tree.command(name="wiki", description="Ask about cached Pokémon GO Wiki/Fandom knowledge.")
    @app_commands.describe(query="Example: shiny pokemon, lucky pokemon, how does mega evolution work")
    async def wiki_command(interaction: discord.Interaction, query: str):
        logger.info("Slash command invoked: /wiki by user_id=%s", interaction.user.id)
        await interaction.response.defer(ephemeral=False, thinking=True)
        answer = await asyncio.to_thread(build_wiki_response, query)
        await interaction.followup.send(answer[:MAX_DISCORD_MESSAGE_LENGTH], ephemeral=False, suppress_embeds=True)

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

        if _is_pvp_query(query):
            pvp_rows, route, league = get_pvp_rows_for_query(query, limit=_requested_pvp_row_count(query))
            logger.info("/ask routed to cached PvPoke rankings and returned %d row(s) for query=%r via route=%s league=%s", len(pvp_rows), query, route, league)
            answer = await asyncio.to_thread(build_pvp_response, query, pvp_rows, route, league)
            await interaction.followup.send(answer[:MAX_DISCORD_MESSAGE_LENGTH], ephemeral=False, suppress_embeds=True)
            return

        if _is_current_raid_event_query(query):
            route, _heading, events = route_mention_query(query)
            logger.info("/ask routed to current raid event data via route=%s and returned %d row(s) for query=%r", route, len(events), query)
            if not events:
                await interaction.followup.send(_event_cache_miss_message(query), ephemeral=False, suppress_embeds=True)
                return
            if is_openai_enabled():
                answer = await asyncio.to_thread(answer_query_with_llm, query, events)
            else:
                answer = answer_query(query, events)
            await interaction.followup.send(answer[:MAX_DISCORD_MESSAGE_LENGTH], ephemeral=False, suppress_embeds=True)
            return

        forms_answer = await asyncio.to_thread(_build_pokemon_go_forms_response, query)
        if forms_answer:
            logger.info("/ask routed to cached Pokémon GO forms for query=%r", query)
            await interaction.followup.send(forms_answer[:MAX_DISCORD_MESSAGE_LENGTH], ephemeral=False, suppress_embeds=True)
            return

        if _should_try_named_event_lookup(query):
            named_events = await asyncio.to_thread(_lookup_named_event, query, 1)
            logger.info("/ask routed to named cached event lookup and returned %d row(s) for query=%r", len(named_events), query)
            if named_events:
                answer = _format_named_event_detail_response(named_events[0], query)
                await interaction.followup.send(answer[:MAX_DISCORD_MESSAGE_LENGTH], ephemeral=False, suppress_embeds=True)
                return

        if _is_pokemon_specific_query(query):
            pokemon_rows = get_pokemon_meta_candidates(query, limit=10)
            logger.info("/ask routed to Pokémon knowledge and returned %d row(s) for query=%r", len(pokemon_rows), query)
            if pokemon_rows:
                if is_openai_enabled():
                    answer = await asyncio.to_thread(answer_pokemon_query_with_llm, query, pokemon_rows)
                else:
                    answer = answer_pokemon_query(query, pokemon_rows)
                await interaction.followup.send(answer[:MAX_DISCORD_MESSAGE_LENGTH], ephemeral=False, suppress_embeds=True)
                return

        if _is_wiki_knowledge_query(query):
            logger.info("/ask routed to cached wiki knowledge for query=%r", query)
            chunks = search_wiki_chunks(query, limit=8)
            if chunks:
                answer = await asyncio.to_thread(build_wiki_response, query)
                await interaction.followup.send(answer[:MAX_DISCORD_MESSAGE_LENGTH], ephemeral=False, suppress_embeds=True)
                return

        if _should_try_generic_event_search(query):
            route, heading, events = route_mention_query(query)
            logger.info("/ask routed to cached event search via route=%s and returned %d row(s) for query=%r", route, len(events), query)
            if not events:
                await interaction.followup.send(_event_cache_miss_message(query), ephemeral=False, suppress_embeds=True)
                return
            if is_openai_enabled():
                answer = await asyncio.to_thread(answer_query_with_llm, query, events)
            else:
                answer = build_event_response(heading, events, "No matching local event data found.")
            await interaction.followup.send(answer[:MAX_DISCORD_MESSAGE_LENGTH], ephemeral=False, suppress_embeds=True)
            return

        logger.info("/ask fell back to general chat for query=%r", query)
        answer = await asyncio.to_thread(_build_general_chat_response, query)
        await interaction.followup.send(answer[:MAX_DISCORD_MESSAGE_LENGTH], ephemeral=False, suppress_embeds=True)

    @tree.command(name="chat", description="Chat normally with Charmander using general AI chat.")
    @app_commands.describe(message="What do you want to chat about?")
    async def chat_command(interaction: discord.Interaction, message: str):
        logger.info("Slash command invoked: /chat by user_id=%s", interaction.user.id)
        await interaction.response.defer(ephemeral=False, thinking=True)
        answer = await asyncio.to_thread(_build_general_chat_response, message)
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

        if _is_pvp_query(query):
            pvp_rows, route, league = get_pvp_rows_for_query(query, limit=_requested_pvp_row_count(query))
            logger.info("/pokemon routed to cached PvPoke rankings and returned %d row(s) for query=%r via route=%s league=%s", len(pvp_rows), query, route, league)
            answer = await asyncio.to_thread(build_pvp_response, query, pvp_rows, route, league)
            await interaction.followup.send(answer, ephemeral=False, suppress_embeds=True)
            return

        forms_answer = await asyncio.to_thread(_build_pokemon_go_forms_response, query)
        if forms_answer:
            logger.info("/pokemon routed to cached Pokémon GO forms for query=%r", query)
            await interaction.followup.send(forms_answer[:MAX_DISCORD_MESSAGE_LENGTH], ephemeral=False, suppress_embeds=True)
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

    @tree.command(name="updatepvp", description="Owner-only: force refresh cached PvPoke rankings.")
    async def update_pvp_command(interaction: discord.Interaction):
        logger.info("Slash command invoked: /updatepvp by user_id=%s", interaction.user.id)
        if owner_id is None or interaction.user.id != owner_id:
            await interaction.response.send_message("This owner-only command can only be run by the configured bot owner.", ephemeral=True, suppress_embeds=True)
            return
        if pvp_cache_manager is not None and pvp_cache_manager.is_update_running:
            await interaction.response.send_message("A PvP ranking update is already in progress. Please try again in a few minutes.", ephemeral=True, suppress_embeds=True)
            return
        if pvp_cache_manager is None:
            await interaction.response.send_message("PvP cache manager is not available.", ephemeral=True, suppress_embeds=True)
            return

        await interaction.response.defer(ephemeral=True, thinking=True)
        result = await pvp_cache_manager.force_refresh(reason="manual", wait_for_lock=False)
        league_rows = result.stats.get("league_rows", {}) if result.stats else {}
        scraper_stage = result.stats.get("scraper_stage", "unknown") if result.stats else "unknown"
        metadata_updated = result.stats.get("metadata_updated", False) if result.stats else result.updated
        errors = result.stats.get("errors", []) if result.stats else []
        if result.updated:
            await interaction.followup.send(
                f"PvP update complete. Upserted {result.count} row(s). League rows: {league_rows}. Scraper stage: {scraper_stage}. Metadata updated: {metadata_updated}. Errors: {errors}.",
                ephemeral=True,
                suppress_embeds=True,
            )
        elif result.reason == "zero-rows":
            await interaction.followup.send(
                f"PvP update finished but returned zero rows. Existing cached data was kept and metadata was not marked fresh. League rows: {league_rows}. Scraper stage: {scraper_stage}. Errors: {errors}.",
                ephemeral=True,
                suppress_embeds=True,
            )
        elif result.reason == "already-running":
            await interaction.followup.send("A PvP ranking update is already in progress. Please try again in a few minutes.", ephemeral=True, suppress_embeds=True)
        else:
            await interaction.followup.send(f"PvP update failed. Existing cached data was kept. Errors: {errors}.", ephemeral=True, suppress_embeds=True)

    @tree.command(name="updatewiki", description="Owner-only: force refresh cached Pokémon GO Wiki knowledge.")
    async def update_wiki_command(interaction: discord.Interaction):
        logger.info("Slash command invoked: /updatewiki by user_id=%s", interaction.user.id)
        if owner_id is None or interaction.user.id != owner_id:
            await interaction.response.send_message("This owner-only command can only be run by the configured bot owner.", ephemeral=True, suppress_embeds=True)
            return
        if wiki_cache_manager is not None and wiki_cache_manager.is_update_running:
            await interaction.response.send_message("A wiki knowledge update is already in progress. Please try again in a few minutes.", ephemeral=True, suppress_embeds=True)
            return
        if wiki_cache_manager is None:
            await interaction.response.send_message("Wiki cache manager is not available.", ephemeral=True, suppress_embeds=True)
            return

        await interaction.response.defer(ephemeral=True, thinking=True)
        result = await wiki_cache_manager.force_refresh(reason="manual", wait_for_lock=False)
        pages_fetched = result.stats.get("pages_fetched", 0) if result.stats else 0
        pages_failed = result.stats.get("pages_failed", 0) if result.stats else 0
        chunks_created = result.stats.get("chunks_created", result.count) if result.stats else result.count
        metadata_updated = result.stats.get("metadata_updated", False) if result.stats else result.updated
        errors = result.stats.get("errors", []) if result.stats else []
        if result.updated:
            await interaction.followup.send(
                f"Wiki update complete. Pages fetched: {pages_fetched}, pages failed: {pages_failed}, chunks created: {chunks_created}, metadata updated: {metadata_updated}. Errors: {errors}.",
                ephemeral=True,
                suppress_embeds=True,
            )
        elif result.reason == "zero-rows":
            await interaction.followup.send(
                f"Wiki update finished but returned zero chunks. Existing cached data was kept and metadata was not marked fresh. Pages fetched: {pages_fetched}, pages failed: {pages_failed}. Errors: {errors}.",
                ephemeral=True,
                suppress_embeds=True,
            )
        elif result.reason == "already-running":
            await interaction.followup.send("A wiki knowledge update is already in progress. Please try again in a few minutes.", ephemeral=True, suppress_embeds=True)
        else:
            await interaction.followup.send(f"Wiki update failed. Existing cached data was kept. Errors: {errors}.", ephemeral=True, suppress_embeds=True)

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
