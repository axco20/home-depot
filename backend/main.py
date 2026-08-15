from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Any, Literal
from uuid import uuid4

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, field_validator

from .clients import OpenRouteServiceClient, RouteCache, StockClient, StoreDirectoryClient
from .domain import Product, Store, normalize_stock_payloads
from .optimizer import is_clearance, optimize, optimize_selected, serialize_plan

load_dotenv()


class PlanRequest(BaseModel):
    homeZip: str
    skus: list[str] = Field(min_length=1, max_length=25)
    bearerToken: str = Field(default="", max_length=4096, exclude=True)
    restockLimit: int = Field(default=6, ge=6, le=8)
    minimumDiscountPercent: int = Field(default=80, ge=80, le=95)
    stockBuffer: int = Field(default=0, ge=0, le=20)
    maxRadiusMiles: int = Field(default=50, ge=5, le=150)
    storeMode: Literal["optimized", "selected", "all"] = "optimized"
    selectedStoreIds: list[str] = Field(default_factory=list, max_length=100)

    @field_validator("homeZip")
    @classmethod
    def validate_zip(cls, value: str) -> str:
        normalized = value.strip()
        if not re.fullmatch(r"\d{5}", normalized):
            raise ValueError("Enter a five-digit ZIP code")
        return normalized

    @field_validator("skus")
    @classmethod
    def validate_skus(cls, values: list[str]) -> list[str]:
        normalized: list[str] = []
        for value in values:
            sku = re.sub(r"\D", "", value)
            if not sku:
                continue
            if sku not in normalized:
                normalized.append(sku)
        if not normalized:
            raise ValueError("Enter at least one SKU")
        if len(normalized) > 25:
            raise ValueError("A plan can contain at most 25 SKUs")
        return normalized

    @field_validator("bearerToken")
    @classmethod
    def clean_bearer_token(cls, value: str) -> str:
        normalized = value.strip()
        if normalized.lower().startswith("bearer "):
            normalized = normalized[7:].strip()
        return normalized

    @field_validator("selectedStoreIds")
    @classmethod
    def clean_selected_store_ids(cls, values: list[str]) -> list[str]:
        return list(dict.fromkeys(value.strip() for value in values if value.strip()))


class Job(BaseModel):
    id: str
    status: Literal["queued", "running", "completed", "failed"] = "queued"
    progress: int = 0
    message: str = "Waiting to start"
    request: PlanRequest
    result: dict[str, Any] | None = None
    error: str | None = None


class StoreSelectionRequest(BaseModel):
    storeIds: list[str] = Field(min_length=1, max_length=100)

    @field_validator("storeIds")
    @classmethod
    def normalize_store_ids(cls, values: list[str]) -> list[str]:
        normalized = list(dict.fromkeys(value.strip() for value in values if value.strip()))
        if not normalized:
            raise ValueError("Select at least one store")
        return normalized


@dataclass(slots=True)
class PlanContext:
    products: dict[str, Product]
    stores: list[Store | None]
    matrix: list[list[int]]
    checked_at: str
    home_coordinate: tuple[float, float]
    warnings: list[dict[str, str]]
    recommended_store_ids: list[str]


