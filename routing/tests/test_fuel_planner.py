"""Unit tests for the fuel planner using a mocked in-memory route."""

from decimal import Decimal

from django.test import SimpleTestCase, TestCase

from routing.models import FuelStation
from routing.services.fuel_planner import (
    FuelPlanError,
    StationOnRoute,
    plan_fuel_stops,
    project_stations_onto_route,
)
from routing.services.geocoding import USAValidationError, assert_usa_place_format
from routing.services.osrm import RouteGeometry, _build_cumulative_miles


def _straight_route(miles: float, steps: int = 20) -> RouteGeometry:
    """
    Synthetic west-to-east route along latitude 35N.
    ~69 miles per degree longitude at this latitude (approx).
    """
    miles_per_deg = 69.0
    start_lon = -118.0
    lat = 35.0
    end_lon = start_lon + (miles / miles_per_deg)
    coords = []
    for i in range(steps + 1):
        t = i / steps
        lon = start_lon + t * (end_lon - start_lon)
        coords.append([lon, lat])
    cum = _build_cumulative_miles(coords)
    # Rescale cumulative to the exact requested distance.
    if cum[-1] > 0:
        scale = miles / cum[-1]
        cum = [c * scale for c in cum]
    return RouteGeometry(
        distance_miles=miles,
        duration_seconds=miles * 60.0,
        coordinates=coords,
        cumulative_miles=cum,
        raw_geometry={"type": "LineString", "coordinates": coords},
    )


def _station(
    *,
    opis_id: int,
    name: str,
    city: str,
    state: str,
    price: str,
    lat: float,
    lon: float,
) -> FuelStation:
    return FuelStation(
        opis_id=opis_id,
        name=name,
        address="test",
        city=city,
        state=state,
        retail_price=Decimal(price),
        latitude=lat,
        longitude=lon,
    )


class USAValidationTests(SimpleTestCase):
    def test_accepts_city_state(self):
        city, state = assert_usa_place_format("Los Angeles, CA")
        self.assertEqual(city, "Los Angeles")
        self.assertEqual(state, "CA")

    def test_rejects_non_us_format(self):
        with self.assertRaises(USAValidationError):
            assert_usa_place_format("Paris, France")

    def test_rejects_bad_state(self):
        with self.assertRaises(USAValidationError):
            assert_usa_place_format("Somewhere, ZZ")


class FuelPlannerTests(SimpleTestCase):
    def test_short_trip_single_priced_fill(self):
        route = _straight_route(200)
        # Station near the start of the route.
        st = _station(
            opis_id=1,
            name="Start Stop",
            city="Testville",
            state="CA",
            price="3.0000",
            lat=35.0,
            lon=-118.0,
        )
        on_route = [
            StationOnRoute(station=st, along_miles=5.0, offset_miles=0.1, price=3.0)
        ]
        plan = plan_fuel_stops(route, on_route, max_range_miles=500, mpg=10)
        self.assertEqual(len(plan.stops), 1)
        self.assertAlmostEqual(plan.total_gallons, 20.0, places=2)
        self.assertAlmostEqual(plan.total_fuel_cost_usd, 60.0, places=2)

    def test_long_trip_prefers_cheaper_station_in_window(self):
        route = _straight_route(900)
        expensive = _station(
            opis_id=1,
            name="Expensive",
            city="A",
            state="CA",
            price="4.0000",
            lat=35.0,
            lon=-117.5,
        )
        cheap = _station(
            opis_id=2,
            name="Cheap",
            city="B",
            state="CA",
            price="2.5000",
            lat=35.0,
            lon=-116.0,
        )
        mid = _station(
            opis_id=3,
            name="Mid",
            city="C",
            state="AZ",
            price="3.0000",
            lat=35.0,
            lon=-112.0,
        )
        far = _station(
            opis_id=4,
            name="Far",
            city="D",
            state="NM",
            price="2.8000",
            lat=35.0,
            lon=-108.0,
        )
        stations = [
            StationOnRoute(expensive, 40.0, 0.2, 4.0),
            StationOnRoute(cheap, 180.0, 0.2, 2.5),
            StationOnRoute(mid, 480.0, 0.2, 3.0),
            StationOnRoute(far, 720.0, 0.2, 2.8),
        ]
        plan = plan_fuel_stops(route, stations, max_range_miles=500, mpg=10)
        self.assertGreaterEqual(len(plan.stops), 1)
        # Initial fill should pick the cheapest in the opening 500-mile window.
        self.assertEqual(plan.stops[0].name, "Cheap")
        self.assertEqual(plan.stops[0].retail_price, 2.5)
        self.assertGreater(plan.total_fuel_cost_usd, 0)
        # 900 miles at 10 mpg needs at least 90 gallons purchased overall.
        self.assertGreaterEqual(plan.total_gallons, 90.0)

    def test_impossible_when_gap_exceeds_range(self):
        route = _straight_route(800)
        only = _station(
            opis_id=1,
            name="Only",
            city="A",
            state="CA",
            price="3.0000",
            lat=35.0,
            lon=-118.0,
        )
        stations = [StationOnRoute(only, 10.0, 0.1, 3.0)]
        # Start with only 50 miles of fuel and no station before empty.
        with self.assertRaises(FuelPlanError):
            plan_fuel_stops(
                route,
                stations,
                max_range_miles=500,
                mpg=10,
                start_fuel_miles=50,
            )

    def test_zero_distance_route(self):
        route = RouteGeometry(
            distance_miles=0.0,
            duration_seconds=0.0,
            coordinates=[[-118.0, 35.0], [-118.0, 35.0]],
            cumulative_miles=[0.0, 0.0],
        )
        plan = plan_fuel_stops(route, [], max_range_miles=500, mpg=10)
        self.assertEqual(plan.total_fuel_cost_usd, 0.0)
        self.assertEqual(plan.stops, [])


class ProjectStationsTests(TestCase):
    def test_filters_by_corridor(self):
        route = _straight_route(100, steps=10)
        near = FuelStation.objects.create(
            opis_id=10,
            name="Near",
            city="Near City",
            state="CA",
            retail_price=Decimal("3.1000"),
            latitude=35.05,
            longitude=-117.5,
        )
        far = FuelStation.objects.create(
            opis_id=11,
            name="Far",
            city="Far City",
            state="CA",
            retail_price=Decimal("2.9000"),
            latitude=40.0,
            longitude=-117.5,
        )
        projected = project_stations_onto_route(
            route, [near, far], corridor_miles=20
        )
        names = {p.station.name for p in projected}
        self.assertIn("Near", names)
        self.assertNotIn("Far", names)
