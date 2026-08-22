from __future__ import annotations

from datetime import datetime, timezone
from functools import wraps
import time
from pathlib import Path
from typing import Any
from uuid import UUID

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[2] / ".env", override=False)

from .food_search import search_catalog
from .agent import answer_food_question, answer_general_diet_question, build_agent_context, portion_clarification_options
from .agent_tools import AgentToolRegistry
from .consumption_advice import build_consumption_advice
from .deepseek_agent import build_deepseek_agent_from_env, build_deepseek_intent_from_env
from .intent_guard import validate_action_intent
from .intent_provider import build_openai_assistant_from_env
from .models import (
    AgentFeedback,
    AgentFeedbackRequest,
    AgentMemory,
    AgentTrace,
    AIAction,
    AIChatRequest,
    AIChatResponse,
    CustomFoodCreateRequest,
    Food,
    Meal,
    MealDraft,
    MealDraftConfirmRequest,
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
    WechatLoginRequest,
    WeightRecordCreate,
)
from .auth import DEV_MODE, create_session, exchange_wechat_code, identity_key, issue_phone_code, logout_session, optional_user, require_user, verify_phone_code
from .openai_agent import AgentProviderError, AgentSettings, build_openai_provider_from_env
from .providers import MockAdviceProvider, MockAssistantProvider, MockVisionProvider, is_safety_message
from .nutrition import build_today, calculate_goal, calculate_meal, scale_nutrition
from .nutrition_sources import OpenFoodFactsProvider
from .risk import detect_risks
from .scoring import score_meal
from .services import business_date
from .store import store

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

vision_provider = MockVisionProvider()
advice_provider = MockAdviceProvider()
assistant_provider = MockAssistantProvider()
packaged_food_provider = OpenFoodFactsProvider()
openai_agent_provider = None
openai_intent_provider = None
deepseek_agent_provider = None
deepseek_intent_provider = None
ai_provider_init_error: str | None = None
try:
    openai_intent_provider = build_openai_assistant_from_env()
    openai_agent_provider = build_openai_provider_from_env()
    deepseek_intent_provider = build_deepseek_intent_from_env()
    deepseek_agent_provider = build_deepseek_agent_from_env()
except Exception:
    ai_provider_init_error = "PROVIDER_INIT_ERROR: AI Provider 初始化失败"


def traced_ai_response(
    profile_id,
    message,
    intent,
    context,
    response: AIChatResponse,
    *,
    structured_intent: IntentInterpretation | None = None,
    final_action_allowed: bool | None = None,
):
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
    )
    response.trace_id = trace.id
    response.context = context
    if response.action:
        response.action.source_trace_id = trace.id
        store.ai_actions[response.action.id] = response.action
    store.agent_traces[trace.id] = trace
    store.persist()
    return {"data": response}


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
    source_priority = {"user_confirmed_label": 3, "open_food_facts": 2}
    for food in visible:
        if not food.barcode:
            continue
        current = preferred_by_barcode.get(food.barcode)
        if current is None or source_priority.get(food.source, 1) >= source_priority.get(current.source, 1):
            preferred_by_barcode[food.barcode] = food
    return [food for food in visible if not food.barcode or preferred_by_barcode.get(food.barcode) is food]


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
) -> Meal:
    meal_items: list[MealItem] = []
    for selected in selections:
        food_id = UUID(str(selected["food_id"]))
        food = next((item for item in store.foods if item.id == food_id), None)
        if food is None:
            raise HTTPException(status_code=400, detail={"code": "FOOD_NOT_FOUND", "message": "食物不存在"})
        weight_g = float(selected["weight_g"])
        consumed_ratio = float(selected.get("consumed_ratio", 1))
        meal_items.append(
            MealItem(
                food_id=food.id,
                name=food.name,
                weight_g=weight_g,
                consumed_ratio=consumed_ratio,
                nutrition=scale_nutrition(food.nutrition_per_100g, weight_g, consumed_ratio),
                user_modified=False,
            )
        )
    nutrition = calculate_meal(meal_items)
    names = [item.name for item in meal_items]
    meal = Meal(
        profile_id=profile.id,
        meal_type=meal_type,
        eaten_at=eaten_at,
        record_source=record_source,
        items=meal_items,
        nutrition=nutrition,
        score=score_meal(nutrition, goal.target, names),
        risks=detect_risks(profile, names, nutrition),
        confidence="high",
    )
    store.meals[meal.id] = meal
    return meal


@app.get("/health")
def health():
    return {"status": "ok", "service": "diet-api", "storage": store.backend_name}


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


