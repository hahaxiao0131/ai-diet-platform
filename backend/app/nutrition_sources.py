from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from .models import Food, Nutrition


class PackagedFoodProvider(Protocol):
    def lookup(self, barcode: str) -> Food | None:
        ...


class OpenFoodFactsProvider:
    """Read-only barcode adapter. Numeric values remain outside the AI model."""

    base_url = "https://world.openfoodfacts.org/api/v3/product"

    def __init__(self, timeout_seconds: float | None = None) -> None:
        self.timeout_seconds = timeout_seconds or float(os.getenv("DIET_FOOD_SOURCE_TIMEOUT", "4"))
        self.user_agent = os.getenv("DIET_FOOD_SOURCE_USER_AGENT", "ShiJianDiet/0.1 (local-development)")

    def lookup(self, barcode: str) -> Food | None:
        fields = "code,product_name,product_name_zh,brands,nutriments,serving_quantity,quantity"
        url = f"{self.base_url}/{quote(barcode)}?fields={fields}"
        request = Request(url, headers={"User-Agent": self.user_agent, "Accept": "application/json"})
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except (HTTPError, URLError, TimeoutError, OSError, json.JSONDecodeError):
            return None

        product = payload.get("product") or {}
        if payload.get("status") in {"failure", 0} or not product:
            return None
        nutriments = product.get("nutriments") or {}
        energy = _number(nutriments.get("energy-kcal_100g"))
        if energy is None:
            energy_kj = _number(nutriments.get("energy-kj_100g"))
            energy = energy_kj / 4.184 if energy_kj is not None else None
        if energy is None:
            return None

        name = _text(product.get("product_name_zh")) or _text(product.get("product_name")) or f"条码食品 {barcode}"
        serving = _number(product.get("serving_quantity"))
        nutrition = Nutrition(
            energy_kcal=energy,
            protein_g=_number(nutriments.get("proteins_100g")) or 0,
            fat_g=_number(nutriments.get("fat_100g")) or 0,
            carbs_g=_number(nutriments.get("carbohydrates_100g")) or 0,
            fiber_g=_number(nutriments.get("fiber_100g")) or 0,
            sodium_mg=(_number(nutriments.get("sodium_100g")) or 0) * 1000,
            sugars_g=_number(nutriments.get("sugars_100g")),
            added_sugar_g=None,
        )
        completeness = sum(value is not None for value in [
            _number(nutriments.get("proteins_100g")),
            _number(nutriments.get("fat_100g")),
            _number(nutriments.get("carbohydrates_100g")),
        ])
        return Food(
            name=name.strip(),
            aliases=[],
            food_type="packaged",
            default_unit="1份",
            default_weight_g=serving if serving and serving > 0 else 100,
            source="open_food_facts",
            source_version="api-v3",
            source_url=f"https://world.openfoodfacts.org/product/{barcode}",
            source_observed_at=datetime.now(timezone.utc),
            barcode=barcode,
            brand=_text(product.get("brands")),
            nutrition_per_100g=nutrition,
            tags=["packaged", "barcode_lookup"],
            confidence="medium" if completeness == 3 else "low",
        )


def _number(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _text(value: Any) -> str | None:
    return str(value).strip() if value is not None and value != "" else None
