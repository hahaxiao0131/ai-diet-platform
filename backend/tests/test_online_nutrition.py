from __future__ import annotations

import json
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app import main as main_module
from app.agent_tools import AgentToolRegistry
from app.main import app
from app.models import AIAction, Food, Nutrition, Profile
from app.nutrition_sources import OpenFoodFactsProvider
from app.online_nutrition import (
    CascadingOnlineNutritionProvider,
    DeepSeekWebNutritionProvider,
    OnlineNutritionLookupResult,
    validate_online_source_url,
)
from app.providers import MockAdviceProvider
from app.services import calculate_goal, scale_nutrition
from app.store import MemoryStore


client = TestClient(app)


class FakeResponses:
    def __init__(self, output_text: str):
        self.output_text = output_text
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(output_text=self.output_text)


class FakeClient:
    def __init__(self, output_text: str):
        self.responses = FakeResponses(output_text)


class FakeHttpResponse:
    def __init__(self, payload: dict):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def read(self):
        return json.dumps(self.payload).encode("utf-8")


class StubOnlineProvider:
    def __init__(self, food: Food):
        self.food = food

    def search(self, *, query, brand, barcode, limit):
        return OnlineNutritionLookupResult(
            foods=[self.food],
            provider="fake_online",
            query=" ".join(part for part in [brand, query, barcode] if part),
            message="已找到带来源的联网营养数据",
        )


def online_food(*, name: str | None = None, source_url: str | None = None) -> Food:
    suffix = uuid4().hex[:8]
    return Food(
        name=name or f"测试品牌牛肉三明治-{suffix}",
        aliases=["测试牛肉三明治"],
        food_type="packaged",
        default_unit="1份(120g)",
        default_weight_g=120,
        source="deepseek_web_search",
        source_version="responses-web-search-v1",
        source_url=source_url or f"https://brand.example.com/nutrition/{suffix}",
        brand="测试品牌",
        nutrition_per_100g=Nutrition(
            energy_kcal=210,
            protein_g=12,
            fat_g=8,
            carbs_g=23,
            fiber_g=2,
            sodium_mg=480,
            sugars_g=3,
        ),
        tags=["online_lookup", "source_type:brand_official"],
        confidence="medium",
    )


def make_registry(food: Food):
    memory_store = MemoryStore(None)
    profile = Profile(
        user_id=uuid4(),
        sex="female",
        age=30,
        height_cm=165,
        current_weight_kg=60,
        activity_level="moderate",
        primary_goal="maintain",
        goal_pace="standard",
        onboarding_completed=True,
    )
    goal = calculate_goal(profile)
    goal.status = "active"
    return memory_store, AgentToolRegistry(
        profile=profile,
        goal=goal,
        foods=memory_store.foods,
        meals=[],
        memories=[],
        advice_provider=MockAdviceProvider(),
        online_nutrition_provider=StubOnlineProvider(food),
    )


def setup_ready_user(key: str):
    login = client.post("/api/v1/auth/mock-login", json={"mock_user_key": key})
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    profile = client.put(
        "/api/v1/profiles/me",
        json={
            "sex": "female",
            "age": 30,
            "height_cm": 165,
            "current_weight_kg": 60,
            "activity_level": "moderate",
            "primary_goal": "maintain",
            "goal_pace": "standard",
        },
        headers=headers,
    ).json()["data"]
    proposal_id = client.post("/api/v1/goals/recalculate", headers=headers).json()["data"]["id"]
    assert client.post(f"/api/v1/goals/{proposal_id}/confirm", headers=headers).status_code == 200
    return headers, profile["id"]


def build_action(profile_id: str, food: Food) -> AIAction:
    nutrition = scale_nutrition(food.nutrition_per_100g, 120)
    return AIAction(
        profile_id=profile_id,
        action_type="create_meal",
        title="确认这次饮食记录",
        summary=f"{food.name} 120g",
        payload={
            "meal_type": "lunch",
            "items": [{
                "food_id": str(food.id),
                "name": food.name,
                "weight_g": 120,
                "consumed_ratio": 1,
                "data_source": food.source,
                "source_url": food.source_url,
                "confidence": food.confidence,
            }],
            "pending_foods": [food.model_dump(mode="json")],
        },
        preview_nutrition=nutrition,
        confidence="medium",
        assumptions=["按来源页面的一份 120g 记录"],
    )


