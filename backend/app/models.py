from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


GoalType = Literal["fat_loss", "muscle_gain", "maintain", "structure"]
GoalPace = Literal["steady", "standard", "aggressive"]
MealType = Literal["breakfast", "lunch", "dinner", "snack"]
Scenario = Literal["home", "quick", "convenience"]
AIResponseKind = Literal["explanation", "meal_record_proposal", "plan_recommendation", "food_replacement", "clarification"]


class PhoneCodeRequest(BaseModel):
    phone: str = Field(pattern=r"^1[3-9]\d{9}$")


class PhoneLoginRequest(PhoneCodeRequest):
    code: str = Field(pattern=r"^\d{6}$")


class WechatLoginRequest(BaseModel):
    code: str = Field(min_length=3, max_length=256)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class Nutrition(BaseModel):
    energy_kcal: float = 0
    protein_g: float = 0
    fat_g: float = 0
    carbs_g: float = 0
    fiber_g: float = 0
    sodium_mg: float = 0
    added_sugar_g: float | None = None
    vegetable_g: float = 0
    fruit_g: float = 0


class Food(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    name: str
    aliases: list[str] = Field(default_factory=list)
    food_type: Literal["ingredient", "standard_dish", "packaged", "custom"] = "ingredient"
    default_unit: str = "100g"
    default_weight_g: float | None = None
    source: str = "local_seed"
    source_version: str = "seed-v1"
    nutrition_per_100g: Nutrition = Field(default_factory=Nutrition)
    allergens: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    confidence: Literal["high", "medium", "low"] = "high"


class CustomFoodCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    basis_weight_g: float = Field(default=100, gt=0, le=5000)
    nutrition: Nutrition
    default_weight_g: float | None = Field(default=None, gt=0, le=5000)


class Profile(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    user_id: UUID
    sex: Literal["male", "female", "unknown"] | None = None
    age: int | None = None
    height_cm: float | None = None
    current_weight_kg: float | None = None
    activity_level: Literal["sedentary", "light", "moderate", "high"] | None = None
    primary_goal: GoalType | None = None
    target_weight_kg: float | None = None
    goal_pace: GoalPace | None = None
    allergies: list[str] = Field(default_factory=list)
    hard_exclusions: list[str] = Field(default_factory=list)
    preferences: list[str] = Field(default_factory=list)
    onboarding_completed: bool = False


class ProfileUpdate(BaseModel):
    sex: Literal["male", "female", "unknown"] | None = None
    age: int | None = Field(default=None, ge=18, le=64)
    height_cm: float | None = Field(default=None, gt=0, le=250)
    current_weight_kg: float | None = Field(default=None, gt=0, le=400)
    activity_level: Literal["sedentary", "light", "moderate", "high"] | None = None
    primary_goal: GoalType | None = None
    target_weight_kg: float | None = Field(default=None, gt=0, le=400)
    goal_pace: GoalPace | None = None
    allergies: list[str] | None = None
    hard_exclusions: list[str] | None = None
    preferences: list[str] | None = None


class GoalProposal(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    profile_id: UUID
    old_goal: dict[str, Any] | None = None
    target: Nutrition
    pace: GoalPace
    goal_type: GoalType
    reasons: list[str]
    rule_version: str = "nutrition-v1"
    status: Literal["proposed", "active", "superseded"] = "proposed"
    created_at: datetime = Field(default_factory=utc_now)


class MealDraftItem(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    food_id: UUID | None = None
    name: str
    estimated_weight_g: float = Field(gt=0)
    household_unit: str = "1份"
    consumed_ratio: float = Field(default=1, ge=0, le=1)
    is_compound: bool = False
    user_confirmed: bool = False
    user_modified: bool = False
    source: str = "mock_vision"
    confidence: Literal["high", "medium", "low"] = "medium"
    nutrition: Nutrition = Field(default_factory=Nutrition)


class MealDraft(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    profile_id: UUID
    meal_type: MealType
    eaten_at: datetime = Field(default_factory=utc_now)
    draft_status: Literal["processing", "ready", "confirmed", "cancelled"] = "ready"
    assets: list[dict[str, str]] = Field(default_factory=list)
    items: list[MealDraftItem] = Field(default_factory=list)
    confidence: Literal["high", "medium", "low"] = "medium"
    provider: str = "mock"
    confirmed_meal_id: UUID | None = None


class MealItem(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    food_id: UUID | None = None
    name: str
    weight_g: float
    consumed_ratio: float = 1
    nutrition: Nutrition = Field(default_factory=Nutrition)
    user_modified: bool = False


class Meal(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    profile_id: UUID
    meal_type: MealType
    eaten_at: datetime
    record_source: str
    items: list[MealItem]
    nutrition: Nutrition
    score: dict[str, Any] | None = None
    risks: list[dict[str, Any]] = Field(default_factory=list)
    confidence: Literal["high", "medium", "low"] = "medium"
    status: Literal["active", "deleted"] = "active"


class MealDraftConfirmItem(BaseModel):
    item_id: UUID
    food_id: UUID | None = None
    name: str | None = None
    weight_g: float = Field(gt=0)
    consumed_ratio: float = Field(default=1, ge=0, le=1)


class MealDraftConfirmRequest(BaseModel):
    items: list[MealDraftConfirmItem] = Field(min_length=1)


class ManualMealItemRequest(BaseModel):
    food_id: UUID
    weight_g: float = Field(gt=0)
    consumed_ratio: float = Field(default=1, ge=0, le=1)


class ManualMealCreateRequest(BaseModel):
    meal_type: MealType = "lunch"
    eaten_at: datetime = Field(default_factory=utc_now)
    items: list[ManualMealItemRequest] = Field(min_length=1)


class AIChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=500)


class AIAction(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    profile_id: UUID
    action_type: Literal["create_meal"] = "create_meal"
    title: str
    summary: str
    payload: dict[str, Any]
    preview_nutrition: Nutrition = Field(default_factory=Nutrition)
    status: Literal["proposed", "confirmed", "cancelled"] = "proposed"
    created_at: datetime = Field(default_factory=utc_now)


class AIChatResponse(BaseModel):
    kind: AIResponseKind
    message: str
    basis: list[str] = Field(default_factory=list)
    suggestions: list[str] = Field(default_factory=list)
    action: AIAction | None = None
    cta: Literal["preview_plans"] | None = None


class MealPlan(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    profile_id: UUID
    meal_type: MealType
    scenario: Scenario
    title: str
    items: list[MealItem]
    nutrition: Nutrition
    reason: str
    risks: list[dict[str, Any]] = Field(default_factory=list)
    status: Literal["draft", "saved", "converted", "cancelled"] = "draft"


class MealPlanPreviewRequest(BaseModel):
    meal_type: MealType = "dinner"
    scenario: Scenario = "quick"
    available_time_min: int = Field(default=20, ge=1, le=240)
    budget_level: Literal["low", "normal", "high"] = "normal"
    available_foods: list[str] = Field(default_factory=list)


class WeightRecordCreate(BaseModel):
    weight_kg: float = Field(gt=0, le=400)
    measured_at: datetime = Field(default_factory=utc_now)


class TodaySummary(BaseModel):
    date: date
    status: Literal["empty", "partial", "complete"]
    score: float | None
    score_status: Literal["insufficient_data", "partial", "final"]
    confidence: Literal["high", "medium", "low"]
    completeness: dict[str, int]
    consumed: Nutrition
    target: Nutrition
    remaining: Nutrition
    gaps: list[str]
    near_limits: list[str]
    meals: list[Meal]
