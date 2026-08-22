from __future__ import annotations

import json
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app import main as main_module
from app.agent_tools import AgentToolRegistry
from app.main import app
from app.models import Meal, MealItem, Nutrition, Profile
from app.openai_agent import (
    AgentProviderError,
    AgentSettings,
    OpenAIAgentProvider,
    build_openai_provider_from_env,
)
from app.providers import MockAdviceProvider
from app.services import calculate_goal, calculate_meal, scale_nutrition
from app.store import MemoryStore


client = TestClient(app)


class FakeResponses:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if not self.responses:
            raise AssertionError("fake response queue exhausted")
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    def parse(self, **kwargs):
        return self.create(**kwargs)


class FakeClient:
    def __init__(self, responses):
        self.responses = FakeResponses(responses)


def tool_call(call_id: str, name: str, arguments: dict):
    return SimpleNamespace(
        output=[{"type": "function_call", "call_id": call_id, "name": name, "arguments": json.dumps(arguments, ensure_ascii=False)}],
        output_text="",
    )


def final_response(**overrides):
    payload = {
        "goal": "完成饮食任务",
        "kind": "dietary_knowledge",
        "message": "已根据后端数据完成。",
        "basis": ["营养值来自后端食物库"],
        "suggestions": [],
        "confidence": "medium",
        "decision_stage": "inform",
        "needs_clarification": False,
        "clarification_options": [],
        "action_id": None,
        "cta": None,
    }
    payload.update(overrides)
    from app.openai_agent import AgentFinalAnswer

    return SimpleNamespace(output=[], output_parsed=AgentFinalAnswer.model_validate(payload))


def make_registry(*, meals=None):
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
    return AgentToolRegistry(
        profile=profile,
        goal=goal,
        foods=memory_store.foods,
        meals=meals or [],
        memories=[],
        advice_provider=MockAdviceProvider(),
    )


def provider_with(responses, *, max_turns=6):
    settings = AgentSettings(
        provider="openai",
        api_key="test-key",
        model="test-model",
        timeout_seconds=30,
        max_tool_turns=max_turns,
    )
    fake_client = FakeClient(responses)
    return OpenAIAgentProvider(settings, client=fake_client), fake_client


def setup_ready_user(key: str):
    login = client.post("/api/v1/auth/mock-login", json={"mock_user_key": key})
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    client.put(
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
    )
    proposal_id = client.post("/api/v1/goals/recalculate", headers=headers).json()["data"]["id"]
    client.post(f"/api/v1/goals/{proposal_id}/confirm", headers=headers)
    return headers


def test_tools_calculate_rice_and_eggs_from_backend_and_only_propose_record():
    registry = make_registry()
    rice = registry.execute("search_food", {"query": "米饭", "barcode": None, "limit": 3}).data["matches"][0]
    egg = registry.execute("search_food", {"query": "鸡蛋", "barcode": None, "limit": 3}).data["matches"][0]

    rice_nutrition = registry.execute("calculate_nutrition", {"food_id": rice["food_id"], "weight_g": 150})
    egg_nutrition = registry.execute("calculate_nutrition", {"food_id": egg["food_id"], "weight_g": 100})
    proposal = registry.execute("propose_meal_record", {
        "meal_type": "lunch",
        "items": [
            {"food_id": rice["food_id"], "weight_g": 150},
            {"food_id": egg["food_id"], "weight_g": 100},
        ],
        "assumptions": ["两个鸡蛋按每个约 50g 换算"],
    })

    action = proposal.data["action"]
    expected_energy = rice_nutrition.data["nutrition"]["energy_kcal"] + egg_nutrition.data["nutrition"]["energy_kcal"]
    assert action["status"] == "proposed"
    assert action["preview_nutrition"]["energy_kcal"] == pytest.approx(expected_energy)
    assert action["payload"]["items"][0]["food_id"] == rice["food_id"]


