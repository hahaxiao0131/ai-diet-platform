from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .agent_tools import AgentToolRegistry, AgentToolResult
from .models import (
    AgentConfidence,
    AgentDecisionStage,
    AgentToolCallAudit,
    AIAction,
    AIResponseKind,
    IntentInterpretation,
)


SYSTEM_INSTRUCTIONS = """你是 AI 个性化饮食管理平台的单智能体饮食助手。你负责理解用户最终目标，并自主选择提供的确定性工具完成任务。

必须遵守：
1. 涉及用户今天摄入、缺口、近期习惯或个性化建议时，先读取对应上下文。
2. 涉及具体食物，先 search_food；份量需要换算时调用 convert_food_portion；所有营养值必须调用 calculate_nutrition，禁止自行计算、补全或编造。
3. 只能使用 search_food 返回的 food_id。工具报告不存在、歧义或信息不足时，不得绕过，应追问用户。
4. 记餐只能调用 propose_meal_record，保存偏好只能调用 propose_memory。它们只是待确认动作，用户确认前不算正式记录。
5. 一句话有多个目标时连续完成，例如先生成午餐待确认记录，再结合上下文和该餐预览给晚餐建议。
6. 可以估算时明确写出工具返回的换算假设和置信度；估算会明显影响结果时追问。
7. 医疗、疾病、怀孕、进食障碍、诊断、治疗和用药问题不得提供诊断或治疗方案。外层还有确定性安全守卫。
8. 不展示隐藏推理。最终只输出面向用户的结论、数据依据、工具结果摘要和待确认边界。
9. 最终严格遵守 Structured Outputs Schema。action_id 只能填写提案工具返回的动作 ID；没有动作时为 null。
10. “包含食物和吃字”不等于记餐。疑问、计划、愿望、可能或假设行为不能调用 propose_meal_record。
"""


class AgentFinalAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid")

    goal: str = Field(description="对用户本轮最终目标的简短概括")
    kind: AIResponseKind
    message: str
    basis: list[str]
    suggestions: list[str]
    confidence: AgentConfidence
    decision_stage: AgentDecisionStage
    needs_clarification: bool
    clarification_options: list[str]
    action_id: UUID | None
    cta: str | None


class AgentRunResult(BaseModel):
    final: AgentFinalAnswer
    action: AIAction | None = None
    tool_calls: list[AgentToolCallAudit] = Field(default_factory=list)
    provider: str = "openai"
    model: str
    latency_ms: int


@dataclass(frozen=True)
class AgentSettings:
    provider: str
    api_key: str | None
    model: str | None
    timeout_seconds: float
    max_tool_turns: int
    reasoning_effort: str = "low"
    max_retries: int = 1
    base_url: str | None = None

    @classmethod
    def from_env(cls) -> "AgentSettings":
        try:
            timeout = max(1.0, float(os.getenv("DIET_AI_TIMEOUT_SECONDS", "20")))
        except ValueError:
            timeout = 20.0
        try:
            max_turns = min(6, max(1, int(os.getenv("DIET_AI_MAX_TOOL_TURNS", "6"))))
        except ValueError:
            max_turns = 6
        try:
            max_retries = min(3, max(0, int(os.getenv("DIET_AI_MAX_RETRIES", "1"))))
        except ValueError:
            max_retries = 1
        reasoning_effort = os.getenv("OPENAI_REASONING_EFFORT", "low").strip().lower() or "low"
        if reasoning_effort not in {"low", "medium", "high"}:
            reasoning_effort = "low"
        return cls(
            provider=os.getenv("DIET_AI_PROVIDER", "rule").strip().lower(),
            api_key=os.getenv("OPENAI_API_KEY") or None,
            model=os.getenv("OPENAI_MODEL") or None,
            timeout_seconds=timeout,
            max_tool_turns=max_turns,
            reasoning_effort=reasoning_effort,
            max_retries=max_retries,
            base_url=os.getenv("OPENAI_BASE_URL") or None,
        )

    @property
    def openai_enabled(self) -> bool:
        return self.provider == "openai" and bool(self.api_key and self.model)


