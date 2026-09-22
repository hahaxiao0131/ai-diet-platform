from __future__ import annotations

import base64
from collections import Counter
from datetime import datetime, timedelta, timezone
from functools import wraps
import re
import time
from pathlib import Path
from typing import Any
from uuid import UUID

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[2] / ".env", override=False)

from .food_search import search_catalog
from .agent import answer_food_question, answer_general_diet_question, build_agent_context
from .agent_tools import AgentToolRegistry
from .assessment import assess_diet, build_assessment_provider_from_env
from .consumption_advice import build_consumption_advice
from .deepseek_agent import build_deepseek_agent_from_env, build_deepseek_intent_from_env
from .intent_guard import validate_action_intent
from .intent_provider import build_openai_assistant_from_env
from .models import (
    AgentFeedback,
    AgentFeedbackRequest,
    AgentMemory,
    AgentToolCallAudit,
    AgentTrace,
    AIAction,
    AIActionTransitionRequest,
    AIActionUpdateRequest,
    AIChatRequest,
    AIChatResponse,
    AIHistoryPage,
    AIHistoryTurn,
    AccountDeleteRequest,
    BodyFeedback,
    BodyFeedbackRequest,
    CustomFoodCreateRequest,
    DietAssessmentRequest,
    ExampleMealDraftRequest,
    Food,
    Meal,
    MealDraft,
    MealDraftConfirmRequest,
    MealDraftItem,
    MealItem,
    MealPlan,
    MealPlanPreviewRequest,
    ManualMealCreateRequest,
    Nutrition,
    IntentInterpretation,
    PackagedFoodLabelRequest,
    PhoneCodeRequest,
    PhoneLoginRequest,
    ProfileUpdate,
    SevenDayTrend,
    TrendDay,
    WechatLoginRequest,
    WeightRecordCreate,
)
from .auth import DEV_MODE, create_session, exchange_wechat_code, identity_key, issue_phone_code, logout_session, optional_user, require_user, verify_phone_code
from .openai_agent import AgentProviderError, AgentSettings, build_openai_provider_from_env
from .providers import MockAdviceProvider, MockAssistantProvider, RuleBasedAssistantProvider, is_safety_message
from .nutrition import build_today, calculate_goal, calculate_meal, scale_nutrition
from .nutrition_sources import OpenFoodFactsProvider
from .online_nutrition import build_online_nutrition_provider_from_env, validate_online_source_url
from .memory_index import PersonalMemoryIndex
from .risk import detect_profile_food_risks, detect_risks, food_allergen_context, recommendation_avoidances
from .reaction_insights import build_reaction_insights
from .scoring import score_meal
from .services import business_date
from .store import _json_value, store

