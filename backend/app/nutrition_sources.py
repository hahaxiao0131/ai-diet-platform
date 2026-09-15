from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from typing import Any, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
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
        fields = "code,product_name,product_name_zh,brands,nutriments,serving_quantity,quantity,allergens_tags"
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
        return self._food_from_product(product, barcode=barcode)

    def search_by_name(self, query: str, brand: str | None = None, limit: int = 3) -> list[Food]:
        """Search the structured catalog before invoking a generative web search."""

        terms = query.strip()
        if brand and brand.casefold() not in terms.casefold():
            terms = f"{brand} {terms}".strip()
        if not terms:
            return []
        fields = "code,product_name,product_name_zh,brands,nutriments,serving_quantity,quantity,allergens_tags"
        params = urlencode({
            "search_terms": terms,
            "search_simple": 1,
            "action": "process",
            "json": 1,
            "page_size": min(max(limit * 10, 10), 50),
            "fields": fields,
        })
        request = Request(
            f"https://world.openfoodfacts.org/cgi/search.pl?{params}",
            headers={"User-Agent": self.user_agent, "Accept": "application/json"},
        )
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except (HTTPError, URLError, TimeoutError, OSError, json.JSONDecodeError):
            return []

        foods: list[Food] = []
        seen_codes: set[str] = set()
        for product in payload.get("products") or []:
            code = _text(product.get("code"))
            if not code or code in seen_codes or not self._is_relevant_product(product, query, brand):
                continue
            food = self._food_from_product(product, barcode=code)
            if food is None:
                continue
            food.tags.append("name_lookup")
            food.confidence = "low"
            foods.append(food)
            seen_codes.add(code)
            if len(foods) >= limit:
                break
        return foods

    @staticmethod
    def _is_relevant_product(product: dict[str, Any], query: str, brand: str | None) -> bool:
        name = " ".join(filter(None, [_text(product.get("product_name_zh")), _text(product.get("product_name"))])).casefold()
        product_brand = (_text(product.get("brands")) or "").casefold()
        normalized_query = query.strip().casefold()
        normalized_brand = (brand or "").strip().casefold()
        product_text = " ".join(filter(None, [
            name,
            _text(product.get("quantity")),
            _text(product.get("serving_quantity")),
        ]))
        query_measures = _normalized_measures(normalized_query)
        product_measures = _normalized_measures(product_text)
        if query_measures and not query_measures.intersection(product_measures):
            return False
        query_numbers = set(re.findall(r"\d+(?:\.\d+)?", normalized_query)) if not query_measures else set()
        product_numbers = set(re.findall(
            r"\d+(?:\.\d+)?",
            product_text,
        ))
        if query_numbers and not query_numbers.intersection(product_numbers):
            return False
        name_match = bool(name and (normalized_query in name or name in normalized_query))
        brand_match = bool(normalized_brand and normalized_brand in product_brand)
        return name_match or brand_match

    @staticmethod
    def _food_from_product(product: dict[str, Any], *, barcode: str) -> Food | None:
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
            allergens=_product_allergens(product),
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


def _product_allergens(product: dict[str, Any]) -> list[str]:
    known = {
        "peanuts": "花生",
        "peanut": "花生",
        "milk": "牛奶",
        "eggs": "鸡蛋",
        "egg": "鸡蛋",
        "gluten": "麸质",
        "wheat": "小麦",
        "shellfish": "海鲜",
        "crustaceans": "海鲜",
    }
    values: list[str] = []
    for raw in product.get("allergens_tags") or []:
        normalized = str(raw).split(":", 1)[-1].strip().lower()
        value = known.get(normalized, normalized)
        if value and value not in values:
            values.append(value)
    return values


def _normalized_measures(value: str) -> set[tuple[str, int]]:
    measures: set[tuple[str, int]] = set()
    unit_rules = {
        "ml": ("volume_ml", 1),
        "毫升": ("volume_ml", 1),
        "cl": ("volume_ml", 10),
        "l": ("volume_ml", 1000),
        "升": ("volume_ml", 1000),
        "g": ("weight_g", 1),
        "克": ("weight_g", 1),
        "kg": ("weight_g", 1000),
        "千克": ("weight_g", 1000),
    }
    for amount, unit in re.findall(r"(\d+(?:\.\d+)?)\s*(ml|毫升|cl|kg|千克|g|克|l|升)\b", value.casefold()):
        kind, factor = unit_rules[unit]
        measures.add((kind, round(float(amount) * factor)))
    return measures


def _text(value: Any) -> str | None:
    return str(value).strip() if value is not None and value != "" else None
