"""Tests for cached Pokémon GO Wiki/Fandom knowledge support."""

from __future__ import annotations

import asyncio
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import config
import database.db as db_module
import wiki_update
from bot.commands import _is_pvp_query, _is_wiki_knowledge_query, register_commands
from bot.wiki_cache import WikiCacheManager
from database.cache_metadata import get_cache_metadata, init_cache_metadata_table, is_cache_stale, update_cache_metadata
from database.egg_pool_db import init_egg_pool_tables
from database.pokemon_db import init_pokemon_tables
from database.pvp_rankings_db import init_pvp_ranking_tables
from database.wiki_knowledge_db import (
    SOURCE_NAME,
    count_wiki_chunks,
    count_wiki_pages,
    init_wiki_knowledge_tables,
    search_wiki_chunks,
    upsert_wiki_chunks,
    upsert_wiki_pages,
)
from scraper.pokemon_go_wiki_scraper import parse_wiki_article_html
import scraper.pokemon_go_wiki_scraper as wiki_scraper


class FakeCommandTree:
    def __init__(self) -> None:
        self.commands: dict[str, object] = {}

    def command(self, name: str, description: str):
        def decorator(func):
            self.commands[name] = func
            return func

        return decorator


class WikiKnowledgeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.temp_path = Path(self.temp_dir.name)
        self.db_path = self.temp_path / "pogo_events.sqlite"
        self.db_patch = mock.patch.object(config, "DATABASE_PATH", self.db_path)
        self.db_module_patch = mock.patch.object(db_module, "DATABASE_PATH", self.db_path)
        self.db_patch.start()
        self.db_module_patch.start()
        self.addCleanup(self.db_patch.stop)
        self.addCleanup(self.db_module_patch.stop)

        init_cache_metadata_table()
        init_wiki_knowledge_tables()
        init_egg_pool_tables()
        init_pvp_ranking_tables()
        init_pokemon_tables()

    def _seed_shiny_wiki(self) -> None:
        scraped_at = datetime.now(timezone.utc).isoformat()
        upsert_wiki_pages(
            [
                {
                    "source": SOURCE_NAME,
                    "title": "Shiny Pokémon",
                    "url": "https://pokemongo.fandom.com/wiki/Shiny_Pok%C3%A9mon",
                    "summary": "Shiny Pokémon are alternate-colored Pokémon.",
                    "scraped_at": scraped_at,
                }
            ]
        )
        upsert_wiki_chunks(
            [
                {
                    "source": SOURCE_NAME,
                    "page_title": "Shiny Pokémon",
                    "url": "https://pokemongo.fandom.com/wiki/Shiny_Pok%C3%A9mon",
                    "section_title": "Overview",
                    "chunk_index": 0,
                    "content": "Shiny Pokémon are alternate-colored Pokémon. Some Pokémon can be encountered as Shiny Pokémon in Pokémon GO, but not all Pokémon can be shiny at all times.",
                    "scraped_at": scraped_at,
                }
            ]
        )

    def test_wiki_table_initialization_upsert_and_search_chunks(self) -> None:
        self._seed_shiny_wiki()

        self.assertEqual(count_wiki_pages(), 1)
        self.assertEqual(count_wiki_chunks(), 1)
        rows = search_wiki_chunks("what are shiny pokemon", limit=3)
        self.assertEqual(rows[0]["page_title"], "Shiny Pokémon")
        self.assertIn("alternate-colored", rows[0]["content"])

    def test_seed_file_reading_ignores_blanks_comments_and_duplicates(self) -> None:
        seed_path = self.temp_path / "wiki_pages_seed.txt"
        seed_path.write_text("\n# comment\nShiny Pokémon\nLucky Pokémon\nshiny pokémon\n", encoding="utf-8")

        titles = wiki_update.read_seed_page_titles(seed_path)

        self.assertEqual(titles, ["Shiny Pokémon", "Lucky Pokémon"])

    def test_wiki_scraper_parses_small_html_fixture_into_chunks(self) -> None:
        html = """
        <div class="mw-parser-output">
          <aside class="portable-infobox"><p>Ignore infobox text.</p></aside>
          <p>Shiny Pokémon are alternate-colored Pokémon in Pokémon GO. They are rare variants with different coloration.</p>
          <h2>Availability <span class="mw-editsection">edit</span></h2>
          <p>Not every Pokémon can be shiny at all times. Availability depends on whether its shiny form has been released.</p>
          <table><tr><td>Ignore navigation table</td></tr></table>
        </div>
        """

        page, chunks = parse_wiki_article_html(
            html,
            title="Shiny Pokémon",
            url="https://pokemongo.fandom.com/wiki/Shiny_Pok%C3%A9mon",
            scraped_at="2026-06-13T00:00:00+00:00",
        )

        self.assertEqual(page["title"], "Shiny Pokémon")
        self.assertGreaterEqual(len(chunks), 1)
        combined = " ".join(chunk["content"] for chunk in chunks)
        self.assertIn("alternate-colored", combined)
        self.assertIn("Not every Pokémon", combined)
        self.assertNotIn("Ignore infobox", combined)
        self.assertNotIn("Ignore navigation table", combined)

    def test_wiki_scraper_uses_search_fallback_for_missing_seed_page(self) -> None:
        fake_response = mock.Mock()
        fake_response.raise_for_status.return_value = None
        fake_response.json.return_value = {
            "query": {
                "search": [
                    {"title": "Lucky Pokémon"},
                    {"title": "Friends"},
                    {"title": "Trading"},
                ]
            }
        }

        with mock.patch.object(wiki_scraper.requests, "get", return_value=fake_response):
            self.assertEqual(wiki_scraper._search_page_title("Lucky Friends"), "Friends")

    def test_wiki_routing_expected_queries(self) -> None:
        self.assertTrue(_is_wiki_knowledge_query("what are shiny pokemon"))
        self.assertTrue(_is_wiki_knowledge_query("how does mega evolution work"))
        self.assertTrue(_is_wiki_knowledge_query("what is Dynamax?"))
        self.assertTrue(_is_wiki_knowledge_query("what is go battle league"))
        self.assertFalse(_is_wiki_knowledge_query("best fire dynamax attackers"))
        self.assertFalse(_is_wiki_knowledge_query("best great league pokemon"))
        self.assertTrue(_is_pvp_query("best great league pokemon"))

    def test_wiki_commands_are_registered(self) -> None:
        tree = FakeCommandTree()

        register_commands(tree, owner_id=123, raid_cache_manager=None, egg_cache_manager=None, dynamax_cache_manager=None, pvp_cache_manager=None, wiki_cache_manager=None)

        self.assertIn("wiki", tree.commands)
        self.assertIn("updatewiki", tree.commands)

    def test_update_does_not_mark_metadata_fresh_when_zero_chunks_fetched(self) -> None:
        stale_time = (datetime.now(timezone.utc) - timedelta(days=45)).isoformat()
        with mock.patch("database.cache_metadata._utc_now", return_value=stale_time):
            update_cache_metadata(wiki_update.CACHE_NAME, source="test", notes="old")
        self.assertIsNotNone(get_cache_metadata(wiki_update.CACHE_NAME))

        with mock.patch("wiki_update.scrape_wiki_pages", return_value=([], [], {"pages_fetched": 0, "pages_failed": 1, "chunks_created": 0, "errors": ["boom"]})):
            count, stats = wiki_update.run_wiki_update(page_titles=["Shiny Pokémon"])

        self.assertEqual(count, 0)
        self.assertFalse(stats["metadata_updated"])
        self.assertTrue(is_cache_stale(wiki_update.CACHE_NAME, 30))

    def test_wiki_cache_manager_zero_chunks_not_updated(self) -> None:
        async def run_case():
            with mock.patch("bot.wiki_cache.run_wiki_update", return_value=(0, {"metadata_updated": False, "errors": []})):
                return await WikiCacheManager().force_refresh("manual", wait_for_lock=False)

        result = asyncio.run(run_case())

        self.assertTrue(result.attempted)
        self.assertFalse(result.updated)
        self.assertEqual(result.reason, "zero-rows")
        self.assertFalse(result.stats.get("metadata_updated"))


if __name__ == "__main__":
    unittest.main()