def test_deepseek_web_search_requires_cited_structured_data_and_normalizes_to_100g():
    payload = {
        "results": [{
            "name": "某品牌牛肉三明治",
            "brand": "某品牌",
            "basis_weight_g": 120,
            "serving_description": "1份(120g)",
            "energy_kcal": 252,
            "protein_g": 14.4,
            "fat_g": 9.6,
            "carbs_g": 27.6,
            "fiber_g": 2.4,
            "sodium_mg": 576,
            "sugars_g": 3.6,
            "allergens": ["麸质"],
            "source_name": "某品牌营养信息",
            "source_url": "https://brand.example.com/products/beef-sandwich",
            "source_type": "brand_official",
        }]
    }
    fake_client = FakeClient(json.dumps(payload, ensure_ascii=False))
    provider = DeepSeekWebNutritionProvider(
        api_key="test-key",
        base_url="https://api.deepseek.com",
        model="test-web-model",
        timeout_seconds=5,
        client=fake_client,
    )

    result = provider.search(query="牛肉三明治", brand="某品牌", barcode=None, limit=3)

    assert result.error_code is None
    assert len(result.foods) == 1
    assert result.foods[0].nutrition_per_100g.energy_kcal == pytest.approx(210)
    assert result.foods[0].nutrition_per_100g.protein_g == pytest.approx(12)
    assert result.foods[0].confidence == "medium"
    assert result.foods[0].allergens == ["麸质"]
    call = fake_client.responses.calls[0]
    assert call["tools"] == [{"type": "web_search"}]
    assert call["tool_choice"] == "required"
    assert call["reasoning"] == {"effort": "low"}
    assert call["max_output_tokens"] == 4000
    assert call["text"]["format"]["type"] == "json_schema"

    cached = provider.search(query="牛肉三明治", brand="某品牌", barcode=None, limit=3)
    assert len(cached.foods) == 1
    assert cached.message == "已使用缓存的带来源营养数据"
    assert len(fake_client.responses.calls) == 1


def test_open_food_facts_name_search_precedes_model_web_search(monkeypatch):
    product = {
        "code": "1234567890123",
        "product_name_zh": "测试品牌牛肉三明治",
        "brands": "测试品牌",
        "serving_quantity": 120,
        "allergens_tags": ["en:peanuts", "en:milk"],
        "nutriments": {
            "energy-kcal_100g": 210,
            "proteins_100g": 12,
            "fat_100g": 8,
            "carbohydrates_100g": 23,
        },
    }
    monkeypatch.setattr(
        "app.nutrition_sources.urlopen",
        lambda request, timeout: FakeHttpResponse({"products": [product]}),
    )
    structured_provider = OpenFoodFactsProvider(timeout_seconds=1)

    class FailingWebProvider:
        def search(self, **kwargs):
            raise AssertionError("structured source should have short-circuited web search")

    provider = CascadingOnlineNutritionProvider(structured_provider, FailingWebProvider())
    result = provider.search(
        query="测试品牌牛肉三明治", brand="测试品牌", barcode=None, limit=3,
    )

    assert result.provider == "open_food_facts"
    assert len(result.foods) == 1
    assert result.foods[0].barcode == "1234567890123"
    assert result.foods[0].confidence == "low"
    assert result.foods[0].source_url == "https://world.openfoodfacts.org/product/1234567890123"
    assert result.foods[0].allergens == ["花生", "牛奶"]


def test_open_food_facts_name_search_rejects_same_brand_with_wrong_pack_size(monkeypatch):
    wrong_size = {
        "code": "5449000054227",
        "product_name": "Coca-Cola Original Taste 500ml",
        "brands": "Coca-Cola",
        "quantity": "500 ml",
        "nutriments": {"energy-kcal_100g": 42, "carbohydrates_100g": 10.6},
    }
    right_size = {
        "code": "5449000000996",
        "product_name": "Coca-Cola Original Taste",
        "brands": "Coca-Cola",
        "quantity": "33 cl",
        "nutriments": {"energy-kcal_100g": 42, "carbohydrates_100g": 10.6},
    }
    monkeypatch.setattr(
        "app.nutrition_sources.urlopen",
        lambda request, timeout: FakeHttpResponse({"products": [wrong_size, right_size]}),
    )

    foods = OpenFoodFactsProvider(timeout_seconds=1).search_by_name(
        "Coca-Cola Original Taste 330ml", brand="Coca-Cola", limit=2,
    )

    assert [food.barcode for food in foods] == ["5449000000996"]


