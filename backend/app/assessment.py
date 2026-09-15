from __future__ import annotations

import json
from collections import Counter
from typing import Any, Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .deepseek_agent import DeepSeekSettings, create_deepseek_client
from .memory_index import PersonalMemoryIndex
from .models import AgentConfidence, DietAssessment, Meal, NutritionEstimateRange, Profile
from .openai_agent import AgentProviderError, AgentSettings, create_openai_client
from .reaction_insights import build_reaction_insights


DISCLAIMER = "本结果仅供健康参考，不构成医疗建议，身体不适请及时就医。"
T = TypeVar("T", bound=BaseModel)


class PydanticOutputParser(Generic[T]):
    """Strict JSON-to-Pydantic parser without adding the full LangChain runtime."""

    def __init__(self, model: type[T]) -> None:
        self.model = model

    def parse(self, value: str | dict[str, Any]) -> T:
        return self.model.model_validate(value if isinstance(value, dict) else json.loads(value))

    def schema(self) -> dict[str, Any]:
        return self.model.model_json_schema()


class DietAssessmentLLMOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    overall_score: int = Field(ge=0, le=100)
    general_analysis: list[str] = Field(max_length=6)
    personalized_analysis: list[str] = Field(max_length=6)
    optimization_suggestions: list[str] = Field(max_length=6)
    nutrition_estimates: list[NutritionEstimateRange] = Field(max_length=8)
    evidence: list[str] = Field(max_length=8)
    confidence: AgentConfidence


ASSESSMENT_SYSTEM_PROMPT = """你是个人日常饮食健康评估助手，不是医疗诊断系统。

你的核心任务分为两部分：
1. 通用膳食标准评估：依据提供的中国居民膳食指南片段，评价饮食均衡、营养短板和食材多样性。
2. 个人专属评估：仅依据提供的用户画像与召回的该用户历史饮食-体感记录，指出与该用户有关的耐受风险。不得把通用常识伪装成个人历史。

强制规则：
- 不默认减脂，不主动计算热量缺口；优先关注均衡、运动适配和肠胃耐受。
- 接受口语和模糊份量。营养只能输出整数区间，明确估算依据与置信度，不得制造精确小数。
- 不诊断疾病，不给治疗或用药方案。持续、严重或危险症状只建议及时就医。
- 只返回符合工具 JSON Schema 的对象，不输出 Markdown、前后缀或隐藏推理。
- general_analysis、personalized_analysis、optimization_suggestions 均使用简短完整中文句子。
"""


class ModelAssessmentProvider:
    def __init__(self, provider: str, model: str, client: Any, timeout_seconds: float, thinking: str = "disabled") -> None:
        self.provider = provider
        self.model = model
        self.client = client
        self.timeout_seconds = timeout_seconds
        self.thinking = thinking
        self.parser = PydanticOutputParser(DietAssessmentLLMOutput)

    def assess(self, prompt: str) -> DietAssessmentLLMOutput:
        try:
            if self.provider == "openai":
                response = self.client.responses.parse(
                    model=self.model,
                    instructions=ASSESSMENT_SYSTEM_PROMPT,
                    input=[{"role": "user", "content": [{"type": "input_text", "text": prompt}]}],
                    text_format=DietAssessmentLLMOutput,
                    store=False,
                    timeout=self.timeout_seconds,
                )
                return DietAssessmentLLMOutput.model_validate(response.output_parsed)

            schema_text = json.dumps(self.parser.schema(), ensure_ascii=False)
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": ASSESSMENT_SYSTEM_PROMPT},
                    {"role": "user", "content": f"{prompt}\n\n必须符合以下 JSON Schema：\n{schema_text}"},
                ],
                response_format={"type": "json_object"},
                max_tokens=1800,
                extra_body={"thinking": {"type": self.thinking}},
            )
            content = response.choices[0].message.content
            if not content:
                raise AgentProviderError("FORMAT_ERROR", "模型未返回结构化评估")
            return self.parser.parse(content)
        except (ValidationError, ValueError, TypeError, json.JSONDecodeError) as exc:
            raise AgentProviderError("FORMAT_ERROR", "模型评估输出不符合固定 JSON Schema") from exc