class AgentProviderError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.tool_calls: list[AgentToolCallAudit] = []
        self.latency_ms: int | None = None


def create_openai_client(settings: AgentSettings) -> Any:
    try:
        from openai import OpenAI
    except ImportError as exc:
        raise AgentProviderError("SDK_UNAVAILABLE", "OpenAI SDK 未安装") from exc
    client_options: dict[str, Any] = {
        "api_key": settings.api_key,
        "timeout": settings.timeout_seconds,
        "max_retries": settings.max_retries,
    }
    if settings.base_url:
        client_options["base_url"] = settings.base_url
    return OpenAI(**client_options)


class OpenAIAgentProvider:
    provider_name = "openai"

    def __init__(self, settings: AgentSettings, client: Any | None = None) -> None:
        if not settings.api_key or not settings.model:
            raise ValueError("OpenAI provider requires OPENAI_API_KEY and OPENAI_MODEL")
        self.settings = settings
        self.model = settings.model
        if client is None:
            client = create_openai_client(settings)
        self.client = client

    def run(
        self,
        message: str,
        tools: AgentToolRegistry,
        interpretation: IntentInterpretation | None = None,
        final_action_allowed: bool | None = None,
    ) -> AgentRunResult:
        started = time.perf_counter()
        conversation: list[dict[str, Any]] = [
            {"role": "user", "content": [{"type": "input_text", "text": message}]}
        ]
        cached_results: dict[str, AgentToolResult] = {}
        audits: list[AgentToolCallAudit] = []
        run_instructions = SYSTEM_INSTRUCTIONS
        if interpretation is not None:
            run_instructions += (
                "\n后端结构化意图（只作为业务约束，不包含隐藏推理）："
                + interpretation.model_dump_json()
                + f"\n后端是否允许创建动作：{bool(final_action_allowed)}。若为 false，不得调用提案工具。"
            )

        try:
            for turn in range(self.settings.max_tool_turns + 1):
                response = self.client.responses.parse(
                    model=self.model,
                    instructions=run_instructions,
                    input=conversation,
                    text_format=AgentFinalAnswer,
                    tools=tools.openai_tools(),
                    tool_choice="auto",
                    parallel_tool_calls=False,
                    max_tool_calls=self.settings.max_tool_turns * 4,
                    reasoning={"effort": self.settings.reasoning_effort},
                    store=False,
                    timeout=self.settings.timeout_seconds,
                )
                output_items = list(getattr(response, "output", []) or [])
                calls = [item for item in output_items if self._item_value(item, "type") == "function_call"]
                if not calls:
                    final = self._parse_final(getattr(response, "output_parsed", None))
                    action = tools.proposal(final.action_id)
                    if final.action_id is None and final.decision_stage == "propose":
                        action = tools.proposal(None)
                    if final.action_id is not None and action is None:
                        raise AgentProviderError("INVALID_ACTION_ID", "模型引用了不存在的待确认动作")
                    return AgentRunResult(
                        final=final,
                        action=action,
                        tool_calls=audits,
                        model=self.model,
                        latency_ms=round((time.perf_counter() - started) * 1000),
                    )

                if turn >= self.settings.max_tool_turns:
                    raise AgentProviderError("TOOL_TURN_LIMIT", "工具调用已达到轮数上限")

                conversation.extend(self._serialize_item(item) for item in output_items)
                new_call_count = 0
                for call in calls:
                    name = str(self._item_value(call, "name") or "")
                    call_id = str(self._item_value(call, "call_id") or "")
                    arguments = self._parse_arguments(self._item_value(call, "arguments"))
                    signature = f"{name}:{json.dumps(arguments, ensure_ascii=False, sort_keys=True, separators=(',', ':'))}"
                    cached = signature in cached_results
                    if cached:
                        result = cached_results[signature]
                    else:
                        result = tools.execute(name, arguments)
                        cached_results[signature] = result
                        new_call_count += 1
                    audits.append(self._audit(name, arguments, result, cached=cached))
                    conversation.append({
                        "type": "function_call_output",
                        "call_id": call_id,
                        "output": result.model_dump_json(),
                    })
                if calls and new_call_count == 0:
                    raise AgentProviderError("TOOL_LOOP", "模型重复调用相同工具且没有产生新进展")
        except AgentProviderError as exc:
            exc.tool_calls = list(audits)
            exc.latency_ms = round((time.perf_counter() - started) * 1000)
            raise
        except Exception as exc:
            classified = self._classify_error(exc)
            classified.tool_calls = list(audits)
            classified.latency_ms = round((time.perf_counter() - started) * 1000)
            raise classified from exc

        raise AgentProviderError("TOOL_TURN_LIMIT", "工具调用已达到轮数上限")

    @staticmethod
    def _item_value(item: Any, key: str) -> Any:
        return item.get(key) if isinstance(item, dict) else getattr(item, key, None)

    @staticmethod
    def _serialize_item(item: Any) -> dict[str, Any]:
        if isinstance(item, dict):
            return item
        if hasattr(item, "model_dump"):
            return item.model_dump(mode="json", exclude_none=True)
        raise AgentProviderError("FORMAT_ERROR", "模型返回了无法识别的输出项")

    @staticmethod
    def _parse_arguments(raw: Any) -> dict[str, Any]:
        if isinstance(raw, dict):
            return raw
        try:
            value = json.loads(raw or "{}")
        except (TypeError, json.JSONDecodeError) as exc:
            raise AgentProviderError("FORMAT_ERROR", "模型工具参数不是有效 JSON") from exc
        if not isinstance(value, dict):
            raise AgentProviderError("FORMAT_ERROR", "模型工具参数必须是 JSON 对象")
        return value

    @staticmethod
    def _parse_final(raw: Any) -> AgentFinalAnswer:
        try:
            return raw if isinstance(raw, AgentFinalAnswer) else AgentFinalAnswer.model_validate(raw)
        except ValidationError as exc:
            raise AgentProviderError("FORMAT_ERROR", "模型最终回答格式不符合约定") from exc

    @staticmethod
    def _audit(name: str, arguments: dict[str, Any], result: AgentToolResult, *, cached: bool) -> AgentToolCallAudit:
        safe_arguments = {
            key: (value[:120] if isinstance(value, str) else value)
            for key, value in arguments.items()
        }
        if result.success:
            data = result.data or {}
            if "matches" in data:
                summary = f"返回 {len(data['matches'])} 个食物结果"
            elif "plans" in data:
                summary = f"生成 {len(data['plans'])} 个候选方案"
            elif "meals" in data:
                summary = f"返回 {len(data['meals'])} 条记录"
            elif "action" in data:
                summary = "生成待确认动作"
            else:
                summary = "调用成功"
        else:
            summary = result.error.message if result.error else "调用失败"
        return AgentToolCallAudit(
            name=name,
            effect=result.effect,
            arguments=safe_arguments,
            success=result.success,
            result_summary=summary,
            error_code=result.error.code if result.error else None,
            cached=cached,
        )

    @staticmethod
    def _classify_error(exc: Exception) -> AgentProviderError:
        name = exc.__class__.__name__.lower()
        message = str(exc).lower()
        if "timeout" in name or "timeout" in message:
            return AgentProviderError("TIMEOUT", "模型请求超时")
        if "ratelimit" in name or "rate limit" in message or "429" in message:
            return AgentProviderError("RATE_LIMIT", "模型服务当前繁忙")
        if "authentication" in name or "api key" in message or "401" in message:
            return AgentProviderError("AUTHENTICATION", "模型认证失败")
        return AgentProviderError("PROVIDER_ERROR", "模型服务暂时不可用")


def build_openai_provider_from_env(client: Any | None = None) -> OpenAIAgentProvider | None:
    settings = AgentSettings.from_env()
    if not settings.openai_enabled:
        return None
    return OpenAIAgentProvider(settings, client=client)
