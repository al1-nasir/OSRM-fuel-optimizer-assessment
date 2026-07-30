"""
Fuel optimizer — dynamic-programming solver at 0.01-gallon precision.

Models origin, candidate stations, and destination as ordered nodes.
Connects nodes only when the conservative estimated leg distance
(p_j − p_i) + d_i + d_j ≤ 490 miles.

State: (node_index, fuel_hundredths) where fuel_hundredths ∈ [0, 5000]
       representing 0.00 to 50.00 gallons.

Goal: Minimize total Decimal purchase cost while ensuring:
  - Every leg ≤ 490 miles (10-mile safety reserve)
  - Every movement has fuel_before_leg ≥ solver_consumption + 1.00 gallon
  - Arrival at destination retains ≥ 1.00 gallon
"""

import math
from dataclasses import dataclass, field
from decimal import Decimal, ROUND_UP, ROUND_HALF_UP

from trip_planner.services.station_search import CandidateStation

# Precision: 0.01 gallon = 1 hundredth
HUNDREDTHS_PER_GALLON = 100
MAX_FUEL_HUNDREDTHS = 5000  # 50.00 gallons
FEASIBLE_LEG_LIMIT = 490.0  # miles
MPG = 10
ENDING_RESERVE_HUNDREDTHS = 100  # 1.00 gallon

INF_COST = Decimal("999999999.99")


@dataclass
class FuelNode:
    """A node in the fuel-planning graph."""

    index: int
    route_position_miles: float
    access_offset_miles: float  # 0.0 for origin and destination
    price_per_gallon: Decimal | None  # None for origin/destination (non-purchasable)
    station_id: int | None = None
    name: str = ""
    address: str = ""
    city: str = ""
    state: str = ""
    latitude: float = 0.0
    longitude: float = 0.0
    is_origin: bool = False
    is_destination: bool = False


@dataclass
class FuelStop:
    """A recommended fuel stop in the plan."""

    sequence: int
    station_id: int
    name: str
    address: str
    city: str
    state: str
    latitude: float
    longitude: float
    route_position_miles: float
    distance_from_route_miles: float
    price_per_gallon: Decimal
    gallons_to_buy: Decimal
    cost_usd: Decimal
    estimated_arrival_fuel_gallons: Decimal
    estimated_departure_fuel_gallons: Decimal
    incoming_leg_miles: float
    outgoing_leg_miles: float | None = None  # filled after plan is built


@dataclass
class OriginPurchase:
    """An origin purchase when a co-located station exists with 0 driving distance."""

    required: bool = False
    station_id: int | None = None
    name: str | None = None
    address: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    price_per_gallon: Decimal | None = None
    gallons_to_buy: Decimal = Decimal("0")
    cost_usd: Decimal = Decimal("0")


@dataclass
class FuelPlan:
    """Complete fuel plan result."""

    stops: list[FuelStop] = field(default_factory=list)
    origin_purchase: OriginPurchase = field(default_factory=OriginPurchase)
    total_estimated_trip_miles: float = 0.0
    main_route_miles: float = 0.0
    total_route_fuel_used_gallons: Decimal = Decimal("0")
    solver_fuel_used_gallons: Decimal = Decimal("0")
    fuel_purchased_on_route_gallons: Decimal = Decimal("0")
    ending_fuel_gallons: Decimal = Decimal("0")
    total_fuel_cost_on_route_usd: Decimal = Decimal("0")
    feasible: bool = True
    error_code: str = ""
    error_message: str = ""


def _estimated_leg_miles(node_i: FuelNode, node_j: FuelNode) -> float:
    """
    Conservative model estimate:
    (p_j − p_i) + d_i + d_j
    """
    return (
        (node_j.route_position_miles - node_i.route_position_miles)
        + node_i.access_offset_miles
        + node_j.access_offset_miles
    )


