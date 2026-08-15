from __future__ import annotations

import unittest

from backend.domain import Observation, Product, Store
from backend.optimizer import (
    METERS_PER_MILE,
    ActionPlan,
    _cluster_ranked_stores,
    build_actions,
    is_clearance,
    optimize,
    optimize_selected,
    serialize_plan,
)


def store(store_id: str, observations: dict[str, tuple[int, float]]) -> Store:
    return Store(
        store_id=store_id,
        name=f"Store {store_id}",
        address=f"{store_id} Main St",
        city="Frederick",
        state="MD",
        zip_code="21704",
        observations={
            sku: Observation(quantity=quantity, price=price, in_stock=quantity > 0)
            for sku, (quantity, price) in observations.items()
        },
    )


class OptimizerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.products = {
            "111": Product("111", "Bucket", 100),
            "222": Product("222", "Tool", 200),
        }
        self.stores = [
            None,
            store("A", {"111": (6, 100), "222": (2, 200)}),
            store("B", {"111": (0, 20), "222": (0, 30)}),
            store("C", {"111": (1, 15), "222": (1, 200)}),
        ]
        self.matrix = [
            [0, 1609, 3218, 16090],
            [1609, 0, 1609, 14481],
            [3218, 1609, 0, 12872],
            [16090, 14481, 12872, 0],
        ]

    def test_receipt_can_mix_skus_and_respects_cap(self) -> None:
        actions = build_actions([1, 2], self.stores, self.products, 80, 6, 0)
        self.assertEqual(len(actions.receipts), 1)
        receipt = actions.receipts[0]
        self.assertEqual(receipt.source_index, 1)
        self.assertEqual(receipt.destination_index, 2)
        self.assertEqual(receipt.units, 6)
        self.assertLessEqual(sum(line.quantity for line in receipt.lines), 6)

    def test_clearance_requires_at_least_eighty_percent_off(self) -> None:
        self.assertTrue(is_clearance(20, 100, 80))
        self.assertFalse(is_clearance(20.01, 100, 80))
        self.assertFalse(is_clearance(124, 217, 80))

    def test_destination_uses_only_one_source_receipt(self) -> None:
        actions = build_actions([1, 3, 2], self.stores, self.products, 80, 8, 0)
        destination_receipts = [receipt for receipt in actions.receipts if receipt.destination_index == 2]
        self.assertEqual(len(destination_receipts), 1)

    def test_direct_clearance_does_not_use_restock_capacity(self) -> None:
        actions = build_actions([3], self.stores, self.products, 80, 6, 0)
        self.assertEqual(actions.direct[3][0].quantity, 1)
        self.assertEqual(actions.receipts, [])

    def test_optimizer_returns_round_trip_actions(self) -> None:
        actions = optimize(self.stores, self.products, self.matrix, 80, 6, 0)
        self.assertTrue(actions.route)
        positions = {node: index for index, node in enumerate(actions.route)}
        for receipt in actions.receipts:
            self.assertLess(positions[receipt.source_index], positions[receipt.destination_index])

    def test_cluster_ranking_prefers_low_marginal_miles(self) -> None:
        mile = round(METERS_PER_MILE)
        matrix = [
            [0, 5 * mile, 6 * mile, 20 * mile],
            [5 * mile, 0, mile, 21 * mile],
            [6 * mile, mile, 0, 22 * mile],
            [20 * mile, 21 * mile, 22 * mile, 0],
        ]
        ranked = _cluster_ranked_stores([(100, 1), (90, 2), (95, 3)], matrix)
        self.assertEqual(ranked, [1, 2, 3])

    def test_manual_selection_is_authoritative(self) -> None:
        actions = optimize_selected([1, 3], self.stores, self.products, self.matrix, 80, 6, 0)
        self.assertEqual(set(actions.route), {1, 3})
        self.assertNotIn(2, actions.route)

    def test_serialized_mileage_counts_each_leg_once(self) -> None:
        actions = ActionPlan(route=[1, 2])
        result = serialize_plan(
            actions,
            self.stores,
            self.matrix,
            "21704",
            "now",
            [],
            [],
        )
        self.assertEqual(result["stops"][0]["cumulativeMiles"], 1.0)
        self.assertEqual(result["stops"][1]["cumulativeMiles"], 2.0)
        self.assertEqual(result["summary"]["totalMiles"], 4.0)


if __name__ == "__main__":
    unittest.main()
