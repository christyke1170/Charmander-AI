"""Tests for Pokémon GO Hub Pokémon/form cache helpers and scraper."""

from __future__ import annotations

import json
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import config
import database.db as db_module
from bot.commands import _is_current_raid_event_query, _is_raid_attacker_query
from database.db import init_db
from database.pokemon_go_forms_db import (
    count_pokemon_go_forms,
    get_pokemon_go_forms_by_dex,
    get_pokemon_go_forms_by_name,
    init_pokemon_go_forms_tables,
    search_pokemon_go_forms,
    upsert_pokemon_go_forms,
)
from scraper.pokemon_go_hub_forms_scraper import (
    generate_pokemon_go_hub_candidates,
    has_real_pokemon_page_content,
    parse_pokemon_go_hub_form_html,
    scrape_pokemon_go_hub_form_page,
    should_prompt_for_manual_cloudflare,
)


SIMPLE_FORM_HTML = """
<html>
  <head><title>Mewtwo (Pokémon GO) – Best Moveset, Counters, Max CP &amp; Stats</title></head>
  <body>
    <div class="Card_card__xQTNH">
      <h1>Mewtwo</h1>
    </div>
    <span class="PokemonPageRenderers_officialImageTyping__BZQBp">
      <img title="Psychic" alt="Psychic" src="/images/icons/ico_13_psychic.webp" />
    </span>
    <div class="PokemonStatBars_gaugeAmount__JfJh6">
      <span class="PokemonStatBars_amount__Q_aF8">300</span>
      <span class="PokemonStatBars_statType__htfki">ATK</span>
    </div>
    <div class="PokemonStatBars_gaugeAmount__JfJh6">
      <span class="PokemonStatBars_amount__Q_aF8">182</span>
      <span class="PokemonStatBars_statType__htfki">DEF</span>
    </div>
    <div class="PokemonStatBars_gaugeAmount__JfJh6">
      <span class="PokemonStatBars_amount__Q_aF8">214</span>
      <span class="PokemonStatBars_statType__htfki">HP</span>
    </div>
    <div class="PokemonPageCompactNotableCPs_row__kbWZA">Lvl 50 Max CP 4724 CP</div>
    <h3>Fast Attacks</h3>
    <ul class="PokemonPageMoves_movesList__L7k6W">
      <li><details><summary><strong class="MoveCard_name__M3I5R">Psycho Cut</strong></summary></details></li>
      <li><details><summary><strong class="MoveCard_name__M3I5R">Confusion *</strong></summary></details></li>
    </ul>
    <h3>Charged Attacks</h3>
    <ul class="PokemonPageMoves_movesList__L7k6W">
      <li><details><summary><strong class="MoveCard_name__M3I5R">Psystrike *</strong></summary></details></li>
      <li><details><summary><strong class="MoveCard_name__M3I5R">Thunderbolt</strong></summary></details></li>
    </ul>
  </body>
</html>
"""


