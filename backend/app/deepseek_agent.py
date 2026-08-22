from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ValidationError

from .agent_tools import AgentToolRegistry, AgentToolResult
from .intent_provider import INTENT_INSTRUCTIONS
from .models import AgentToolCallAudit, AIAction, IntentInterpretation
from .openai_agent import (
    AgentFinalAnswer,
    AgentProviderError,
    AgentRunResult,
    OpenAIAgentProvider,
    SYSTEM_INSTRUCTIONS,
)


FINAL_TOOL_NAME = "submit_final_answer"
INTENT_TOOL_NAME = "submit_intent"


@dataclass(frozen=True)
class DeepSeekSettings:
    provider: str
    api_key: str | None
    base_url: str
    model: str | None
    timeout_seconds: float
    max_tool_turns: int
    max_retries: int = 1
    thinking: str = "disabled"

    @classmethod
    def from_env(cls) -> "DeepSeekSettings":
        try:
            timeout = max(1.0, float(os.getenv("DIET_AI_TIMEOUT_SECONDS", "30")))
        except ValueError:
            timeout = 30.0
        try:
            max_turns = min(6, max(1, int(os.getenv("DIET_AI_MAX_TOOL_TURNS", "6"))))
        except ValueError:
            max_turns = 6
        try:
            max_retries = min(3, max(0, int(os.getenv("DIET_AI_MAX_RETRIES", "1"))))
        except ValueError:
            max_retries = 1
        thinking = os.getenv("DEEPSEEK_THINKING", "disabled").strip().lower() or "disabled"
        if thinking not in {"enabled", "disabled"}:
            thinking = "disabled"
        return cls(
            provider=os.getenv("DIET_AI_PROVIDER", "rule").strip().lower(),
            api_key=os.getenv("DEEPSEEK_API_KEY") or None,
            base_url=os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com").rstrip("/"),
            model=os.getenv("DEEPSEEK_MODEL") or None,
            timeout_seconds=timeout,
            max_tool_turns=max_turns,
            max_retries=max_retries,
            thinking=thinking,
        )

    @property
    def enabled(self) -> bool:
        return self.provider == "deepseek" and bool(self.api_key and self.model)


class DeepSeekIntentResult(BaseModel):
    interpretation: IntentInterpretation
    provider: str = "deepseek"
    model: str
    latency_ms: int


def create_deepseek_client(settings: DeepSeekSettings) -> Any:
    try:
        from openai import OpenAI
    except ImportError as exc:
        raise AgentProviderError("SDK_UNAVAILABLE", "OpenAI 兼容 SDK 未安装") from exc
    return OpenAI(
        api_key=settings.api_key,
        base_url=settings.base_url,
        timeout=settings.timeout_seconds,
        max_retries=settings.max_retries,
    )


def _function_tool(name: str, description: str, model: type[BaseModel]) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": model.model_json_schema(),
        },
    }


def _business_tools(registry: AgentToolRegistry) -> list[dict[str, Any]]:
    tools = []
    for item in registry.openai_tools():
        tools.append({
            "type": "function",
            "function": {
                "name": item["name"],
                "description": item["description"],
                "parameters": item["parameters"],
            },
        })
    return tools


def _thinking_body(settings: DeepSeekSettings) -> dict[str, Any]:
    return {"thinking": {"type": settings.thinking}}


def _message_dict(message: Any) -> dict[str, Any]:
    if isinstance(message, dict):
        return message
    if hasattr(message, "model_dump"):
        return message.model_dump(mode="json", exclude_none=True)
    raise AgentProviderError("FORMAT_ERROR", "模型返回了无法识别的消息")


def _tool_calls(message: Any) -> list[Any]:
    if isinstance(message, dict):
        return list(message.get("tool_calls") or [])
    return list(getattr(message, "tool_calls", None) or [])


def _value(item: Any, key: str) -> Any:
    return item.get(key) if isinstance(item, dict) else getattr(item, key, None)


def _function_value(call: Any, key: str) -> Any:
    function = _value(call, "function")
    return _value(function, key)


