from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field


GoalType = Literal["fat_loss", "muscle_gain", "maintain", "structure"]
GoalPace = Literal["steady", "standard", "aggressive"]
MealType = Literal["breakfast", "lunch", "dinner", "snack"]
Scenario = Literal["home", "quick", "convenience"]
AIResponseKind = Literal[
    "explanation",
    "meal_record_proposal",
    "consumption_advice",
    "plan_recommendation",
    "food_replacement",
    "food_nutrition",
    "dietary_knowledge",
    "memory_proposal",
    "safety_profile_proposal",
    "clarification",
    "safety",
    "health_assessment",
    "exercise_record_proposal",
]
AgentConfidence = Literal["high", "medium", "low"]
AgentDecisionStage = Literal["inform", "clarify", "propose", "safety"]
IntentName = Literal[
    "meal_record",
    "consumption_advice",
    "food_nutrition",
    "plan_recommendation",
    "food_replacement",
    "memory_preference",
    "protein_explanation",
    "dietary_knowledge",
    "safety",
    "clarification",
    "exercise_record",
]
SpeechAct = Literal["statement", "question", "command", "hypothetical"]
TemporalStatus = Literal["completed", "current", "planned", "hypothetical", "unknown"]
IntentModality = Literal["actual", "possible", "desired", "conditional", "unknown"]


class PhoneCodeRequest(BaseModel):
    phone: str = Field(pattern=r"^1[3-9]\d{9}$")


class PhoneLoginRequest(PhoneCodeRequest):
    code: str = Field(pattern=r"^\d{6}$")


class WechatLoginRequest(BaseModel):
    code: str = Field(min_length=3, max_length=256)


class AccountDeleteRequest(BaseModel):
    confirmation: str = Field(min_length=1, max_length=20)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class Nutrition(BaseModel):
    energy_kcal: float = 0
    protein_g: float = 0
    fat_g: float = 0
    carbs_g: float = 0
    fiber_g: float = 0
    sodium_mg: float = 0
    sugars_g: float | None = None
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
    source_url: str | None = None
    source_observed_at: datetime | None = None
    barcode: str | None = None
    brand: str | None = None
    verified_by_user: bool = False
    nutrition_per_100g: Nutrition = Field(default_factory=Nutrition)
    allergens: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    confidence: Literal["high", "medium", "low"] = "high"


class CustomFoodCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    basis_weight_g: float = Field(default=100, gt=0, le=5000)
    nutrition: Nutrition
    default_weight_g: float | None = Field(default=None, gt=0, le=5000)
    allergens: list[str] = Field(default_factory=list, max_length=20)


class PackagedFoodLabelRequest(CustomFoodCreateRequest):
    barcode: str | None = Field(default=None, pattern=r"^\d{8,14}$")
    brand: str | None = Field(default=None, max_length=80)


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
    food_intolerances: list[str] = Field(default_factory=list)
    digestive_sensitivities: list[str] = Field(default_factory=list)
    exercise_habits: list[str] = Field(default_factory=list)
    safety_profile_reviewed: bool = False
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
    food_intolerances: list[str] | None = None
    digestive_sensitivities: list[str] | None = None
    exercise_habits: list[str] | None = None
    safety_profile_reviewed: bool | None = None


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
    source: str = "example_template"
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
    provider: str = "example_template"
    confirmed_meal_id: UUID | None = None


class ExampleMealDraftRequest(BaseModel):
    meal_type: MealType = "lunch"