app = FastAPI(title="StockPath API", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

jobs: dict[str, Job] = {}
tasks: dict[str, asyncio.Task[None]] = {}
contexts: dict[str, PlanContext] = {}
cache = RouteCache()


def schedule_plan(job_id: str) -> None:
    task = asyncio.create_task(run_plan(job_id))
    tasks[job_id] = task
    task.add_done_callback(lambda _task: tasks.pop(job_id, None))


def resolve_store_selection(
    zip_code: str,
    radius_miles: int,
    requested_catalog_ids: list[str],
    stores: dict[str, Store],
) -> tuple[list[str], list[str]]:
    catalog_result = StoreDirectoryClient.cached_result(zip_code, radius_miles) or {}
    catalog = {
        store["catalogId"]: store
        for store in catalog_result.get("stores") or []
    }
    resolved: list[str] = []
    unresolved: list[str] = []

    def normalized(value: str) -> str:
        return re.sub(r"[^a-z0-9]", "", value.lower())

    for catalog_id in requested_catalog_ids:
        if catalog_id in stores:
            resolved.append(catalog_id)
            continue
        preview = catalog.get(catalog_id)
        if not preview:
            unresolved.append(catalog_id)
            continue

        preview_address = str(preview.get("address") or "")
        zip_match = re.search(r"\b(\d{5})\b", preview_address)
        house_match = re.match(r"\s*(\d+)", preview_address)
        candidates: list[tuple[float, str]] = []
        for store_id, store in stores.items():
            if zip_match and store.zip_code != zip_match.group(1):
                continue
            if house_match and not store.address.strip().startswith(house_match.group(1)):
                continue
            address_score = SequenceMatcher(
                None,
                normalized(preview_address),
                normalized(store.full_address),
            ).ratio()
            name_score = SequenceMatcher(
                None,
                normalized(str(preview.get("storeName") or "")),
                normalized(store.name),
            ).ratio()
            candidates.append((address_score * 0.8 + name_score * 0.2, store_id))

        if candidates and max(candidates)[0] >= 0.55:
            resolved.append(max(candidates)[1])
        else:
            unresolved.append(catalog_id)

    return list(dict.fromkeys(resolved)), unresolved


def available_store_catalog(context: PlanContext, minimum_discount_percent: int) -> list[dict[str, Any]]:
    catalog: list[dict[str, Any]] = []
    for store in context.stores[1:]:
        if store is None or store.latitude is None or store.longitude is None:
            continue
        stocked_skus = sum(
            1
            for observation in store.observations.values()
            if observation.in_stock and observation.quantity > 0
        )
        clearance_skus = sum(
            1
            for sku, observation in store.observations.items()
            if sku in context.products
            and is_clearance(
                observation.price,
                context.products[sku].retail_price,
                minimum_discount_percent,
            )
        )
        catalog.append(
            {
                "storeId": store.store_id,
                "storeName": store.name,
                "address": store.full_address,
                "state": store.state,
                "latitude": store.latitude,
                "longitude": store.longitude,
                "distanceMiles": store.distance_miles,
                "stockedSkuCount": stocked_skus,
                "clearanceSkuCount": clearance_skus,
            }
        )
    return sorted(
        catalog,
        key=lambda store: (
            store["distanceMiles"] is None,
            store["distanceMiles"] or 0,
            store["storeName"],
        ),
    )


async def complete_plan(
    job: Job,
    context: PlanContext,
    selected_store_ids: list[str] | None = None,
) -> None:
    selection_warnings: list[dict[str, str]] = []
    job.progress = max(job.progress, 78)
    job.message = (
        "Optimizing the selected store loop"
        if selected_store_ids is not None
        else "Balancing clustered savings and route miles"
    )

    if selected_store_ids is None:
        actions = await asyncio.to_thread(
            optimize,
            context.stores,
            context.products,
            context.matrix,
            job.request.minimumDiscountPercent,
            job.request.restockLimit,
            job.request.stockBuffer,
        )
    else:
        requested = set(selected_store_ids)
        selected_indices = [
            index
            for index, store in enumerate(context.stores)
            if index > 0 and store is not None and store.store_id in requested
        ]
        missing = requested - {
            context.stores[index].store_id  # type: ignore[union-attr]
            for index in selected_indices
        }
        if not selected_indices:
            raise RuntimeError("None of the selected stores are available in this stock snapshot")
        if missing:
            selection_warnings.append(
                {
                    "message": (
                        "Selected stores not returned in this stock snapshot: "
                        f"{', '.join(sorted(missing))}"
                    )
                }
            )
        actions = await asyncio.to_thread(
            optimize_selected,
            selected_indices,
            context.stores,
            context.products,
            context.matrix,
            job.request.minimumDiscountPercent,
            job.request.restockLimit,
            job.request.stockBuffer,
        )

    if not actions.route:
        raise RuntimeError("No qualifying clearance route was found for these SKUs")

    if selected_store_ids is None or not context.recommended_store_ids:
        context.recommended_store_ids = [
            context.stores[index].store_id  # type: ignore[union-attr]
            for index in actions.route
        ]

    route_coordinates = [context.home_coordinate] + [
        (
            context.stores[index].latitude,  # type: ignore[union-attr]
            context.stores[index].longitude,  # type: ignore[union-attr]
        )
        for index in actions.route
    ] + [context.home_coordinate]
    job.progress = max(job.progress, 92)
    job.message = "Drawing the final road route"
    warnings = [*context.warnings, *selection_warnings]
    try:
        geometry = await OpenRouteServiceClient(cache).route_geometry(route_coordinates)
    except Exception as exc:
        geometry = [[latitude, longitude] for latitude, longitude in route_coordinates]
        warnings.append(
            {
                "message": (
                    "The road-route line was temporarily unavailable; the ordered stops and "
                    f"road-mile totals are still valid. ({exc})"
                )
            }
        )

    job.result = serialize_plan(
        actions,
        context.stores,
        context.matrix,
        job.request.homeZip,
        context.checked_at,
        geometry,
        warnings,
    )
    job.result["availableStores"] = available_store_catalog(
        context,
        job.request.minimumDiscountPercent,
    )
    job.result["selectionMode"] = "custom" if selected_store_ids is not None else "optimized"
    job.result["recommendedStoreIds"] = context.recommended_store_ids
    job.progress = 100
    job.message = "Route ready"
    job.status = "completed"


async def run_selected_plan(job_id: str, selected_store_ids: list[str]) -> None:
    job = jobs[job_id]
    context = contexts[job_id]
    job.status = "running"
    try:
        await complete_plan(job, context, selected_store_ids)
    except Exception as exc:
        job.status = "failed"
        job.error = str(exc)
        job.message = "Route generation failed"


def schedule_selected_plan(job_id: str, selected_store_ids: list[str]) -> None:
    task = asyncio.create_task(run_selected_plan(job_id, selected_store_ids))
    tasks[job_id] = task
    task.add_done_callback(lambda _task: tasks.pop(job_id, None))


async def run_plan(job_id: str) -> None:
    job = jobs[job_id]
    job.status = "running"

    async def update(progress: int, message: str) -> None:
        job.progress = min(99, max(job.progress, progress))
        job.message = message

    try:
        await update(2, "Connecting to Hidden Clearances")
        payloads, stock_errors = await StockClient(job.request.bearerToken).fetch_many(
            job.request.homeZip,
            job.request.skus,
            update,
        )
        error_messages = [error["message"] for error in stock_errors]
        if any(message.startswith("429 ") for message in error_messages):
            raise RuntimeError(
                f"Hidden Clearances rate-limited the batch after {len(payloads)} successful "
                "SKU request(s). No automatic retries or remaining SKU calls were made."
            )
        if not payloads:
            if any(message.startswith(("401 ", "403 ")) for message in error_messages):
                raise RuntimeError(
                    "Hidden Clearances rejected the bearer token. Paste a fresh token copied "
                    "from an authenticated Hidden Clearances stock request."
                )
            first_error = error_messages[0] if error_messages else "Unknown upstream error"
            raise RuntimeError(f"All Hidden Clearances SKU requests failed: {first_error}")

        products, store_map, checked_at = normalize_stock_payloads(payloads)
        if not stores_with_observations(store_map):
            raise RuntimeError("The stock API did not return any stores")

        store_map = {
            store_id: store
            for store_id, store in store_map.items()
            if store.distance_miles is None or store.distance_miles <= job.request.maxRadiusMiles
        }
        if not store_map:
            raise RuntimeError(
                f"No returned stores are within {job.request.maxRadiusMiles} miles of {job.request.homeZip}"
            )

        selected_for_plan: list[str] | None = None
        if job.request.selectedStoreIds:
            resolved_eligible_ids, unresolved_catalog_ids = resolve_store_selection(
                job.request.homeZip,
                job.request.maxRadiusMiles,
                job.request.selectedStoreIds,
                store_map,
            )
            if job.request.storeMode != "optimized":
                selected_for_plan = resolved_eligible_ids
            if unresolved_catalog_ids:
                stock_errors.append(
                    {
                        "message": (
                            f"Could not match {len(unresolved_catalog_ids)} selected map location(s) "
                            "to the stock response"
                        )
                    }
                )
            eligible_ids = set(resolved_eligible_ids)
            store_map = {
                store_id: store
                for store_id, store in store_map.items()
                if store_id in eligible_ids
            }
            if not store_map:
                raise RuntimeError(
                    "None of the mapped stores were returned by Hidden Clearances for these SKUs"
                )
        elif job.request.storeMode != "optimized":
            raise RuntimeError("Select at least one store before generating the route")

        route_client = OpenRouteServiceClient(cache)
        await update(42, f"Locating {len(store_map)} stores inside the route radius")
        home_coordinate = await route_client.geocode(f"{job.request.homeZip}, USA")

        located: list[Store] = []
        geocode_errors: list[dict[str, str]] = []
        semaphore = asyncio.Semaphore(5)
        geocoded_count = 0
        geocode_lock = asyncio.Lock()

        async def locate(store: Store) -> None:
            nonlocal geocoded_count
            async with semaphore:
                try:
                    store.latitude, store.longitude = await route_client.geocode(store.full_address)
                    located.append(store)
                except Exception as exc:  # individual stores can be omitted safely
                    geocode_errors.append({"message": f"Could not map {store.name}: {exc}"})
                finally:
                    async with geocode_lock:
                        geocoded_count += 1
                        await update(
                            42 + round((geocoded_count / len(store_map)) * 12),
                            f"Located {geocoded_count} of {len(store_map)} stores",
                        )

        await asyncio.gather(*(locate(store) for store in store_map.values()))
        if not located:
            raise RuntimeError("None of the returned stores could be located")

        located.sort(key=lambda store: store.store_id)
        stores: list[Store | None] = [None, *located]
        coordinates = [home_coordinate] + [
            (store.latitude, store.longitude)
            for store in located
            if store.latitude is not None and store.longitude is not None
        ]
        matrix = await route_client.distance_matrix(coordinates, update)
        context = PlanContext(
            products=products,
            stores=stores,
            matrix=matrix,
            checked_at=checked_at,
            home_coordinate=home_coordinate,
            warnings=[*stock_errors, *geocode_errors],
            recommended_store_ids=[],
        )
        contexts[job_id] = context
        await complete_plan(
            job,
            context,
            selected_for_plan,
        )
    except Exception as exc:
        job.status = "failed"
        job.error = str(exc)
        job.message = "Route generation failed"


def stores_with_observations(stores: dict[str, Store]) -> bool:
    return any(store.observations for store in stores.values())


@app.get("/api/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/stores")
async def find_stores(
    zip_code: str = Query(alias="zip", pattern=r"^\d{5}$"),
    radius_miles: int = Query(default=50, alias="radiusMiles", ge=5, le=150),
) -> dict[str, Any]:
    try:
        return await StoreDirectoryClient(cache).find(zip_code, radius_miles)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Could not load nearby stores: {exc}") from exc


@app.post("/api/plans", status_code=status.HTTP_202_ACCEPTED)
async def create_plan(request: PlanRequest) -> dict[str, Any]:
    for existing in jobs.values():
        if existing.status not in {"queued", "running"}:
            continue
        if existing.request == request:
            return {"id": existing.id, "status": existing.status, "progress": existing.progress}
        raise HTTPException(
            status_code=409,
            detail="Another route is already loading stock. Wait for it to finish before starting a new plan.",
        )
    job_id = str(uuid4())
    jobs[job_id] = Job(id=job_id, request=request)
    schedule_plan(job_id)
    return {"id": job_id, "status": "queued", "progress": 0}


@app.get("/api/plans/{job_id}")
async def get_plan(job_id: str) -> Job:
    job = jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Plan not found")
    return job


@app.get("/api/plans/{job_id}/events")
async def plan_events(job_id: str) -> StreamingResponse:
    if job_id not in jobs:
        raise HTTPException(status_code=404, detail="Plan not found")

    async def stream():
        previous = ""
        while True:
            job = jobs.get(job_id)
            if not job:
                return
            serialized = json.dumps(job.model_dump(mode="json"), separators=(",", ":"))
            if serialized != previous:
                yield f"data: {serialized}\n\n"
                previous = serialized
            if job.status in {"completed", "failed"}:
                return
            await asyncio.sleep(0.5)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.post("/api/plans/{job_id}/stores", status_code=status.HTTP_202_ACCEPTED)
async def select_plan_stores(job_id: str, selection: StoreSelectionRequest) -> dict[str, Any]:
    job = jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Plan not found")
    if job_id not in contexts:
        raise HTTPException(status_code=409, detail="Store data is not ready for this plan")
    if job.status in {"queued", "running"}:
        raise HTTPException(status_code=409, detail="This route is already being rebuilt")
    if any(
        other_id != job_id and other.status in {"queued", "running"}
        for other_id, other in jobs.items()
    ):
        raise HTTPException(status_code=409, detail="Another route is currently loading")

    job.status = "queued"
    job.progress = 75
    job.message = "Waiting to rebuild selected stores"
    job.error = None
    schedule_selected_plan(job_id, selection.storeIds)
    return {"id": job_id, "status": "queued", "progress": job.progress}


@app.post("/api/plans/{job_id}/refresh", status_code=status.HTTP_202_ACCEPTED)
async def refresh_plan(job_id: str, response: Response) -> dict[str, Any]:
    job = jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Plan not found")
    if job.status in {"queued", "running"}:
        response.status_code = status.HTTP_409_CONFLICT
        return {"id": job_id, "status": job.status, "progress": job.progress}
    job.status = "queued"
    job.progress = 0
    job.message = "Waiting to refresh"
    job.error = None
    job.result = None
    schedule_plan(job_id)
    return {"id": job_id, "status": "queued", "progress": 0}