app = FastAPI(title="AI Diet Management API", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"http://(localhost|127\.0\.0\.1):51\d{2}",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def atomic_store_write(func):
    @wraps(func)
    def wrapped(*args, **kwargs):
        with store.atomic():
            return func(*args, **kwargs)

    return wrapped

advice_provider = MockAdviceProvider()
assistant_provider = MockAssistantProvider()
packaged_food_provider = OpenFoodFactsProvider()
online_nutrition_provider = None
openai_agent_provider = None
openai_intent_provider = None
deepseek_agent_provider = None
deepseek_intent_provider = None
assessment_provider = None
memory_index = PersonalMemoryIndex()
ai_provider_init_error: str | None = None
online_food_init_error: str | None = None
try:
    openai_intent_provider = build_openai_assistant_from_env()
    openai_agent_provider = build_openai_provider_from_env()
    deepseek_intent_provider = build_deepseek_intent_from_env()
    deepseek_agent_provider = build_deepseek_agent_from_env()
    assessment_provider = build_assessment_provider_from_env()
except Exception:
    ai_provider_init_error = "PROVIDER_INIT_ERROR: AI Provider 初始化失败"
try:
    online_nutrition_provider = build_online_nutrition_provider_from_env(packaged_food_provider)
except Exception:
    online_food_init_error = "ONLINE_FOOD_INIT_ERROR: 联网营养查询初始化失败"


def traced_ai_response(
    profile_id,
    message,
    intent,
    context,
    response: AIChatResponse,
    *,
    structured_intent: IntentInterpretation | None = None,
    final_action_allowed: bool | None = None,
    parent_trace_id: UUID | None = None,
    resolved_message: str | None = None,
):
    effective_message = resolved_message or message
    if response.action is not None and response.action.action_type == "create_meal" and "body_feedback" not in response.action.payload:
        feedback = extract_body_feedback(effective_message)
        if feedback is not None:
            response.action.payload["body_feedback"] = feedback.model_dump(mode="json")
    if response.action is not None and response.action.action_type == "create_meal" and "eaten_at" not in response.action.payload:
        eaten_at = datetime.now(timezone.utc)
        if "昨天" in effective_message or "昨日" in effective_message:
            eaten_at -= timedelta(days=1)
        response.action.payload["eaten_at"] = eaten_at.isoformat()
    if response.action is not None and response.action.action_type == "create_meal":
        item_names = [str(item.get("name", "")).strip() for item in response.action.payload.get("items", [])]
        profile = store.profiles.get(profile_id)
        safety_risks = detect_profile_food_risks(
            profile,
            item_names,
            mode="record",
            food_allergens=action_allergen_context(response.action),
        ) if profile else []
        if safety_risks:
            response.action.payload["safety_risks"] = safety_risks
            for risk in safety_risks:
                warning = f"安全提示：{risk['message']}。确认仅用于如实记录已经发生的饮食"
                if warning not in response.action.assumptions:
                    response.action.assumptions.append(warning)
            if any(risk.get("decision_effect") == "recorded_with_warning" for risk in safety_risks):
                pending_note = "这是已发生事实的待确认记录；确认后会保存并标注高风险，未来推荐仍会拦截相关食物。"
                if pending_note not in response.message:
                    response.message = f"{response.message.rstrip()} {pending_note}"
    if response.action is not None:
        response.decision_stage = "propose"
        response.needs_clarification = False
        response.clarification_options = []
    trace = AgentTrace(
        profile_id=profile_id,
        message=message,
        intent=intent,
        decision_stage=response.decision_stage,
        confidence=response.confidence,
        context_snapshot=context.model_dump(mode="json"),
        outcome=response.kind,
        requires_confirmation=response.action is not None,
        provider=response.provider,
        model=response.model,
        tool_calls=response.tool_calls,
        fallback_used=response.fallback_used,
        fallback_reason=response.fallback_reason,
        latency_ms=response.latency_ms,
        structured_intent=structured_intent.model_dump(mode="json") if structured_intent else None,
        speech_act=structured_intent.speech_act if structured_intent else None,
        temporal_status=structured_intent.temporal_status if structured_intent else None,
        should_create_action=structured_intent.should_create_action if structured_intent else None,
        final_action_allowed=final_action_allowed,
        intent_conflict=response.intent_conflict,
        response_message=response.message,
        session_summary=f"{intent}：{response.message[:180]}",
        parent_trace_id=parent_trace_id,
        resolved_message=resolved_message if resolved_message != message else None,
        needs_clarification=response.needs_clarification,
    )
    response.trace_id = trace.id
    response.context = context
    if response.action:
        response.action.source_trace_id = trace.id
        store.ai_actions[response.action.id] = response.action
    store.agent_traces[trace.id] = trace
    store.persist()
    memory_index.upsert_conversation(trace)
    return {"data": response}


def fallback_is_retryable(reason: str | None) -> bool:
    return bool(reason and reason.split(":", 1)[0] in {"TIMEOUT", "RATE_LIMIT", "PROVIDER_ERROR", "REFUSAL_OR_EMPTY", "FORMAT_ERROR"})


def get_profile(user_id: UUID):
    return store.get_profile(user_id)


def profile_is_complete(profile) -> bool:
    return all([
        profile.sex in {"male", "female"},
        profile.age is not None,
        profile.height_cm is not None,
        profile.current_weight_kg is not None,
        profile.activity_level is not None,
        profile.primary_goal is not None,
        profile.goal_pace is not None,
    ])


def normalize_structured_intent(message: str, interpretation: IntentInterpretation) -> IntentInterpretation:
    if not (
        interpretation.intent == "meal_record"
        and interpretation.should_create_action
        and interpretation.temporal_status in {"completed", "current"}
        and interpretation.foods
        and interpretation.meal_type is None
    ):
        return interpretation
    clarification_is_only_meal_type = not interpretation.clarification_question or any(
        word in interpretation.clarification_question
        for word in ["哪一餐", "餐次", "早餐", "午餐", "晚餐", "加餐"]
    )
    return interpretation.model_copy(update={
        "meal_type": assistant_provider._meal_type(message),
        "requires_clarification": False if clarification_is_only_meal_type else interpretation.requires_clarification,
        "clarification_question": None if clarification_is_only_meal_type else interpretation.clarification_question,
    })


def preserve_explicit_meal_record(
    model_interpretation: IntentInterpretation,
    rule_interpretation: IntentInterpretation,
) -> IntentInterpretation:
    """Keep an explicit completed meal actionable when the model misclassifies it."""
    if not (
        rule_interpretation.intent == "meal_record"
        and rule_interpretation.speech_act == "statement"
        and rule_interpretation.temporal_status in {"completed", "current"}
        and rule_interpretation.should_create_action
        and rule_interpretation.foods
    ):
        return model_interpretation
    return model_interpretation.model_copy(update={
        "intent": "meal_record",
        "speech_act": "statement",
        "temporal_status": rule_interpretation.temporal_status,
        "modality": "actual",
        "foods": model_interpretation.foods or rule_interpretation.foods,
        "meal_type": model_interpretation.meal_type or rule_interpretation.meal_type,
        "should_create_action": True,
        "requires_clarification": False,
        "clarification_question": None,
    })


def extract_body_feedback(message: str) -> BodyFeedback | None:
    symptom_aliases = {
        "腹泻": ["腹泻", "拉稀", "拉肚子"],
        "腹胀": ["腹胀", "胀气", "肚子胀"],
        "犯困": ["犯困", "很困", "嗜睡"],
        "反酸": ["反酸", "烧心"],
        "偏头痛": ["偏头痛", "头痛"],
        "恶心": ["恶心", "想吐"],
        "无不适": ["无不适", "没不舒服", "没有不适"],
    }
    symptoms = [name for name, aliases in symptom_aliases.items() if any(alias in message for alias in aliases)]
    if not symptoms:
        return None
    if "无不适" in symptoms:
        symptoms = ["无不适"]
    severity = "none" if symptoms == ["无不适"] else "severe" if any(word in message for word in ["严重", "剧烈", "反复"]) else "moderate" if any(word in message for word in ["明显", "比较", "很"]) else "mild"
    timing_match = re.search(r"(\d+)\s*(分钟|小时)后", message)
    minutes = None
    if timing_match:
        minutes = int(timing_match.group(1)) * (60 if timing_match.group(2) == "小时" else 1)
    return BodyFeedback(symptoms=symptoms, severity=severity, occurred_after_minutes=minutes)


def resolve_chat_follow_up(
    message: str,
    reply_to_trace_id: UUID | None,
    profile_id: UUID,
) -> tuple[str, AgentTrace | None]:
    if reply_to_trace_id is None:
        return message, None
    parent = store.agent_traces.get(reply_to_trace_id)
    if parent is None or parent.profile_id != profile_id:
        raise HTTPException(status_code=404, detail={"code": "AI_TRACE_NOT_FOUND", "message": "引用的对话不存在"})
    if not (parent.needs_clarification or parent.decision_stage == "clarify") or not parent.structured_intent:
        return message, parent

    try:
        previous = IntentInterpretation.model_validate(parent.structured_intent)
    except ValueError:
        return message, parent
    if not previous.foods:
        return message, parent

    # Only merge short, recognizable answers. A new food or an unrelated question
    # must remain a new task even when the UI still references a clarification.
    if any(food.raw_name in message for food in previous.foods):
        return message, parent

    weight_match = re.search(r"(?<!\d)(\d+(?:\.\d+)?)\s*(?:g|克)(?!\w)", message, re.IGNORECASE)
    count_match = re.search(r"([一二两三四五六七八九十百半\d]+)\s*(个|只|枚|颗|片|根|勺|块|碗|杯|盒|份)", message)
    meal_type = next((key for key, aliases in {
        "breakfast": ("早餐", "早饭"),
        "lunch": ("午餐", "中餐", "中午"),
        "dinner": ("晚餐", "晚饭", "晚上"),
        "snack": ("加餐", "夜宵"),
    }.items() if any(alias in message for alias in aliases)), None)
    planned = any(word in message for word in ["准备吃", "计划吃", "还没吃", "想吃", "打算吃"])
    completed = any(word in message for word in ["已经吃", "吃过", "刚吃", "是已吃", "是吃过"])
    accepts_estimate = any(word in message for word in ["按常用份量", "按常见份量", "按你估算", "就按这个", "按这个估算"])
    if not any([weight_match, count_match, meal_type, planned, completed, accepts_estimate]):
        return message, parent

    if len(previous.foods) > 1 and (weight_match or count_match):
        # A bare portion cannot be assigned safely when several foods are pending.
        return message, parent

    foods = [food.model_copy(deep=True) for food in previous.foods]
    if len(foods) == 1 and weight_match:
        foods[0].explicit_weight_g = float(weight_match.group(1))
        foods[0].quantity = None
        foods[0].unit = "克"
    elif len(foods) == 1 and count_match:
        foods[0].quantity = _parse_follow_up_count(count_match.group(1))
        foods[0].unit = count_match.group(2)
        foods[0].explicit_weight_g = None

    meal_labels = {"breakfast": "早餐", "lunch": "午餐", "dinner": "晚餐", "snack": "加餐"}
    resolved_meal_type = meal_type or previous.meal_type
    meal_label = meal_labels.get(resolved_meal_type or "", "")
    food_texts = []
    for food in foods:
        if food.explicit_weight_g is not None:
            portion = f"{food.explicit_weight_g:g}克"
        elif food.quantity is not None and food.unit:
            portion = f"{food.quantity:g}{food.unit}"
        else:
            portion = ""
        food_texts.append(f"{portion}{food.raw_name}")

    if planned:
        prefix = f"我{meal_label}准备吃"
    else:
        prefix = f"我{meal_label}已经吃了"
    if "昨天" in parent.message or "昨日" in parent.message:
        prefix = prefix.replace("我", "我昨天", 1)
    resolved = prefix + "和".join(food_texts)
    feedback = extract_body_feedback(parent.message)
    if feedback is not None and not planned:
        resolved += f"，餐后出现{'、'.join(feedback.symptoms)}"
    return resolved, parent


def build_conversation_message(
    message: str,
    parent_trace: AgentTrace | None,
    profile_id: UUID,
    *,
    max_turns: int = 4,
) -> str:
    """Attach a short, ownership-checked transcript for conversational references."""

    if parent_trace is None:
        return message
    chain: list[AgentTrace] = []
    seen: set[UUID] = set()
    current: AgentTrace | None = parent_trace
    while current is not None and len(chain) < max_turns:
        if current.id in seen or current.profile_id != profile_id:
            break
        seen.add(current.id)
        chain.append(current)
        current = store.agent_traces.get(current.parent_trace_id) if current.parent_trace_id else None
    if not chain:
        return message

    transcript: list[str] = []
    for trace in reversed(chain):
        user_text = trace.resolved_message or trace.message
        transcript.append(f"用户：{user_text[:500]}")
        if trace.response_message:
            transcript.append(f"助手：{trace.response_message[:500]}")
    return (
        "【最近对话，仅用于理解当前消息中的省略、指代和补充；当前消息优先，"
        "不要把历史中未确认的饮食当作已记录事实】\n"
        + "\n".join(transcript)
        + f"\n【当前消息】\n{message}"
    )


def attach_long_term_memory(message: str, recalled: dict[str, list[str]]) -> str:
    sections: list[str] = []
    if recalled.get("preferences"):
        sections.append("已由用户确认的长期偏好（可作为事实）：\n- " + "\n- ".join(recalled["preferences"][:4]))
    if recalled.get("reactions"):
        sections.append("历史饮食—体感记录（仅表示个人观察，不证明因果）：\n- " + "\n- ".join(recalled["reactions"][:4]))
    if recalled.get("conversations"):
        sections.append("相关历史对话（只用于回忆讨论内容，未确认内容不得作为事实）：\n- " + "\n- ".join(recalled["conversations"][:4]))
    if not sections:
        return message
    return "【按当前问题召回的长期记忆】\n" + "\n".join(sections) + "\n\n" + message


def _parse_follow_up_count(value: str) -> float:
    if value.isdigit():
        return float(value)
    if value == "半":
        return 0.5
    digits = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
    if value == "十":
        return 10
    if "十" in value:
        left, right = value.split("十", 1)
        return float((digits.get(left, 1) * 10) + digits.get(right, 0))
    return float(digits.get(value, 1))


def active_goal_or_error(profile_id: UUID):
    goal = store.active_goals.get(profile_id)
    if not goal:
        raise HTTPException(status_code=409, detail={"code": "GOAL_PROPOSAL_REQUIRED", "message": "请先确认每日目标"})
    return goal


def visible_foods(profile_id: UUID | None = None) -> list[Food]:
    profile_tag = f"profile:{profile_id}" if profile_id else None
    visible: list[Food] = []
    for food in store.foods:
        owner_tags = [tag for tag in food.tags if tag.startswith("profile:")]
        if owner_tags and (profile_tag is None or profile_tag not in owner_tags):
            continue
        visible.append(food)
    preferred_by_barcode: dict[str, Food] = {}
    source_priority = {"user_confirmed_label": 4, "open_food_facts": 3, "deepseek_web_search": 2}
    for food in visible:
        if not food.barcode:
            continue
        current = preferred_by_barcode.get(food.barcode)
        if current is None or source_priority.get(food.source, 1) >= source_priority.get(current.source, 1):
            preferred_by_barcode[food.barcode] = food
    return [food for food in visible if not food.barcode or preferred_by_barcode.get(food.barcode) is food]


def action_allergen_context(action: AIAction) -> dict[str, list[str]]:
    """Resolve allergens for persistent and session-only foods in an AI proposal."""
    foods_by_id = {str(food.id): food for food in store.foods}
    for raw_food in action.payload.get("pending_foods", []):
        try:
            pending = Food.model_validate(raw_food)
        except (TypeError, ValueError):
            continue
        foods_by_id[str(pending.id)] = pending
    context: dict[str, list[str]] = {}
    for item in action.payload.get("items", []):
        name = str(item.get("name", "")).strip()
        if not name:
            continue
        declared = list(item.get("allergens") or [])
        food = foods_by_id.get(str(item.get("food_id", "")))
        allergens = declared or (list(food.allergens) if food else [])
        if allergens:
            context[name] = allergens
    return context


def nutrition_from_label(payload: CustomFoodCreateRequest) -> Nutrition:
    factor = 100 / payload.basis_weight_g
    return Nutrition(**{
        key: (value * factor if value is not None else None)
        for key, value in payload.nutrition.model_dump().items()
    })


def build_meal_from_foods(
    profile,
    goal,
    meal_type: str,
    eaten_at: datetime,
    selections: list[dict[str, Any]],
    record_source: str,
    body_feedback: BodyFeedbackRequest | BodyFeedback | dict[str, Any] | None = None,
) -> Meal:
    meal_items: list[MealItem] = []
    selected_foods: list[Food] = []
    has_estimated_portion = False
    for selected in selections:
        food_id = UUID(str(selected["food_id"]))
        food = next((item for item in store.foods if item.id == food_id), None)
        if food is None:
            raise HTTPException(status_code=400, detail={"code": "FOOD_NOT_FOUND", "message": "食物不存在"})
        weight_g = float(selected["weight_g"])
        consumed_ratio = float(selected.get("consumed_ratio", 1))
        estimated_range = selected.get("estimated_weight_range_g")
        if estimated_range and len(estimated_range) == 2 and float(estimated_range[0]) != float(estimated_range[1]):
            has_estimated_portion = True
        meal_items.append(
            MealItem(
                food_id=food.id,
                name=food.name,
                weight_g=weight_g,
                consumed_ratio=consumed_ratio,
                nutrition=scale_nutrition(food.nutrition_per_100g, weight_g, consumed_ratio),
                user_modified=False,
                estimated_weight_range_g=estimated_range,
            )
        )
        selected_foods.append(food)
    nutrition = calculate_meal(meal_items)
    names = [item.name for item in meal_items]
    risks = detect_risks(
        profile,
        names,
        nutrition,
        mode="record",
        food_allergens=food_allergen_context(meal_items, selected_foods),
    )
    meal = Meal(
        profile_id=profile.id,
        meal_type=meal_type,
        eaten_at=eaten_at,
        record_source=record_source,
        items=meal_items,
        nutrition=nutrition,
        score=score_meal(nutrition, goal.target, names),
        risks=risks,
        confidence="low" if has_estimated_portion else "high",
        body_feedback=BodyFeedback.model_validate(body_feedback.model_dump() if hasattr(body_feedback, "model_dump") else body_feedback) if body_feedback else None,
    )
    store.meals[meal.id] = meal
    return meal


MEAL_TYPE_ALIASES = {
    "breakfast": ("早餐", "早饭"),
    "lunch": ("午餐", "中餐", "中午"),
    "dinner": ("晚餐", "晚饭", "晚上"),
    "snack": ("加餐", "夜宵"),
}
MEAL_TYPE_LABELS = {"breakfast": "早餐", "lunch": "午餐", "dinner": "晚餐", "snack": "加餐"}


def pending_meal_action_for_trace(profile_id: UUID, trace_id: UUID | None) -> AIAction | None:
    """Return only the proposed action attached to the referenced chat turn."""
    if trace_id is None:
        return None
    return next((
        action for action in store.ai_actions.values()
        if action.profile_id == profile_id
        and action.action_type == "create_meal"
        and action.status == "proposed"
        and action.source_trace_id == trace_id
    ), None)


def _split_safety_values(value: str) -> list[str]:
    cleaned = value.strip(" 的，,。；;！!")
    values = [item.strip() for item in re.split(r"[、]|(?:和|与|及)", cleaned) if item.strip()]
    return values[:8]


def extract_safety_profile_statements(message: str) -> list[dict[str, str]]:
    """Recognize explicit self-reported restrictions without asking the model to infer them."""
    if "?" in message or "？" in message:
        return []
    clauses = [
        clause.strip()
        for clause in re.split(r"[，,。；;！!]|(?:另外|同时|还有|并且)|(?<!不耐受)也", message)
        if clause.strip()
    ]
    patterns = (
        ("allergies", "食物过敏", r"^(?:我|本人)?(?:吃)?(?P<value>[\u4e00-\u9fa5A-Za-z0-9、和与及]{1,24}?)(?:会|就会|容易)过敏$"),
        ("allergies", "食物过敏", r"^(?:我|本人)?(?:对)?(?P<value>[\u4e00-\u9fa5A-Za-z0-9、和与及]{1,24}?)(?:会|有)?过敏$"),
        ("food_intolerances", "食物不耐受", r"^(?:我|本人)?(?:对)?(?P<value>[\u4e00-\u9fa5A-Za-z0-9、和与及]{1,24}?)(?:有)?不耐受$"),
        ("hard_exclusions", "绝对避免食物", r"^(?:我|本人)?(?:以后)?(?:不要|别)(?:再)?(?:给我)?推荐(?P<value>[\u4e00-\u9fa5A-Za-z0-9、和与及]{1,24})$"),
        ("hard_exclusions", "绝对避免食物", r"^(?:我|本人)?(?:绝对|坚决)?(?:不能吃|不可以吃|不可吃|不吃|忌食)(?P<value>[\u4e00-\u9fa5A-Za-z0-9、和与及]{1,24})$"),
        ("digestive_sensitivities", "肠胃敏感", r"^(?:我|本人)?(?:的)?肠胃(?:比较|很|特别)?敏感$"),
        ("digestive_sensitivities", "肠胃敏感", r"^(?:我|本人)?(?:吃)?(?P<value>[\u4e00-\u9fa5A-Za-z0-9]{1,12})(?:总是|经常)(?:容易|会)(?:腹泻|腹胀|胃痛|反酸)$"),
    )
    statements: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for clause in clauses:
        for field, label, pattern in patterns:
            match = re.fullmatch(pattern, clause)
            if not match:
                continue
            raw_value = match.groupdict().get("value") or "肠胃敏感"
            for value in _split_safety_values(raw_value):
                key = (field, value)
                if key not in seen:
                    seen.add(key)
                    statements.append({"profile_field": field, "category_label": label, "value": value})
            break
    return statements


def extract_safety_profile_statement(message: str) -> dict[str, str] | None:
    """Compatibility wrapper for callers that only need the first explicit statement."""
    statements = extract_safety_profile_statements(message)
    return statements[0] if statements else None


def safety_profile_proposal(profile, source_message: str, statements: list[dict[str, str]]) -> AIChatResponse:
    first = statements[0]
    field = first["profile_field"]
    label = first["category_label"]
    value = first["value"]
    effects = {
        "allergies": "确认后会硬性拦截含该食物的推荐与保存",
        "hard_exclusions": "确认后会硬性拦截相关推荐与保存",
        "food_intolerances": "确认后推荐会主动避开，记餐时会提示风险",
        "digestive_sensitivities": "确认后用于个性化提醒，不会被当作过敏诊断",
    }
    action = AIAction(
        profile_id=profile.id,
        action_type="update_safety_profile",
        title=f"确认更新{label}" if len(statements) == 1 else f"确认更新{len(statements)}项安全档案",
        summary=f"将“{value}”加入{label}" if len(statements) == 1 else "；".join(f"{item['category_label']}：{item['value']}" for item in statements),
        payload={
            "profile_field": field,
            "value": value,
            "updates": [
                {"profile_field": item["profile_field"], "value": item["value"], "operation": "add"}
                for item in statements
            ],
            "operation": "add",
            "source_message": source_message,
        },
        confidence="high",
        assumptions=[effects[field], "只有确认后才写入安全档案，可在“我的/档案”中删除"],
    )
    return AIChatResponse(
        kind="safety_profile_proposal",
        message=(f"我识别到一条明确的{label}信息。{effects[field]}，请先确认是否写入长期安全档案。"
                 if len(statements) == 1 else f"我识别到 {len(statements)} 条明确的饮食安全信息。它们会按各自风险等级生效，请一次确认后写入长期安全档案。"),
        basis=[f"用户明确表达：{source_message}", "由后端确定性规则识别，未让模型猜测"],
        action=action,
        confidence="high",
        decision_stage="propose",
        provider="safety_profile_rules",
    )


def action_foods(action: AIAction, profile_id: UUID) -> list[Food] | None:
    visible = {food.id: food for food in visible_foods(profile_id)}
    # Online candidates are carried by a proposal until the user confirms it.
    # They are safe to use for preview recalculation, but are not added to Store.
    for pending in action.payload.get("pending_foods") or []:
        try:
            food = Food.model_validate(pending)
        except ValueError:
            continue
        visible.setdefault(food.id, food)
    foods: list[Food] = []
    for item in action.payload.get("items") or []:
        try:
            food = visible.get(UUID(str(item["food_id"])))
        except (KeyError, ValueError):
            food = None
        if food is None:
            return None
        foods.append(food)
    return foods or None


def action_summary(action: AIAction) -> str:
    return "、".join(
        f"{item.get('name', '食物')} {float(item.get('weight_g', 0)):g}g"
        for item in action.payload.get("items") or []
    )


def refresh_pending_meal_action(action: AIAction, profile_id: UUID, *, reason: str, source: str) -> None:
    """Recalculate an existing proposal with deterministic food data only."""
    foods = action_foods(action, profile_id)
    if foods is None:
        raise HTTPException(status_code=409, detail={"code": "AI_ACTION_FOOD_UNAVAILABLE", "message": "提案中的食物已不可用，请重新发起记餐。"})
    food_by_id = {food.id: food for food in foods}
    preview_items: list[MealItem] = []
    confidence = "high"
    for item in action.payload.get("items") or []:
        food = food_by_id[UUID(str(item["food_id"]))]
        weight_g = float(item["weight_g"])
        consumed_ratio = float(item.get("consumed_ratio", 1))
        estimated_range = item.get("estimated_weight_range_g")
        preview_items.append(MealItem(
            food_id=food.id,
            name=food.name,
            weight_g=weight_g,
            consumed_ratio=consumed_ratio,
            nutrition=scale_nutrition(food.nutrition_per_100g, weight_g, consumed_ratio),
            estimated_weight_range_g=estimated_range,
        ))
        if estimated_range and len(estimated_range) == 2 and estimated_range[0] != estimated_range[1]:
            confidence = "low"
    action.preview_nutrition = calculate_meal(preview_items)
    action.summary = action_summary(action)
    action.confidence = confidence
    action.revision += 1
    action.payload.setdefault("update_history", []).append({
        "at": datetime.now(timezone.utc).isoformat(),
        "source": source,
        "reason": reason,
    })


def new_food_mentioned_in_follow_up(message: str, action: AIAction, profile_id: UUID) -> bool:
    current_ids = {str(item.get("food_id")) for item in action.payload.get("items") or []}
    text = message.strip()
    for food in visible_foods(profile_id):
        if str(food.id) in current_ids:
            continue
        names = [food.name, *food.aliases]
        if any(len(name) > 1 and name in text for name in names):
            return True
    return False


def meal_type_from_message(message: str) -> str | None:
    return next((meal_type for meal_type, aliases in MEAL_TYPE_ALIASES.items() if any(alias in message for alias in aliases)), None)


def update_pending_action_from_message(
    action: AIAction,
    message: str,
    profile_id: UUID,
) -> tuple[AIChatResponse | None, str | None]:
    """Apply a short, unambiguous follow-up to one pending proposal.

    Returns a response plus a stable intent label. None means the message must be
    handled by the regular assistant flow instead of guessing which proposal to edit.
    """
    text = message.strip()
    if new_food_mentioned_in_follow_up(text, action, profile_id):
        return AIChatResponse(
            kind="clarification",
            message="这条补充里出现了新的食物。为了避免改错原来的待确认记录，我先保留原提案；请确认是要修改原餐，还是新建一条饮食记录。",
            basis=["待确认提案只允许修改原有食物的份量、餐次、时间或食用比例"],
            suggestions=["继续修改原来的份量", "我另外吃了新的食物"],
            confidence="low",
            decision_stage="clarify",
            needs_clarification=True,
        ), "meal_record_update"

    explicitly_not_eaten = (
        any(phrase in text for phrase in ("其实没吃", "没有吃", "还没吃", "准备吃", "打算吃", "计划吃"))
        or bool(re.search(r"没吃(?!完)", text))
    )
    if explicitly_not_eaten:
        action.status = "cancelled"
        action.revision += 1
        action.payload.setdefault("update_history", []).append({
            "at": datetime.now(timezone.utc).isoformat(),
            "source": "chat",
            "reason": "用户说明未实际食用，已取消待确认提案",
        })
        return AIChatResponse(
            kind="meal_record_proposal",
            message="已取消这条待确认记餐，不会写入饮食记录。",
            basis=["你说明这餐没有实际吃，系统不会保存"],
            action=action,
            confidence="high",
            decision_stage="propose",
            tool_calls=[AgentToolCallAudit(
                name="cancel_pending_meal_proposal",
                effect="proposal",
                arguments={"action_id": str(action.id)},
                success=True,
                result_summary="已取消，未写入正式饮食记录",
            )],
        ), "meal_record_update"

    foods = action_foods(action, profile_id)
    if foods is None:
        return AIChatResponse(
            kind="clarification",
            message="原提案中的食物已不可用，无法安全更新。请重新描述这餐，我会生成新的待确认记录。",
            confidence="low",
            decision_stage="clarify",
            needs_clarification=True,
        ), "meal_record_update"
    if len(foods) != 1 and (re.search(r"\d+(?:\.\d+)?\s*(?:g|克)", text, re.I) or re.search(r"[一二两三四五六七八九十半\d]+\s*(?:个|只|枚|颗|片|根|勺|块|碗|杯|盒|份)", text)):
        return AIChatResponse(
            kind="clarification",
            message="原提案里有多种食物，单独的份量无法安全分配。请说明要修改哪一种食物和对应份量。",
            confidence="low",
            decision_stage="clarify",
            needs_clarification=True,
        ), "meal_record_update"

    changes: list[str] = []
    meal_type = meal_type_from_message(text)
    if meal_type and meal_type != action.payload.get("meal_type"):
        action.payload["meal_type"] = meal_type
        changes.append(f"餐次改为{MEAL_TYPE_LABELS[meal_type]}")

    if "昨天" in text or "昨日" in text:
        action.payload["eaten_at"] = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        changes.append("记录日期改为昨天")
    elif "今天" in text:
        action.payload["eaten_at"] = datetime.now(timezone.utc).isoformat()
        changes.append("记录日期改为今天")

    if len(foods) == 1:
        selected = action.payload["items"][0]
        food = foods[0]
        weight_match = re.search(r"(?<!\d)(\d+(?:\.\d+)?)\s*(?:g|克)(?:\s*(?:一|1)?(?:个|只|枚|颗|片|根|块|份))?", text, re.I)
        count_match = re.search(r"([一二两三四五六七八九十半\d]+)\s*(个|只|枚|颗|片|根|勺|块|碗|杯|盒|份)", text)
        ratio_match = re.search(r"(?:吃了|实际吃了|只吃了)?\s*(一半|半个|半份|\d+(?:\.\d+)?\s*%)", text)
        if weight_match:
            weight_g = float(weight_match.group(1))
            selected["weight_g"] = weight_g
            selected["estimated_weight_range_g"] = [weight_g, weight_g]
            changes.append(f"{food.name}改为 {weight_g:g}g")
        elif count_match:
            count = _parse_follow_up_count(count_match.group(1))
            unit = count_match.group(2)
            per_unit = RuleBasedAssistantProvider.unit_weights.get(unit, {}).get(food.name)
            if per_unit is None:
                return AIChatResponse(
                    kind="clarification",
                    message=f"{food.name}按“{unit}”缺少可靠换算，暂不修改原提案。请补充克数，或说“按常见份量”。",
                    basis=["不能把不可靠的单位换算当作准确营养记录"],
                    confidence="low",
                    decision_stage="clarify",
                    needs_clarification=True,
                    clarification_options=["按常见份量", "我来补充克数"],
                ), "meal_record_update"
            weight_g = count * per_unit
            selected["weight_g"] = weight_g
            selected["estimated_weight_range_g"] = [round(weight_g * 0.8), round(weight_g * 1.2)]
            changes.append(f"{food.name}改为 {count:g}{unit}（约 {weight_g:g}g）")
        if ratio_match:
            ratio_text = ratio_match.group(1)
            ratio = 0.5 if ratio_text in {"一半", "半个", "半份"} else float(ratio_text.rstrip("%")) / 100
            selected["consumed_ratio"] = ratio
            changes.append(f"实际食用比例改为 {round(ratio * 100):g}%")

    accepts_estimate = any(word in text for word in ("按常用份量", "按常见份量", "按你估算", "就按这个", "按这个估算"))
    confirms_eaten = any(word in text for word in ("已经吃", "吃过", "刚吃", "是已吃", "是吃过"))
    if accepts_estimate:
        changes.append("保留常见份量估算")
    if confirms_eaten:
        changes.append("确认已实际食用")
    if not changes:
        return None, None

    refresh_pending_meal_action(action, profile_id, reason="；".join(changes), source="chat")
    action.assumptions = [*action.assumptions, f"本次补充：{'；'.join(changes)}"][-8:]
    return AIChatResponse(
        kind="meal_record_proposal",
        message=f"已更新原来的待确认记录：{'；'.join(changes)}。营养预览已按新信息重新计算，确认后才会写入。",
        basis=["已绑定到原待确认动作，没有创建新的饮食记录", "营养预览由后端食物数据重新计算"],
        action=action,
        confidence=action.confidence,
        decision_stage="propose",
        tool_calls=[
            AgentToolCallAudit(
                name="update_pending_meal_proposal",
                effect="proposal",
                arguments={"action_id": str(action.id), "changes": changes},
                success=True,
                result_summary="已更新待确认提案",
            ),
            AgentToolCallAudit(
                name="calculate_nutrition",
                effect="read_only",
                arguments={"action_id": str(action.id)},
                success=True,
                result_summary="已按更新后的份量重新计算营养",
            ),
        ],
    ), "meal_record_update"


@app.get("/health")
def health():
    return {"status": "ok", "service": "diet-api", "storage": store.backend_name}


@app.get("/api/v1/ai/status")
def ai_status(user_id: UUID = Depends(require_user)):
    provider = "deepseek" if deepseek_agent_provider is not None else "openai" if openai_agent_provider is not None else "rule"
    active_provider = deepseek_agent_provider or openai_agent_provider
    settings = AgentSettings.from_env()
    return {"data": {
        "mode": "model" if active_provider is not None else "rules",
        "provider": provider,
        "model": getattr(active_provider, "model", None),
        "available": active_provider is not None,
        "timeout_seconds": settings.timeout_seconds,
        "max_retries": settings.max_retries,
        "configuration_issue": ai_provider_init_error if active_provider is None else None,
        "online_food_search_available": online_nutrition_provider is not None,
        "online_food_search_issue": online_food_init_error,
    }}


@app.post("/api/v1/auth/mock-login")
def mock_login(payload: dict[str, str]):
    if not DEV_MODE:
        raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "接口不存在"})
    user_id = store.get_or_create_user(payload.get("mock_user_key", "demo-user-001"))
    profile = get_profile(user_id)
    return {"access_token": create_session(user_id), "user": {"id": user_id, "is_new": not profile.onboarding_completed}}


