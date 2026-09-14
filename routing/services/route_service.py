"""Orchestrate geocode -> OSRM -> fuel plan for a USA start/end pair."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from routing.services.fuel_planner import FuelPlanError, build_plan_for_route
from routing.services.geocoding import (
    GeocodingError,
    USAValidationError,
    assert_usa_place_format,
    geocode,
)
from routing.services.osrm import RoutingError, fetch_route


class RouteServiceError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def compute_route(start: str, end: str) -> dict[str, Any]:
    """
    End-to-end route + fuel plan.

    External calls (cache permitting):
      1-2) Nominatim for start and end (skipped on cache hit)
      3) One OSRM driving route request
    Station coordinates come from local cache / city_coords.json only.
    """
    start = (start or "").strip()
    end = (end or "").strip()
    if not start or not end:
        raise RouteServiceError("Both 'start' and 'end' are required")

    try:
        assert_usa_place_format(start)
        assert_usa_place_format(end)
    except USAValidationError as exc:
        raise RouteServiceError(str(exc), status=400) from exc

    try:
        start_pt = geocode(start, require_usa=True)
        end_pt = geocode(end, require_usa=True)
    except USAValidationError as exc:
        raise RouteServiceError(str(exc), status=400) from exc
    except GeocodingError as exc:
        raise RouteServiceError(str(exc), status=502) from exc

    try:
        route = fetch_route(
            start_pt.longitude,
            start_pt.latitude,
            end_pt.longitude,
            end_pt.latitude,
        )
    except RoutingError as exc:
        raise RouteServiceError(str(exc), status=502) from exc

    try:
        plan = build_plan_for_route(route)
    except FuelPlanError as exc:
        raise RouteServiceError(str(exc), status=422) from exc

    return {
        "start": {
            "query": start,
            "latitude": start_pt.latitude,
            "longitude": start_pt.longitude,
            "display_name": start_pt.display_name,
        },
        "end": {
            "query": end,
            "latitude": end_pt.latitude,
            "longitude": end_pt.longitude,
            "display_name": end_pt.display_name,
        },
        "route": {
            "distance_miles": round(route.distance_miles, 2),
            "duration_seconds": round(route.duration_seconds, 1),
            "geometry": route.as_geojson(),
        },
        "vehicle": {
            "max_range_miles": plan.max_range_miles,
            "mpg": plan.mpg,
        },
        "fuel_stops": [asdict(s) for s in plan.stops],
        "total_fuel_cost_usd": plan.total_fuel_cost_usd,
        "total_gallons_purchased": plan.total_gallons,
    }