def build_assessment_provider_from_env() -> ModelAssessmentProvider | None:
    deepseek = DeepSeekSettings.from_env()
    if deepseek.enabled:
        return ModelAssessmentProvider(
            "deepseek",
            deepseek.model or "",
            create_deepseek_client(deepseek),
            deepseek.timeout_seconds,
            deepseek.thinking,
        )
    openai = AgentSettings.from_env()
    if openai.openai_enabled:
        return ModelAssessmentProvider("openai", openai.model or "", create_openai_client(openai), openai.timeout_seconds)
    return None


def nutrition_ranges(meals: list[Meal]) -> list[NutritionEstimateRange]:
    if not meals:
        return []
    totals = {
        "能量": (sum(meal.nutrition.energy_kcal for meal in meals), "kcal"),
        "蛋白质": (sum(meal.nutrition.protein_g for meal in meals), "g"),
        "碳水化合物": (sum(meal.nutrition.carbs_g for meal in meals), "g"),
        "脂肪": (sum(meal.nutrition.fat_g for meal in meals), "g"),
        "膳食纤维": (sum(meal.nutrition.fiber_g for meal in meals), "g"),
    }
    uncertainty = 0.1 if all(meal.confidence == "high" for meal in meals) else 0.2 if all(meal.confidence != "low" for meal in meals) else 0.3
    return [
        NutritionEstimateRange(
            nutrient=name,
            minimum=max(0, round(value * (1 - uncertainty))),
            maximum=max(0, round(value * (1 + uncertainty))),
            unit=unit,
            basis=f"根据已记录食物，以约±{round(uncertainty * 100)}%反映份量和食物差异",
        )
        for name, (value, unit) in totals.items()
    ]


def _rule_assessment(
    profile: Profile,
    meals: list[Meal],
    rag: dict[str, list[str]],
    reaction_insights: list,
) -> DietAssessmentLLMOutput:
    foods = [item.name for meal in meals for item in meal.items]
    feedbacks = [meal.body_feedback for meal in meals if meal.body_feedback]
    symptoms = [symptom for feedback in feedbacks for symptom in feedback.symptoms if symptom != "无不适"]
    score_values = [meal.score.get("score") for meal in meals if meal.score and isinstance(meal.score.get("score"), (int, float))]
    score = round(sum(score_values) / len(score_values)) if score_values else max(35, min(85, 45 + len(set(foods)) * 5))

    general = []
    general.append("食材种类偏少，均衡性和微量营养素覆盖仍可提升。" if len(set(foods)) < max(3, len(meals) * 2) else "已包含多种食材，食物多样性基础较好。")
    day_count = max(1, len({meal.eaten_at.date() for meal in meals}))
    if sum(meal.nutrition.vegetable_g for meal in meals) < 200 * day_count:
        general.append("蔬菜记录偏少，建议在后续正餐增加深色蔬菜。")
    if sum(meal.nutrition.protein_g for meal in meals) > 0:
        general.append("已有蛋白质来源，仍应与主食、蔬菜和水果合理搭配。")

    personalized = []
    if profile.food_intolerances:
        personalized.append(f"你的不耐受标签包括{'、'.join(profile.food_intolerances)}，选择奶制品或复合食品时应留意配料。")
    if profile.digestive_sensitivities:
        personalized.append(f"你的肠胃敏感记录包括{'、'.join(profile.digestive_sensitivities)}，本次建议优先参考餐后真实体感。")
    if reaction_insights:
        personalized.extend(insight.summary for insight in reaction_insights[:2])
    elif symptoms:
        common = Counter(symptoms).most_common(2)
        personalized.append(f"本次记录出现{'、'.join(name for name, _ in common)}，可继续观察是否与相同食物和份量重复相关。")
    if rag["reactions"]:
        personalized.append("检索到与你本次食物或体感相关的个人历史记录，已作为风险提示依据。")
    if not personalized:
        personalized.append("当前个人体感数据较少，暂不把通用食物风险判断成你的个人规律。")

    suggestions = ["下一餐优先补充当前缺少的食物类别，并继续记录餐后体感。"]
    if reaction_insights:
        suggestions.append(reaction_insights[0].next_action)
    elif symptoms:
        suggestions.append("相同食物下次先减少单次份量并记录体感；若不适持续、加重或反复出现，请及时就医。")
    elif profile.exercise_habits:
        suggestions.append(f"结合你的运动习惯（{'、'.join(profile.exercise_habits)}），运动前后注意主食、蛋白质与水分搭配。")

    return DietAssessmentLLMOutput(
        overall_score=score,
        general_analysis=general,
        personalized_analysis=personalized,
        optimization_suggestions=suggestions,
        nutrition_estimates=nutrition_ranges(meals),
        evidence=[*rag["guide"], *(insight.summary for insight in reaction_insights[:2]), *rag["reactions"][:1]],
        confidence="medium" if meals else "low",
    )


