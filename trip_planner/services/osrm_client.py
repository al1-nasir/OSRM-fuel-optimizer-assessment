"""
OSRM client — fetches driving routes from the public OSRM demo server.

Returns distance (miles), duration (minutes), and full GeoJSON geometry.
Results are cached so repeated trips do not call the external service.

⚠  The public OSRM server is acceptable for a coding exercise but is NOT
   suitable for production.  A production system must self-host OSRM or use
   a managed routing provider.
"""

import hashlib
import logging

import httpx
from django.conf import settings
from django.core.cache import cache

logger = logging.getLogger(__name__)

METERS_PER_MILE = 1609.344


class OSRMError(Exception):
    """Raised when the OSRM request fails or returns an unusable response."""

    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(message)


def get_route(
    start_lat: float,
    start_lng: float,
    finish_lat: float,
    finish_lng: float,
) -> dict:
    """
    Fetch a driving route from OSRM.

    Args:
        start_lat, start_lng: Origin coordinates.
        finish_lat, finish_lng: Destination coordinates.

    Returns:
        {
            "distance_miles": float,
            "estimated_duration_minutes": float,
            "geometry": {
                "type": "LineString",
                "coordinates": [[lng, lat], ...]
            },
        }

    Raises:
        OSRMError: on network failure or bad response.
    """
    cache_key = _cache_key(start_lat, start_lng, finish_lat, finish_lng)

    cached = cache.get(cache_key)
    if cached is not None:
        logger.debug("OSRM cache hit for route")
        return cached

    cfg = settings.FUEL_PLANNER
    base = cfg["OSRM_BASE_URL"].rstrip("/")
    timeout = cfg["OSRM_TIMEOUT_SECONDS"]

    # OSRM expects lng,lat;lng,lat
    coords = f"{start_lng},{start_lat};{finish_lng},{finish_lat}"
    url = f"{base}/route/v1/driving/{coords}"
    params = {
        "overview": "full",
        "geometries": "geojson",
        "steps": "false",
    }

    try:
        with httpx.Client(timeout=timeout) as client:
            response = client.get(url, params=params)
            response.raise_for_status()
    except httpx.TimeoutException:
        raise OSRMError(
            "routing_timeout",
            "Routing service timed out. Please try again.",
        )
    except httpx.HTTPError as e:
        raise OSRMError(
            "routing_error",
            f"Routing service unavailable: {e}",
        )

    data = response.json()

    if data.get("code") != "Ok" or not data.get("routes"):
        raise OSRMError(
            "routing_error",
            f"OSRM returned: {data.get('code', 'unknown')} — "
            f"{data.get('message', 'no routes found')}",
        )

    route = data["routes"][0]
    distance_meters = route["distance"]
    duration_seconds = route["duration"]
    geometry = route["geometry"]

    result = {
        "distance_miles": round(distance_meters / METERS_PER_MILE, 2),
        "estimated_duration_minutes": round(duration_seconds / 60, 1),
        "geometry": geometry,
    }

    # Cache the route
    cache.set(cache_key, result, timeout=cfg["ROUTE_CACHE_TIMEOUT"])
    logger.info(
        "OSRM route: %.1f miles, %.0f min",
        result["distance_miles"],
        result["estimated_duration_minutes"],
    )

    return result


def _cache_key(
    start_lat: float,
    start_lng: float,
    finish_lat: float,
    finish_lng: float,
) -> str:
    """Deterministic cache key for a route between two coordinate pairs."""
    raw = f"{start_lat:.6f},{start_lng:.6f}|{finish_lat:.6f},{finish_lng:.6f}"
    h = hashlib.md5(raw.encode()).hexdigest()
    return f"osrm_route:{h}"