def _arguments(call: Any) -> dict[str, Any]:
    raw = _function_value(call, "arguments")
    if isinstance(raw, dict):
        return raw
    try:
        value = json.loads(raw or "{}")
    except (TypeError, json.JSONDecodeError) as exc:
        raise AgentProviderError("FORMAT_ERROR", "模型工具参数不是有效 JSON") from exc
    if not isinstance(value, dict):
        raise AgentProviderError("FORMAT_ERROR", "模型工具参数必须是 JSON 对象")
    return value


def _normalize_null_fields(payload: dict[str, Any], fields: set[str]) -> dict[str, Any]:
    normalized = dict(payload)
    for field in fields:
        value = normalized.get(field)
        if isinstance(value, str) and value.strip().lower() in {"", "null", "none"}:
            normalized[field] = None
    return normalized


class DeepSeekIntentProvider:
    provider_name = "deepseek"

    def __init__(self, settings: DeepSeekSettings, client: Any | None = None) -> None:
        if not settings.api_key or not settings.model:
            raise ValueError("DeepSeek provider requires DEEPSEEK_API_KEY and DEEPSEEK_MODEL")
        self.settings = settings
        self.model = settings.model
        self.client = client or create_deepseek_client(settings)

    def interpret(self, message: str) -> DeepSeekIntentResult:
        started = time.perf_counter()
        tool = _function_tool(
            INTENT_TOOL_NAME,
            "提交对用户饮食请求的完整结构化意图。所有字段都必须填写，未知值使用 null。",
            IntentInterpretation,
        )
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": INTENT_INSTRUCTIONS},
                    {"role": "user", "content": message},
                ],
                tools=[tool],
                tool_choice={"type": "function", "function": {"name": INTENT_TOOL_NAME}},
                max_tokens=1200,
                extra_body=_thinking_body(self.settings),
            )
            choices = list(getattr(response, "choices", None) or [])
            if not choices:
                raise AgentProviderError("REFUSAL_OR_EMPTY", "模型未返回结构化意图")
            calls = _tool_calls(choices[0].message)
            call = next((item for item in calls if _function_value(item, "name") == INTENT_TOOL_NAME), None)
            if call is None:
                raise AgentProviderError("FORMAT_ERROR", "模型未调用结构化意图函数")
            intent_payload = _normalize_null_fields(
                _arguments(call),
                {"meal_type", "clarification_question"},
            )
            for food in intent_payload.get("foods", []):
                if isinstance(food, dict):
                    food.update(_normalize_null_fields(food, {"quantity", "unit", "explicit_weight_g"}))
            interpretation = IntentInterpretation.model_validate(intent_payload)
            return DeepSeekIntentResult(
                interpretation=interpretation,
                model=self.model,
                latency_ms=round((time.perf_counter() - started) * 1000),
            )
        except AgentProviderError:
            raise
        except ValidationError as exc:
            raise AgentProviderError("FORMAT_ERROR", "模型意图输出不符合 Schema") from exc
        except Exception as exc:
            raise OpenAIAgentProvider._classify_error(exc) from exc


