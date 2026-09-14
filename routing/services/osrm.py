"""Thin OSRM client for a single driving route between two lon/lat points."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

import requests
from django.conf import settings

logger = logging.getLogger(__name__)

METERS_PER_MILE = 1609.344


@dataclass
class RouteGeometry:
    """Decoded route: distance in miles, duration seconds, lon/lat polyline."""

    distance_miles: float
    duration_seconds: float
    # List of [lon, lat] along the route (GeoJSON order)
    coordinates: list[list[float]] = field(default_factory=list)
    # Cumulative distance (miles) at each coordinate index
    cumulative_miles: list[float] = field(default_factory=list)
    raw_geometry: dict[str, Any] | None = None

    def as_geojson(self) -> dict[str, Any]:
        return {
            "type": "LineString",
            "coordinates": self.coordinates,
        }


class RoutingError(Exception):
    pass


def _haversine_miles(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    """Great-circle distance in miles between two WGS84 points."""
    from math import asin, cos, radians, sin, sqrt

    r = 3958.7613  # Earth radius miles
    dlon = radians(lon2 - lon1)
    dlat = radians(lat2 - lat1)
    a = (
        sin(dlat / 2) ** 2
        + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon / 2) ** 2
    )
    return 2 * r * asin(sqrt(a))


def _build_cumulative_miles(coords: list[list[float]]) -> list[float]:
    if not coords:
        return []
    cum = [0.0]
    total = 0.0
    for i in range(1, len(coords)):
        lon1, lat1 = coords[i - 1]
        lon2, lat2 = coords[i]
        total += _haversine_miles(lon1, lat1, lon2, lat2)
        cum.append(total)
    return cum


def fetch_route(
    start_lon: float,
    start_lat: float,
    end_lon: float,
    end_lat: float,
) -> RouteGeometry:
    """
    Request one driving route from the public OSRM demo server.

    Uses overview=full + geometries=geojson so we get a usable polyline
    in a single HTTP call.
    """
    base = settings.OSRM_BASE_URL
    coords = f"{start_lon},{start_lat};{end_lon},{end_lat}"
    url = f"{base}/route/v1/driving/{coords}"
    params = {
        "overview": "full",
        "geometries": "geojson",
        "steps": "false",
    }
    try:
        resp = requests.get(url, params=params, timeout=45)
        resp.raise_for_status()
        payload = resp.json()
    except requests.RequestException as exc:
        raise RoutingError(f"OSRM request failed: {exc}") from exc

    if payload.get("code") != "Ok" or not payload.get("routes"):
        raise RoutingError(f"OSRM returned no route: {payload.get('code')}")

    route = payload["routes"][0]
    geometry = route.get("geometry") or {}
    coordinates = geometry.get("coordinates") or []
    if len(coordinates) < 2:
        raise RoutingError("OSRM geometry too short")

    distance_miles = float(route.get("distance", 0.0)) / METERS_PER_MILE
    duration_seconds = float(route.get("duration", 0.0))
    cumulative = _build_cumulative_miles(coordinates)

    # Prefer OSRM's reported distance for the total; rescale cumulative to match.
    if cumulative and cumulative[-1] > 0 and distance_miles > 0:
        scale = distance_miles / cumulative[-1]
        cumulative = [c * scale for c in cumulative]

    return RouteGeometry(
        distance_miles=distance_miles,
        duration_seconds=duration_seconds,
        coordinates=coordinates,
        cumulative_miles=cumulative,
        raw_geometry=geometry,
    )


def point_at_distance(route: RouteGeometry, miles: float) -> tuple[float, float]:
    """Return (lon, lat) nearest to the given distance along the route."""
    if not route.coordinates:
        raise RoutingError("Empty route")
    target = max(0.0, min(miles, route.distance_miles))
    cum = route.cumulative_miles
    for i, d in enumerate(cum):
        if d >= target:
            lon, lat = route.coordinates[i]
            return lon, lat
    lon, lat = route.coordinates[-1]
    return lon, lat


def nearest_route_distance_miles(
    route: RouteGeometry,
    lon: float,
    lat: float,
) -> tuple[float, float]:
    """
    Approximate distance along the route of the nearest vertex to (lon, lat).

    Returns (along_route_miles, perpendicular_miles).
    """
    best_i = 0
    best_d = float("inf")
    for i, (clon, clat) in enumerate(route.coordinates):
        d = _haversine_miles(lon, lat, clon, clat)
        if d < best_d:
            best_d = d
            best_i = i
    along = route.cumulative_miles[best_i] if route.cumulative_miles else 0.0
    return along, best_d
