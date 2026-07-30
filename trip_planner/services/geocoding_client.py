"""
Geocoding client — resolves user-supplied US location strings to coordinates.

Uses the US Census single-address geocoder endpoint.
Results are cached to avoid redundant calls for repeated locations.
"""

import hashlib
import logging
from urllib.parse import quote

import httpx
from django.conf import settings
from django.core.cache import cache

logger = logging.getLogger(__name__)


class GeocodingError(Exception):
    """Base error for geocoding failures."""

    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(message)


def geocode_location(address: str) -> dict:
    """
    Geocode a US location string to latitude/longitude.

    Args:
        address: Free-text US location (e.g. "Dallas, TX").

    Returns:
        {"latitude": float, "longitude": float, "matched_address": str}

    Raises:
        GeocodingError: with code "invalid_location" or "ambiguous_location".
    """
    normalized = _normalize_address(address)
    cache_key = _cache_key(normalized)

    # Check cache first
    cached = cache.get(cache_key)
    if cached is not None:
        logger.debug("Geocode cache hit for %s", normalized)
        return cached

    cfg = settings.FUEL_PLANNER

    url = cfg["CENSUS_GEOCODER_URL"]
    params = {
        "format": "json",
        "benchmark": "Public_AR_Current",
        "address": normalized,
    }

    try:
        with httpx.Client(
            timeout=cfg["CENSUS_GEOCODER_TIMEOUT_SECONDS"]
        ) as client:
            response = client.get(url, params=params)
            response.raise_for_status()
    except httpx.TimeoutException:
        raise GeocodingError(
            "geocoding_timeout",
            f"Geocoding service timed out for '{address}'.",
        )
    except httpx.HTTPError as e:
        raise GeocodingError(
            "geocoding_error",
            f"Geocoding service error for '{address}': {e}",
        )

    data = response.json()
    matches = data.get("result", {}).get("addressMatches", [])

    if len(matches) == 1:
        match = matches[0]
        coords = match.get("coordinates", {})
        lat = coords.get("y")
        lng = coords.get("x")
        if lat and lng and (17.0 <= lat <= 72.0 and -180.0 <= lng <= -65.0):
            result = {
                "latitude": lat,
                "longitude": lng,
                "matched_address": match.get("matchedAddress", ""),
            }
            cache.set(cache_key, result, timeout=cfg["GEOCODE_CACHE_TIMEOUT"])
            logger.info("Geocoded via Census '%s' → (%s, %s)", address, lat, lng)
            return result

    # ----- Fallback to Photon API if Census fails or returns 0/multiple matches -----
    try:
        with httpx.Client(timeout=10.0, headers={"User-Agent": "Mozilla/5.0"}) as client:
            resp = client.get(
                "https://photon.komoot.io/api/",
                params={"q": normalized, "limit": 1},
            )
            resp.raise_for_status()
            pdata = resp.json()
            features = pdata.get("features", [])
            if features:
                coords = features[0].get("geometry", {}).get("coordinates", [])
                if len(coords) >= 2:
                    lng, lat = float(coords[0]), float(coords[1])
                    if 17.0 <= lat <= 72.0 and -180.0 <= lng <= -65.0:
                        props = features[0].get("properties", {})
                        disp = props.get("name") or normalized
                        result = {
                            "latitude": lat,
                            "longitude": lng,
                            "matched_address": disp,
                        }
                        cache.set(cache_key, result, timeout=cfg["GEOCODE_CACHE_TIMEOUT"])
                        logger.info("Geocoded via Photon '%s' → (%s, %s)", address, lat, lng)
                        return result
    except Exception as e:
        logger.warning("Photon fallback failed for '%s': %s", address, e)

    raise GeocodingError(
        "invalid_location",
        f"No match found for '{address}'. Please provide a valid US location.",
    )


def _normalize_address(address: str) -> str:
    """Normalize whitespace and case for consistent caching."""
    import re
    return re.sub(r"\s+", " ", address.strip())


def _cache_key(normalized_address: str) -> str:
    """Generate a deterministic cache key for a geocoded address."""
    h = hashlib.md5(normalized_address.lower().encode()).hexdigest()
    return f"geocode:{h}"
