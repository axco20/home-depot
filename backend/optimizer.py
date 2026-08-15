from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from .domain import Product, Store

METERS_PER_MILE = 1609.344


@dataclass(slots=True)
class ReceiptLine:
    sku: str
    name: str
    quantity: int
    source_price: float
    target_price: float
    retail_price: float
    image_url: str = ""

    @property
    def savings(self) -> float:
        return max(0, self.retail_price - self.target_price) * self.quantity


@dataclass(slots=True)
class Receipt:
    receipt_id: str
    source_index: int
    destination_index: int
    lines: list[ReceiptLine]

    @property
    def units(self) -> int:
        return sum(line.quantity for line in self.lines)

    @property
    def savings(self) -> float:
        return sum(line.savings for line in self.lines)


@dataclass(slots=True)
class DirectLine:
    sku: str
    name: str
    quantity: int
    price: float
    retail_price: float
    item_location: str
    image_url: str = ""

    @property
    def savings(self) -> float:
        return max(0, self.retail_price - self.price) * self.quantity


@dataclass(slots=True)
class ActionPlan:
    route: list[int]
    direct: dict[int, list[DirectLine]] = field(default_factory=dict)
    receipts: list[Receipt] = field(default_factory=list)

    @property
    def units(self) -> int:
        return sum(line.quantity for lines in self.direct.values() for line in lines) + sum(
            receipt.units for receipt in self.receipts
        )

    @property
    def savings(self) -> float:
        return sum(line.savings for lines in self.direct.values() for line in lines) + sum(
            receipt.savings for receipt in self.receipts
        )

    @property
    def actionable_stores(self) -> set[int]:
        result = set(self.direct)
        for receipt in self.receipts:
            result.add(receipt.source_index)
            result.add(receipt.destination_index)
        return result


@dataclass(slots=True)
class CandidatePlan:
    actions: ActionPlan
    meters: int


def is_clearance(price: float, retail_price: float, minimum_discount_percent: int) -> bool:
    if price <= 0 or retail_price <= 0:
        return False
    discount_percent = ((retail_price - price) / retail_price) * 100
    return discount_percent + 1e-9 >= minimum_discount_percent


def route_distance(route: list[int], matrix: list[list[int]]) -> int:
    nodes = [0, *route, 0]
    return sum(matrix[nodes[index]][nodes[index + 1]] for index in range(len(nodes) - 1))


def _two_opt(route: list[int], matrix: list[list[int]], passes: int = 8) -> list[int]:
    best = route[:]
    best_distance = route_distance(best, matrix)
    for _ in range(passes):
        improved = False
        for left in range(len(best) - 1):
            for right in range(left + 1, len(best)):
                candidate = best[:left] + list(reversed(best[left : right + 1])) + best[right + 1 :]
                candidate_distance = route_distance(candidate, matrix)
                if candidate_distance < best_distance:
                    best = candidate
                    best_distance = candidate_distance
                    improved = True
        if not improved:
            break
    return best


def shortest_cycle(store_indices: list[int], matrix: list[list[int]]) -> list[int]:
    if not store_indices:
        return []
    remaining = set(store_indices)
    route: list[int] = []
    current = 0
    while remaining:
        next_node = min(remaining, key=lambda candidate: matrix[current][candidate])
        route.append(next_node)
        remaining.remove(next_node)
        current = next_node
    return _two_opt(route, matrix)


def _available_quantity(store: Store, sku: str, stock_buffer: int) -> int:
    observation = store.observations.get(sku)
    if not observation or not observation.in_stock:
        return 0
    return max(0, observation.quantity - stock_buffer)


