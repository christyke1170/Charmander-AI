"""Static scraper for Pokémon GO Hub DB pages.

Current inspection notes (2026-06-11):
- robots.txt is reachable, allows crawling, and lists Pokémon sitemaps 0-11.
- curl received server-rendered HTML with useful text from the home, Pokédex,
  and Pokémon detail pages, but Python requests currently receives Cloudflare
  challenge HTML for those same pages in this environment.
- This scraper does not scrape live on user questions. It only runs during
  manual update commands/scripts.

TODO: If the site owners expose static HTML/API data or if you choose to use an
approved browser-based approach later, add Playwright support behind an explicit
manual update flag and keep the same local cache interface.
"""

from __future__ import annotations

import logging
import re
import time
from datetime import datetime, timezone
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from config import POKEMON_DB_SCRAPE_LIMIT, REQUEST_HEADERS, REQUEST_TIMEOUT_SECONDS
from scraper.normalize_events import clean_text


logger = logging.getLogger(__name__)

SOURCE_NAME = "Pokémon GO Hub DB"
BASE_URL = "https://db.pokemongohub.net/"
POKEDEX_URL = urljoin(BASE_URL, "tools/pokedex")
ROBOTS_URL = urljoin(BASE_URL, "robots.txt")
SITEMAP_URLS = [urljoin(BASE_URL, f"pokemon/sitemap/{index}.xml") for index in range(12)]
REQUEST_DELAY_SECONDS = 0.75


def _is_cloudflare_challenge(html: str) -> bool:
    lowered = html.lower()
    return "just a moment" in lowered and "challenges.cloudflare.com" in lowered


def _get(url: str) -> requests.Response | None:
    try:
        response = requests.get(url, headers=REQUEST_HEADERS, timeout=REQUEST_TIMEOUT_SECONDS)
    except requests.RequestException as exc:
        logger.warning("Failed to fetch Pokémon GO Hub URL %s: %s", url, exc)
        return None
    if response.status_code == 403 and _is_cloudflare_challenge(response.text):
        logger.warning("Pokémon GO Hub returned Cloudflare challenge for %s; skipping static scrape.", url)
    elif response.status_code >= 400:
        logger.warning("Pokémon GO Hub returned HTTP %s for %s", response.status_code, url)
    return response


def discover_pokemon_links(limit: int = POKEMON_DB_SCRAPE_LIMIT) -> tuple[list[str], dict[str, int]]:
    """Discover Pokémon detail links from static pages/sitemaps when available."""

    discovered: list[str] = []
    seen: set[str] = set()
    urls_to_try = [ROBOTS_URL, *SITEMAP_URLS, POKEDEX_URL, BASE_URL]
    pokemon_url_pattern = re.compile(r"https://db\.pokemongohub\.net/pokemon/(?!sitemap/)[A-Za-z0-9_./-]+|/pokemon/(?!sitemap/)[A-Za-z0-9_./-]+")
    stats = {"discovery_pages_checked": 0, "discovery_pages_blocked": 0, "pokemon_link_matches": 0}

    for url in urls_to_try:
        response = _get(url)
        if response is None or response.status_code >= 400 or _is_cloudflare_challenge(response.text):
            if response is not None and response.status_code == 403 and _is_cloudflare_challenge(response.text):
                stats["discovery_pages_blocked"] += 1
            continue
        stats["discovery_pages_checked"] += 1

        matches = pokemon_url_pattern.findall(response.text)
        soup = BeautifulSoup(response.text, "html.parser")
        matches.extend(a.get("href") or "" for a in soup.select("a[href*='/pokemon/']"))
        stats["pokemon_link_matches"] += len(matches)
        for match in matches:
            full_url = urljoin(BASE_URL, match.strip())
            parsed = urlparse(full_url)
            if parsed.netloc != "db.pokemongohub.net" or "/pokemon/" not in parsed.path:
                continue
            if "/pokemon/sitemap/" in parsed.path:
                continue
            normalized = full_url.rstrip("/")
            if normalized not in seen:
                seen.add(normalized)
                discovered.append(normalized)
            if len(discovered) >= limit:
                logger.info("Discovered %d Pokémon detail links", len(discovered))
                return discovered, stats

    logger.info("Discovered %d Pokémon detail links", len(discovered))
    return discovered, stats