def assess_diet(
    profile: Profile,
    meals: list[Meal],
    message: str,
    memory_index: PersonalMemoryIndex,
    provider: ModelAssessmentProvider | None,
    *,
    history_meals: list[Meal] | None = None,
) -> DietAssessment:
    history = history_meals if history_meals is not None else meals
    rag = memory_index.retrieve(profile, history, message or "饮食均衡和餐后体感")
    reaction_insights = build_reaction_insights(history, limit=3)
    ranges = nutrition_ranges(meals)
    prompt = json.dumps({
        "用户本轮描述": message,
        "用户画像": {
            "身高_cm": profile.height_cm,
            "体重_kg": profile.current_weight_kg,
            "目标": profile.primary_goal,
            "不耐受": profile.food_intolerances,
            "肠胃敏感": profile.digestive_sensitivities,
            "运动习惯": profile.exercise_habits,
        },
        "已确认饮食摘要": [{
            "日期": meal.eaten_at.date().isoformat(),
            "食物": [item.name for item in meal.items],
            "体感": meal.body_feedback.model_dump(mode="json") if meal.body_feedback else None,
        } for meal in meals],
        "后端营养区间": [item.model_dump(mode="json") for item in ranges],
        "后端个人关联证据": [item.model_dump(mode="json") for item in reaction_insights],
        "RAG召回指南": rag["guide"],
        "RAG召回个人体感": rag["reactions"],
    }, ensure_ascii=False)

    fallback_reason = None
    output = None
    if provider is not None:
        try:
            output = provider.assess(prompt)
        except Exception as exc:
            fallback_reason = getattr(exc, "message", "模型评估暂时不可用")
    if output is None:
        output = _rule_assessment(profile, meals, rag, reaction_insights)
    if ranges:
        output.nutrition_estimates = ranges
    confidence = output.confidence if output.confidence in {"high", "medium", "low"} else "low"
    result = DietAssessment(
        **output.model_dump(exclude={"confidence"}),
        confidence=confidence,
        provider=provider.provider if provider and fallback_reason is None else "rule",
        model=provider.model if provider else None,
        fallback_used=fallback_reason is not None,
        fallback_reason=fallback_reason,
    )
    result.markdown = assessment_markdown(result)
    return result


def assessment_markdown(result: DietAssessment) -> str:
    return "\n".join([
        f"【综合饮食评分：{result.overall_score}/100】",
        "【通用膳食分析】",
        *result.general_analysis,
        "【你的个人专属提示】",
        *result.personalized_analysis,
        "【优化建议】",
        *result.optimization_suggestions,
        f"⚠️免责声明：{DISCLAIMER}",
    ])
