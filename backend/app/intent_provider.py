from __future__ import annotations

import time
from typing import Any

from pydantic import BaseModel, ValidationError

from .models import IntentInterpretation
from .openai_agent import AgentProviderError, AgentSettings, OpenAIAgentProvider, create_openai_client


INTENT_INSTRUCTIONS = """你是饮食管理系统的语义意图解释器。只负责把用户原话转换为给定的 Structured Outputs Schema，不执行工具、不计算营养、不创建数据。

规则：
1. 先判断用户是在报告已经发生的事实，还是询问未来、可能、愿望或假设行为。
2. “包含食物和吃字”不等于记餐。只有已经发生、正在发生，或明确命令“记录、记一下、加入某餐”时，should_create_action 才能为 true。
3. question、planned、hypothetical、possible、desired、conditional 默认 should_create_action=false。
4. “可以吃吗、想吃、准备吃、打算吃、如果吃、再吃会不会超标”归为 consumption_advice，不是 meal_record。
5. food_nutrition 用于查询热量或营养；food_replacement 用于替换；memory_preference 用于明确口味偏好。
6. 同一句话有多个目标时返回主要意图，同时保留全部食物实体。
7. 食物实体只提取 raw_name、quantity、unit、explicit_weight_g。未知字段必须为 null，不得猜测。
8. 不生成 food_id，不生成营养值，不假设数据库存在某食物。
9. 疾病、诊断、治疗、用药、孕期和进食障碍相关内容标记为 safety。
10. 缺少会影响业务执行的关键信息时 requires_clarification=true，并给出简短 clarification_question。
11. 严格遵守 Schema，不输出额外文本或隐藏推理。
"""


class IntentProviderResult(BaseModel):
    interpretation: IntentInterpretation
    provider: str
    model: str | None
    latency_ms: int


class OpenAIAssistantProvider:
    provider_name = "openai"

    def __init__(self, settings: AgentSettings, client: Any | None = None) -> None:
        if not settings.api_key or not settings.model:
            raise ValueError("OpenAI provider requires OPENAI_API_KEY and OPENAI_MODEL")
        self.settings = settings
        self.model = settings.model
        if client is None:
            client = create_openai_client(settings)
        self.client = client

    def interpret(self, message: str) -> IntentProviderResult:
        started = time.perf_counter()
        try:
            response = self.client.responses.parse(
                model=self.model,
                instructions=INTENT_INSTRUCTIONS,
                input=[{"role": "user", "content": [{"type": "input_text", "text": message}]}],
                text_format=IntentInterpretation,
                reasoning={"effort": self.settings.reasoning_effort},
                max_output_tokens=1200,
                store=False,
                timeout=self.settings.timeout_seconds,
            )
            parsed = getattr(response, "output_parsed", None)
            if parsed is None:
                raise AgentProviderError("REFUSAL_OR_EMPTY", "模型未返回可用的结构化意图")
            interpretation = parsed if isinstance(parsed, IntentInterpretation) else IntentInterpretation.model_validate(parsed)
            return IntentProviderResult(
                interpretation=interpretation,
                provider=self.provider_name,
                model=self.model,
                latency_ms=round((time.perf_counter() - started) * 1000),
            )
        except AgentProviderError:
            raise
        except ValidationError as exc:
            raise AgentProviderError("FORMAT_ERROR", "模型意图输出不符合 Schema") from exc
        except Exception as exc:
            raise OpenAIAgentProvider._classify_error(exc) from exc


def build_openai_assistant_from_env(client: Any | None = None) -> OpenAIAssistantProvider | None:
    settings = AgentSettings.from_env()
    if not settings.openai_enabled:
        return None
    return OpenAIAssistantProvider(settings, client=client)
