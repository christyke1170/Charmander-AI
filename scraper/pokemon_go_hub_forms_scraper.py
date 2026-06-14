"""Manual-only scraper for Pokémon GO Hub Pokémon/form pages.

Normal Discord questions must never scrape these pages live. This module is only
used by explicit/local cache update scripts.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup, Tag

import config
from config import REQUEST_HEADERS, REQUEST_TIMEOUT_SECONDS
from scraper.raid_attacker_scraper import BLOCK_MARKERS


logger = logging.getLogger(__name__)

BASE_URL = "https://db.pokemongohub.net/pokemon"
SOURCE_NAME = "pokemongohub_pokemon_db"
DEFAULT_MAX_DEX = 1025
CANDIDATE_SUFFIXES = (
    "",
    "Shadow",
    "Mega",
    "Mega_X",
    "Mega_Y",
    "Primal",
    "Alolan",
    "Galarian",
    "Hisuian",
    "Paldean",
    "Origin",
    "Altered",
    "Therian",
    "Incarnate",
    "Attack",
    "Defense",
    "Speed",
    "Crowned_Sword",
    "Crowned_Shield",
)
FORMS_BLOCK_MARKERS = BLOCK_MARKERS + (
    "cf-browser-verification",
    "challenge-platform",
    "performing security verification",
)
BLOCK_TITLE_MARKERS = ("just a moment", "attention required")
REAL_CONTENT_MARKERS = (
    "best moveset",
    "max cp",
    "charged attacks",
    "fast attacks",
    "pokemonpagemoves_moveslist",
    "pokemonpagecompactnotablecps",
    "pokemonstatbars_gaugeamount",
    "pokemonpagerenderers_officialimagetyping",
)
VALID_TYPE_NAMES = {
    "Normal",
    "Fire",
    "Water",
    "Electric",
    "Grass",
    "Ice",
    "Fighting",
    "Poison",
    "Ground",
    "Flying",
    "Psychic",
    "Bug",
    "Rock",
    "Ghost",
    "Dragon",
    "Dark",
    "Steel",
    "Fairy",
}
INVALID_TITLE_MARKERS = (
    "just a moment",
    "attention required",
    "enable javascript and cookies to continue",
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def generate_pokemon_go_hub_form_candidates(max_dex: int = DEFAULT_MAX_DEX) -> list[dict]:
    """Generate Pokémon GO Hub Pokémon/form page candidates without assuming they exist."""

    candidates: list[dict] = []
    upper = max(int(max_dex), 0)
    for dex_number in range(1, upper + 1):
        for suffix in CANDIDATE_SUFFIXES:
            slug = str(dex_number) if not suffix else f"{dex_number}-{suffix}"
            candidates.append(
                {
                    "dex_number": dex_number,
                    "suffix": suffix or None,
                    "form": suffix or None,
                    "url": f"{BASE_URL}/{slug}",
                }
            )
    return candidates


def generate_pokemon_go_hub_candidates(max_dex: int = DEFAULT_MAX_DEX, include_forms: bool = False) -> list[dict]:
    """Generate Pokémon GO Hub candidates in Pokédex order.

    By default this returns only the base Pokédex entry for each dex number.
    Pass `include_forms=True` to restore broad suffix/form candidate generation.
    """

    if include_forms:
        return generate_pokemon_go_hub_form_candidates(max_dex=max_dex)

    candidates: list[dict] = []
    upper = max(int(max_dex), 0)
    for dex_number in range(1, upper + 1):
        candidates.append(
            {
                "dex_number": dex_number,
                "suffix": None,
                "form": None,
                "url": f"{BASE_URL}/{dex_number}",
            }
        )
    return candidates


def _clean_text(value: str | None) -> str:
    if not value:
        return ""
    return re.sub(r"\s+", " ", value).strip()


def _visible_text_from_html(html: str) -> str:
    if not html:
        return ""
    return BeautifulSoup(html, "html.parser").get_text("\n", strip=True)


def _extract_title(soup: BeautifulSoup) -> str:
    h1 = soup.select_one("h1")
    if h1:
        return _clean_text(h1.get_text(" ", strip=True))
    if soup.title:
        return _clean_text(soup.title.get_text(" ", strip=True).split("|", 1)[0])
    return ""


def _title_from_html(html: str) -> str:
    if not html:
        return ""
    soup = BeautifulSoup(html, "html.parser")
    return _extract_title(soup)


def _looks_like_real_pokemon_page(html: str) -> bool:
    if not html:
        return False
    soup = BeautifulSoup(html, "html.parser")
    has_title = bool(_extract_title(soup))
    has_types = bool(_extract_types(soup))
    has_stats = any(_stat_value_by_label(soup, label) is not None for label in ("ATK", "DEF", "HP"))
    fast_moves, elite_fast_moves = _extract_moves_for_heading(soup, "Fast Attacks")
    charged_moves, elite_charged_moves = _extract_moves_for_heading(soup, "Charged Attacks")
    has_moves = bool(fast_moves or elite_fast_moves or charged_moves or elite_charged_moves)
    has_max_cp = _extract_max_cp(soup) is not None
    signals = sum(bool(value) for value in (has_types, has_stats, has_moves, has_max_cp))
    return has_title and signals >= 2


def _slug_suffix_from_url(url: str) -> str | None:
    path = urlparse(url).path.rstrip("/")
    slug = path.split("/")[-1] if path else ""
    if "-" not in slug:
        return None
    return slug.split("-", 1)[1] or None


def _form_label(form_hint: str | None, title: str = "") -> str | None:
    raw = _clean_text(form_hint.replace("_", " ")) if form_hint else ""
    lowered_title = title.lower()
    if raw:
        return raw
    if "gigantamax" in lowered_title:
        return "Gigantamax"
    if "shadow" in lowered_title:
        return "Shadow"
    if lowered_title.startswith("mega "):
        return "Mega"
    return None


def _split_pokemon_name_and_form(title: str, form_hint: str | None) -> tuple[str, str | None]:
    cleaned_title = _clean_text(title)
    if not cleaned_title:
        return "", None
    form = _form_label(form_hint, title=cleaned_title)
    lowered_title = cleaned_title.lower()
    if not form:
        return cleaned_title, None

    form_lower = form.lower()
    if form_lower == "shadow":
        if lowered_title.startswith("shadow "):
            return _clean_text(cleaned_title[7:]), form
        if lowered_title.endswith(" shadow"):
            return _clean_text(cleaned_title[:-7]), form
    if form_lower == "mega":
        if lowered_title.startswith("mega "):
            return _clean_text(cleaned_title[5:]), form
    if form_lower == "mega x":
        if lowered_title.startswith("mega ") and lowered_title.endswith(" x"):
            return _clean_text(cleaned_title[5:-2]), form
    if form_lower == "mega y":
        if lowered_title.startswith("mega ") and lowered_title.endswith(" y"):
            return _clean_text(cleaned_title[5:-2]), form
    if lowered_title.startswith(f"{form_lower} "):
        return _clean_text(cleaned_title[len(form) + 1 :]), form
    if lowered_title.endswith(f" {form_lower}"):
        return _clean_text(cleaned_title[: -(len(form) + 1)]), form
    return cleaned_title, form


def _extract_types(soup: BeautifulSoup) -> list[str]:
    container = soup.select_one(".PokemonPageRenderers_officialImageTyping__BZQBp")
    if not container:
        return []
    types: list[str] = []
    for image in container.select("img[title]"):
        title = _clean_text(image.get("title"))
        if not title:
            continue
        if title.lower() in {"rain", "windy", "snow", "fog", "sunny", "cloudy", "partly cloudy", "extreme"}:
            continue
        if title not in VALID_TYPE_NAMES:
            continue
        if title not in types:
            types.append(title)
        if len(types) >= 2:
            break
    return types


def _stat_value_by_label(soup: BeautifulSoup, label: str) -> int | None:
    label = label.upper()
    for stat in soup.select(".PokemonStatBars_gaugeAmount__JfJh6"):
        stat_type = _clean_text(
            stat.select_one(".PokemonStatBars_statType__htfki").get_text(" ", strip=True)
            if stat.select_one(".PokemonStatBars_statType__htfki")
            else ""
        )
        if stat_type != label:
            continue
        amount_text = _clean_text(
            stat.select_one(".PokemonStatBars_amount__Q_aF8").get_text(" ", strip=True)
            if stat.select_one(".PokemonStatBars_amount__Q_aF8")
            else ""
        )
        match = re.search(r"\d+", amount_text)
        return int(match.group(0)) if match else None
    return None


def _extract_max_cp(soup: BeautifulSoup) -> int | None:
    for row in soup.select(".PokemonPageCompactNotableCPs_row__kbWZA"):
        row_text = _clean_text(row.get_text(" ", strip=True))
        if "Lvl 50" not in row_text or "Max CP" not in row_text:
            continue
        match = re.search(r"(\d+)\s*CP", row_text, flags=re.IGNORECASE)
        if match:
            return int(match.group(1))
    overview = _clean_text(soup.get_text(" ", strip=True))
    match = re.search(r"maximum Combat Power stat is (\d+) CP", overview, flags=re.IGNORECASE)
    return int(match.group(1)) if match else None


def _extract_moves_for_heading(soup: BeautifulSoup, heading_text: str) -> tuple[list[str], list[str]]:
    heading = next(
        (
            tag
            for tag in soup.find_all(["h2", "h3", "h4"])
            if _clean_text(tag.get_text(" ", strip=True)).lower() == heading_text.lower()
        ),
        None,
    )
    if not isinstance(heading, Tag):
        return [], []
    section = heading.find_next("ul", class_=re.compile(r"PokemonPageMoves_movesList"))
    if not isinstance(section, Tag):
        return [], []
    regular: list[str] = []
    elite: list[str] = []
    for strong in section.select("summary .MoveCard_name__M3I5R"):
        text = _clean_text(strong.get_text(" ", strip=True))
        if not text:
            continue
        is_elite = "*" in text
        name = _clean_text(text.replace("*", ""))
        if not name:
            continue
        target = elite if is_elite else regular
        if name not in target:
            target.append(name)
    return regular, elite


def has_real_pokemon_page_content(content: str | None, title: str | None = None) -> bool:
    combined = f"{title or ''}\n{content or ''}".lower()
    if any(marker in (title or "").lower() for marker in INVALID_TITLE_MARKERS):
        return False
    if "enable javascript and cookies to continue" in combined:
        return False
    has_marker = any(marker in combined for marker in REAL_CONTENT_MARKERS)
    has_pokemon_path = "/pokemon/" in combined or "db.pokemongohub.net/pokemon/" in combined
    has_stat_signals = any(token in combined for token in ("atk", "def", "hp", "combat power"))
    return bool(_looks_like_real_pokemon_page(content or "") or has_marker or (has_pokemon_path and has_stat_signals))


def is_forms_blocked_content(content: str | None, status_code: int | None = None, title: str | None = None) -> bool:
    combined = f"{title or ''}\n{content or ''}"
    if has_real_pokemon_page_content(combined, title=title):
        return False
    lowered_title = (title or "").lower()
    if any(marker in lowered_title for marker in BLOCK_TITLE_MARKERS):
        return True
    lowered = (content or "").lower()
    has_block_marker = any(marker in lowered for marker in FORMS_BLOCK_MARKERS) or "security verification" in lowered
    return bool(has_block_marker or (status_code in {403, 429, 503} and lowered))


def is_blocked_page(content: str | None, status_code: int | None = None) -> bool:
    return is_forms_blocked_content(content, status_code=status_code)


def should_prompt_for_manual_cloudflare(
    content: str | None,
    *,
    title: str | None = None,
    manual_cloudflare: bool = False,
) -> bool:
    """Return True only when manual Cloudflare intervention is enabled and actually needed."""

    if not manual_cloudflare:
        return False
    combined = content or ""
    return bool(
        is_forms_blocked_content(combined, title=title)
        and not has_real_pokemon_page_content(combined, title=title)
    )


def parse_pokemon_go_hub_form_html(url: str, html: str, dex_number: int, form_hint: str | None = None) -> dict | None:
    """Parse one Pokémon GO Hub Pokémon/form page from HTML."""

    if not html or is_forms_blocked_content(html, title=_title_from_html(html)):
        return None
    soup = BeautifulSoup(html, "html.parser")
    title = _extract_title(soup)
    lowered_title = title.lower()
    if (
        not title
        or lowered_title in {"404", "not found"}
        or any(marker in lowered_title for marker in INVALID_TITLE_MARKERS)
        or "this page could not be found" in lowered_title
        or not _looks_like_real_pokemon_page(html)
    ):
        return None

    raw_form_hint = form_hint or _slug_suffix_from_url(url)
    pokemon_name, form = _split_pokemon_name_and_form(title, raw_form_hint)
    types = _extract_types(soup)
    fast_moves, elite_fast_moves = _extract_moves_for_heading(soup, "Fast Attacks")
    charged_moves, elite_charged_moves = _extract_moves_for_heading(soup, "Charged Attacks")

    title_and_url = f"{title} {url}".lower()
    form_lower = (form or "").lower()
    is_shadow = int("shadow" in form_lower or "shadow" in title_and_url)
    is_mega = int("mega" in form_lower or lowered_title.startswith("mega ") or "-mega" in url.lower())
    is_gigantamax = int("gigantamax" in form_lower or "gigantamax" in title_and_url or " gmax" in title_and_url)

    return {
        "source": SOURCE_NAME,
        "dex_number": dex_number,
        "pokemon_name": pokemon_name or title,
        "form": form,
        "type_1": types[0] if types else None,
        "type_2": types[1] if len(types) > 1 else None,
        "fast_moves": json.dumps(fast_moves, ensure_ascii=False) if fast_moves else None,
        "charged_moves": json.dumps(charged_moves, ensure_ascii=False) if charged_moves else None,
        "elite_fast_moves": json.dumps(elite_fast_moves, ensure_ascii=False) if elite_fast_moves else None,
        "elite_charged_moves": json.dumps(elite_charged_moves, ensure_ascii=False) if elite_charged_moves else None,
        "attack": _stat_value_by_label(soup, "ATK"),
        "defense": _stat_value_by_label(soup, "DEF"),
        "stamina": _stat_value_by_label(soup, "HP"),
        "max_cp": _extract_max_cp(soup),
        "is_shadow": is_shadow,
        "is_mega": is_mega,
        "is_gigantamax": is_gigantamax,
        "url": url,
        "scraped_at": _utc_now(),
    }


def parse_rendered_pokemon_go_form_content(
    *,
    url: str,
    html: str,
    visible_text: str,
    dex_number: int,
    form_hint: str | None = None,
    title: str = "",
    return_metadata: bool = False,
) -> tuple[dict | None, dict[str, Any], bool] | tuple[dict | None, str, bool]:
    combined_content = f"{visible_text}\n{html}"
    real_content_detected = has_real_pokemon_page_content(combined_content, title=title)
    if is_forms_blocked_content(combined_content, title=title):
        metadata = {"parser_used": "blocked", "real_content_detected": real_content_detected}
        if return_metadata:
            return None, metadata, True
        return None, "blocked", True
    row = parse_pokemon_go_hub_form_html(url, html, dex_number=dex_number, form_hint=form_hint)
    parser_used = "dom" if row else "none"
    metadata = {"parser_used": parser_used, "real_content_detected": real_content_detected}
    if return_metadata:
        return row, metadata, False
    return row, parser_used, False


def _forms_profile_path() -> Path | None:
    profile_dir = (config.POKEMON_FORMS_BROWSER_PROFILE_DIR or "").strip()
    if not profile_dir:
        return None
    user_data_dir = Path(profile_dir)
    if not user_data_dir.is_absolute():
        user_data_dir = config.BASE_DIR / user_data_dir
    return user_data_dir


def _launch_context(playwright: Any, *, force_headed: bool = False) -> tuple[Any, Any | None, Path | None]:
    launch_options = {
        "headless": False if force_headed else bool(config.POKEMON_FORMS_BROWSER_HEADLESS),
        "slow_mo": int(config.POKEMON_FORMS_BROWSER_SLOW_MO_MS or 0),
    }
    user_data_dir = _forms_profile_path()
    if user_data_dir is not None:
        user_data_dir.mkdir(parents=True, exist_ok=True)
        context = playwright.chromium.launch_persistent_context(str(user_data_dir), **launch_options)
        return context, None, user_data_dir
    browser = playwright.chromium.launch(**launch_options)
    context = browser.new_context()
    return context, browser, None


def _empty_stats(stage: str = "requests") -> dict[str, Any]:
    return {
        "url": "",
        "final_url": "",
        "dex_number": None,
        "form_hint": None,
        "status_code": None,
        "blocked": False,
        "invalid": False,
        "error": None,
        "scraper_stage": stage,
        "parser_used": "",
        "real_content_detected": False,
        "page_title": "",
        "browser_profile_dir": "",
        "browser_headless": bool(config.POKEMON_FORMS_BROWSER_HEADLESS),
        "pause_browser": False,
    }


def _scrape_with_browser_form_page(
    *,
    url: str,
    dex_number: int,
    form_hint: str | None = None,
    pause_for_cloudflare: bool = False,
    include_debug_artifacts: bool = False,
) -> tuple[dict | None, dict[str, Any]]:
    stats = _empty_stats("browser")
    stats.update(
        {
            "url": url,
            "dex_number": dex_number,
            "form_hint": form_hint,
            "browser_headless": False if pause_for_cloudflare else bool(config.POKEMON_FORMS_BROWSER_HEADLESS),
            "pause_browser": bool(pause_for_cloudflare),
            "browser_profile_dir": str(_forms_profile_path() or ""),
        }
    )
    if not config.POKEMON_FORMS_USE_BROWSER_SCRAPER:
        stats["error"] = "browser-disabled"
        return None, stats
    try:
        from playwright.sync_api import Error as PlaywrightError
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        stats["error"] = f"playwright-not-installed: {exc}"
        return None, stats

    timeout_ms = int(config.POKEMON_FORMS_BROWSER_TIMEOUT_SECONDS or 60) * 1000
    screenshot_bytes: bytes | None = None
    html = ""
    visible_text = ""
    should_pause_for_manual_intervention = False
    try:
        with sync_playwright() as playwright:
            context, browser, profile_path = _launch_context(playwright, force_headed=pause_for_cloudflare)
            stats["browser_profile_dir"] = str(profile_path or "")
            try:
                context.set_default_timeout(timeout_ms)
                context.set_default_navigation_timeout(timeout_ms)
                page = context.new_page()
                try:
                    page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
                    try:
                        page.wait_for_load_state("networkidle", timeout=min(timeout_ms, 5_000))
                    except PlaywrightTimeoutError:
                        pass
                    stats["final_url"] = page.url
                    stats["page_title"] = page.title()
                    html = page.content()
                    try:
                        visible_text = page.locator("body").inner_text(timeout=min(timeout_ms, 5_000))
                    except PlaywrightTimeoutError:
                        visible_text = _visible_text_from_html(html)
                    combined_content = f"{visible_text}\n{html}"
                    should_pause_for_manual_intervention = should_prompt_for_manual_cloudflare(
                        combined_content,
                        title=stats["page_title"],
                        manual_cloudflare=pause_for_cloudflare,
                    )
                    if should_pause_for_manual_intervention:
                        print("Solve Cloudflare in the opened browser, then press Enter here to continue.")
                        try:
                            input()
                        except EOFError:
                            logger.warning(
                                "Pokémon forms pause requested, but stdin is not interactive; continuing without manual pause."
                            )
                        try:
                            page.wait_for_load_state("networkidle", timeout=min(timeout_ms, 5_000))
                        except PlaywrightTimeoutError:
                            pass
                    stats["final_url"] = page.url
                    stats["page_title"] = page.title()
                    html = page.content()
                    try:
                        visible_text = page.locator("body").inner_text(timeout=min(timeout_ms, 5_000))
                    except PlaywrightTimeoutError:
                        visible_text = _visible_text_from_html(html)
                    screenshot_bytes = page.screenshot(full_page=True)
                    row, parse_metadata, blocked = parse_rendered_pokemon_go_form_content(
                        url=page.url,
                        html=html,
                        visible_text=visible_text,
                        dex_number=dex_number,
                        form_hint=form_hint,
                        title=stats["page_title"],
                        return_metadata=True,
                    )
                    stats.update(parse_metadata)
                    stats["blocked"] = bool(blocked)
                    if row is None and not blocked:
                        lowered_title = str(stats.get("page_title") or "").lower()
                        stats["invalid"] = lowered_title in {"404", "not found"} or "could not be found" in lowered_title
                    if include_debug_artifacts:
                        stats["html"] = html
                        stats["visible_text"] = visible_text
                        stats["screenshot_bytes"] = screenshot_bytes
                    return row, stats
                finally:
                    page.close()
            finally:
                context.close()
                if browser is not None:
                    browser.close()
    except PlaywrightError as exc:
        stats["error"] = str(exc)
    if include_debug_artifacts:
        stats["html"] = html
        stats["visible_text"] = visible_text
        stats["screenshot_bytes"] = screenshot_bytes
    return None, stats


def load_pokemon_go_hub_form_page(
    url: str,
    dex_number: int,
    form_hint: str | None = None,
    *,
    use_browser: bool | None = None,
    pause_browser: bool = False,
    include_debug_artifacts: bool = False,
) -> dict[str, Any]:
    """Load one Pokémon GO Hub form page through requests first, then optional browser fallback."""

    browser_enabled = config.POKEMON_FORMS_USE_BROWSER_SCRAPER if use_browser is None else bool(use_browser)
    stats = _empty_stats("requests")
    stats.update(
        {
            "url": url,
            "final_url": url,
            "dex_number": dex_number,
            "form_hint": form_hint,
            "pause_browser": bool(pause_browser),
        }
    )
    html = ""
    visible_text = ""
    fallback_reason = ""

    try:
        response = requests.get(url, headers=REQUEST_HEADERS, timeout=REQUEST_TIMEOUT_SECONDS)
        stats["status_code"] = response.status_code
        html = response.text or ""
        title = _title_from_html(html)
        visible_text = _visible_text_from_html(html)
        stats["page_title"] = title
        row, parse_metadata, blocked = parse_rendered_pokemon_go_form_content(
            url=url,
            html=html,
            visible_text=visible_text,
            dex_number=dex_number,
            form_hint=form_hint,
            title=title,
            return_metadata=True,
        )
        stats.update(parse_metadata)
        if include_debug_artifacts:
            stats["html"] = html
            stats["visible_text"] = visible_text
        if response.status_code == 404:
            stats["invalid"] = True
            return {"row": None, "stats": stats}
        if blocked or (response.status_code in {403, 429, 503} and not stats["real_content_detected"]):
            stats["blocked"] = True
            fallback_reason = "blocked"
        elif response.status_code is not None and response.status_code >= 400 and not stats["real_content_detected"]:
            stats["invalid"] = True
            return {"row": None, "stats": stats}
        elif row is not None:
            return {"row": row, "stats": stats}
        else:
            fallback_reason = "no-usable-content"
    except requests.RequestException as exc:
        stats["error"] = str(exc)
        logger.warning("Failed to fetch Pokémon GO Hub form URL %s: %s", url, exc)
        fallback_reason = "request-error"

    if not browser_enabled:
        if include_debug_artifacts and "html" not in stats:
            stats["html"] = html
            stats["visible_text"] = visible_text
        if fallback_reason == "no-usable-content":
            stats["invalid"] = True
        return {"row": None, "stats": stats}

    browser_row, browser_stats = _scrape_with_browser_form_page(
        url=url,
        dex_number=dex_number,
        form_hint=form_hint,
        pause_for_cloudflare=pause_browser,
        include_debug_artifacts=include_debug_artifacts,
    )
    browser_stats["fallback_reason"] = fallback_reason
    if browser_row is not None or browser_stats.get("blocked") or browser_stats.get("invalid") or browser_stats.get("error"):
        return {"row": browser_row, "stats": browser_stats}
    return {"row": None, "stats": browser_stats}


def scrape_pokemon_go_hub_form_page(
    url: str,
    dex_number: int,
    form_hint: str | None = None,
    *,
    use_browser: bool | None = None,
    pause_browser: bool = False,
    include_debug_artifacts: bool = False,
) -> tuple[dict | None, dict]:
    """Fetch and parse one Pokémon GO Hub Pokémon/form page.

    Returns `(row, stats)` where `row` is `None` for 404/invalid/blocked pages.
    """

    result = load_pokemon_go_hub_form_page(
        url,
        dex_number,
        form_hint,
        use_browser=use_browser,
        pause_browser=pause_browser,
        include_debug_artifacts=include_debug_artifacts,
    )
    return result["row"], result["stats"]