@app.post("/api/v1/auth/phone/code")
def send_phone_code(payload: PhoneCodeRequest):
    if not DEV_MODE:
        raise HTTPException(status_code=503, detail={"code": "SMS_NOT_CONFIGURED", "message": "短信服务尚未配置"})
    code = issue_phone_code(payload.phone)
    response: dict[str, Any] = {"expires_in": 300, "message": "验证码已发送"}
    response["dev_code"] = code
    return {"data": response}


@app.post("/api/v1/auth/phone/login")
def phone_login(payload: PhoneLoginRequest):
    if not verify_phone_code(payload.phone, payload.code):
        raise HTTPException(status_code=400, detail={"code": "PHONE_CODE_INVALID", "message": "验证码错误或已过期"})
    user_id = store.get_or_create_user(identity_key("phone", payload.phone))
    profile = get_profile(user_id)
    return {"data": {"access_token": create_session(user_id), "user": {"id": user_id, "is_new": not profile.onboarding_completed}}}


@app.post("/api/v1/auth/wechat/login")
def wechat_login(payload: WechatLoginRequest):
    openid = exchange_wechat_code(payload.code)
    user_id = store.get_or_create_user(identity_key("wechat", openid))
    profile = get_profile(user_id)
    return {"data": {"access_token": create_session(user_id), "user": {"id": user_id, "is_new": not profile.onboarding_completed}}}


