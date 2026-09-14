"""Nominatim geocoding with aggressive DB + file cache and polite rate limiting."""

from __future__ import annotations

import hashlib
import json
import logging
import re
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests
from django.conf import settings

from routing.models import GeocodeCache

logger = logging.getLogger(__name__)

# Nominatim usage policy: max 1 request/second for shared instances.
_MIN_INTERVAL_SEC = 1.05
_lock = threading.Lock()
_last_request_at = 0.0

# Loose "City, ST" pattern for USA validation at the input layer.
_US_PLACE_RE = re.compile(
    r"^\s*(.+?)\s*,\s*([A-Za-z]{2})\s*$"
)

US_STATE_CODES = {
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA",
    "HI", "ID", "IL", "IN", "IA", "KS", "KY", "LA", "ME", "MD",
    "MA", "MI", "MN", "MS", "MO", "MT", "NE", "NV", "NH", "NJ",
    "NM", "NY", "NC", "ND", "OH", "OK", "OR", "PA", "RI", "SC",
    "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV", "WI", "WY",
    "DC",
}


@dataclass(frozen=True)
class GeoPoint:
    latitude: float
    longitude: float
    display_name: str = ""
    country_code: str = ""

    @property
    def lon_lat(self) -> tuple[float, float]:
        return (self.longitude, self.latitude)


class GeocodingError(Exception):
    pass


class USAValidationError(GeocodingError):
    pass


def normalize_place_query(place: str) -> str:
    return " ".join(place.strip().split())


def parse_us_city_state(place: str) -> tuple[str, str] | None:
    """Return (city, STATE) if the string looks like 'City, ST' with a US code."""
    m = _US_PLACE_RE.match(place or "")
    if not m:
        return None
    city, state = m.group(1).strip(), m.group(2).strip().upper()
    if state not in US_STATE_CODES:
        return None
    return city, state


def assert_usa_place_format(place: str) -> tuple[str, str]:
    parsed = parse_us_city_state(place)
    if not parsed:
        raise USAValidationError(
            f"Expected a US place like 'Los Angeles, CA'. Got: {place!r}"
        )
    return parsed


def _file_cache_path(query: str) -> Path:
    cache_dir = Path(settings.GEOCODE_CACHE_DIR)
    cache_dir.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(query.encode("utf-8")).hexdigest()[:40]
    return cache_dir / f"{digest}.json"


def _read_file_cache(query: str) -> dict[str, Any] | None:
    path = _file_cache_path(query)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _write_file_cache(query: str, payload: dict[str, Any]) -> None:
    path = _file_cache_path(query)
    try:
        path.write_text(json.dumps(payload), encoding="utf-8")
    except OSError as exc:
        logger.warning("Could not write geocode file cache: %s", exc)


def _rate_limit() -> None:
    global _last_request_at
    with _lock:
        now = time.monotonic()
        wait = _MIN_INTERVAL_SEC - (now - _last_request_at)
        if wait > 0:
            time.sleep(wait)
        _last_request_at = time.monotonic()


def _nominatim_search(query: str, *, countrycodes: str = "us") -> list[dict]:
    _rate_limit()
    url = f"{settings.NOMINATIM_BASE_URL}/search"
    headers = {"User-Agent": settings.NOMINATIM_USER_AGENT}
    params = {
        "q": query,
        "format": "json",
        "limit": 1,
        "countrycodes": countrycodes,
        "addressdetails": 1,
    }
    resp = requests.get(url, params=params, headers=headers, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    if not isinstance(data, list):
        return []
    return data


def _point_from_result(result: dict) -> GeoPoint:
    address = result.get("address") or {}
    country_code = (address.get("country_code") or "").lower()
    return GeoPoint(
        latitude=float(result["lat"]),
        longitude=float(result["lon"]),
        display_name=result.get("display_name") or "",
        country_code=country_code,
    )


def geocode(place: str, *, require_usa: bool = True) -> GeoPoint:
    """
    Resolve a place string to coordinates.

    Cache order: Django GeocodeCache table, then on-disk JSON, then Nominatim.
    """
    query = normalize_place_query(place)
    if not query:
        raise GeocodingError("Empty geocode query")

    if require_usa:
        assert_usa_place_format(query)

    # 1) DB cache
    cached = GeocodeCache.objects.filter(query__iexact=query).first()
    if cached is not None:
        if not cached.found or cached.latitude is None or cached.longitude is None:
            raise GeocodingError(f"No geocode result for {query!r}")
        if require_usa and cached.country_code and cached.country_code.lower() != "us":
            raise USAValidationError(f"Place is outside the USA: {query!r}")
        return GeoPoint(
            latitude=cached.latitude,
            longitude=cached.longitude,
            display_name=cached.display_name,
            country_code=cached.country_code,
        )

    # 2) File cache
    file_hit = _read_file_cache(query.lower())
    if file_hit is not None:
        if not file_hit.get("found"):
            GeocodeCache.objects.update_or_create(
                query=query,
                defaults={
                    "found": False,
                    "latitude": None,
                    "longitude": None,
                    "display_name": "",
                    "country_code": "",
                },
            )
            raise GeocodingError(f"No geocode result for {query!r}")
        point = GeoPoint(
            latitude=float(file_hit["latitude"]),
            longitude=float(file_hit["longitude"]),
            display_name=file_hit.get("display_name") or "",
            country_code=file_hit.get("country_code") or "",
        )
        GeocodeCache.objects.update_or_create(
            query=query,
            defaults={
                "found": True,
                "latitude": point.latitude,
                "longitude": point.longitude,
                "display_name": point.display_name,
                "country_code": point.country_code,
            },
        )
        if require_usa and point.country_code and point.country_code != "us":
            raise USAValidationError(f"Place is outside the USA: {query!r}")
        return point

    # 3) Live Nominatim
    try:
        results = _nominatim_search(query, countrycodes="us" if require_usa else "")
    except requests.RequestException as exc:
        raise GeocodingError(f"Geocoding request failed: {exc}") from exc

    if not results:
        payload = {"found": False}
        _write_file_cache(query.lower(), payload)
        GeocodeCache.objects.update_or_create(
            query=query,
            defaults={"found": False},
        )
        raise GeocodingError(f"No geocode result for {query!r}")

    point = _point_from_result(results[0])
    if require_usa and point.country_code and point.country_code != "us":
        raise USAValidationError(f"Place is outside the USA: {query!r}")

    payload = {
        "found": True,
        "latitude": point.latitude,
        "longitude": point.longitude,
        "display_name": point.display_name,
        "country_code": point.country_code,
    }
    _write_file_cache(query.lower(), payload)
    GeocodeCache.objects.update_or_create(
        query=query,
        defaults={
            "found": True,
            "latitude": point.latitude,
            "longitude": point.longitude,
            "display_name": point.display_name,
            "country_code": point.country_code,
        },
    )
    return point


def geocode_city_state(city: str, state: str) -> GeoPoint | None:
    """Best-effort geocode for a station city. Returns None on failure."""
    query = f"{city}, {state}"
    try:
        return geocode(query, require_usa=True)
    except (GeocodingError, USAValidationError) as exc:
        logger.debug("City geocode miss for %s: %s", query, exc)
        return None
