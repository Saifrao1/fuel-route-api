"""Rate-limited Nominatim warmup for unique station city/state pairs."""

from __future__ import annotations

from django.core.management.base import BaseCommand
from django.db.models import Count

from routing.models import FuelStation
from routing.services.geocoding import GeocodingError, geocode


class Command(BaseCommand):
    help = (
        "Geocode unique station cities (polite Nominatim rate limit). "
        "Then run apply_city_coords to copy results onto stations."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--limit",
            type=int,
            default=0,
            help="Optional max number of unique cities to geocode (0 = all).",
        )
        parser.add_argument(
            "--state",
            type=str,
            default="",
            help="Optional two-letter state filter.",
        )

    def handle(self, *args, **options):
        qs = FuelStation.objects.all()
        state = (options["state"] or "").strip().upper()
        if state:
            qs = qs.filter(state=state)

        pairs = (
            qs.values("city", "state")
            .annotate(n=Count("id"))
            .order_by("state", "city")
        )
        limit = options["limit"]
        done = 0
        ok = 0
        fail = 0
        for row in pairs:
            if limit and done >= limit:
                break
            query = f"{row['city']}, {row['state']}"
            done += 1
            try:
                point = geocode(query, require_usa=True)
                self.stdout.write(f"OK {query} -> {point.latitude}, {point.longitude}")
                ok += 1
            except GeocodingError as exc:
                self.stderr.write(f"MISS {query}: {exc}")
                fail += 1

        self.stdout.write(
            self.style.SUCCESS(f"Geocoded {ok} cities ({fail} misses, {done} attempted)")
        )
        self.stdout.write("Next: python manage.py apply_city_coords")