@app.get("/api/v1/auth/session")
def auth_session(user_id: UUID = Depends(require_user)):
    profile = get_profile(user_id)
    return {"data": {"user": {"id": user_id, "is_new": not profile.onboarding_completed}}}


@app.post("/api/v1/auth/logout")
def auth_logout(authorization: str | None = Header(default=None)):
    logout_session(authorization)
    return {"data": {"ok": True}}


@app.get("/api/v1/account/export")
def export_account_data(user_id: UUID = Depends(require_user)):
    """Export only the authenticated user's structured data, never sessions."""
    profile = get_profile(user_id)
    profile_id = profile.id
    owned_foods = [
        food for food in store.foods
        if f"profile:{profile_id}" in food.tags
    ]
    feedback = [item for item in store.agent_feedback.values() if item.profile_id == profile_id]
    export = {
        "schema_version": "diet-account-export-v1",
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "profile": profile.model_dump(mode="json"),
        "goals": [goal.model_dump(mode="json") for goal in store.goal_proposals.values() if goal.profile_id == profile_id],
        "active_goal": store.active_goals.get(profile_id).model_dump(mode="json") if store.active_goals.get(profile_id) else None,
        "weight_records": _json_value(store.weights.get(profile_id, [])),
        "meals": [meal.model_dump(mode="json") for meal in store.meals.values() if meal.profile_id == profile_id],
        "meal_drafts": [draft.model_dump(mode="json") for draft in store.drafts.values() if draft.profile_id == profile_id],
        "meal_plans": [plan.model_dump(mode="json") for plan in store.plans.values() if plan.profile_id == profile_id],
        "custom_foods": [food.model_dump(mode="json") for food in owned_foods],
        "ai_memories": [memory.model_dump(mode="json") for memory in store.agent_memories.values() if memory.profile_id == profile_id],
        "ai_traces": [trace.model_dump(mode="json") for trace in store.agent_traces.values() if trace.profile_id == profile_id],
        "ai_actions": [action.model_dump(mode="json") for action in store.ai_actions.values() if action.profile_id == profile_id],
        "ai_feedback": [item.model_dump(mode="json") for item in feedback],
    }
    return {"data": export}


@app.delete("/api/v1/account")
def delete_account(payload: AccountDeleteRequest, user_id: UUID = Depends(require_user)):
    if payload.confirmation != "永久删除":
        raise HTTPException(status_code=400, detail={
            "code": "ACCOUNT_DELETE_CONFIRMATION_REQUIRED",
            "message": "请输入“永久删除”确认账号删除。",
        })
    profile = get_profile(user_id)
    profile_id = profile.id
    meal_ids = [meal.id for meal in store.meals.values() if meal.profile_id == profile_id]
    memory_ids = [memory.id for memory in store.agent_memories.values() if memory.profile_id == profile_id]
    trace_ids = [trace.id for trace in store.agent_traces.values() if trace.profile_id == profile_id]
    memory_index.delete_profile(profile_id, meal_ids=meal_ids, memory_ids=memory_ids, trace_ids=trace_ids)
    try:
        deleted = store.delete_user(user_id)
    except Exception as exc:
        raise HTTPException(status_code=500, detail={
            "code": "ACCOUNT_DELETE_FAILED",
            "message": "账号删除失败，未完成删除，请稍后重试。",
        }) from exc
    if deleted is None:
        raise HTTPException(status_code=404, detail={"code": "ACCOUNT_NOT_FOUND", "message": "账号不存在或已删除"})
    return {"data": {"deleted": True, "counts": deleted["counts"]}}


@app.get("/api/v1/profiles/me")
def profile_me(user_id: UUID = Depends(require_user)):
    return {"data": get_profile(user_id)}


@app.put("/api/v1/profiles/me")
def update_profile(payload: ProfileUpdate, user_id: UUID = Depends(require_user)):
    profile = get_profile(user_id)
    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(profile, key, value)
    if profile.onboarding_completed and not profile_is_complete(profile):
        profile.onboarding_completed = False
    store.persist()
    return {"data": profile}


@app.post("/api/v1/goals/recalculate")
def recalculate_goal(user_id: UUID = Depends(require_user)):
    profile = get_profile(user_id)
    if not profile_is_complete(profile):
        raise HTTPException(status_code=409, detail={"code": "PROFILE_INCOMPLETE", "message": "请先完成个人基础信息"})
    old = store.active_goals.get(profile.id)
    proposal = calculate_goal(profile, old.target.model_dump() if old else None)
    store.goal_proposals[proposal.id] = proposal
    store.persist()
    return {"data": proposal}


@app.post("/api/v1/goals/{proposal_id}/confirm")
@atomic_store_write
def confirm_goal(proposal_id: UUID, user_id: UUID = Depends(require_user)):
    profile = get_profile(user_id)
    proposal = store.goal_proposals.get(proposal_id)
    if not proposal or proposal.profile_id != profile.id:
        raise HTTPException(status_code=404, detail={"code": "GOAL_PROPOSAL_NOT_FOUND", "message": "目标提案不存在"})
    if profile.id in store.active_goals:
        store.active_goals[profile.id].status = "superseded"
    proposal.status = "active"
    store.active_goals[profile.id] = proposal
    profile.onboarding_completed = True
    store.persist()
    return {"data": proposal}


@app.get("/api/v1/goals/current")
def current_goal(user_id: UUID = Depends(require_user)):
    return {"data": store.active_goals.get(get_profile(user_id).id)}


@app.get("/api/v1/foods/search")
def search_foods(
    q: str = Query(default=""),
    user_id: UUID | None = Depends(optional_user),
):
    profile_id = get_profile(user_id).id if user_id else None
    foods = visible_foods(profile_id)
    if not q.strip():
        foods = [food for food in foods if food.source != "open_food_facts"]
    return {"data": search_catalog(foods, q)}


@app.get("/api/v1/foods/recent")
def recent_foods(user_id: UUID = Depends(require_user)):
    profile = get_profile(user_id)
    meals = sorted(store.meals_for(profile.id), key=lambda item: item.eaten_at, reverse=True)
    food_ids = []
    for meal in meals:
        for item in meal.items:
            if item.food_id and item.food_id not in food_ids:
                food_ids.append(item.food_id)
    foods_by_id = {food.id: food for food in visible_foods(profile.id)}
    return {"data": [foods_by_id[food_id] for food_id in food_ids if food_id in foods_by_id][:10]}


@app.get("/api/v1/foods/barcode/{barcode}")
def lookup_food_barcode(barcode: str, user_id: UUID = Depends(require_user)):
    if not barcode.isdigit() or not 8 <= len(barcode) <= 14:
        raise HTTPException(status_code=422, detail={"code": "INVALID_BARCODE", "message": "请输入 8 至 14 位商品条码"})
    profile = get_profile(user_id)
    visible = visible_foods(profile.id)
    user_label = next((food for food in reversed(visible) if food.barcode == barcode and food.source == "user_confirmed_label"), None)
    if user_label:
        return {"data": user_label}
    cached = next((food for food in reversed(visible) if food.barcode == barcode and food.source == "open_food_facts"), None)
    if cached:
        return {"data": cached}
    food = packaged_food_provider.lookup(barcode)
    if food is None:
        raise HTTPException(status_code=404, detail={"code": "BARCODE_NOT_FOUND", "message": "条码库暂未收录，请按包装营养成分表录入"})
    store.foods.append(food)
    store.persist()
    return {"data": food}


@app.post("/api/v1/foods/custom")
def create_custom_food(payload: CustomFoodCreateRequest, user_id: UUID = Depends(require_user)):
    profile = get_profile(user_id)
    nutrition_per_100g = nutrition_from_label(payload)
    food = Food(
        name=payload.name.strip(),
        food_type="custom",
        default_unit="1份",
        default_weight_g=payload.default_weight_g or payload.basis_weight_g,
        source="user_nutrition_label",
        source_version="user-label-v1",
        nutrition_per_100g=nutrition_per_100g,
        allergens=[item.strip() for item in payload.allergens if item.strip()],
        tags=["user_label", f"profile:{profile.id}"],
        confidence="high",
    )
    store.foods.append(food)
    store.persist()
    return {"data": food}


@app.post("/api/v1/foods/label")
def create_packaged_food_label(payload: PackagedFoodLabelRequest, user_id: UUID = Depends(require_user)):
    profile = get_profile(user_id)
    food = Food(
        name=payload.name.strip(),
        food_type="packaged",
        default_unit="1份",
        default_weight_g=payload.default_weight_g or payload.basis_weight_g,
        source="user_confirmed_label",
        source_version="user-label-v1",
        source_observed_at=datetime.now(timezone.utc),
        barcode=payload.barcode,
        brand=payload.brand.strip() if payload.brand else None,
        verified_by_user=True,
        nutrition_per_100g=nutrition_from_label(payload),
        allergens=[item.strip() for item in payload.allergens if item.strip()],
        tags=["user_label", "packaged", f"profile:{profile.id}"],
        confidence="high",
    )
    existing = next((item for item in store.foods if item.barcode and item.barcode == payload.barcode and f"profile:{profile.id}" in item.tags), None)
    if existing:
        food.id = existing.id
        store.foods[store.foods.index(existing)] = food
    else:
        store.foods.append(food)
    store.persist()
    return {"data": food}


def example_draft_items(foods: list[Food]) -> list[MealDraftItem]:
    """Fixed sample content for the draft-confirmation demonstration."""

    lookup = {food.name: food for food in foods}
    suggestions = [
        ("米饭", 180, "1碗"),
        ("番茄炒蛋", 180, "1份"),
        ("清炒青菜", 150, "1盘"),
    ]
    return [
        MealDraftItem(
            food_id=lookup[name].id,
            name=name,
            estimated_weight_g=weight,
            household_unit=unit,
            source="example_template",
            confidence="medium",
            nutrition=lookup[name].nutrition_per_100g,
        )
        for name, weight, unit in suggestions
    ]


@app.post("/api/v1/meal-drafts")
def retired_photo_meal_draft(user_id: UUID = Depends(require_user)):
    raise HTTPException(
        status_code=410,
        detail={
            "code": "PHOTO_RECOGNITION_NOT_AVAILABLE",
            "message": "当前版本不支持拍照或图片识别，请使用手动记录、AI 对话记餐，或打开示例识别体验。",
        },
    )


@app.post("/api/v1/meal-drafts/example")
def create_example_meal_draft(payload: ExampleMealDraftRequest, user_id: UUID = Depends(require_user)):
    profile = get_profile(user_id)
    draft = MealDraft(
        profile_id=profile.id,
        meal_type=payload.meal_type,
        items=example_draft_items(store.foods),
        assets=[],
        provider="example_template",
    )
    store.drafts[draft.id] = draft
    store.persist()
    return {"data": draft}


@app.get("/api/v1/meal-drafts/{draft_id}")
def get_meal_draft(draft_id: UUID, user_id: UUID = Depends(require_user)):
    draft = store.drafts.get(draft_id)
    if not draft or draft.profile_id != get_profile(user_id).id:
        raise HTTPException(status_code=404, detail={"code": "MEAL_DRAFT_NOT_FOUND", "message": "饮食草稿不存在"})
    return {"data": draft}


@app.patch("/api/v1/meal-drafts/{draft_id}/items/{item_id}")
def update_draft_item(draft_id: UUID, item_id: UUID, payload: dict[str, Any], user_id: UUID = Depends(require_user)):
    draft = store.drafts.get(draft_id)
    if not draft or draft.profile_id != get_profile(user_id).id:
        raise HTTPException(status_code=404, detail={"code": "MEAL_DRAFT_NOT_FOUND", "message": "饮食草稿不存在"})
    item = next((item for item in draft.items if item.id == item_id), None)
    if not item:
        raise HTTPException(status_code=404, detail={"code": "MEAL_DRAFT_ITEM_NOT_FOUND", "message": "草稿食物不存在"})
    for key in ["name", "estimated_weight_g", "household_unit", "consumed_ratio"]:
        if key in payload:
            setattr(item, key, payload[key])
    item.user_modified = True
    store.persist()
    return {"data": draft}