def build_actions(
    route: list[int],
    stores: list[Store | None],
    products: dict[str, Product],
    minimum_discount_percent: int,
    restock_limit: int,
    stock_buffer: int,
) -> ActionPlan:
    plan = ActionPlan(route=route[:])
    remaining: dict[tuple[int, str], int] = {}

    for store_index in route:
        store = stores[store_index]
        if store is None:
            continue
        direct_lines: list[DirectLine] = []
        for sku, product in products.items():
            quantity = _available_quantity(store, sku, stock_buffer)
            remaining[(store_index, sku)] = quantity
            observation = store.observations.get(sku)
            if not observation or quantity <= 0:
                continue
            if is_clearance(observation.price, product.retail_price, minimum_discount_percent):
                direct_lines.append(
                    DirectLine(
                        sku=sku,
                        name=product.name,
                        quantity=quantity,
                        price=observation.price,
                        retail_price=product.retail_price,
                        item_location=observation.item_location,
                        image_url=product.image_url,
                    )
                )
                remaining[(store_index, sku)] = 0
        if direct_lines:
            plan.direct[store_index] = direct_lines

    for destination_position, destination_index in enumerate(route):
        destination = stores[destination_index]
        if destination is None or destination_position == 0:
            continue

        best_source: int | None = None
        best_lines: list[ReceiptLine] = []
        best_savings = 0.0

        for source_index in route[:destination_position]:
            source = stores[source_index]
            if source is None:
                continue
            unit_choices: list[tuple[float, str, float, float]] = []
            for sku, product in products.items():
                source_observation = source.observations.get(sku)
                target_observation = destination.observations.get(sku)
                available = remaining.get((source_index, sku), 0)
                if not source_observation or not target_observation or available <= 0:
                    continue
                if source_observation.price <= target_observation.price:
                    continue
                if not is_clearance(
                    target_observation.price,
                    product.retail_price,
                    minimum_discount_percent,
                ):
                    continue
                unit_savings = product.retail_price - target_observation.price
                unit_choices.extend(
                    (unit_savings, sku, source_observation.price, target_observation.price)
                    for _ in range(available)
                )

            unit_choices.sort(reverse=True, key=lambda choice: choice[0])
            chosen = unit_choices[:restock_limit]
            if not chosen:
                continue

            grouped: dict[str, ReceiptLine] = {}
            for _, sku, source_price, target_price in chosen:
                if sku not in grouped:
                    grouped[sku] = ReceiptLine(
                        sku=sku,
                        name=products[sku].name,
                        quantity=0,
                        source_price=source_price,
                        target_price=target_price,
                        retail_price=products[sku].retail_price,
                        image_url=products[sku].image_url,
                    )
                grouped[sku].quantity += 1
            lines = list(grouped.values())
            savings = sum(line.savings for line in lines)
            if savings > best_savings or (
                math.isclose(savings, best_savings) and sum(line.quantity for line in lines) > sum(line.quantity for line in best_lines)
            ):
                best_source = source_index
                best_lines = lines
                best_savings = savings

        if best_source is None:
            continue
        receipt = Receipt(
            receipt_id=f"R{len(plan.receipts) + 1}",
            source_index=best_source,
            destination_index=destination_index,
            lines=best_lines,
        )
        plan.receipts.append(receipt)
        for line in best_lines:
            remaining[(best_source, line.sku)] -= line.quantity

    return plan


def _store_scores(
    stores: list[Store | None],
    products: dict[str, Product],
    matrix: list[list[int]],
    minimum_discount_percent: int,
    restock_limit: int,
    stock_buffer: int,
) -> list[tuple[float, int]]:
    scored: list[tuple[float, int]] = []
    for store_index in range(1, len(stores)):
        store = stores[store_index]
        if store is None:
            continue
        direct_value = 0.0
        destination_units: list[float] = []
        source_value = 0.0
        for sku, product in products.items():
            observation = store.observations.get(sku)
            quantity = _available_quantity(store, sku, stock_buffer)
            if observation and quantity > 0 and is_clearance(
                observation.price, product.retail_price, minimum_discount_percent
            ):
                direct_value += (product.retail_price - observation.price) * quantity

            if observation and is_clearance(
                observation.price, product.retail_price, minimum_discount_percent
            ):
                for possible_source in stores[1:]:
                    if possible_source is None or possible_source.store_id == store.store_id:
                        continue
                    source_observation = possible_source.observations.get(sku)
                    available = _available_quantity(possible_source, sku, stock_buffer)
                    if source_observation and source_observation.price > observation.price and available > 0:
                        destination_units.extend(
                            [product.retail_price - observation.price] * min(available, restock_limit)
                        )

            if observation and quantity > 0:
                best_target = 0.0
                for possible_target in stores[1:]:
                    if possible_target is None or possible_target.store_id == store.store_id:
                        continue
                    target_observation = possible_target.observations.get(sku)
                    if not target_observation or observation.price <= target_observation.price:
                        continue
                    if is_clearance(
                        target_observation.price,
                        product.retail_price,
                        minimum_discount_percent,
                    ):
                        best_target = max(best_target, product.retail_price - target_observation.price)
                source_value += best_target * min(quantity, restock_limit)

        destination_units.sort(reverse=True)
        destination_value = sum(destination_units[:restock_limit])
        value = direct_value + destination_value + source_value * 0.35
        if value <= 0:
            continue
        # Keep raw opportunity value here. Geographic efficiency is applied
        # while building the candidate set so a dense group is valued by its
        # marginal route miles, not by every store's distance from home.
        scored.append((value, store_index))
    return sorted(scored, reverse=True)


