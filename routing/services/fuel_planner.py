"""
Cost-aware fuel stop planner for a fixed driving route.

Vehicle assumptions (defaults): 500 mile tank range, 10 mpg.
Stations are matched to the route corridor using cached coordinates only
so a live request stays within start/end geocode + one OSRM call.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from decimal import Decimal
from typing import Iterable, Sequence

from django.conf import settings

from routing.models import FuelStation
from routing.services.osrm import RouteGeometry, nearest_route_distance_miles

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class StationOnRoute:
    station: FuelStation
    along_miles: float
    offset_miles: float
    price: float


@dataclass(frozen=True)
class FuelStop:
    opis_id: int
    name: str
    city: str
    state: str
    retail_price: float
    latitude: float
    longitude: float
    along_miles: float
    gallons: float
    cost_usd: float


@dataclass
class FuelPlan:
    stops: list[FuelStop]
    total_fuel_cost_usd: float
    total_gallons: float
    route_distance_miles: float
    mpg: float
    max_range_miles: float


class FuelPlanError(Exception):
    pass


def _as_float(value: Decimal | float | int) -> float:
    return float(value)


def project_stations_onto_route(
    route: RouteGeometry,
    stations: Iterable[FuelStation],
    *,
    corridor_miles: float | None = None,
) -> list[StationOnRoute]:
    """Keep stations that already have coordinates and sit near the polyline."""
    corridor = (
        corridor_miles
        if corridor_miles is not None
        else float(settings.STATION_CORRIDOR_MILES)
    )
    projected: list[StationOnRoute] = []
    for station in stations:
        if station.latitude is None or station.longitude is None:
            continue
        along, offset = nearest_route_distance_miles(
            route, station.longitude, station.latitude
        )
        if offset > corridor:
            continue
        if along < -1.0 or along > route.distance_miles + 1.0:
            continue
        projected.append(
            StationOnRoute(
                station=station,
                along_miles=max(0.0, min(along, route.distance_miles)),
                offset_miles=offset,
                price=_as_float(station.retail_price),
            )
        )
    projected.sort(key=lambda s: (s.along_miles, s.price))
    return projected


def _make_stop(chosen: StationOnRoute, gallons: float) -> FuelStop:
    st = chosen.station
    cost = gallons * chosen.price
    return FuelStop(
        opis_id=st.opis_id,
        name=st.name,
        city=st.city,
        state=st.state,
        retail_price=chosen.price,
        latitude=float(st.latitude),
        longitude=float(st.longitude),
        along_miles=round(chosen.along_miles, 2),
        gallons=round(gallons, 3),
        cost_usd=round(cost, 2),
    )


def plan_fuel_stops(
    route: RouteGeometry,
    stations_on_route: Sequence[StationOnRoute],
    *,
    max_range_miles: float | None = None,
    mpg: float | None = None,
    start_fuel_miles: float | None = None,
) -> FuelPlan:
    """
    Greedy cost-effective refuel plan.

    Assumptions:
    - Depart with a full tank unless start_fuel_miles is provided.
    - Initial tank is priced at the cheapest station in the first
      max_range miles of corridor (treated as the pre-trip fill location).
    - While the destination is not reachable on remaining fuel, stop at the
      cheapest station in the reachable window, fill the tank, and continue.

    total_fuel_cost_usd is the sum of the initial fill (if priced) and all
    on-route refill purchases. Gallons not burned past the destination stay
    in the tank; we still count purchase cost (realistic for fill-ups).
    """
    max_range = float(max_range_miles or settings.VEHICLE_MAX_RANGE_MILES)
    mpg_val = float(mpg or settings.VEHICLE_MPG)
    if max_range <= 0 or mpg_val <= 0:
        raise FuelPlanError("Vehicle range and mpg must be positive")

    dest = float(route.distance_miles)
    if dest <= 0:
        return FuelPlan([], 0.0, 0.0, 0.0, mpg_val, max_range)

    ahead = list(stations_on_route)
    stops: list[FuelStop] = []
    total_cost = 0.0
    total_gallons = 0.0

    if start_fuel_miles is None:
        fuel = max_range
        # Price the departing fuel at the cheapest station in the opening window.
        # Buy a full tank when the trip needs on-route refuels; otherwise buy
        # only what the trip burns.
        initial_window = [s for s in ahead if s.along_miles <= max_range + 1e-6]
        if initial_window:
            best = min(initial_window, key=lambda s: (s.price, s.along_miles))
            if dest <= max_range:
                gallons = dest / mpg_val
            else:
                gallons = max_range / mpg_val
            stop = _make_stop(best, gallons)
            stops.append(stop)
            total_cost += stop.cost_usd
            total_gallons += gallons
        else:
            logger.warning("No station available to price the initial tank fill")
    else:
        fuel = min(float(start_fuel_miles), max_range)

    position = 0.0
    safety = 0
    while position + fuel < dest - 1e-6:
        safety += 1
        if safety > 10_000:
            raise FuelPlanError("Fuel planning failed to converge")

        reach = position + fuel
        window = [s for s in ahead if position < s.along_miles <= reach + 1e-6]
        if not window:
            raise FuelPlanError(
                "Cannot reach destination or next station with current fuel range. "
                f"Stopped near mile {position:.1f} with {fuel:.1f} miles of fuel left."
            )

        best_price = min(s.price for s in window)
        candidates = [s for s in window if abs(s.price - best_price) < 1e-9]
        chosen = max(candidates, key=lambda s: s.along_miles)

        drive = chosen.along_miles - position
        fuel -= drive
        position = chosen.along_miles

        gallons_to_full = (max_range - fuel) / mpg_val
        stop = _make_stop(chosen, gallons_to_full)
        stops.append(stop)
        total_cost += stop.cost_usd
        total_gallons += gallons_to_full
        fuel = max_range

        ahead = [s for s in ahead if s.along_miles > position + 0.05]

    return FuelPlan(
        stops=stops,
        total_fuel_cost_usd=round(total_cost, 2),
        total_gallons=round(total_gallons, 3),
        route_distance_miles=round(dest, 2),
        mpg=mpg_val,
        max_range_miles=max_range,
    )


def build_plan_for_route(
    route: RouteGeometry,
    *,
    corridor_miles: float | None = None,
    max_range_miles: float | None = None,
    mpg: float | None = None,
) -> FuelPlan:
    """Load candidate stations from the DB and plan stops for the route."""
    qs = FuelStation.objects.exclude(latitude__isnull=True).exclude(
        longitude__isnull=True
    )
    projected = project_stations_onto_route(
        route, qs.iterator(chunk_size=1000), corridor_miles=corridor_miles
    )
    if not projected:
        logger.warning("No geocoded stations found near the route corridor")
    return plan_fuel_stops(
        route,
        projected,
        max_range_miles=max_range_miles,
        mpg=mpg,
    )
