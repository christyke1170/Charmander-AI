"""Phase 1 tests for cached PvPoke ranking storage and update safety."""

from __future__ import annotations

import sys
import unittest
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock
import asyncio


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import config
from ai.pvp_answerer import _requested_pvp_row_count, format_compact_pvp_rankings
from bot.commands import (
    _is_pvp_query,
    _is_raid_attacker_query,
    _is_dynamax_query,
    _is_egg_pool_query,
    _is_current_raid_event_query,
    build_pvp_response,
    get_pvp_rows_for_query,
    register_commands,
)
from bot.pvp_cache import PvpCacheManager
import database.db as db_module
from database.egg_pool_db import init_egg_pool_tables
from database.pokemon_db import init_pokemon_tables
import pvp_update
from database.cache_metadata import get_cache_metadata, init_cache_metadata_table, is_cache_stale, update_cache_metadata
from database.pvp_rankings_db import (
    SOURCE_NAME,
    count_pvp_rankings,
    get_top_pvp_rankings,
    init_pvp_ranking_tables,
    normalize_pvp_league,
    search_pvp_rankings,
    upsert_pvp_rankings,
)
from scraper.pvpoke_scraper import parse_pvpoke_ranking_json


class FakeCommandTree:
    def __init__(self) -> None:
        self.commands: dict[str, object] = {}

    def command(self, name: str, description: str):
        def decorator(func):
            self.commands[name] = func
            return func

        return decorator