def _best_insertion(
    route: list[int],
    store_index: int,
    matrix: list[list[int]],
) -> tuple[int, int]:
    """Return the cheapest cycle insertion position and additional meters."""
    nodes = [0, *route, 0]
    best_position = 0
    best_delta = math.inf
    for position in range(len(nodes) - 1):
        before = nodes[position]
        after = nodes[position + 1]
        delta = (
            matrix[before][store_index]
            + matrix[store_index][after]
            - matrix[before][after]
        )
        if delta < best_delta:
            best_delta = delta
            best_position = position
    return best_position, max(0, int(best_delta))


def _cluster_ranked_stores(
    scores: list[tuple[float, int]],
    matrix: list[list[int]],
) -> list[int]:
    """Rank profitable stores by value and marginal miles into the loop."""
    if not scores:
        return []

    values = {store_index: value for value, store_index in scores}
    seed = max(
        values,
        key=lambda store_index: values[store_index]
        / (1 + (matrix[0][store_index] / METERS_PER_MILE) * 0.12),
    )
    cycle = [seed]
    ranked = [seed]
    remaining = set(values) - {seed}

    while remaining:
        choices: list[tuple[float, float, int, int]] = []
        for store_index in remaining:
            position, delta = _best_insertion(cycle, store_index, matrix)
            delta_miles = delta / METERS_PER_MILE
            # A store close to an existing cluster should beat an isolated
            # store with roughly the same inventory opportunity.
            utility = values[store_index] / (1 + delta_miles * 0.30)
            choices.append((utility, values[store_index], store_index, position))
        _, _, selected, position = max(choices)
        cycle.insert(position, selected)
        ranked.append(selected)
        remaining.remove(selected)

    return ranked


def _pareto_choice(candidates: list[CandidatePlan]) -> CandidatePlan:
    ordered = sorted(candidates, key=lambda candidate: candidate.meters)
    frontier: list[CandidatePlan] = []
    best_savings = -1.0
    best_units = -1
    for candidate in ordered:
        if candidate.actions.savings > best_savings or (
            math.isclose(candidate.actions.savings, best_savings) and candidate.actions.units > best_units
        ):
            frontier.append(candidate)
            best_savings = candidate.actions.savings
            best_units = candidate.actions.units

    max_savings = max(candidate.actions.savings for candidate in frontier) or 1
    max_units = max(candidate.actions.units for candidate in frontier) or 1
    max_meters = max(candidate.meters for candidate in frontier) or 1

    def ideal_distance(candidate: CandidatePlan) -> float:
        benefit = 0.8 * candidate.actions.savings / max_savings + 0.2 * candidate.actions.units / max_units
        miles = candidate.meters / max_meters
        return math.sqrt((1 - benefit) ** 2 + miles**2)

    return min(frontier, key=ideal_distance)


