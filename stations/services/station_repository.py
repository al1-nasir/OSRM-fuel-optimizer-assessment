"""
Station repository — spatial queries against PostGIS for finding
stations near a driving route.
"""

from django.contrib.gis.geos import GEOSGeometry, LineString
from django.contrib.gis.measure import D

from stations.models import FuelStation


def find_stations_near_route(
    route_geometry: dict,
    corridor_miles: float = 5.0,
) -> list[FuelStation]:
    """
    Find geocoded fuel stations within *corridor_miles* of the
    supplied GeoJSON route geometry.

    Args:
        route_geometry: GeoJSON dict with type "LineString" and coordinates.
        corridor_miles: Buffer distance in miles from the route line.

    Returns:
        QuerySet of FuelStation objects with location within the corridor.
    """
    # Convert GeoJSON to PostGIS LineString
    geojson_str = _geojson_to_wkt_input(route_geometry)
    route_line = GEOSGeometry(geojson_str, srid=4326)

    # Simplify line slightly to accelerate PostGIS spatial index query without losing accuracy
    if route_line.num_points > 1000:
        route_line = route_line.simplify(0.0005, preserve_topology=True)

    # Query stations within the corridor
    stations = (
        FuelStation.objects
        .filter(
            geocode_status="success",
            location__isnull=False,
            location__distance_lte=(route_line, D(mi=corridor_miles)),
        )
        .order_by("retail_price")
    )

    return list(stations)


def _geojson_to_wkt_input(geojson: dict) -> str:
    """Convert a GeoJSON geometry dict to a GeoJSON string for GEOSGeometry."""
    import json
    return json.dumps(geojson)