def test_crab_count_uses_explicit_low_confidence_edible_portion_estimate():
    registry = make_registry()
    crab = registry.execute("search_food", {"query": "小闸蟹", "barcode": None, "limit": 3})

    assert crab.success is True
    match = crab.data["matches"][0]
    assert match["standard_name"] == "螃蟹"

    portion = registry.execute("convert_food_portion", {
        "food_id": match["food_id"],
        "amount": 3,
        "unit": "只",
    })

    assert portion.success is True
    assert portion.data["weight_g"] == 150
    assert portion.data["confidence"] == "low"
    assert "每只可食部分约 50g" in portion.data["assumption"]
    assert portion.data["clarification_required"] is False


def test_ambiguous_serving_requires_clarification_and_invalid_food_id_is_rejected():
    registry = make_registry()
    fried_rice = registry.execute("search_food", {"query": "炒饭", "barcode": None, "limit": 3}).data["matches"][0]
    portion = registry.execute("convert_food_portion", {
        "food_id": fried_rice["food_id"], "amount": 1, "unit": "份",
    })
    assert portion.success is True
    assert portion.data["clarification_required"] is True
    assert portion.data["confidence"] == "low"

    missing = registry.execute("calculate_nutrition", {"food_id": str(uuid4()), "weight_g": 100})
    assert missing.success is False
    assert missing.error.code == "FOOD_NOT_FOUND"


def test_single_agent_completes_xiaolongbao_record_and_dinner_preview_in_six_tool_turns():
    registry = make_registry()
    xiaolongbao = next(food for food in registry.foods if food.name == "小笼包")
    responses = [
        tool_call("c1", "get_today_context", {}),
        tool_call("c2", "search_food", {"query": "小笼包", "barcode": None, "limit": 3}),
        tool_call("c3", "convert_food_portion", {"food_id": str(xiaolongbao.id), "amount": 4, "unit": "个"}),
        tool_call("c4", "calculate_nutrition", {"food_id": str(xiaolongbao.id), "weight_g": 120}),
        tool_call("c5", "propose_meal_record", {
            "meal_type": "lunch",
            "items": [{"food_id": str(xiaolongbao.id), "weight_g": 120}],
            "assumptions": ["小笼包按每个约 30g 换算"],
        }),
        tool_call("c6", "preview_meal_plans", {"meal_type": "dinner", "scenario": None}),
        final_response(
            goal="记录四个小笼包并安排晚餐",
            kind="meal_record_proposal",
            message="午餐已生成待确认记录；晚餐可优先选择均衡方案。只有确认后，小笼包才会计入今日摄入。",
            basis=["四个小笼包按每个约 30g 换算为 120g", "营养和晚餐候选均由后端计算"],
            suggestions=["查看晚餐方案"],
            confidence="medium",
            decision_stage="propose",
            cta="preview_plans",
        ),
    ]
    provider, fake_client = provider_with(responses)

    result = provider.run("我中午吃了四个小笼包，晚上还能吃什么", registry)

    assert result.action is not None
    assert result.action.payload["items"][0]["weight_g"] == 120
    assert result.action.preview_nutrition.energy_kcal == 276
    assert [call.name for call in result.tool_calls] == [
        "get_today_context", "search_food", "convert_food_portion", "calculate_nutrition",
        "propose_meal_record", "preview_meal_plans",
    ]
    assert len(fake_client.responses.calls) == 7


def test_model_invalid_food_id_is_audited_and_never_creates_action():
    registry = make_registry()
    provider, _ = provider_with([
        tool_call("bad1", "calculate_nutrition", {"food_id": str(uuid4()), "weight_g": 100}),
        final_response(
            goal="查询不存在的食物",
            kind="clarification",
            message="没有找到可用食物，请补充名称。",
            confidence="low",
            decision_stage="clarify",
            needs_clarification=True,
            clarification_options=["补充食物名称"],
        ),
    ])

    result = provider.run("帮我算这个食物", registry)

    assert result.action is None
    assert result.tool_calls[0].success is False
    assert result.tool_calls[0].error_code == "FOOD_NOT_FOUND"


