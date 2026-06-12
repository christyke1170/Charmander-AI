"""Smoke tests for raid attacker cache freshness/update behavior."""

from __future__ import annotations

import asyncio
import re
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
import raid_attacker_import
import raid_attacker_update
from ai.raid_attacker_answerer import _requested_row_count
from bot.commands import (
    MAX_DISCORD_MESSAGE_LENGTH,
    _is_egg_pool_query,
    _is_current_raid_event_query,
    _is_raid_attacker_query,
    build_egg_response,
    build_contextual_mention_response,
    build_raid_attacker_response,
    get_raid_attacker_rows_for_query,
    infer_raid_attacker_route_from_bot_message,
    register_commands,
)
from bot.raid_attacker_cache import RaidAttackerCacheManager
from database.cache_metadata import get_cache_metadata, init_cache_metadata_table, is_cache_stale, update_cache_metadata
from database.egg_pool_db import (
    get_all_egg_pool_sections,
    get_egg_pools_by_distance,
    init_egg_pool_tables,
    search_egg_pools,
    upsert_egg_pool_rows,
)
from database.raid_attackers_db import (
    get_best_raid_attackers_across_types,
    get_top_raid_attackers,
    get_top_raid_attackers_by_type,
    init_raid_attacker_tables,
    upsert_raid_attacker_rankings,
)
from raid_attacker_import import EXAMPLE_DATA_WARNING, import_raid_attacker_seed
from raid_attacker_update import CACHE_NAME, run_raid_attacker_update
from scraper.raid_attacker_scraper import (
    BEST_PER_TYPE_URL_TEMPLATE,
    POKEMON_TYPES,
    build_best_per_type_url,
    is_blocked_page,
    parse_best_per_type_table,
)
from scraper.egg_scraper import detect_egg_distance_km, detect_pool_type, parse_leekduck_egg_html


class FakeCommandTree:
    def __init__(self) -> None:
        self.commands: dict[str, object] = {}

    def command(self, name: str, description: str):
        def decorator(func):
            self.commands[name] = func
            return func

        return decorator


class RaidAttackerCacheSmokeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.temp_dir = TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.temp_path = Path(self.temp_dir.name)
        self.db_path = self.temp_path / "pogo_events.sqlite"
        self.data_dir = self.temp_path / "data"
        self.data_dir.mkdir()

        self.db_patch = mock.patch.object(config, "DATABASE_PATH", self.db_path)
        self.db_module_patch = mock.patch.object(db_module, "DATABASE_PATH", self.db_path)
        self.csv_patch = mock.patch.object(raid_attacker_import, "CSV_SEED_PATH", self.data_dir / "raid_attackers_seed.csv")
        self.json_patch = mock.patch.object(raid_attacker_import, "JSON_SEED_PATH", self.data_dir / "raid_attackers_seed.json")
        self.db_patch.start()
        self.db_module_patch.start()
        self.csv_patch.start()
        self.json_patch.start()
        self.addCleanup(self.db_patch.stop)
        self.addCleanup(self.db_module_patch.stop)
        self.addCleanup(self.csv_patch.stop)
        self.addCleanup(self.json_patch.stop)

        init_cache_metadata_table()
        init_raid_attacker_tables()
        init_egg_pool_tables()

    def _seed_rankings(self) -> None:
        scraped_at = datetime.now(timezone.utc).isoformat()
        upsert_raid_attacker_rankings(
            [
                {
                    "source": "test",
                    "ranking_scope": "overall",
                    "pokemon_name": "Overall One",
                    "pokemon_type": "dragon",
                    "rank": 1,
                    "fast_move": "Dragon Tail",
                    "charged_move": "Outrage",
                    "scraped_at": scraped_at,
                },
                {
                    "source": "test",
                    "ranking_scope": "overall",
                    "pokemon_name": "Overall Two",
                    "pokemon_type": "ground",
                    "rank": 2,
                    "fast_move": "Mud Shot",
                    "charged_move": "Earthquake",
                    "scraped_at": scraped_at,
                },
                {
                    "source": "test",
                    "ranking_scope": "type:fire",
                    "pokemon_name": "Fire One",
                    "pokemon_type": "fire",
                    "rank": 1,
                    "fast_move": "Fire Spin",
                    "charged_move": "Blast Burn",
                    "score": "25.0",
                    "dps": "30.0",
                    "tdo": "600",
                    "scraped_at": scraped_at,
                },
                {
                    "source": "test",
                    "ranking_scope": "type:fire",
                    "pokemon_name": "Fire Two",
                    "pokemon_type": "fire",
                    "rank": 2,
                    "fast_move": "Fire Fang",
                    "charged_move": "Overheat",
                    "score": "20.0",
                    "dps": "28.0",
                    "tdo": "500",
                    "scraped_at": scraped_at,
                },
                {
                    "source": "test",
                    "ranking_scope": "type:water",
                    "pokemon_name": "Water One",
                    "pokemon_type": "water",
                    "rank": 1,
                    "fast_move": "Waterfall",
                    "charged_move": "Hydro Pump",
                    "score": "26.0",
                    "dps": "29.0",
                    "tdo": "650",
                    "scraped_at": scraped_at,
                },
                {
                    "source": "test",
                    "ranking_scope": "type:steel",
                    "pokemon_name": "Steel One",
                    "pokemon_type": "steel",
                    "rank": 1,
                    "fast_move": "Metal Claw",
                    "charged_move": "Meteor Mash",
                    "score": "27.0",
                    "dps": "31.0",
                    "tdo": "700",
                    "scraped_at": scraped_at,
                },
                {
                    "source": "test",
                    "ranking_scope": "type:dragon",
                    "pokemon_name": "Dragon Tie Low DPS",
                    "pokemon_type": "dragon",
                    "rank": 1,
                    "fast_move": "Dragon Tail",
                    "charged_move": "Outrage",
                    "score": "27.0",
                    "dps": "30.0",
                    "tdo": "800",
                    "scraped_at": scraped_at,
                },
            ]
        )

    def _sample_fire_rows(self, count: int = 12, url: str = "https://db.pokemongohub.net/pokemon-list/best-per-type/fire") -> list[dict[str, object]]:
        rows: list[dict[str, object]] = []
        for index in range(1, count + 1):
            rows.append(
                {
                    "ranking_scope": "type:fire",
                    "pokemon_name": f"Fire Attacker {index}",
                    "pokemon_type": "fire",
                    "rank": index,
                    "fast_move": "Fire Spin",
                    "charged_move": "Blast Burn *",
                    "score": f"{30 - (index / 10):.2f}",
                    "dps": f"{35 - (index / 10):.2f}",
                    "tdo": str(900 - index),
                    "url": url,
                }
            )
        return rows

    def _seed_many_type_rankings(self, type_name: str = "fire", count: int = 25) -> None:
        scraped_at = datetime.now(timezone.utc).isoformat()
        rows = []
        for index in range(1, count + 1):
            rows.append(
                {
                    "source": "test",
                    "ranking_scope": f"type:{type_name}",
                    "pokemon_name": f"{type_name.title()} Attacker {index}",
                    "pokemon_type": type_name,
                    "rank": index,
                    "fast_move": "Fast Move",
                    "charged_move": "Charged Move",
                    "score": f"{50 - index:.2f}",
                    "dps": f"{40 - index:.2f}",
                    "tdo": str(1000 - index),
                    "url": f"https://db.pokemongohub.net/pokemon-list/best-per-type/{type_name}",
                    "scraped_at": scraped_at,
                }
            )
        upsert_raid_attacker_rankings(rows)

    def _compact_numbered_rows(self, response: str) -> list[str]:
        return [line for line in response.splitlines() if re.match(r"^\d+\. ", line)]

    async def test_fresh_cache_does_not_update(self) -> None:
        update_cache_metadata(CACHE_NAME, source="test", notes="fresh")
        manager = RaidAttackerCacheManager()

        with mock.patch("bot.raid_attacker_cache.run_raid_attacker_update", return_value=(1, {})) as updater:
            result = await manager.refresh_if_stale("smoke")

        self.assertFalse(result.attempted)
        self.assertEqual(result.reason, "fresh")
        updater.assert_not_called()

    async def test_stale_cache_triggers_update(self) -> None:
        with mock.patch("bot.raid_attacker_cache.is_cache_stale", return_value=True), mock.patch(
            "bot.raid_attacker_cache.run_raid_attacker_update", return_value=(3, {"rows": 3})
        ) as updater:
            result = await RaidAttackerCacheManager().refresh_if_stale("smoke")

        self.assertTrue(result.attempted)
        self.assertTrue(result.updated)
        self.assertEqual(result.count, 3)
        updater.assert_called_once()

    def test_zero_row_update_does_not_mark_cache_fresh(self) -> None:
        stale_time = (datetime.now(timezone.utc) - timedelta(days=45)).isoformat()
        with mock.patch("database.cache_metadata._utc_now", return_value=stale_time):
            update_cache_metadata(CACHE_NAME, source="test", notes="old")
        metadata = get_cache_metadata(CACHE_NAME)
        self.assertIsNotNone(metadata)

        with mock.patch("raid_attacker_update.scrape_raid_attacker_rankings", return_value=([], {"scraped_rows": 0})), mock.patch.object(
            raid_attacker_update.config, "RAID_ATTACKER_USE_BROWSER_SCRAPER", False
        ):
            count, _stats = run_raid_attacker_update()

        self.assertEqual(count, 0)
        self.assertTrue(is_cache_stale(CACHE_NAME, 30))

    def test_missing_seed_file_returns_zero_without_crashing(self) -> None:
        result = import_raid_attacker_seed()

        self.assertEqual(result["imported"], 0)
        self.assertEqual(result["skipped"], 0)
        self.assertIsNone(result["path"])

    def test_example_seed_rows_are_rejected_by_default(self) -> None:
        seed_path = self.data_dir / "raid_attackers_seed.csv"
        seed_path.write_text(
            "source,ranking_scope,pokemon_name,form,pokemon_type,rank,fast_move,charged_move,score,dps,tdo,summary,url,scraped_at\n"
            "example_not_current_meta,overall,Example Mega Rayquaza,,dragon,1,Dragon Tail,Dragon Ascent,example,example,example,Example only - not guaranteed current meta truth.,https://example.invalid,\n",
            encoding="utf-8",
        )

        result = import_raid_attacker_seed()

        self.assertEqual(result["imported"], 0)
        self.assertEqual(result["skipped"], 1)
        self.assertEqual(result["example_rows"], 1)
        self.assertTrue(result["example_data_rejected"])
        self.assertEqual(result["message"], EXAMPLE_DATA_WARNING)

    def test_example_seed_rows_do_not_mark_cache_fresh(self) -> None:
        stale_time = (datetime.now(timezone.utc) - timedelta(days=45)).isoformat()
        with mock.patch("database.cache_metadata._utc_now", return_value=stale_time):
            update_cache_metadata(CACHE_NAME, source="test", notes="old")
        seed_path = self.data_dir / "raid_attackers_seed.csv"
        seed_path.write_text(
            "source,ranking_scope,pokemon_name,form,pokemon_type,rank,fast_move,charged_move,score,dps,tdo,summary,url,scraped_at\n"
            "example_not_current_meta,overall,Example Mega Rayquaza,,dragon,1,Dragon Tail,Dragon Ascent,example,example,example,Example only - not guaranteed current meta truth.,https://example.invalid,\n",
            encoding="utf-8",
        )

        with mock.patch("raid_attacker_update.scrape_raid_attacker_rankings", return_value=([], {"rows_parsed": 0})), mock.patch.object(
            raid_attacker_update.config, "RAID_ATTACKER_USE_BROWSER_SCRAPER", False
        ):
            count, stats = run_raid_attacker_update(force=True)

        self.assertEqual(count, 0)
        self.assertEqual(stats.get("seed_example_data_rejected"), 1)
        self.assertTrue(is_cache_stale(CACHE_NAME, 30))

    async def test_concurrent_update_lock_prevents_duplicate_updates(self) -> None:
        calls = 0

        def blocking_update() -> tuple[int, dict[str, int]]:
            nonlocal calls
            calls += 1
            import time

            time.sleep(0.1)
            return 1, {"rows": 1}

        manager = RaidAttackerCacheManager()
        with mock.patch("bot.raid_attacker_cache.run_raid_attacker_update", side_effect=blocking_update):
            first = asyncio.create_task(manager.force_refresh("first", wait_for_lock=False))
            await asyncio.sleep(0.02)
            second = await manager.force_refresh("second", wait_for_lock=False)
            first_result = await first

        self.assertTrue(first_result.updated)
        self.assertFalse(second.attempted)
        self.assertEqual(second.reason, "already-running")
        self.assertEqual(calls, 1)

    def test_get_top_raid_attackers_preserves_overall_rank_order(self) -> None:
        self._seed_rankings()

        rows = get_top_raid_attackers(limit=10)

        self.assertEqual([row["pokemon_name"] for row in rows], ["Overall One", "Overall Two"])
        self.assertEqual([row["rank"] for row in rows], [1, 2])

    def test_get_top_raid_attackers_by_type_preserves_type_rank_order(self) -> None:
        self._seed_rankings()

        rows = get_top_raid_attackers_by_type("fire", limit=10)

        self.assertEqual([row["pokemon_name"] for row in rows], ["Fire One", "Fire Two"])
        self.assertEqual([row["ranking_scope"] for row in rows], ["type:fire", "type:fire"])

    def test_query_routing_uses_type_specific_rankings(self) -> None:
        self._seed_rankings()

        rows, route = get_raid_attacker_rows_for_query("best fire attacker", limit=10)

        self.assertEqual(route, "type:fire")
        self.assertEqual(rows[0]["pokemon_name"], "Fire One")

    def test_raid_attacker_intent_detection_handles_natural_language_and_typos(self) -> None:
        true_queries = [
            "what are the best pokemon to use in raids",
            "what are the best water pokemon",
            "top Steel type tchackers",
            "best steel types, top 10",
        ]
        false_queries = [
            "what raids are active rn",
            "what 5-star raids are active",
        ]

        for query in true_queries:
            with self.subTest(query=query):
                self.assertTrue(_is_raid_attacker_query(query))
        for query in false_queries:
            with self.subTest(query=query):
                self.assertFalse(_is_raid_attacker_query(query))

    def test_current_raid_event_queries_still_route_to_events(self) -> None:
        true_queries = [
            "what raids are active rn",
            "what 5-star raids are active",
        ]

        for query in true_queries:
            with self.subTest(query=query):
                self.assertTrue(_is_current_raid_event_query(query))
                self.assertFalse(_is_raid_attacker_query(query))

    def test_requested_row_count_parses_top_n_queries(self) -> None:
        cases = {
            "top 20": 20,
            "can you give me the top 20?": 20,
            "can I get 25": 20,
            "best raid attackers": 5,
            "give me 15": 15,
            "show 20": 20,
            "list 12": 12,
        }

        for query, expected in cases.items():
            with self.subTest(query=query):
                self.assertEqual(_requested_row_count(query), expected)

    def test_query_routing_uses_detected_type_for_best_water_pokemon(self) -> None:
        self._seed_rankings()

        rows, route = get_raid_attacker_rows_for_query("best water pokemon", limit=10)

        self.assertEqual(route, "type:water")
        self.assertEqual(rows[0]["pokemon_name"], "Water One")

    def test_query_routing_uses_detected_type_for_steel_typo_attackers(self) -> None:
        self._seed_rankings()

        rows, route = get_raid_attacker_rows_for_query("top Steel type tchackers", limit=10)

        self.assertEqual(route, "type:steel")
        self.assertEqual(rows[0]["pokemon_name"], "Steel One")

    def test_query_routing_uses_detected_type_for_best_steel_types_top_10(self) -> None:
        self._seed_rankings()

        rows, route = get_raid_attacker_rows_for_query("best steel types, top 10", limit=10)

        self.assertEqual(route, "type:steel")
        self.assertEqual(rows[0]["pokemon_name"], "Steel One")

    def test_query_routing_uses_derived_overall_for_broad_raid_attacker_query(self) -> None:
        self._seed_rankings()

        rows, route = get_raid_attacker_rows_for_query("best pokemon to use in raids", limit=3)

        self.assertEqual(route, "derived_overall")
        self.assertEqual([row["pokemon_name"] for row in rows], ["Steel One", "Dragon Tie Low DPS", "Water One"])

    def test_derived_overall_sorts_by_numeric_score_dps_tdo(self) -> None:
        self._seed_rankings()

        rows = get_best_raid_attackers_across_types(limit=4)

        self.assertEqual([row["pokemon_name"] for row in rows], ["Steel One", "Dragon Tie Low DPS", "Water One", "Fire One"])
        self.assertEqual([row["ranking_scope"] for row in rows], ["type:steel", "type:dragon", "type:water", "type:fire"])

    def test_default_raid_attacker_response_is_compact_top_5_without_tdo(self) -> None:
        rows = self._sample_fire_rows(count=12)

        with mock.patch("bot.commands.is_openai_enabled", return_value=False):
            response = build_raid_attacker_response("best fire attackers", rows, route="type:fire")

        numbered_rows = self._compact_numbered_rows(response)
        self.assertEqual(len(numbered_rows), 5)
        self.assertNotIn("\n- ", response)
        self.assertNotIn("* Fast Move", response)
        self.assertNotIn("Fast Move:", response)
        self.assertNotIn("Charged Move:", response)
        self.assertNotIn("TDO", response)
        self.assertTrue(all(" — " in line and " / " in line and "Score " in line and "DPS " in line for line in numbered_rows))
        self.assertLessEqual(len(response), MAX_DISCORD_MESSAGE_LENGTH)

    def test_top_10_raid_attacker_response_shows_up_to_10_rows(self) -> None:
        rows = self._sample_fire_rows(count=12)

        with mock.patch("bot.commands.is_openai_enabled", return_value=False):
            response = build_raid_attacker_response("top 10 fire attackers", rows, route="type:fire")

        numbered_rows = self._compact_numbered_rows(response)
        self.assertEqual(len(numbered_rows), 10)
        self.assertTrue(numbered_rows[-1].startswith("10. "))
        self.assertLessEqual(len(response), MAX_DISCORD_MESSAGE_LENGTH)

    def test_top_20_raid_attacker_response_shows_more_than_5_rows_when_it_fits(self) -> None:
        rows = self._sample_fire_rows(count=20)

        with mock.patch("bot.commands.is_openai_enabled", return_value=False):
            response = build_raid_attacker_response("top 20 fire attackers", rows, route="type:fire")

        numbered_rows = self._compact_numbered_rows(response)
        self.assertEqual(len(numbered_rows), 20)
        self.assertTrue(numbered_rows[-1].startswith("20. "))
        self.assertLessEqual(len(response), MAX_DISCORD_MESSAGE_LENGTH)

    def test_request_above_20_is_capped_with_readability_note(self) -> None:
        rows = self._sample_fire_rows(count=25)

        with mock.patch("bot.commands.is_openai_enabled", return_value=False):
            response = build_raid_attacker_response("can I get 25 fire attackers", rows, route="type:fire")

        numbered_rows = self._compact_numbered_rows(response)
        self.assertEqual(len(numbered_rows), 20)
        self.assertIn("Showing top 20 to keep the Discord message readable.", response)
        self.assertLessEqual(len(response), MAX_DISCORD_MESSAGE_LENGTH)

    def test_bulk_or_tdo_raid_attacker_response_includes_tdo(self) -> None:
        rows = self._sample_fire_rows(count=3)

        with mock.patch("bot.commands.is_openai_enabled", return_value=False):
            bulky_response = build_raid_attacker_response("best bulky raid attackers", rows, route="type:fire")
            tdo_response = build_raid_attacker_response("highest TDO attackers", rows, route="type:fire")

        self.assertIn("TDO", bulky_response)
        self.assertIn("TDO", tdo_response)

    def test_source_url_appears_once_in_compact_response(self) -> None:
        source_url = "https://db.pokemongohub.net/pokemon-list/best-per-type/fire"
        rows = self._sample_fire_rows(count=8, url=source_url)

        with mock.patch("bot.commands.is_openai_enabled", return_value=False):
            response = build_raid_attacker_response("top 10 fire attackers", rows, route="type:fire")

        self.assertEqual(response.count(source_url), 1)
        self.assertIn(f"Source: {source_url}", response)

    def test_derived_overall_uses_single_cached_source_line(self) -> None:
        rows = self._sample_fire_rows(count=3)
        rows[1]["url"] = "https://db.pokemongohub.net/pokemon-list/best-per-type/water"

        with mock.patch("bot.commands.is_openai_enabled", return_value=False):
            response = build_raid_attacker_response("best raid attackers", rows, route="derived_overall")

        self.assertIn("Source: cached Pokémon GO Hub best-per-type tables.", response)
        self.assertNotIn("https://db.pokemongohub.net/pokemon-list/best-per-type/fire", response)

    def test_compact_response_reduces_rows_instead_of_cutting_mid_answer(self) -> None:
        rows = self._sample_fire_rows(count=20)
        for row in rows:
            row["pokemon_name"] = f"Extremely Long Named Fire Raid Attacker {row['rank']} With Extra Descriptor Text " * 3
            row["fast_move"] = "Very Long Fast Move Name With Extra Words " * 2
            row["charged_move"] = "Very Long Charged Move Name With Extra Words * " * 2

        with mock.patch("bot.commands.is_openai_enabled", return_value=False):
            response = build_raid_attacker_response("top 20 fire attackers", rows, route="type:fire")

        numbered_rows = self._compact_numbered_rows(response)
        self.assertLess(len(numbered_rows), 20)
        self.assertIn(f"Showing top {len(numbered_rows)} because of Discord message length.", response)
        self.assertTrue(response.endswith("Source: https://db.pokemongohub.net/pokemon-list/best-per-type/fire"))
        self.assertLessEqual(len(response), MAX_DISCORD_MESSAGE_LENGTH)

    def test_infer_raid_attacker_route_from_previous_bot_message(self) -> None:
        self.assertEqual(
            infer_raid_attacker_route_from_bot_message("Top cached Fire-type raid attackers:\n1. Mega Blaziken — Fire Spin / Blast Burn* — Score 28.04, DPS 31.98"),
            "type:fire",
        )
        self.assertEqual(
            infer_raid_attacker_route_from_bot_message(
                "Top raid attackers, derived from cached per-type rankings:\n"
                "1. Mega Mewtwo Y — Confusion / Psystrike* — Score 36.31, DPS 39.58\n"
                "Source: cached Pokémon GO Hub best-per-type tables."
            ),
            "derived_overall",
        )
        self.assertIsNone(infer_raid_attacker_route_from_bot_message("## Raid-Related Pokémon GO Events"))

    def test_reply_context_previous_fire_answer_top_20_routes_to_fire(self) -> None:
        self._seed_many_type_rankings("fire", count=25)
        previous = "Top cached Fire-type raid attackers:\n1. Fire Attacker 1 — Fast Move / Charged Move — Score 49.00, DPS 39.00"

        with mock.patch("bot.commands.is_openai_enabled", return_value=False):
            response, route, count = build_contextual_mention_response("top 20?", previous)

        self.assertEqual(route, "raid_attackers")
        self.assertEqual(count, 20)
        self.assertIn("Top cached Fire-type raid attackers:", response)
        self.assertEqual(len(self._compact_numbered_rows(response)), 20)

    def test_reply_context_previous_derived_answer_top_20_routes_to_derived_overall(self) -> None:
        self._seed_many_type_rankings("fire", count=12)
        self._seed_many_type_rankings("water", count=12)
        previous = (
            "Top raid attackers, derived from cached per-type rankings:\n"
            "1. Fire Attacker 1 — Fast Move / Charged Move — Score 49.00, DPS 39.00\n"
            "Source: cached Pokémon GO Hub best-per-type tables."
        )

        with mock.patch("bot.commands.is_openai_enabled", return_value=False):
            response, route, count = build_contextual_mention_response("can you give me the top 20?", previous)

        self.assertEqual(route, "raid_attackers")
        self.assertEqual(count, 20)
        self.assertIn("Top raid attackers, derived from cached per-type rankings:", response)
        self.assertEqual(len(self._compact_numbered_rows(response)), 20)

    def test_real_looking_seed_rows_can_import_and_mark_fresh(self) -> None:
        seed_path = self.data_dir / "raid_attackers_seed.csv"
        seed_path.write_text(
            "source,ranking_scope,pokemon_name,form,pokemon_type,rank,fast_move,charged_move,score,dps,tdo,summary,url,scraped_at\n"
            "seed,overall,Seed Overall,,dragon,1,Fast,Charged,100,10,100,Seed row,https://example.invalid,\n",
            encoding="utf-8",
        )

        with mock.patch("raid_attacker_update.scrape_raid_attacker_rankings", return_value=([], {"rows_parsed": 0})), mock.patch.object(
            raid_attacker_update.config, "RAID_ATTACKER_USE_BROWSER_SCRAPER", False
        ):
            count, stats = run_raid_attacker_update(force=True)

        self.assertEqual(count, 1)
        self.assertEqual(stats.get("seed_imported"), 1)
        self.assertFalse(is_cache_stale(CACHE_NAME, 30))
        self.assertEqual(get_top_raid_attackers(limit=1)[0]["pokemon_name"], "Seed Overall")

    def test_raid_attacker_commands_are_registered(self) -> None:
        tree = FakeCommandTree()

        register_commands(tree, owner_id=123, raid_cache_manager=None)

        self.assertIn("raidattackers", tree.commands)
        self.assertIn("updateraidattackers", tree.commands)

    def test_egg_table_upsert_search_by_distance_and_pokemon(self) -> None:
        scraped_at = datetime.now(timezone.utc).isoformat()
        upsert_egg_pool_rows(
            [
                {
                    "source": "test",
                    "pool_name": "1 km Eggs",
                    "egg_distance_km": 1,
                    "pool_type": "standard",
                    "pokemon_name": "Bulbasaur",
                    "scraped_at": scraped_at,
                    "url": "https://leekduck.com/eggs/",
                },
                {
                    "source": "test",
                    "pool_name": "10 km Eggs",
                    "egg_distance_km": 10,
                    "pool_type": "standard",
                    "pokemon_name": "Larvesta",
                    "scraped_at": scraped_at,
                    "url": "https://leekduck.com/eggs/",
                },
            ]
        )

        self.assertEqual(get_egg_pools_by_distance(1)[0]["pokemon_name"], "Bulbasaur")
        self.assertEqual(get_egg_pools_by_distance(10)[0]["pokemon_name"], "Larvesta")
        self.assertEqual(search_egg_pools("Larvesta")[0]["pool_name"], "10 km Eggs")
        self.assertIn("1 km Eggs", get_all_egg_pool_sections())

    def test_egg_pool_type_and_distance_detection(self) -> None:
        cases = {
            "1 km Eggs": (1, "standard"),
            "5 km Eggs (Adventure Sync Rewards)": (5, "adventure_sync"),
            "7 km Eggs (From Route Gift)": (7, "route_gift"),
            "10 km Eggs": (10, "standard"),
        }

        for pool_name, expected in cases.items():
            with self.subTest(pool_name=pool_name):
                self.assertEqual(detect_egg_distance_km(pool_name), expected[0])
                self.assertEqual(detect_pool_type(pool_name), expected[1])

    def test_leekduck_egg_html_parser_includes_one_km_and_special_pools(self) -> None:
        html = """
        <html><body>
          <h1>Current Eggs Hatches</h1>
          <h2>1 km Eggs</h2>
          <ul class="egg-grid"><li class="pokemon-card"><div class="icon"><img alt="Bulbasaur"/><svg class="shiny-icon"></svg></div><span class="name">Bulbasaur</span><div class="cp-range"><span>CP </span>637</div><div class="rarity"><svg class="mini-egg"></svg><svg class="mini-egg"></svg></div></li></ul>
          <h2>5 km Eggs (Adventure Sync Rewards)</h2><div class="egg-section"><div class="description">Weekly Adventure Sync Rewards.</div></div>
          <ul class="egg-grid"><li class="pokemon-card"><span class="name">Riolu</span><div class="cp-range">CP 567</div><div class="rarity"><svg class="mini-egg"></svg></div></li></ul>
          <h2>7 km Eggs (From Route Gift)</h2>
          <ul class="egg-grid"><li class="pokemon-card"><span class="name">Galarian Corsola</span></li></ul>
        </body></html>
        """

        rows, stats = parse_leekduck_egg_html(html, scraped_at="2026-06-12T00:00:00+00:00")

        self.assertEqual(stats["sections_found"], 3)
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[0]["pool_name"], "1 km Eggs")
        self.assertEqual(rows[0]["egg_distance_km"], 1)
        self.assertEqual(rows[0]["shiny_available"], 1)
        self.assertEqual(rows[0]["rarity_text"], "2 eggs")
        self.assertEqual(rows[1]["pool_type"], "adventure_sync")
        self.assertEqual(rows[2]["pool_type"], "route_gift")

    def test_egg_intent_detection_handles_natural_language(self) -> None:
        true_queries = [
            "what can hatch from eggs?",
            "what's in 10km eggs?",
            "what is in 1km eggs?",
            "can Larvesta hatch from eggs?",
        ]

        for query in true_queries:
            with self.subTest(query=query):
                self.assertTrue(_is_egg_pool_query(query))

    def test_egg_response_formatting_is_compact_grouped_and_single_source(self) -> None:
        scraped_at = datetime.now(timezone.utc).isoformat()
        upsert_egg_pool_rows(
            [
                {
                    "source": "test",
                    "pool_name": "10 km Eggs",
                    "egg_distance_km": 10,
                    "pool_type": "standard",
                    "pokemon_name": "Larvesta",
                    "scraped_at": scraped_at,
                    "url": "https://leekduck.com/eggs/",
                },
                {
                    "source": "test",
                    "pool_name": "10 km Eggs (Adventure Sync Rewards)",
                    "egg_distance_km": 10,
                    "pool_type": "adventure_sync",
                    "pokemon_name": "Gible",
                    "scraped_at": scraped_at,
                    "url": "https://leekduck.com/eggs/",
                },
            ]
        )

        with mock.patch("bot.commands.is_openai_enabled", return_value=False):
            response = build_egg_response("what's in 10km eggs?")
            pokemon_response = build_egg_response("can Larvesta hatch from eggs?")

        self.assertIn("Current 10 km Eggs:", response)
        self.assertIn("10 km Eggs (Adventure Sync Rewards):", response)
        self.assertEqual(response.count("Source: https://leekduck.com/eggs/"), 1)
        self.assertLessEqual(len(response), MAX_DISCORD_MESSAGE_LENGTH)
        self.assertIn("Larvesta is currently listed in:", pokemon_response)
        self.assertEqual(pokemon_response.count("Source: https://leekduck.com/eggs/"), 1)

    def test_egg_commands_are_registered(self) -> None:
        tree = FakeCommandTree()

        register_commands(tree, owner_id=123, raid_cache_manager=None, egg_cache_manager=None)

        self.assertIn("eggs", tree.commands)
        self.assertIn("updateeggs", tree.commands)

    def test_best_per_type_url_generation_for_all_types(self) -> None:
        self.assertEqual(len(POKEMON_TYPES), 18)
        for type_name in POKEMON_TYPES:
            self.assertEqual(
                build_best_per_type_url(type_name),
                BEST_PER_TYPE_URL_TEMPLATE.format(type_name=type_name),
            )

    def test_best_per_type_exact_table_parser_maps_columns_and_scope(self) -> None:
        html = """
        <html><body>
          <table><tr><th>Other</th></tr><tr><td>Ignore me</td></tr></table>
          <table>
            <thead>
              <tr><th>#</th><th>Name</th><th>Fast Attack</th><th>Charged Attack</th><th>DPS</th><th>TDO</th><th>Score</th></tr>
            </thead>
            <tbody>
              <tr><td>1.</td><td>Mega Tyranitar</td><td>Bite</td><td>Brutal Swing</td><td>28.03</td><td>912.3</td><td>26.05</td></tr>
              <tr><td>3</td><td>Shadow Hydreigon</td><td>Bite</td><td>Brutal Swing *</td><td>28.08</td><td>500.4</td><td>24.65</td></tr>
            </tbody>
          </table>
        </body></html>
        """

        rows = parse_best_per_type_table(
            html,
            "dark",
            url="https://db.pokemongohub.net/pokemon-list/best-per-type/dark",
            scraped_at="2026-06-12T00:00:00+00:00",
        )

        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["source"], "pokemongohub_best_per_type")
        self.assertEqual(rows[0]["ranking_scope"], "type:dark")
        self.assertEqual(rows[0]["pokemon_name"], "Mega Tyranitar")
        self.assertEqual(rows[0]["form"], "")
        self.assertEqual(rows[0]["pokemon_type"], "dark")
        self.assertEqual(rows[0]["rank"], 1)
        self.assertEqual(rows[0]["fast_move"], "Bite")
        self.assertEqual(rows[0]["charged_move"], "Brutal Swing")
        self.assertEqual(rows[0]["dps"], "28.03")
        self.assertEqual(rows[0]["tdo"], "912.3")
        self.assertEqual(rows[0]["score"], "26.05")
        self.assertEqual(rows[1]["charged_move"], "Brutal Swing *")

    def test_best_per_type_parser_handles_blocked_or_empty_html(self) -> None:
        self.assertTrue(is_blocked_page("<html>Just a moment... challenge-platform</html>", 503))
        self.assertEqual(parse_best_per_type_table("", "fire"), [])
        self.assertEqual(parse_best_per_type_table("<html>Just a moment cloudflare</html>", "fire"), [])

    def test_best_per_type_parser_avoids_duplicate_rows(self) -> None:
        html = """
        <table>
          <tr><th>#</th><th>Name</th><th>Fast Attack</th><th>Charged Attack</th><th>DPS</th><th>TDO</th><th>Score</th></tr>
          <tr><td>1</td><td>Mega Blaziken</td><td>Fire Spin</td><td>Blast Burn</td><td>30.1</td><td>700</td><td>27.5</td></tr>
          <tr><td>1</td><td>Mega Blaziken</td><td>Fire Spin</td><td>Blast Burn</td><td>30.1</td><td>700</td><td>27.5</td></tr>
          <tr><td>2</td><td></td><td>Fire Spin</td><td>Overheat</td><td>20</td><td>400</td><td>18</td></tr>
        </table>
        """

        rows = parse_best_per_type_table(html, "fire")

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["ranking_scope"], "type:fire")
        self.assertEqual(rows[0]["pokemon_name"], "Mega Blaziken")

    def test_browser_scraper_module_imports_without_running_browser(self) -> None:
        import scraper.raid_attacker_browser_scraper as browser_scraper

        self.assertTrue(callable(browser_scraper.scrape_best_attackers_per_type_with_browser))
        self.assertEqual(browser_scraper.BROWSER_SOURCE_NAME, "pokemongohub_best_per_type_browser")

    def test_browser_config_values_parse_as_expected(self) -> None:
        self.assertIsInstance(config.RAID_ATTACKER_USE_BROWSER_SCRAPER, bool)
        self.assertIsInstance(config.RAID_ATTACKER_BROWSER_HEADLESS, bool)
        self.assertIsInstance(config.RAID_ATTACKER_BROWSER_TIMEOUT_SECONDS, int)
        self.assertIsInstance(config.RAID_ATTACKER_BROWSER_SLOW_MO_MS, int)
        self.assertIsInstance(config.RAID_ATTACKER_BROWSER_PROFILE_DIR, str)

    def test_update_flow_tries_browser_after_zero_request_rows_when_enabled(self) -> None:
        browser_row = {
            "source": "pokemongohub_best_per_type_browser",
            "ranking_scope": "type:dark",
            "pokemon_name": "Mega Tyranitar",
            "pokemon_type": "dark",
            "rank": 1,
            "fast_move": "Bite",
            "charged_move": "Brutal Swing",
            "dps": "28.03",
            "tdo": "912.3",
            "score": "26.05",
            "url": "https://db.pokemongohub.net/pokemon-list/best-per-type/dark",
            "scraped_at": datetime.now(timezone.utc).isoformat(),
        }

        with mock.patch("raid_attacker_update.scrape_raid_attacker_rankings", return_value=([], {"rows_parsed": 0})), mock.patch(
            "raid_attacker_update.scrape_raid_attacker_rankings_with_browser",
            return_value=([browser_row], {"browser_rows_parsed": 1, "browser_pages_checked": 1}),
        ) as browser_scraper, mock.patch.object(raid_attacker_update.config, "RAID_ATTACKER_USE_BROWSER_SCRAPER", True):
            count, stats = run_raid_attacker_update(force=True)

        self.assertEqual(count, 1)
        browser_scraper.assert_called_once()
        self.assertEqual(stats.get("update_stage"), "browser")
        self.assertFalse(is_cache_stale(CACHE_NAME, 30))
        self.assertEqual(get_top_raid_attackers_by_type("dark", limit=1)[0]["pokemon_name"], "Mega Tyranitar")

    def test_update_flow_skips_browser_when_disabled(self) -> None:
        with mock.patch("raid_attacker_update.scrape_raid_attacker_rankings", return_value=([], {"rows_parsed": 0})), mock.patch(
            "raid_attacker_update.scrape_raid_attacker_rankings_with_browser"
        ) as browser_scraper, mock.patch.object(raid_attacker_update.config, "RAID_ATTACKER_USE_BROWSER_SCRAPER", False):
            count, stats = run_raid_attacker_update(force=True)

        self.assertEqual(count, 0)
        browser_scraper.assert_not_called()
        self.assertFalse(stats.get("browser_enabled"))
        self.assertTrue(is_cache_stale(CACHE_NAME, 30))

    def test_zero_browser_rows_do_not_mark_metadata_fresh(self) -> None:
        stale_time = (datetime.now(timezone.utc) - timedelta(days=45)).isoformat()
        with mock.patch("database.cache_metadata._utc_now", return_value=stale_time):
            update_cache_metadata(CACHE_NAME, source="test", notes="old")

        with mock.patch("raid_attacker_update.scrape_raid_attacker_rankings", return_value=([], {"rows_parsed": 0})), mock.patch(
            "raid_attacker_update.scrape_raid_attacker_rankings_with_browser",
            return_value=([], {"browser_rows_parsed": 0, "browser_pages_checked": 1}),
        ), mock.patch.object(raid_attacker_update.config, "RAID_ATTACKER_USE_BROWSER_SCRAPER", True):
            count, stats = run_raid_attacker_update(force=True)

        self.assertEqual(count, 0)
        self.assertEqual(stats.get("browser_rows_parsed"), 0)
        self.assertTrue(is_cache_stale(CACHE_NAME, 30))


if __name__ == "__main__":
    unittest.main()