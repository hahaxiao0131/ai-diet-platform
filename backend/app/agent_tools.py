from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from .agent import build_agent_context
from .food_search import normalize_search_text, search_catalog
from .models import (
    AgentMemory,
    AIAction,
    Food,
    GoalProposal,
    Meal,
    MealItem,
    MealType,
    Nutrition,
    Profile,
    Scenario,
)
from .nutrition import build_today, calculate_meal, scale_nutrition, sum_nutrition
from .providers import AdviceProvider, RuleBasedAssistantProvider
from .risk import detect_risks


ToolEffect = Literal["read_only", "proposal", "confirmed_write"]


class ToolInput(BaseModel):
    model_config = ConfigDict(extra="forbid")


class GetTodayContextInput(ToolInput):
    pass


class MealContextItem(BaseModel):
    meal_id: UUID
    meal_type: MealType
    eaten_at: datetime
    foods: list[str]
    nutrition: Nutrition


class GetTodayContextOutput(BaseModel):
    meals: list[MealContextItem]
    consumed: Nutrition
    target: Nutrition
    remaining: Nutrition
    gaps: list[str]
    near_limits: list[str]
    completeness: dict[str, int]
    confidence: Literal["high", "medium", "low"]
    missing_data: list[str]
    active_memories: list[str]


class SearchFoodInput(ToolInput):
    query: str | None = Field(description="食物名称或别名；按条码查找时传 null")
    barcode: str | None = Field(description="8 到 14 位商品条码；按名称查找时传 null")
    limit: int = Field(ge=1, le=10)

    @model_validator(mode="after")
    def require_query_or_barcode(self):
        if not (self.query and self.query.strip()) and not (self.barcode and self.barcode.strip()):
            raise ValueError("query 或 barcode 至少提供一个")
        return self


class FoodToolRecord(BaseModel):
    food_id: UUID
    standard_name: str
    aliases: list[str]
    barcode: str | None
    data_source: str
    nutrition_per_100g: Nutrition
    default_weight_g: float | None
    confidence: Literal["high", "medium", "low"]


class SearchFoodOutput(BaseModel):
    matches: list[FoodToolRecord]
    clarification_required: bool
    message: str


class ConvertFoodPortionInput(ToolInput):
    food_id: UUID
    amount: float = Field(gt=0, le=10000)
    unit: Literal["g", "克", "ml", "毫升", "个", "只", "枚", "颗", "片", "根", "勺", "块", "碗", "杯", "盒", "份"]


class ConvertFoodPortionOutput(BaseModel):
    food_id: UUID
    standard_name: str
    weight_g: float | None
    assumption: str | None
    confidence: Literal["high", "medium", "low"]
    clarification_required: bool
    clarification_message: str | None


class CalculateNutritionInput(ToolInput):
    food_id: UUID
    weight_g: float = Field(gt=0, le=10000)


class CalculateNutritionOutput(BaseModel):
    food_id: UUID
    standard_name: str
    weight_g: float
    nutrition: Nutrition
    data_source: str
    confidence: Literal["high", "medium", "low"]


class MealRecordToolItem(ToolInput):
    food_id: UUID
    weight_g: float = Field(gt=0, le=10000)


class ProposeMealRecordInput(ToolInput):
    meal_type: MealType
    items: list[MealRecordToolItem] = Field(min_length=1, max_length=20)
    assumptions: list[str] = Field(max_length=20)


class ProposalOutput(BaseModel):
    action: AIAction
    requires_confirmation: Literal[True] = True


class PreviewMealPlansInput(ToolInput):
    meal_type: MealType
    scenario: Scenario | None = Field(description="指定场景，或传 null 返回多个场景")


class MealPlanCandidate(BaseModel):
    title: str
    scenario: Scenario
    items: list[MealItem]
    nutrition: Nutrition
    reason: str
    risks: list[dict[str, Any]]


class PreviewMealPlansOutput(BaseModel):
    plans: list[MealPlanCandidate]
    remaining_before: Nutrition
    confidence: Literal["high", "medium", "low"]