@app.post("/api/v1/meal-drafts")
def create_meal_draft(payload: dict[str, Any], user_id: UUID = Depends(require_user)):
    profile = get_profile(user_id)
    items = vision_provider.analyze_meal(payload.get("assets", []), store.foods)
    draft = MealDraft(profile_id=profile.id, meal_type=payload.get("meal_type", "lunch"), items=items, assets=payload.get("assets", []))
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
    nutrition = calculate_meal(meal_items)
    names = [item.name for item in meal_items]
    risks = detect_risks(profile, names, nutrition)
    score = score_meal(nutrition, goal.target, names)
    meal = Meal(
        profile_id=profile.id,
        meal_type=draft.meal_type,
        eaten_at=draft.eaten_at,
        record_source="photo_mock",
        items=meal_items,
        nutrition=nutrition,
        score=score,
        risks=risks,
        confidence=draft.confidence,
    )
    store.meals[meal.id] = meal
    draft.draft_status = "confirmed"
    draft.confirmed_meal_id = meal.id
    store.persist()
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
    meal = build_meal_from_foods(profile, goal, payload.meal_type, payload.eaten_at, selections, "manual")
    store.persist()
    return {"data": meal}


@app.get("/api/v1/today")
def today(user_id: UUID = Depends(require_user)):
    profile = get_profile(user_id)
    goal = store.active_goals.get(profile.id)
    target = goal.target if goal else Nutrition()
    return {"data": build_today(profile, store.meals_for(profile.id), target)}


@app.post("/api/v1/meal-plans/preview")
def preview_meal_plans(payload: MealPlanPreviewRequest, user_id: UUID = Depends(require_user)):
    profile = get_profile(user_id)
    goal = active_goal_or_error(profile.id)
    today_data = build_today(profile, store.meals_for(profile.id), goal.target)
    avoidances = [memory.value for memory in store.memories_for(profile.id) if memory.category == "avoidance"]
    options = advice_provider.propose(
        {"remaining": today_data.remaining.model_dump(), "avoidances": [*profile.hard_exclusions, *avoidances]},
        visible_foods(profile.id),
    )
    plans = []
    for option in options:
        items = []
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
        nutrition = calculate_meal(items)
        risks = detect_risks(profile, [item.name for item in items], nutrition)
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
    plan.risks = detect_risks(profile, [item.name for item in plan.items], plan.nutrition)
    store.persist()
    return {"data": plan}


@app.post("/api/v1/meal-plans/{plan_id}/save")
def save_plan(plan_id: UUID, user_id: UUID = Depends(require_user)):
    plan = store.plans.get(plan_id)
    if not plan or plan.profile_id != get_profile(user_id).id:
        raise HTTPException(status_code=404, detail={"code": "PLAN_NOT_FOUND", "message": "计划不存在"})
    if any(risk["level"] == "block" for risk in plan.risks):
        raise HTTPException(status_code=409, detail={"code": "RISK_BLOCKED", "message": "该方案命中硬性风险，不能保存"})
    plan.status = "saved"
    store.persist()
    return {"data": plan}


