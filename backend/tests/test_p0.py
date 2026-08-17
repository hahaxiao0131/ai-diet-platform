import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

os.environ["DIET_DISABLE_PERSISTENCE"] = "1"

from fastapi.testclient import TestClient

import app.main as main_module
from app.main import app
from app.models import Food, Meal, MealItem, Nutrition
from app.services import calculate_goal
from app.store import MemoryStore


client = TestClient(app)


def test_local_store_restores_profile_goal_custom_food_and_meal(request):
    path = Path(__file__).parent / f".tmp_store_{uuid4().hex}.json"
    request.addfinalizer(lambda: path.unlink(missing_ok=True))
    request.addfinalizer(lambda: path.with_suffix(path.suffix + ".bak").unlink(missing_ok=True))
    first = MemoryStore(path)
    user_id = first.get_or_create_user("persistent-user")
    profile = first.get_profile(user_id)
    profile.sex = "female"
    profile.age = 31
    profile.height_cm = 166
    profile.current_weight_kg = 61
    profile.activity_level = "light"
    profile.primary_goal = "maintain"
    profile.goal_pace = "standard"
    profile.onboarding_completed = True
    goal = calculate_goal(profile)
    goal.status = "active"
    first.goal_proposals[goal.id] = goal
    first.active_goals[profile.id] = goal

    custom_food = Food(
        name="持久化测试食物",
        food_type="custom",
        source="user_nutrition_label",
        default_weight_g=120,
        nutrition_per_100g=Nutrition(energy_kcal=100, protein_g=10),
    )
    first.foods.append(custom_food)
    meal_item = MealItem(
        food_id=custom_food.id,
        name=custom_food.name,
        weight_g=120,
        nutrition=Nutrition(energy_kcal=120, protein_g=12),
    )
    meal = Meal(
        profile_id=profile.id,
        meal_type="lunch",
        eaten_at=datetime.now(timezone.utc),
        record_source="manual",
        items=[meal_item],
        nutrition=meal_item.nutrition,
        confidence="high",
    )
    first.meals[meal.id] = meal
    first.persist()

    restored = MemoryStore(path)
    assert restored.users["persistent-user"] == user_id
    assert restored.get_profile(user_id).onboarding_completed is True
    assert restored.active_goals[profile.id].id == goal.id
    assert any(food.id == custom_food.id for food in restored.foods)
    assert restored.meals[meal.id].nutrition.energy_kcal == 120


def setup_user():
    login = client.post("/api/v1/auth/mock-login", json={"mock_user_key": "test-user"})
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


def setup_ready_user(key: str):
    login = client.post("/api/v1/auth/mock-login", json={"mock_user_key": key})
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    profile = {
        "sex": "female",
        "age": 30,
        "height_cm": 165,
        "current_weight_kg": 60,
        "activity_level": "light",
        "primary_goal": "structure",
        "goal_pace": "standard",
    }
    assert client.put("/api/v1/profiles/me", json=profile, headers=headers).status_code == 200
    proposal_id = client.post("/api/v1/goals/recalculate", headers=headers).json()["data"]["id"]
    assert client.post(f"/api/v1/goals/{proposal_id}/confirm", headers=headers).status_code == 200
    return headers


def test_p0_onboarding_goal_and_meal_flow():
    headers = setup_user()
    profile = {
        "sex": "male",
        "age": 28,
        "height_cm": 175,
        "current_weight_kg": 72,
        "activity_level": "moderate",
        "primary_goal": "fat_loss",
        "goal_pace": "standard",
    }
    response = client.put("/api/v1/profiles/me", json=profile, headers=headers)
    assert response.status_code == 200
    proposal = client.post("/api/v1/goals/recalculate", headers=headers)
    assert proposal.status_code == 200
    proposal_id = proposal.json()["data"]["id"]
    assert client.post(f"/api/v1/goals/{proposal_id}/confirm", headers=headers).status_code == 200

    draft = client.post(
        "/api/v1/meal-drafts",
        json={"meal_type": "lunch", "assets": [{"type": "image", "storage_key": "demo.jpg"}]},
        headers=headers,
    )
    assert draft.status_code == 200
    draft_data = draft.json()["data"]
    items = draft_data["items"]
    confirmed = client.post(
        f"/api/v1/meal-drafts/{draft_data['id']}/confirm",
        json={
            "items": [
                {
                    "item_id": item["id"],
                    "food_id": item["food_id"],
                    "weight_g": item["estimated_weight_g"],
                    "consumed_ratio": 1,
                }
                for item in items
            ]
        },
        headers=headers,
    )
    assert confirmed.status_code == 200
    meal = confirmed.json()["data"]
    assert meal["nutrition"]["energy_kcal"] > 0
    assert "score" in meal

    today = client.get("/api/v1/today", headers=headers)
    assert today.status_code == 200
    assert today.json()["data"]["completeness"]["recorded_meals"] == 1

    plans = client.post("/api/v1/meal-plans/preview", json={"meal_type": "dinner"}, headers=headers)
    assert plans.status_code == 200
    assert len(plans.json()["data"]["plans"]) == 3


def test_registration_requires_profile_and_goal_before_onboarding_is_complete():
    login = client.post("/api/v1/auth/mock-login", json={"mock_user_key": f"onboarding-{uuid4()}"})
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    assert login.json()["user"]["is_new"] is True

    incomplete = client.put(
        "/api/v1/profiles/me",
        json={"sex": "female", "age": 29},
        headers=headers,
    )
    assert incomplete.status_code == 200
    assert incomplete.json()["data"]["onboarding_completed"] is False
    blocked = client.post("/api/v1/goals/recalculate", headers=headers)
    assert blocked.status_code == 409
    assert blocked.json()["detail"]["code"] == "PROFILE_INCOMPLETE"

    completed_fields = client.put(
        "/api/v1/profiles/me",
        json={
            "height_cm": 165,
            "current_weight_kg": 58,
            "activity_level": "moderate",
            "primary_goal": "maintain",
            "goal_pace": "standard",
        },
        headers=headers,
    )
    assert completed_fields.json()["data"]["onboarding_completed"] is False
    proposal_id = client.post("/api/v1/goals/recalculate", headers=headers).json()["data"]["id"]
    assert client.post(f"/api/v1/goals/{proposal_id}/confirm", headers=headers).status_code == 200
    assert client.get("/api/v1/profiles/me", headers=headers).json()["data"]["onboarding_completed"] is True
    assert client.get("/api/v1/auth/session", headers=headers).json()["data"]["user"]["is_new"] is False


def test_manual_food_search_and_add_meal():
    headers = setup_user()
    profile = {
        "sex": "female",
        "age": 30,
        "height_cm": 165,
        "current_weight_kg": 60,
        "activity_level": "light",
        "primary_goal": "structure",
        "goal_pace": "standard",
    }
    assert client.put("/api/v1/profiles/me", json=profile, headers=headers).status_code == 200
    proposal_id = client.post("/api/v1/goals/recalculate", headers=headers).json()["data"]["id"]
    assert client.post(f"/api/v1/goals/{proposal_id}/confirm", headers=headers).status_code == 200

    foods = client.get("/api/v1/foods/search?q=鸡蛋", headers=headers)
    assert foods.status_code == 200
    egg = foods.json()["data"][0]
    meal = client.post(
        "/api/v1/meals/manual",
        json={
            "meal_type": "breakfast",
            "items": [{"food_id": egg["id"], "weight_g": 100, "consumed_ratio": 1}],
        },
        headers=headers,
    )
    assert meal.status_code == 200
    data = meal.json()["data"]
    assert data["record_source"] == "manual"
    assert data["nutrition"]["energy_kcal"] > 0
    assert data["score"]["score"] > 0


def test_common_foods_support_alias_category_and_default_portion_search():
    common = client.get("/api/v1/foods/search?q=")
    assert common.status_code == 200
    assert len(common.json()["data"]) == 10

    alias = client.get("/api/v1/foods/search?q=西红柿")
    assert alias.status_code == 200
    assert alias.json()["data"][0]["name"] == "番茄"
    assert alias.json()["data"][0]["default_weight_g"] == 150

    vegetables = client.get("/api/v1/foods/search?q=蔬菜")
    vegetable_names = {food["name"] for food in vegetables.json()["data"]}
    assert {"番茄", "西兰花", "黄瓜", "菠菜"}.issubset(vegetable_names)

    protein = client.get("/api/v1/foods/search?q=肉蛋")
    protein_names = {food["name"] for food in protein.json()["data"]}
    assert {"鸡蛋", "鸡胸肉", "牛肉", "虾仁"}.issubset(protein_names)

    searchable_examples = {
        "金针菇": "金针菇",
        "花甲": "蛤蜊",
        "火龙果": "火龙果",
        "腰果": "腰果",
        "生抽": "酱油",
        "饺子": "水饺",
        "三纹鱼": "三文鱼",
    }
    for query, expected_name in searchable_examples.items():
        response = client.get(f"/api/v1/foods/search?q={query}")
        assert response.status_code == 200
        assert response.json()["data"][0]["name"] == expected_name


def test_create_custom_food_from_nutrition_label_then_add_meal():
    headers = setup_user()
    profile = {
        "sex": "male",
        "age": 32,
        "height_cm": 176,
        "current_weight_kg": 76,
        "activity_level": "light",
        "primary_goal": "maintain",
        "goal_pace": "standard",
    }
    assert client.put("/api/v1/profiles/me", json=profile, headers=headers).status_code == 200
    proposal_id = client.post("/api/v1/goals/recalculate", headers=headers).json()["data"]["id"]
    assert client.post(f"/api/v1/goals/{proposal_id}/confirm", headers=headers).status_code == 200

    custom = client.post(
        "/api/v1/foods/custom",
        json={
            "name": "自定义酸奶",
            "basis_weight_g": 100,
            "default_weight_g": 180,
            "nutrition": {
                "energy_kcal": 88,
                "protein_g": 3.2,
                "fat_g": 2.8,
                "carbs_g": 12,
                "fiber_g": 0,
                "sodium_mg": 55,
                "added_sugar_g": 8,
                "vegetable_g": 0,
                "fruit_g": 0,
            },
        },
        headers=headers,
    )
    assert custom.status_code == 200
    food = custom.json()["data"]
    assert food["food_type"] == "custom"

    meal = client.post(
        "/api/v1/meals/manual",
        json={
            "meal_type": "snack",
            "items": [{"food_id": food["id"], "weight_g": 180, "consumed_ratio": 1}],
        },
        headers=headers,
    )
    assert meal.status_code == 200
    data = meal.json()["data"]
    assert data["items"][0]["name"] == "自定义酸奶"
    assert round(data["nutrition"]["energy_kcal"]) == 158


def test_ai_assistant_explains_and_requires_confirmation_before_recording():
    login = client.post("/api/v1/auth/mock-login", json={"mock_user_key": "ai-assistant-user"})
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    profile = {
        "sex": "female",
        "age": 29,
        "height_cm": 164,
        "current_weight_kg": 58,
        "activity_level": "moderate",
        "primary_goal": "structure",
        "goal_pace": "standard",
    }
    assert client.put("/api/v1/profiles/me", json=profile, headers=headers).status_code == 200
    proposal_id = client.post("/api/v1/goals/recalculate", headers=headers).json()["data"]["id"]
    assert client.post(f"/api/v1/goals/{proposal_id}/confirm", headers=headers).status_code == 200

    explanation = client.post("/api/v1/ai/chat", json={"message": "为什么蛋白质不足？"}, headers=headers)
    assert explanation.status_code == 200
    assert explanation.json()["data"]["kind"] == "explanation"
    assert explanation.json()["data"]["action"] is None

    proposal = client.post(
        "/api/v1/ai/chat",
        json={"message": "午餐吃了150克米饭和两个鸡蛋"},
        headers=headers,
    )
    assert proposal.status_code == 200
    response = proposal.json()["data"]
    assert response["kind"] == "meal_record_proposal"
    assert response["action"]["status"] == "proposed"
    assert [item["weight_g"] for item in response["action"]["payload"]["items"]] == [150, 100]
    assert client.get("/api/v1/today", headers=headers).json()["data"]["completeness"]["recorded_meals"] == 0

    action_id = response["action"]["id"]
    confirmed = client.post(f"/api/v1/ai/actions/{action_id}/confirm", headers=headers)
    assert confirmed.status_code == 200
    assert confirmed.json()["data"]["meal"]["record_source"] == "ai_confirmed"
    assert client.get("/api/v1/today", headers=headers).json()["data"]["completeness"]["recorded_meals"] == 1

    repeated = client.post(f"/api/v1/ai/actions/{action_id}/confirm", headers=headers)
    assert repeated.status_code == 200
    assert client.get("/api/v1/today", headers=headers).json()["data"]["completeness"]["recorded_meals"] == 1


def test_ai_assistant_routes_plan_and_replacement_requests():
    login = client.post("/api/v1/auth/mock-login", json={"mock_user_key": "ai-plan-user"})
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    profile = {
        "sex": "male",
        "age": 34,
        "height_cm": 178,
        "current_weight_kg": 75,
        "activity_level": "light",
        "primary_goal": "maintain",
        "goal_pace": "standard",
    }
    client.put("/api/v1/profiles/me", json=profile, headers=headers)
    proposal_id = client.post("/api/v1/goals/recalculate", headers=headers).json()["data"]["id"]
    client.post(f"/api/v1/goals/{proposal_id}/confirm", headers=headers)

    plan = client.post("/api/v1/ai/chat", json={"message": "今天还能吃什么？"}, headers=headers)
    assert plan.json()["data"]["kind"] == "plan_recommendation"
    assert plan.json()["data"]["cta"] == "preview_plans"

    replacement = client.post("/api/v1/ai/chat", json={"message": "鸡胸肉能换什么？"}, headers=headers)
    assert replacement.json()["data"]["kind"] == "food_replacement"
    assert "虾仁" in replacement.json()["data"]["message"]


def test_ai_agent_clarifies_ambiguous_portions_before_proposing_a_record():
    headers = setup_ready_user(f"agent-clarify-{uuid4().hex}")

    vague = client.post(
        "/api/v1/ai/chat",
        json={"message": "午餐吃了米饭和鸡蛋"},
        headers=headers,
    )
    assert vague.status_code == 200
    response = vague.json()["data"]
    assert response["kind"] == "clarification"
    assert response["needs_clarification"] is True
    assert response["confidence"] == "low"
    assert response["action"] is None
    assert response["trace_id"]
    assert response["clarification_options"]
    assert client.get("/api/v1/today", headers=headers).json()["data"]["completeness"]["recorded_meals"] == 0

    clarified = client.post(
        "/api/v1/ai/chat",
        json={"message": response["clarification_options"][0]},
        headers=headers,
    ).json()["data"]
    assert clarified["kind"] == "meal_record_proposal"
    assert clarified["action"]["status"] == "proposed"
    assert clarified["action"]["source_trace_id"] == clarified["trace_id"]
    assert client.get("/api/v1/today", headers=headers).json()["data"]["completeness"]["recorded_meals"] == 0


def test_ai_agent_memory_requires_confirmation_and_can_be_deleted():
    headers = setup_ready_user(f"agent-memory-{uuid4().hex}")

    proposal = client.post(
        "/api/v1/ai/chat",
        json={"message": "我不喜欢鸡胸肉"},
        headers=headers,
    ).json()["data"]
    assert proposal["kind"] == "memory_proposal"
    assert proposal["action"]["action_type"] == "remember_preference"
    assert client.get("/api/v1/ai/memories", headers=headers).json()["data"] == []

    confirmed = client.post(
        f"/api/v1/ai/actions/{proposal['action']['id']}/confirm",
        headers=headers,
    )
    assert confirmed.status_code == 200
    memory = confirmed.json()["data"]["memory"]
    assert memory["category"] == "avoidance"
    assert memory["value"] == "鸡胸肉"
    assert len(client.get("/api/v1/ai/memories", headers=headers).json()["data"]) == 1

    replacement = client.post(
        "/api/v1/ai/chat",
        json={"message": "虾仁能换什么？"},
        headers=headers,
    ).json()["data"]
    assert "鸡胸肉" not in replacement["message"]

    plans = client.post(
        "/api/v1/meal-plans/preview",
        json={"meal_type": "dinner", "scenario": "quick"},
        headers=headers,
    ).json()["data"]["plans"]
    assert all(item["name"] != "鸡胸肉" for plan in plans for item in plan["items"])
    assert any("已按你的偏好避开鸡胸肉" in plan["reason"] for plan in plans)

    deleted = client.delete(f"/api/v1/ai/memories/{memory['id']}", headers=headers)
    assert deleted.status_code == 200
    assert client.get("/api/v1/ai/memories", headers=headers).json()["data"] == []


def test_ai_agent_feedback_is_traceable_and_safety_questions_do_not_create_actions():
    owner = setup_ready_user(f"agent-feedback-{uuid4().hex}")
    other = setup_ready_user(f"agent-feedback-other-{uuid4().hex}")

    answer = client.post(
        "/api/v1/ai/chat",
        json={"message": "为什么蛋白质不足？"},
        headers=owner,
    ).json()["data"]
    feedback = client.post(
        f"/api/v1/ai/traces/{answer['trace_id']}/feedback",
        json={"rating": "helpful"},
        headers=owner,
    )
    assert feedback.status_code == 200
    assert feedback.json()["data"]["rating"] == "helpful"
    assert client.post(
        f"/api/v1/ai/traces/{answer['trace_id']}/feedback",
        json={"rating": "not_helpful"},
        headers=other,
    ).status_code == 404

    safety = client.post(
        "/api/v1/ai/chat",
        json={"message": "我有糖尿病，应该怎么调整药物和饮食？"},
        headers=owner,
    ).json()["data"]
    assert safety["kind"] == "safety"
    assert safety["decision_stage"] == "safety"
    assert safety["action"] is None


def test_ai_agent_answers_food_nutrition_and_general_diet_questions():
    headers = setup_ready_user(f"agent-diet-qa-{uuid4().hex}")

    noodles = client.post(
        "/api/v1/ai/chat",
        json={"message": "200g挂面的热量是多少？"},
        headers=headers,
    ).json()["data"]
    assert noodles["kind"] == "food_nutrition"
    assert "696 kcal" in noodles["message"]
    assert any("干重" in item for item in noodles["basis"])
    assert noodles["action"] is None

    comparison = client.post(
        "/api/v1/ai/chat",
        json={"message": "米饭和馒头哪个热量高？"},
        headers=headers,
    ).json()["data"]
    assert comparison["kind"] == "food_nutrition"
    assert "馒头" in comparison["message"]
    assert "更高" in comparison["message"]

    general = client.post(
        "/api/v1/ai/chat",
        json={"message": "减脂可以吃主食吗？"},
        headers=headers,
    ).json()["data"]
    assert general["kind"] == "dietary_knowledge"
    assert "不需要完全戒掉主食" in general["message"]

    cherry_tomatoes = client.post(
        "/api/v1/ai/chat",
        json={"message": "十颗圣女果的热量是多少？"},
        headers=headers,
    ).json()["data"]
    assert cherry_tomatoes["kind"] == "food_nutrition"
    assert "150g圣女果约含 33 kcal" in cherry_tomatoes["message"]
    assert any("每颗约 15g" in item and "本次约 150g" in item for item in cherry_tomatoes["basis"])

    twelve_cherry_tomatoes = client.post(
        "/api/v1/ai/chat",
        json={"message": "十二颗小番茄有多少热量？"},
        headers=headers,
    ).json()["data"]
    assert "180g圣女果约含 40 kcal" in twelve_cherry_tomatoes["message"]

    gummy_sugar = client.post(
        "/api/v1/ai/chat",
        json={"message": "三颗QQ糖的糖分是多少？"},
        headers=headers,
    ).json()["data"]
    assert gummy_sugar["kind"] == "food_nutrition"
    assert gummy_sugar["confidence"] == "low"
    assert "无法仅凭当前标签准确算出糖分" in gummy_sugar["message"]
    assert "9g旺仔QQ糖约含 6.2g 碳水" in gummy_sugar["message"]
    assert any("碳水化合物不能等同于糖" in item for item in gummy_sugar["basis"])

    gummy_record = client.post(
        "/api/v1/ai/chat",
        json={"message": "我吃了三颗QQ糖"},
        headers=headers,
    ).json()["data"]
    assert gummy_record["kind"] == "meal_record_proposal"
    assert len(gummy_record["action"]["payload"]["items"]) == 1
    assert gummy_record["action"]["payload"]["items"][0]["name"] == "旺仔QQ糖"
    assert gummy_record["action"]["payload"]["items"][0]["weight_g"] == 9
    assert gummy_record["action"]["confidence"] == "low"


def test_barcode_source_is_cached_and_user_label_has_priority(monkeypatch):
    barcode = "6901234567892"

    class FakePackagedFoodProvider:
        def __init__(self):
            self.calls = 0

        def lookup(self, requested_barcode: str):
            self.calls += 1
            assert requested_barcode == barcode
            return Food(
                name="测试果汁软糖",
                food_type="packaged",
                default_weight_g=20,
                source="open_food_facts",
                source_version="api-v3",
                source_url=f"https://world.openfoodfacts.org/product/{barcode}",
                barcode=barcode,
                brand="测试品牌",
                nutrition_per_100g=Nutrition(energy_kcal=320, carbs_g=72, sugars_g=48),
                confidence="medium",
            )

    provider = FakePackagedFoodProvider()
    monkeypatch.setattr(main_module, "packaged_food_provider", provider)
    owner = setup_ready_user(f"barcode-owner-{uuid4().hex}")
    other = setup_ready_user(f"barcode-other-{uuid4().hex}")

    first = client.get(f"/api/v1/foods/barcode/{barcode}", headers=owner)
    second = client.get(f"/api/v1/foods/barcode/{barcode}", headers=owner)
    assert first.status_code == second.status_code == 200
    assert first.json()["data"]["source"] == "open_food_facts"
    assert first.json()["data"]["nutrition_per_100g"]["sugars_g"] == 48
    assert provider.calls == 1
    common_foods = client.get("/api/v1/foods/search", headers=owner).json()["data"]
    assert all(food.get("barcode") != barcode for food in common_foods)
    searched_foods = client.get("/api/v1/foods/search", params={"q": "测试果汁软糖"}, headers=owner).json()["data"]
    assert any(food.get("barcode") == barcode for food in searched_foods)

    label = client.post(
        "/api/v1/foods/label",
        json={
            "name": "我手中的测试软糖",
            "brand": "包装品牌",
            "barcode": barcode,
            "basis_weight_g": 100,
            "default_weight_g": 20,
            "nutrition": {"energy_kcal": 300, "carbs_g": 68, "sugars_g": 42},
        },
        headers=owner,
    )
    assert label.status_code == 200
    assert label.json()["data"]["verified_by_user"] is True
    assert client.get(f"/api/v1/foods/barcode/{barcode}", headers=owner).json()["data"]["source"] == "user_confirmed_label"
    assert client.get(f"/api/v1/foods/barcode/{barcode}", headers=other).json()["data"]["source"] == "open_food_facts"


def test_today_excludes_meals_from_previous_business_dates():
    headers = setup_ready_user(f"date-user-{uuid4().hex}")
    rice = client.get("/api/v1/foods/search?q=米饭", headers=headers).json()["data"][0]
    old_meal = client.post(
        "/api/v1/meals/manual",
        json={
            "meal_type": "dinner",
            "eaten_at": (datetime.now(timezone.utc) - timedelta(days=2)).isoformat(),
            "items": [{"food_id": rice["id"], "weight_g": 150, "consumed_ratio": 1}],
        },
        headers=headers,
    )
    assert old_meal.status_code == 200
    assert client.get("/api/v1/today", headers=headers).json()["data"]["completeness"]["recorded_meals"] == 0

    current_meal = client.post(
        "/api/v1/meals/manual",
        json={
            "meal_type": "lunch",
            "items": [{"food_id": rice["id"], "weight_g": 150, "consumed_ratio": 1}],
        },
        headers=headers,
    )
    assert current_meal.status_code == 200
    today = client.get("/api/v1/today", headers=headers).json()["data"]
    assert today["completeness"]["recorded_meals"] == 1
    assert round(today["consumed"]["energy_kcal"]) == round(current_meal.json()["data"]["nutrition"]["energy_kcal"])


def test_draft_confirmation_is_idempotent():
    headers = setup_ready_user(f"draft-user-{uuid4().hex}")
    draft = client.post(
        "/api/v1/meal-drafts",
        json={"meal_type": "lunch", "assets": [{"type": "image", "storage_key": "demo.jpg"}]},
        headers=headers,
    ).json()["data"]
    payload = {
        "items": [
            {
                "item_id": item["id"],
                "food_id": item["food_id"],
                "weight_g": item["estimated_weight_g"],
                "consumed_ratio": 1,
            }
            for item in draft["items"]
        ]
    }
    first = client.post(f"/api/v1/meal-drafts/{draft['id']}/confirm", json=payload, headers=headers)
    second = client.post(f"/api/v1/meal-drafts/{draft['id']}/confirm", json=payload, headers=headers)
    assert first.status_code == second.status_code == 200
    assert first.json()["data"]["id"] == second.json()["data"]["id"]
    assert len(client.get("/api/v1/meals", headers=headers).json()["data"]) == 1


def test_custom_food_is_visible_only_to_its_owner():
    owner = setup_ready_user(f"food-owner-{uuid4().hex}")
    other = setup_ready_user(f"food-other-{uuid4().hex}")
    name = f"私有酸奶-{uuid4().hex[:8]}"
    created = client.post(
        "/api/v1/foods/custom",
        json={
            "name": name,
            "basis_weight_g": 100,
            "nutrition": {"energy_kcal": 70, "protein_g": 4},
        },
        headers=owner,
    )
    assert created.status_code == 200
    assert client.get(f"/api/v1/foods/search?q={name}", headers=owner).json()["data"][0]["name"] == name
    assert all(food["name"] != name for food in client.get(f"/api/v1/foods/search?q={name}", headers=other).json()["data"])
    assert all(food["name"] != name for food in client.get(f"/api/v1/foods/search?q={name}").json()["data"])


def test_plan_replacement_recalculates_and_rejects_negative_index():
    headers = setup_ready_user(f"plan-user-{uuid4().hex}")
    plans = client.post("/api/v1/meal-plans/preview", json={"meal_type": "dinner"}, headers=headers).json()["data"]["plans"]
    plan = plans[0]
    original_energy = plan["nutrition"]["energy_kcal"]
    replacement = "虾仁" if plan["items"][0]["name"] != "虾仁" else "鸡胸肉"
    updated = client.post(
        f"/api/v1/meal-plans/{plan['id']}/replace",
        json={"index": 0, "food_name": replacement},
        headers=headers,
    )
    assert updated.status_code == 200
    assert updated.json()["data"]["items"][0]["name"] == replacement
    assert updated.json()["data"]["nutrition"]["energy_kcal"] != original_energy
    assert client.post(
        f"/api/v1/meal-plans/{plan['id']}/replace",
        json={"index": -1, "food_name": replacement},
        headers=headers,
    ).status_code == 400


def test_phone_login_session_and_logout():
    phone = f"139{str(int(uuid4().hex[:8], 16))[-8:].zfill(8)}"
    sent = client.post("/api/v1/auth/phone/code", json={"phone": phone})
    assert sent.status_code == 200
    code = sent.json()["data"]["dev_code"]
    repeated = client.post("/api/v1/auth/phone/code", json={"phone": phone})
    assert repeated.status_code == 429
    assert repeated.json()["detail"]["code"] == "PHONE_CODE_TOO_FREQUENT"

    assert client.post("/api/v1/auth/phone/login", json={"phone": phone, "code": "000000"}).status_code == 400
    logged_in = client.post("/api/v1/auth/phone/login", json={"phone": phone, "code": code})
    assert logged_in.status_code == 200
    session = logged_in.json()["data"]
    headers = {"Authorization": f"Bearer {session['access_token']}"}
    assert client.get("/api/v1/auth/session", headers=headers).status_code == 200
    assert client.get("/api/v1/profiles/me", headers=headers).status_code == 200
    assert client.post("/api/v1/auth/logout", headers=headers).status_code == 200
    assert client.get("/api/v1/profiles/me", headers=headers).status_code == 401


def test_wechat_development_login_is_stable_and_unauthorized_requests_are_blocked():
    assert client.get("/api/v1/profiles/me").status_code == 401
    first = client.post("/api/v1/auth/wechat/login", json={"code": "dev-wechat-test-user"})
    second = client.post("/api/v1/auth/wechat/login", json={"code": "dev-wechat-test-user"})
    assert first.status_code == second.status_code == 200
    assert first.json()["data"]["user"]["id"] == second.json()["data"]["user"]["id"]
    assert first.json()["data"]["access_token"] != second.json()["data"]["access_token"]