class DeepSeekAgentProvider:
    provider_name = "deepseek"

    def __init__(self, settings: DeepSeekSettings, client: Any | None = None) -> None:
        if not settings.api_key or not settings.model:
            raise ValueError("DeepSeek provider requires DEEPSEEK_API_KEY and DEEPSEEK_MODEL")
        self.settings = settings
        self.model = settings.model
        self.client = client or create_deepseek_client(settings)

    def run(
        self,
        message: str,
        tools: AgentToolRegistry,
        interpretation: IntentInterpretation | None = None,
        final_action_allowed: bool | None = None,
    ) -> AgentRunResult:
        started = time.perf_counter()
        instructions = SYSTEM_INSTRUCTIONS + (
            "\n你使用 DeepSeek Chat Completions。完成业务工具调用后，必须调用 submit_final_answer，"
            "不得用普通文本结束。"
        )
        if interpretation is not None:
            instructions += (
                "\n后端结构化意图（只作为业务约束，不包含隐藏推理）："
                + interpretation.model_dump_json()
                + f"\n后端是否允许创建动作：{bool(final_action_allowed)}。若为 false，不得调用提案工具。"
            )
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": instructions},
            {"role": "user", "content": message},
        ]
        final_tool = _function_tool(
            FINAL_TOOL_NAME,
            "提交最终用户可见回答。必须在完成所需业务工具后调用。",
            AgentFinalAnswer,
        )
        model_tools = [*_business_tools(tools), final_tool]
        cached_results: dict[str, AgentToolResult] = {}
        audits: list[AgentToolCallAudit] = []

        try:
            for turn in range(self.settings.max_tool_turns + 1):
                response = self.client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    tools=model_tools,
                    tool_choice="auto",
                    max_tokens=1800,
                    extra_body=_thinking_body(self.settings),
                )
                choices = list(getattr(response, "choices", None) or [])
                if not choices:
                    raise AgentProviderError("REFUSAL_OR_EMPTY", "模型未返回可用结果")
                assistant_message = choices[0].message
                calls = _tool_calls(assistant_message)
                if not calls:
                    raise AgentProviderError("FORMAT_ERROR", "模型未通过工具提交最终回答")

                final_calls = [call for call in calls if _function_value(call, "name") == FINAL_TOOL_NAME]
                business_calls = [call for call in calls if _function_value(call, "name") != FINAL_TOOL_NAME]
                if final_calls and not business_calls:
                    final_payload = _normalize_null_fields(_arguments(final_calls[0]), {"action_id", "cta"})
                    final = AgentFinalAnswer.model_validate(final_payload)
                    action = tools.proposal(final.action_id)
                    if final.action_id is None and final.decision_stage == "propose":
                        action = tools.proposal(None)
                    if final.action_id is not None and action is None:
                        raise AgentProviderError("INVALID_ACTION_ID", "模型引用了不存在的待确认动作")
                    return AgentRunResult(
                        final=final,
                        action=action,
                        tool_calls=audits,
                        provider=self.provider_name,
                        model=self.model,
                        latency_ms=round((time.perf_counter() - started) * 1000),
                    )

                if turn >= self.settings.max_tool_turns:
                    raise AgentProviderError("TOOL_TURN_LIMIT", "工具调用已达到轮数上限")

                messages.append(_message_dict(assistant_message))
                new_call_count = 0
                for call in calls:
                    call_id = str(_value(call, "id") or "")
                    name = str(_function_value(call, "name") or "")
                    if name == FINAL_TOOL_NAME:
                        messages.append({
                            "role": "tool",
                            "tool_call_id": call_id,
                            "content": json.dumps({"accepted": False, "reason": "请先处理同轮业务工具，再单独提交最终回答"}, ensure_ascii=False),
                        })
                        continue
                    arguments = _arguments(call)
                    signature = f"{name}:{json.dumps(arguments, ensure_ascii=False, sort_keys=True, separators=(',', ':'))}"
                    cached = signature in cached_results
                    if cached:
                        result = cached_results[signature]
                    else:
                        result = tools.execute(name, arguments)
                        cached_results[signature] = result
                        new_call_count += 1
                    audits.append(OpenAIAgentProvider._audit(name, arguments, result, cached=cached))
                    messages.append({
                        "role": "tool",
                        "tool_call_id": call_id,
                        "content": result.model_dump_json(),
                    })
                if business_calls and new_call_count == 0:
                    raise AgentProviderError("TOOL_LOOP", "模型重复调用相同工具且没有产生新进展")
        except AgentProviderError as exc:
            exc.tool_calls = list(audits)
            exc.latency_ms = round((time.perf_counter() - started) * 1000)
            raise
        except ValidationError as exc:
            error = AgentProviderError("FORMAT_ERROR", "模型最终回答格式不符合约定")
            error.tool_calls = list(audits)
            error.latency_ms = round((time.perf_counter() - started) * 1000)
            raise error from exc
        except Exception as exc:
            error = OpenAIAgentProvider._classify_error(exc)
            error.tool_calls = list(audits)
            error.latency_ms = round((time.perf_counter() - started) * 1000)
            raise error from exc

        raise AgentProviderError("TOOL_TURN_LIMIT", "工具调用已达到轮数上限")


def build_deepseek_intent_from_env(client: Any | None = None) -> DeepSeekIntentProvider | None:
    settings = DeepSeekSettings.from_env()
    if not settings.enabled:
        return None
    return DeepSeekIntentProvider(settings, client=client)


def build_deepseek_agent_from_env(client: Any | None = None) -> DeepSeekAgentProvider | None:
    settings = DeepSeekSettings.from_env()
    if not settings.enabled:
        return None
    return DeepSeekAgentProvider(settings, client=client)
