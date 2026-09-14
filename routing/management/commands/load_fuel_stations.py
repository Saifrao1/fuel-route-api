from django.core.management.base import BaseCommand

from routing.services.fuel_loader import load_fuel_stations, resolve_csv_path


class Command(BaseCommand):
    help = "Load truck stop fuel prices from CSV into the database."

    def add_arguments(self, parser):
        parser.add_argument(
            "--sample",
            action="store_true",
            help="Force the smaller sample CSV even if the full file exists.",
        )
        parser.add_argument(
            "--clear",
            action="store_true",
            help="Delete existing FuelStation rows before loading.",
        )
        parser.add_argument(
            "--path",
            type=str,
            default=None,
            help="Optional explicit CSV path.",
        )

    def handle(self, *args, **options):
        from pathlib import Path

        path = Path(options["path"]) if options["path"] else None
        if path is None:
            path = resolve_csv_path(prefer_sample=options["sample"])
        count = load_fuel_stations(
            path,
            clear_existing=options["clear"],
            prefer_sample=options["sample"],
        )
        self.stdout.write(self.style.SUCCESS(f"Loaded {count} stations from {path}"))
