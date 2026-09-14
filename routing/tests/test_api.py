"""API-level tests that avoid live external HTTP calls."""

from unittest.mock import patch

from django.test import TestCase
from rest_framework.test import APIClient

from routing.services.geocoding import GeoPoint
from routing.services.osrm import RouteGeometry


class HealthTests(TestCase):
    def test_health(self):
        client = APIClient()
        resp = client.get("/api/health/")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["status"], "ok")


class RouteApiValidationTests(TestCase):
    def test_missing_params(self):
        client = APIClient()
        resp = client.post("/api/route/", {}, format="json")
        self.assertEqual(resp.status_code, 400)

    def test_rejects_non_usa_format(self):
        client = APIClient()
        resp = client.post(
            "/api/route/",
            {"start": "Toronto, ON", "end": "Chicago, IL"},
            format="json",
        )
        self.assertEqual(resp.status_code, 400)

    @patch("routing.services.route_service.build_plan_for_route")
    @patch("routing.services.route_service.fetch_route")
    @patch("routing.services.route_service.geocode")
    def test_post_route_mocked(self, mock_geocode, mock_fetch, mock_plan):
        from routing.services.fuel_planner import FuelPlan

        mock_geocode.side_effect = [
            GeoPoint(34.05, -118.25, "Los Angeles", "us"),
            GeoPoint(34.15, -117.90, "Nearby", "us"),
        ]
        coords = [[-118.25, 34.05], [-117.90, 34.15]]
        mock_fetch.return_value = RouteGeometry(
            distance_miles=40.0,
            duration_seconds=2400.0,
            coordinates=coords,
            cumulative_miles=[0.0, 40.0],
            raw_geometry={"type": "LineString", "coordinates": coords},
        )
        mock_plan.return_value = FuelPlan(
            stops=[],
            total_fuel_cost_usd=12.5,
            total_gallons=4.0,
            route_distance_miles=40.0,
            mpg=10.0,
            max_range_miles=500.0,
        )

        client = APIClient()
        resp = client.post(
            "/api/route/",
            {"start": "Los Angeles, CA", "end": "Pomona, CA"},
            format="json",
        )
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertIn("route", body)
        self.assertIn("geometry", body["route"])
        self.assertEqual(body["total_fuel_cost_usd"], 12.5)

    @patch("routing.services.route_service.build_plan_for_route")
    @patch("routing.services.route_service.fetch_route")
    @patch("routing.services.route_service.geocode")
    def test_get_route_mocked(self, mock_geocode, mock_fetch, mock_plan):
        from routing.services.fuel_planner import FuelPlan

        mock_geocode.side_effect = [
            GeoPoint(34.05, -118.25, "Los Angeles", "us"),
            GeoPoint(36.17, -115.14, "Las Vegas", "us"),
        ]
        coords = [[-118.25, 34.05], [-115.14, 36.17]]
        mock_fetch.return_value = RouteGeometry(
            distance_miles=270.0,
            duration_seconds=15000.0,
            coordinates=coords,
            cumulative_miles=[0.0, 270.0],
        )
        mock_plan.return_value = FuelPlan(
            stops=[],
            total_fuel_cost_usd=80.0,
            total_gallons=27.0,
            route_distance_miles=270.0,
            mpg=10.0,
            max_range_miles=500.0,
        )
        client = APIClient()
        resp = client.get(
            "/api/route/",
            {"start": "Los Angeles, CA", "end": "Las Vegas, NV"},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["route"]["distance_miles"], 270.0)
