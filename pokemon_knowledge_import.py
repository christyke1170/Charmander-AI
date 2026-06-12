"""Import local seed Pokémon knowledge into the existing cache table."""

from __future__ import annotations

import csv
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from config import BASE_DIR, configure_logging
from database.pokemon_db import init_pokemon_tables, upsert_pokemon_knowledge


DATA_DIR = BASE_DIR / "data"
CSV_SEED_PATH = DATA_DIR / "pokemon_knowledge_seed.csv"
JSON_SEED_PATH = DATA_DIR / "pokemon_knowledge_seed.json"
DEFAULT_SOURCE = "manual_seed"

POKEMON_KNOWLEDGE_FIELDS = (
    "source",
    "pokemon_id",
    "name",
    "form",
    "types",
    "max_cp",
    "best_moveset",
    "weaknesses",
    "resistances",
    "pve_summary",
    "pvp_summary",
    "raid_counter_summary",
    "raw_text",
    "url",
    "scraped_at",
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _clean_value(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _normalize_row(raw_row: dict[str, Any], scraped_at: str) -> dict[str, str | None]:
    """Map a CSV/JSON object to pokemon_knowledge fields."""

    row = {field: _clean_value(raw_row.get(field)) for field in POKEMON_KNOWLEDGE_FIELDS}
    row["source"] = row.get("source") or DEFAULT_SOURCE
    row["scraped_at"] = row.get("scraped_at") or scraped_at
    return row


def _read_csv_rows(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8-sig", newline="") as csv_file:
        return list(csv.DictReader(csv_file))


def _read_json_rows(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as json_file:
        data = json.load(json_file)

    if isinstance(data, list):
        rows = data
    elif isinstance(data, dict) and isinstance(data.get("pokemon"), list):
        rows = data["pokemon"]
    else:
        raise ValueError("JSON seed must be a list of objects or an object with a 'pokemon' list.")

    if not all(isinstance(row, dict) for row in rows):
        raise ValueError("Every JSON Pokémon seed entry must be an object.")
    return rows


def _select_seed_file() -> tuple[Path | None, str | None]:
    if CSV_SEED_PATH.exists():
        return CSV_SEED_PATH, "csv"
    if JSON_SEED_PATH.exists():
        return JSON_SEED_PATH, "json"
    return None, None


def import_pokemon_knowledge_seed() -> dict[str, Any]:
    """Import the first available local Pokémon seed file and return stats."""

    seed_path, file_type = _select_seed_file()
    if seed_path is None or file_type is None:
        return {
            "imported": 0,
            "skipped": 0,
            "file_type": None,
            "path": None,
            "message": (
                "No local Pokémon knowledge seed file found. Put either "
                f"{CSV_SEED_PATH.relative_to(BASE_DIR)} or {JSON_SEED_PATH.relative_to(BASE_DIR)} "
                "in the project, then run this import again."
            ),
        }

    raw_rows = _read_csv_rows(seed_path) if file_type == "csv" else _read_json_rows(seed_path)
    scraped_at = _utc_now()
    imported = 0
    skipped = 0

    init_pokemon_tables()
    for raw_row in raw_rows:
        row = _normalize_row(raw_row, scraped_at)
        if not row.get("name"):
            skipped += 1
            continue
        upsert_pokemon_knowledge(row)
        imported += 1

    return {
        "imported": imported,
        "skipped": skipped,
        "file_type": file_type,
        "path": str(seed_path.relative_to(BASE_DIR)),
        "message": f"Imported/upserted {imported} Pokémon knowledge row(s) from {seed_path.relative_to(BASE_DIR)}.",
    }


if __name__ == "__main__":
    configure_logging()
    result = import_pokemon_knowledge_seed()
    print(result["message"])
    print(f"Rows imported/upserted: {result['imported']}")
    print(f"Rows skipped: {result['skipped']}")
    print(f"File type used: {result['file_type'] or 'none'}")