class MealItem(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    food_id: UUID | None = None
    name: str
    weight_g: float
    consumed_ratio: float = 1
    nutrition: Nutrition = Field(default_factory=Nutrition)
    user_modified: bool = False
    estimated_weight_range_g: list[float] | None = None


class BodyFeedback(BaseModel):
    symptoms: list[str] = Field(default_factory=list, max_length=12)
    severity: Literal["none", "mild", "moderate", "severe"] = "none"
    occurred_after_minutes: int | None = Field(default=None, ge=0, le=10080)
    notes: str | None = Field(default=None, max_length=300)
    recorded_at: datetime = Field(default_factory=utc_now)


class BodyFeedbackRequest(BaseModel):
    symptoms: list[str] = Field(default_factory=list, max_length=12)
    severity: Literal["none", "mild", "moderate", "severe"] = "none"
    occurred_after_minutes: int | None = Field(default=None, ge=0, le=10080)
    notes: str | None = Field(default=None, max_length=300)


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
    body_feedback: BodyFeedback | None = None
    status: Literal["active", "deleted"] = "active"


ExerciseIntensity = Literal["low", "moderate", "high"]


class ExerciseRecord(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    profile_id: UUID
    exercise_type: str = Field(min_length=1, max_length=80)
    started_at: datetime = Field(default_factory=utc_now)
    duration_min: int = Field(gt=0, le=1440)
    intensity: ExerciseIntensity = "moderate"
    calories_burned: float | None = Field(default=None, ge=0, le=10000)
    calories_range_kcal: list[float] | None = Field(default=None, min_length=2, max_length=2)
    calories_source: Literal["user_estimated", "system_estimated"] = "system_estimated"
    source: Literal["manual", "ai"] = "manual"
    notes: str | None = Field(default=None, max_length=300)
    status: Literal["active", "deleted"] = "active"
    created_at: datetime = Field(default_factory=utc_now)


class ExerciseCreateRequest(BaseModel):
    exercise_type: str = Field(min_length=1, max_length=80)
    started_at: datetime = Field(default_factory=utc_now)
    duration_min: int = Field(gt=0, le=1440)
    intensity: ExerciseIntensity = "moderate"
    calories_burned: float | None = Field(default=None, ge=0, le=10000)
    notes: str | None = Field(default=None, max_length=300)


class ExerciseUpdateRequest(BaseModel):
    exercise_type: str | None = Field(default=None, min_length=1, max_length=80)
    started_at: datetime | None = None
    duration_min: int | None = Field(default=None, gt=0, le=1440)
    intensity: ExerciseIntensity | None = None
    calories_burned: float | None = Field(default=None, ge=0, le=10000)
    notes: str | None = Field(default=None, max_length=300)


class MealDraftConfirmItem(BaseModel):
    item_id: UUID
    food_id: UUID | None = None
    name: str | None = None
    weight_g: float = Field(gt=0)
    consumed_ratio: float = Field(default=1, ge=0, le=1)


class MealDraftConfirmRequest(BaseModel):
    items: list[MealDraftConfirmItem] = Field(min_length=1)
    body_feedback: BodyFeedbackRequest | None = None


class ManualMealItemRequest(BaseModel):
    food_id: UUID
    weight_g: float = Field(gt=0)
    consumed_ratio: float = Field(default=1, ge=0, le=1)


class ManualMealCreateRequest(BaseModel):
    meal_type: MealType = "lunch"
    eaten_at: datetime = Field(default_factory=utc_now)
    items: list[ManualMealItemRequest] = Field(min_length=1)
    body_feedback: BodyFeedbackRequest | None = None


class DietAssessmentRequest(BaseModel):
    meal_id: UUID | None = None
    days: int = Field(default=1, ge=1, le=7)
    message: str | None = Field(default=None, max_length=1000)


class NutritionEstimateRange(BaseModel):
    nutrient: str
    minimum: int = Field(ge=0)
    maximum: int = Field(ge=0)
    unit: str
    basis: str


class DietAssessment(BaseModel):
    overall_score: int = Field(ge=0, le=100)
    general_analysis: list[str] = Field(default_factory=list, max_length=6)
    personalized_analysis: list[str] = Field(default_factory=list, max_length=6)
    optimization_suggestions: list[str] = Field(default_factory=list, max_length=6)
    nutrition_estimates: list[NutritionEstimateRange] = Field(default_factory=list, max_length=8)
    evidence: list[str] = Field(default_factory=list, max_length=8)
    confidence: AgentConfidence = "medium"
    disclaimer: str = "本结果仅供健康参考，不构成医疗建议，身体不适请及时就医。"
    markdown: str = ""
    provider: str = "rule"
    model: str | None = None
    fallback_used: bool = False
    fallback_reason: str | None = None


class TrendDay(BaseModel):
    date: date
    meal_count: int
    score: int | None
    food_variety: int
    body_feedback_count: int
    discomfort_count: int
    symptoms: list[str] = Field(default_factory=list)


class ReactionInsight(BaseModel):
    food_name: str
    symptom: str
    occurrences: int = Field(ge=1)
    evidence_level: Literal["observation", "suspected", "repeated"]
    confidence: AgentConfidence
    first_seen: date
    last_seen: date
    summary: str
    next_action: str


class SevenDayTrend(BaseModel):
    start_date: date
    end_date: date
    days: list[TrendDay]
    average_score: int | None
    total_meals: int
    food_variety: int
    feedback_completion_rate: int
    common_symptoms: list[str] = Field(default_factory=list)
    reaction_insights: list[ReactionInsight] = Field(default_factory=list)
    feedback_due_count: int = 0
    focus_action: str | None = None


class AIChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=500)
    reply_to_trace_id: UUID | None = None


class IntentFoodEntity(BaseModel):
    model_config = ConfigDict(extra="forbid")

    raw_name: str
    quantity: float | None
    unit: str | None
    explicit_weight_g: float | None


class IntentInterpretation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    intent: IntentName
    speech_act: SpeechAct
    temporal_status: TemporalStatus
    modality: IntentModality
    foods: list[IntentFoodEntity]
    meal_type: MealType | None
    should_create_action: bool
    requires_clarification: bool
    clarification_question: str | None
    confidence: AgentConfidence


class AgentContext(BaseModel):
    recorded_meals: int
    expected_meals: int
    remaining_energy_kcal: float
    remaining_protein_g: float
    remaining_fiber_g: float
    gaps: list[str] = Field(default_factory=list)
    near_limits: list[str] = Field(default_factory=list)
    data_confidence: AgentConfidence
    missing_data: list[str] = Field(default_factory=list)
    active_memories: list[str] = Field(default_factory=list)


class AgentMemory(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    profile_id: UUID
    category: Literal["preference", "avoidance", "habit"]
    value: str
    source_message: str
    status: Literal["active", "deleted"] = "active"
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class AgentToolCallAudit(BaseModel):
    name: str
    effect: Literal["read_only", "proposal", "confirmed_write"]
    arguments: dict[str, Any] = Field(default_factory=dict)
    success: bool
    result_summary: str | None = None
    error_code: str | None = None
    cached: bool = False


class AgentTrace(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    profile_id: UUID
    message: str
    intent: str
    decision_stage: AgentDecisionStage
    confidence: AgentConfidence
    context_snapshot: dict[str, Any]
    outcome: str
    requires_confirmation: bool = False
    provider: str = "rule"
    model: str | None = None
    tool_calls: list[AgentToolCallAudit] = Field(default_factory=list)
    fallback_used: bool = False
    fallback_reason: str | None = None
    latency_ms: int | None = None
    structured_intent: dict[str, Any] | None = None
    speech_act: SpeechAct | None = None
    temporal_status: TemporalStatus | None = None
    should_create_action: bool | None = None
    final_action_allowed: bool | None = None
    intent_conflict: str | None = None
    response_message: str | None = None
    session_summary: str | None = None
    parent_trace_id: UUID | None = None
    resolved_message: str | None = None
    needs_clarification: bool = False
    created_at: datetime = Field(default_factory=utc_now)


class AgentFeedbackRequest(BaseModel):
    rating: Literal["helpful", "not_helpful"]
    reason: str | None = Field(default=None, max_length=300)


class AgentFeedback(BaseModel):
    trace_id: UUID
    profile_id: UUID
    rating: Literal["helpful", "not_helpful"]
    reason: str | None = None
    created_at: datetime = Field(default_factory=utc_now)


class AIAction(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    profile_id: UUID
    action_type: Literal["create_meal", "create_exercise", "remember_preference", "update_safety_profile"] = "create_meal"
    title: str
    summary: str
    payload: dict[str, Any]
    preview_nutrition: Nutrition = Field(default_factory=Nutrition)
    confidence: AgentConfidence = "medium"
    assumptions: list[str] = Field(default_factory=list)
    source_trace_id: UUID | None = None
    status: Literal["proposed", "confirmed", "cancelled"] = "proposed"
    revision: int = Field(default=1, ge=1)
    created_at: datetime = Field(default_factory=utc_now)


class AIActionMealItemUpdate(BaseModel):
    """A constrained edit for an already proposed meal action.

    The food identity is intentionally not editable here. Changing food requires a
    new proposal so a short follow-up cannot silently rewrite what was recorded.
    """

    food_id: UUID
    weight_g: float = Field(gt=0, le=10000)
    consumed_ratio: float = Field(default=1, ge=0, le=1)


class AIActionUpdateRequest(BaseModel):
    meal_type: MealType
    items: list[AIActionMealItemUpdate] = Field(min_length=1, max_length=20)
    expected_revision: int | None = Field(default=None, ge=1)


class AIActionTransitionRequest(BaseModel):
    expected_revision: int | None = Field(default=None, ge=1)


class AIChatResponse(BaseModel):
    kind: AIResponseKind
    message: str
    basis: list[str] = Field(default_factory=list)
    suggestions: list[str] = Field(default_factory=list)
    action: AIAction | None = None
    cta: Literal["preview_plans"] | None = None
    trace_id: UUID | None = None
    confidence: AgentConfidence = "medium"
    decision_stage: AgentDecisionStage = "inform"
    context: AgentContext | None = None
    needs_clarification: bool = False
    clarification_options: list[str] = Field(default_factory=list)
    tool_calls: list[AgentToolCallAudit] = Field(default_factory=list)
    provider: str = "rule"
    model: str | None = None
    latency_ms: int | None = None
    fallback_used: bool = False
    fallback_reason: str | None = None
    fallback_retryable: bool = False
    intent_conflict: str | None = None


class AIHistoryTurn(BaseModel):
    trace_id: UUID
    user_message: str
    assistant_response: AIChatResponse
    created_at: datetime
    feedback_rating: Literal["helpful", "not_helpful"] | None = None


class AIHistoryPage(BaseModel):
    items: list[AIHistoryTurn]
    limit: int
    has_more: bool
    next_cursor: str | None = None
    next_before: datetime | None = None


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
    exercises: list[ExerciseRecord] = Field(default_factory=list)
    exercise_count: int = 0
    exercise_duration_min: int = 0
    exercise_calories_burned: float = 0
    net_energy_kcal: float | None = None
