from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import re
import sqlite3
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import httpx


ProgressCallback = Callable[[int, str], Awaitable[None]]
stock_logger = logging.getLogger("stockpath.hidden_clearances")


class StockClient:
    def __init__(self, token: str | None = None) -> None:
        self.base_url = os.getenv(
            "HIDDEN_CLEARANCES_BASE_URL",
            "https://api.hiddenclearances.com/api/v1",
        ).rstrip("/")
        self.token = (token or os.getenv("HIDDEN_CLEARANCES_TOKEN", "")).strip()

    async def fetch_many(
        self,
        zip_code: str,
        skus: list[str],
        progress: ProgressCallback,
    ) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
        if not self.token:
            raise RuntimeError("HIDDEN_CLEARANCES_TOKEN is not configured")

        request_interval = max(
            0.5,
            float(os.getenv("HIDDEN_CLEARANCES_REQUEST_INTERVAL_SECONDS", "5")),
        )
        errors: list[dict[str, str]] = []
        results: list[dict[str, Any]] = []
        last_request_started = 0.0
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/json",
            "User-Agent": "StockPath/1.0",
        }

        async with httpx.AsyncClient(timeout=35, headers=headers) as client:
            for sku_index, sku in enumerate(skus):
                last_error = "Unknown stock API error"
                stop_batch = False
                payload: dict[str, Any] | None = None

                elapsed = asyncio.get_running_loop().time() - last_request_started
                if elapsed < request_interval:
                    delay = request_interval - elapsed
                    await progress(
                        5 + round((sku_index / len(skus)) * 35),
                        f"Waiting {math.ceil(delay)}s before Hidden API request {sku_index + 1} of {len(skus)}",
                    )
                    await asyncio.sleep(delay)
                last_request_started = asyncio.get_running_loop().time()

                await progress(
                    5 + round((sku_index / len(skus)) * 35),
                    f"Hidden API request {sku_index + 1} of {len(skus)} · SKU {sku}",
                )
                stock_logger.info(
                    "request %s/%s zip=%s sku=%s",
                    sku_index + 1,
                    len(skus),
                    zip_code,
                    sku,
                )

                try:
                    response = await client.get(
                        f"{self.base_url}/homedepot/stock",
                        params={"zip": zip_code, "sku": sku},
                    )
                    stock_logger.info(
                        "response %s/%s sku=%s status=%s",
                        sku_index + 1,
                        len(skus),
                        sku,
                        response.status_code,
                    )
                    if response.status_code >= 400:
                        detail = ""
                        try:
                            error_body = response.json()
                            detail = str(
                                error_body.get("message")
                                or error_body.get("detail")
                                or error_body.get("error")
                                or ""
                            )
                        except (ValueError, AttributeError):
                            pass
                        label = httpx.codes.get_reason_phrase(response.status_code)
                        last_error = f"{response.status_code} {label}"
                        if detail:
                            last_error += f": {detail}"
                        stop_batch = response.status_code in {401, 403, 429}
                    else:
                        payload = response.json()
                        if not payload.get("success"):
                            last_error = str(payload.get("message") or "Stock request failed")
                            payload = None
                except (httpx.HTTPError, ValueError) as exc:
                    last_error = str(exc)
                    stock_logger.warning(
                        "request %s/%s sku=%s failed=%s",
                        sku_index + 1,
                        len(skus),
                        sku,
                        type(exc).__name__,
                    )

                if payload is not None:
                    results.append(payload)
                else:
                    errors.append({"sku": sku, "message": last_error})

                completed = sku_index + 1
                await progress(
                    5 + round((completed / len(skus)) * 35),
                    f"Loaded {completed} of {len(skus)} SKUs",
                )

                if stop_batch:
                    for remaining_sku in skus[completed:]:
                        errors.append({"sku": remaining_sku, "message": last_error})
                    stock_logger.info(
                        "batch stopped after %s request(s); no calls made for %s remaining SKU(s)",
                        completed,
                        len(skus) - completed,
                    )
                    break

        return results, errors


