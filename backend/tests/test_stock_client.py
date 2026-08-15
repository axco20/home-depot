from __future__ import annotations

import asyncio
import os
import unittest
from unittest.mock import AsyncMock, patch

from backend.clients import StockClient


class FakeResponse:
    def __init__(self, status_code: int, payload: dict) -> None:
        self.status_code = status_code
        self._payload = payload

    def json(self) -> dict:
        return self._payload


class FakeAsyncClient:
    def __init__(self, responses: list[FakeResponse], *args, **kwargs) -> None:
        self.responses = responses
        self.calls: list[dict] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args) -> None:
        return None

    async def get(self, _url: str, *, params: dict) -> FakeResponse:
        self.calls.append(params)
        return self.responses[len(self.calls) - 1]


async def ignore_progress(_progress: int, _message: str) -> None:
    return None


class StockClientTests(unittest.TestCase):
    def test_exactly_one_request_is_made_per_sku(self) -> None:
        skus = ["111", "222", "333"]
        fake_client = FakeAsyncClient(
            [
                FakeResponse(200, {"success": True, "item": {"sku": sku}, "stores": []})
                for sku in skus
            ]
        )
        with (
            patch("backend.clients.httpx.AsyncClient", return_value=fake_client),
            patch("backend.clients.asyncio.sleep", new=AsyncMock()),
            patch.dict(os.environ, {"HIDDEN_CLEARANCES_REQUEST_INTERVAL_SECONDS": "0.5"}),
        ):
            results, errors = asyncio.run(StockClient("token").fetch_many("21704", skus, ignore_progress))

        self.assertEqual(len(fake_client.calls), 3)
        self.assertEqual([call["sku"] for call in fake_client.calls], skus)
        self.assertEqual(len(results), 3)
        self.assertEqual(errors, [])

    def test_rate_limit_stops_without_retrying_or_calling_remaining_skus(self) -> None:
        fake_client = FakeAsyncClient([FakeResponse(429, {"message": "Too many requests"})])
        with patch("backend.clients.httpx.AsyncClient", return_value=fake_client):
            results, errors = asyncio.run(
                StockClient("token").fetch_many("21704", ["111", "222", "333"], ignore_progress)
            )

        self.assertEqual(len(fake_client.calls), 1)
        self.assertEqual(results, [])
        self.assertEqual(len(errors), 3)
        self.assertTrue(all(error["message"].startswith("429 ") for error in errors))


if __name__ == "__main__":
    unittest.main()

