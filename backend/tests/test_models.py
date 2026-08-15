from __future__ import annotations

import asyncio
import unittest
from unittest.mock import patch

from fastapi import HTTPException

from backend.domain import Product, Store
from backend.clients import StoreDirectoryClient
from backend.main import (
    Job,
    PlanContext,
    PlanRequest,
    StoreSelectionRequest,
    contexts,
    create_plan,
    jobs,
    resolve_store_selection,
    select_plan_stores,
)


class PlanModelTests(unittest.TestCase):
    def tearDown(self) -> None:
        jobs.clear()
        contexts.clear()
        StoreDirectoryClient._cache.clear()

    def test_bearer_token_is_normalized_and_never_serialized(self) -> None:
        request = PlanRequest(
            homeZip="21704",
            skus=["206751547"],
            bearerToken="Bearer secret-token",
        )
        job = Job(id="test", request=request)

        self.assertEqual(request.bearerToken, "secret-token")
        self.assertEqual(request.minimumDiscountPercent, 80)
        self.assertEqual(request.maxRadiusMiles, 50)
        self.assertNotIn("bearerToken", job.model_dump()["request"])

        other_token = request.model_copy(update={"bearerToken": "different-token"})
        self.assertNotEqual(request, other_token)

    def test_duplicate_active_plan_reuses_job_without_scheduling_twice(self) -> None:
        request = PlanRequest(homeZip="21704", skus=["111"], bearerToken="token")
        with patch("backend.main.schedule_plan") as schedule:
            first = asyncio.run(create_plan(request))
            second = asyncio.run(create_plan(request))

        self.assertEqual(first["id"], second["id"])
        schedule.assert_called_once()

    def test_different_plan_cannot_run_beside_active_batch(self) -> None:
        first = PlanRequest(homeZip="21704", skus=["111"], bearerToken="token")
        second = PlanRequest(homeZip="21704", skus=["222"], bearerToken="token")
        with patch("backend.main.schedule_plan"):
            asyncio.run(create_plan(first))
            with self.assertRaises(HTTPException) as raised:
                asyncio.run(create_plan(second))

        self.assertEqual(raised.exception.status_code, 409)

    def test_store_selection_reuses_loaded_plan_context(self) -> None:
        request = PlanRequest(homeZip="21704", skus=["111"], bearerToken="token")
        jobs["test"] = Job(id="test", request=request, status="completed")
        contexts["test"] = PlanContext(
            products={"111": Product("111", "Bucket", 100)},
            stores=[
                None,
                Store("A", "Store A", "1 Main", "Frederick", "MD", "21704"),
            ],
            matrix=[[0, 1], [1, 0]],
            checked_at="now",
            home_coordinate=(39.4, -77.4),
            warnings=[],
            recommended_store_ids=["A"],
        )

        with patch("backend.main.schedule_selected_plan") as schedule:
            response = asyncio.run(
                select_plan_stores("test", StoreSelectionRequest(storeIds=["A"]))
            )

        self.assertEqual(response["status"], "queued")
        schedule.assert_called_once_with("test", ["A"])

    def test_osm_catalog_location_resolves_to_hidden_store_id(self) -> None:
        StoreDirectoryClient._cache[("21704", 50)] = (
            999999,
            {
                "stores": [
                    {
                        "catalogId": "osm-way-123",
                        "storeName": "Silver Spring",
                        "address": "2300 Broadbirch Drive, Silver Spring, MD 20904",
                    }
                ]
            },
        )
        hidden_store = Store(
            "2551",
            "Silver Spring",
            "2300 Broadbirch Drive",
            "Silver Spring",
            "MD",
            "20904",
        )

        resolved, unresolved = resolve_store_selection(
            "21704",
            50,
            ["osm-way-123"],
            {"2551": hidden_store},
        )

        self.assertEqual(resolved, ["2551"])
        self.assertEqual(unresolved, [])


if __name__ == "__main__":
    unittest.main()
