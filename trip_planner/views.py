"""
Fuel Plan API view — POST /api/v1/trips/fuel-plan/

Orchestrates the complete fuel-planning pipeline:
1. Validate input
2. Geocode origin and destination
3. Fetch driving route from OSRM (cached)
4. Find stations near the route via PostGIS
5. Project stations onto the route
6. Run DP fuel optimizer
7. Cache and return the plan
"""

import logging
from decimal import Decimal, ROUND_HALF_UP

from django.conf import settings
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from trip_planner.exceptions import DomainError
from trip_planner.serializers import FuelPlanRequestSerializer
from trip_planner.services.cache_service import (
    cache_fuel_plan,
    get_cached_fuel_plan,
    get_fuel_plan_cache_key,
)
from trip_planner.services.fuel_optimizer import optimize_fuel_plan
from trip_planner.services.geocoding_client import GeocodingError, geocode_location
from trip_planner.services.osrm_client import OSRMError, get_route
from trip_planner.services.station_search import build_candidate_list

logger = logging.getLogger(__name__)


class FuelPlanView(APIView):
    """
    POST /api/v1/trips/fuel-plan/

    Accepts start and finish US locations, returns a driving route
    with cost-optimised fuel stops.
    """

    def post(self, request):
        # ----- 1. Validate input -------------------------------------------
        serializer = FuelPlanRequestSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(
                {
                    "error": {
                        "code": "validation_error",
                        "message": str(serializer.errors),
                    }
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        data = serializer.validated_data
        start_label = data["start"]
        finish_label = data["finish"]
        starting_fuel = float(data["starting_fuel_gallons"])

        # ----- 2. Geocode origin and destination ---------------------------
        try:
            origin = geocode_location(start_label)
        except GeocodingError as e:
            raise DomainError(e.code, f"Start location: {e.message}")

        try:
            destination = geocode_location(finish_label)
        except GeocodingError as e:
            raise DomainError(e.code, f"Finish location: {e.message}")

        # Check same effective location
        if (
            abs(origin["latitude"] - destination["latitude"]) < 0.001
            and abs(origin["longitude"] - destination["longitude"]) < 0.001
        ):
            raise DomainError(
                "same_location",
                "Start and finish resolve to the same location.",
            )

        cfg = settings.FUEL_PLANNER
        corridor = cfg["ROUTE_CORRIDOR_MILES"]

        # ----- 3. Check fuel plan cache ------------------------------------
        cache_key = get_fuel_plan_cache_key(
            origin["latitude"],
            origin["longitude"],
            destination["latitude"],
            destination["longitude"],
            starting_fuel,
            corridor,
        )
        cached_plan = get_cached_fuel_plan(cache_key)
        if cached_plan is not None:
            logger.info("Returning cached fuel plan")
            return Response(cached_plan)

        # ----- 4. Get driving route from OSRM ------------------------------
        try:
            route = get_route(
                origin["latitude"],
                origin["longitude"],
                destination["latitude"],
                destination["longitude"],
            )
        except OSRMError as e:
            raise DomainError(e.code, e.message, http_status=503)

        # ----- 5. Find stations near the route -----------------------------
        candidates = build_candidate_list(
            route["geometry"],
            route["distance_miles"],
            corridor_miles=corridor,
        )

        # ----- 6. Run fuel optimizer DP ------------------------------------
        fuel_plan = optimize_fuel_plan(
            candidates=candidates,
            route_distance_miles=route["distance_miles"],
            starting_fuel_gallons=starting_fuel,
        )

        # Dynamic fallback: if standard corridor is too sparse, expand to 10 miles
        if not fuel_plan.feasible and corridor < 10.0:
            logger.info("Retrying station search with expanded 10-mile corridor")
            candidates = build_candidate_list(
                route["geometry"],
                route["distance_miles"],
                corridor_miles=10.0,
            )
            fuel_plan = optimize_fuel_plan(
                candidates=candidates,
                route_distance_miles=route["distance_miles"],
                starting_fuel_gallons=starting_fuel,
            )

        if not fuel_plan.feasible:
            raise DomainError(
                fuel_plan.error_code,
                fuel_plan.error_message,
            )

        # ----- 7. Build response -------------------------------------------
        response_data = self._build_response(
            origin=origin,
            destination=destination,
            start_label=start_label,
            finish_label=finish_label,
            route=route,
            fuel_plan=fuel_plan,
            starting_fuel=starting_fuel,
            cfg=cfg,
        )

        # Cache the response
        cache_fuel_plan(cache_key, response_data)

        return Response(response_data)

    def _build_response(
        self,
        origin: dict,
        destination: dict,
        start_label: str,
        finish_label: str,
        route: dict,
        fuel_plan,
        starting_fuel: float,
        cfg: dict,
    ) -> dict:
        """Build the full API response JSON."""

        stops = []
        for s in fuel_plan.stops:
            stops.append({
                "sequence": s.sequence,
                "station_id": s.station_id,
                "name": s.name,
                "address": s.address,
                "city": s.city,
                "state": s.state,
                "location": {
                    "latitude": s.latitude,
                    "longitude": s.longitude,
                },
                "route_position_miles": s.route_position_miles,
                "distance_from_route_miles": s.distance_from_route_miles,
                "price_per_gallon": float(s.price_per_gallon),
                "gallons_to_buy": float(s.gallons_to_buy),
                "cost_usd": float(s.cost_usd),
                "estimated_arrival_fuel_gallons": float(s.estimated_arrival_fuel_gallons),
                "estimated_departure_fuel_gallons": float(s.estimated_departure_fuel_gallons),
                "incoming_leg_miles": s.incoming_leg_miles,
                "outgoing_leg_miles": s.outgoing_leg_miles,
            })

        op = fuel_plan.origin_purchase
        origin_purchase = {
            "required": op.required,
            "station": {
                "station_id": op.station_id,
                "name": op.name,
                "address": op.address,
                "location": {
                    "latitude": op.latitude,
                    "longitude": op.longitude,
                } if op.latitude else None,
            },
            "price_per_gallon": float(op.price_per_gallon) if op.price_per_gallon else None,
            "gallons_to_buy": float(op.gallons_to_buy),
            "cost_usd": float(op.cost_usd),
            "included_in_total_fuel_cost": True,
        }

        return {
            "origin": {
                "label": start_label,
                "latitude": origin["latitude"],
                "longitude": origin["longitude"],
            },
            "destination": {
                "label": finish_label,
                "latitude": destination["latitude"],
                "longitude": destination["longitude"],
            },
            "route": {
                "distance_miles": route["distance_miles"],
                "estimated_duration_minutes": route["estimated_duration_minutes"],
                "geometry": route["geometry"],
            },
            "vehicle": {
                "fuel_efficiency_mpg": cfg["VEHICLE_MPG"],
                "maximum_range_miles": cfg["MAX_RANGE_MILES"],
                "tank_capacity_gallons": cfg["TANK_CAPACITY_GALLONS"],
                "starting_fuel_gallons": starting_fuel,
            },
            "origin_purchase": origin_purchase,
            "fuel_stops": stops,
            "summary": {
                "main_route_miles": fuel_plan.main_route_miles,
                "total_estimated_trip_miles": fuel_plan.total_estimated_trip_miles,
                "total_route_fuel_used_gallons": float(
                    fuel_plan.total_route_fuel_used_gallons
                ),
                "solver_fuel_used_gallons": float(
                    fuel_plan.solver_fuel_used_gallons
                ),
                "fuel_purchased_on_route_gallons": float(
                    fuel_plan.fuel_purchased_on_route_gallons
                ),
                "ending_fuel_gallons": float(fuel_plan.ending_fuel_gallons),
                "total_fuel_cost_on_route_usd": float(
                    fuel_plan.total_fuel_cost_on_route_usd
                ),
                "currency": "USD",
            },
            "assumptions": [
                "The vehicle begins with the stated starting fuel amount.",
                "Fuel stops are selected from stations within the configured route corridor.",
                "Prices are based on the supplied CSV snapshot.",
                "Fuel efficiency is 10 MPG with a 50-gallon tank (500-mile max range).",
                "A 10-mile safety reserve (1 gallon) is maintained at all times.",
                "Route positions and access distances are geodesic approximations.",
                "The public OSRM demo server is used — production should self-host.",
            ],
        }
