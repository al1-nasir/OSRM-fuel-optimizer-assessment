# Fuel Route Planner API

A Django REST API that accepts US start and finish locations, returns the optimal driving route, recommends cost-effective fuel stops from a supplied CSV of truck-stop prices, and calculates total fuel cost for a vehicle with **10 MPG** efficiency and **500-mile maximum range**.

---

## What This Solves

Long-haul trucking across the US requires strategic fuel stops. This API:

1. Takes a start and finish location in the USA
2. Fetches the driving route via OSRM (Open Source Routing Machine)
3. Finds fuel stations near the route using PostGIS spatial queries
4. Runs a **dynamic-programming optimizer** to select minimum-cost fuel stops
5. Returns route geometry, stop details, and total cost — ready for map rendering

---

## Architecture

```
┌──────────────┐     ┌─────────────────────┐     ┌──────────────┐
│   Client     │────▶│  Django REST API     │────▶│  PostgreSQL  │
│  (Postman /  │     │                      │     │  + PostGIS   │
│   Leaflet)   │◀────│  POST /fuel-plan/    │◀────│              │
└──────────────┘     └──────┬──────┬────────┘     └──────────────┘
                            │      │
                    ┌───────▼──┐ ┌─▼────────┐     ┌──────────────┐
                    │  US      │ │  OSRM    │     │    Redis     │
                    │  Census  │ │  Router  │     │    Cache     │
                    │  Geocoder│ │          │     │              │
                    └──────────┘ └──────────┘     └──────────────┘
```

### Request Flow

**One-time preparation:**
```
CSV file → import command → PostgreSQL → batch geocoding → coordinates stored → spatial index
```

**Normal API request:**
```
Client → validate input → geocode origin/destination (cached)
       → fetch OSRM route (cached) → PostGIS spatial query for nearby stations
       → DP fuel optimizer (local computation) → cache and return JSON
```

---

## Technology Stack

| Component | Technology | Rationale |
|-----------|-----------|-----------|
| Language | Python 3.13+ | Assignment requirement |
| Framework | Django 6.0.x | Assignment requirement |
| API Layer | Django REST Framework | Serialization, validation |
| Database | PostgreSQL 16 + PostGIS | Spatial queries for station proximity |
| Cache | Redis 7 | Route/geocode/plan caching |
| HTTP Client | httpx | Async-capable, timeout support |
| Routing | OSRM (public demo) | Free, returns GeoJSON geometry |
| Geocoding | US Census Geocoder | Free, US-focused, batch support |
| Map Demo | Leaflet + OpenStreetMap | Free, interactive |
| Containerization | Docker Compose | Reproducible environment |

---

## Setup Instructions

### Prerequisites

- Docker and Docker Compose
- The fuel prices CSV file

### Quick Start

```bash
# 1. Clone the repository
git clone <repo-url>
cd FUEL_ROUTE_ESTIMATION_ASSESSMENT

# 2. Start all services
docker compose up -d --build

# 3. Run database migrations
docker compose exec web python manage.py migrate

# 4. Import fuel station data
docker compose exec web python manage.py import_fuel_stations fuel-prices-for-be-assessment.csv

# 5. Geocode station addresses (one-time, ~2 minutes)
docker compose exec web python manage.py geocode_fuel_stations --only-missing

# 6. Access the API
# API:  http://localhost:8000/api/v1/trips/fuel-plan/
# Demo: http://localhost:8000/demo/map/
```

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `DEBUG` | `True` | Django debug mode |
| `DATABASE_URL` | `postgis://fuel_user:fuel_password@db:5432/fuel_route_db` | PostGIS connection |
| `REDIS_URL` | `redis://redis:6379/0` | Redis cache connection |
| `OSRM_BASE_URL` | `https://router.project-osrm.org` | OSRM routing endpoint |
| `DJANGO_SECRET_KEY` | (dev default) | Django secret key |
| `ALLOWED_HOSTS` | `*` | Django allowed hosts |

---

## API Endpoint

### `POST /api/v1/trips/fuel-plan/`

#### Request

```json
{
    "start": "Dallas, TX",
    "finish": "Atlanta, GA",
    "starting_fuel_gallons": 50
}
```

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `start` | string | Yes | — | US start location |
| `finish` | string | Yes | — | US finish location |
| `starting_fuel_gallons` | decimal | No | 50 | Fuel at departure (0–50 gal) |

#### Example curl

```bash
curl -X POST http://localhost:8000/api/v1/trips/fuel-plan/ \
  -H "Content-Type: application/json" \
  -d '{"start": "Dallas, TX", "finish": "Atlanta, GA", "starting_fuel_gallons": 50}'
```

#### Success Response (200)

