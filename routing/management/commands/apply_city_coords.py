"""Apply data/city_coords.json and GeocodeCache hits onto FuelStation rows."""

from __future__ import annotations

import json
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand

from routing.models import FuelStation, GeocodeCache


class Command(BaseCommand):
    help = "Attach lat/lon to stations from city_coords.json and geocode cache."

    def handle(self, *args, **options):
        path = Path(settings.FUEL_DATA_DIR) / "city_coords.json"
        mapping: dict[str, tuple[float, float]] = {}
        if path.exists():
            raw = json.loads(path.read_text(encoding="utf-8"))
            for key, pair in raw.items():
                mapping[key.lower()] = (float(pair[0]), float(pair[1]))

        for row in GeocodeCache.objects.filter(found=True):
            if row.latitude is None or row.longitude is None:
                continue
            mapping[row.query.lower()] = (row.latitude, row.longitude)

        updated = 0
        for station in FuelStation.objects.all().iterator():
            key = f"{station.city}, {station.state}".lower()
            coords = mapping.get(key)
            if not coords:
                continue
            lat, lon = coords
            if station.latitude == lat and station.longitude == lon:
                continue
            station.latitude = lat
            station.longitude = lon
            station.save(update_fields=["latitude", "longitude"])
            updated += 1

        self.stdout.write(self.style.SUCCESS(f"Updated coordinates on {updated} stations"))