class ProposeMemoryInput(ToolInput):
    category: Literal["preference", "avoidance", "habit"]
    value: str = Field(min_length=1, max_length=80)
    source_message: str = Field(min_length=1, max_length=500)


class GetRecentMealsInput(ToolInput):
    limit: int = Field(ge=1, le=20)
    meal_type: MealType | None = Field(description="筛选餐次，或传 null 返回全部")


class RecentMealItem(BaseModel):
    meal_id: UUID
    meal_type: MealType
    eaten_at: datetime
    items: list[MealItem]
    nutrition: Nutrition
    confidence: Literal["high", "medium", "low"]


class GetRecentMealsOutput(BaseModel):
    meals: list[RecentMealItem]
    count: int


class AgentToolError(BaseModel):
    code: str
    message: str
    clarification_required: bool = False


class AgentToolResult(BaseModel):
    tool: str
    effect: ToolEffect
    success: bool
    data: dict[str, Any] | None = None
    error: AgentToolError | None = None


class AgentToolFailure(Exception):
    def __init__(self, code: str, message: str, *, clarification_required: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.clarification_required = clarification_required


@dataclass(frozen=True)
class ToolDefinition:
    description: str
    effect: ToolEffect
    input_model: type[ToolInput]
    output_model: type[BaseModel]
    handler_name: str


TOOL_DEFINITIONS: dict[str, ToolDefinition] = {
    "get_today_context": ToolDefinition(
        "读取当前用户今天已确认的餐次、摄入、目标、营养缺口、接近上限指标和数据可信度。涉及个性化判断时先调用。",
        "read_only", GetTodayContextInput, GetTodayContextOutput, "_get_today_context",
    ),
    "search_food": ToolDefinition(
        "按名称、别名或条码搜索当前用户可见的食物。后续工具只能使用这里返回的 food_id。",
        "read_only", SearchFoodInput, SearchFoodOutput, "_search_food",
    ),
    "convert_food_portion": ToolDefinition(
        "用确定性规则把克、个、碗、杯、片、颗等份量换算为克，并返回假设和置信度。",
        "read_only", ConvertFoodPortionInput, ConvertFoodPortionOutput, "_convert_food_portion",
    ),
    "calculate_nutrition": ToolDefinition(
        "根据已存在的 food_id 和克数，由后端食物库确定性计算营养，不允许模型自行计算。",
        "read_only", CalculateNutritionInput, CalculateNutritionOutput, "_calculate_nutrition",
    ),
    "propose_meal_record": ToolDefinition(
        "生成待用户确认的饮食记录动作；不会直接写入正式餐食。只有现有 confirm 接口可以完成记录。",
        "proposal", ProposeMealRecordInput, ProposalOutput, "_propose_meal_record",
    ),
    "preview_meal_plans": ToolDefinition(
        "依据今日缺口、目标、偏好和禁忌生成候选餐食，所有营养值由后端重新计算并校验。",
        "proposal", PreviewMealPlansInput, PreviewMealPlansOutput, "_preview_meal_plans",
    ),
    "propose_memory": ToolDefinition(
        "生成保存饮食偏好或习惯的待确认动作；不会直接写入长期记忆。",
        "proposal", ProposeMemoryInput, ProposalOutput, "_propose_memory",
    ),
    "get_recent_meals": ToolDefinition(
        "读取当前用户最近的有效饮食记录，用于复用上一餐或分析近期饮食。",
        "read_only", GetRecentMealsInput, GetRecentMealsOutput, "_get_recent_meals",
    ),
}


class AgentToolRegistry:
    """Identity-bound deterministic tool gateway exposed to the model."""

    def __init__(
        self,
        *,
        profile: Profile,
        goal: GoalProposal,
        foods: list[Food],
        meals: list[Meal],
        memories: list[AgentMemory],
        advice_provider: AdviceProvider,
    ) -> None:
        self.profile = profile
        self.goal = goal
        self.foods = list(foods)
        self.meals = [meal for meal in meals if meal.profile_id == profile.id and meal.status == "active"]
        self.memories = [memory for memory in memories if memory.profile_id == profile.id and memory.status == "active"]
        self.advice_provider = advice_provider
        self._foods_by_id = {food.id: food for food in self.foods}
        self._proposals: dict[UUID, AIAction] = {}
        self._pending_meal_nutrition: list[Nutrition] = []

    def openai_tools(self) -> list[dict[str, Any]]:
        return [
            {
                "type": "function",
                "name": name,
                "description": definition.description,
                "parameters": definition.input_model.model_json_schema(),
                "strict": True,
            }
            for name, definition in TOOL_DEFINITIONS.items()
        ]

    def effect_for(self, name: str) -> ToolEffect:
        definition = TOOL_DEFINITIONS.get(name)
        return definition.effect if definition else "read_only"

    def proposal(self, action_id: UUID | str | None) -> AIAction | None:
        if action_id is None:
            return next(reversed(self._proposals.values()), None) if self._proposals else None
        try:
            return self._proposals.get(UUID(str(action_id)))
        except ValueError:
            return None

    def execute(self, name: str, arguments: dict[str, Any]) -> AgentToolResult:
        definition = TOOL_DEFINITIONS.get(name)
        if definition is None:
            return AgentToolResult(
                tool=name,
                effect="read_only",
                success=False,
                error=AgentToolError(code="TOOL_NOT_FOUND", message="工具不存在"),
            )
        try:
            payload = definition.input_model.model_validate(arguments)
            handler: Callable[[ToolInput], BaseModel] = getattr(self, definition.handler_name)
            output = definition.output_model.model_validate(handler(payload))
            return AgentToolResult(
                tool=name,
                effect=definition.effect,
                success=True,
                data=output.model_dump(mode="json"),
            )
        except ValidationError as exc:
            return AgentToolResult(
                tool=name,
                effect=definition.effect,
                success=False,
                error=AgentToolError(
                    code="INVALID_ARGUMENTS",
                    message="工具参数不完整或格式错误",
                    clarification_required=True,
                ),
                data={"validation_errors": exc.errors(include_url=False)},
            )
        except AgentToolFailure as exc:
            return AgentToolResult(
                tool=name,
                effect=definition.effect,
                success=False,
                error=AgentToolError(
                    code=exc.code,
                    message=exc.message,
                    clarification_required=exc.clarification_required,
                ),
            )
        except Exception:
            return AgentToolResult(
                tool=name,
                effect=definition.effect,
                success=False,
                error=AgentToolError(code="TOOL_INTERNAL_ERROR", message="工具暂时无法完成，请稍后重试"),
            )

    def _food_or_fail(self, food_id: UUID) -> Food:
        food = self._foods_by_id.get(food_id)
        if food is None:
            raise AgentToolFailure("FOOD_NOT_FOUND", "食物不存在或不属于当前用户可见范围")
        return food

    def _get_today_context(self, _: GetTodayContextInput) -> GetTodayContextOutput:
        today = build_today(self.profile, self.meals, self.goal.target)
        context = build_agent_context(today, self.memories)
        return GetTodayContextOutput(
            meals=[
                MealContextItem(
                    meal_id=meal.id,
                    meal_type=meal.meal_type,
                    eaten_at=meal.eaten_at,
                    foods=[item.name for item in meal.items],
                    nutrition=meal.nutrition,
                )
                for meal in today.meals
            ],
            consumed=today.consumed,
            target=today.target,
            remaining=today.remaining,
            gaps=today.gaps,
            near_limits=today.near_limits,
            completeness=today.completeness,
            confidence=today.confidence,
            missing_data=context.missing_data,
            active_memories=context.active_memories,
        )

    def _search_food(self, payload: SearchFoodInput) -> SearchFoodOutput:
        if payload.barcode:
            matches = [food for food in self.foods if food.barcode == payload.barcode.strip()][:payload.limit]
        else:
            matches = search_catalog(self.foods, payload.query or "", payload.limit)
        records = [self._food_record(food) for food in matches]
        if not records:
            return SearchFoodOutput(matches=[], clarification_required=True, message="没有找到匹配食物，请补充名称、品牌或营养标签")
        query = normalize_search_text(payload.query or "")
        exact = bool(payload.barcode) or any(
            query in {normalize_search_text(food.name), *(normalize_search_text(alias) for alias in food.aliases)}
            for food in matches
        )
        return SearchFoodOutput(
            matches=records,
            clarification_required=len(records) > 1 and not exact,
            message="已找到可用食物" if exact else "找到多个可能结果，请按 standard_name 确认",
        )

    @staticmethod
    def _food_record(food: Food) -> FoodToolRecord:
        return FoodToolRecord(
            food_id=food.id,
            standard_name=food.name,
            aliases=food.aliases,
            barcode=food.barcode,
            data_source=food.source,
            nutrition_per_100g=food.nutrition_per_100g,
            default_weight_g=food.default_weight_g,
            confidence=food.confidence,
        )

    def _convert_food_portion(self, payload: ConvertFoodPortionInput) -> ConvertFoodPortionOutput:
        food = self._food_or_fail(payload.food_id)
        if payload.unit in {"g", "克"}:
            return ConvertFoodPortionOutput(
                food_id=food.id,
                standard_name=food.name,
                weight_g=round(payload.amount, 2),
                assumption=None,
                confidence="high",
                clarification_required=False,
                clarification_message=None,
            )
        if payload.unit in {"ml", "毫升"}:
            return ConvertFoodPortionOutput(
                food_id=food.id,
                standard_name=food.name,
                weight_g=round(payload.amount, 2),
                assumption=f"{food.name}暂按 1ml 约等于 1g 换算",
                confidence="medium",
                clarification_required=False,
                clarification_message=None,
            )
        per_unit = RuleBasedAssistantProvider.unit_weights.get(payload.unit, {}).get(food.name)
        if per_unit:
            crab_estimate = food.name == "螃蟹" and payload.unit == "只"
            return ConvertFoodPortionOutput(
                food_id=food.id,
                standard_name=food.name,
                weight_g=round(payload.amount * per_unit, 2),
                assumption=(
                    "螃蟹按每只可食部分约 50g 估算，个体大小和可食率差异较大"
                    if crab_estimate
                    else f"{food.name}按每{payload.unit}约 {per_unit:g}g 换算"
                ),
                confidence="low" if crab_estimate or food.confidence == "low" else "medium",
                clarification_required=False,
                clarification_message=None,
            )
        estimate = food.default_weight_g or RuleBasedAssistantProvider.default_weights.get(food.name)
        return ConvertFoodPortionOutput(
            food_id=food.id,
            standard_name=food.name,
            weight_g=round(payload.amount * estimate, 2) if estimate else None,
            assumption=f"{food.name}每份暂按常用份量 {estimate:g}g 估算" if estimate else None,
            confidence="low",
            clarification_required=True,
            clarification_message=f"{food.name}的“{payload.unit}”缺少可靠换算，请补充克数或确认常用份量",
        )

    def _calculate_nutrition(self, payload: CalculateNutritionInput) -> CalculateNutritionOutput:
        food = self._food_or_fail(payload.food_id)
        return CalculateNutritionOutput(
            food_id=food.id,
            standard_name=food.name,
            weight_g=payload.weight_g,
            nutrition=scale_nutrition(food.nutrition_per_100g, payload.weight_g),
            data_source=food.source,
            confidence=food.confidence,
        )

    def _propose_meal_record(self, payload: ProposeMealRecordInput) -> ProposalOutput:
        meal_items: list[MealItem] = []
        action_items: list[dict[str, Any]] = []
        confidence: Literal["high", "medium", "low"] = "high"
        for selected in payload.items:
            food = self._food_or_fail(selected.food_id)
            nutrition = scale_nutrition(food.nutrition_per_100g, selected.weight_g)
            meal_items.append(MealItem(food_id=food.id, name=food.name, weight_g=selected.weight_g, nutrition=nutrition))
            action_items.append({
                "food_id": str(food.id),
                "name": food.name,
                "weight_g": selected.weight_g,
                "consumed_ratio": 1,
            })
            if food.confidence == "low":
                confidence = "low"
            elif food.confidence == "medium" and confidence == "high":
                confidence = "medium"
        if payload.assumptions and confidence == "high":
            confidence = "medium"
        preview = calculate_meal(meal_items)
        action = AIAction(
            profile_id=self.profile.id,
            action_type="create_meal",
            title="确认这次饮食记录",
            summary="、".join(f"{item.name} {item.weight_g:g}g" for item in meal_items),
            payload={"meal_type": payload.meal_type, "items": action_items},
            preview_nutrition=preview,
            confidence=confidence,
            assumptions=payload.assumptions,
        )
        self._proposals[action.id] = action
        self._pending_meal_nutrition.append(preview)
        return ProposalOutput(action=action)

    def _preview_meal_plans(self, payload: PreviewMealPlansInput) -> PreviewMealPlansOutput:
        today = build_today(self.profile, self.meals, self.goal.target)
        pending = sum_nutrition(self._pending_meal_nutrition)
        effective_remaining = Nutrition(**{
            key: max(0, (getattr(today.remaining, key) or 0) - (getattr(pending, key) or 0))
            if value is not None else None
            for key, value in today.remaining.model_dump().items()
        })
        avoidances = [memory.value for memory in self.memories if memory.category == "avoidance"]
        options = self.advice_provider.propose(
            {"remaining": effective_remaining.model_dump(), "avoidances": [*self.profile.hard_exclusions, *avoidances]},
            self.foods,
        )
        if payload.scenario:
            options = [option for option in options if option["scenario"] == payload.scenario]
        plans: list[MealPlanCandidate] = []
        for option in options:
            items = [
                MealItem(
                    food_id=entry["food"].id,
                    name=entry["food"].name,
                    weight_g=entry["weight_g"],
                    nutrition=scale_nutrition(entry["food"].nutrition_per_100g, entry["weight_g"]),
                )
                for entry in option["items"]
            ]
            nutrition = calculate_meal(items)
            plans.append(MealPlanCandidate(
                title=option["title"],
                scenario=option["scenario"],
                items=items,
                nutrition=nutrition,
                reason=option["reason"],
                risks=detect_risks(self.profile, [item.name for item in items], nutrition),
            ))
        return PreviewMealPlansOutput(plans=plans, remaining_before=effective_remaining, confidence=today.confidence)

    def _propose_memory(self, payload: ProposeMemoryInput) -> ProposalOutput:
        verb = "避免" if payload.category == "avoidance" else "优先考虑" if payload.category == "preference" else "记住"
        action = AIAction(
            profile_id=self.profile.id,
            action_type="remember_preference",
            title="确认保存饮食偏好",
            summary=f"后续建议中{verb}{payload.value}",
            payload={
                "category": payload.category,
                "value": payload.value.strip(),
                "source_message": payload.source_message,
            },
            confidence="high",
        )
        self._proposals[action.id] = action
        return ProposalOutput(action=action)

    def _get_recent_meals(self, payload: GetRecentMealsInput) -> GetRecentMealsOutput:
        meals = self.meals
        if payload.meal_type:
            meals = [meal for meal in meals if meal.meal_type == payload.meal_type]
        selected = sorted(meals, key=lambda meal: meal.eaten_at, reverse=True)[:payload.limit]
        output = [
            RecentMealItem(
                meal_id=meal.id,
                meal_type=meal.meal_type,
                eaten_at=meal.eaten_at,
                items=meal.items,
                nutrition=meal.nutrition,
                confidence=meal.confidence,
            )
            for meal in selected
        ]
        return GetRecentMealsOutput(meals=output, count=len(output))
