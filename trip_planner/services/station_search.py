"""
Station search — projects fuel stations onto the driving route,
calculates route positions and access offsets using geodesic distances.

Uses WGS84 geodesic segment lengths (not geometric fraction × OSRM total).
"""

import math
from dataclasses import dataclass
from decimal import Decimal

from stations.models import FuelStation
from stations.services.station_repository import find_stations_near_route


@dataclass
class CandidateStation:
    """A station projected onto the driving route."""

    station: FuelStation
    route_position_miles: float
    distance_from_route_miles: float
    retail_price: Decimal


# Approximate radius of Earth in miles
EARTH_RADIUS_MILES = 3958.8


def _haversine_miles(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Geodesic distance between two WGS84 points in miles."""
    rlat1, rlng1 = math.radians(lat1), math.radians(lng1)
    rlat2, rlng2 = math.radians(lat2), math.radians(lng2)

    dlat = rlat2 - rlat1
    dlng = rlng2 - rlng1

    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(rlat1) * math.cos(rlat2) * math.sin(dlng / 2) ** 2
    )
    c = 2 * math.asin(math.sqrt(a))
    return EARTH_RADIUS_MILES * c


def _closest_point_on_segment(
    px: float, py: float,
    ax: float, ay: float,
    bx: float, by: float,
) -> tuple[float, float, float]:
    """
    Find the closest point on segment AB to point P in (lng, lat) space.
    Returns (closest_lng, closest_lat, t) where t in [0,1] is the fraction
    along the segment.

    NOTE: This is an approximate projection in lat/lng space. For our corridor
    of ≤5 miles this is adequate.
    """
    dx = bx - ax
    dy = by - ay
    seg_len_sq = dx * dx + dy * dy

    if seg_len_sq < 1e-14:
        return ax, ay, 0.0

    t = ((px - ax) * dx + (py - ay) * dy) / seg_len_sq
    t = max(0.0, min(1.0, t))

    cx = ax + t * dx
    cy = ay + t * dy
    return cx, cy, t


def build_candidate_list(
    route_geometry: dict,
    route_distance_miles: float,
    corridor_miles: float = 5.0,
) -> list[CandidateStation]:
    """
    Find stations near the route and calculate their route positions
    using geodesic segment-by-segment distance accumulation.

    Args:
        route_geometry: GeoJSON LineString from OSRM.
        route_distance_miles: Total OSRM route distance in miles.
        corridor_miles: Buffer distance for station search.

    Returns:
        List of CandidateStation sorted by route_position_miles.
    """
    stations = find_stations_near_route(route_geometry, corridor_miles)

    if not stations:
        return []

    full_coords = route_geometry["coordinates"]  # [[lng, lat], ...]

    # Pre-compute cumulative geodesic distances along full route polyline
    full_seg_cumulative = [0.0]
    for i in range(1, len(full_coords)):
        prev_lng, prev_lat = full_coords[i - 1]
        curr_lng, curr_lat = full_coords[i]
        seg_dist = _haversine_miles(prev_lat, prev_lng, curr_lat, curr_lng)
        full_seg_cumulative.append(full_seg_cumulative[-1] + seg_dist)

    total_geodesic = full_seg_cumulative[-1] if full_seg_cumulative else 1.0
    scale_factor = route_distance_miles / total_geodesic if total_geodesic > 0 else 1.0

    # Downsample segment search step for ultra-fast point snapping on dense long polylines
    step = max(1, len(full_coords) // 1000) if len(full_coords) > 1000 else 1

    candidates = []

    for station in stations:
        if station.location is None:
            continue

        st_lng = station.location.x
        st_lat = station.location.y

        # Find the closest route segment to this station
        best_dist = float("inf")
        best_route_pos = 0.0

        best_sq_dist = float("inf")
        best_i = 0
        best_t = 0.0
        best_cx, best_cy = st_lng, st_lat

        for i in range(0, len(full_coords) - 1, step):
            next_i = min(i + step, len(full_coords) - 1)
            ax, ay = full_coords[i]       # lng, lat
            bx, by = full_coords[next_i]  # lng, lat

            cx, cy, t = _closest_point_on_segment(
                st_lng, st_lat, ax, ay, bx, by
            )

            dx = st_lng - cx
            dy = st_lat - cy
            sq_dist = dx * dx + dy * dy

            if sq_dist < best_sq_dist:
                best_sq_dist = sq_dist
                best_i = i
                best_next_i = next_i
                best_t = t
                best_cx, best_cy = cx, cy

        # Compute geodesic distance only once for the closest segment
        best_dist = _haversine_miles(st_lat, st_lng, best_cy, best_cx)
        seg_length = full_seg_cumulative[best_next_i] - full_seg_cumulative[best_i]
        raw_route_pos = full_seg_cumulative[best_i] + best_t * seg_length
        best_route_pos = raw_route_pos * scale_factor

        candidates.append(CandidateStation(
            station=station,
            route_position_miles=round(best_route_pos, 4),
            distance_from_route_miles=round(best_dist, 4),
            retail_price=station.retail_price,
        ))

    # Sort by route position; tie-break by station ID for determinism
    candidates.sort(
        key=lambda c: (round(c.route_position_miles, 2), c.station.opis_truckstop_id)
    )

    return candidates