@pytest.mark.parametrize("url", [
    "http://brand.example.com/label",
    "https://127.0.0.1/label",
    "https://192.168.1.8/label",
    "https://www.google.com/search?q=nutrition",
])
def test_online_source_url_rejects_unverifiable_or_unsafe_pages(url):
    with pytest.raises(ValueError):
        validate_online_source_url(url)


def test_online_candidate_stays_in_agent_session_and_nutrition_is_calculated_by_backend():
    food = online_food()
    memory_store, registry = make_registry(food)

    local = registry.execute("search_food", {"query": food.name, "barcode": None, "limit": 3})
    online = registry.execute("search_online_nutrition", {
        "query": food.name, "brand": food.brand, "barcode": None, "limit": 3,
    })
    calculated = registry.execute("calculate_nutrition", {"food_id": str(food.id), "weight_g": 120})
    proposal = registry.execute("propose_meal_record", {
        "meal_type": "lunch",
        "items": [{"food_id": str(food.id), "weight_g": 120, "estimated_weight_range_g": None}],
        "assumptions": ["按联网来源的一份 120g 记录"],
        "body_feedback": None,
    })

    assert local.data["clarification_required"] is False
    assert local.data["online_attempted"] is True
    assert local.data["matches"][0]["standard_name"] == food.name
    assert local.data["matches"][0]["source_url"] == food.source_url
    assert online.success is True
    assert online.data["matches"][0]["source_url"] == food.source_url
    assert all(item.id != food.id for item in memory_store.foods)
    assert calculated.data["nutrition"]["energy_kcal"] == pytest.approx(252)
    assert proposal.data["action"]["preview_nutrition"]["energy_kcal"] == pytest.approx(252)
    assert proposal.data["action"]["payload"]["pending_foods"][0]["id"] == str(food.id)


def test_search_food_automatically_prefers_online_for_an_unknown_branded_food():
    food = online_food(name="赛百味牛肉三明治")
    _, registry = make_registry(food)

    result = registry.execute(
        "search_food",
        {"query": "赛百味牛肉三明治", "barcode": None, "limit": 3},
    )

    assert result.success is True
    assert result.data["online_attempted"] is True
    assert result.data["lookup_provider"] == "fake_online"
    assert result.data["clarification_required"] is False
    assert result.data["matches"][0]["food_id"] == str(food.id)
    assert result.data["matches"][0]["source_url"] == food.source_url


def test_online_food_is_persisted_only_after_owner_confirms():
    headers, profile_id = setup_ready_user(f"online-confirm-{uuid4().hex}")
    other_headers, _ = setup_ready_user(f"online-confirm-other-{uuid4().hex}")
    food = online_food()
    action = build_action(profile_id, food)
    main_module.store.ai_actions[action.id] = action

    assert all(item.id != food.id for item in main_module.store.foods)
    assert client.post(f"/api/v1/ai/actions/{action.id}/confirm", headers=other_headers).status_code == 404
    response = client.post(f"/api/v1/ai/actions/{action.id}/confirm", headers=headers)

    assert response.status_code == 200
    assert response.json()["data"]["meal"]["nutrition"]["energy_kcal"] == pytest.approx(252)
    assert any(item.id == food.id for item in main_module.store.foods)
    owner_results = client.get("/api/v1/foods/search", params={"q": food.name}, headers=headers).json()["data"]
    other_results = client.get("/api/v1/foods/search", params={"q": food.name}, headers=other_headers).json()["data"]
    assert any(item["id"] == str(food.id) for item in owner_results)
    assert all(item["id"] != str(food.id) for item in other_results)


def test_cancel_or_unsafe_source_never_persists_online_food():
    headers, profile_id = setup_ready_user(f"online-cancel-{uuid4().hex}")
    cancelled_food = online_food()
    cancelled_action = build_action(profile_id, cancelled_food)
    main_module.store.ai_actions[cancelled_action.id] = cancelled_action

    assert client.post(f"/api/v1/ai/actions/{cancelled_action.id}/cancel", headers=headers).status_code == 200
    assert all(item.id != cancelled_food.id for item in main_module.store.foods)

    unsafe_food = online_food(source_url="https://127.0.0.1/private-label")
    unsafe_action = build_action(profile_id, unsafe_food)
    main_module.store.ai_actions[unsafe_action.id] = unsafe_action
    rejected = client.post(f"/api/v1/ai/actions/{unsafe_action.id}/confirm", headers=headers)

    assert rejected.status_code == 409
    assert rejected.json()["detail"]["code"] == "ONLINE_FOOD_REJECTED"
    assert all(item.id != unsafe_food.id for item in main_module.store.foods)