class PvpRankingsPhase1Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.db_path = Path(self.temp_dir.name) / "pogo_events.sqlite"
        self.db_patch = mock.patch.object(config, "DATABASE_PATH", self.db_path)
        self.db_module_patch = mock.patch.object(db_module, "DATABASE_PATH", self.db_path)
        self.db_patch.start()
        self.db_module_patch.start()
        self.addCleanup(self.db_patch.stop)
        self.addCleanup(self.db_module_patch.stop)
        init_cache_metadata_table()
        init_pvp_ranking_tables()
        init_egg_pool_tables()
        init_pokemon_tables()

    def _seed_pvp_rankings(self) -> None:
        scraped_at = datetime.now(timezone.utc).isoformat()
        upsert_pvp_rankings(
            [
                {
                    "source": SOURCE_NAME,
                    "league": "great",
                    "league_cp": 1500,
                    "rank": 1,
                    "pokemon_name": "Lickilicky",
                    "type_1": "Normal",
                    "fast_move": "Rollout",
                    "charged_move_1": "Body Slam",
                    "charged_move_2": "Shadow Ball",
                    "score": "95.4",
                    "url": "https://pvpoke.com/rankings/all/1500/overall/",
                    "scraped_at": scraped_at,
                },
                {
                    "source": SOURCE_NAME,
                    "league": "great",
                    "league_cp": 1500,
                    "rank": 2,
                    "pokemon_name": "Azumarill",
                    "type_1": "Water",
                    "type_2": "Fairy",
                    "fast_move": "Bubble",
                    "charged_move_1": "Ice Beam",
                    "charged_move_2": "Play Rough",
                    "score": "88.7",
                    "url": "https://pvpoke.com/rankings/all/1500/overall/",
                    "scraped_at": scraped_at,
                },
                {
                    "source": SOURCE_NAME,
                    "league": "great",
                    "league_cp": 1500,
                    "rank": 3,
                    "pokemon_name": "Skarmory",
                    "type_1": "Steel",
                    "type_2": "Flying",
                    "fast_move": "Steel Wing",
                    "charged_move_1": "Sky Attack",
                    "charged_move_2": "Brave Bird",
                    "score": "86.2",
                    "url": "https://pvpoke.com/rankings/all/1500/overall/",
                    "scraped_at": scraped_at,
                },
                {
                    "source": SOURCE_NAME,
                    "league": "ultra",
                    "league_cp": 2500,
                    "rank": 1,
                    "pokemon_name": "Giratina Altered",
                    "type_1": "Ghost",
                    "type_2": "Dragon",
                    "fast_move": "Shadow Claw",
                    "charged_move_1": "Dragon Claw",
                    "charged_move_2": "Ancient Power",
                    "score": "94.1",
                    "url": "https://pvpoke.com/rankings/all/2500/overall/",
                    "scraped_at": scraped_at,
                },
                {
                    "source": SOURCE_NAME,
                    "league": "ultra",
                    "league_cp": 2500,
                    "rank": 12,
                    "pokemon_name": "Skarmory",
                    "type_1": "Steel",
                    "type_2": "Flying",
                    "fast_move": "Steel Wing",
                    "charged_move_1": "Sky Attack",
                    "charged_move_2": "Brave Bird",
                    "score": "80.2",
                    "url": "https://pvpoke.com/rankings/all/2500/overall/",
                    "scraped_at": scraped_at,
                },
                {
                    "source": SOURCE_NAME,
                    "league": "master",
                    "league_cp": 10000,
                    "rank": 1,
                    "pokemon_name": "Zygarde Complete",
                    "type_1": "Dragon",
                    "type_2": "Ground",
                    "fast_move": "Dragon Tail",
                    "charged_move_1": "Crunch",
                    "charged_move_2": "Earthquake",
                    "score": "96.0",
                    "url": "https://pvpoke.com/rankings/all/10000/overall/",
                    "scraped_at": scraped_at,
                },
            ]
        )

    def test_table_initialization_upsert_and_query_by_league(self) -> None:
        scraped_at = datetime.now(timezone.utc).isoformat()
        inserted = upsert_pvp_rankings(
            [
                {
                    "source": SOURCE_NAME,
                    "league": "great",
                    "league_cp": 1500,
                    "rank": 2,
                    "pokemon_name": "Clodsire",
                    "type_1": "Poison",
                    "type_2": "Ground",
                    "fast_move": "Poison Sting",
                    "charged_move_1": "Earthquake",
                    "charged_move_2": "Stone Edge",
                    "score": "96.1",
                    "url": "https://pvpoke.com/rankings/all/1500/overall/clodsire/",
                    "scraped_at": scraped_at,
                },
                {
                    "source": SOURCE_NAME,
                    "league": "great league",
                    "league_cp": 1500,
                    "rank": 1,
                    "pokemon_name": "Azumarill",
                    "type_1": "Water",
                    "type_2": "Fairy",
                    "fast_move": "Bubble",
                    "charged_move_1": "Ice Beam",
                    "charged_move_2": "Play Rough",
                    "score": "97.2",
                    "url": "https://pvpoke.com/rankings/all/1500/overall/azumarill/",
                    "scraped_at": scraped_at,
                },
                {
                    "source": SOURCE_NAME,
                    "league": "ultra",
                    "league_cp": 2500,
                    "rank": 1,
                    "pokemon_name": "Giratina Altered",
                    "scraped_at": scraped_at,
                },
            ]
        )

        self.assertEqual(inserted, 3)
        self.assertEqual(count_pvp_rankings(), 3)
        great_rows = get_top_pvp_rankings("gl", limit=5)
        self.assertEqual([row["pokemon_name"] for row in great_rows], ["Azumarill", "Clodsire"])
        self.assertEqual(search_pvp_rankings("bubble", league="great", limit=5)[0]["pokemon_name"], "Azumarill")

    def test_league_normalization(self) -> None:
        cases = {
            "great league": "great",
            "gl": "great",
            "ultra league": "ultra",
            "ul": "ultra",
            "master league": "master",
            "ml": "master",
        }
        for query, expected in cases.items():
            with self.subTest(query=query):
                self.assertEqual(normalize_pvp_league(query), expected)

    def test_scraper_parser_can_parse_small_fixture(self) -> None:
        ranking_fixture = [
            {"speciesId": "azumarill", "speciesName": "Azumarill", "moveset": ["BUBBLE", "ICE_BEAM", "PLAY_ROUGH"], "score": 97.2},
            {"speciesId": "sandslash_alolan_shadow", "speciesName": "Shadow Alolan Sandslash", "moveset": ["SHADOW_CLAW", "ICE_PUNCH", "DRILL_RUN"], "score": "95.5"},
        ]
        pokemon_index = {
            "azumarill": {"speciesId": "azumarill", "speciesName": "Azumarill", "types": ["water", "fairy"]},
            "sandslash_alolan_shadow": {
                "speciesId": "sandslash_alolan_shadow",
                "speciesName": "Shadow Alolan Sandslash",
                "types": ["ice", "steel"],
            },
        }
        move_index = {
            "BUBBLE": "Bubble",
            "ICE_BEAM": "Ice Beam",
            "PLAY_ROUGH": "Play Rough",
            "SHADOW_CLAW": "Shadow Claw",
            "ICE_PUNCH": "Ice Punch",
            "DRILL_RUN": "Drill Run",
        }

        rows = parse_pvpoke_ranking_json(
            ranking_fixture,
            league="great",
            pokemon_index=pokemon_index,
            move_index=move_index,
            scraped_at="2026-06-13T00:00:00+00:00",
            limit=200,
        )

        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["rank"], 1)
        self.assertEqual(rows[0]["pokemon_name"], "Azumarill")
        self.assertEqual(rows[0]["type_1"], "Water")
        self.assertEqual(rows[0]["type_2"], "Fairy")
        self.assertEqual(rows[0]["fast_move"], "Bubble")
        self.assertEqual(rows[0]["charged_move_1"], "Ice Beam")
        self.assertEqual(rows[0]["charged_move_2"], "Play Rough")
        self.assertEqual(rows[1]["pokemon_name"], "Shadow Alolan Sandslash")

    def test_update_does_not_mark_metadata_fresh_when_zero_rows_scraped(self) -> None:
        stale_time = (datetime.now(timezone.utc) - timedelta(days=45)).isoformat()
        with mock.patch("database.cache_metadata._utc_now", return_value=stale_time):
            update_cache_metadata(pvp_update.CACHE_NAME, source="test", notes="old")
        self.assertIsNotNone(get_cache_metadata(pvp_update.CACHE_NAME))

        with mock.patch("pvp_update.scrape_pvpoke_rankings", return_value=([], {"league_rows": {}, "parse_failures": 1, "scraper_stage": "test", "errors": []})):
            count, stats = pvp_update.run_pvp_update(limit_per_league=2)

        self.assertEqual(count, 0)
        self.assertFalse(stats["metadata_updated"])
        self.assertTrue(is_cache_stale(pvp_update.CACHE_NAME, 30))

    def test_pvp_intent_detection_handles_expected_queries(self) -> None:
        true_queries = [
            "best great league pokemon",
            "top 20 ultra league",
            "is azumarill good in great league",
            "best pvp pokemon",
            "pvpoke skarmory",
        ]
        for query in true_queries:
            with self.subTest(query=query):
                self.assertTrue(_is_pvp_query(query))

    def test_pvp_intent_does_not_steal_other_query_types(self) -> None:
        false_queries = [
            "best fire raid attackers",
            "best fire dynamax attackers",
            "what is in 10km eggs?",
            "what raids are active rn",
        ]
        for query in false_queries:
            with self.subTest(query=query):
                self.assertFalse(_is_pvp_query(query))
        self.assertTrue(_is_raid_attacker_query("best fire raid attackers"))
        self.assertTrue(_is_dynamax_query("best fire dynamax attackers"))
        self.assertTrue(_is_egg_pool_query("what is in 10km eggs?"))
        self.assertTrue(_is_current_raid_event_query("what raids are active rn"))

    def test_get_pvp_rows_for_query_routes_league_rankings_and_pokemon_search(self) -> None:
        self._seed_pvp_rankings()

        great_rows, great_route, great_league = get_pvp_rows_for_query("best great league pokemon", limit=5)
        ultra_rows, ultra_route, ultra_league = get_pvp_rows_for_query("top 20 ultra league", limit=20)
        master_rows, master_route, master_league = get_pvp_rows_for_query("best master league pokemon", limit=5)
        azu_rows, azu_route, azu_league = get_pvp_rows_for_query("is azumarill good in great league", limit=20)
        skarmory_rows, skarmory_route, skarmory_league = get_pvp_rows_for_query("pvpoke skarmory", limit=20)

        self.assertEqual((great_route, great_league, great_rows[0]["pokemon_name"]), ("league:great", "great", "Lickilicky"))
        self.assertEqual((ultra_route, ultra_league, ultra_rows[0]["pokemon_name"]), ("league:ultra", "ultra", "Giratina Altered"))
        self.assertEqual((master_route, master_league, master_rows[0]["pokemon_name"]), ("league:master", "master", "Zygarde Complete"))
        self.assertEqual((azu_route, azu_league, azu_rows[0]["pokemon_name"]), ("pokemon_search", "great", "Azumarill"))
        self.assertEqual(skarmory_route, "pokemon_search")
        self.assertIsNone(skarmory_league)
        self.assertEqual([row["league"] for row in skarmory_rows], ["great", "ultra"])

    def test_get_pvp_rows_for_query_returns_overview_for_general_pvp(self) -> None:
        self._seed_pvp_rankings()

        rows, route, league = get_pvp_rows_for_query("best pvp pokemon", limit=20)

        self.assertEqual(rows, [])
        self.assertEqual(route, "overview")
        self.assertIsNone(league)

    def test_pvp_formatter_is_compact_single_source_and_caps_top_n(self) -> None:
        rows = []
        for index in range(1, 26):
            rows.append(
                {
                    "league": "great",
                    "rank": index,
                    "pokemon_name": f"Great Mon {index}",
                    "type_1": "Normal",
                    "fast_move": "Rollout",
                    "charged_move_1": "Body Slam",
                    "charged_move_2": "Shadow Ball",
                    "score": f"{100 - index:.1f}",
                }
            )

        response = format_compact_pvp_rankings("top 25 great league", rows, "great", "league:great", max_rows=_requested_pvp_row_count("top 25"))
        numbered_rows = [line for line in response.splitlines() if re.match(r"^\d+\. ", line)]

        self.assertIn("Top Great League PvP rankings:", response)
        self.assertEqual(len(numbered_rows), 20)
        self.assertIn("Showing top 20 to keep the Discord message readable.", response)
        self.assertEqual(response.count("Source:"), 1)
        self.assertTrue(response.endswith("Source: https://pvpoke.com/rankings/all/1500/overall/"))

    def test_pvp_formatter_specific_pokemon_output(self) -> None:
        rows = [
            {
                "league": "great",
                "rank": 24,
                "pokemon_name": "Azumarill",
                "type_1": "Water",
                "type_2": "Fairy",
                "fast_move": "Bubble",
                "charged_move_1": "Ice Beam",
                "charged_move_2": "Play Rough",
                "score": "88.7",
            }
        ]

        response = format_compact_pvp_rankings("is azumarill good in great league", rows, "great", "pokemon_search", max_rows=10)

        self.assertIn("Azumarill in cached PvPoke rankings:", response)
        self.assertIn("- Great League: #24 — Water/Fairy — Bubble | Ice Beam / Play Rough — Score 88.7", response)
        self.assertEqual(response.count("Source:"), 1)
        self.assertTrue(response.endswith("Source: cached PvPoke rankings."))

    def test_build_pvp_response_reports_empty_cache(self) -> None:
        response = build_pvp_response("great", [], "league:great", "great")

        self.assertEqual(response, "The PvP ranking cache is empty. Ask the bot owner to run `/updatepvp`.")

    def test_pvp_commands_are_registered(self) -> None:
        tree = FakeCommandTree()

        register_commands(tree, owner_id=123, raid_cache_manager=None, egg_cache_manager=None, dynamax_cache_manager=None, pvp_cache_manager=None)

        self.assertIn("pvp", tree.commands)
        self.assertIn("updatepvp", tree.commands)

    def test_pvp_cache_manager_fresh_cache_does_not_update(self) -> None:
        async def run_case():
            with mock.patch("bot.pvp_cache.is_cache_stale", return_value=False), mock.patch("bot.pvp_cache.run_pvp_update", return_value=(1, {})) as updater:
                result = await PvpCacheManager().refresh_if_stale("smoke")
            return result, updater

        result, updater = asyncio.run(run_case())

        self.assertFalse(result.attempted)
        self.assertEqual(result.reason, "fresh")
        updater.assert_not_called()

    def test_pvp_cache_manager_zero_rows_not_updated(self) -> None:
        async def run_case():
            with mock.patch("bot.pvp_cache.run_pvp_update", return_value=(0, {"metadata_updated": False, "league_rows": {}})):
                return await PvpCacheManager().force_refresh("manual", wait_for_lock=False)

        result = asyncio.run(run_case())

        self.assertTrue(result.attempted)
        self.assertFalse(result.updated)
        self.assertEqual(result.reason, "zero-rows")
        self.assertFalse(result.stats.get("metadata_updated"))


if __name__ == "__main__":
    unittest.main()