def _precedence_route(
    store_indices: list[int],
    receipts: list[Receipt],
    matrix: list[list[int]],
) -> list[int]:
    if len(store_indices) <= 2:
        return store_indices
    try:
        from ortools.constraint_solver import pywrapcp, routing_enums_pb2
    except ImportError:
        return shortest_cycle(store_indices, matrix)

    nodes = [0, *store_indices]
    node_position = {node: index for index, node in enumerate(nodes)}
    manager = pywrapcp.RoutingIndexManager(len(nodes), 1, 0)
    routing = pywrapcp.RoutingModel(manager)

    def distance_callback(from_index: int, to_index: int) -> int:
        return matrix[nodes[manager.IndexToNode(from_index)]][nodes[manager.IndexToNode(to_index)]]

    callback = routing.RegisterTransitCallback(distance_callback)
    routing.SetArcCostEvaluatorOfAllVehicles(callback)
    routing.AddDimension(callback, 0, 100_000_000, True, "RoadDistance")
    dimension = routing.GetDimensionOrDie("RoadDistance")
    solver = routing.solver()
    for receipt in receipts:
        if receipt.source_index not in node_position or receipt.destination_index not in node_position:
            continue
        source = manager.NodeToIndex(node_position[receipt.source_index])
        destination = manager.NodeToIndex(node_position[receipt.destination_index])
        solver.Add(dimension.CumulVar(source) <= dimension.CumulVar(destination))

    parameters = pywrapcp.DefaultRoutingSearchParameters()
    parameters.first_solution_strategy = routing_enums_pb2.FirstSolutionStrategy.PATH_CHEAPEST_ARC
    parameters.local_search_metaheuristic = routing_enums_pb2.LocalSearchMetaheuristic.GUIDED_LOCAL_SEARCH
    parameters.time_limit.seconds = 3
    solution = routing.SolveWithParameters(parameters)
    if not solution:
        return store_indices

    result: list[int] = []
    index = routing.Start(0)
    while not routing.IsEnd(index):
        node = nodes[manager.IndexToNode(index)]
        if node != 0:
            result.append(node)
        index = solution.Value(routing.NextVar(index))
    return result


def optimize(
    stores: list[Store | None],
    products: dict[str, Product],
    matrix: list[list[int]],
    minimum_discount_percent: int,
    restock_limit: int,
    stock_buffer: int,
) -> ActionPlan:
    scores = _store_scores(
        stores,
        products,
        matrix,
        minimum_discount_percent,
        restock_limit,
        stock_buffer,
    )
    ranked = _cluster_ranked_stores(scores, matrix)
    if not ranked:
        return ActionPlan(route=[])

    sizes = sorted({min(len(ranked), size) for size in [1, 2, 3, 5, 8, 12, 16, 24, 32, 48, 64, 100]})
    candidates: list[CandidatePlan] = []
    for size in sizes:
        initial_route = shortest_cycle(ranked[:size], matrix)
        orientations = [initial_route, list(reversed(initial_route))]
        best_for_size: CandidatePlan | None = None
        for route in orientations:
            actions = build_actions(
                route,
                stores,
                products,
                minimum_discount_percent,
                restock_limit,
                stock_buffer,
            )
            actionable_route = [node for node in route if node in actions.actionable_stores]
            actions.route = actionable_route
            candidate = CandidatePlan(actions=actions, meters=route_distance(actionable_route, matrix))
            if best_for_size is None or candidate.actions.savings > best_for_size.actions.savings or (
                math.isclose(candidate.actions.savings, best_for_size.actions.savings)
                and candidate.meters < best_for_size.meters
            ):
                best_for_size = candidate
        if best_for_size and best_for_size.actions.units > 0:
            candidates.append(best_for_size)

    if not candidates:
        return ActionPlan(route=[])
    selected = _pareto_choice(candidates)
    selected.actions.route = _precedence_route(
        selected.actions.route,
        selected.actions.receipts,
        matrix,
    )
    return selected.actions


def optimize_selected(
    store_indices: list[int],
    stores: list[Store | None],
    products: dict[str, Product],
    matrix: list[list[int]],
    minimum_discount_percent: int,
    restock_limit: int,
    stock_buffer: int,
) -> ActionPlan:
    """Build the shortest route through exactly the stores selected by a user."""
    selected = list(dict.fromkeys(store_indices))
    if not selected:
        return ActionPlan(route=[])

    # Establish the geographic loop before assigning receipts. Every receipt
    # is therefore sourced from an earlier stop without imposing a later route
    # constraint that could cause geographic ping-pong.
    route = _precedence_route(selected, [], matrix)
    candidates: list[CandidatePlan] = []
    for orientation in [route, list(reversed(route))]:
        actions = build_actions(
            orientation,
            stores,
            products,
            minimum_discount_percent,
            restock_limit,
            stock_buffer,
        )
        # Manual selection is authoritative, including selected stores that do
        # not currently produce an automatic action.
        actions.route = orientation
        candidates.append(CandidatePlan(actions=actions, meters=route_distance(orientation, matrix)))

    return max(
        candidates,
        key=lambda candidate: (
            candidate.actions.savings,
            candidate.actions.units,
            -candidate.meters,
        ),
    ).actions


