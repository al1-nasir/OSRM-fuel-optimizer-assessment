"""
Cache service — manages caching for fuel plans, routes, and geocodes.
"""

import hashlib
import json
import logging
from decimal import Decimal

from django.conf import settings
from django.core.cache import cache

logger = logging.getLogger(__name__)


class DecimalEncoder(json.JSONEncoder):
    """JSON encoder that handles Decimal values."""

    def default(self, obj):
        if isinstance(obj, Decimal):
            return str(obj)
        return super().default(obj)


def get_fuel_plan_cache_key(
    start_lat: float,
    start_lng: float,
    finish_lat: float,
    finish_lng: float,
    starting_fuel_gallons: float,
    corridor_miles: float,
) -> str:
    """Build a deterministic cache key for a fuel plan."""
    raw = (
        f"{start_lat:.6f},{start_lng:.6f}|"
        f"{finish_lat:.6f},{finish_lng:.6f}|"
        f"fuel={starting_fuel_gallons:.2f}|"
        f"corridor={corridor_miles:.1f}"
    )
    h = hashlib.md5(raw.encode()).hexdigest()
    return f"fuel_plan:{h}"


def get_cached_fuel_plan(cache_key: str) -> dict | None:
    """Retrieve a cached fuel plan."""
    return cache.get(cache_key)


def cache_fuel_plan(cache_key: str, plan_data: dict) -> None:
    """Cache a fuel plan response."""
    cfg = settings.FUEL_PLANNER
    cache.set(cache_key, plan_data, timeout=cfg["FUEL_PLAN_CACHE_TIMEOUT"])
    logger.debug("Cached fuel plan: %s", cache_key)
