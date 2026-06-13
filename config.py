"""Application configuration loaded from environment variables."""

from __future__ import annotations

import logging
import os
from pathlib import Path

from dotenv import load_dotenv


BASE_DIR = Path(__file__).resolve().parent
DATABASE_PATH = BASE_DIR / "database" / "pogo_events.sqlite"

# Load .env from the project root when present.
load_dotenv(BASE_DIR / ".env")

DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN", "").strip()
DISCORD_OWNER_ID_RAW = os.getenv("DISCORD_OWNER_ID", "").strip()
DISCORD_OWNER_ID = int(DISCORD_OWNER_ID_RAW) if DISCORD_OWNER_ID_RAW.isdigit() else None

OPENAI_API_KEY = (os.getenv("OPENAI_API_KEY") or os.getenv("LLM_API_KEY") or "").strip()
OPENAI_MODEL = (os.getenv("OPENAI_MODEL") or os.getenv("LLM_MODEL") or "gpt-4o-mini").strip() or "gpt-4o-mini"
POKEMON_DB_SCRAPE_LIMIT_RAW = os.getenv("POKEMON_DB_SCRAPE_LIMIT", "50").strip()
POKEMON_DB_SCRAPE_LIMIT = int(POKEMON_DB_SCRAPE_LIMIT_RAW) if POKEMON_DB_SCRAPE_LIMIT_RAW.isdigit() else 50
RAID_ATTACKER_CACHE_MAX_AGE_DAYS_RAW = os.getenv("RAID_ATTACKER_CACHE_MAX_AGE_DAYS", "30").strip()
RAID_ATTACKER_CACHE_MAX_AGE_DAYS = (
    int(RAID_ATTACKER_CACHE_MAX_AGE_DAYS_RAW) if RAID_ATTACKER_CACHE_MAX_AGE_DAYS_RAW.isdigit() else 30
)
RAID_ATTACKER_AUTO_UPDATE = os.getenv("RAID_ATTACKER_AUTO_UPDATE", "true").strip().lower() in {"1", "true", "yes", "on"}
RAID_ATTACKER_AUTO_UPDATE_CHECK_HOURS_RAW = os.getenv("RAID_ATTACKER_AUTO_UPDATE_CHECK_HOURS", "24").strip()
RAID_ATTACKER_AUTO_UPDATE_CHECK_HOURS = (
    int(RAID_ATTACKER_AUTO_UPDATE_CHECK_HOURS_RAW) if RAID_ATTACKER_AUTO_UPDATE_CHECK_HOURS_RAW.isdigit() else 24
)
DYNAMAX_CACHE_MAX_AGE_DAYS_RAW = os.getenv("DYNAMAX_CACHE_MAX_AGE_DAYS", "30").strip()
DYNAMAX_CACHE_MAX_AGE_DAYS = int(DYNAMAX_CACHE_MAX_AGE_DAYS_RAW) if DYNAMAX_CACHE_MAX_AGE_DAYS_RAW.isdigit() else 30
DYNAMAX_AUTO_UPDATE = os.getenv("DYNAMAX_AUTO_UPDATE", "true").strip().lower() in {"1", "true", "yes", "on"}
DYNAMAX_AUTO_UPDATE_CHECK_HOURS_RAW = os.getenv("DYNAMAX_AUTO_UPDATE_CHECK_HOURS", "24").strip()
DYNAMAX_AUTO_UPDATE_CHECK_HOURS = (
    int(DYNAMAX_AUTO_UPDATE_CHECK_HOURS_RAW) if DYNAMAX_AUTO_UPDATE_CHECK_HOURS_RAW.isdigit() else 24
)
EGG_CACHE_MAX_AGE_DAYS_RAW = os.getenv("EGG_CACHE_MAX_AGE_DAYS", "30").strip()
EGG_CACHE_MAX_AGE_DAYS = int(EGG_CACHE_MAX_AGE_DAYS_RAW) if EGG_CACHE_MAX_AGE_DAYS_RAW.isdigit() else 30
EGG_AUTO_UPDATE = os.getenv("EGG_AUTO_UPDATE", "true").strip().lower() in {"1", "true", "yes", "on"}
EGG_AUTO_UPDATE_CHECK_HOURS_RAW = os.getenv("EGG_AUTO_UPDATE_CHECK_HOURS", "24").strip()
EGG_AUTO_UPDATE_CHECK_HOURS = int(EGG_AUTO_UPDATE_CHECK_HOURS_RAW) if EGG_AUTO_UPDATE_CHECK_HOURS_RAW.isdigit() else 24
PVP_CACHE_MAX_AGE_DAYS_RAW = os.getenv("PVP_CACHE_MAX_AGE_DAYS", "30").strip()
PVP_CACHE_MAX_AGE_DAYS = int(PVP_CACHE_MAX_AGE_DAYS_RAW) if PVP_CACHE_MAX_AGE_DAYS_RAW.isdigit() else 30
PVP_AUTO_UPDATE = os.getenv("PVP_AUTO_UPDATE", "true").strip().lower() in {"1", "true", "yes", "on"}
PVP_AUTO_UPDATE_CHECK_HOURS_RAW = os.getenv("PVP_AUTO_UPDATE_CHECK_HOURS", "24").strip()
PVP_AUTO_UPDATE_CHECK_HOURS = int(PVP_AUTO_UPDATE_CHECK_HOURS_RAW) if PVP_AUTO_UPDATE_CHECK_HOURS_RAW.isdigit() else 24
WIKI_CACHE_MAX_AGE_DAYS_RAW = os.getenv("WIKI_CACHE_MAX_AGE_DAYS", "30").strip()
WIKI_CACHE_MAX_AGE_DAYS = int(WIKI_CACHE_MAX_AGE_DAYS_RAW) if WIKI_CACHE_MAX_AGE_DAYS_RAW.isdigit() else 30
WIKI_AUTO_UPDATE = os.getenv("WIKI_AUTO_UPDATE", "true").strip().lower() in {"1", "true", "yes", "on"}
WIKI_AUTO_UPDATE_CHECK_HOURS_RAW = os.getenv("WIKI_AUTO_UPDATE_CHECK_HOURS", "24").strip()
WIKI_AUTO_UPDATE_CHECK_HOURS = int(WIKI_AUTO_UPDATE_CHECK_HOURS_RAW) if WIKI_AUTO_UPDATE_CHECK_HOURS_RAW.isdigit() else 24
WEB_SEARCH_PROVIDER = os.getenv("WEB_SEARCH_PROVIDER", "none").strip().lower() or "none"
GOOGLE_CSE_API_KEY = os.getenv("GOOGLE_CSE_API_KEY", "").strip()
GOOGLE_CSE_ENGINE_ID = os.getenv("GOOGLE_CSE_ENGINE_ID", "").strip()


