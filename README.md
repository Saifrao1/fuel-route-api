# Fuel Route API

Django + Django REST Framework service that plans a USA driving route and
suggests cost-effective fuel stops for a truck with a **500 mile** tank range
and **10 mpg** fuel economy.

## Setup

```bash
cd fuel-route-api
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python manage.py migrate
python manage.py load_fuel_stations --sample --clear
python manage.py apply_city_coords
python manage.py runserver
```

For the full price file (already under `data/`):

```bash
python manage.py load_fuel_stations --clear
python manage.py apply_city_coords
# Optional: geocode additional cities (rate-limited Nominatim)
# python manage.py warmup_geocode --limit 50
# python manage.py apply_city_coords
```

The loader prefers `data/fuel-prices-for-be-assessment.csv` when present,
otherwise it uses `data/fuel-prices-sample.csv`.

## Endpoints

### Health

```bash
curl -s http://127.0.0.1:8000/api/health/
```

### Route (POST)

```bash
curl -s -X POST http://127.0.0.1:8000/api/route/ \
  -H 'Content-Type: application/json' \
  -d '{"start": "Los Angeles, CA", "end": "Las Vegas, NV"}'
```

### Route (GET)

```bash
curl -s 'http://127.0.0.1:8000/api/route/?start=Los%20Angeles,%20CA&end=Las%20Vegas,%20NV'
```

### Example response shape

```json
{
  "start": {"query": "Los Angeles, CA", "latitude": 34.05, "longitude": -118.25},
  "end": {"query": "Las Vegas, NV", "latitude": 36.17, "longitude": -115.14},
  "route": {
    "distance_miles": 270.12,
    "duration_seconds": 15000.0,
    "geometry": {"type": "LineString", "coordinates": [[-118.25, 34.05], ...]}
  },
  "vehicle": {"max_range_miles": 500.0, "mpg": 10.0},
  "fuel_stops": [
    {
      "name": "...",
      "city": "...",
      "state": "CA",
      "retail_price": 3.25,
      "along_miles": 12.4,
      "gallons": 27.0,
      "cost_usd": 87.75
    }
  ],
  "total_fuel_cost_usd": 87.75,
  "total_gallons_purchased": 27.0
}
```

Start and end must look like `City, ST` with a valid US state code.

`422` is an **intentional edge case**, not a crash. Example: `Los Angeles, CA` → `Chicago, IL` on the sample station set. That trip needs on-route refuels, but sample stations leave a range gap along the corridor, so the planner returns `{"detail": "..."}` instead of a fake fuel plan. Load the full CSV if you want long-haul coverage.

## Algorithm notes

1. **Geocode** start and end. Lookup order: SQLite `GeocodeCache`, on-disk
   JSON under `cache/geocode/`, bundled `data/city_coords.json`, then
   Nominatim (`countrycodes=us`). The bundled file keeps common USA places
   working when the public Nominatim endpoint blocks datacenter IPs.
2. **Route** once with public OSRM (`/route/v1/driving/...`, GeoJSON overview).
   That is the only routing HTTP call per request.
3. **Station matching** uses coordinates already attached to `FuelStation`
   rows (from `data/city_coords.json` and/or prior geocode warmup). Live
   requests do not geocode every truck stop. Stations farther than
   `STATION_CORRIDOR_MILES` (default 15) from the polyline are ignored.
4. **Fuel plan** (greedy, cost-aware):
   - Depart with a full tank. Price that fuel at the cheapest station in the
     first `max_range` miles along the corridor. For trips that fit in one
     tank, only buy `distance / mpg` gallons.
   - While the destination is not reachable on remaining fuel, stop at the
     cheapest station inside the reachable window, fill the tank, and continue.
   - `total_fuel_cost_usd` is the sum of those purchases.

External call budget for a cold cache: up to two Nominatim lookups + one OSRM
route (three calls). Warm cache: a single OSRM call.

## Tests

```bash
python manage.py test routing
```

Planner tests use a synthetic route and do not need network access. API tests
mock geocoding and OSRM.

## Configuration

See `.env.example` for `OSRM_BASE_URL`, `NOMINATIM_*`, vehicle range/mpg, and
cache directory settings.

## Network note

The public OSRM and Nominatim demos can rate-limit or block datacenter IPs.
If a live `/api/route/` call fails in your environment, the code path is still
exercised by unit tests, and geocode results persist once a successful lookup
lands in the cache. Retry from a normal network or point `OSRM_BASE_URL` /
`NOMINATIM_BASE_URL` at your own instances.