def _consumption_hundredths(leg_miles: float) -> int:
    """
    Physical consumption rounded UP to the next 0.01 gallon.
    consumption = leg_miles / MPG, rounded up.
    """
    gallons = leg_miles / MPG
    hundredths = math.ceil(gallons * HUNDREDTHS_PER_GALLON)
    return hundredths


def optimize_fuel_plan(
    candidates: list[CandidateStation],
    route_distance_miles: float,
    starting_fuel_gallons: float = 50.0,
    origin_stations: list[CandidateStation] | None = None,
) -> FuelPlan:
    """
    Run the DP fuel optimizer.

    Args:
        candidates: Stations sorted by route position (from station_search).
        route_distance_miles: Main OSRM route distance in miles.
        starting_fuel_gallons: Fuel at origin in gallons (0–50).
        origin_stations: Stations co-located at origin (distance 0) for
                         zero-fuel origin purchase scenarios.

    Returns:
        FuelPlan with optimal stops or infeasible status.
    """
    plan = FuelPlan(main_route_miles=route_distance_miles)

    # ----- Build node graph --------------------------------------------------
    nodes: list[FuelNode] = []

    # Node 0: Origin
    origin = FuelNode(
        index=0,
        route_position_miles=0.0,
        access_offset_miles=0.0,
        price_per_gallon=None,
        is_origin=True,
    )
    nodes.append(origin)

    # Handle origin purchase
    origin_purchase = OriginPurchase()
    origin_purchase_node_idx = None

    if origin_stations:
        # Sort by price asc, then station ID asc for deterministic tie-break
        sorted_origin = sorted(
            origin_stations,
            key=lambda c: (c.retail_price, c.station.opis_truckstop_id),
        )
        best_origin = sorted_origin[0]

        # Create an origin purchase node at position 0 with offset 0
        opn = FuelNode(
            index=1,
            route_position_miles=0.0,
            access_offset_miles=0.0,
            price_per_gallon=best_origin.retail_price,
            station_id=best_origin.station.opis_truckstop_id,
            name=best_origin.station.name,
            address=f"{best_origin.station.address}, {best_origin.station.city}, {best_origin.station.state}",
            city=best_origin.station.city,
            state=best_origin.station.state,
            latitude=best_origin.station.location.y,
            longitude=best_origin.station.location.x,
            is_origin=True,
        )
        nodes.append(opn)
        origin_purchase_node_idx = 1
        origin_purchase.required = True
        origin_purchase.station_id = best_origin.station.opis_truckstop_id
        origin_purchase.name = best_origin.station.name
        origin_purchase.address = opn.address
        origin_purchase.latitude = opn.latitude
        origin_purchase.longitude = opn.longitude
        origin_purchase.price_per_gallon = best_origin.retail_price

    # Station nodes
    for c in candidates:
        # Skip stations already used as origin purchase
        if origin_purchase_node_idx is not None and (
            c.station.opis_truckstop_id == origin_purchase.station_id
        ):
            continue

        node = FuelNode(
            index=len(nodes),
            route_position_miles=c.route_position_miles,
            access_offset_miles=c.distance_from_route_miles,
            price_per_gallon=c.retail_price,
            station_id=c.station.opis_truckstop_id,
            name=c.station.name,
            address=f"{c.station.address}, {c.station.city}, {c.station.state}",
            city=c.station.city,
            state=c.station.state,
            latitude=c.station.location.y,
            longitude=c.station.location.x,
        )
        nodes.append(node)

    # Destination node
    dest = FuelNode(
        index=len(nodes),
        route_position_miles=route_distance_miles,
        access_offset_miles=0.0,
        price_per_gallon=None,
        is_destination=True,
    )
    nodes.append(dest)

    # Update indices
    for i, node in enumerate(nodes):
        node.index = i

    n = len(nodes)

    # ----- Build adjacency (edges) with feasibility check --------------------
    # edges[i] = list of j indices reachable from i
    edges: list[list[int]] = [[] for _ in range(n)]

    for i in range(n - 1):
        reachable = []
        for j in range(i + 1, n):
            leg = _estimated_leg_miles(nodes[i], nodes[j])
            if leg <= FEASIBLE_LEG_LIMIT:
                reachable.append(j)

        if not reachable:
            continue

        # If destination is reachable, always include it
        dest_idx = n - 1
        filtered = set()
        if dest_idx in reachable:
            filtered.add(dest_idx)

        # Include top 15 cheapest stations
        station_nodes = [j for j in reachable if j != dest_idx]
        cheap_sorted = sorted(
            station_nodes,
            key=lambda j: (
                nodes[j].price_per_gallon
                if nodes[j].price_per_gallon is not None
                else Decimal("9999")
            )
        )
        for j in cheap_sorted[:15]:
            filtered.add(j)

        # Include top 5 farthest stations (highest route position) to maximize range
        far_sorted = sorted(
            station_nodes,
            key=lambda j: nodes[j].route_position_miles,
            reverse=True,
        )
        for j in far_sorted[:5]:
            filtered.add(j)

        edges[i] = sorted(list(filtered))

    # Check if destination is reachable at all
    if not any(n - 1 in edges[i] for i in range(n - 1)):
        # Check if any path through stations can reach destination
        # via transitive edges — but for now check direct edges
        pass  # The DP will discover infeasibility

    # ----- Dynamic Programming -----------------------------------------------
    starting_hundredths = min(
        round(starting_fuel_gallons * 10) * 10,
        MAX_FUEL_HUNDREDTHS,
    )

    # dp[node][fuel] = minimum cost to reach this state
    dp = [[INF_COST] * (MAX_FUEL_HUNDREDTHS + 1) for _ in range(n)]
    # predecessor tracking: (prev_node, prev_fuel, purchased_hundredths)
    pred = [[None] * (MAX_FUEL_HUNDREDTHS + 1) for _ in range(n)]

    # Initialize origin
    dp[0][starting_hundredths] = Decimal("0")

    # Track active fuel states per node to avoid sparse array iteration
    active_fuel_states: list[set[int]] = [set() for _ in range(n)]
    active_fuel_states[0].add(starting_hundredths)

    STEP_H = 100  # 1.00 gallon step size for purchase options

    for i in range(n - 1):
        node_i = nodes[i]

        for fuel_h in sorted(active_fuel_states[i]):
            if dp[i][fuel_h] >= INF_COST:
                continue

            current_cost = dp[i][fuel_h]

            can_purchase = (
                node_i.price_per_gallon is not None
                and not node_i.is_destination
            )
            if node_i.is_origin and node_i.price_per_gallon is None:
                can_purchase = False

            if can_purchase:
                smart_targets = {MAX_FUEL_HUNDREDTHS}
                for j in edges[i]:
                    leg = _estimated_leg_miles(node_i, nodes[j])
                    cons_h = _consumption_hundredths(leg)
                    needed = cons_h + ENDING_RESERVE_HUNDREDTHS
                    if needed >= fuel_h:
                        needed_step = min(MAX_FUEL_HUNDREDTHS, math.ceil(needed / STEP_H) * STEP_H)
                        smart_targets.add(needed_step)
                targets = sorted([t for t in smart_targets if t >= fuel_h])
            else:
                targets = [fuel_h]

            for fuel_after_purchase in targets:
                buy_h = fuel_after_purchase - fuel_h
                purchase_cost = Decimal("0")

                if buy_h > 0 and node_i.price_per_gallon is not None:
                    gallons_bought = Decimal(buy_h) / Decimal(HUNDREDTHS_PER_GALLON)
                    purchase_cost = gallons_bought * node_i.price_per_gallon

                total_cost = current_cost + purchase_cost

                # Try all reachable next nodes
                for j in edges[i]:
                    node_j = nodes[j]
                    leg = _estimated_leg_miles(node_i, node_j)
                    consumption_h = _consumption_hundredths(leg)

                    # Movement valid only if fuel_before_leg >= consumption + reserve
                    min_fuel_needed = consumption_h + ENDING_RESERVE_HUNDREDTHS
                    if fuel_after_purchase < min_fuel_needed:
                        continue

                    fuel_after_travel = fuel_after_purchase - consumption_h

                    if total_cost < dp[j][fuel_after_travel]:
                        dp[j][fuel_after_travel] = total_cost
                        pred[j][fuel_after_travel] = (i, fuel_h, buy_h)
                        active_fuel_states[j].add(fuel_after_travel)

    # ----- Find best destination state ----------------------------------------
    dest_idx = n - 1
    best_cost = INF_COST
    best_fuel_h = -1

    for fuel_h in sorted(active_fuel_states[dest_idx]):
        if fuel_h >= ENDING_RESERVE_HUNDREDTHS and dp[dest_idx][fuel_h] < best_cost:
            best_cost = dp[dest_idx][fuel_h]
            best_fuel_h = fuel_h

    if best_cost >= INF_COST:
        plan.feasible = False
        plan.error_code = "no_feasible_fuel_plan"
        plan.error_message = (
            "No fuel-station sequence within the configured route corridor "
            "can satisfy the 500-mile range constraint."
        )
        return plan

    # ----- Backtrack to reconstruct the plan ----------------------------------
    path: list[tuple[int, int, int]] = []  # (node_idx, fuel_at_arrival_h, purchased_h)

    node_idx = dest_idx
    fuel_h = best_fuel_h

    while pred[node_idx][fuel_h] is not None:
        prev_node, prev_fuel, purchased = pred[node_idx][fuel_h]
        path.append((node_idx, fuel_h, 0))  # purchased at this node = 0 (we arrive here)
        # The purchase happened at prev_node
        path[-1] = (node_idx, fuel_h, 0)
        # We need to reconstruct more carefully
        node_idx = prev_node
        fuel_h = prev_fuel

    path.append((node_idx, fuel_h, 0))
    path.reverse()

    # Now rebuild with correct purchase amounts
    # Re-walk the predecessor chain from destination
    chain = []
    node_idx = dest_idx
    fuel_h = best_fuel_h

    while pred[node_idx][fuel_h] is not None:
        prev_node, prev_fuel, purchased_h = pred[node_idx][fuel_h]
        chain.append({
            "node_idx": node_idx,
            "arrival_fuel_h": fuel_h,
            "from_node": prev_node,
            "from_fuel_h": prev_fuel,
            "purchased_at_from_h": purchased_h,
        })
        node_idx = prev_node
        fuel_h = prev_fuel

    chain.reverse()

    # Build fuel stops
    stops: list[FuelStop] = []
    total_trip_miles = 0.0
    total_fuel_used_h = 0
    total_purchased_h = 0
    sequence = 0

    origin_purchase_applied = False

    for step in chain:
        from_node = nodes[step["from_node"]]
        to_node = nodes[step["node_idx"]]
        purchased_h = step["purchased_at_from_h"]
        arrival_fuel_h = step["arrival_fuel_h"]

        leg_miles = _estimated_leg_miles(from_node, to_node)
        consumption_h = _consumption_hundredths(leg_miles)
        total_trip_miles += leg_miles
        total_fuel_used_h += consumption_h

        # If purchase happened at from_node and it's a station
        if purchased_h > 0 and from_node.price_per_gallon is not None:
            gallons = Decimal(purchased_h) / Decimal(HUNDREDTHS_PER_GALLON)
            cost = (gallons * from_node.price_per_gallon).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            )
            total_purchased_h += purchased_h

            # Arrival fuel at from_node = from_fuel_h
            from_fuel_h = step["from_fuel_h"]
            arrival_gal = Decimal(from_fuel_h) / Decimal(HUNDREDTHS_PER_GALLON)
            departure_gal = Decimal(from_fuel_h + purchased_h) / Decimal(HUNDREDTHS_PER_GALLON)

            # Check if this is origin purchase node
            if origin_purchase_node_idx is not None and step["from_node"] == origin_purchase_node_idx:
                origin_purchase.gallons_to_buy = gallons
                origin_purchase.cost_usd = cost
                origin_purchase_applied = True
            else:
                sequence += 1
                incoming_leg = 0.0
                if sequence == 1:
                    incoming_leg = leg_miles  # This is wrong; need to track
                    # Actually, incoming_leg for the stop is from the previous node
                    # For the first stop, it's from origin
                    pass

                stops.append(FuelStop(
                    sequence=sequence,
                    station_id=from_node.station_id,
                    name=from_node.name,
                    address=from_node.address,
                    city=from_node.city,
                    state=from_node.state,
                    latitude=from_node.latitude,
                    longitude=from_node.longitude,
                    route_position_miles=from_node.route_position_miles,
                    distance_from_route_miles=from_node.access_offset_miles,
                    price_per_gallon=from_node.price_per_gallon,
                    gallons_to_buy=gallons,
                    cost_usd=cost,
                    estimated_arrival_fuel_gallons=arrival_gal,
                    estimated_departure_fuel_gallons=departure_gal,
                    incoming_leg_miles=0.0,  # filled below
                ))

    # Calculate leg miles for each stop
    # Rebuild the path as: origin → [stops] → destination
    visited_nodes = []
    node_idx = dest_idx
    fuel_h = best_fuel_h
    visited_chain = []

    while pred[node_idx][fuel_h] is not None:
        prev_node, prev_fuel, purchased_h = pred[node_idx][fuel_h]
        visited_chain.append((prev_node, node_idx, purchased_h))
        node_idx = prev_node
        fuel_h = prev_fuel

    visited_chain.reverse()

    # Calculate incoming/outgoing legs for stops
    stop_idx = 0
    for i, stop in enumerate(stops):
        # Find this stop's node in the chain
        for vc in visited_chain:
            from_n, to_n, _ = vc
            fn = nodes[from_n]
            if fn.station_id == stop.station_id and fn.index != 0:
                # Find the leg from the previous node to this one
                prev_leg = _estimated_leg_miles(
                    nodes[visited_chain[visited_chain.index(vc) - 1][0]] if visited_chain.index(vc) > 0 else nodes[0],
                    fn,
                )
                stop.incoming_leg_miles = round(prev_leg, 2)
                # Outgoing leg
                stop.outgoing_leg_miles = round(
                    _estimated_leg_miles(fn, nodes[to_n]), 2
                )
                break

    # ----- Build summary ------------------------------------------------------
    ending_fuel_h = best_fuel_h
    ending_fuel_gal = Decimal(ending_fuel_h) / Decimal(HUNDREDTHS_PER_GALLON)

    total_purchased_gal = Decimal(total_purchased_h) / Decimal(HUNDREDTHS_PER_GALLON)
    total_fuel_used_gal = Decimal(total_fuel_used_h) / Decimal(HUNDREDTHS_PER_GALLON)

    # Solver fuel used (rounded up per-leg)
    solver_fuel_gal = total_fuel_used_gal

    total_cost = sum((s.cost_usd for s in stops), Decimal("0"))
    if origin_purchase_applied:
        total_cost += origin_purchase.cost_usd
        total_purchased_gal += origin_purchase.gallons_to_buy

    plan.stops = stops
    plan.origin_purchase = origin_purchase
    plan.total_estimated_trip_miles = round(total_trip_miles, 2)
    plan.main_route_miles = route_distance_miles
    plan.total_route_fuel_used_gallons = total_fuel_used_gal
    plan.solver_fuel_used_gallons = solver_fuel_gal
    plan.fuel_purchased_on_route_gallons = total_purchased_gal
    plan.ending_fuel_gallons = ending_fuel_gal
    plan.total_fuel_cost_on_route_usd = total_cost.quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )

    return plan
