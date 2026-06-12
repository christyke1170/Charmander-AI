"""Import local seed raid attacker rankings into SQLite."""

from __future__ import annotations

import csv
import json
import argparse
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from config import BASE_DIR, configure_logging
from database.raid_attackers_db import init_raid_attacker_tables, upsert_raid_attacker_rankings


DATA_DIR = BASE_DIR / "data"
CSV_SEED_PATH = DATA_DIR / "raid_attackers_seed.csv"
JSON_SEED_PATH = DATA_DIR / "raid_attackers_seed.json"
DEFAULT_SOURCE = "manual_seed"
EXAMPLE_DATA_WARNING = "Seed file appears to contain example placeholder data. Replace it with real raid attacker rankings before importing."

RAID_ATTACKER_FIELDS = (
    "source",
    "ranking_scope",
    "pokemon_name",
    "form",
    "pokemon_type",
    "rank",
    "fast_move",
    "charged_move",
    "score",
    "dps",
    "tdo",
    "summary",
    "url",
    "scraped_at",
)


def _display_path(path: Path) -> str:
    try:
        return str(path.relative_to(BASE_DIR))
    except ValueError:
        return str(path)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _clean_value(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _normalize_row(raw_row: dict[str, Any], scraped_at: str) -> dict[str, Any]:
    row = {field: _clean_value(raw_row.get(field)) for field in RAID_ATTACKER_FIELDS}
    row["source"] = row.get("source") or DEFAULT_SOURCE
    row["scraped_at"] = row.get("scraped_at") or scraped_at
    return row


def _is_example_row(row: dict[str, Any]) -> bool:
    """Return True if a seed row appears to be placeholder/example data."""

    source = str(row.get("source") or "").lower()
    pokemon_name = str(row.get("pokemon_name") or "")
    summary = str(row.get("summary") or "").lower()
    return (
        "example" in source
        or pokemon_name.startswith("Example ")
        or "not guaranteed current meta truth" in summary
    )


def _read_csv_rows(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8-sig", newline="") as csv_file:
        return list(csv.DictReader(csv_file))


def _read_json_rows(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as json_file:
        data = json.load(json_file)
    if isinstance(data, list):
        rows = data
    elif isinstance(data, dict) and isinstance(data.get("raid_attackers"), list):
        rows = data["raid_attackers"]
    else:
        raise ValueError("JSON seed must be a list of objects or an object with a 'raid_attackers' list.")
    if not all(isinstance(row, dict) for row in rows):
        raise ValueError("Every JSON raid attacker seed entry must be an object.")
    return rows


def _select_seed_file() -> tuple[Path | None, str | None]:
    if CSV_SEED_PATH.exists():
        return CSV_SEED_PATH, "csv"
    if JSON_SEED_PATH.exists():
        return JSON_SEED_PATH, "json"
    return None, None


def import_raid_attacker_seed(allow_example_data: bool = False) -> dict[str, Any]:
    """Import the first available local raid attacker seed file and return stats."""

    seed_path, file_type = _select_seed_file()
    if seed_path is None or file_type is None:
        return {
            "imported": 0,
            "skipped": 0,
            "file_type": None,
            "path": None,
            "message": (
                "No local raid attacker seed file found. Put either "
                f"{_display_path(CSV_SEED_PATH)} or {_display_path(JSON_SEED_PATH)} "
                "in the project, then run the update again."
            ),
        }

    raw_rows = _read_csv_rows(seed_path) if file_type == "csv" else _read_json_rows(seed_path)
    scraped_at = _utc_now()
    rows: list[dict[str, Any]] = []
    skipped = 0
    example_rows = 0
    for raw_row in raw_rows:
        row = _normalize_row(raw_row, scraped_at)
        if _is_example_row(row):
            example_rows += 1
        if not row.get("ranking_scope") or not row.get("pokemon_name"):
            skipped += 1
            continue
        rows.append(row)

    if example_rows and not allow_example_data:
        return {
            "imported": 0,
            "skipped": len(raw_rows),
            "example_rows": example_rows,
            "example_data_rejected": True,
            "file_type": file_type,
            "path": _display_path(seed_path),
            "message": EXAMPLE_DATA_WARNING,
        }

    init_raid_attacker_tables()
    imported = upsert_raid_attacker_rankings(rows)
    return {
        "imported": imported,
        "skipped": skipped,
        "example_rows": example_rows,
        "example_data_rejected": False,
        "file_type": file_type,
        "path": _display_path(seed_path),
        "message": f"Imported/upserted {imported} raid attacker ranking row(s) from {_display_path(seed_path)}.",
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Import local raid attacker ranking seed data.")
    parser.add_argument(
        "--allow-example-data",
        action="store_true",
        help="Allow importing placeholder example data. Do not use this for production bot data.",
    )
    args = parser.parse_args()
    configure_logging()
    result = import_raid_attacker_seed(allow_example_data=args.allow_example_data)
    print(result["message"])
    print(f"Rows imported/upserted: {result['imported']}")
    print(f"Rows skipped: {result['skipped']}")
    print(f"Example rows detected: {result.get('example_rows', 0)}")
    print(f"File type used: {result['file_type'] or 'none'}")