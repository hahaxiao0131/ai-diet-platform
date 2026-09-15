from __future__ import annotations

import ipaddress
import json
import os
from datetime import datetime, timezone
from typing import Any, Literal, Protocol
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from .models import Food, Nutrition
from .nutrition_sources import PackagedFoodProvider


OnlineSourceType = Literal[
    "brand_official",
    "government_database",
    "open_database",
    "retailer_label",
    "general_web",
]


def validate_online_source_url(value: str) -> str:
    """Accept only direct, public HTTPS evidence pages."""

    parsed = urlparse(value.strip())
    if parsed.scheme != "https" or not parsed.hostname:
        raise ValueError("来源必须是可验证的 HTTPS 页面")
    host = parsed.hostname.lower()
    if host == "localhost":
        raise ValueError("来源不能指向本地地址")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None
    if address and (address.is_private or address.is_loopback or address.is_link_local or address.is_reserved):
        raise ValueError("来源不能指向私有网络地址")
    blocked_search_hosts = {
        "google.com", "www.google.com", "bing.com", "www.bing.com",
        "baidu.com", "www.baidu.com",
    }
    if host in blocked_search_hosts:
        raise ValueError("必须返回原始资料页面，不能返回搜索结果页")
    return value.strip()


class OnlineNutritionItem(BaseModel):
    """Structured extraction target for one sourced nutrition record."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=120)
    brand: str | None = Field(default=None, max_length=120)
    basis_weight_g: float = Field(gt=0, le=5000)
    serving_description: str = Field(min_length=1, max_length=160)
    energy_kcal: float = Field(ge=0, le=900)
    protein_g: float = Field(ge=0, le=100)
    fat_g: float = Field(ge=0, le=100)
    carbs_g: float = Field(ge=0, le=100)
    fiber_g: float = Field(ge=0, le=100)
    sodium_mg: float = Field(ge=0, le=50000)
    sugars_g: float | None = Field(default=None, ge=0, le=100)
    allergens: list[str] = Field(default_factory=list, max_length=20)
    source_name: str = Field(min_length=1, max_length=160)
    source_url: str
    source_type: OnlineSourceType

    @field_validator("source_url")
    @classmethod
    def validate_source_url(cls, value: str) -> str:
        return validate_online_source_url(value)

    @model_validator(mode="after")
    def require_meaningful_nutrition(self):
        if self.energy_kcal <= 0 or not any([self.protein_g, self.fat_g, self.carbs_g, self.sugars_g or 0]):
            raise ValueError("营养数据缺少可用的能量或宏量营养素")
        return self


class OnlineNutritionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    results: list[OnlineNutritionItem] = Field(max_length=5)


class OnlineNutritionLookupResult(BaseModel):
    foods: list[Food] = Field(default_factory=list)
    provider: str
    query: str
    message: str
    error_code: str | None = None


class OnlineNutritionProvider(Protocol):
    def search(
        self,
        *,
        query: str | None,
        brand: str | None,
        barcode: str | None,
        limit: int,
    ) -> OnlineNutritionLookupResult:
        ...


class DeepSeekWebNutritionProvider:
    """Uses DeepSeek web search only to extract cited nutrition facts."""

    provider_name = "deepseek_web_search"

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        model: str,
        timeout_seconds: float,
        max_output_tokens: int = 4000,
        client: Any | None = None,
    ) -> None:
        self.model = model
        self.timeout_seconds = timeout_seconds
        self.max_output_tokens = max_output_tokens
        self._cache: dict[str, list[Food]] = {}
        if client is not None:
            self.client = client
            return
        from openai import OpenAI

        self.client = OpenAI(
            api_key=api_key,
            base_url=base_url.rstrip("/"),
            timeout=timeout_seconds,
            max_retries=0,
        )

    def search(
        self,
        *,
        query: str | None,
        brand: str | None,
        barcode: str | None,
        limit: int,
    ) -> OnlineNutritionLookupResult:
        normalized_query = " ".join(part for part in [brand, query, barcode] if part).strip()
        if not normalized_query:
            return OnlineNutritionLookupResult(
                provider=self.provider_name,
                query="",
                message="缺少食物名称、品牌或条码",
                error_code="QUERY_REQUIRED",
            )
        cache_key = normalized_query.casefold()
        if cache_key in self._cache:
            return OnlineNutritionLookupResult(
                foods=[food.model_copy(deep=True) for food in self._cache[cache_key]][:limit],
                provider=self.provider_name,
                query=normalized_query,
                message="已使用缓存的带来源营养数据",
            )
        schema = OnlineNutritionResponse.model_json_schema()
        instructions = (
            "必须先使用 web_search，只返回与名称和品牌准确匹配的一条食品营养记录。"
            "全部营养数值必须来自同一个直接 HTTPS 页面，包含其对应份量克数 basis_weight_g。"
            "优先品牌官网，其次政府或开放数据库，再次零售商原始标签；不得返回搜索结果页。"
            "allergens 只填写同一来源明确标注的过敏原，未标注时返回空数组。"
            "不得推测、拼接多个页面或用相似食物替代。没有可靠完整数据时 results 返回空数组。"
        )
        try:
            response = self.client.responses.create(
                model=self.model,
                instructions=instructions,
                input=f"查询食物：{normalized_query}。只返回 1 个最可靠的精确候选。",
                tools=[{"type": "web_search"}],
                tool_choice="required",
                reasoning={"effort": "low"},
                text={
                    "format": {
                        "type": "json_schema",
                        "name": "online_nutrition_results",
                        "schema": schema,
                    }
                },
                max_output_tokens=self.max_output_tokens,
            )
            raw = getattr(response, "output_text", None) or self._extract_output_text(response)
            parsed = OnlineNutritionResponse.model_validate_json(raw)
        except ValidationError:
            return OnlineNutritionLookupResult(
                provider=self.provider_name,
                query=normalized_query,
                message="联网结果未通过营养数据格式校验",
                error_code="INVALID_ONLINE_RESULT",
            )
        except Exception:
            return OnlineNutritionLookupResult(
                provider=self.provider_name,
                query=normalized_query,
                message="联网营养查询暂时不可用",
                error_code="ONLINE_PROVIDER_ERROR",
            )

        foods: list[Food] = []
        seen_urls: set[str] = set()
        for item in parsed.results[:limit]:
            if item.source_url in seen_urls:
                continue
            seen_urls.add(item.source_url)
            foods.append(self._to_food(item, normalized_query))
        if foods:
            self._cache[cache_key] = [food.model_copy(deep=True) for food in foods]
        return OnlineNutritionLookupResult(
            foods=foods,
            provider=self.provider_name,
            query=normalized_query,
            message="已找到带来源的联网营养数据" if foods else "未找到可验证的营养资料",
        )

    @staticmethod
    def _extract_output_text(response: Any) -> str:
        if hasattr(response, "model_dump"):
            payload = response.model_dump(mode="json")
        elif isinstance(response, dict):
            payload = response
        else:
            return ""
        for item in reversed(payload.get("output") or []):
            if item.get("type") != "message":
                continue
            for content in item.get("content") or []:
                text = content.get("text")
                if isinstance(text, str) and text.strip():
                    return text
        return ""

    @staticmethod
    def _to_food(item: OnlineNutritionItem, original_query: str) -> Food:
        factor = 100 / item.basis_weight_g
        source_type_confidence: dict[OnlineSourceType, Literal["medium", "low"]] = {
            "brand_official": "medium",
            "government_database": "medium",
            "open_database": "medium",
            "retailer_label": "low",
            "general_web": "low",
        }
        alias = original_query if original_query != item.name and len(original_query) <= 80 else None
        return Food(
            name=item.name.strip(),
            aliases=[alias] if alias else [],
            food_type="packaged" if item.brand else "standard_dish",
            default_unit=item.serving_description,
            default_weight_g=item.basis_weight_g,
            source="deepseek_web_search",
            source_version="responses-web-search-v1",
            source_url=item.source_url,
            source_observed_at=datetime.now(timezone.utc),
            brand=item.brand.strip() if item.brand else None,
            verified_by_user=False,
            nutrition_per_100g=Nutrition(
                energy_kcal=item.energy_kcal * factor,
                protein_g=item.protein_g * factor,
                fat_g=item.fat_g * factor,
                carbs_g=item.carbs_g * factor,
                fiber_g=item.fiber_g * factor,
                sodium_mg=item.sodium_mg * factor,
                sugars_g=item.sugars_g * factor if item.sugars_g is not None else None,
                added_sugar_g=None,
            ),
            allergens=[value.strip() for value in item.allergens if value.strip()],
            tags=["online_lookup", f"source_type:{item.source_type}", f"source_name:{item.source_name}"],
            confidence=source_type_confidence[item.source_type],
        )


class CascadingOnlineNutritionProvider:
    """Structured barcode lookup first, web search second."""

    def __init__(
        self,
        packaged_provider: PackagedFoodProvider,
        web_provider: OnlineNutritionProvider | None,
    ) -> None:
        self.packaged_provider = packaged_provider
        self.web_provider = web_provider

    def search(
        self,
        *,
        query: str | None,
        brand: str | None,
        barcode: str | None,
        limit: int,
    ) -> OnlineNutritionLookupResult:
        if barcode:
            food = self.packaged_provider.lookup(barcode)
            if food is not None:
                return OnlineNutritionLookupResult(
                    foods=[food],
                    provider="open_food_facts",
                    query=barcode,
                    message="已通过条码找到结构化营养数据",
                )
        search_by_name = getattr(self.packaged_provider, "search_by_name", None)
        if query and callable(search_by_name):
            foods = search_by_name(query, brand=brand, limit=limit)
            if foods:
                return OnlineNutritionLookupResult(
                    foods=foods,
                    provider="open_food_facts",
                    query=" ".join(part for part in [brand, query] if part),
                    message="已通过开放食品数据库找到结构化营养数据",
                )
        if self.web_provider is not None and (query or brand):
            return self.web_provider.search(query=query, brand=brand, barcode=barcode, limit=limit)
        return OnlineNutritionLookupResult(
            provider="online_food_sources",
            query=" ".join(part for part in [brand, query, barcode] if part),
            message="没有找到可验证的联网营养数据",
            error_code="ONLINE_NOT_FOUND",
        )


def build_online_nutrition_provider_from_env(
    packaged_provider: PackagedFoodProvider,
    *,
    client: Any | None = None,
) -> OnlineNutritionProvider | None:
    enabled = os.getenv("DIET_ONLINE_FOOD_SEARCH", "1").strip().lower() not in {"0", "false", "off"}
    if not enabled:
        return None
    api_key = os.getenv("DEEPSEEK_API_KEY") or None
    web_provider: OnlineNutritionProvider | None = None
    if api_key:
        base_url = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com").rstrip("/")
        if base_url == "https://api.deepseek.com/beta":
            base_url = "https://api.deepseek.com"
        try:
            timeout = max(2.0, float(os.getenv("DIET_ONLINE_FOOD_TIMEOUT_SECONDS", "30")))
        except ValueError:
            timeout = 30.0
        try:
            max_output_tokens = max(2000, int(os.getenv("DIET_ONLINE_FOOD_MAX_OUTPUT_TOKENS", "4000")))
        except ValueError:
            max_output_tokens = 4000
        web_provider = DeepSeekWebNutritionProvider(
            api_key=api_key,
            base_url=base_url,
            model=os.getenv("DIET_ONLINE_FOOD_MODEL", "deepseek-v4-flash"),
            timeout_seconds=timeout,
            max_output_tokens=max_output_tokens,
            client=client,
        )
    return CascadingOnlineNutritionProvider(packaged_provider, web_provider)
