"""Manual Pokémon GO Hub knowledge cache update."""

from __future__ import annotations

import logging

from config import POKEMON_DB_SCRAPE_LIMIT, configure_logging
from database.db import init_db
from database.pokemon_db import init_pokemon_tables, upsert_pokemon_knowledge_rows
from scraper.pokemongohub_db_scraper import scrape_pokemon_knowledge


logger = logging.getLogger(__name__)


def run_pokemon_knowledge_update(limit: int = POKEMON_DB_SCRAPE_LIMIT) -> tuple[int, dict[str, int]]:
    """Run the manual Pokémon knowledge update and return count + stats."""

    init_db()
    init_pokemon_tables()
    rows, stats = scrape_pokemon_knowledge(limit=limit)
    count = upsert_pokemon_knowledge_rows(rows)
    logger.info("Pokémon knowledge update complete. Upserted %d row(s). Stats: %s", count, stats)
    return count, stats


def _format_zero_row_warning(stats: dict[str, int]) -> str:
    """Return an explicit no-success warning for empty Pokémon cache updates."""

    if stats.get("discovered_links", 0) == 0:
        return (
            "No usable Pokémon detail links were discovered. "
            f"Discovery pages checked: {stats.get('discovery_pages_checked', 0)}; "
            f"blocked discovery pages: {stats.get('discovery_pages_blocked', 0)}; "
            f"Pokémon link selector/regex matches: {stats.get('pokemon_link_matches', 0)}."
        )
    return (
        "Pokémon detail links were discovered, but zero usable rows were parsed. "
        f"Pages scraped: {stats.get('pages_scraped', 0)}; "
        f"blocked detail pages: {stats.get('blocked_pages', 0)}; "
        f"parse failures: {stats.get('parse_failures', 0)}."
    )


if __name__ == "__main__":
    configure_logging()
    total, scrape_stats = run_pokemon_knowledge_update()
    print(f"Discovered links: {scrape_stats.get('discovered_links', 0)}")
    print(f"Pages scraped: {scrape_stats.get('pages_scraped', 0)}")
    print(f"Discovery pages checked: {scrape_stats.get('discovery_pages_checked', 0)}")
    print(f"Pokémon link matches: {scrape_stats.get('pokemon_link_matches', 0)}")
    print(f"Parse failures: {scrape_stats.get('parse_failures', 0)}")
    print(f"Rows upserted: {total}")
    if total == 0:
        print(_format_zero_row_warning(scrape_stats))
        print("No bypass or browser automation was attempted.")