"""Pokémon GO Wiki/Fandom scraper using the MediaWiki API when possible."""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote

import requests
from bs4 import BeautifulSoup, Tag

from config import REQUEST_HEADERS, REQUEST_TIMEOUT_SECONDS
from database.wiki_knowledge_db import SOURCE_NAME, normalize_text


logger = logging.getLogger(__name__)

API_BASE_URL = "https://pokemongo.fandom.com/api.php"
WIKI_PAGE_BASE_URL = "https://pokemongo.fandom.com/wiki/"
MIN_CHUNK_CHARS = 700
MAX_CHUNK_CHARS = 1200
SEARCH_TITLE_STOP_WORDS = {"a", "an", "and", "go", "of", "pokemon", "pokémon", "the"}


def build_wiki_page_url(title: str) -> str:
    """Return the canonical Fandom wiki URL for a page title."""

    slug = quote((title or "").strip().replace(" ", "_"), safe="()_,-")
    return f"{WIKI_PAGE_BASE_URL}{slug}"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _clean_text(value: str) -> str:
    text = re.sub(r"\[\s*(?:edit|source|\d+)\s*\]", " ", value, flags=re.IGNORECASE)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _remove_noise(soup: BeautifulSoup) -> None:
    selectors = [
        "script",
        "style",
        "noscript",
        "svg",
        "sup.reference",
        ".reference",
        ".mw-editsection",
        ".toc",
        ".portable-infobox",
        ".infobox",
        ".navbox",
        ".metadata",
        ".printfooter",
        ".catlinks",
        ".noprint",
        ".wds-banner-notification__container",
        "figure",
        "table",
    ]
    for selector in selectors:
        for node in soup.select(selector):
            node.decompose()


def _iter_article_nodes(soup: BeautifulSoup) -> list[Tag]:
    root = soup.select_one(".mw-parser-output") or soup.select_one("main") or soup.body or soup
    nodes: list[Tag] = []
    for node in root.find_all(["h2", "h3", "h4", "p", "li"], recursive=True):
        if not isinstance(node, Tag):
            continue
        if node.find_parent(["table", "nav", "aside"]):
            continue
        nodes.append(node)
    return nodes


def extract_sections_from_html(html: str, default_title: str = "Overview") -> list[dict[str, str]]:
    """Extract clean section text from a MediaWiki/Fandom article HTML fragment."""

    soup = BeautifulSoup(html or "", "html.parser")
    _remove_noise(soup)
    sections: list[dict[str, str]] = []
    current_title = "Overview"
    current_parts: list[str] = []

    def flush() -> None:
        nonlocal current_parts
        content = _clean_text(" ".join(current_parts))
        if content:
            sections.append({"section_title": current_title or default_title or "Overview", "content": content})
        current_parts = []

    for node in _iter_article_nodes(soup):
        if node.name in {"h2", "h3", "h4"}:
            heading = _clean_text(node.get_text(" ", strip=True))
            heading = re.sub(r"\s*\[edit\]\s*", "", heading, flags=re.IGNORECASE).strip()
            if heading:
                flush()
                current_title = heading
            continue
        text = _clean_text(node.get_text(" ", strip=True))
        if not text or len(text) < 20:
            continue
        if text.lower() in {"contents", "navigation"}:
            continue
        current_parts.append(text)
    flush()

    if sections:
        return sections

    fallback = _clean_text(soup.get_text(" ", strip=True))
    return [{"section_title": default_title or "Overview", "content": fallback}] if fallback else []


def split_text_into_chunks(content: str, min_chars: int = MIN_CHUNK_CHARS, max_chars: int = MAX_CHUNK_CHARS) -> list[str]:
    """Split article text into Discord/RAG-sized chunks without cutting sentences when possible."""

    text = _clean_text(content)
    if not text:
        return []
    if len(text) <= max_chars:
        return [text]

    sentences = re.split(r"(?<=[.!?])\s+", text)
    chunks: list[str] = []
    current = ""
    for sentence in sentences:
        sentence = sentence.strip()
        if not sentence:
            continue
        if len(sentence) > max_chars:
            if current:
                chunks.append(current.strip())
                current = ""
            for start in range(0, len(sentence), max_chars):
                part = sentence[start : start + max_chars].strip()
                if part:
                    chunks.append(part)
            continue
        candidate = f"{current} {sentence}".strip() if current else sentence
        if len(candidate) <= max_chars:
            current = candidate
        else:
            if current:
                chunks.append(current.strip())
            current = sentence
    if current:
        chunks.append(current.strip())

    merged: list[str] = []
    for chunk in chunks:
        if merged and len(merged[-1]) < min_chars and len(merged[-1]) + 1 + len(chunk) <= max_chars:
            merged[-1] = f"{merged[-1]} {chunk}".strip()
        else:
            merged.append(chunk)
    return merged