def _extract_section_text(soup: BeautifulSoup, keywords: tuple[str, ...]) -> str | None:
    lowered_keywords = tuple(keyword.lower() for keyword in keywords)
    matches: list[str] = []
    for heading in soup.find_all(["h1", "h2", "h3", "h4", "h5"]):
        heading_text = heading.get_text(" ", strip=True)
        if not heading_text or not any(keyword in heading_text.lower() for keyword in lowered_keywords):
            continue
        section_parts = [heading_text]
        sibling = heading.find_next_sibling()
        steps = 0
        while sibling is not None and steps < 5:
            if getattr(sibling, "name", None) in {"h1", "h2", "h3", "h4", "h5"}:
                break
            text = clean_text(sibling.get_text(" ", strip=True)) if hasattr(sibling, "get_text") else None
            if text:
                section_parts.append(text)
            sibling = sibling.find_next_sibling()
            steps += 1
        matches.append(" ".join(section_parts))
    return clean_text(" ".join(matches))


def parse_pokemon_page(url: str, html: str) -> dict[str, str | None] | None:
    """Parse one static Pokémon detail page into a cache row."""

    if _is_cloudflare_challenge(html):
        return None

    soup = BeautifulSoup(html, "html.parser")
    raw_text = clean_text(soup.get_text(" ", strip=True))
    if not raw_text or "just a moment" in raw_text.lower():
        return None

    title = clean_text(soup.select_one("h1").get_text(" ", strip=True) if soup.select_one("h1") else None)
    if not title and soup.title:
        title = clean_text(soup.title.get_text(" ", strip=True).split("|", 1)[0])
    if not title:
        return None

    path_parts = [part for part in urlparse(url).path.split("/") if part]
    pokemon_id = path_parts[1] if len(path_parts) > 1 and path_parts[0] == "pokemon" else None
    form = path_parts[2] if len(path_parts) > 2 else None
    badge_text = " ".join(node.get_text(" ", strip=True) for node in soup.select("[class*='type'], [class*='Type']"))

    return {
        "source": SOURCE_NAME,
        "pokemon_id": pokemon_id,
        "name": title,
        "form": form,
        "types": clean_text(badge_text),
        "max_cp": _extract_section_text(soup, ("max cp", "combat power")),
        "best_moveset": _extract_section_text(soup, ("best moveset", "moveset", "moves")),
        "weaknesses": _extract_section_text(soup, ("weakness", "weaknesses")),
        "resistances": _extract_section_text(soup, ("resistance", "resistances")),
        "pve_summary": _extract_section_text(soup, ("pve", "raid", "attacker", "gym")),
        "pvp_summary": _extract_section_text(soup, ("pvp", "great league", "ultra league", "master league")),
        "raid_counter_summary": _extract_section_text(soup, ("counter", "counters")),
        "raw_text": raw_text[:5000],
        "url": url,
        "scraped_at": datetime.now(timezone.utc).isoformat(),
    }


def scrape_pokemon_knowledge(limit: int = POKEMON_DB_SCRAPE_LIMIT) -> tuple[list[dict[str, str | None]], dict[str, int]]:
    """Scrape Pokémon GO Hub static pages during a manual update."""

    links, discovery_stats = discover_pokemon_links(limit=limit)
    rows: list[dict[str, str | None]] = []
    pages_scraped = 0
    blocked_pages = 0
    parse_failures = 0

    for url in links[:limit]:
        time.sleep(REQUEST_DELAY_SECONDS)
        response = _get(url)
        if response is None:
            continue
        pages_scraped += 1
        if response.status_code == 403 and _is_cloudflare_challenge(response.text):
            blocked_pages += 1
            continue
        if response.status_code >= 400:
            continue
        row = parse_pokemon_page(url, response.text)
        if row:
            rows.append(row)
        else:
            parse_failures += 1

    stats = {
        **discovery_stats,
        "discovered_links": len(links),
        "pages_scraped": pages_scraped,
        "blocked_pages": blocked_pages,
        "parse_failures": parse_failures,
        "rows": len(rows),
    }
    logger.info("Pokémon GO Hub scrape stats: %s", stats)
    if not links:
        logger.warning(
            "No usable Pokémon detail links found. Checked %d discovery page(s); selector/regex matches=%d.",
            stats["discovery_pages_checked"],
            stats["pokemon_link_matches"],
        )
    elif not rows:
        logger.warning(
            "No usable Pokémon rows parsed from %d detail page(s). Section selectors may need updates.",
            pages_scraped,
        )
    return rows, stats