@app.post("/api/v1/meal-drafts/{draft_id}/confirm")
@atomic_store_write
def confirm_meal_draft(draft_id: UUID, payload: MealDraftConfirmRequest, user_id: UUID = Depends(require_user)):
    draft = store.drafts.get(draft_id)
    profile = get_profile(user_id)
    if not draft or draft.profile_id != profile.id:
        raise HTTPException(status_code=404, detail={"code": "MEAL_DRAFT_NOT_FOUND", "message": "饮食草稿不存在"})
    if draft.draft_status == "confirmed" and draft.confirmed_meal_id:
        confirmed_meal = store.meals.get(draft.confirmed_meal_id)
        if confirmed_meal:
            return {"data": confirmed_meal}
    goal = active_goal_or_error(profile.id)
    draft_map = {item.id: item for item in draft.items}
    meal_items: list[MealItem] = []
    selected_foods: list[Food] = []
    for selected in payload.items:
        draft_item = draft_map.get(selected.item_id)
        if not draft_item:
            raise HTTPException(status_code=400, detail={"code": "MEAL_DRAFT_ITEM_NOT_FOUND", "message": "确认项目不存在"})
        food = next((item for item in store.foods if item.id == selected.food_id), None)
        if food is None:
            food = next((item for item in store.foods if item.name == (selected.name or draft_item.name)), None)
        if food is None:
            raise HTTPException(status_code=400, detail={"code": "FOOD_NOT_FOUND", "message": "食物不存在"})
        nutrition = scale_nutrition(food.nutrition_per_100g, selected.weight_g, selected.consumed_ratio)
        meal_items.append(
            MealItem(
                food_id=food.id,
                name=selected.name or food.name,
                weight_g=selected.weight_g,
                consumed_ratio=selected.consumed_ratio,
                nutrition=nutrition,
                user_modified=draft_item.user_modified or selected.name is not None,
            )
        )
        selected_foods.append(food)
    nutrition = calculate_meal(meal_items)
    names = [item.name for item in meal_items]
    risks = detect_risks(
        profile,
        names,
        nutrition,
        mode="record",
        food_allergens=food_allergen_context(meal_items, selected_foods),
    )
    score = score_meal(nutrition, goal.target, names)
    meal = Meal(
        profile_id=profile.id,
        meal_type=draft.meal_type,
        eaten_at=draft.eaten_at,
        record_source="example_draft",
        items=meal_items,
        nutrition=nutrition,
        score=score,
        risks=risks,
        confidence=draft.confidence,
        body_feedback=BodyFeedback(**payload.body_feedback.model_dump()) if payload.body_feedback else None,
    )
    store.meals[meal.id] = meal
    draft.draft_status = "confirmed"
    draft.confirmed_meal_id = meal.id
    store.persist()
    if meal.body_feedback:
        memory_index.upsert_feedback(profile, meal)
    return {"data": meal}


@app.get("/api/v1/meals")
def list_meals(date_value: str | None = Query(default=None, alias="date"), user_id: UUID = Depends(require_user)):
    profile = get_profile(user_id)
    meals = store.meals_for(profile.id)
    if date_value:
        meals = [meal for meal in meals if business_date(meal.eaten_at).isoformat() == date_value]
    return {"data": sorted(meals, key=lambda item: item.eaten_at)}


@app.post("/api/v1/meals/manual")
def create_manual_meal(payload: ManualMealCreateRequest, user_id: UUID = Depends(require_user)):
    profile = get_profile(user_id)
    goal = active_goal_or_error(profile.id)
    selections = [item.model_dump() for item in payload.items]
    meal = build_meal_from_foods(profile, goal, payload.meal_type, payload.eaten_at, selections, "manual", payload.body_feedback)
    store.persist()
    if meal.body_feedback:
        memory_index.upsert_feedback(profile, meal)
    return {"data": meal}


@app.post("/api/v1/meals/{meal_id}/feedback")
def save_meal_feedback(meal_id: UUID, payload: BodyFeedbackRequest, user_id: UUID = Depends(require_user)):
    profile = get_profile(user_id)
    meal = store.meals.get(meal_id)
    if meal is None or meal.profile_id != profile.id or meal.status != "active":
        raise HTTPException(status_code=404, detail={"code": "MEAL_NOT_FOUND", "message": "饮食记录不存在"})
    meal.body_feedback = BodyFeedback(**payload.model_dump())
    store.persist()
    memory_index.upsert_feedback(profile, meal)
    return {"data": meal}


@app.get("/api/v1/today")
def today(user_id: UUID = Depends(require_user)):
    profile = get_profile(user_id)
    goal = store.active_goals.get(profile.id)
    target = goal.target if goal else Nutrition()
    return {"data": build_today(profile, store.meals_for(profile.id), target, store.exercises_for(profile.id))}


@app.get("/api/v1/trends/7-days")
def seven_day_trend(user_id: UUID = Depends(require_user)):
    profile = get_profile(user_id)
    end_date = business_date(datetime.now(timezone.utc))
    start_date = end_date - timedelta(days=6)
    historical_meals = store.meals_for(profile.id)
    meals = [meal for meal in historical_meals if start_date <= business_date(meal.eaten_at) <= end_date]
    days = []
    all_foods: set[str] = set()
    symptom_counts: Counter[str] = Counter()
    score_values: list[float] = []
    for offset in range(7):
        current_date = start_date + timedelta(days=offset)
        current = [meal for meal in meals if business_date(meal.eaten_at) == current_date]
        foods = {item.name for meal in current for item in meal.items}
        all_foods.update(foods)
        feedbacks = [meal.body_feedback for meal in current if meal.body_feedback]
        symptoms = [symptom for feedback in feedbacks for symptom in feedback.symptoms if symptom != "无不适"]
        symptom_counts.update(symptoms)
        daily_scores = [meal.score.get("score") for meal in current if meal.score and isinstance(meal.score.get("score"), (int, float))]
        if daily_scores:
            score_values.extend(daily_scores)
        days.append(TrendDay(
            date=current_date,
            meal_count=len(current),
            score=round(sum(daily_scores) / len(daily_scores)) if daily_scores else None,
            food_variety=len(foods),
            body_feedback_count=len(feedbacks),
            discomfort_count=sum(1 for feedback in feedbacks if feedback.symptoms != ["无不适"]),
            symptoms=list(dict.fromkeys(symptoms)),
        ))
    now = datetime.now(timezone.utc)
    feedback_due_count = sum(
        1 for meal in meals
        if meal.body_feedback is None and timedelta(hours=1) <= now - meal.eaten_at.astimezone(timezone.utc) <= timedelta(hours=24)
    )
    reaction_insights = build_reaction_insights(historical_meals, limit=3)
    if feedback_due_count:
        focus_action = f"先为最近的{feedback_due_count}餐补充餐后体感，让个性化判断有可靠依据。"
    elif reaction_insights and reaction_insights[0].evidence_level != "observation":
        focus_action = reaction_insights[0].next_action
    elif meals and round(sum(1 for meal in meals if meal.body_feedback) / len(meals) * 100) < 60:
        focus_action = "本周先为至少3餐补充“无不适”或真实体感，建立个人对照数据。"
    elif len(all_foods) < 12:
        focus_action = "下一餐增加一种近期较少出现的蔬菜、全谷物或豆类，并继续观察体感。"
    else:
        focus_action = "继续保持食材多样，并选择一餐记录份量和体感，验证当前饮食是否适合你。"
    trend = SevenDayTrend(
        start_date=start_date,
        end_date=end_date,
        days=days,
        average_score=round(sum(score_values) / len(score_values)) if score_values else None,
        total_meals=len(meals),
        food_variety=len(all_foods),
        feedback_completion_rate=round(sum(1 for meal in meals if meal.body_feedback) / len(meals) * 100) if meals else 0,
        common_symptoms=[name for name, _ in symptom_counts.most_common(3)],
        reaction_insights=reaction_insights,
        feedback_due_count=feedback_due_count,
        focus_action=focus_action,
    )
    return {"data": trend}


@app.post("/api/v1/ai/assess")
def run_diet_assessment(payload: DietAssessmentRequest, user_id: UUID = Depends(require_user)):
    profile = get_profile(user_id)
    all_meals = store.meals_for(profile.id)
    if payload.meal_id:
        meal = next((item for item in all_meals if item.id == payload.meal_id), None)
        if meal is None:
            raise HTTPException(status_code=404, detail={"code": "MEAL_NOT_FOUND", "message": "饮食记录不存在"})
        meals = [meal]
    else:
        end_date = business_date(datetime.now(timezone.utc))
        start_date = end_date - timedelta(days=payload.days - 1)
        meals = [meal for meal in all_meals if start_date <= business_date(meal.eaten_at) <= end_date]
    if not meals and not payload.message:
        raise HTTPException(status_code=409, detail={"code": "ASSESSMENT_DATA_REQUIRED", "message": "请先记录饮食，或补充要评估的饮食描述"})
    query = payload.message or "；".join(
        f"{meal.eaten_at.date().isoformat()}：{'、'.join(item.name for item in meal.items)}；体感：{'、'.join(meal.body_feedback.symptoms) if meal.body_feedback else '未记录'}"
        for meal in meals
    )
    return {"data": assess_diet(
        profile,
        meals,
        query,
        memory_index,
        assessment_provider,
        history_meals=all_meals,
    )}


@app.post("/api/v1/meal-plans/preview")
def preview_meal_plans(payload: MealPlanPreviewRequest, user_id: UUID = Depends(require_user)):
    profile = get_profile(user_id)
    goal = active_goal_or_error(profile.id)
    today_data = build_today(profile, store.meals_for(profile.id), goal.target, store.exercises_for(profile.id))
    avoidances = [memory.value for memory in store.memories_for(profile.id) if memory.category == "avoidance"]
    foods = visible_foods(profile.id)
    options = advice_provider.propose(
        {"remaining": today_data.remaining.model_dump(), "avoidances": [*recommendation_avoidances(profile, foods), *avoidances]},
        foods,
    )
    plans = []
    for option in options:
        items = []
        selected_foods: list[Food] = []
        for entry in option["items"]:
            food = entry["food"]
            items.append(
                MealItem(
                    food_id=food.id,
                    name=food.name,
                    weight_g=entry["weight_g"],
                    nutrition=scale_nutrition(food.nutrition_per_100g, entry["weight_g"]),
                )
            )
            selected_foods.append(food)
        nutrition = calculate_meal(items)
        risks = detect_risks(
            profile,
            [item.name for item in items],
            nutrition,
            mode="recommendation",
            food_allergens=food_allergen_context(items, selected_foods),
        )
        if any(risk["level"] == "block" for risk in risks):
            continue
        plan = MealPlan(
            profile_id=profile.id,
            meal_type=payload.meal_type,
            scenario=option["scenario"],
            title=option["title"],
            items=items,
            nutrition=nutrition,
            reason=option["reason"],
            risks=risks,
        )
        store.plans[plan.id] = plan
        plans.append(plan)
    if not plans:
        raise HTTPException(status_code=409, detail={
            "code": "SAFETY_PROFILE_BLOCKED_RECOMMENDATION",
            "message": "候选餐食均命中你的安全档案，请先调整食材或档案后重试",
        })
    store.persist()
    return {"data": {"plans": plans, "remaining_before": today_data.remaining}}


@app.post("/api/v1/meal-plans/{plan_id}/replace")
def replace_plan_item(plan_id: UUID, payload: dict[str, Any], user_id: UUID = Depends(require_user)):
    plan = store.plans.get(plan_id)
    profile = get_profile(user_id)
    if not plan or plan.profile_id != profile.id:
        raise HTTPException(status_code=404, detail={"code": "PLAN_NOT_FOUND", "message": "计划不存在"})
    index = payload.get("index", 0)
    replacement = next((food for food in store.foods if food.name == payload.get("food_name")), None)
    if not isinstance(index, int) or index < 0 or replacement is None or index >= len(plan.items):
        raise HTTPException(status_code=400, detail={"code": "FOOD_NOT_FOUND", "message": "替换食物不存在"})
    plan.items[index].food_id = replacement.id
    plan.items[index].name = replacement.name
    plan.items[index].nutrition = scale_nutrition(replacement.nutrition_per_100g, plan.items[index].weight_g)
    plan.nutrition = calculate_meal(plan.items)
    plan.risks = detect_risks(
        profile,
        [item.name for item in plan.items],
        plan.nutrition,
        mode="recommendation",
        food_allergens=food_allergen_context(plan.items, visible_foods(profile.id)),
    )
    store.persist()
    return {"data": plan}


@app.post("/api/v1/meal-plans/{plan_id}/save")
def save_plan(plan_id: UUID, user_id: UUID = Depends(require_user)):
    plan = store.plans.get(plan_id)
    profile = get_profile(user_id)
    if not plan or plan.profile_id != profile.id:
        raise HTTPException(status_code=404, detail={"code": "PLAN_NOT_FOUND", "message": "计划不存在"})
    plan.risks = detect_risks(
        profile,
        [item.name for item in plan.items],
        plan.nutrition,
        mode="recommendation",
        food_allergens=food_allergen_context(plan.items, visible_foods(profile.id)),
    )
    if any(risk["level"] == "block" for risk in plan.risks):
        blocking = next(risk for risk in plan.risks if risk["level"] == "block")
        raise HTTPException(status_code=409, detail={"code": "RISK_BLOCKED", "message": blocking["message"], "risks": plan.risks})
    plan.status = "saved"
    store.persist()
    return {"data": plan}


@app.post("/api/v1/ai/chat")
def ai_chat(payload: AIChatRequest, user_id: UUID = Depends(require_user)):
    profile = get_profile(user_id)
    request_message = payload.message
    message, parent_trace = resolve_chat_follow_up(request_message, payload.reply_to_trace_id, profile.id)
    parent_trace_id = parent_trace.id if parent_trace else None
    goal = active_goal_or_error(profile.id)
    meals = store.meals_for(profile.id)
    today_data = build_today(profile, meals, goal.target, store.exercises_for(profile.id))
    memories = store.memories_for(profile.id)
    recalled_memory = memory_index.retrieve(
        profile,
        meals,
        request_message,
        limit=4,
        memories=memories,
        traces=store.agent_traces.values(),
    )
    model_message = attach_long_term_memory(
        build_conversation_message(message, parent_trace, profile.id),
        recalled_memory,
    )
    recalled_count = sum(len(recalled_memory.get(key, [])) for key in ["preferences", "reactions", "conversations"])
    memory_audit = AgentToolCallAudit(
        name="get_long_term_memory",
        effect="read_only",
        arguments={"query": request_message[:120]},
        success=True,
        result_summary=f"召回 {recalled_count} 条相关长期记忆（{recalled_memory['backend'][0]}）",
    ) if recalled_count else None

    def include_memory_audit(response: AIChatResponse) -> AIChatResponse:
        if memory_audit is not None and not any(call.name == memory_audit.name for call in response.tool_calls):
            response.tool_calls.insert(0, memory_audit)
        return response

    context = build_agent_context(today_data, memories)
    foods = visible_foods(profile.id)
    settings = AgentSettings.from_env()
    intent_provider = deepseek_intent_provider or openai_intent_provider
    agent_provider = deepseek_agent_provider or openai_agent_provider
    agent_started = time.perf_counter()
    rule_structured = assistant_provider.interpret_structured(message, foods)

    safety_statements = extract_safety_profile_statements(message)
    if safety_statements:
        response = safety_profile_proposal(profile, request_message, safety_statements)
        return traced_ai_response(
            profile.id,
            request_message,
            "safety_profile_update",
            context,
            response,
            structured_intent=rule_structured,
            final_action_allowed=True,
            parent_trace_id=parent_trace_id,
            resolved_message=message,
        )

    if is_safety_message(message):
        return traced_ai_response(profile.id, request_message, "safety", context, AIChatResponse(
            kind="safety",
            message="这个问题涉及疾病、用药或特殊生理状态，我不能据此替你调整治疗或给出诊断性饮食方案。请先咨询医生或注册营养师；我可以继续帮你如实记录饮食。",
            basis=["当前助手只提供一般饮食管理支持", "医疗目标和禁忌必须由专业人员确认"],
            suggestions=["帮我记录刚才吃的食物", "查看今天的普通饮食记录"],
            confidence="high",
            decision_stage="safety",
            provider="safety_guard",
        ), structured_intent=rule_structured, final_action_allowed=False,
            parent_trace_id=parent_trace_id, resolved_message=message)

    # A short reply to a proposed meal is an amendment, not a new task. Restrict
    # this to the action attached to the explicitly referenced trace so unrelated
    # conversation or another user's proposal can never be edited accidentally.
    pending_action = pending_meal_action_for_trace(profile.id, parent_trace_id)
    if pending_action is not None and parent_trace is not None and not parent_trace.needs_clarification and parent_trace.decision_stage != "clarify":
        pending_response, pending_intent = update_pending_action_from_message(
            pending_action,
            message,
            profile.id,
        )
        if pending_response is not None:
            return traced_ai_response(
                profile.id,
                request_message,
                pending_intent or "meal_record_update",
                context,
                pending_response,
                structured_intent=rule_structured,
                final_action_allowed=pending_action.status == "proposed",
                parent_trace_id=parent_trace_id,
                resolved_message=message,
            )

    fallback_reason: str | None = None
    fallback_tool_calls = []
    structured = rule_structured
    intent_provider_name = "rule"
    intent_model: str | None = None
    intent_latency_ms = 0
    if intent_provider is not None:
        try:
            intent_result = intent_provider.interpret(model_message)
            structured = intent_result.interpretation
            intent_provider_name = intent_result.provider
            intent_model = intent_result.model
            intent_latency_ms = intent_result.latency_ms
        except AgentProviderError as exc:
            fallback_reason = f"{exc.code}: {exc.message}"
        except Exception:
            fallback_reason = "PROVIDER_ERROR: 意图模型暂时不可用"
    elif settings.provider in {"openai", "deepseek"}:
        fallback_reason = ai_provider_init_error or "AI_NOT_CONFIGURED: 未配置对应 Provider 的 API Key 或模型"

    structured = preserve_explicit_meal_record(structured, rule_structured)
    structured = normalize_structured_intent(message, structured)

    guard = validate_action_intent(message, structured)
    tools = AgentToolRegistry(
        profile=profile,
        goal=goal,
        foods=foods,
        meals=meals,
        memories=memories,
        advice_provider=advice_provider,
        online_nutrition_provider=online_nutrition_provider,
    )

    if settings.provider in {"openai", "deepseek"} and agent_provider is None and fallback_reason is None:
        fallback_reason = ai_provider_init_error or "AI_NOT_CONFIGURED: 智能体 Provider 未初始化"

    if structured.intent == "consumption_advice" and (agent_provider is None or fallback_reason is not None):
        response = build_consumption_advice(message, structured, tools)
        response.provider = f"{intent_provider_name}+rules" if intent_provider_name != "rule" else "rule"
        response.model = intent_model
        response.latency_ms = round((time.perf_counter() - agent_started) * 1000)
        response.fallback_used = fallback_reason is not None
        response.fallback_reason = fallback_reason
        response.fallback_retryable = fallback_is_retryable(fallback_reason)
        include_memory_audit(response)
        return traced_ai_response(
            profile.id,
            request_message,
            structured.intent,
            context,
            response,
            structured_intent=structured,
            final_action_allowed=False,
            parent_trace_id=parent_trace_id,
            resolved_message=message,
        )

    if structured.intent == "meal_record" and not guard.allowed:
        if structured.foods and structured.temporal_status in {"planned", "hypothetical"}:
            advice_intent = structured.model_copy(update={
                "intent": "consumption_advice",
                "should_create_action": False,
            })
            response = build_consumption_advice(message, advice_intent, tools)
        else:
            response = AIChatResponse(
                kind="clarification",
                message="这句话还不能确认是已经吃过，还是在询问之后是否要吃，因此不会生成记餐动作。",
                basis=["疑问、计划、愿望和假设表达不能写入饮食记录"],
                suggestions=["我刚吃了两根香蕉", "我可以再吃两根香蕉吗"],
                confidence="low",
                decision_stage="clarify",
                needs_clarification=True,
                clarification_options=["这是已经吃过的", "这是准备吃的"],
            )
        response.intent_conflict = guard.conflict_reason
        response.provider = f"{intent_provider_name}+rules" if intent_provider_name != "rule" else "rule"
        response.model = intent_model
        response.latency_ms = round((time.perf_counter() - agent_started) * 1000)
        include_memory_audit(response)
        return traced_ai_response(
            profile.id,
            request_message,
            structured.intent,
            context,
            response,
            structured_intent=structured,
            final_action_allowed=False,
            parent_trace_id=parent_trace_id,
            resolved_message=message,
        )

    if agent_provider is not None and fallback_reason is None:
        try:
            result = agent_provider.run(
                model_message,
                tools,
                interpretation=structured,
                final_action_allowed=guard.allowed,
            )
            final = result.final
            action = result.action
            intent_conflict = None
            if structured.intent == "consumption_advice":
                required_tools = {"get_today_context", "search_food", "calculate_nutrition"}
                if any(food.quantity is not None and food.unit is not None and food.explicit_weight_g is None for food in structured.foods):
                    required_tools.add("convert_food_portion")
                successful_tools = {call.name for call in result.tool_calls if call.success}
                if not required_tools.issubset(successful_tools):
                    validation_error = AgentProviderError("TOOL_VALIDATION", "消费建议缺少后端营养校验")
                    validation_error.tool_calls = result.tool_calls
                    raise validation_error
                if action is not None:
                    intent_conflict = "消费建议禁止生成记餐或记忆动作"
                action = None
            if action is not None and action.action_type == "create_meal" and action.payload.get("pending_foods"):
                successful_tools = {call.name for call in result.tool_calls if call.success}
                online_lookup_completed = "search_online_nutrition" in successful_tools or any(
                    call.name == "search_food"
                    and call.success
                    and bool(call.result_summary and "已优先联网" in call.result_summary)
                    for call in result.tool_calls
                )
                required_online_tools = {"search_food", "calculate_nutrition", "propose_meal_record"}
                if not required_online_tools.issubset(successful_tools) or not online_lookup_completed:
                    validation_error = AgentProviderError(
                        "ONLINE_TOOL_VALIDATION",
                        "联网食物记餐缺少本地查询、来源检索或后端营养校验",
                    )
                    validation_error.tool_calls = result.tool_calls
                    raise validation_error
            action_matches_intent = action is None or (
                action.action_type == "create_meal" and structured.intent == "meal_record"
            ) or (
                action.action_type == "remember_preference" and structured.intent == "memory_preference"
            )
            if action is not None and (not guard.allowed or not action_matches_intent):
                action = None
                intent_conflict = guard.conflict_reason or "模型动作类型与结构化意图不一致"
            return traced_ai_response(profile.id, request_message, final.goal, context, AIChatResponse(
                kind="consumption_advice" if structured.intent == "consumption_advice" else final.kind,
                message=final.message,
                basis=final.basis,
                suggestions=final.suggestions,
                action=action,
                cta="preview_plans" if final.cta == "preview_plans" else None,
                confidence=final.confidence,
                decision_stage=final.decision_stage,
                needs_clarification=final.needs_clarification,
                clarification_options=final.clarification_options,
                tool_calls=([memory_audit] if memory_audit is not None else []) + result.tool_calls,
                provider=result.provider,
                model=result.model,
                latency_ms=result.latency_ms + intent_latency_ms,
                intent_conflict=intent_conflict,
            ), structured_intent=structured, final_action_allowed=bool(action) if result.action else guard.allowed,
                parent_trace_id=parent_trace_id, resolved_message=message)
        except AgentProviderError as exc:
            fallback_reason = f"{exc.code}: {exc.message}"
            fallback_tool_calls = exc.tool_calls
        except Exception:
            fallback_reason = "PROVIDER_ERROR: 模型服务暂时不可用"

    interpretation = assistant_provider.interpret(message, foods)
    intent = interpretation["intent"]

    def rule_response(response: AIChatResponse):
        include_memory_audit(response)
        response.provider = "rule"
        response.model = getattr(agent_provider, "model", settings.model) if fallback_reason else None
        response.fallback_used = fallback_reason is not None
        response.fallback_reason = fallback_reason
        response.fallback_retryable = fallback_is_retryable(fallback_reason)
        merged_tool_calls = []
        for call in [*fallback_tool_calls, *response.tool_calls]:
            if any(
                existing.name == call.name
                and existing.arguments == call.arguments
                and existing.success == call.success
                for existing in merged_tool_calls
            ):
                continue
            merged_tool_calls.append(call)
        response.tool_calls = merged_tool_calls
        response.latency_ms = round((time.perf_counter() - agent_started) * 1000) if fallback_reason else None
        if response.action is not None and not guard.allowed:
            response.action = None
            response.intent_conflict = guard.conflict_reason or "动作未通过后端确定性校验"
            response.kind = "clarification"
            response.decision_stage = "clarify"
            response.needs_clarification = True
            response.message = "这句话还不能确认是已发生的饮食记录，因此没有创建待确认动作。"
        return traced_ai_response(
            profile.id,
            request_message,
            intent,
            context,
            response,
            structured_intent=structured,
            final_action_allowed=bool(response.action) if response.action else guard.allowed,
            parent_trace_id=parent_trace_id,
            resolved_message=message,
        )

    if intent == "safety":
        return rule_response(AIChatResponse(
            kind="safety",
            message="这个问题涉及疾病、用药或特殊生理状态，我不能据此替你调整治疗或给出诊断性饮食方案。请先咨询医生或注册营养师；我可以继续帮你如实记录饮食。",
            basis=["当前助手只提供一般饮食管理支持", "医疗目标和禁忌必须由专业人员确认"],
            suggestions=["帮我记录刚才吃的食物", "查看今天的普通饮食记录"],
            confidence="high",
            decision_stage="safety",
        ))

    if intent == "consumption_advice":
        return rule_response(build_consumption_advice(message, structured, tools))

    if intent == "memory_preference":
        value = interpretation.get("memory_value")
        if not value:
            return rule_response(AIChatResponse(
                kind="clarification",
                message="我理解你想设置饮食偏好，但还没识别出具体食物。请直接说，例如“我不喜欢鸡胸肉”。",
                basis=["只记住你明确表达并确认的偏好"],
                suggestions=["我不喜欢鸡胸肉", "我喜欢豆腐"],
                confidence="low",
                decision_stage="clarify",
                needs_clarification=True,
            ))
        category = interpretation["memory_category"]
        verb = "避免" if category == "avoidance" else "优先考虑"
        action = AIAction(
            profile_id=profile.id,
            action_type="remember_preference",
            title="确认保存饮食偏好",
            summary=f"后续建议中{verb}{value}",
            payload={"category": category, "value": value, "source_message": request_message},
            confidence="high",
        )
        return rule_response(AIChatResponse(
            kind="memory_proposal",
            message="我可以把这条信息作为可管理的饮食偏好保存。确认后才会用于后续建议，你也可以随时删除。",
            basis=[f"识别到食物：{value}", "记忆只影响推荐，不会修改历史饮食记录"],
            suggestions=[],
            action=action,
            confidence="high",
            decision_stage="propose",
        ))

    if intent == "food_nutrition":
        answer = answer_food_question(message, interpretation["items"])
        return rule_response(AIChatResponse(
            kind="food_nutrition",
            message=answer["message"],
            basis=answer["basis"],
            suggestions=["米饭和馒头哪个热量高？", "怎么看营养成分表？"],
            confidence=answer["confidence"],
            decision_stage="inform",
        ))

    if intent == "dietary_knowledge":
        answer = answer_food_question(message, interpretation["items"]) if interpretation["items"] else answer_general_diet_question(message)
        return rule_response(AIChatResponse(
            kind="dietary_knowledge",
            message=answer["message"],
            basis=answer["basis"],
            suggestions=["减脂可以吃主食吗？", "高蛋白食物有哪些？", "怎么看营养成分表？"],
            confidence=answer["confidence"],
            decision_stage="inform",
        ))

    if intent == "protein_explanation":
        consumed = round(today_data.consumed.protein_g)
        target = round(today_data.target.protein_g)
        remaining = max(0, round(today_data.remaining.protein_g))
        if today_data.completeness["recorded_meals"] == 0:
            message = "今天还没有有效餐次记录，所以系统暂时无法判断真实摄入。先记下一餐，蛋白质判断会更可靠。"
        elif remaining > 0:
            message = f"目前记录的食物共提供约 {consumed}g 蛋白质，距离今日建议还差约 {remaining}g。下一餐优先安排一份明确的优质蛋白。"
        else:
            message = f"你今天已记录约 {consumed}g 蛋白质，已经达到当前建议目标。"
        return rule_response(AIChatResponse(
            kind="explanation",
            message=message,
            basis=[
                f"蛋白质 {consumed}g / {target}g",
                (
                    f"已记录 {today_data.completeness['recorded_meals']} 餐，基础餐次已完整"
                    if today_data.completeness["recorded_meals"] >= today_data.completeness["expected_meals"]
                    else f"已记录 {today_data.completeness['recorded_meals']} / {today_data.completeness['expected_meals']} 餐"
                ),
            ],
            suggestions=["今天还能吃什么？", "帮我安排下一餐"],
            confidence=today_data.confidence,
            decision_stage="inform",
        ))

    if intent == "plan_recommendation":
        remaining_energy = max(0, round(today_data.remaining.energy_kcal))
        remaining_protein = max(0, round(today_data.remaining.protein_g))
        caution = f"，同时注意{today_data.near_limits[0]}已接近上限" if today_data.near_limits else ""
        memory_basis = [f"已考虑偏好：{'、'.join(context.active_memories)}"] if context.active_memories else []
        return rule_response(AIChatResponse(
            kind="plan_recommendation",
            message=f"下一餐可以优先补约 {remaining_protein}g 的蛋白质缺口{caution}。我会给你家常、快手和便利三种方案，营养值先预览再决定。",
            basis=[f"今日剩余约 {remaining_energy} kcal", f"蛋白质还差约 {remaining_protein}g", *memory_basis],
            suggestions=["为什么蛋白质不足？", "鸡胸肉能换什么？"],
            cta="preview_plans",
            confidence=today_data.confidence,
            decision_stage="inform",
        ))

    if intent == "food_replacement":
        found_names = [item["food"].name for item in interpretation["items"]]
        replacement_map = {
            "鸡胸肉": ["虾仁", "豆腐", "鸡蛋"],
            "虾仁": ["鸡胸肉", "豆腐", "鸡蛋"],
            "牛奶": ["鸡蛋", "豆腐"],
            "米饭": ["燕麦", "香蕉"],
        }
        source = found_names[0] if found_names else "当前蛋白质"
        avoided = {memory.value for memory in memories if memory.category == "avoidance"}
        avoided.update(profile.hard_exclusions)
        alternatives = [name for name in replacement_map.get(source, ["虾仁", "豆腐", "鸡蛋"]) if name not in avoided]
        if not alternatives:
            alternatives = ["豆腐", "鸡蛋"]
        return rule_response(AIChatResponse(
            kind="food_replacement",
            message=f"{source}可以优先换成{'、'.join(alternatives)}。替换时需要按实际份量重新计算，不能只按食物名称一比一替换。",
            basis=["替代顺序优先考虑营养接近", "禁忌与过敏优先于口味偏好"],
            suggestions=["帮我安排下一餐", "今天还能吃什么？"],
            cta="preview_plans",
            confidence="medium" if found_names else "low",
            decision_stage="inform",
        ))

    if intent == "meal_record":
        estimated_items = [item for item in interpretation["items"] if item["weight_source"] != "explicit_weight"]
        preview_items: list[MealItem] = []
        action_items = []
        for extracted in interpretation["items"]:
            food = extracted["food"]
            weight_g = extracted["weight_g"]
            if extracted["weight_source"] == "explicit_weight":
                spread = 0
            elif "蟹" in food.name:
                spread = 0.35
            elif extracted["weight_source"] == "household_unit":
                spread = 0.2
            else:
                spread = 0.3
            weight_range = [round(weight_g * (1 - spread)), round(weight_g * (1 + spread))]
            preview_items.append(MealItem(
                food_id=food.id,
                name=food.name,
                weight_g=weight_g,
                nutrition=scale_nutrition(food.nutrition_per_100g, weight_g),
                estimated_weight_range_g=weight_range,
            ))
            action_items.append({
                "food_id": str(food.id),
                "name": food.name,
                "weight_g": weight_g,
                "consumed_ratio": 1,
                "estimated_weight_range_g": weight_range,
            })
        preview_nutrition = calculate_meal(preview_items)
        item_summary = "、".join(f"{item['name']} {round(item['weight_g'])}g" for item in action_items)
        action = AIAction(
            profile_id=profile.id,
            title="确认这次饮食记录",
            summary=item_summary,
            payload={"meal_type": interpretation["meal_type"], "items": action_items},
            preview_nutrition=preview_nutrition,
            confidence="low" if estimated_items else interpretation["confidence"],
            assumptions=[
                f"{item['assumption']}，实际份量暂按约{action_item['estimated_weight_range_g'][0]}-{action_item['estimated_weight_range_g'][1]}g理解"
                if item["weight_source"] != "explicit_weight" else item["assumption"]
                for item, action_item in zip(interpretation["items"], action_items) if item["assumption"]
            ],
        )
        portion_basis = "份量来自你的明确描述" if not action.assumptions else "口语份量已换算为估算区间，并明确列出假设"
        return rule_response(AIChatResponse(
            kind="meal_record_proposal",
            message="我识别出了下面这些食物和份量。它们目前只是待确认提案，确认后才会计入今日数据。",
            basis=[portion_basis, "最终营养由后端规则计算"],
            suggestions=["今天还能吃什么？"],
            action=action,
            confidence="low" if estimated_items else interpretation["confidence"],
            decision_stage="propose",
        ))

    return rule_response(AIChatResponse(
        kind="clarification",
        message="我可以回答食物热量、营养成分、份量换算、食物比较和常见饮食搭配问题。请补充具体食物或你最关心的目标。",
        basis=["普通饮食知识可以直接回答", "疾病治疗、诊断和用药仍需要专业人员"],
        suggestions=["200g挂面的热量是多少？", "减脂可以吃主食吗？", "午餐吃了150克米饭和两个鸡蛋"],
        confidence="low",
        decision_stage="clarify",
        needs_clarification=True,
    ))


@app.get("/api/v1/ai/context")
def get_ai_context(user_id: UUID = Depends(require_user)):
    profile = get_profile(user_id)
    goal = active_goal_or_error(profile.id)
    today_data = build_today(profile, store.meals_for(profile.id), goal.target, store.exercises_for(profile.id))
    return {"data": build_agent_context(today_data, store.memories_for(profile.id))}


@app.get("/api/v1/ai/history")
def get_ai_history(
    limit: int = Query(default=30, ge=1, le=50),
    cursor: str | None = Query(default=None),
    before: datetime | None = Query(default=None),
    user_id: UUID = Depends(require_user),
):
    """Return a bounded, display-safe conversation page for the current profile."""
    profile = get_profile(user_id)
    cursor_key: tuple[datetime, str] | None = None
    if cursor:
        try:
            padded = cursor + "=" * (-len(cursor) % 4)
            decoded = base64.b64decode(padded, altchars=b"-_", validate=True).decode("utf-8")
            encoded_created_at, encoded_trace_id = decoded.split("|", maxsplit=1)
            cursor_created_at = datetime.fromisoformat(encoded_created_at.replace("Z", "+00:00"))
            if cursor_created_at.tzinfo is None:
                raise ValueError("cursor timestamp must include a timezone")
            cursor_key = (cursor_created_at, str(UUID(encoded_trace_id)))
        except (UnicodeDecodeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail={
                "code": "INVALID_AI_HISTORY_CURSOR",
                "message": "历史记录分页游标无效，请重新加载。",
            }) from exc

    def is_before_page(trace: AgentTrace) -> bool:
        trace_key = (trace.created_at, str(trace.id))
        if cursor_key is not None:
            return trace_key < cursor_key
        return before is None or trace.created_at < before

    traces = [
        trace for trace in store.agent_traces.values()
        if trace.profile_id == profile.id and is_before_page(trace)
    ]
    traces.sort(key=lambda trace: (trace.created_at, str(trace.id)), reverse=True)
    page = traces[:limit + 1]
    has_more = len(page) > limit
    selected = list(reversed(page[:limit]))
    actions_by_trace = {
        action.source_trace_id: action
        for action in store.ai_actions.values()
        if action.profile_id == profile.id and action.source_trace_id is not None
    }
    valid_kinds = {
        "explanation", "meal_record_proposal", "consumption_advice", "plan_recommendation",
        "food_replacement", "food_nutrition", "dietary_knowledge", "memory_proposal",
        "safety_profile_proposal", "clarification", "safety", "health_assessment",
    }
    items: list[AIHistoryTurn] = []
    for trace in selected:
        feedback = store.agent_feedback.get(trace.id)
        action = actions_by_trace.get(trace.id)
        response = AIChatResponse(
            kind=trace.outcome if trace.outcome in valid_kinds else "explanation",
            message=trace.response_message or "这条历史回答没有可恢复的正文。",
            action=action,
            trace_id=trace.id,
            confidence=trace.confidence,
            decision_stage="propose" if action and action.status == "proposed" else trace.decision_stage,
            provider=trace.provider,
            model=trace.model,
            latency_ms=trace.latency_ms,
            fallback_used=trace.fallback_used,
            fallback_reason=trace.fallback_reason,
            intent_conflict=trace.intent_conflict,
        )
        items.append(AIHistoryTurn(
            trace_id=trace.id,
            user_message=trace.message,
            assistant_response=response,
            created_at=trace.created_at,
            feedback_rating=feedback.rating if feedback and feedback.profile_id == profile.id else None,
        ))
    oldest = selected[0] if has_more and selected else None
    next_cursor = None
    if oldest is not None:
        raw_cursor = f"{oldest.created_at.isoformat()}|{oldest.id}".encode("utf-8")
        next_cursor = base64.urlsafe_b64encode(raw_cursor).decode("ascii").rstrip("=")
    result = AIHistoryPage(
        items=items,
        limit=limit,
        has_more=has_more,
        next_cursor=next_cursor,
        next_before=oldest.created_at if oldest is not None else None,
    )
    return {"data": result}


