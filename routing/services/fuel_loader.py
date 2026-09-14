"""Load fuel station rows from the assessment CSV into the database."""

from __future__ import annotations

import csv
import logging
from decimal import Decimal, InvalidOperation
from pathlib import Path

from django.conf import settings

from routing.models import FuelStation

logger = logging.getLogger(__name__)


def resolve_csv_path(prefer_sample: bool = False) -> Path:
    """Prefer the full CSV when present, otherwise fall back to the sample."""
    full = Path(settings.FUEL_CSV_FULL)
    sample = Path(settings.FUEL_CSV_SAMPLE)
    if prefer_sample and sample.exists():
        return sample
    if full.exists():
        return full
    if sample.exists():
        return sample
    raise FileNotFoundError(
        f"No fuel CSV found. Expected {full} or {sample}."
    )


def _parse_price(raw: str) -> Decimal | None:
    try:
        return Decimal(str(raw).strip())
    except (InvalidOperation, AttributeError):
        return None


def load_fuel_stations(
    csv_path: Path | None = None,
    *,
    clear_existing: bool = False,
    prefer_sample: bool = False,
) -> int:
    """
    Import stations from CSV. Returns the number of rows inserted.

    Deduplicates on (opis_id, name, city, state, retail_price) within a run
    by skipping blank / invalid price rows.
    """
    path = csv_path or resolve_csv_path(prefer_sample=prefer_sample)
    logger.info("Loading fuel stations from %s", path)

    if clear_existing:
        deleted, _ = FuelStation.objects.all().delete()
        logger.info("Cleared %s existing station rows", deleted)

    batch: list[FuelStation] = []
    inserted = 0
    skipped = 0

    with path.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            price = _parse_price(row.get("Retail Price", ""))
            if price is None:
                skipped += 1
                continue
            try:
                opis_id = int(str(row.get("OPIS Truckstop ID", "0")).strip() or "0")
            except ValueError:
                skipped += 1
                continue

            rack_raw = (row.get("Rack ID") or "").strip()
            rack_id = int(rack_raw) if rack_raw.isdigit() else None

            batch.append(
                FuelStation(
                    opis_id=opis_id,
                    name=(row.get("Truckstop Name") or "").strip()[:255],
                    address=(row.get("Address") or "").strip()[:255],
                    city=(row.get("City") or "").strip()[:128],
                    state=(row.get("State") or "").strip().upper()[:2],
                    rack_id=rack_id,
                    retail_price=price,
                )
            )
            if len(batch) >= 500:
                FuelStation.objects.bulk_create(batch, batch_size=500)
                inserted += len(batch)
                batch.clear()

    if batch:
        FuelStation.objects.bulk_create(batch, batch_size=500)
        inserted += len(batch)

    logger.info("Loaded %s stations (%s skipped)", inserted, skipped)
    return inserted