class PokemonGoFormsTests(unittest.TestCase):
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
        init_db()
        init_pokemon_go_forms_tables()

    def test_candidate_url_generation_defaults_to_base_pokedex_order(self) -> None:
        candidates = generate_pokemon_go_hub_candidates(max_dex=10)

        self.assertEqual(len(candidates), 10)
        self.assertEqual(
            [candidate["url"] for candidate in candidates],
            [f"https://db.pokemongohub.net/pokemon/{dex_number}" for dex_number in range(1, 11)],
        )
        self.assertEqual([candidate["dex_number"] for candidate in candidates], list(range(1, 11)))
        self.assertTrue(all(candidate["form"] is None for candidate in candidates))
        self.assertTrue(all(candidate["suffix"] is None for candidate in candidates))

    def test_candidate_generation_excludes_form_suffixes_by_default(self) -> None:
        candidates = generate_pokemon_go_hub_candidates(max_dex=151)
        urls = {candidate["url"] for candidate in candidates}

        self.assertEqual(len(candidates), 151)
        self.assertIn("https://db.pokemongohub.net/pokemon/150", urls)
        self.assertNotIn("https://db.pokemongohub.net/pokemon/1-Shadow", urls)
        self.assertNotIn("https://db.pokemongohub.net/pokemon/1-Mega", urls)
        self.assertNotIn("https://db.pokemongohub.net/pokemon/150-Shadow", urls)
        self.assertNotIn("https://db.pokemongohub.net/pokemon/150-Mega_X", urls)
        self.assertNotIn("https://db.pokemongohub.net/pokemon/428-Mega", urls)

    def test_candidate_generation_can_include_forms_when_explicitly_enabled(self) -> None:
        candidates = generate_pokemon_go_hub_candidates(max_dex=151, include_forms=True)
        urls = {candidate["url"] for candidate in candidates}

        self.assertIn("https://db.pokemongohub.net/pokemon/150", urls)
        self.assertIn("https://db.pokemongohub.net/pokemon/150-Shadow", urls)
        self.assertIn("https://db.pokemongohub.net/pokemon/150-Mega_X", urls)

    def test_candidate_generation_keeps_default_dex_150_base_only(self) -> None:
        candidates = generate_pokemon_go_hub_candidates(max_dex=151)
        dex_150 = [candidate for candidate in candidates if candidate["dex_number"] == 150]

        self.assertEqual(len(dex_150), 1)
        self.assertEqual(dex_150[0]["url"], "https://db.pokemongohub.net/pokemon/150")

    def test_db_upsert_and_search_by_name(self) -> None:
        scraped_at = datetime.now(timezone.utc).isoformat()
        inserted = upsert_pokemon_go_forms(
            [
                {
                    "source": "pokemongohub_pokemon_db",
                    "dex_number": 6,
                    "pokemon_name": "Charizard",
                    "form": None,
                    "type_1": "Fire",
                    "type_2": "Flying",
                    "fast_moves": ["Fire Spin"],
                    "charged_moves": ["Blast Burn"],
                    "url": "https://db.pokemongohub.net/pokemon/6",
                    "scraped_at": scraped_at,
                },
                {
                    "source": "pokemongohub_pokemon_db",
                    "dex_number": 6,
                    "pokemon_name": "Charizard",
                    "form": "Mega X",
                    "type_1": "Fire",
                    "type_2": "Dragon",
                    "fast_moves": ["Fire Spin"],
                    "charged_moves": ["Dragon Claw"],
                    "url": "https://db.pokemongohub.net/pokemon/6-Mega_X",
                    "scraped_at": scraped_at,
                },
            ]
        )
        self.assertEqual(inserted, 2)
        self.assertEqual(count_pokemon_go_forms(), 2)

        name_rows = get_pokemon_go_forms_by_name("charizard")
        dex_rows = get_pokemon_go_forms_by_dex(6)
        search_rows = search_pokemon_go_forms("mega x", limit=10)

        self.assertEqual(len(name_rows), 2)
        self.assertEqual(len(dex_rows), 2)
        self.assertEqual(search_rows[0]["form"], "Mega X")
        self.assertEqual(json.loads(name_rows[0]["fast_moves"]), ["Fire Spin"])

    def test_parser_fixture_extracts_expected_fields(self) -> None:
        row = parse_pokemon_go_hub_form_html(
            "https://db.pokemongohub.net/pokemon/150",
            SIMPLE_FORM_HTML,
            dex_number=150,
        )

        self.assertIsNotNone(row)
        assert row is not None
        self.assertEqual(row["pokemon_name"], "Mewtwo")
        self.assertIsNone(row["form"])
        self.assertEqual(row["type_1"], "Psychic")
        self.assertEqual(row["type_2"], None)
        self.assertEqual(json.loads(row["fast_moves"]), ["Psycho Cut"])
        self.assertEqual(json.loads(row["elite_fast_moves"]), ["Confusion"])
        self.assertEqual(json.loads(row["charged_moves"]), ["Thunderbolt"])
        self.assertEqual(json.loads(row["elite_charged_moves"]), ["Psystrike"])
        self.assertEqual(row["attack"], 300)
        self.assertEqual(row["defense"], 182)
        self.assertEqual(row["stamina"], 214)
        self.assertEqual(row["max_cp"], 4724)
        self.assertEqual(row["is_shadow"], 0)
        self.assertEqual(row["is_mega"], 0)

    def test_real_pokemon_content_overrides_stale_cloudflare_text(self) -> None:
        html = f"<div>Cloudflare challenge-platform just a moment</div>{SIMPLE_FORM_HTML}"
        row = parse_pokemon_go_hub_form_html(
            "https://db.pokemongohub.net/pokemon/150",
            html,
            dex_number=150,
        )

        self.assertTrue(has_real_pokemon_page_content(html, title="Mewtwo"))
        self.assertIsNotNone(row)
        assert row is not None
        self.assertEqual(row["pokemon_name"], "Mewtwo")

    def test_type_parser_ignores_non_type_titles(self) -> None:
        html = SIMPLE_FORM_HTML.replace(
            "</span>",
            '<img title="Mewtwo is available in Pokémon GO" alt="bad" src="/bad.png" /></span>',
            1,
        )
        row = parse_pokemon_go_hub_form_html(
            "https://db.pokemongohub.net/pokemon/150",
            html,
            dex_number=150,
        )

        self.assertIsNotNone(row)
        assert row is not None
        self.assertEqual(row["type_1"], "Psychic")
        self.assertIsNone(row["type_2"])

    def test_cloudflare_challenge_page_does_not_parse_fake_row(self) -> None:
        html = """
        <html>
          <head><title>Just a moment...</title></head>
          <body>Enable JavaScript and cookies to continue</body>
        </html>
        """
        row = parse_pokemon_go_hub_form_html(
            "https://db.pokemongohub.net/pokemon/150",
            html,
            dex_number=150,
        )

        self.assertFalse(has_real_pokemon_page_content(html, title="Just a moment..."))
        self.assertIsNone(row)

    def test_manual_cloudflare_prompt_not_triggered_when_real_pokemon_content_is_present(self) -> None:
        content = f"Mewtwo\n{SIMPLE_FORM_HTML}\nCloudflare challenge-platform just a moment"

        self.assertFalse(
            should_prompt_for_manual_cloudflare(
                content,
                title="Mewtwo",
                manual_cloudflare=True,
            )
        )

    def test_manual_cloudflare_prompt_only_triggered_for_blocked_content_when_enabled(self) -> None:
        blocked_content = "<html><head><title>Just a moment...</title></head><body>Enable JavaScript and cookies to continue</body></html>"

        self.assertTrue(
            should_prompt_for_manual_cloudflare(
                blocked_content,
                title="Just a moment...",
                manual_cloudflare=True,
            )
        )
        self.assertFalse(
            should_prompt_for_manual_cloudflare(
                blocked_content,
                title="Just a moment...",
                manual_cloudflare=False,
            )
        )

    def test_invalid_or_404_page_returns_no_row(self) -> None:
        response = mock.Mock(status_code=404, text="Not Found")
        with mock.patch("scraper.pokemon_go_hub_forms_scraper.requests.get", return_value=response):
            row, stats = scrape_pokemon_go_hub_form_page(
                "https://db.pokemongohub.net/pokemon/9999",
                dex_number=9999,
            )

        self.assertIsNone(row)
        self.assertTrue(stats["invalid"])
        self.assertFalse(stats["blocked"])
        self.assertEqual(stats["status_code"], 404)

    def test_requests_blocked_page_calls_browser_fallback(self) -> None:
        response = mock.Mock(status_code=403, text="Just a moment... Cloudflare")
        browser_row = {
            "source": "pokemongohub_pokemon_db",
            "dex_number": 150,
            "pokemon_name": "Mewtwo",
            "form": None,
            "url": "https://db.pokemongohub.net/pokemon/150",
            "scraped_at": datetime.now(timezone.utc).isoformat(),
        }
        browser_stats = {
            "url": "https://db.pokemongohub.net/pokemon/150",
            "final_url": "https://db.pokemongohub.net/pokemon/150",
            "blocked": False,
            "invalid": False,
            "page_title": "Mewtwo",
            "real_content_detected": True,
            "scraper_stage": "browser",
        }
        with mock.patch("scraper.pokemon_go_hub_forms_scraper.requests.get", return_value=response), mock.patch(
            "scraper.pokemon_go_hub_forms_scraper._scrape_with_browser_form_page",
            return_value=(browser_row, browser_stats),
        ) as browser_mock:
            row, stats = scrape_pokemon_go_hub_form_page(
                "https://db.pokemongohub.net/pokemon/150",
                dex_number=150,
            )

        self.assertTrue(browser_mock.called)
        self.assertEqual(row, browser_row)
        self.assertEqual(stats["scraper_stage"], "browser")

    def test_invalid_non_pokemon_page_returns_no_row_without_crashing(self) -> None:
        html = "<html><head><title>Not Found</title></head><body>This page could not be found.</body></html>"
        response = mock.Mock(status_code=200, text=html)
        with mock.patch("scraper.pokemon_go_hub_forms_scraper.requests.get", return_value=response), mock.patch(
            "scraper.pokemon_go_hub_forms_scraper._scrape_with_browser_form_page",
            return_value=(None, {"invalid": True, "blocked": False, "scraper_stage": "browser", "page_title": "Not Found"}),
        ):
            row, stats = scrape_pokemon_go_hub_form_page(
                "https://db.pokemongohub.net/pokemon/9999-does-not-exist",
                dex_number=9999,
            )

        self.assertIsNone(row)
        self.assertTrue(stats["invalid"])

    def test_existing_route_intent_behavior_is_unchanged(self) -> None:
        self.assertTrue(_is_current_raid_event_query("what 5-star raids are active"))
        self.assertFalse(_is_raid_attacker_query("what 5-star raids are active"))
        self.assertFalse(_is_raid_attacker_query("tell me a joke"))