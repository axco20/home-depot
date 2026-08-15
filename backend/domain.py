from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class Product:
    sku: str
    name: str
    retail_price: float
    image_url: str = ""
    product_url: str = ""


@dataclass(slots=True)
class Observation:
    quantity: int
    price: float
    in_stock: bool
    item_location: str = ""


@dataclass(slots=True)
class Store:
    store_id: str
    name: str
    address: str
    city: str
    state: str
    zip_code: str
    distance_miles: float | None = None
    observations: dict[str, Observation] = field(default_factory=dict)
    latitude: float | None = None
    longitude: float | None = None

    @property
    def full_address(self) -> str:
        return f"{self.address}, {self.city}, {self.state} {self.zip_code}"


def normalize_stock_payloads(payloads: list[dict[str, Any]]) -> tuple[dict[str, Product], dict[str, Store], str]:
    products: dict[str, Product] = {}
    stores: dict[str, Store] = {}
    checked_at = ""

    for payload in payloads:
        item = payload.get("item") or {}
        sku = str(item.get("sku") or "").strip()
        if not sku:
            continue
        products[sku] = Product(
            sku=sku,
            name=str(item.get("name") or f"SKU {sku}"),
            retail_price=float(item.get("retailPrice") or 0),
            image_url=str(item.get("imageUrl") or ""),
            product_url=str(item.get("productUrl") or ""),
        )
        checked_at = max(checked_at, str(payload.get("checkedAt") or ""))

        for raw_store in payload.get("stores") or []:
            store_id = str(raw_store.get("storeId") or "").strip()
            if not store_id:
                continue
            store = stores.setdefault(
                store_id,
                Store(
                    store_id=store_id,
                    name=str(raw_store.get("storeName") or f"Store {store_id}"),
                    address=str(raw_store.get("address") or ""),
                    city=str(raw_store.get("city") or ""),
                    state=str(raw_store.get("state") or ""),
                    zip_code=str(raw_store.get("zip") or ""),
                    distance_miles=(
                        float(raw_store["distance"])
                        if raw_store.get("distance") is not None
                        else None
                    ),
                ),
            )
            if raw_store.get("distance") is not None:
                reported_distance = float(raw_store["distance"])
                store.distance_miles = (
                    reported_distance
                    if store.distance_miles is None
                    else min(store.distance_miles, reported_distance)
                )
            quantity = max(0, int(raw_store.get("quantity") or 0))
            store.observations[sku] = Observation(
                quantity=quantity,
                price=float(raw_store.get("price") or 0),
                in_stock=bool(raw_store.get("inStock")) and quantity > 0,
                item_location=str(raw_store.get("itemLocation") or ""),
            )

    return products, stores, checked_at
