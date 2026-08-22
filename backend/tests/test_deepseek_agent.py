from __future__ import annotations

import json
from types import SimpleNamespace
from uuid import uuid4

from app.agent_tools import AgentToolRegistry
from app.deepseek_agent import (
    DeepSeekAgentProvider,
    DeepSeekIntentProvider,
    DeepSeekSettings,
    build_deepseek_agent_from_env,
)
from app.models import Profile
from app.providers import MockAdviceProvider
from app.services import calculate_goal
from app.store import MemoryStore


class FakeMessage:
    def __init__(self, calls):
        self.tool_calls = calls
        self.content = None

    def model_dump(self, **_kwargs):
        return {"role": "assistant", "content": None, "tool_calls": self.tool_calls}


class FakeCompletions:
    def __init__(self, messages):
        self.messages = list(messages)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if not self.messages:
            raise AssertionError("fake completion queue exhausted")
        return SimpleNamespace(choices=[SimpleNamespace(message=FakeMessage(self.messages.pop(0)))])


def fake_client(messages):
    completions = FakeCompletions(messages)
    return SimpleNamespace(chat=SimpleNamespace(completions=completions)), completions


def function_call(call_id: str, name: str, arguments: dict):
    return {
        "id": call_id,
        "type": "function",
        "function": {"name": name, "arguments": json.dumps(arguments, ensure_ascii=False)},
    }


def make_registry():
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
        meals=[],
        memories=[],
        advice_provider=MockAdviceProvider(),
    )


def settings():
    return DeepSeekSettings(
        provider="deepseek",
        api_key="test-key",
        base_url="https://api.deepseek.com/beta",
        model="deepseek-v4-flash",
        timeout_seconds=30,
        max_tool_turns=6,
        max_retries=0,
        thinking="disabled",
    )


def test_deepseek_settings_are_read_without_openai_variables(monkeypatch):
    monkeypatch.setenv("DIET_AI_PROVIDER", "deepseek")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-deepseek-key")
    monkeypatch.setenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com/beta/")
    monkeypatch.setenv("DEEPSEEK_MODEL", "deepseek-v4-flash")

    result = DeepSeekSettings.from_env()

    assert result.enabled is True
    assert result.base_url == "https://api.deepseek.com/beta"
    assert result.model == "deepseek-v4-flash"


def test_deepseek_intent_uses_forced_function_and_pydantic_validation():
    intent = {
        "intent": "consumption_advice",
        "speech_act": "question",
        "temporal_status": "planned",
        "modality": "possible",
        "foods": [{"raw_name": "香蕉", "quantity": 2, "unit": "根", "explicit_weight_g": None}],
        "meal_type": None,
        "should_create_action": False,
        "requires_clarification": False,
        "clarification_question": None,
        "confidence": "high",
    }
    client, completions = fake_client([[function_call("intent-1", "submit_intent", intent)]])
    provider = DeepSeekIntentProvider(settings(), client=client)

    result = provider.interpret("我可以再吃两根香蕉吗")

    assert result.interpretation.intent == "consumption_advice"
    assert result.interpretation.should_create_action is False
    assert completions.calls[0]["tool_choice"]["function"]["name"] == "submit_intent"
    assert completions.calls[0]["extra_body"]["thinking"]["type"] == "disabled"


def test_deepseek_agent_executes_backend_tool_then_submits_final_answer():
    final = {
        "goal": "判断今天还能吃什么",
        "kind": "dietary_knowledge",
        "message": "已根据今天的后端营养数据给出建议。",
        "basis": ["今日数据来自后端"],
        "suggestions": [],
        "confidence": "medium",
        "decision_stage": "inform",
        "needs_clarification": False,
        "clarification_options": [],
        "action_id": "null",
        "cta": "null",
    }
    client, completions = fake_client([
        [function_call("tool-1", "get_today_context", {})],
        [function_call("final-1", "submit_final_answer", final)],
    ])
    provider = DeepSeekAgentProvider(settings(), client=client)

    result = provider.run("今天还能吃什么", make_registry())

    assert result.provider == "deepseek"
    assert result.action is None
    assert [call.name for call in result.tool_calls] == ["get_today_context"]
    second_messages = completions.calls[1]["messages"]
    assert any(message.get("role") == "tool" for message in second_messages)


def test_deepseek_builder_requires_key_and_model(monkeypatch):
    monkeypatch.setenv("DIET_AI_PROVIDER", "deepseek")
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("DEEPSEEK_MODEL", raising=False)

    assert build_deepseek_agent_from_env() is None
