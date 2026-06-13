"""Scraper for Pokémon GO Hub DB Dynamax/Gigantamax best-per-type attackers.

This module is only used by explicit/manual/background cache update flows. Normal
Discord questions should read from the local SQLite cache and never scrape live.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests
from bs4 import BeautifulSoup, Tag

import config
from config import REQUEST_HEADERS, REQUEST_TIMEOUT_SECONDS
from scraper.raid_attacker_scraper import BLOCK_MARKERS, POKEMON_TYPES, is_blocked_page


logger = logging.getLogger(__name__)

SOURCE_NAME = "pokemongohub_dynamax_attackers_per_type"
DYNAMAX_ATTACKERS_URL = "https://db.pokemongohub.net/best/dynamax-attackers-per-type"
TABLE_HEADERS = ("#", "Name", "DPS", "TDO", "Score")
TYPE_SET = set(POKEMON_TYPES)
REAL_CONTENT_MARKERS = (
    "best dynamax attackers per type",
    "top 10 normal-type dynamax attackers",
    "top 10 fire-type dynamax attackers",
    "max move damage",
    "max phases",
)
BLOCK_TITLE_MARKERS = ("just a moment", "attention required")


def build_dynamax_type_url(type_name: str) -> str:
    return f"{DYNAMAX_ATTACKERS_URL}#{type_name.strip().lower()}"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _clean_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, Tag):
        text = value.get_text(" ", strip=True)
    else:
        text = str(value)
    return re.sub(r"\s+", " ", text).strip()


def _normalize_header(value: str) -> str:
    text = _clean_text(value).lower().replace("#", "ranknumber")
    return re.sub(r"[^a-z0-9]+", "", text)


def _rank_from_text(value: str) -> int | None:
    match = re.search(r"\d+", value or "")
    if not match:
        return None
    try:
        return int(match.group(0))
    except ValueError:
        return None


def _table_headers(table: Tag) -> list[str]:
    header_row = table.find("tr")
    if header_row is None:
        return []
    header_cells = header_row.find_all("th")
    if not header_cells:
        header_cells = header_row.find_all(["th", "td"])
    return [_clean_text(cell) for cell in header_cells]


def _header_index_map(headers: list[str]) -> dict[str, int]:
    normalized_to_index = {_normalize_header(header): index for index, header in enumerate(headers)}
    aliases = {
        "rank": ("ranknumber", "rank", ""),
        "pokemon_name": ("name", "pokemon", "pokemonname"),
        "fast_move": ("fastattack", "fastmove", "quickmove", "quickattack"),
        "charged_move": ("chargedattack", "chargedmove", "chargeattack", "chargemove", "maxmove"),
        "dps": ("dps",),
        "tdo": ("tdo",),
        "score": ("score", "maxmovedamage"),
        "max_phases": ("maxphases", "phases"),
    }
    index_map: dict[str, int] = {}
    for field, possible_headers in aliases.items():
        for possible_header in possible_headers:
            if possible_header in normalized_to_index:
                index_map[field] = normalized_to_index[possible_header]
                break
    return index_map


def _is_dynamax_table(headers: list[str]) -> bool:
    index_map = _header_index_map(headers)
    return all(field in index_map for field in ("rank", "pokemon_name")) and any(
        field in index_map for field in ("score", "dps", "tdo", "fast_move", "charged_move")
    )


def _row_cells(row: Tag) -> list[str]:
    cells = row.find_all("td")
    if not cells:
        cells = row.find_all(["th", "td"])
    return [_clean_text(cell) for cell in cells]


def _cell(cells: list[str], index_map: dict[str, int], field: str) -> str:
    index = index_map.get(field)
    if index is None or index >= len(cells):
        return ""
    return cells[index]


def _make_row(
    *,
    type_name: str,
    scraped_at: str,
    rank: int | None,
    pokemon_name: str,
    fast_move: str,
    charged_move: str,
    dps: str,
    tdo: str,
    score: str,
    max_phases: str = "",
    summary: str | None = None,
) -> dict[str, Any]:
    type_label = type_name.title()
    rank_label = f"#{rank}" if rank is not None else "unranked"
    if summary is None and score and max_phases:
        summary = f"Max Move Damage: {score}; Max Phases: {max_phases}"
    elif summary is None and score:
        summary = f"Max Move Damage: {score}"
    return {
        "source": SOURCE_NAME,
        "ranking_scope": f"type:{type_name}",
        "pokemon_name": pokemon_name,
        "form": "",
        "pokemon_type": type_name,
        "rank": rank,
        "fast_move": fast_move or None,
        "charged_move": charged_move or None,
        "score": score or None,
        "dps": dps or None,
        "tdo": tdo or None,
        "summary": summary or f"Rank {rank_label} {type_label}-type Dynamax/Gigantamax attacker on Pokémon GO Hub.",
        "url": build_dynamax_type_url(type_name),
        "scraped_at": scraped_at,
    }


def has_real_dynamax_content(text: str | None) -> bool:
    lowered = (text or "").lower()
    return any(marker in lowered for marker in REAL_CONTENT_MARKERS)


def is_dynamax_blocked_content(content: str | None, title: str | None = None) -> bool:
    """Return True only when content looks blocked and no real Dynamax ranking content is present."""

    combined = f"{title or ''}\n{content or ''}"
    if has_real_dynamax_content(combined):
        return False
    lowered_title = (title or "").lower()
    if any(marker in lowered_title for marker in BLOCK_TITLE_MARKERS):
        return True
    lowered_content = (content or "").lower()
    has_block_marker = any(marker in lowered_content for marker in BLOCK_MARKERS) or "security verification" in lowered_content
    return bool(has_block_marker)


def _normalize_anchor_value(value: Any) -> str:
    return re.sub(r"[^a-z]+", "", str(value or "").strip().lower())


def _heading_type_from_text(text: str) -> str | None:
    lowered = _clean_text(text).lower()
    if lowered in TYPE_SET:
        return lowered
    for type_name in POKEMON_TYPES:
        if lowered in {f"{type_name} type", f"{type_name}-type"}:
            return type_name
        if re.search(rf"\b{re.escape(type_name)}\b", lowered) and any(
            keyword in lowered for keyword in ("dynamax", "gigantamax", "attackers", "type")
        ):
            return type_name
    return None


def _section_type_for_tag(tag: Tag) -> str | None:
    """Return a Pokémon type when a tag is a type anchor/heading marker."""

    for attr in ("id", "name"):
        anchor_value = _normalize_anchor_value(tag.get(attr))
        if anchor_value in TYPE_SET:
            return anchor_value
    if tag.name in {"h1", "h2", "h3", "h4", "h5", "h6"}:
        return _heading_type_from_text(_clean_text(tag))
    return None


def _section_tables_by_type(soup: BeautifulSoup) -> dict[str, list[Tag]]:
    """Walk the single page once and assign tables to the nearest preceding type section."""

    tables_by_type: dict[str, list[Tag]] = {type_name: [] for type_name in POKEMON_TYPES}
    current_type: str | None = None
    root = soup.body or soup
    for element in root.descendants:
        if not isinstance(element, Tag):
            continue
        marker_type = _section_type_for_tag(element)
        if marker_type:
            current_type = marker_type
            continue
        if element.name == "table" and current_type in TYPE_SET:
            tables_by_type[current_type].append(element)
    return tables_by_type


def _parse_table_for_type(table: Tag, type_name: str, timestamp: str, limit_per_type: int) -> list[dict[str, Any]]:
    headers = _table_headers(table)
    if not _is_dynamax_table(headers):
        return []
    index_map = _header_index_map(headers)
    body = table.find("tbody")
    table_rows = body.find_all("tr") if isinstance(body, Tag) else table.find_all("tr")[1:]
    parsed_rows: list[dict[str, Any]] = []
    seen: set[tuple[str, int | None, str, str]] = set()
    for row in table_rows:
        if not isinstance(row, Tag):
            continue
        cells = _row_cells(row)
        pokemon_name = _cell(cells, index_map, "pokemon_name")
        if not pokemon_name:
            continue
        rank = _rank_from_text(_cell(cells, index_map, "rank"))
        fast_move = _cell(cells, index_map, "fast_move")
        charged_move = _cell(cells, index_map, "charged_move")
        dps = _cell(cells, index_map, "dps")
        tdo = _cell(cells, index_map, "tdo")
        score = _cell(cells, index_map, "score")
        max_phases = _cell(cells, index_map, "max_phases")
        dedupe_key = (pokemon_name, rank, fast_move, charged_move)
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        parsed_rows.append(
            _make_row(
                type_name=type_name,
                scraped_at=timestamp,
                rank=rank,
                pokemon_name=pokemon_name,
                fast_move=fast_move,
                charged_move=charged_move,
                dps=dps,
                tdo=tdo,
                score=score,
                max_phases=max_phases,
            )
        )
        if len(parsed_rows) >= limit_per_type:
            break
    return parsed_rows


def parse_dynamax_attackers_page(
    html: str,
    limit_per_type: int = 10,
    scraped_at: str | None = None,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Parse cached rows from the Dynamax attacker page, grouped by type anchors."""

    if not html or is_dynamax_blocked_content(html):
        return [], {type_name: 0 for type_name in POKEMON_TYPES}

    soup = BeautifulSoup(html, "html.parser")
    timestamp = scraped_at or _utc_now()
    rows: list[dict[str, Any]] = []
    type_rows: dict[str, int] = {type_name: 0 for type_name in POKEMON_TYPES}
    tables_by_type = _section_tables_by_type(soup)

    for type_name in POKEMON_TYPES:
        parsed_rows: list[dict[str, Any]] = []
        for table in tables_by_type.get(type_name, []):
            parsed_rows.extend(_parse_table_for_type(table, type_name, timestamp, limit_per_type - len(parsed_rows)))
            if parsed_rows:
                break
        rows.extend(parsed_rows)
        type_rows[type_name] = len(parsed_rows)

    return rows, type_rows