def parse_wiki_article_html(
    html: str,
    title: str,
    url: str | None = None,
    scraped_at: str | None = None,
    page_id: str | int | None = None,
    revision_id: str | int | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Parse a wiki article HTML fragment into page/chunk rows."""

    scraped_at = scraped_at or _utc_now()
    url = url or build_wiki_page_url(title)
    sections = extract_sections_from_html(html, default_title=title)
    summary = sections[0]["content"][:500] if sections else None
    page_row = {
        "source": SOURCE_NAME,
        "title": title,
        "url": url,
        "page_id": str(page_id) if page_id is not None else None,
        "revision_id": str(revision_id) if revision_id is not None else None,
        "summary": summary,
        "scraped_at": scraped_at,
    }

    chunk_rows: list[dict[str, Any]] = []
    chunk_index = 0
    for section in sections:
        section_title = section.get("section_title") or "Overview"
        for chunk in split_text_into_chunks(section.get("content") or ""):
            chunk_rows.append(
                {
                    "source": SOURCE_NAME,
                    "page_title": title,
                    "url": url,
                    "section_title": section_title,
                    "chunk_index": chunk_index,
                    "content": chunk,
                    "content_norm": normalize_text(f"{title} {section_title} {chunk}"),
                    "scraped_at": scraped_at,
                }
            )
            chunk_index += 1
    return page_row, chunk_rows


def _fetch_page_via_api(title: str) -> dict[str, Any]:
    params = {
        "action": "parse",
        "page": title,
        "prop": "text|sections|links",
        "redirects": "1",
        "format": "json",
        "formatversion": "2",
    }
    response = requests.get(API_BASE_URL, params=params, headers=REQUEST_HEADERS, timeout=REQUEST_TIMEOUT_SECONDS)
    response.raise_for_status()
    data = response.json()
    if "error" in data:
        raise RuntimeError(data["error"].get("info") or str(data["error"]))
    parsed = data.get("parse") or {}
    html = parsed.get("text") or ""
    if isinstance(html, dict):
        html = html.get("*") or ""
    if not html:
        raise RuntimeError("MediaWiki API returned no article HTML")
    return {
        "title": parsed.get("title") or title,
        "page_id": parsed.get("pageid"),
        "revision_id": parsed.get("revid"),
        "html": html,
    }


def _search_page_title(title: str) -> str | None:
    """Return a likely existing wiki page title for a missing seed title."""

    params = {
        "action": "query",
        "list": "search",
        "srsearch": title,
        "format": "json",
        "formatversion": "2",
        "srlimit": "10",
    }
    response = requests.get(API_BASE_URL, params=params, headers=REQUEST_HEADERS, timeout=REQUEST_TIMEOUT_SECONDS)
    response.raise_for_status()
    results = (response.json().get("query") or {}).get("search") or []
    if not results:
        return None

    requested_terms = [term for term in normalize_text(title).split() if term not in SEARCH_TITLE_STOP_WORDS]
    scored: list[tuple[int, str]] = []
    for result in results:
        candidate = str(result.get("title") or "").strip()
        if not candidate:
            continue
        candidate_norm = normalize_text(candidate)
        score = 0
        if candidate_norm == normalize_text(title):
            score += 100
        for term in requested_terms:
            if term in candidate_norm.split():
                score += 20
            elif term in candidate_norm:
                score += 8
        if requested_terms and requested_terms[-1] in candidate_norm:
            score += 10
        scored.append((score, candidate))
    if not scored:
        return None
    scored.sort(key=lambda item: (-item[0], len(item[1])))
    return scored[0][1] if scored[0][0] > 0 else None


def _fetch_page_via_html(title: str) -> dict[str, Any]:
    url = build_wiki_page_url(title)
    response = requests.get(url, headers=REQUEST_HEADERS, timeout=REQUEST_TIMEOUT_SECONDS)
    response.raise_for_status()
    return {"title": title, "page_id": None, "revision_id": None, "html": response.text}


def scrape_wiki_pages(page_titles: list[str]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    """Fetch and chunk Pokémon GO Wiki pages.

    Uses Fandom's MediaWiki API first and falls back to direct HTML requests if
    the API cannot return usable article text. One failed page is logged and
    recorded without failing the whole update.
    """

    stats: dict[str, Any] = {
        "pages_requested": len(page_titles),
        "pages_fetched": 0,
        "pages_failed": 0,
        "chunks_created": 0,
        "errors": [],
    }
    page_rows: list[dict[str, Any]] = []
    chunk_rows: list[dict[str, Any]] = []
    scraped_at = _utc_now()
    seen: set[str] = set()

    for raw_title in page_titles:
        title = re.sub(r"\s+", " ", (raw_title or "").strip())
        if not title or title.startswith("#"):
            continue
        dedupe_key = title.casefold()
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        try:
            try:
                fetched = _fetch_page_via_api(title)
            except Exception as api_exc:
                logger.warning("MediaWiki API fetch failed for %s; trying search/HTML fallback: %s", title, api_exc)
                replacement_title = None
                try:
                    replacement_title = _search_page_title(title)
                except Exception as search_exc:
                    logger.warning("MediaWiki search fallback failed for %s: %s", title, search_exc)
                if replacement_title and replacement_title.casefold() != title.casefold():
                    logger.info("Using MediaWiki search fallback for %s -> %s", title, replacement_title)
                    fetched = _fetch_page_via_api(replacement_title)
                else:
                    fetched = _fetch_page_via_html(title)
            canonical_title = fetched.get("title") or title
            page_url = build_wiki_page_url(canonical_title)
            page_row, chunks = parse_wiki_article_html(
                fetched.get("html") or "",
                title=canonical_title,
                url=page_url,
                scraped_at=scraped_at,
                page_id=fetched.get("page_id"),
                revision_id=fetched.get("revision_id"),
            )
            if not chunks:
                raise RuntimeError("No usable wiki chunks were extracted")
            page_rows.append(page_row)
            chunk_rows.extend(chunks)
            stats["pages_fetched"] += 1
            stats["chunks_created"] += len(chunks)
        except Exception as exc:
            logger.exception("Failed to scrape wiki page %s: %s", title, exc)
            stats["pages_failed"] += 1
            stats["errors"].append(f"{title}: {exc}")
            continue

    return page_rows, chunk_rows, stats