class RouteCache:
    def __init__(self) -> None:
        configured = os.getenv("STOCKPATH_DB")
        if not configured:
            configured = "/tmp/stockpath.sqlite3" if os.getenv("VERCEL") else "backend/stockpath.sqlite3"
        self.path = Path(configured)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS geocodes (
                    address TEXT PRIMARY KEY,
                    latitude REAL NOT NULL,
                    longitude REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS road_legs (
                    origin_key TEXT NOT NULL,
                    destination_key TEXT NOT NULL,
                    meters INTEGER NOT NULL,
                    PRIMARY KEY (origin_key, destination_key)
                );
                """
            )

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path)

    def get_geocode(self, address: str) -> tuple[float, float] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT latitude, longitude FROM geocodes WHERE address = ?",
                (address,),
            ).fetchone()
        return (float(row[0]), float(row[1])) if row else None

    def put_geocode(self, address: str, latitude: float, longitude: float) -> None:
        with self._connect() as connection:
            connection.execute(
                "INSERT OR REPLACE INTO geocodes(address, latitude, longitude) VALUES (?, ?, ?)",
                (address, latitude, longitude),
            )

    @staticmethod
    def coordinate_key(coordinate: tuple[float, float]) -> str:
        return f"{coordinate[0]:.6f},{coordinate[1]:.6f}"

    def get_leg(self, origin: tuple[float, float], destination: tuple[float, float]) -> int | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT meters FROM road_legs WHERE origin_key = ? AND destination_key = ?",
                (self.coordinate_key(origin), self.coordinate_key(destination)),
            ).fetchone()
        return int(row[0]) if row else None

    def get_all_legs(self) -> dict[tuple[str, str], int]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT origin_key, destination_key, meters FROM road_legs"
            ).fetchall()
        return {(str(origin), str(destination)): int(meters) for origin, destination, meters in rows}

    def put_leg(self, origin: tuple[float, float], destination: tuple[float, float], meters: int) -> None:
        with self._connect() as connection:
            connection.execute(
                "INSERT OR REPLACE INTO road_legs(origin_key, destination_key, meters) VALUES (?, ?, ?)",
                (self.coordinate_key(origin), self.coordinate_key(destination), meters),
            )

    def put_legs(self, legs: list[tuple[tuple[float, float], tuple[float, float], int]]) -> None:
        with self._connect() as connection:
            connection.executemany(
                "INSERT OR REPLACE INTO road_legs(origin_key, destination_key, meters) VALUES (?, ?, ?)",
                [
                    (self.coordinate_key(origin), self.coordinate_key(destination), meters)
                    for origin, destination, meters in legs
                ],
            )


class OpenRouteServiceClient:
    def __init__(self, cache: RouteCache) -> None:
        self.api_key = os.getenv("OPENROUTESERVICE_API_KEY", "").strip()
        self.base_url = os.getenv(
            "OPENROUTESERVICE_BASE_URL",
            "https://api.heigit.org/openrouteservice",
        ).rstrip("/")
        self.geocode_url = os.getenv(
            "OPENROUTESERVICE_GEOCODE_URL",
            "https://api.heigit.org/pelias/v1",
        ).rstrip("/")
        self.cache = cache

    def _require_key(self) -> None:
        if not self.api_key:
            raise RuntimeError("OPENROUTESERVICE_API_KEY is not configured")

    async def _request_with_retry(
        self,
        client: httpx.AsyncClient,
        method: str,
        url: str,
        **kwargs: Any,
    ) -> httpx.Response:
        response: httpx.Response | None = None
        for attempt in range(4):
            response = await client.request(method, url, **kwargs)
            if response.status_code not in {429, 502, 503, 504}:
                response.raise_for_status()
                return response
            if attempt < 3:
                retry_after = response.headers.get("Retry-After", "")
                try:
                    delay = min(12.0, max(1.0, float(retry_after)))
                except ValueError:
                    delay = float(2**attempt)
                await asyncio.sleep(delay)
        assert response is not None
        response.raise_for_status()
        return response

    async def geocode(self, address: str) -> tuple[float, float]:
        cached = self.cache.get_geocode(address)
        if cached:
            return cached
        self._require_key()
        async with httpx.AsyncClient(timeout=25) as client:
            response = await self._request_with_retry(
                client,
                "GET",
                f"{self.geocode_url}/search",
                params={"api_key": self.api_key, "text": address, "size": 1, "boundary.country": "US"},
            )
            features = response.json().get("features") or []
            if not features:
                raise RuntimeError(f"Could not locate {address}")
            longitude, latitude = features[0]["geometry"]["coordinates"]
        coordinate = (float(latitude), float(longitude))
        self.cache.put_geocode(address, *coordinate)
        return coordinate

    async def distance_matrix(
        self,
        coordinates: list[tuple[float, float]],
        progress: ProgressCallback,
    ) -> list[list[int]]:
        self._require_key()
        size = len(coordinates)
        matrix = [[0 if i == j else -1 for j in range(size)] for i in range(size)]
        cached_legs = self.cache.get_all_legs()
        for i in range(size):
            for j in range(size):
                cached = cached_legs.get(
                    (
                        self.cache.coordinate_key(coordinates[i]),
                        self.cache.coordinate_key(coordinates[j]),
                    )
                )
                if cached is not None:
                    matrix[i][j] = cached

        missing_pairs = [(i, j) for i in range(size) for j in range(size) if matrix[i][j] < 0]

        if not missing_pairs:
            await progress(72, "Using cached road distances")
            return matrix

        block_size = 25
        blocks: list[tuple[list[int], list[int]]] = []
        for source_start in range(0, size, block_size):
            sources = list(range(source_start, min(size, source_start + block_size)))
            for destination_start in range(0, size, block_size):
                destinations = list(range(destination_start, min(size, destination_start + block_size)))
                if any(matrix[i][j] < 0 for i in sources for j in destinations if i != j):
                    blocks.append((sources, destinations))

        async with httpx.AsyncClient(timeout=60) as client:
            for block_number, (sources, destinations) in enumerate(blocks, start=1):
                locations = [coordinates[index] for index in sources] + [coordinates[index] for index in destinations]
                ors_locations = [[longitude, latitude] for latitude, longitude in locations]
                response = await self._request_with_retry(
                    client,
                    "POST",
                    f"{self.base_url}/v2/matrix/driving-car",
                    headers={"Authorization": self.api_key, "Content-Type": "application/json"},
                    json={
                        "locations": ors_locations,
                        "sources": list(range(len(sources))),
                        "destinations": list(range(len(sources), len(locations))),
                        "metrics": ["distance"],
                        "units": "m",
                    },
                )
                distances = response.json().get("distances") or []
                new_legs: list[tuple[tuple[float, float], tuple[float, float], int]] = []
                for source_offset, source_index in enumerate(sources):
                    for destination_offset, destination_index in enumerate(destinations):
                        if source_index == destination_index:
                            matrix[source_index][destination_index] = 0
                            continue
                        value = distances[source_offset][destination_offset]
                        if value is None:
                            continue
                        meters = max(1, round(float(value)))
                        matrix[source_index][destination_index] = meters
                        new_legs.append((coordinates[source_index], coordinates[destination_index], meters))
                self.cache.put_legs(new_legs)
                await progress(
                    55 + round((block_number / max(1, len(blocks))) * 20),
                    f"Mapped road distances {block_number} of {len(blocks)}",
                )

        unreachable = [(i, j) for i in range(size) for j in range(size) if matrix[i][j] < 0]
        if unreachable:
            raise RuntimeError("Some selected stores are unreachable by road")
        return matrix

    async def route_geometry(self, coordinates: list[tuple[float, float]]) -> list[list[float]]:
        self._require_key()
        if len(coordinates) < 2:
            return [[coordinates[0][0], coordinates[0][1]]] if coordinates else []

        geometry: list[list[float]] = []
        async with httpx.AsyncClient(timeout=60) as client:
            for start in range(0, len(coordinates) - 1, 49):
                chunk = coordinates[start : min(len(coordinates), start + 50)]
                if len(chunk) < 2:
                    continue
                response = await self._request_with_retry(
                    client,
                    "POST",
                    f"{self.base_url}/v2/directions/driving-car/geojson",
                    headers={"Authorization": self.api_key, "Content-Type": "application/json"},
                    content=json.dumps({
                        "coordinates": [[longitude, latitude] for latitude, longitude in chunk],
                    }),
                )
                raw = response.json()["features"][0]["geometry"]["coordinates"]
                converted = [[float(latitude), float(longitude)] for longitude, latitude in raw]
                geometry.extend(converted if not geometry else converted[1:])
        return geometry


class StoreDirectoryClient:
    """Find nearby Home Depot locations without touching the stock provider."""

    _cache: dict[tuple[str, int], tuple[float, dict[str, Any]]] = {}

    def __init__(self, cache: RouteCache) -> None:
        self.route_client = OpenRouteServiceClient(cache)
        configured = os.getenv("OVERPASS_BASE_URL", "https://overpass-api.de/api/interpreter")
        self.overpass_urls = [
            configured,
            "https://overpass.kumi.systems/api/interpreter",
        ]

    @classmethod
    def cached_result(cls, zip_code: str, radius_miles: int) -> dict[str, Any] | None:
        cached = cls._cache.get((zip_code, radius_miles))
        if not cached:
            return None
        return cached[1]

    @staticmethod
    def _distance_miles(
        origin: tuple[float, float],
        destination: tuple[float, float],
    ) -> float:
        origin_latitude, origin_longitude = map(math.radians, origin)
        latitude, longitude = map(math.radians, destination)
        latitude_delta = latitude - origin_latitude
        longitude_delta = longitude - origin_longitude
        haversine = (
            math.sin(latitude_delta / 2) ** 2
            + math.cos(origin_latitude)
            * math.cos(latitude)
            * math.sin(longitude_delta / 2) ** 2
        )
        return 3958.7613 * 2 * math.asin(math.sqrt(haversine))

    @staticmethod
    def _store_id(tags: dict[str, Any]) -> str:
        reference = str(tags.get("ref") or "")
        matches = re.findall(r"\d{1,4}", reference)
        if matches:
            return matches[-1].zfill(4)
        website = str(tags.get("website") or "").rstrip("/")
        match = re.search(r"/(\d{1,4})$", website)
        return match.group(1).zfill(4) if match else ""

    @staticmethod
    def _address(tags: dict[str, Any]) -> str:
        street = " ".join(
            value
            for value in [
                str(tags.get("addr:housenumber") or "").strip(),
                str(tags.get("addr:street") or "").strip(),
            ]
            if value
        )
        locality = ", ".join(
            value
            for value in [
                str(tags.get("addr:city") or "").strip(),
                " ".join(
                    value
                    for value in [
                        str(tags.get("addr:state") or "").strip(),
                        str(tags.get("addr:postcode") or "").strip(),
                    ]
                    if value
                ),
            ]
            if value
        )
        return ", ".join(value for value in [street, locality] if value)

    async def find(self, zip_code: str, radius_miles: int) -> dict[str, Any]:
        cache_key = (zip_code, radius_miles)
        now = asyncio.get_running_loop().time()
        cached = self._cache.get(cache_key)
        if cached and cached[0] > now:
            return cached[1]

        home_coordinate = await self.route_client.geocode(f"{zip_code}, USA")
        radius_meters = round(radius_miles * 1609.344)
        latitude, longitude = home_coordinate
        query = (
            "[out:json][timeout:25];("
            f'nwr(around:{radius_meters},{latitude},{longitude})["brand:wikidata"="Q864407"];'
            f'nwr(around:{radius_meters},{latitude},{longitude})["brand"~"Home Depot",i];'
            ");out center tags;"
        )

        payload: dict[str, Any] | None = None
        last_error = "Store directory is temporarily unavailable"
        async with httpx.AsyncClient(timeout=40, headers={"User-Agent": "StockPath/1.0"}) as client:
            for url in dict.fromkeys(self.overpass_urls):
                try:
                    response = await client.get(url, params={"data": query})
                    response.raise_for_status()
                    payload = response.json()
                    break
                except (httpx.HTTPError, ValueError) as exc:
                    last_error = str(exc)
        if payload is None:
            raise RuntimeError(last_error)

        stores: dict[str, dict[str, Any]] = {}
        for element in payload.get("elements") or []:
            tags = element.get("tags") or {}
            center = element.get("center") or {}
            store_latitude = element.get("lat", center.get("lat"))
            store_longitude = element.get("lon", center.get("lon"))
            if store_latitude is None or store_longitude is None:
                continue
            coordinate = (float(store_latitude), float(store_longitude))
            distance = self._distance_miles(home_coordinate, coordinate)
            if distance > radius_miles + 0.25:
                continue
            store_id = self._store_id(tags)
            catalog_id = store_id or f"osm-{element.get('type', 'item')}-{element.get('id')}"
            address = self._address(tags)
            store = {
                "catalogId": catalog_id,
                "storeId": store_id,
                "storeName": str(tags.get("branch") or tags.get("name") or "The Home Depot"),
                "address": address,
                "state": str(tags.get("addr:state") or ""),
                "latitude": coordinate[0],
                "longitude": coordinate[1],
                "distanceMiles": round(distance, 1),
                # Locations without an OSM store number can still be resolved
                # against Hidden's address data when a street number and ZIP
                # are present. Unidentifiable map shapes stay visible only.
                "selectable": bool(
                    store_id
                    or (re.match(r"\s*\d+", address) and re.search(r"\b\d{5}\b", address))
                ),
            }
            existing = stores.get(catalog_id)
            if existing is None or len(store["address"]) > len(existing["address"]):
                stores[catalog_id] = store

        ordered = sorted(stores.values(), key=lambda store: (store["distanceMiles"], store["storeName"]))
        deduplicated: list[dict[str, Any]] = []
        for store in ordered:
            if not store["storeId"] and any(
                self._distance_miles(
                    (store["latitude"], store["longitude"]),
                    (existing["latitude"], existing["longitude"]),
                ) < 0.2
                for existing in deduplicated
            ):
                continue
            deduplicated.append(store)
        ordered = deduplicated
        result = {
            "homeZip": zip_code,
            "radiusMiles": radius_miles,
            "homeCoordinate": [home_coordinate[0], home_coordinate[1]],
            "stores": ordered[:100],
            "truncated": len(ordered) > 100,
            "source": "OpenStreetMap contributors",
        }
        self._cache[cache_key] = (now + 86_400, result)
        return result
