"""
Unit tests for the fuel optimizer (DP solver).
"""

from decimal import Decimal

import pytest

from trip_planner.services.fuel_optimizer import (
    CandidateStation,
    FuelNode,
    FuelPlan,
    optimize_fuel_plan,
)
from trip_planner.services.station_search import CandidateStation


class MockStation:
    """Mock FuelStation for testing without DB."""

    def __init__(self, opis_truckstop_id, name, address, city, state, retail_price, lat, lng):
        self.opis_truckstop_id = opis_truckstop_id
        self.name = name
        self.address = address
        self.city = city
        self.state = state
        self.retail_price = Decimal(str(retail_price))
        self.location = type("Location", (), {"x": lng, "y": lat})()
        self.pk = opis_truckstop_id


def _make_candidate(tsid, name, city, state, price, route_pos, dist_from_route, lat=32.0, lng=-96.0):
    """Helper to create a CandidateStation for testing."""
    station = MockStation(
        opis_truckstop_id=tsid,
        name=name,
        address=f"{tsid} Test Rd",
        city=city,
        state=state,
        retail_price=price,
        lat=lat,
        lng=lng,
    )
    return CandidateStation(
        station=station,
        route_position_miles=route_pos,
        distance_from_route_miles=dist_from_route,
        retail_price=Decimal(str(price)),
    )


class TestFuelOptimizer:
    def test_no_stops_needed_short_route(self):
        """Route under 500 miles with full tank needs no stops."""
        candidates = [
            _make_candidate(1, "Station A", "City", "TX", 3.00, 100.0, 1.0),
            _make_candidate(2, "Station B", "City", "TX", 3.50, 200.0, 2.0),
        ]

        plan = optimize_fuel_plan(
            candidates=candidates,
            route_distance_miles=300.0,
            starting_fuel_gallons=50.0,
        )

        assert plan.feasible
        assert len(plan.stops) == 0
        assert plan.total_fuel_cost_on_route_usd == Decimal("0")

    def test_single_stop_long_route(self):
        """Route over 500 miles with full tank needs at least one stop."""
        candidates = [
            _make_candidate(1, "Cheap Station", "City", "TX", 2.50, 250.0, 1.0),
            _make_candidate(2, "Expensive Station", "City", "TX", 4.00, 300.0, 1.0),
        ]

        plan = optimize_fuel_plan(
            candidates=candidates,
            route_distance_miles=700.0,
            starting_fuel_gallons=50.0,
        )

        assert plan.feasible
        assert len(plan.stops) >= 1

    def test_cheaper_station_preferred(self):
        """Optimizer should prefer cheaper station when both are reachable."""
        candidates = [
            _make_candidate(1, "Cheap", "City", "TX", 2.00, 200.0, 1.0),
            _make_candidate(2, "Expensive", "City", "TX", 5.00, 250.0, 1.0),
        ]

        plan = optimize_fuel_plan(
            candidates=candidates,
            route_distance_miles=700.0,
            starting_fuel_gallons=50.0,
        )

        assert plan.feasible
        if len(plan.stops) > 0:
            # Should use the cheaper station
            stop_ids = [s.station_id for s in plan.stops]
            assert 1 in stop_ids

    def test_infeasible_when_no_stations(self):
        """No stations in corridor on a long route is infeasible."""
        plan = optimize_fuel_plan(
            candidates=[],
            route_distance_miles=800.0,
            starting_fuel_gallons=50.0,
        )

        assert not plan.feasible
        assert plan.error_code == "no_feasible_fuel_plan"

    def test_ending_fuel_at_least_one_gallon(self):
        """Plan must arrive at destination with ≥ 1 gallon."""
        candidates = [
            _make_candidate(1, "Station", "City", "TX", 3.00, 200.0, 1.0),
            _make_candidate(2, "Station2", "City", "TX", 3.00, 400.0, 1.0),
        ]

        plan = optimize_fuel_plan(
            candidates=candidates,
            route_distance_miles=600.0,
            starting_fuel_gallons=50.0,
        )

        if plan.feasible:
            assert plan.ending_fuel_gallons >= Decimal("1.00")

    def test_zero_starting_fuel_no_origin_station(self):
        """Zero fuel with no origin station → infeasible."""
        candidates = [
            _make_candidate(1, "Far Station", "City", "TX", 3.00, 100.0, 1.0),
        ]

        plan = optimize_fuel_plan(
            candidates=candidates,
            route_distance_miles=300.0,
            starting_fuel_gallons=0.0,
        )

        assert not plan.feasible

    def test_fuel_accounting_invariant(self):
        """starting_fuel + purchased - consumed = ending_fuel."""
        candidates = [
            _make_candidate(1, "Station A", "City", "TX", 3.00, 200.0, 1.0),
            _make_candidate(2, "Station B", "City", "TX", 3.00, 400.0, 1.0),
        ]

        plan = optimize_fuel_plan(
            candidates=candidates,
            route_distance_miles=600.0,
            starting_fuel_gallons=50.0,
        )

        if plan.feasible:
            starting = Decimal("50.00")
            purchased = plan.fuel_purchased_on_route_gallons
            used = plan.solver_fuel_used_gallons
            ending = plan.ending_fuel_gallons

            # starting + purchased - used should ≈ ending
            # (accounting for rounding in consumption)
            computed_ending = starting + purchased - used
            assert abs(computed_ending - ending) <= Decimal("0.1")

    def test_leg_distance_under_490(self):
        """No leg in the plan should exceed 490 miles."""
        candidates = [
            _make_candidate(1, "Station", "City", "TX", 3.00, 250.0, 2.0),
            _make_candidate(2, "Station2", "City", "TX", 3.00, 480.0, 2.0),
        ]

        plan = optimize_fuel_plan(
            candidates=candidates,
            route_distance_miles=700.0,
            starting_fuel_gallons=50.0,
        )

        # The plan should be feasible and all legs ≤ 490
        if plan.feasible:
            for stop in plan.stops:
                if stop.incoming_leg_miles > 0:
                    assert stop.incoming_leg_miles <= 490.0
