"""Import local manual Dynamax/Gigantamax attacker rankings into SQLite."""

from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from config import BASE_DIR, configure_logging
from database.cache_metadata import init_cache_metadata_table, update_cache_metadata
from database.db import init_db
from database.dynamax_attackers_db import (
    clear_dynamax_attackers_for_source,
    init_dynamax_attacker_tables,
    upsert_dynamax_attackers,
)
from database.raid_attackers_db import POKEMON_TYPES, normalize_type_name
from scraper.dynamax_attacker_scraper import SOURCE_NAME as LIVE_SOURCE


DATA_DIR = BASE_DIR / "data"
CSV_PATH = DATA_DIR / "dynamax_attackers.csv"
CACHE_NAME = "dynamax_attackers"
MANUAL_SOURCE = "manual_dynamax_csv"
EXAMPLE_DATA_WARNING = "CSV contains placeholder/example Dynamax rows. Replace them with real rankings or pass --allow-example-data for local testing only."

DYNAMAX_CSV_FIELDS = (
    "ranking_scope",
    "pokemon_type",
    "rank",
    "pokemon_name",
    "form",
    "fast_move",
    "charged_move",
    "score",
    "dps",
    "tdo",
    "summary",
    "url",
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


def _is_example_row(row: dict[str, Any]) -> bool:
    values = " ".join(str(row.get(field) or "") for field in DYNAMAX_CSV_FIELDS).lower()
    pokemon_name = str(row.get("pokemon_name") or "")
    return (
        pokemon_name.startswith("Example ")
        or "example" in values
        or "placeholder" in values
        or "replace with real" in values
        or "not guaranteed current meta truth" in values
    )


def _parse_rank(value: Any, row_number: int, errors: list[str]) -> int | None:
    text = _clean_value(value)
    if not text:
        return None
    try:
        return int(text)
    except ValueError:
        errors.append(f"row {row_number}: rank must be an integer when present")
        return None


def _normalize_and_validate_row(raw_row: dict[str, Any], row_number: int, scraped_at: str) -> tuple[dict[str, Any] | None, list[str]]:
    errors: list[str] = []
    row = {field: _clean_value(raw_row.get(field)) for field in DYNAMAX_CSV_FIELDS}

    pokemon_name = row.get("pokemon_name")
    ranking_scope = row.get("ranking_scope")
    pokemon_type = normalize_type_name(row.get("pokemon_type"))

    if not pokemon_name:
        errors.append(f"row {row_number}: pokemon_name is required")
    if not ranking_scope:
        errors.append(f"row {row_number}: ranking_scope is required")
    elif not ranking_scope.lower().startswith("type:"):
        errors.append(f"row {row_number}: ranking_scope must look like type:<type>")
    else:
        scope_type = normalize_type_name(ranking_scope[5:])
        if not scope_type:
            errors.append(f"row {row_number}: ranking_scope type is not one of the 18 Pokémon types")
        else:
            row["ranking_scope"] = f"type:{scope_type}"

    if not row.get("pokemon_type"):
        errors.append(f"row {row_number}: pokemon_type is required")
    elif not pokemon_type:
        errors.append(f"row {row_number}: pokemon_type must be one of the 18 Pokémon types")
    else:
        row["pokemon_type"] = pokemon_type

    if errors:
        return None, errors

    row["rank"] = _parse_rank(row.get("rank"), row_number, errors)
    if errors:
        return None, errors

    row["source"] = MANUAL_SOURCE
    row["scraped_at"] = scraped_at
    return row, []


def _read_csv_rows(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8-sig", newline="") as csv_file:
        reader = csv.DictReader(csv_file)
        missing = [field for field in DYNAMAX_CSV_FIELDS if field not in (reader.fieldnames or [])]
        if missing:
            raise ValueError(f"CSV is missing required column(s): {', '.join(missing)}")
        return list(reader)


def import_dynamax_csv(
    path: Path | None = None,
    allow_example_data: bool = False,
    update_metadata_on_success: bool = True,
) -> dict[str, Any]:
    """Import manual Dynamax CSV rows and return detailed stats."""

    init_db()
    init_cache_metadata_table()
    init_dynamax_attacker_tables()

    csv_path = path or CSV_PATH
    if not csv_path.exists():
        return {
            "rows_read": 0,
            "imported": 0,
            "skipped": 0,
            "validation_errors": [],
            "example_rows": 0,
            "example_data_rejected": False,
            "metadata_updated": False,
            "source": MANUAL_SOURCE,
            "path": None,
            "message": f"No manual Dynamax CSV found at {_display_path(csv_path)}.",
        }

    raw_rows = _read_csv_rows(csv_path)
    scraped_at = _utc_now()
    rows: list[dict[str, Any]] = []
    validation_errors: list[str] = []
    skipped = 0
    example_rows = 0

    for index, raw_row in enumerate(raw_rows, start=2):
        normalized, row_errors = _normalize_and_validate_row(raw_row, index, scraped_at)
        if row_errors or normalized is None:
            validation_errors.extend(row_errors)
            skipped += 1
            continue
        if _is_example_row(normalized):
            example_rows += 1
            if not allow_example_data:
                validation_errors.append(f"row {index}: {EXAMPLE_DATA_WARNING}")
                skipped += 1
                continue
        rows.append(normalized)

    imported = 0
    metadata_updated = False
    if rows:
        clear_dynamax_attackers_for_source(LIVE_SOURCE)
        clear_dynamax_attackers_for_source(MANUAL_SOURCE)
        imported = upsert_dynamax_attackers(rows)
        if imported > 0 and update_metadata_on_success:
            update_cache_metadata(
                CACHE_NAME,
                source=MANUAL_SOURCE,
                notes=f"Imported/upserted {imported} manual Dynamax attacker row(s) from {_display_path(csv_path)}.",
            )
            metadata_updated = True

    return {
        "rows_read": len(raw_rows),
        "imported": imported,
        "skipped": skipped,
        "validation_errors": validation_errors,
        "example_rows": example_rows,
        "example_data_rejected": bool(example_rows and not allow_example_data),
        "metadata_updated": metadata_updated,
        "source": MANUAL_SOURCE,
        "path": _display_path(csv_path),
        "message": f"Imported/upserted {imported} manual Dynamax attacker row(s) from {_display_path(csv_path)}.",
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Import manual Dynamax/Gigantamax attacker ranking CSV data.")
    parser.add_argument("--path", type=Path, default=CSV_PATH, help="CSV path to import. Defaults to data/dynamax_attackers.csv.")
    parser.add_argument(
        "--allow-example-data",
        action="store_true",
        help="Allow importing placeholder example data. Do not use this for production bot data.",
    )
    args = parser.parse_args()
    configure_logging()
    result = import_dynamax_csv(path=args.path, allow_example_data=args.allow_example_data)
    print(result["message"])
    print(f"Rows read: {result['rows_read']}")
    print(f"Rows imported/upserted: {result['imported']}")
    print(f"Rows skipped: {result['skipped']}")
    print(f"Example rows detected: {result.get('example_rows', 0)}")
    print(f"Metadata updated: {result.get('metadata_updated', False)}")
    print(f"Validation errors: {len(result.get('validation_errors', []))}")
    for error in result.get("validation_errors", []):
        print(f"- {error}")