def _env_bool(name: str, default: bool) -> bool:
    raw_value = os.getenv(name)
    if raw_value is None:
        return default
    return raw_value.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    raw_value = os.getenv(name, str(default)).strip()
    try:
        value = int(raw_value)
    except ValueError:
        return default
    return value if value >= 0 else default


RAID_ATTACKER_USE_BROWSER_SCRAPER = _env_bool("RAID_ATTACKER_USE_BROWSER_SCRAPER", True)
RAID_ATTACKER_BROWSER_HEADLESS = _env_bool("RAID_ATTACKER_BROWSER_HEADLESS", True)
RAID_ATTACKER_BROWSER_TIMEOUT_SECONDS = _env_int("RAID_ATTACKER_BROWSER_TIMEOUT_SECONDS", 45)
RAID_ATTACKER_BROWSER_SLOW_MO_MS = _env_int("RAID_ATTACKER_BROWSER_SLOW_MO_MS", 0)
RAID_ATTACKER_BROWSER_PROFILE_DIR = os.getenv("RAID_ATTACKER_BROWSER_PROFILE_DIR", "").strip()

DYNAMAX_USE_BROWSER_SCRAPER = _env_bool("DYNAMAX_USE_BROWSER_SCRAPER", True)
DYNAMAX_BROWSER_HEADLESS = _env_bool("DYNAMAX_BROWSER_HEADLESS", False)
DYNAMAX_BROWSER_TIMEOUT_SECONDS = _env_int("DYNAMAX_BROWSER_TIMEOUT_SECONDS", 60)
DYNAMAX_BROWSER_SLOW_MO_MS = _env_int("DYNAMAX_BROWSER_SLOW_MO_MS", 50)
DYNAMAX_BROWSER_PROFILE_DIR = os.getenv("DYNAMAX_BROWSER_PROFILE_DIR", "data/playwright-dynamax-profile").strip()

REQUEST_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/125.0 Safari/537.36 PokemonGoDiscordBot/0.1"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}
REQUEST_TIMEOUT_SECONDS = 15


def configure_logging() -> None:
    """Configure a simple application-wide logger."""

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )
