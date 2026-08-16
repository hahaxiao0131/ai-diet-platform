from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from .food_search import search_catalog
from .models import (
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
    PhoneCodeRequest,
    PhoneLoginRequest,
    ProfileUpdate,
    WechatLoginRequest,
    WeightRecordCreate,
)
from .auth import DEV_MODE, create_session, exchange_wechat_code, identity_key, issue_phone_code, logout_session, optional_user, require_user, verify_phone_code
from .providers import MockAdviceProvider, MockAssistantProvider, MockVisionProvider
from .nutrition import build_today, calculate_goal, calculate_meal, scale_nutrition
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

vision_provider = MockVisionProvider()
advice_provider = MockAdviceProvider()
assistant_provider = MockAssistantProvider()


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
    return [
        food for food in store.foods
        if food.food_type != "custom" or (profile_tag is not None and profile_tag in food.tags)
    ]


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
    return {"status": "ok", "service": "diet-api"}


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
    return {"data": search_catalog(visible_foods(profile_id), q)}


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


@app.post("/api/v1/foods/custom")
def create_custom_food(payload: CustomFoodCreateRequest, user_id: UUID = Depends(require_user)):
    profile = get_profile(user_id)
    factor = 100 / payload.basis_weight_g
    nutrition_per_100g = Nutrition(
        **{
            key: (value * factor if value is not None else None)
            for key, value in payload.nutrition.model_dump().items()
        }
    )
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
    options = advice_provider.propose({"remaining": today_data.remaining.model_dump()}, store.foods)
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
    interpretation = assistant_provider.interpret(payload.message, store.foods)
    intent = interpretation["intent"]

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
        return {"data": AIChatResponse(
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
        )}

    if intent == "plan_recommendation":
        remaining_energy = max(0, round(today_data.remaining.energy_kcal))
        remaining_protein = max(0, round(today_data.remaining.protein_g))
        caution = f"，同时注意{today_data.near_limits[0]}已接近上限" if today_data.near_limits else ""
        return {"data": AIChatResponse(
            kind="plan_recommendation",
            message=f"下一餐可以优先补约 {remaining_protein}g 的蛋白质缺口{caution}。我会给你家常、快手和便利三种方案，营养值先预览再决定。",
            basis=[f"今日剩余约 {remaining_energy} kcal", f"蛋白质还差约 {remaining_protein}g"],
            suggestions=["为什么蛋白质不足？", "鸡胸肉能换什么？"],
            cta="preview_plans",
        )}

    if intent == "food_replacement":
        found_names = [item["food"].name for item in interpretation["items"]]
        replacement_map = {
            "鸡胸肉": ["虾仁", "豆腐", "鸡蛋"],
            "虾仁": ["鸡胸肉", "豆腐", "鸡蛋"],
            "牛奶": ["鸡蛋", "豆腐"],
            "米饭": ["燕麦", "香蕉"],
        }
        source = found_names[0] if found_names else "当前蛋白质"
        alternatives = replacement_map.get(source, ["虾仁", "豆腐", "鸡蛋"])
        return {"data": AIChatResponse(
            kind="food_replacement",
            message=f"{source}可以优先换成{'、'.join(alternatives)}。替换时需要按实际份量重新计算，不能只按食物名称一比一替换。",
            basis=["替代顺序优先考虑营养接近", "禁忌与过敏优先于口味偏好"],
            suggestions=["帮我安排下一餐", "今天还能吃什么？"],
            cta="preview_plans",
        )}

    if intent == "meal_record":
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
        )
        store.ai_actions[action.id] = action
        store.persist()
        return {"data": AIChatResponse(
            kind="meal_record_proposal",
            message="我识别出了下面这些食物和份量。它们目前只是待确认提案，确认后才会计入今日数据。",
            basis=["份量来自你的描述或常用份量估算", "最终营养由后端规则计算"],
            suggestions=["今天还能吃什么？"],
            action=action,
        )}

    return {"data": AIChatResponse(
        kind="clarification",
        message="我目前最擅长帮你记录一餐、解释今天的数据、安排下一餐和替换食材。你可以直接说“午餐吃了150克米饭和两个鸡蛋”。",
        basis=["V1 只处理饮食管理任务", "医疗问题需要咨询专业人员"],
        suggestions=["今天还能吃什么？", "为什么蛋白质不足？", "午餐吃了150克米饭和两个鸡蛋"],
    )}


@app.post("/api/v1/ai/actions/{action_id}/confirm")
def confirm_ai_action(action_id: UUID, user_id: UUID = Depends(require_user)):
    profile = get_profile(user_id)
    action = store.ai_actions.get(action_id)
    if not action or action.profile_id != profile.id:
        raise HTTPException(status_code=404, detail={"code": "AI_ACTION_NOT_FOUND", "message": "AI动作不存在"})
    if action.status == "cancelled":
        raise HTTPException(status_code=409, detail={"code": "AI_ACTION_CANCELLED", "message": "该动作已取消"})
    if action.status == "confirmed":
        meal = store.meals.get(UUID(action.payload["meal_id"]))
        return {"data": {"action": action, "meal": meal}}
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
    return {"data": {"action": action, "meal": meal}}


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

