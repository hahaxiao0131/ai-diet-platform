from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app import main as main_module
from app.intent_provider import IntentProviderResult, OpenAIAssistantProvider
from app.main import app
from app.models import IntentFoodEntity, IntentInterpretation
from app.openai_agent import AgentProviderError, AgentSettings
from app.providers import RuleBasedAssistantProvider
from app.store import seeded_foods


client = TestClient(app)


@pytest.mark.parametrize(("message", "intent", "speech_act", "temporal_status", "should_create"), [
    ("我吃了两根香蕉", "meal_record", "statement", "completed", True),
    ("我刚吃了两根香蕉", "meal_record", "statement", "completed", True),
    ("帮我记录两根香蕉", "meal_record", "command", "unknown", True),
    ("我可以再吃两根香蕉吗", "consumption_advice", "question", "unknown", False),
    ("我想吃两根香蕉", "consumption_advice", "statement", "planned", False),
    ("我准备晚上吃两根香蕉", "consumption_advice", "statement", "planned", False),
    ("如果再吃两根香蕉会超标吗", "consumption_advice", "hypothetical", "hypothetical", False),
    ("两根香蕉多少热量", "food_nutrition", "statement", "unknown", False),
    ("香蕉能换成什么", "food_replacement", "statement", "unknown", False),
    ("我不喜欢香蕉", "memory_preference", "statement", "unknown", True),
    ("两根香蕉呢", "clarification", "statement", "unknown", False),
])
def test_rule_provider_distinguishes_semantic_intents(message, intent, speech_act, temporal_status, should_create):
    result = RuleBasedAssistantProvider().interpret_structured(message, seeded_foods())

    assert result.intent == intent
    assert result.speech_act == speech_act
    assert result.temporal_status == temporal_status
    assert result.should_create_action is should_create
    assert result.foods[0].raw_name == "香蕉"
    if "两根" in message:
        assert result.foods[0].quantity == 2
        assert result.foods[0].unit == "根"
        assert result.foods[0].explicit_weight_g is None


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


@pytest.mark.parametrize("message", [
    "我可以再吃两根香蕉吗",
    "我想吃两根香蕉",
    "我准备晚上吃两根香蕉",
    "如果再吃两根香蕉会超标吗",
])
def test_consulting_planned_and_hypothetical_banana_never_create_actions(message):
    headers = setup_ready_user(f"banana-advice-{uuid4().hex}")
    before = len(main_module.store.ai_actions)

    response = client.post("/api/v1/ai/chat", json={"message": message}, headers=headers).json()["data"]

    assert response["kind"] == "consumption_advice"
    assert response["action"] is None
    assert "香蕉约 200g" in response["message"]
    assert "186 kcal" in response["message"]
    assert any("当前剩余" in item for item in response["basis"])
    assert len(main_module.store.ai_actions) == before


@pytest.mark.parametrize("message", ["我吃了两根香蕉", "我刚吃了两根香蕉", "帮我记录两根香蕉"])
def test_completed_or_explicit_command_creates_only_pending_meal_action(message):
    headers = setup_ready_user(f"banana-record-{uuid4().hex}")

    response = client.post("/api/v1/ai/chat", json={"message": message}, headers=headers).json()["data"]

    assert response["kind"] == "meal_record_proposal"
    assert response["action"]["status"] == "proposed"
    assert response["action"]["payload"]["items"][0]["weight_g"] == 200
    assert client.get("/api/v1/today", headers=headers).json()["data"]["completeness"]["recorded_meals"] == 0
    trace = main_module.store.agent_traces[next(reversed(main_module.store.agent_traces))]
    assert trace.speech_act in {"statement", "command"}
    assert trace.final_action_allowed is True