def test_identical_tool_calls_are_cached_then_loop_is_stopped():
    registry = make_registry()
    provider, fake_client = provider_with([
        tool_call("same1", "get_today_context", {}),
        tool_call("same2", "get_today_context", {}),
    ])

    with pytest.raises(AgentProviderError) as exc_info:
        provider.run("今天还能吃什么", registry)

    assert exc_info.value.code == "TOOL_LOOP"
    assert len(fake_client.responses.calls) == 2


def test_timeout_falls_back_to_rule_provider_without_breaking_chat(monkeypatch):
    class TimeoutProvider:
        model = "fake-timeout-model"

        def run(self, message, tools, **kwargs):
            raise AgentProviderError("TIMEOUT", "模型请求超时")

    headers = setup_ready_user(f"agent-timeout-{uuid4().hex}")
    monkeypatch.setattr(main_module, "openai_agent_provider", TimeoutProvider())

    response = client.post("/api/v1/ai/chat", json={"message": "200g挂面的热量是多少？"}, headers=headers)
    data = response.json()["data"]

    assert response.status_code == 200
    assert data["kind"] == "food_nutrition"
    assert data["provider"] == "rule"
    assert data["model"] == "fake-timeout-model"
    assert data["fallback_used"] is True
    assert data["fallback_reason"].startswith("TIMEOUT")
    trace = main_module.store.agent_traces[next(reversed(main_module.store.agent_traces))]
    assert trace.fallback_used is True


def test_no_api_key_keeps_application_in_rule_mode(monkeypatch):
    monkeypatch.setenv("DIET_AI_PROVIDER", "openai")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_MODEL", raising=False)

    assert build_openai_provider_from_env() is None


def test_agent_settings_reads_compatible_api_base_url(monkeypatch):
    monkeypatch.setenv("DIET_AI_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "test-relay-key")
    monkeypatch.setenv("OPENAI_MODEL", "gpt-5.5")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://relay.example.test")

    settings = AgentSettings.from_env()

    assert settings.openai_enabled is True
    assert settings.base_url == "https://relay.example.test"


def test_agent_provider_passes_compatible_api_base_url_to_sdk(monkeypatch):
    captured = {}

    def fake_openai(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(responses=SimpleNamespace())

    monkeypatch.setattr("openai.OpenAI", fake_openai)
    settings = AgentSettings(
        "openai",
        "test-relay-key",
        "gpt-5.5",
        20,
        6,
        "low",
        1,
        "https://relay.example.test",
    )

    OpenAIAgentProvider(settings)

    assert captured["base_url"] == "https://relay.example.test"
    assert captured["api_key"] == "test-relay-key"


def test_fried_rice_clarifies_and_actions_cannot_cross_users():
    owner = setup_ready_user(f"agent-owner-{uuid4().hex}")
    other = setup_ready_user(f"agent-other-{uuid4().hex}")

    vague = client.post("/api/v1/ai/chat", json={"message": "我吃了一份炒饭"}, headers=owner).json()["data"]
    assert vague["kind"] == "clarification"
    assert vague["needs_clarification"] is True
    assert vague["action"] is None

    proposal = client.post(
        "/api/v1/ai/chat",
        json={"message": "我中午吃了150克米饭和两个鸡蛋"},
        headers=owner,
    ).json()["data"]
    action_id = proposal["action"]["id"]

    assert client.post(f"/api/v1/ai/actions/{action_id}/confirm", headers=other).status_code == 404
    assert client.post(f"/api/v1/ai/actions/{action_id}/cancel", headers=other).status_code == 404
    assert client.post(f"/api/v1/ai/actions/{action_id}/cancel", headers=owner).status_code == 200