@app.post("/api/v1/ai/chat")
def ai_chat(payload: AIChatRequest, user_id: UUID = Depends(require_user)):
    profile = get_profile(user_id)
    goal = active_goal_or_error(profile.id)
    today_data = build_today(profile, store.meals_for(profile.id), goal.target)
    memories = store.memories_for(profile.id)
    context = build_agent_context(today_data, memories)
    foods = visible_foods(profile.id)
    settings = AgentSettings.from_env()
    intent_provider = deepseek_intent_provider or openai_intent_provider
    agent_provider = deepseek_agent_provider or openai_agent_provider
    agent_started = time.perf_counter()
    rule_structured = assistant_provider.interpret_structured(payload.message, foods)

    if is_safety_message(payload.message):
        return traced_ai_response(profile.id, payload.message, "safety", context, AIChatResponse(
            kind="safety",
            message="这个问题涉及疾病、用药或特殊生理状态，我不能据此替你调整治疗或给出诊断性饮食方案。请先咨询医生或注册营养师；我可以继续帮你如实记录饮食。",
            basis=["当前助手只提供一般饮食管理支持", "医疗目标和禁忌必须由专业人员确认"],
            suggestions=["帮我记录刚才吃的食物", "查看今天的普通饮食记录"],
            confidence="high",
            decision_stage="safety",
            provider="safety_guard",
        ), structured_intent=rule_structured, final_action_allowed=False)

    fallback_reason: str | None = None
    fallback_tool_calls = []
    structured = rule_structured
    intent_provider_name = "rule"
    intent_model: str | None = None
    intent_latency_ms = 0
    if intent_provider is not None:
        try:
            intent_result = intent_provider.interpret(payload.message)
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

    guard = validate_action_intent(payload.message, structured)
    tools = AgentToolRegistry(
        profile=profile,
        goal=goal,
        foods=foods,
        meals=store.meals_for(profile.id),
        memories=memories,
        advice_provider=advice_provider,
    )

    if settings.provider in {"openai", "deepseek"} and agent_provider is None and fallback_reason is None:
        fallback_reason = ai_provider_init_error or "AI_NOT_CONFIGURED: 智能体 Provider 未初始化"

    if structured.intent == "consumption_advice" and (agent_provider is None or fallback_reason is not None):
        response = build_consumption_advice(payload.message, structured, tools)
        response.provider = f"{intent_provider_name}+rules" if intent_provider_name != "rule" else "rule"
        response.model = intent_model
        response.latency_ms = round((time.perf_counter() - agent_started) * 1000)
        response.fallback_used = fallback_reason is not None
        response.fallback_reason = fallback_reason
        return traced_ai_response(
            profile.id,
            payload.message,
            structured.intent,
            context,
            response,
            structured_intent=structured,
            final_action_allowed=False,
        )

    if structured.intent == "meal_record" and not guard.allowed:
        if structured.foods and structured.temporal_status in {"planned", "hypothetical"}:
            advice_intent = structured.model_copy(update={
                "intent": "consumption_advice",
                "should_create_action": False,
            })
            response = build_consumption_advice(payload.message, advice_intent, tools)
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
        return traced_ai_response(
            profile.id,
            payload.message,
            structured.intent,
            context,
            response,
            structured_intent=structured,
            final_action_allowed=False,
        )

    if agent_provider is not None and fallback_reason is None:
        try:
            result = agent_provider.run(
                payload.message,
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
            action_matches_intent = action is None or (
                action.action_type == "create_meal" and structured.intent == "meal_record"
            ) or (
                action.action_type == "remember_preference" and structured.intent == "memory_preference"
            )
            if action is not None and (not guard.allowed or not action_matches_intent):
                action = None
                intent_conflict = guard.conflict_reason or "模型动作类型与结构化意图不一致"
            return traced_ai_response(profile.id, payload.message, final.goal, context, AIChatResponse(
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
                tool_calls=result.tool_calls,
                provider=result.provider,
                model=result.model,
                latency_ms=result.latency_ms + intent_latency_ms,
                intent_conflict=intent_conflict,
            ), structured_intent=structured, final_action_allowed=bool(action) if result.action else guard.allowed)
        except AgentProviderError as exc:
            fallback_reason = f"{exc.code}: {exc.message}"
            fallback_tool_calls = exc.tool_calls
        except Exception:
            fallback_reason = "PROVIDER_ERROR: 模型服务暂时不可用"

    interpretation = assistant_provider.interpret(payload.message, foods)
    intent = interpretation["intent"]

    def rule_response(response: AIChatResponse):
        response.provider = "rule"
        response.model = getattr(agent_provider, "model", settings.model) if fallback_reason else None
        response.fallback_used = fallback_reason is not None
        response.fallback_reason = fallback_reason
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
        return traced_ai_response(
            profile.id,
            payload.message,
            intent,
            context,
            response,
            structured_intent=structured,
            final_action_allowed=bool(response.action) if response.action else guard.allowed,
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
        return rule_response(build_consumption_advice(payload.message, structured, tools))

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
            payload={"category": category, "value": value, "source_message": payload.message},
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
        answer = answer_food_question(payload.message, interpretation["items"])
        return rule_response(AIChatResponse(
            kind="food_nutrition",
            message=answer["message"],
            basis=answer["basis"],
            suggestions=["米饭和馒头哪个热量高？", "怎么看营养成分表？"],
            confidence=answer["confidence"],
            decision_stage="inform",
        ))

    if intent == "dietary_knowledge":
        answer = answer_food_question(payload.message, interpretation["items"]) if interpretation["items"] else answer_general_diet_question(payload.message)
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
        low_confidence_items = [item for item in interpretation["items"] if item["weight_source"] == "default_estimate"]
        if low_confidence_items:
            names = "、".join(item["food"].name for item in low_confidence_items)
            return rule_response(AIChatResponse(
                kind="clarification",
                message=f"我识别到了食物，但 {names} 的份量没有说清楚。补充克数，或确认按常用份量估算后，我再生成待确认记录。",
                basis=[item["assumption"] for item in low_confidence_items if item["assumption"]],
                suggestions=[],
                confidence="low",
                decision_stage="clarify",
                needs_clarification=True,
                clarification_options=portion_clarification_options(interpretation["items"]),
            ))
        preview_items: list[MealItem] = []
        action_items = []
        for extracted in interpretation["items"]:
            food = extracted["food"]
            weight_g = extracted["weight_g"]
            preview_items.append(MealItem(
                food_id=food.id,
                name=food.name,
                weight_g=weight_g,
                nutrition=scale_nutrition(food.nutrition_per_100g, weight_g),
            ))
            action_items.append({"food_id": str(food.id), "name": food.name, "weight_g": weight_g, "consumed_ratio": 1})
        preview_nutrition = calculate_meal(preview_items)
        item_summary = "、".join(f"{item['name']} {round(item['weight_g'])}g" for item in action_items)
        action = AIAction(
            profile_id=profile.id,
            title="确认这次饮食记录",
            summary=item_summary,
            payload={"meal_type": interpretation["meal_type"], "items": action_items},
            preview_nutrition=preview_nutrition,
            confidence=interpretation["confidence"],
            assumptions=[item["assumption"] for item in interpretation["items"] if item["assumption"]],
        )
        portion_basis = "份量来自你的明确描述" if not action.assumptions else "家庭单位已换算为克数，并明确列出假设"
        return rule_response(AIChatResponse(
            kind="meal_record_proposal",
            message="我识别出了下面这些食物和份量。它们目前只是待确认提案，确认后才会计入今日数据。",
            basis=[portion_basis, "最终营养由后端规则计算"],
            suggestions=["今天还能吃什么？"],
            action=action,
            confidence=interpretation["confidence"],
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
    today_data = build_today(profile, store.meals_for(profile.id), goal.target)
    return {"data": build_agent_context(today_data, store.memories_for(profile.id))}


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


@app.post("/api/v1/ai/actions/{action_id}/confirm")
@atomic_store_write
def confirm_ai_action(action_id: UUID, user_id: UUID = Depends(require_user)):
    profile = get_profile(user_id)
    action = store.ai_actions.get(action_id)
    if not action or action.profile_id != profile.id:
        raise HTTPException(status_code=404, detail={"code": "AI_ACTION_NOT_FOUND", "message": "AI动作不存在"})
    if action.status == "cancelled":
        raise HTTPException(status_code=409, detail={"code": "AI_ACTION_CANCELLED", "message": "该动作已取消"})
    if action.status == "confirmed":
        meal = store.meals.get(UUID(action.payload["meal_id"])) if action.action_type == "create_meal" else None
        memory = store.agent_memories.get(UUID(action.payload["memory_id"])) if action.action_type == "remember_preference" else None
        return {"data": {"action": action, "meal": meal, "memory": memory}}
    if action.action_type == "remember_preference":
        existing = next((memory for memory in store.memories_for(profile.id) if memory.category == action.payload["category"] and memory.value == action.payload["value"]), None)
        memory = existing or AgentMemory(
            profile_id=profile.id,
            category=action.payload["category"],
            value=action.payload["value"],
            source_message=action.payload["source_message"],
        )
        store.agent_memories[memory.id] = memory
        action.status = "confirmed"
        action.payload["memory_id"] = str(memory.id)
        store.persist()
        return {"data": {"action": action, "meal": None, "memory": memory}}
    goal = active_goal_or_error(profile.id)
    meal = build_meal_from_foods(
        profile,
        goal,
        action.payload["meal_type"],
        datetime.now(timezone.utc),
        action.payload["items"],
        "ai_confirmed",
    )
    action.status = "confirmed"
    action.payload["meal_id"] = str(meal.id)
    store.persist()
    return {"data": {"action": action, "meal": meal, "memory": None}}


@app.post("/api/v1/ai/actions/{action_id}/cancel")
def cancel_ai_action(action_id: UUID, user_id: UUID = Depends(require_user)):
    profile = get_profile(user_id)
    action = store.ai_actions.get(action_id)
    if not action or action.profile_id != profile.id:
        raise HTTPException(status_code=404, detail={"code": "AI_ACTION_NOT_FOUND", "message": "AI动作不存在"})
    if action.status == "confirmed":
        raise HTTPException(status_code=409, detail={"code": "AI_ACTION_ALREADY_CONFIRMED", "message": "该动作已经确认"})
    action.status = "cancelled"
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