def test_model_meal_record_with_hypothetical_state_is_blocked_before_agent(monkeypatch):
    class ConflictingIntentProvider:
        def interpret(self, message):
            return IntentProviderResult(
                interpretation=IntentInterpretation(
                    intent="meal_record",
                    speech_act="hypothetical",
                    temporal_status="hypothetical",
                    modality="conditional",
                    foods=[IntentFoodEntity(raw_name="香蕉", quantity=2, unit="根", explicit_weight_g=None)],
                    meal_type=None,
                    should_create_action=True,
                    requires_clarification=False,
                    clarification_question=None,
                    confidence="high",
                ),
                provider="openai",
                model="fake-intent-model",
                latency_ms=3,
            )

    class MustNotRunAgent:
        model = "fake-agent-model"

        def run(self, *args, **kwargs):
            raise AssertionError("action agent must not run after deterministic conflict")

    headers = setup_ready_user(f"intent-conflict-{uuid4().hex}")
    monkeypatch.setattr(main_module, "openai_intent_provider", ConflictingIntentProvider())
    monkeypatch.setattr(main_module, "openai_agent_provider", MustNotRunAgent())

    response = client.post("/api/v1/ai/chat", json={"message": "如果再吃两根香蕉会超标吗"}, headers=headers).json()["data"]

    assert response["action"] is None
    assert response["kind"] == "consumption_advice"
    assert response["intent_conflict"]
    trace = main_module.store.agent_traces[next(reversed(main_module.store.agent_traces))]
    assert trace.should_create_action is True
    assert trace.final_action_allowed is False
    assert trace.intent_conflict


def test_model_unknown_food_is_clarified_without_action(monkeypatch):
    class UnknownFoodIntentProvider:
        def interpret(self, message):
            return IntentProviderResult(
                interpretation=IntentInterpretation(
                    intent="consumption_advice",
                    speech_act="question",
                    temporal_status="planned",
                    modality="possible",
                    foods=[IntentFoodEntity(raw_name="银河果", quantity=2, unit="颗", explicit_weight_g=None)],
                    meal_type=None,
                    should_create_action=False,
                    requires_clarification=False,
                    clarification_question=None,
                    confidence="high",
                ),
                provider="openai",
                model="fake-intent-model",
                latency_ms=2,
            )

    headers = setup_ready_user(f"unknown-food-{uuid4().hex}")
    monkeypatch.setattr(main_module, "openai_intent_provider", UnknownFoodIntentProvider())

    response = client.post("/api/v1/ai/chat", json={"message": "我可以吃两颗银河果吗"}, headers=headers).json()["data"]

    assert response["kind"] == "clarification"
    assert response["action"] is None
    assert response["needs_clarification"] is True


def test_openai_intent_provider_uses_native_structured_output():
    expected = IntentInterpretation(
        intent="consumption_advice",
        speech_act="question",
        temporal_status="hypothetical",
        modality="possible",
        foods=[IntentFoodEntity(raw_name="香蕉", quantity=2, unit="根", explicit_weight_g=None)],
        meal_type=None,
        should_create_action=False,
        requires_clarification=False,
        clarification_question=None,
        confidence="high",
    )

    class FakeResponses:
        def __init__(self):
            self.kwargs = None

        def parse(self, **kwargs):
            self.kwargs = kwargs
            return SimpleNamespace(output_parsed=expected)

    fake_client = SimpleNamespace(responses=FakeResponses())
    provider = OpenAIAssistantProvider(
        AgentSettings("openai", "test-key", "test-model", 20, 6, "low", 1),
        client=fake_client,
    )

    result = provider.interpret("我可以再吃两根香蕉吗")

    assert result.interpretation == expected
    assert fake_client.responses.kwargs["text_format"] is IntentInterpretation
    assert "text" not in fake_client.responses.kwargs


def test_invalid_structured_intent_safely_falls_back_to_rule_provider(monkeypatch):
    class InvalidSchemaProvider:
        def interpret(self, message):
            raise AgentProviderError("FORMAT_ERROR", "模型意图输出不符合 Schema")

    headers = setup_ready_user(f"invalid-schema-{uuid4().hex}")
    monkeypatch.setattr(main_module, "openai_intent_provider", InvalidSchemaProvider())

    response = client.post("/api/v1/ai/chat", json={"message": "我刚吃了两根香蕉"}, headers=headers).json()["data"]

    assert response["kind"] == "meal_record_proposal"
    assert response["fallback_used"] is True
    assert response["fallback_reason"].startswith("FORMAT_ERROR")


def test_medical_message_still_uses_deterministic_safety_guard():
    headers = setup_ready_user(f"medical-guard-{uuid4().hex}")

    response = client.post(
        "/api/v1/ai/chat",
        json={"message": "我在用胰岛素，可以调整药量后吃两根香蕉吗"},
        headers=headers,
    ).json()["data"]

    assert response["kind"] == "safety"
    assert response["provider"] == "safety_guard"
    assert response["action"] is None