def _non_empty_lines(text: str) -> list[str]:
    return [line.strip() for line in (text or "").splitlines() if line.strip()]


def _text_sections_by_type(lines: list[str]) -> dict[str, list[str]]:
    sections: dict[str, list[str]] = {type_name: [] for type_name in POKEMON_TYPES}
    current_type: str | None = None
    heading_pattern = re.compile(r"^Top\s+10\s+([A-Za-z]+)-type\s+Dynamax\s+Attackers\b", re.IGNORECASE)
    for line in lines:
        heading_match = heading_pattern.search(line)
        if heading_match:
            candidate_type = heading_match.group(1).strip().lower()
            current_type = candidate_type if candidate_type in TYPE_SET else None
            continue
        if current_type in TYPE_SET:
            sections[current_type].append(line)
    return sections


def _parse_text_section_rows(section_lines: list[str], type_name: str, timestamp: str, limit_per_type: int) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen: set[tuple[int | None, str, str, str]] = set()
    rank_pattern = re.compile(r"^(\d{1,2})\.?$")
    metric_pattern = re.compile(r"^(\d+(?:\.\d+)?)\s+(\d{1,3})$")
    header_tokens = {"#", "name", "fast attack", "charged attack", "max move damage", "max phases"}
    lines = [line for line in section_lines if line.strip().lower() not in header_tokens]
    index = 0
    while index < len(lines) and len(rows) < limit_per_type:
        rank_match = rank_pattern.match(lines[index])
        if not rank_match:
            index += 1
            continue
        try:
            rank = int(rank_match.group(1))
        except ValueError:
            index += 1
            continue
        if index + 3 >= len(lines):
            break
        pokemon_name = lines[index + 1]
        fast_move = lines[index + 2]
        charged_move = lines[index + 3]
        metric_index = index + 4
        max_move_damage = ""
        max_phases = ""
        while metric_index < min(index + 8, len(lines)):
            if rank_pattern.match(lines[metric_index]):
                break
            metric_match = metric_pattern.match(lines[metric_index])
            if metric_match:
                max_move_damage = metric_match.group(1)
                max_phases = metric_match.group(2)
                break
            metric_index += 1
        if not pokemon_name or not fast_move or not charged_move or not max_move_damage:
            index += 1
            continue
        dedupe_key = (rank, pokemon_name, fast_move, charged_move)
        if dedupe_key not in seen:
            seen.add(dedupe_key)
            rows.append(
                _make_row(
                    type_name=type_name,
                    scraped_at=timestamp,
                    rank=rank,
                    pokemon_name=pokemon_name,
                    fast_move=fast_move,
                    charged_move=charged_move,
                    dps="",
                    tdo="",
                    score=max_move_damage,
                    summary=(
                        f"Max Move Damage: {max_move_damage}; Max Phases: {max_phases}"
                        if max_phases
                        else f"Max Move Damage: {max_move_damage}"
                    ),
                )
            )
        index = max(metric_index + 1, index + 4)
    return rows