```json
{
    "origin": {"label": "Dallas, TX", "latitude": 32.7767, "longitude": -96.797},
    "destination": {"label": "Atlanta, GA", "latitude": 33.749, "longitude": -84.388},
    "route": {
        "distance_miles": 700.0,
        "estimated_duration_minutes": 630.0,
        "geometry": {"type": "LineString", "coordinates": [[...]]}
    },
    "vehicle": {
        "fuel_efficiency_mpg": 10,
        "maximum_range_miles": 500,
        "tank_capacity_gallons": 50,
        "starting_fuel_gallons": 50
    },
    "fuel_stops": [{
        "sequence": 1,
        "station_id": 321,
        "name": "Example Travel Center",
        "address": "123 Main St, Monroe, LA",
        "route_position_miles": 245.8,
        "distance_from_route_miles": 1.1,
        "price_per_gallon": 3.12,
        "gallons_to_buy": 21.22,
        "cost_usd": 66.21,
        "estimated_arrival_fuel_gallons": 25.42,
        "estimated_departure_fuel_gallons": 46.64
    }],
    "summary": {
        "main_route_miles": 700.0,
        "total_estimated_trip_miles": 702.2,
        "total_route_fuel_used_gallons": 70.22,
        "fuel_purchased_on_route_gallons": 21.22,
        "ending_fuel_gallons": 1.0,
        "total_fuel_cost_on_route_usd": 66.21,
        "currency": "USD"
    }
}
```

#### Error Responses

| Code | HTTP Status | Meaning |
|------|-------------|---------|
| `validation_error` | 400 | Missing/invalid fields |
| `invalid_location` | 400 | Location not found in US |
| `ambiguous_location` | 400 | Multiple geocode matches |
| `same_location` | 400 | Start = finish |
| `no_feasible_fuel_plan` | 400 | Cannot reach destination within range constraints |
| `routing_error` | 503 | OSRM service unavailable |

---

## Management Commands

### Import Fuel Stations

```bash
python manage.py import_fuel_stations path/to/fuel_prices.csv
```

- Reads CSV with exact header mapping
- Deduplicates by OPIS Truckstop ID, keeping the lowest price
- Skips non-US records (Canadian provinces)
- Reports: created, updated, skipped, invalid

### Geocode Fuel Stations

```bash
python manage.py geocode_fuel_stations --only-missing
python manage.py geocode_fuel_stations --retry-failed
```

- Uses US Census Geocoder batch API (up to 10,000 records per call)
- Validates coordinates within US bounding box
- Marks failed/ambiguous geocodes for review

---

## Fuel Optimizer

### Dynamic Programming Solver

The optimizer uses a DP approach over ordered route nodes:

- **State:** `(node_index, fuel_hundredths)` — fuel in 0.01-gallon increments (0–5000)
- **Nodes:** Origin → candidate stations (ordered by route position) → destination
- **Edges:** Node `i` → `j` only if `estimated_leg ≤ 490 miles`
- **Leg formula:** `(p_j - p_i) + d_i + d_j` (route positions + access offsets)
- **Goal:** Minimize total `Decimal` purchase cost

### Constraints

- Vehicle: 10 MPG, 50-gallon tank, 500-mile max range
- Safety reserve: 10 miles (1 gallon) maintained at all times
- Every leg: `estimated_miles ≤ 490`
- Ending fuel: `≥ 1.00 gallon` at destination
- Invariant: `starting_fuel + purchased - consumed = ending_fuel`

---

## Caching Strategy

| Cache Layer | Key Components | TTL |
|-------------|---------------|-----|
| Geocode cache | MD5(normalized address) | 30 days |
| OSRM route cache | MD5(start coords + finish coords) | 1 hour |
| Fuel plan cache | MD5(coords + fuel + corridor) | 30 minutes |

---

## Running Tests

```bash
# Inside Docker
docker compose exec web pytest -v

# Locally (with DB running)
pytest -v
```

---

## Postman Collection

Import `postman_collection.json` into Postman for pre-built requests covering:
- Dallas → Atlanta (medium route)
- Dallas → Austin (short route)
- LA → NYC (long route)
- Partial tank scenario
- Invalid input scenario

---

## Assumptions and Limitations

1. **Vehicle specs:** 50-gallon tank, 10 MPG = 500-mile max range
2. **Starting fuel:** Default 50 gallons (full tank); cost of pre-existing fuel is excluded
3. **Price data:** CSV is a static snapshot, not live prices
4. **Route corridor:** 5-mile buffer from route centerline
5. **Distance model:** Geodesic (haversine) for route positions; access offsets are straight-line, not driving detours
6. **One OSRM call:** Per uncached trip; no per-station routing calls
7. **OSRM demo server:** Acceptable for exercise; **production must self-host or use managed routing**
8. **Stations without coordinates:** Excluded from recommendations until geocoded
9. **US only:** Non-US records in CSV are filtered during import
10. **Safety reserve:** 10-mile / 1-gallon buffer enforced on every leg

---

## Project Structure

```
fuel_route_planner/
├── config/                      # Django project settings
│   ├── settings.py
│   ├── urls.py
│   └── wsgi.py
├── stations/                    # Fuel station data management
│   ├── models.py                # FuelStation model with PostGIS
│   ├── admin.py                 # GIS-enabled admin
│   ├── management/commands/
│   │   ├── import_fuel_stations.py
│   │   └── geocode_fuel_stations.py
│   └── services/
│       └── station_repository.py
├── trip_planner/                # API and business logic
│   ├── serializers.py
│   ├── views.py
│   ├── exceptions.py
│   ├── urls.py
│   ├── demo_views.py
│   └── services/
│       ├── geocoding_client.py
│       ├── osrm_client.py
│       ├── station_search.py
│       ├── fuel_optimizer.py
│       └── cache_service.py
├── templates/demo/map.html      # Leaflet map demo
├── tests/                       # pytest + pytest-django
├── docker-compose.yml
├── Dockerfile
├── requirements.txt
├── postman_collection.json
└── README.md
```
# OSRM-fuel-optimizer-assessment