@app.get("/api/v1/ai/memories")
def get_ai_memories(user_id: UUID = Depends(require_user)):
    profile = get_profile(user_id)
    return {"data": store.memories_for(profile.id)}


@app.delete("/api/v1/ai/memories/{memory_id}")
def delete_ai_memory(memory_id: UUID, user_id: UUID = Depends(require_user)):
    profile = get_profile(user_id)
    memory = store.agent_memories.get(memory_id)
    if not memory or memory.profile_id != profile.id or memory.status == "deleted":
        raise HTTPException(status_code=404, detail={"code": "AI_MEMORY_NOT_FOUND", "message": "这条记忆不存在"})
    memory.status = "deleted"
    memory.updated_at = datetime.now(timezone.utc)
    memory_index.delete_preference(memory.id)
    store.persist()
    return {"data": memory}


@app.post("/api/v1/ai/traces/{trace_id}/feedback")
def give_ai_feedback(trace_id: UUID, payload: AgentFeedbackRequest, user_id: UUID = Depends(require_user)):
    profile = get_profile(user_id)
    trace = store.agent_traces.get(trace_id)
    if not trace or trace.profile_id != profile.id:
        raise HTTPException(status_code=404, detail={"code": "AI_TRACE_NOT_FOUND", "message": "这次回答不存在"})
    feedback = AgentFeedback(trace_id=trace_id, profile_id=profile.id, **payload.model_dump())
    store.agent_feedback[trace_id] = feedback
    store.persist()
    return {"data": feedback}


def materialize_confirmed_online_foods(action: AIAction, profile_id: UUID) -> None:
    """Persist sourced food snapshots only inside the confirmed write boundary."""

    snapshots = action.payload.get("pending_foods") or []
    if not snapshots:
        return
    referenced_ids = {str(item.get("food_id")) for item in action.payload.get("items") or []}
    allowed_sources = {"open_food_facts", "deepseek_web_search"}
    seen_ids: set[UUID] = set()
    for raw in snapshots:
        try:
            food = Food.model_validate(raw)
        except Exception as exc:
            raise HTTPException(
                status_code=409,
                detail={"code": "ONLINE_FOOD_INVALID", "message": "联网食物快照无法通过校验"},
            ) from exc
        try:
            validate_online_source_url(food.source_url or "")
        except ValueError as exc:
            raise HTTPException(
                status_code=409,
                detail={"code": "ONLINE_FOOD_REJECTED", "message": "联网营养资料来源地址不安全"},
            ) from exc
        nutrition = food.nutrition_per_100g
        nutrition_values = [nutrition.protein_g, nutrition.fat_g, nutrition.carbs_g, nutrition.sugars_g or 0]
        valid_nutrition = (
            0 < nutrition.energy_kcal <= 900
            and any(value > 0 for value in nutrition_values)
            and all(0 <= value <= 100 for value in nutrition_values)
            and 0 <= nutrition.sodium_mg <= 50000
        )
        if (
            str(food.id) not in referenced_ids
            or food.id in seen_ids
            or food.source not in allowed_sources
            or food.confidence == "high"
            or not valid_nutrition
        ):
            raise HTTPException(
                status_code=409,
                detail={"code": "ONLINE_FOOD_REJECTED", "message": "联网营养资料缺少可靠来源或数值异常"},
            )
        seen_ids.add(food.id)
        profile_tag = f"profile:{profile_id}"
        existing = next(
            (item for item in store.foods if item.source_url == food.source_url and item.name == food.name and profile_tag in item.tags),
            None,
        )
        if existing is not None:
            for selected in action.payload.get("items") or []:
                if str(selected.get("food_id")) == str(food.id):
                    selected["food_id"] = str(existing.id)
            continue
        food.tags = [tag for tag in food.tags if not tag.startswith("profile:")]
        food.tags.append(profile_tag)
        food.verified_by_user = False
        store.foods.append(food)


@app.patch("/api/v1/ai/actions/{action_id}")
@atomic_store_write
def update_ai_meal_action(
    action_id: UUID,
    payload: AIActionUpdateRequest,
    user_id: UUID = Depends(require_user),
):
    """Edit a proposed meal without ever turning it into a confirmed record."""
    profile = get_profile(user_id)
    action = store.ai_actions.get(action_id)
    if not action or action.profile_id != profile.id or action.action_type != "create_meal":
        raise HTTPException(status_code=404, detail={"code": "AI_ACTION_NOT_FOUND", "message": "待确认记餐不存在"})
    if action.status != "proposed":
        raise HTTPException(status_code=409, detail={"code": "AI_ACTION_NOT_EDITABLE", "message": "只有待确认的记餐可以修改"})
    require_action_revision(action, payload.expected_revision)

    current_items = action.payload.get("items") or []
    current_ids = {str(item.get("food_id")) for item in current_items}
    updates = {str(item.food_id): item for item in payload.items}
    if current_ids != set(updates) or len(current_items) != len(payload.items):
        raise HTTPException(status_code=400, detail={
            "code": "AI_ACTION_ITEMS_IMMUTABLE",
            "message": "修改时不能替换或增删食物；如需换食物，请取消后重新描述这餐。",
        })

    for item in current_items:
        update = updates[str(item["food_id"])]
        item["weight_g"] = update.weight_g
        item["consumed_ratio"] = update.consumed_ratio
        item["estimated_weight_range_g"] = [update.weight_g, update.weight_g]
    action.payload["meal_type"] = payload.meal_type
    refresh_pending_meal_action(action, profile.id, reason="用户在待确认卡片中手动修改餐次、份量或食用比例", source="manual_edit")
    action.assumptions = [*action.assumptions, "已按你在待确认卡片中的手动填写重新计算营养预览"][-8:]

    goal = active_goal_or_error(profile.id)
    today_data = build_today(profile, store.meals_for(profile.id), goal.target, store.exercises_for(profile.id))
    trace = AgentTrace(
        profile_id=profile.id,
        message="用户在待确认记餐卡片中修改提案",
        intent="meal_record_update",
        decision_stage="propose",
        confidence=action.confidence,
        context_snapshot=build_agent_context(today_data, store.memories_for(profile.id)).model_dump(mode="json"),
        outcome="meal_record_proposal",
        requires_confirmation=True,
        provider="manual_action_editor",
        tool_calls=[
            AgentToolCallAudit(
                name="update_pending_meal_proposal",
                effect="proposal",
                arguments={"action_id": str(action.id), "meal_type": payload.meal_type},
                success=True,
                result_summary="已更新待确认提案",
            ),
            AgentToolCallAudit(
                name="calculate_nutrition",
                effect="read_only",
                arguments={"action_id": str(action.id)},
                success=True,
                result_summary="已按手动修改重新计算营养",
            ),
        ],
        parent_trace_id=action.source_trace_id,
        response_message="已更新待确认记餐预览，等待用户确认。",
        session_summary="用户手动修改待确认记餐提案",
    )
    store.agent_traces[trace.id] = trace
    store.persist()
    return {"data": action}


def require_action_revision(action: AIAction, expected_revision: int | None) -> None:
    if expected_revision is None or expected_revision == action.revision:
        return
    raise HTTPException(status_code=409, detail={
        "code": "AI_ACTION_REVISION_CONFLICT",
        "message": "这条待确认动作已在其他消息中更新，请按最新内容操作",
        "current_action": action.model_dump(mode="json"),
    })


@app.post("/api/v1/ai/actions/{action_id}/confirm")
@atomic_store_write
def confirm_ai_action(
    action_id: UUID,
    payload: AIActionTransitionRequest | None = None,
    user_id: UUID = Depends(require_user),
):
    profile = get_profile(user_id)
    action = store.ai_actions.get(action_id)
    if not action or action.profile_id != profile.id:
        raise HTTPException(status_code=404, detail={"code": "AI_ACTION_NOT_FOUND", "message": "AI动作不存在"})
    require_action_revision(action, payload.expected_revision if payload else None)
    if action.status == "cancelled":
        raise HTTPException(status_code=409, detail={"code": "AI_ACTION_CANCELLED", "message": "该动作已取消"})
    if action.status == "confirmed":
        if payload is not None:
            raise HTTPException(status_code=409, detail={"code": "AI_ACTION_ALREADY_CONFIRMED", "message": "该动作已经确认"})
        meal = store.meals.get(UUID(action.payload["meal_id"])) if action.action_type == "create_meal" else None
        memory = store.agent_memories.get(UUID(action.payload["memory_id"])) if action.action_type == "remember_preference" else None
        return {"data": {"action": action, "meal": meal, "memory": memory, "profile": profile if action.action_type == "update_safety_profile" else None}}
    if action.action_type == "remember_preference":
        existing = next((memory for memory in store.memories_for(profile.id) if memory.category == action.payload["category"] and memory.value == action.payload["value"]), None)
        memory = existing or AgentMemory(
            profile_id=profile.id,
            category=action.payload["category"],
            value=action.payload["value"],
            source_message=action.payload["source_message"],
        )
        store.agent_memories[memory.id] = memory
        memory_index.upsert_preference(memory)
        action.status = "confirmed"
        action.revision += 1
        action.payload["memory_id"] = str(memory.id)
        store.persist()
        return {"data": {"action": action, "meal": None, "memory": memory}}
    if action.action_type == "update_safety_profile":
        allowed_fields = {"allergies", "hard_exclusions", "food_intolerances", "digestive_sensitivities"}
        updates = action.payload.get("updates") or [{
            "profile_field": action.payload.get("profile_field"),
            "value": action.payload.get("value"),
            "operation": action.payload.get("operation", "add"),
        }]
        normalized_updates = [
            (item.get("profile_field"), str(item.get("value", "")).strip(), item.get("operation", "add"))
            for item in updates
            if isinstance(item, dict)
        ]
        if not normalized_updates or any(field not in allowed_fields or not value or operation != "add" for field, value, operation in normalized_updates):
            raise HTTPException(status_code=400, detail={"code": "INVALID_SAFETY_PROFILE_ACTION", "message": "安全档案更新内容无效"})
        for field, value, _ in normalized_updates:
            current_values = list(getattr(profile, field))
            if value not in current_values:
                current_values.append(value)
                setattr(profile, field, current_values)
        profile.safety_profile_reviewed = True
        action.status = "confirmed"
        action.revision += 1
        store.persist()
        return {"data": {"action": action, "meal": None, "memory": None, "profile": profile}}
    preview_names = [str(item.get("name", "")) for item in action.payload.get("items", [])]
    preview_risks = detect_profile_food_risks(
        profile,
        preview_names,
        mode="record",
        food_allergens=action_allergen_context(action),
    )
    if preview_risks:
        action.payload["safety_risks"] = preview_risks
        action.payload["safety_acknowledged_at"] = datetime.now(timezone.utc).isoformat()
    materialize_confirmed_online_foods(action, profile.id)
    goal = active_goal_or_error(profile.id)
    meal = build_meal_from_foods(
        profile,
        goal,
        action.payload["meal_type"],
        datetime.fromisoformat(action.payload["eaten_at"].replace("Z", "+00:00")) if action.payload.get("eaten_at") else datetime.now(timezone.utc),
        action.payload["items"],
        "ai_confirmed",
        action.payload.get("body_feedback"),
    )
    action.status = "confirmed"
    action.revision += 1
    action.payload["meal_id"] = str(meal.id)
    store.persist()
    if meal.body_feedback:
        memory_index.upsert_feedback(profile, meal)
    return {"data": {"action": action, "meal": meal, "memory": None}}


@app.post("/api/v1/ai/actions/{action_id}/cancel")
def cancel_ai_action(
    action_id: UUID,
    payload: AIActionTransitionRequest | None = None,
    user_id: UUID = Depends(require_user),
):
    profile = get_profile(user_id)
    action = store.ai_actions.get(action_id)
    if not action or action.profile_id != profile.id:
        raise HTTPException(status_code=404, detail={"code": "AI_ACTION_NOT_FOUND", "message": "AI动作不存在"})
    require_action_revision(action, payload.expected_revision if payload else None)
    if action.status != "proposed":
        code = "AI_ACTION_ALREADY_CONFIRMED" if action.status == "confirmed" else "AI_ACTION_CANCELLED"
        raise HTTPException(status_code=409, detail={"code": code, "message": "该动作已结束，不能再次取消"})
    action.status = "cancelled"
    action.revision += 1
    store.persist()
    return {"data": action}


@app.post("/api/v1/weights")
def record_weight(payload: WeightRecordCreate, user_id: UUID = Depends(require_user)):
    profile = get_profile(user_id)
    store.weights.setdefault(profile.id, []).append(payload.model_dump())
    profile.current_weight_kg = payload.weight_kg
    store.persist()
    return {"data": payload}


@app.get("/api/v1/weights/trend")
def weight_trend(user_id: UUID = Depends(require_user)):
    profile = get_profile(user_id)
    return {"data": store.weights.get(profile.id, [])}