def parse_dynamax_attackers_text(
    text: str,
    timestamp: str | None = None,
    limit_per_type: int = 10,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Parse rendered Dynamax card/list text grouped under Top 10 <Type>-type headings."""

    if not text or is_dynamax_blocked_content(text):
        return [], {type_name: 0 for type_name in POKEMON_TYPES}
    scraped_at = timestamp or _utc_now()
    rows: list[dict[str, Any]] = []
    type_rows: dict[str, int] = {type_name: 0 for type_name in POKEMON_TYPES}
    sections = _text_sections_by_type(_non_empty_lines(text))
    for type_name in POKEMON_TYPES:
        parsed_rows = _parse_text_section_rows(sections.get(type_name, []), type_name, scraped_at, limit_per_type)
        rows.extend(parsed_rows)
        type_rows[type_name] = len(parsed_rows)
    return rows, type_rows


def _visible_text_from_html(html: str) -> str:
    if not html:
        return ""
    return BeautifulSoup(html, "html.parser").get_text("\n", strip=True)


def _score_row_count(rows: list[dict[str, Any]]) -> int:
    return sum(1 for row in rows if _clean_text(row.get("score")))


def parse_rendered_dynamax_content(
    *,
    html: str,
    visible_text: str,
    title: str = "",
    limit_per_type: int = 10,
    scraped_at: str | None = None,
    return_metadata: bool = False,
) -> tuple[list[dict[str, Any]], dict[str, int], str, bool] | tuple[list[dict[str, Any]], dict[str, int], dict[str, Any], bool]:
    """Parse browser-rendered Dynamax content via DOM first, then visible text fallback."""

    combined_content = f"{visible_text}\n{html}"
    if is_dynamax_blocked_content(combined_content, title=title):
        type_rows = {type_name: 0 for type_name in POKEMON_TYPES}
        metadata = {
            "parser_used": "blocked",
            "dom_rows": 0,
            "text_rows": 0,
            "dom_score_rows": 0,
            "text_score_rows": 0,
        }
        if return_metadata:
            return [], type_rows, metadata, True
        return [], type_rows, "blocked", True
    timestamp = scraped_at or _utc_now()
    dom_rows, dom_type_rows = parse_dynamax_attackers_page(html, limit_per_type=limit_per_type, scraped_at=timestamp)
    text_rows, text_type_rows = parse_dynamax_attackers_text(visible_text or _visible_text_from_html(html), timestamp=timestamp, limit_per_type=limit_per_type)
    dom_score_rows = _score_row_count(dom_rows)
    text_score_rows = _score_row_count(text_rows)
    dom_score_coverage = (dom_score_rows / len(dom_rows)) if dom_rows else 0.0
    dom_score_coverage_poor = bool(dom_rows) and dom_score_coverage < 0.5

    if dom_rows and text_rows and dom_score_coverage_poor and text_score_rows > dom_score_rows:
        rows = text_rows
        type_rows = text_type_rows
        parser_used = "text_preferred_score_coverage"
    elif dom_rows:
        rows = dom_rows
        type_rows = dom_type_rows
        parser_used = "dom"
    elif text_rows:
        rows = text_rows
        type_rows = text_type_rows
        parser_used = "text"
    else:
        rows = []
        type_rows = text_type_rows
        parser_used = "none"

    metadata = {
        "parser_used": parser_used,
        "dom_rows": len(dom_rows),
        "text_rows": len(text_rows),
        "dom_score_rows": dom_score_rows,
        "text_score_rows": text_score_rows,
    }
    if return_metadata:
        return rows, type_rows, metadata, False
    return rows, type_rows, parser_used, False


def fetch_dynamax_attackers_page() -> tuple[str, int | None, str | None]:
    try:
        response = requests.get(DYNAMAX_ATTACKERS_URL, headers=REQUEST_HEADERS, timeout=REQUEST_TIMEOUT_SECONDS)
    except requests.RequestException as exc:
        logger.warning("Failed to fetch Pokémon GO Hub Dynamax attackers URL %s: %s", DYNAMAX_ATTACKERS_URL, exc)
        return DYNAMAX_ATTACKERS_URL, None, None
    return DYNAMAX_ATTACKERS_URL, response.status_code, response.text


def _empty_stats(stage: str = "requests") -> dict[str, Any]:
    return {
        "pages_checked": 0,
        "pages_blocked": 0,
        "pages_parsed": 0,
        "rows_parsed": 0,
        "type_rows": {},
        "parse_failures": 0,
        "blocked": False,
        "error": None,
        "scraper_stage": stage,
        "status_code": None,
    }


def _is_blocked_text(text: str | None) -> bool:
    return is_dynamax_blocked_content(text)


def _dynamax_profile_path() -> Path | None:
    profile_dir = (config.DYNAMAX_BROWSER_PROFILE_DIR or "").strip()
    if not profile_dir:
        return None
    user_data_dir = Path(profile_dir)
    if not user_data_dir.is_absolute():
        user_data_dir = config.BASE_DIR / user_data_dir
    return user_data_dir


def _launch_context(playwright: Any, *, force_headed: bool = False) -> tuple[Any, Any | None, Path | None]:
    launch_options = {
        "headless": False if force_headed else bool(config.DYNAMAX_BROWSER_HEADLESS),
        "slow_mo": int(config.DYNAMAX_BROWSER_SLOW_MO_MS or 0),
    }
    user_data_dir = _dynamax_profile_path()
    if user_data_dir is not None:
        user_data_dir.mkdir(parents=True, exist_ok=True)
        context = playwright.chromium.launch_persistent_context(str(user_data_dir), **launch_options)
        return context, None, user_data_dir
    browser = playwright.chromium.launch(**launch_options)
    context = browser.new_context()
    return context, browser, None


def _scrape_with_browser(limit_per_type: int, pause_for_cloudflare: bool = False) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    stats = _empty_stats("browser")
    stats["browser_headless"] = False if pause_for_cloudflare else bool(config.DYNAMAX_BROWSER_HEADLESS)
    stats["browser_profile_dir"] = str(_dynamax_profile_path() or "")
    stats["pause_browser"] = bool(pause_for_cloudflare)
    if not config.DYNAMAX_USE_BROWSER_SCRAPER:
        stats["error"] = "browser-disabled"
        return [], stats
    try:
        from playwright.sync_api import Error as PlaywrightError
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        stats["error"] = f"playwright-not-installed: {exc}"
        return [], stats

    timeout_ms = int(config.DYNAMAX_BROWSER_TIMEOUT_SECONDS or 60) * 1000
    try:
        with sync_playwright() as playwright:
            context, browser, profile_path = _launch_context(playwright, force_headed=pause_for_cloudflare)
            stats["browser_profile_dir"] = str(profile_path or "")
            try:
                context.set_default_timeout(timeout_ms)
                context.set_default_navigation_timeout(timeout_ms)
                page = context.new_page()
                try:
                    stats["pages_checked"] = 1
                    page.goto(DYNAMAX_ATTACKERS_URL, wait_until="domcontentloaded", timeout=timeout_ms)
                    try:
                        page.wait_for_load_state("networkidle", timeout=min(timeout_ms, 3_000))
                    except PlaywrightTimeoutError:
                        pass
                    if pause_for_cloudflare:
                        print("Solve Cloudflare in the opened browser, then press Enter here to continue.")
                        try:
                            input()
                        except EOFError:
                            logger.warning("Dynamax pause requested, but stdin is not interactive; continuing without manual pause.")
                        try:
                            page.wait_for_load_state("networkidle", timeout=min(timeout_ms, 5_000))
                        except PlaywrightTimeoutError:
                            pass
                    html = page.content()
                    try:
                        visible_text = page.locator("body").inner_text(timeout=min(timeout_ms, 5_000))
                    except PlaywrightTimeoutError:
                        visible_text = _visible_text_from_html(html)
                    title = page.title()
                    stats["real_content_detected"] = has_real_dynamax_content(f"{title}\n{visible_text}\n{html}")
                    rows, type_rows, parse_metadata, blocked = parse_rendered_dynamax_content(
                        html=html,
                        visible_text=visible_text,
                        title=title,
                        limit_per_type=limit_per_type,
                        scraped_at=_utc_now(),
                        return_metadata=True,
                    )
                    stats.update(parse_metadata)
                    parser_used = str(parse_metadata.get("parser_used") or "")
                    if parser_used in {"text", "text_preferred_score_coverage"}:
                        stats["scraper_stage"] = "browser_text"
                    if blocked:
                        stats["pages_blocked"] = 1
                        stats["blocked"] = True
                        return [], stats
                    stats["type_rows"] = type_rows
                    stats["rows_parsed"] = len(rows)
                    if rows:
                        stats["pages_parsed"] = 1
                    else:
                        stats["parse_failures"] = 1
                    return rows, stats
                finally:
                    page.close()
            finally:
                context.close()
                if browser is not None:
                    browser.close()
    except PlaywrightError as exc:
        stats["error"] = str(exc)
        return [], stats


def scrape_dynamax_attackers_per_type(limit_per_type: int = 10, pause_browser: bool = False) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Scrape top Dynamax/Gigantamax attackers per type from Pokémon GO Hub."""

    url, status_code, html = fetch_dynamax_attackers_page()
    stats = _empty_stats("requests")
    stats["pages_checked"] = 1
    stats["status_code"] = status_code

    if html is None:
        stats["parse_failures"] = 1
        stats["error"] = "request-failed"
    elif is_dynamax_blocked_content(html) or (status_code in {403, 429, 503} and not has_real_dynamax_content(html)):
        stats["pages_blocked"] = 1
        stats["blocked"] = True
    elif status_code is not None and status_code >= 400 and not has_real_dynamax_content(html):
        stats["parse_failures"] = 1
        stats["error"] = f"http-{status_code}"
    else:
        rows, type_rows, parse_metadata, blocked = parse_rendered_dynamax_content(
            html=html,
            visible_text=_visible_text_from_html(html),
            limit_per_type=limit_per_type,
            scraped_at=_utc_now(),
            return_metadata=True,
        )
        stats.update(parse_metadata)
        parser_used = str(parse_metadata.get("parser_used") or "")
        if parser_used in {"text", "text_preferred_score_coverage"}:
            stats["scraper_stage"] = "requests_text"
        stats["type_rows"] = type_rows
        stats["rows_parsed"] = len(rows)
        if rows:
            stats["pages_parsed"] = 1
            logger.info("Pokémon GO Hub Dynamax attacker scrape stats: %s", stats)
            return rows, stats
        stats["parse_failures"] = 1

    browser_rows, browser_stats = _scrape_with_browser(limit_per_type, pause_for_cloudflare=pause_browser)
    if browser_rows:
        logger.info("Pokémon GO Hub Dynamax browser scrape stats: %s", browser_stats)
        return browser_rows, browser_stats

    if browser_stats.get("blocked"):
        stats["blocked"] = True
        stats["pages_blocked"] = max(int(stats.get("pages_blocked", 0)), 1)
    if browser_stats.get("error") and not stats.get("error"):
        stats["error"] = browser_stats.get("error")
    stats["browser_stats"] = browser_stats
    logger.warning("Pokémon GO Hub Dynamax scrape returned zero rows. Stats: %s", stats)
    return [], stats
