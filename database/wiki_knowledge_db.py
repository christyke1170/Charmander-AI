"""SQLite helpers for cached Pokémon GO Wiki/Fandom knowledge."""

from __future__ import annotations

import logging
import re
import unicodedata
from typing import Any, Iterable

from database.db import get_connection


logger = logging.getLogger(__name__)

SOURCE_NAME = "pokemongo_fandom_wiki"

WIKI_PAGES_SCHEMA = """
CREATE TABLE IF NOT EXISTS wiki_pages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL,
    title TEXT NOT NULL,
    url TEXT NOT NULL,
    page_id TEXT,
    revision_id TEXT,
    summary TEXT,
    scraped_at TEXT NOT NULL,
    UNIQUE(source, title)
);
"""

WIKI_CHUNKS_SCHEMA = """
CREATE TABLE IF NOT EXISTS wiki_chunks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL,
    page_title TEXT NOT NULL,
    url TEXT NOT NULL,
    section_title TEXT,
    chunk_index INTEGER NOT NULL,
    content TEXT NOT NULL,
    content_norm TEXT,
    scraped_at TEXT NOT NULL,
    UNIQUE(source, page_title, section_title, chunk_index)
);
"""

WIKI_ALIASES_SCHEMA = """
CREATE TABLE IF NOT EXISTS wiki_search_aliases (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    alias TEXT NOT NULL UNIQUE,
    page_title TEXT NOT NULL
);
"""

SEARCH_STOP_WORDS = {
    "a",
    "about",
    "all",
    "and",
    "an",
    "are",
    "can",
    "details",
    "does",
    "do",
    "explain",
    "give",
    "how",
    "info",
    "in",
    "is",
    "me",
    "of",
    "tell",
    "the",
    "what",
    "with",
    "work",
    "works",
}

DEFAULT_WIKI_ALIASES: tuple[tuple[str, str], ...] = (
    ("shiny", "Shiny Pokémon"),
    ("shiny pokemon", "Shiny Pokémon"),
    ("shiny pokémon", "Shiny Pokémon"),
    ("lucky pokemon", "Lucky Pokémon"),
    ("lucky pokémon", "Lucky Pokémon"),
    ("lucky friends", "Friends"),
    ("lucky friend", "Friends"),
    ("lucky trade", "Trading"),
    ("mega", "Mega Evolution"),
    ("mega evolution", "Mega Evolution"),
    ("mega evolve", "Mega Evolution"),
    ("shadow pokemon", "Shadow Pokémon"),
    ("shadow pokémon", "Shadow Pokémon"),
    ("purified pokemon", "Purified Pokémon"),
    ("purified pokémon", "Purified Pokémon"),
    ("adventure sync", "Adventure Sync"),
    ("routes", "Routes"),
    ("route", "Routes"),
    ("buddy", "Buddy Pokémon"),
    ("buddy pokemon", "Buddy Pokémon"),
    ("go battle league", "GO Battle League"),
    ("gbl", "GO Battle League"),
    ("team rocket", "Team GO Rocket"),
    ("rocket", "Team GO Rocket"),
)


def normalize_text(value: Any) -> str:
    """Return compact lowercase ASCII-ish text for searching.

    This intentionally normalizes Pokémon/Pokemon spelling and strips accents so
    cached titles, aliases, and user queries can be compared consistently.
    """

    text = "" if value is None else str(value)
    text = unicodedata.normalize("NFKD", text)
    text = "".join(char for char in text if not unicodedata.combining(char))
    text = re.sub(r"[^a-zA-Z0-9]+", " ", text).lower()
    return re.sub(r"\s+", " ", text).strip()


def _clean_text(value: Any) -> str | None:
    if value is None:
        return None
    text = re.sub(r"\s+", " ", str(value)).strip()
    return text or None


def _normalize_page_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "source": _clean_text(row.get("source")) or SOURCE_NAME,
        "title": _clean_text(row.get("title")),
        "url": _clean_text(row.get("url")),
        "page_id": _clean_text(row.get("page_id")),
        "revision_id": _clean_text(row.get("revision_id")),
        "summary": _clean_text(row.get("summary")),
        "scraped_at": _clean_text(row.get("scraped_at")),
    }


def _normalize_chunk_row(row: dict[str, Any]) -> dict[str, Any]:
    content = _clean_text(row.get("content"))
    content_norm = _clean_text(row.get("content_norm")) or normalize_text(content)
    try:
        chunk_index = int(row.get("chunk_index", 0))
    except (TypeError, ValueError):
        chunk_index = 0
    return {
        "source": _clean_text(row.get("source")) or SOURCE_NAME,
        "page_title": _clean_text(row.get("page_title")),
        "url": _clean_text(row.get("url")),
        "section_title": _clean_text(row.get("section_title")) or "Overview",
        "chunk_index": chunk_index,
        "content": content,
        "content_norm": content_norm,
        "scraped_at": _clean_text(row.get("scraped_at")),
    }


def init_wiki_knowledge_tables() -> None:
    """Create wiki knowledge cache tables if needed."""

    with get_connection() as conn:
        conn.execute(WIKI_PAGES_SCHEMA)
        conn.execute(WIKI_CHUNKS_SCHEMA)
        conn.execute(WIKI_ALIASES_SCHEMA)
    logger.info("Wiki knowledge tables initialized")


def clear_wiki_knowledge_for_source(source: str) -> None:
    """Delete cached wiki page/chunk rows for one source."""

    with get_connection() as conn:
        conn.execute("DELETE FROM wiki_chunks WHERE source = ?", (source,))
        conn.execute("DELETE FROM wiki_pages WHERE source = ?", (source,))


