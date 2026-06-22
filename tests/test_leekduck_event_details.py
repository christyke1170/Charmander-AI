"""Minimal tests for cached Leek Duck event detail parsing and formatting."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from database import db as event_db
from bot.commands import _format_named_event_detail_response
from scraper.leekduck_scraper import parse_event_detail_html


class LeekDuckEventDetailTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "events.sqlite3"
        self.conn_manager = patch("database.db.DATABASE_PATH", self.db_path)
        self.conn_manager.start()
        event_db.init_db()

    def tearDown(self) -> None:
        self.conn_manager.stop()
        self.temp_dir.cleanup()

    def test_parse_event_detail_html_extracts_bonuses_and_raids(self) -> None:
        html = """
        <html><body>
          <h1>Pokémon GO Fest 2026: Global</h1>
          <h2>Bonuses</h2>
          <ul>
            <li>Free for all Trainers during event weekend</li>
            <li>Increased shiny chance</li>
          </ul>
          <h2>Raids</h2>
          <ul>
            <li>Saturday: Mega Mewtwo X</li>
            <li>Sunday: Mega Mewtwo Y</li>
          </ul>
        </body></html>
        """
        detail = parse_event_detail_html(html, "https://leekduck.com/events/pokemon-go-fest-2026-global/")
        self.assertIsNotNone(detail)
        sections = detail["sections_json"]
        self.assertIn("bonuses", sections)
        self.assertIn("raids", sections)
        self.assertIn("Increased shiny chance", sections["bonuses"])
        self.assertIn("Saturday: Mega Mewtwo X", sections["raids"])

    def test_named_event_formatter_prefers_cached_detail(self) -> None:
        event = {
            "source": "Leek Duck",
            "title": "Pokémon GO Fest 2026: Global",
            "category": "Event",
            "start_time": "2026-08-22T10:00:00+00:00",
            "end_time": "2026-08-23T18:00:00+00:00",
            "url": "https://leekduck.com/events/pokemon-go-fest-2026-global/",
            "summary": "GO Fest",
            "raw_text": "GO Fest",
            "scraped_at": "2026-06-22T00:00:00+00:00",
        }
        event_db.upsert_event(event)
        event_db.upsert_event_detail(
            {
                "event_url": event["url"],
                "event_title": event["title"],
                "fetched_at": "2026-06-22T00:00:00+00:00",
                "summary_text": "cached summary",
                "sections_json": {
                    "raids": ["Saturday: Mega Mewtwo X", "Sunday: Mega Mewtwo Y"],
                    "bonuses": ["Increased shiny chance"],
                    "features": ["Special Research including Zeraora"],
                },
            }
        )

        response = _format_named_event_detail_response(event, "what raids are during go fest global")
        self.assertIn("Mega Mewtwo X", response)
        self.assertIn("Raids:", response)
        self.assertIn("Source: Leek Duck cached event details.", response)


if __name__ == "__main__":
    unittest.main()