def serialize_plan(
    actions: ActionPlan,
    stores: list[Store | None],
    matrix: list[list[int]],
    home_zip: str,
    checked_at: str,
    geometry: list[list[float]],
    stock_errors: list[dict[str, str]],
) -> dict[str, Any]:
    receipts_by_source: dict[int, list[Receipt]] = {}
    receipt_by_destination: dict[int, Receipt] = {}
    for receipt in actions.receipts:
        receipts_by_source.setdefault(receipt.source_index, []).append(receipt)
        receipt_by_destination[receipt.destination_index] = receipt

    carrying: dict[str, int] = {}
    stops: list[dict[str, Any]] = []
    previous = 0
    cumulative_meters = 0
    for sequence, store_index in enumerate(actions.route, start=1):
        store = stores[store_index]
        if store is None:
            continue
        leg_meters = matrix[previous][store_index]
        cumulative_meters += leg_meters

        source_receipts = receipts_by_source.get(store_index, [])
        for receipt in source_receipts:
            carrying[receipt.receipt_id] = receipt.units

        adjustment = receipt_by_destination.get(store_index)
        if adjustment:
            carrying.pop(adjustment.receipt_id, None)

        roles: list[str] = []
        if actions.direct.get(store_index):
            roles.append("clearance buy")
        if source_receipts:
            roles.append("pickup")
        if adjustment:
            roles.append("price match")

        stops.append(
            {
                "sequence": sequence,
                "storeId": store.store_id,
                "storeName": store.name,
                "address": store.full_address,
                "latitude": store.latitude,
                "longitude": store.longitude,
                "legMiles": round(leg_meters / METERS_PER_MILE, 1),
                "cumulativeMiles": round(cumulative_meters / METERS_PER_MILE, 1),
                "roles": roles,
                "directPurchases": [
                    {
                        "sku": line.sku,
                        "name": line.name,
                        "quantity": line.quantity,
                        "price": round(line.price, 2),
                        "retailPrice": round(line.retail_price, 2),
                        "itemLocation": line.item_location,
                        "imageUrl": line.image_url,
                    }
                    for line in actions.direct.get(store_index, [])
                ],
                "purchaseReceipts": [
                    {
                        "receiptId": receipt.receipt_id,
                        "destinationStoreId": stores[receipt.destination_index].store_id,
                        "destinationStoreName": stores[receipt.destination_index].name,
                        "units": receipt.units,
                        "lines": [
                            {
                                "sku": line.sku,
                                "name": line.name,
                                "quantity": line.quantity,
                                "buyPrice": round(line.source_price, 2),
                                "targetPrice": round(line.target_price, 2),
                                "imageUrl": line.image_url,
                            }
                            for line in receipt.lines
                        ],
                    }
                    for receipt in source_receipts
                ],
                "priceAdjustment": None
                if not adjustment
                else {
                    "receiptId": adjustment.receipt_id,
                    "sourceStoreId": stores[adjustment.source_index].store_id,
                    "sourceStoreName": stores[adjustment.source_index].name,
                    "units": adjustment.units,
                    "lines": [
                        {
                            "sku": line.sku,
                            "name": line.name,
                            "quantity": line.quantity,
                            "buyPrice": round(line.source_price, 2),
                            "targetPrice": round(line.target_price, 2),
                            "imageUrl": line.image_url,
                        }
                        for line in adjustment.lines
                    ],
                },
                "carryingAfter": {
                    "receipts": len(carrying),
                    "units": sum(carrying.values()),
                },
            }
        )
        previous = store_index

    return_meters = matrix[previous][0] if actions.route else 0
    total_meters = cumulative_meters + return_meters
    direct_units = sum(line.quantity for lines in actions.direct.values() for line in lines)
    restock_units = sum(receipt.units for receipt in actions.receipts)

    return {
        "homeZip": home_zip,
        "checkedAt": checked_at,
        "summary": {
            "totalMiles": round(total_meters / METERS_PER_MILE, 1),
            "returnMiles": round(return_meters / METERS_PER_MILE, 1),
            "storesVisited": len(actions.route),
            "totalUnits": direct_units + restock_units,
            "directUnits": direct_units,
            "restockUnits": restock_units,
            "totalSavings": round(actions.savings, 2),
        },
        "stops": stops,
        "routeCoordinates": geometry,
        "warnings": [
            f"SKU {error['sku']}: {error['message']}" if error.get("sku") else error["message"]
            for error in stock_errors
        ],
    }