def upsert_wiki_page(row: dict[str, Any]) -> None:
    """Insert or update one cached wiki page row."""

    normalized = _normalize_page_row(row)
    required = ("source", "title", "url", "scraped_at")
    missing = [field for field in required if not normalized.get(field)]
    if missing:
        raise ValueError(f"Wiki page row missing required field(s): {', '.join(missing)}")

    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO wiki_pages (source, title, url, page_id, revision_id, summary, scraped_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(source, title) DO UPDATE SET
                url = excluded.url,
                page_id = excluded.page_id,
                revision_id = excluded.revision_id,
                summary = excluded.summary,
                scraped_at = excluded.scraped_at
            """,
            (
                normalized.get("source"),
                normalized.get("title"),
                normalized.get("url"),
                normalized.get("page_id"),
                normalized.get("revision_id"),
                normalized.get("summary"),
                normalized.get("scraped_at"),
            ),
        )


def upsert_wiki_chunk(row: dict[str, Any]) -> None:
    """Insert or update one cached wiki chunk row."""

    normalized = _normalize_chunk_row(row)
    required = ("source", "page_title", "url", "chunk_index", "content", "scraped_at")
    missing = [field for field in required if normalized.get(field) is None or normalized.get(field) == ""]
    if missing:
        raise ValueError(f"Wiki chunk row missing required field(s): {', '.join(missing)}")

    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO wiki_chunks (
                source, page_title, url, section_title, chunk_index, content, content_norm, scraped_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(source, page_title, section_title, chunk_index) DO UPDATE SET
                url = excluded.url,
                content = excluded.content,
                content_norm = excluded.content_norm,
                scraped_at = excluded.scraped_at
            """,
            (
                normalized.get("source"),
                normalized.get("page_title"),
                normalized.get("url"),
                normalized.get("section_title"),
                normalized.get("chunk_index"),
                normalized.get("content"),
                normalized.get("content_norm"),
                normalized.get("scraped_at"),
            ),
        )


def upsert_wiki_pages(rows: Iterable[dict[str, Any]]) -> int:
    """Upsert multiple page rows and return the number attempted."""

    count = 0
    for row in rows:
        upsert_wiki_page(row)
        count += 1
    return count


def upsert_wiki_chunks(rows: Iterable[dict[str, Any]]) -> int:
    """Upsert multiple chunk rows and return the number attempted."""

    count = 0
    for row in rows:
        upsert_wiki_chunk(row)
        count += 1
    return count


def count_wiki_pages() -> int:
    with get_connection() as conn:
        row = conn.execute("SELECT COUNT(*) AS count FROM wiki_pages").fetchone()
    return int(row["count"] if row else 0)


def count_wiki_chunks() -> int:
    with get_connection() as conn:
        row = conn.execute("SELECT COUNT(*) AS count FROM wiki_chunks").fetchone()
    return int(row["count"] if row else 0)


def _fetch_all(query: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    with get_connection() as conn:
        rows = conn.execute(query, params).fetchall()
    return [dict(row) for row in rows]


def _query_terms(query: str) -> list[str]:
    normalized = normalize_text(query)
    terms = [term for term in normalized.split() if len(term) >= 2 and term not in SEARCH_STOP_WORDS]
    if not terms and normalized:
        terms = normalized.split()
    return terms[:8]


def _score_chunk(row: dict[str, Any], terms: list[str], phrase: str) -> int:
    title = normalize_text(row.get("page_title"))
    section = normalize_text(row.get("section_title"))
    content = row.get("content_norm") or normalize_text(row.get("content"))
    haystack = f"{title} {section} {content}"
    score = 0
    if phrase and phrase in title:
        score += 25
    if phrase and phrase in haystack:
        score += 10
    for term in terms:
        if term in title:
            score += 8
        if term in section:
            score += 4
        score += min(content.count(term), 6)
    return score


def search_wiki_chunks(query: str, limit: int = 8) -> list[dict[str, Any]]:
    """Search cached wiki chunks with normalized LIKE filtering and keyword scoring."""

    normalized_query = normalize_text(query)
    terms = _query_terms(query)
    if not normalized_query and not terms:
        return []

    filters: list[str] = []
    params: list[Any] = []
    for term in terms or normalized_query.split():
        filters.append(
            "(LOWER(COALESCE(page_title, '')) LIKE ? OR LOWER(COALESCE(section_title, '')) LIKE ? OR COALESCE(content_norm, '') LIKE ?)"
        )
        like = f"%{term}%"
        params.extend([like, like, like])

    where = " OR ".join(filters) if filters else "COALESCE(content_norm, '') LIKE ?"
    if not filters:
        params.append(f"%{normalized_query}%")
    candidates = _fetch_all(
        f"""
        SELECT * FROM wiki_chunks
        WHERE {where}
        LIMIT ?
        """,
        (*params, max(int(limit) * 8, 25)),
    )
    scored = [(_score_chunk(row, terms, normalized_query), row) for row in candidates]
    scored = [(score, row) for score, row in scored if score > 0]
    scored.sort(key=lambda item: (-item[0], str(item[1].get("page_title") or ""), int(item[1].get("chunk_index") or 0)))
    return [row for _score, row in scored[: max(int(limit), 0)]]


def get_wiki_chunks_by_page_title(title: str, limit: int = 20) -> list[dict[str, Any]]:
    """Return cached chunks for a page title ordered by section/chunk index."""

    normalized_title = (title or "").strip().lower()
    if not normalized_title:
        return []
    return _fetch_all(
        """
        SELECT * FROM wiki_chunks
        WHERE LOWER(page_title) = ?
        ORDER BY chunk_index ASC
        LIMIT ?
        """,
        (normalized_title, int(limit)),
    )
