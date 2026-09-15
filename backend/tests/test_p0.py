import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID, uuid4

os.environ["DIET_DISABLE_PERSISTENCE"] = "1"

from fastapi.testclient import TestClient

import app.main as main_module
from app.main import app
from app.memory_index import PersonalMemoryIndex
from app.models import AIAction, AgentMemory, AgentTrace, Food, Meal, MealItem, MealPlan, Nutrition, Profile
from app.services import calculate_goal, detect_profile_food_risks
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
        "/api/v1/meal-drafts/example",
        json={"meal_type": "lunch"},
        headers=headers,
    )
    assert draft.status_code == 200
    draft_data = draft.json()["data"]
    assert draft_data["provider"] == "example_template"
    assert draft_data["assets"] == []
    assert all(item["source"] == "example_template" for item in draft_data["items"])
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
    assert meal["record_source"] == "example_draft"
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


def test_pending_ai_meal_can_be_amended_by_follow_up_then_confirmed():
    headers = setup_ready_user(f"amend-pending-meal-{uuid4().hex}")

    initial = client.post(
        "/api/v1/ai/chat",
        json={"message": "我晚饭吃了一个牛肉馅饼"},
        headers=headers,
    )
    assert initial.status_code == 200
    initial_data = initial.json()["data"]
    action = initial_data["action"]
    assert action["status"] == "proposed"
    assert action["payload"]["items"][0]["weight_g"] == 120

    amended = client.post(
        "/api/v1/ai/chat",
        json={"message": "50克一个", "reply_to_trace_id": initial_data["trace_id"]},
        headers=headers,
    )
    assert amended.status_code == 200
    amended_data = amended.json()["data"]
    assert amended_data["action"]["id"] == action["id"]
    assert amended_data["action"]["status"] == "proposed"
    assert amended_data["action"]["payload"]["meal_type"] == "dinner"
    assert amended_data["action"]["payload"]["items"][0]["weight_g"] == 50
    assert amended_data["action"]["preview_nutrition"]["energy_kcal"] < action["preview_nutrition"]["energy_kcal"]
    assert client.get("/api/v1/today", headers=headers).json()["data"]["completeness"]["recorded_meals"] == 0

    corrected_meal_type = client.post(
        "/api/v1/ai/chat",
        json={"message": "其实是午餐", "reply_to_trace_id": amended_data["trace_id"]},
        headers=headers,
    )
    assert corrected_meal_type.status_code == 200
    corrected_data = corrected_meal_type.json()["data"]
    assert corrected_data["action"]["id"] == action["id"]
    assert corrected_data["action"]["payload"]["meal_type"] == "lunch"

    confirmed = client.post(f"/api/v1/ai/actions/{action['id']}/confirm", headers=headers)
    assert confirmed.status_code == 200
    meal = confirmed.json()["data"]["meal"]
    assert meal["meal_type"] == "lunch"
    assert meal["items"][0]["weight_g"] == 50
    assert client.get("/api/v1/today", headers=headers).json()["data"]["completeness"]["recorded_meals"] == 1


def test_pending_ai_meal_can_be_cancelled_by_follow_up_without_writing():
    headers = setup_ready_user(f"cancel-pending-meal-{uuid4().hex}")
    initial = client.post(
        "/api/v1/ai/chat",
        json={"message": "我晚饭吃了一个牛肉馅饼"},
        headers=headers,
    )
    initial_data = initial.json()["data"]
    action = initial_data["action"]

    cancelled = client.post(
        "/api/v1/ai/chat",
        json={"message": "其实没吃", "reply_to_trace_id": initial_data["trace_id"]},
        headers=headers,
    )
    assert cancelled.status_code == 200
    assert cancelled.json()["data"]["action"]["id"] == action["id"]
    assert cancelled.json()["data"]["action"]["status"] == "cancelled"
    assert client.get("/api/v1/today", headers=headers).json()["data"]["completeness"]["recorded_meals"] == 0
    assert client.post(f"/api/v1/ai/actions/{action['id']}/confirm", headers=headers).status_code == 409


def test_pending_ai_meal_card_edit_recalculates_preview_without_confirming():
    headers = setup_ready_user(f"edit-pending-meal-{uuid4().hex}")
    initial = client.post(
        "/api/v1/ai/chat",
        json={"message": "我晚饭吃了一个牛肉馅饼"},
        headers=headers,
    ).json()["data"]
    action = initial["action"]
    item = action["payload"]["items"][0]

    updated = client.patch(
        f"/api/v1/ai/actions/{action['id']}",
        json={"meal_type": "lunch", "items": [{"food_id": item["food_id"], "weight_g": 80, "consumed_ratio": 0.5}]},
        headers=headers,
    )
    assert updated.status_code == 200
    data = updated.json()["data"]
    assert data["status"] == "proposed"
    assert data["payload"]["meal_type"] == "lunch"
    assert data["payload"]["items"][0]["weight_g"] == 80
    assert data["payload"]["items"][0]["consumed_ratio"] == 0.5
    assert data["preview_nutrition"]["energy_kcal"] < action["preview_nutrition"]["energy_kcal"]
    assert client.get("/api/v1/today", headers=headers).json()["data"]["completeness"]["recorded_meals"] == 0


def test_ai_action_revision_rejects_stale_edits_and_terminal_transitions():
    headers = setup_ready_user(f"action-revision-{uuid4().hex}")
    proposal = client.post(
        "/api/v1/ai/chat",
        json={"message": "我晚饭吃了一个牛肉馅饼"},
        headers=headers,
    ).json()["data"]["action"]
    assert proposal["revision"] == 1
    item = proposal["payload"]["items"][0]
    edit_payload = {
        "meal_type": "dinner",
        "items": [{"food_id": item["food_id"], "weight_g": 50, "consumed_ratio": 1}],
        "expected_revision": 1,
    }

    edited = client.patch(f"/api/v1/ai/actions/{proposal['id']}", json=edit_payload, headers=headers)
    assert edited.status_code == 200
    assert edited.json()["data"]["revision"] == 2
    assert edited.json()["data"]["payload"]["items"][0]["weight_g"] == 50

    stale_edit = client.patch(f"/api/v1/ai/actions/{proposal['id']}", json=edit_payload, headers=headers)
    assert stale_edit.status_code == 409
    assert stale_edit.json()["detail"]["code"] == "AI_ACTION_REVISION_CONFLICT"
    assert stale_edit.json()["detail"]["current_action"]["revision"] == 2

    stale_confirm = client.post(
        f"/api/v1/ai/actions/{proposal['id']}/confirm",
        json={"expected_revision": 1},
        headers=headers,
    )
    assert stale_confirm.status_code == 409
    assert stale_confirm.json()["detail"]["current_action"]["payload"]["items"][0]["weight_g"] == 50

    confirmed = client.post(
        f"/api/v1/ai/actions/{proposal['id']}/confirm",
        json={"expected_revision": 2},
        headers=headers,
    )
    assert confirmed.status_code == 200
    assert confirmed.json()["data"]["action"]["status"] == "confirmed"
    assert confirmed.json()["data"]["action"]["revision"] == 3

    terminal_cancel = client.post(
        f"/api/v1/ai/actions/{proposal['id']}/cancel",
        json={"expected_revision": 3},
        headers=headers,
    )
    assert terminal_cancel.status_code == 409
    assert terminal_cancel.json()["detail"]["code"] == "AI_ACTION_ALREADY_CONFIRMED"


def test_ai_action_cancel_increments_revision_and_cannot_be_repeated():
    headers = setup_ready_user(f"action-cancel-revision-{uuid4().hex}")
    action = client.post(
        "/api/v1/ai/chat",
        json={"message": "我晚饭吃了一个牛肉馅饼"},
        headers=headers,
    ).json()["data"]["action"]

    cancelled = client.post(
        f"/api/v1/ai/actions/{action['id']}/cancel",
        json={"expected_revision": action["revision"]},
        headers=headers,
    )
    assert cancelled.status_code == 200
    assert cancelled.json()["data"]["status"] == "cancelled"
    assert cancelled.json()["data"]["revision"] == action["revision"] + 1

    repeated = client.post(
        f"/api/v1/ai/actions/{action['id']}/cancel",
        json={"expected_revision": cancelled.json()["data"]["revision"]},
        headers=headers,
    )
    assert repeated.status_code == 409
    assert repeated.json()["detail"]["code"] == "AI_ACTION_CANCELLED"


def test_ai_history_is_empty_for_a_new_user():
    headers = setup_ready_user(f"history-empty-{uuid4().hex}")
    response = client.get("/api/v1/ai/history", headers=headers)
    assert response.status_code == 200
    assert response.json()["data"] == {
        "items": [], "limit": 30, "has_more": False, "next_cursor": None, "next_before": None,
    }


def test_ai_history_is_profile_isolated_sorted_and_bounded():
    owner = setup_ready_user(f"history-owner-{uuid4().hex}")
    other = setup_ready_user(f"history-other-{uuid4().hex}")
    owner_profile = UUID(client.get("/api/v1/profiles/me", headers=owner).json()["data"]["id"])
    other_profile = UUID(client.get("/api/v1/profiles/me", headers=other).json()["data"]["id"])
    start = datetime(2026, 8, 31, 2, 0, tzinfo=timezone.utc)
    owner_traces = []
    for index in range(4):
        trace = AgentTrace(
            profile_id=owner_profile,
            message=f"owner-{index}",
            intent="dietary_knowledge",
            decision_stage="inform",
            confidence="medium",
            context_snapshot={"private": f"secret-{index}"},
            outcome="dietary_knowledge",
            response_message=f"answer-{index}",
            created_at=start + timedelta(minutes=index),
        )
        main_module.store.agent_traces[trace.id] = trace
        owner_traces.append(trace)
    foreign = AgentTrace(
        profile_id=other_profile,
        message="other-user-private-message",
        intent="dietary_knowledge",
        decision_stage="inform",
        confidence="medium",
        context_snapshot={"private": "foreign-secret"},
        outcome="dietary_knowledge",
        response_message="other-answer",
        created_at=start + timedelta(minutes=10),
    )
    main_module.store.agent_traces[foreign.id] = foreign

    first_page = client.get("/api/v1/ai/history", params={"limit": 2}, headers=owner)
    assert first_page.status_code == 200
    data = first_page.json()["data"]
    assert [item["user_message"] for item in data["items"]] == ["owner-2", "owner-3"]
    assert data["has_more"] is True
    assert data["next_cursor"]
    assert data["next_before"] == owner_traces[2].created_at.isoformat().replace("+00:00", "Z")
    assert all("context_snapshot" not in item and "private" not in str(item) for item in data["items"])
    assert "other-user-private-message" not in str(data)

    older = client.get(
        "/api/v1/ai/history",
        params={"limit": 2, "cursor": data["next_cursor"]},
        headers=owner,
    )
    assert older.status_code == 200
    assert [item["user_message"] for item in older.json()["data"]["items"]] == ["owner-0", "owner-1"]


def test_ai_history_cursor_handles_identical_timestamps_and_rejects_invalid_values():
    headers = setup_ready_user(f"history-cursor-{uuid4().hex}")
    profile_id = UUID(client.get("/api/v1/profiles/me", headers=headers).json()["data"]["id"])
    created_at = datetime(2026, 8, 31, 4, 0, tzinfo=timezone.utc)
    expected = []
    for index in range(3):
        trace = AgentTrace(
            profile_id=profile_id,
            message=f"same-time-{index}",
            intent="dietary_knowledge",
            decision_stage="inform",
            confidence="medium",
            context_snapshot={},
            outcome="dietary_knowledge",
            response_message=f"same-answer-{index}",
            created_at=created_at,
        )
        main_module.store.agent_traces[trace.id] = trace
        expected.append(trace)
    expected.sort(key=lambda trace: str(trace.id))

    first = client.get("/api/v1/ai/history", params={"limit": 2}, headers=headers)
    assert first.status_code == 200
    first_data = first.json()["data"]
    assert first_data["has_more"] is True
    assert first_data["next_cursor"]
    second = client.get(
        "/api/v1/ai/history",
        params={"limit": 2, "cursor": first_data["next_cursor"]},
        headers=headers,
    )
    assert second.status_code == 200
    messages = [item["user_message"] for item in second.json()["data"]["items"] + first_data["items"]]
    assert messages == [trace.message for trace in expected]
    assert len(messages) == len(set(messages)) == 3

    invalid = client.get("/api/v1/ai/history", params={"cursor": "not-a-valid-cursor"}, headers=headers)
    assert invalid.status_code == 400
    assert invalid.json()["detail"]["code"] == "INVALID_AI_HISTORY_CURSOR"


def test_ai_history_restores_latest_terminal_action_and_feedback_once():
    headers = setup_ready_user(f"history-action-{uuid4().hex}")
    initial = client.post(
        "/api/v1/ai/chat",
        json={"message": "我晚饭吃了一个牛肉馅饼"},
        headers=headers,
    ).json()["data"]
    updated = client.post(
        "/api/v1/ai/chat",
        json={"message": "50克一个", "reply_to_trace_id": initial["trace_id"]},
        headers=headers,
    ).json()["data"]
    action = updated["action"]
    assert action["id"] == initial["action"]["id"]
    assert client.post(
        f"/api/v1/ai/actions/{action['id']}/confirm",
        json={"expected_revision": action["revision"]},
        headers=headers,
    ).status_code == 200
    assert client.post(
        f"/api/v1/ai/traces/{updated['trace_id']}/feedback",
        json={"rating": "helpful"},
        headers=headers,
    ).status_code == 200

    history = client.get("/api/v1/ai/history", params={"limit": 50}, headers=headers)
    assert history.status_code == 200
    items = history.json()["data"]["items"]
    restored_actions = [item["assistant_response"]["action"] for item in items if item["assistant_response"]["action"]]
    assert len(restored_actions) == 1
    restored = restored_actions[0]
    assert restored["id"] == action["id"]
    assert restored["status"] == "confirmed"
    assert restored["revision"] == action["revision"] + 1
    assert restored["payload"]["items"][0]["weight_g"] == 50
    feedback_turn = next(item for item in items if item["trace_id"] == updated["trace_id"])
    assert feedback_turn["feedback_rating"] == "helpful"


def test_account_export_isolated_and_account_delete_removes_user_data_but_keeps_public_food():
    owner = setup_ready_user(f"account-owner-{uuid4().hex}")
    other = setup_ready_user(f"account-other-{uuid4().hex}")
    owner_profile_id = UUID(client.get("/api/v1/profiles/me", headers=owner).json()["data"]["id"])
    other_profile_id = UUID(client.get("/api/v1/profiles/me", headers=other).json()["data"]["id"])
    public_food = next(food for food in main_module.store.foods if food.name == "米饭")
    custom = client.post(
        "/api/v1/foods/custom",
        json={"name": "仅属于导出用户的食品", "basis_weight_g": 100, "default_weight_g": 50, "nutrition": {"energy_kcal": 200, "protein_g": 8, "fat_g": 4, "carbs_g": 30, "fiber_g": 2, "sodium_mg": 20}},
        headers=owner,
    )
    assert custom.status_code == 200
    meal = client.post(
        "/api/v1/meals/manual",
        json={"meal_type": "lunch", "items": [{"food_id": str(public_food.id), "weight_g": 150, "consumed_ratio": 1}]},
        headers=owner,
    )
    assert meal.status_code == 200
    draft = client.post("/api/v1/meal-drafts/example", json={"meal_type": "dinner"}, headers=owner)
    assert draft.status_code == 200
    chat = client.post("/api/v1/ai/chat", json={"message": "我今天吃了米饭"}, headers=owner)
    assert chat.status_code == 200
    trace_id = chat.json()["data"]["trace_id"]
    assert client.post(f"/api/v1/ai/traces/{trace_id}/feedback", json={"rating": "helpful"}, headers=owner).status_code == 200
    profile = main_module.store.profiles[owner_profile_id]
    memory = AgentMemory(profile_id=owner_profile_id, category="preference", value="清淡", source_message="测试导出")
    main_module.store.agent_memories[memory.id] = memory
    action = AIAction(profile_id=owner_profile_id, title="测试动作", summary="测试", payload={})
    main_module.store.ai_actions[action.id] = action
    main_module.store.weights.setdefault(owner_profile_id, []).append({"weight_kg": 60, "measured_at": "2026-09-02T00:00:00+00:00"})
    main_module.store.persist()

    exported = client.get("/api/v1/account/export", headers=owner)
    assert exported.status_code == 200
    data = exported.json()["data"]
    assert data["schema_version"] == "diet-account-export-v1"
    assert data["profile"]["id"] == str(profile.id)
    assert data["custom_foods"][0]["name"] == "仅属于导出用户的食品"
    assert data["meals"] and data["meal_drafts"] and data["ai_traces"] and data["ai_actions"] and data["ai_memories"]
    assert data["ai_feedback"]
    assert "sessions" not in data and "access_token" not in str(data) and "token_hash" not in str(data)
    assert "account-other" not in str(data)

    wrong = client.request("DELETE", "/api/v1/account", json={"confirmation": "删除账号"}, headers=owner)
    assert wrong.status_code == 400
    assert client.get("/api/v1/profiles/me", headers=owner).status_code == 200

    deleted = client.request("DELETE", "/api/v1/account", json={"confirmation": "永久删除"}, headers=owner)
    assert deleted.status_code == 200
    assert deleted.json()["data"]["deleted"] is True
    assert client.get("/api/v1/profiles/me", headers=owner).status_code == 401
    assert client.get("/api/v1/account/export", headers=owner).status_code == 401
    assert client.get("/api/v1/profiles/me", headers=other).status_code == 200
    assert other_profile_id in main_module.store.profiles
    assert owner_profile_id not in main_module.store.profiles
    assert not any(food.name == "仅属于导出用户的食品" for food in main_module.store.foods)
    assert any(food.id == public_food.id for food in main_module.store.foods)


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


def test_ai_agent_accepts_ambiguous_portions_as_a_low_confidence_proposal():
    headers = setup_ready_user(f"agent-clarify-{uuid4().hex}")

    vague = client.post(
        "/api/v1/ai/chat",
        json={"message": "午餐吃了米饭和鸡蛋"},
        headers=headers,
    )
    assert vague.status_code == 200
    response = vague.json()["data"]
    assert response["kind"] == "meal_record_proposal"
    assert response["needs_clarification"] is False
    assert response["confidence"] == "low"
    assert response["action"] is not None
    assert response["trace_id"]
    assert response["clarification_options"] == []
    assert all(item["estimated_weight_range_g"] for item in response["action"]["payload"]["items"])
    assert client.get("/api/v1/today", headers=headers).json()["data"]["completeness"]["recorded_meals"] == 0


def test_ai_agent_uses_parent_trace_for_a_portion_only_follow_up():
    headers = setup_ready_user(f"agent-follow-up-{uuid4().hex}")

    vague = client.post(
        "/api/v1/ai/chat",
        json={"message": "我午餐吃了一根火腿肠"},
        headers=headers,
    ).json()["data"]
    trace = main_module.store.agent_traces[UUID(vague["trace_id"])]
    trace.needs_clarification = True
    trace.decision_stage = "clarify"

    clarified = client.post(
        "/api/v1/ai/chat",
        json={"message": "50g 一根，确认即可", "reply_to_trace_id": vague["trace_id"]},
        headers=headers,
    )
    assert clarified.status_code == 200
    response = clarified.json()["data"]
    assert response["kind"] == "meal_record_proposal"
    assert response["decision_stage"] == "propose"
    assert response["needs_clarification"] is False
    assert response["clarification_options"] == []
    assert response["action"]["payload"]["items"][0]["name"] == "火腿肠"
    assert response["action"]["payload"]["items"][0]["weight_g"] == 50


def test_regular_follow_up_builds_a_short_conversation_context():
    headers = setup_ready_user(f"agent-context-{uuid4().hex}")
    first = client.post(
        "/api/v1/ai/chat",
        json={"message": "一个苹果大约有多少热量？"},
        headers=headers,
    ).json()["data"]
    parent = main_module.store.agent_traces[UUID(first["trace_id"])]

    contextualized = main_module.build_conversation_message(
        "那香蕉呢？",
        parent,
        parent.profile_id,
    )

    assert "用户：一个苹果大约有多少热量？" in contextualized
    assert f"助手：{parent.response_message}" in contextualized
    assert contextualized.endswith("【当前消息】\n那香蕉呢？")

    follow_up = client.post(
        "/api/v1/ai/chat",
        json={"message": "那香蕉呢？", "reply_to_trace_id": first["trace_id"]},
        headers=headers,
    ).json()["data"]
    assert any(call["name"] == "get_long_term_memory" for call in follow_up["tool_calls"])


def test_long_term_memory_recalls_old_relevant_history_and_isolates_users(monkeypatch):
    monkeypatch.setenv("DIET_DISABLE_PERSISTENCE", "1")
    owner = Profile(user_id=uuid4())
    other = Profile(user_id=uuid4())
    preference = AgentMemory(
        profile_id=owner.id,
        category="avoidance",
        value="乳糖不耐受，避免普通牛奶",
        source_message="我喝普通牛奶会不舒服",
    )
    old_relevant = AgentTrace(
        profile_id=owner.id,
        message="我早餐吃燕麦后感觉状态不错",
        intent="meal_record",
        decision_stage="inform",
        confidence="medium",
        context_snapshot={},
        outcome="explanation",
        response_message="记住了这次关于燕麦的讨论。",
    )
    newer_traces = [
        AgentTrace(
            profile_id=owner.id,
            message=f"第{index}次无关的蔬菜讨论",
            intent="dietary_knowledge",
            decision_stage="inform",
            confidence="medium",
            context_snapshot={},
            outcome="explanation",
            response_message="建议保持食物多样。",
        )
        for index in range(6)
    ]
    foreign = AgentTrace(
        profile_id=other.id,
        message="燕麦和牛奶是另一个用户的隐私",
        intent="meal_record",
        decision_stage="inform",
        confidence="medium",
        context_snapshot={},
        outcome="explanation",
        response_message="不应被召回。",
    )

    recalled = PersonalMemoryIndex().retrieve(
        owner,
        [],
        "我之前说过燕麦和牛奶吗",
        memories=[preference],
        traces=[old_relevant, *newer_traces, foreign],
    )
    attached = main_module.attach_long_term_memory("【当前消息】\n那我早餐怎么搭配？", recalled)

    assert any("乳糖不耐受" in item for item in recalled["preferences"])
    assert any("燕麦" in item for item in recalled["conversations"])
    assert all("另一个用户" not in item for item in recalled["conversations"])
    assert "已由用户确认的长期偏好" in attached
    assert "相关历史对话" in attached


def test_long_term_index_does_not_rewrite_an_unchanged_conversation(monkeypatch):
    class FakeCollection:
        def __init__(self):
            self.upsert_calls = 0

        def upsert(self, **kwargs):
            self.upsert_calls += 1

    index = PersonalMemoryIndex()
    collection = FakeCollection()
    index._conversations = collection
    monkeypatch.setattr(index, "_ensure", lambda: True)
    trace = AgentTrace(
        profile_id=uuid4(),
        message="长期记忆性能测试",
        intent="dietary_knowledge",
        decision_stage="inform",
        confidence="medium",
        context_snapshot={},
        outcome="explanation",
        response_message="同一条对话不应重复写入。",
    )

    index.upsert_conversation(trace)
    index.upsert_conversation(trace)

    assert collection.upsert_calls == 1


def test_ai_agent_follow_up_can_supply_meal_type_without_repeating_foods():
    headers = setup_ready_user(f"agent-follow-up-meal-{uuid4().hex}")
    vague = client.post(
        "/api/v1/ai/chat",
        json={"message": "我吃了两个鸡蛋"},
        headers=headers,
    ).json()["data"]
    trace = main_module.store.agent_traces[UUID(vague["trace_id"])]
    trace.needs_clarification = True
    trace.decision_stage = "clarify"

    response = client.post(
        "/api/v1/ai/chat",
        json={"message": "是晚餐", "reply_to_trace_id": vague["trace_id"]},
        headers=headers,
    ).json()["data"]

    assert response["kind"] == "meal_record_proposal"
    assert response["action"]["payload"]["meal_type"] == "dinner"
    assert response["action"]["payload"]["items"][0]["name"] == "鸡蛋"
    assert response["action"]["payload"]["items"][0]["weight_g"] == 100


def test_ai_agent_follow_up_preserves_body_feedback_when_portion_is_added():
    headers = setup_ready_user(f"agent-follow-up-feedback-{uuid4().hex}")
    vague = client.post(
        "/api/v1/ai/chat",
        json={"message": "我吃了一个牛肉馅饼，之后肚子胀"},
        headers=headers,
    ).json()["data"]
    trace = main_module.store.agent_traces[UUID(vague["trace_id"])]
    trace.needs_clarification = True
    trace.decision_stage = "clarify"

    response = client.post(
        "/api/v1/ai/chat",
        json={"message": "120克", "reply_to_trace_id": vague["trace_id"]},
        headers=headers,
    ).json()["data"]

    assert response["action"]["payload"]["items"][0]["weight_g"] == 120
    assert response["action"]["payload"]["body_feedback"]["symptoms"] == ["腹胀"]


def test_ai_agent_follow_up_can_correct_a_record_into_planned_consumption():
    headers = setup_ready_user(f"agent-follow-up-planned-{uuid4().hex}")
    vague = client.post(
        "/api/v1/ai/chat",
        json={"message": "我吃了两个香蕉"},
        headers=headers,
    ).json()["data"]
    trace = main_module.store.agent_traces[UUID(vague["trace_id"])]
    trace.needs_clarification = True
    trace.decision_stage = "clarify"

    response = client.post(
        "/api/v1/ai/chat",
        json={"message": "这是准备吃的", "reply_to_trace_id": vague["trace_id"]},
        headers=headers,
    ).json()["data"]

    assert response["kind"] == "consumption_advice"
    assert response["action"] is None
    assert response["decision_stage"] == "inform"


def test_ai_agent_rejects_a_parent_trace_owned_by_another_user():
    owner_headers = setup_ready_user(f"agent-trace-owner-{uuid4().hex}")
    other_headers = setup_ready_user(f"agent-trace-other-{uuid4().hex}")
    vague = client.post(
        "/api/v1/ai/chat",
        json={"message": "我午餐吃了一根火腿肠"},
        headers=owner_headers,
    ).json()["data"]

    response = client.post(
        "/api/v1/ai/chat",
        json={"message": "50克", "reply_to_trace_id": vague["trace_id"]},
        headers=other_headers,
    )
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "AI_TRACE_NOT_FOUND"


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

    xiaolongbao_record = client.post(
        "/api/v1/ai/chat",
        json={"message": "我今天中午吃了四个小笼包"},
        headers=headers,
    ).json()["data"]
    assert xiaolongbao_record["kind"] == "meal_record_proposal"
    assert xiaolongbao_record["needs_clarification"] is False
    assert xiaolongbao_record["action"]["payload"]["items"][0]["name"] == "小笼包"
    assert xiaolongbao_record["action"]["payload"]["items"][0]["weight_g"] == 120
    assert xiaolongbao_record["action"]["preview_nutrition"]["energy_kcal"] == 276
    assert any("每个约 30g" in item for item in xiaolongbao_record["action"]["assumptions"])


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
        "/api/v1/meal-drafts/example",
        json={"meal_type": "lunch"},
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


def test_photo_draft_endpoint_is_retired_and_example_draft_stays_explicit():
    headers = setup_ready_user(f"retired-photo-{uuid4().hex}")

    retired = client.post(
        "/api/v1/meal-drafts",
        json={"meal_type": "lunch", "assets": [{"type": "image", "storage_key": "user-photo.jpg"}]},
        headers=headers,
    )
    example = client.post(
        "/api/v1/meal-drafts/example",
        json={"meal_type": "lunch"},
        headers=headers,
    )

    assert retired.status_code == 410
    assert retired.json()["detail"]["code"] == "PHOTO_RECOGNITION_NOT_AVAILABLE"
    assert "不支持拍照或图片识别" in retired.json()["detail"]["message"]
    assert example.status_code == 200
    assert example.json()["data"]["provider"] == "example_template"
    assert example.json()["data"]["assets"] == []


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


def test_lactose_intolerance_uses_deterministic_confirmed_profile_update_and_blocks_recommendations():
    headers = setup_ready_user(f"safety-lactose-{uuid4().hex}")
    proposed = client.post(
        "/api/v1/ai/chat",
        json={"message": "我乳糖不耐受，喝普通牛奶会不舒服"},
        headers=headers,
    )
    assert proposed.status_code == 200
    response = proposed.json()["data"]
    assert response["kind"] == "safety_profile_proposal"
    assert response["provider"] == "safety_profile_rules"
    assert response["action"]["action_type"] == "update_safety_profile"
    assert client.get("/api/v1/profiles/me", headers=headers).json()["data"]["food_intolerances"] == []

    confirmed = client.post(f"/api/v1/ai/actions/{response['action']['id']}/confirm", headers=headers)
    assert confirmed.status_code == 200
    assert "乳糖" in confirmed.json()["data"]["profile"]["food_intolerances"]
    plans = client.post("/api/v1/meal-plans/preview", json={"meal_type": "dinner"}, headers=headers)
    assert plans.status_code == 200
    assert all("牛奶" not in item["name"] for plan in plans.json()["data"]["plans"] for item in plan["items"])


def test_peanut_allergy_requires_confirmation_then_records_actual_consumption_with_risk():
    headers = setup_ready_user(f"safety-peanut-{uuid4().hex}")
    allergy = client.post("/api/v1/ai/chat", json={"message": "我对花生过敏"}, headers=headers).json()["data"]
    assert client.post(f"/api/v1/ai/actions/{allergy['action']['id']}/confirm", headers=headers).status_code == 200

    meal = client.post("/api/v1/ai/chat", json={"message": "我午餐吃了25克花生"}, headers=headers)
    assert meal.status_code == 200
    meal_action = meal.json()["data"]["action"]
    assert meal_action and meal_action["action_type"] == "create_meal"
    assert meal_action["payload"]["safety_risks"][0]["type"] == "allergy"
    assert meal_action["payload"]["safety_risks"][0]["evaluation_mode"] == "record"
    assert meal_action["payload"]["safety_risks"][0]["decision_effect"] == "recorded_with_warning"
    assert "已阻止" not in meal_action["payload"]["safety_risks"][0]["message"]
    assert "已发生事实的待确认记录" in meal.json()["data"]["message"]
    assert "确认仅用于如实记录" in "".join(meal_action["assumptions"])
    confirmed = client.post(f"/api/v1/ai/actions/{meal_action['id']}/confirm", headers=headers)
    assert confirmed.status_code == 200
    saved = confirmed.json()["data"]
    assert saved["meal"]["risks"][0]["level"] == "block"
    assert saved["meal"]["risks"][0]["decision_effect"] == "recorded_with_warning"
    assert "已阻止" not in saved["meal"]["risks"][0]["message"]
    assert saved["action"]["payload"]["safety_acknowledged_at"]
    assert len(client.get("/api/v1/meals", headers=headers).json()["data"]) == 1


def test_peanut_allergy_blocks_planned_consumption_advice_with_an_alternative():
    headers = setup_ready_user(f"safety-peanut-plan-{uuid4().hex}")
    allergy = client.post("/api/v1/ai/chat", json={"message": "我对花生过敏"}, headers=headers).json()["data"]
    assert client.post(f"/api/v1/ai/actions/{allergy['action']['id']}/confirm", headers=headers).status_code == 200

    planned = client.post(
        "/api/v1/ai/chat",
        json={"message": "晚餐准备吃25克花生"},
        headers=headers,
    )
    assert planned.status_code == 200
    response = planned.json()["data"]
    assert response["kind"] == "consumption_advice"
    assert response["decision_stage"] == "safety"
    assert response["action"] is None
    assert "不能建议" in response["message"]
    assert "花生" in "".join(response["basis"])
    assert any("不含花生" in suggestion for suggestion in response["suggestions"])
    assert "可以纳入" not in response["message"]
    assert client.get("/api/v1/meals", headers=headers).json()["data"] == []


def test_hard_exclusion_and_intolerance_block_planned_consumption_advice():
    cases = [
        ("我不能吃牛肉", "晚餐准备吃50克牛肉", "牛肉"),
        ("我乳糖不耐受", "晚餐准备喝250克牛奶", "乳糖"),
    ]
    for profile_message, planned_message, restriction in cases:
        headers = setup_ready_user(f"safety-plan-block-{uuid4().hex}")
        proposal = client.post("/api/v1/ai/chat", json={"message": profile_message}, headers=headers).json()["data"]
        assert client.post(f"/api/v1/ai/actions/{proposal['action']['id']}/confirm", headers=headers).status_code == 200

        response = client.post("/api/v1/ai/chat", json={"message": planned_message}, headers=headers).json()["data"]
        assert response["decision_stage"] == "safety"
        assert response["action"] is None
        assert restriction in response["message"]
        assert "可以纳入" not in response["message"]


def test_safety_statements_cover_short_hard_and_compound_phrasings():
    cases = [
        ("花生过敏", "allergies", "花生"),
        ("我不能吃花生", "hard_exclusions", "花生"),
        ("我吃花生会过敏", "allergies", "花生"),
    ]
    for message, expected_field, expected_value in cases:
        headers = setup_ready_user(f"safety-expression-{uuid4().hex}")
        response = client.post("/api/v1/ai/chat", json={"message": message}, headers=headers)
        assert response.status_code == 200
        action = response.json()["data"]["action"]
        assert action["action_type"] == "update_safety_profile"
        assert action["payload"]["profile_field"] == expected_field
        assert action["payload"]["value"] == expected_value


def test_multiple_safety_statements_are_confirmed_atomically():
    headers = setup_ready_user(f"safety-multiple-{uuid4().hex}")
    proposed = client.post(
        "/api/v1/ai/chat",
        json={"message": "我对花生过敏，也乳糖不耐受"},
        headers=headers,
    )
    assert proposed.status_code == 200
    action = proposed.json()["data"]["action"]
    assert [(item["profile_field"], item["value"]) for item in action["payload"]["updates"]] == [
        ("allergies", "花生"),
        ("food_intolerances", "乳糖"),
    ]
    before = client.get("/api/v1/profiles/me", headers=headers).json()["data"]
    assert before["allergies"] == [] and before["food_intolerances"] == []

    confirmed = client.post(f"/api/v1/ai/actions/{action['id']}/confirm", headers=headers)
    assert confirmed.status_code == 200
    profile = confirmed.json()["data"]["profile"]
    assert profile["allergies"] == ["花生"]
    assert profile["food_intolerances"] == ["乳糖"]


def test_hard_exclusion_can_be_cancelled_and_profile_items_can_be_deleted():
    headers = setup_ready_user(f"safety-hard-{uuid4().hex}")
    proposal = client.post("/api/v1/ai/chat", json={"message": "不要给我推荐牛肉"}, headers=headers).json()["data"]
    assert proposal["action"]["payload"]["profile_field"] == "hard_exclusions"
    cancelled = client.post(f"/api/v1/ai/actions/{proposal['action']['id']}/cancel", headers=headers)
    assert cancelled.status_code == 200
    assert client.get("/api/v1/profiles/me", headers=headers).json()["data"]["hard_exclusions"] == []

    added = client.put("/api/v1/profiles/me", json={"allergies": ["花生"], "hard_exclusions": ["牛肉"], "safety_profile_reviewed": True}, headers=headers)
    assert added.status_code == 200
    deleted = client.put("/api/v1/profiles/me", json={"allergies": [], "hard_exclusions": []}, headers=headers)
    assert deleted.status_code == 200
    assert deleted.json()["data"]["allergies"] == []
    assert deleted.json()["data"]["hard_exclusions"] == []


def test_safety_profile_actions_are_isolated_between_users_and_feedback_is_not_auto_promoted():
    owner = setup_ready_user(f"safety-owner-{uuid4().hex}")
    other = setup_ready_user(f"safety-other-{uuid4().hex}")
    proposal = client.post("/api/v1/ai/chat", json={"message": "我对花生过敏"}, headers=owner).json()["data"]
    forbidden = client.post(f"/api/v1/ai/actions/{proposal['action']['id']}/confirm", headers=other)
    assert forbidden.status_code == 404

    feedback = client.post("/api/v1/ai/chat", json={"message": "我吃花生后腹泻"}, headers=owner)
    assert feedback.status_code == 200
    feedback_action = feedback.json()["data"].get("action")
    assert feedback_action is None or feedback_action["action_type"] != "update_safety_profile"
    profile = client.get("/api/v1/profiles/me", headers=owner).json()["data"]
    assert profile["allergies"] == []
    assert profile["digestive_sensitivities"] == []


def test_plan_save_rechecks_latest_safety_profile_and_new_recommendations_exclude_it():
    headers = setup_ready_user(f"safety-plan-{uuid4().hex}")
    initial = client.post("/api/v1/meal-plans/preview", json={"meal_type": "dinner"}, headers=headers)
    assert initial.status_code == 200
    plan = initial.json()["data"]["plans"][0]
    excluded_food = plan["items"][0]["name"]

    updated = client.put(
        "/api/v1/profiles/me",
        json={"hard_exclusions": [excluded_food], "safety_profile_reviewed": True},
        headers=headers,
    )
    assert updated.status_code == 200
    blocked_save = client.post(f"/api/v1/meal-plans/{plan['id']}/save", headers=headers)
    assert blocked_save.status_code == 409
    assert blocked_save.json()["detail"]["code"] == "RISK_BLOCKED"
    assert blocked_save.json()["detail"]["risks"][0]["alternative"]

    refreshed = client.post("/api/v1/meal-plans/preview", json={"meal_type": "dinner"}, headers=headers)
    assert refreshed.status_code == 200
    assert all(excluded_food != item["name"] for candidate in refreshed.json()["data"]["plans"] for item in candidate["items"])


def create_hidden_peanut_food(headers, name: str = "可可谷物能量棒"):
    response = client.post(
        "/api/v1/foods/custom",
        json={
            "name": name,
            "basis_weight_g": 100,
            "default_weight_g": 50,
            "allergens": ["花生"],
            "nutrition": {
                "energy_kcal": 420,
                "protein_g": 10,
                "fat_g": 16,
                "carbs_g": 58,
                "fiber_g": 5,
                "sodium_mg": 180,
                "vegetable_g": 0,
                "fruit_g": 0,
            },
        },
        headers=headers,
    )
    assert response.status_code == 200
    food = response.json()["data"]
    assert "花生" not in food["name"]
    assert food["allergens"] == ["花生"]
    return food


def test_declared_allergen_blocks_planned_consumption_but_actual_ai_meal_is_recorded_with_source():
    headers = setup_ready_user(f"declared-allergen-ai-{uuid4().hex}")
    food = create_hidden_peanut_food(headers)
    assert client.put(
        "/api/v1/profiles/me",
        json={"allergies": ["花生"], "safety_profile_reviewed": True},
        headers=headers,
    ).status_code == 200

    planned = client.post(
        "/api/v1/ai/chat",
        json={"message": f"晚餐准备吃50克{food['name']}"},
        headers=headers,
    )
    assert planned.status_code == 200
    planned_data = planned.json()["data"]
    assert planned_data["decision_stage"] == "safety"
    assert planned_data["action"] is None
    assert any("过敏原标注：花生" in basis for basis in planned_data["basis"])
    assert client.get("/api/v1/meals", headers=headers).json()["data"] == []

    actual = client.post(
        "/api/v1/ai/chat",
        json={"message": f"我午餐吃了50克{food['name']}"},
        headers=headers,
    )
    assert actual.status_code == 200
    action = actual.json()["data"]["action"]
    risk = next(risk for risk in action["payload"]["safety_risks"] if risk["type"] == "allergy")
    assert risk["level"] == "block"
    assert risk["evaluation_mode"] == "record"
    assert risk["decision_effect"] == "recorded_with_warning"
    assert "已阻止" not in risk["message"]
    assert risk["match_source"] == "allergen"
    assert risk["matched_value"] == "花生"
    assert client.get("/api/v1/meals", headers=headers).json()["data"] == []

    confirmed = client.post(f"/api/v1/ai/actions/{action['id']}/confirm", headers=headers)
    assert confirmed.status_code == 200
    meal_risk = next(risk for risk in confirmed.json()["data"]["meal"]["risks"] if risk["type"] == "allergy")
    assert meal_risk["match_source"] == "allergen"
    assert meal_risk["matched_value"] == "花生"
    assert meal_risk["alternative"]
    assert meal_risk["decision_effect"] == "recorded_with_warning"


def test_declared_allergen_blocks_preview_and_plan_save_without_harming_safe_food(monkeypatch):
    headers = setup_ready_user(f"declared-allergen-plan-{uuid4().hex}")
    risky = create_hidden_peanut_food(headers, "巧克力谷物棒")
    safe = client.post(
        "/api/v1/foods/custom",
        json={
            "name": "原味米果",
            "basis_weight_g": 100,
            "default_weight_g": 40,
            "allergens": [],
            "nutrition": {"energy_kcal": 380, "protein_g": 6, "fat_g": 4, "carbs_g": 80, "fiber_g": 2, "sodium_mg": 120},
        },
        headers=headers,
    ).json()["data"]
    profile = client.put(
        "/api/v1/profiles/me",
        json={"allergies": ["花生"], "safety_profile_reviewed": True},
        headers=headers,
    ).json()["data"]
    risky_food = next(food for food in main_module.store.foods if str(food.id) == risky["id"])

    class OnlyRiskyAdvice:
        def propose(self, context, foods):
            return [{
                "title": "测试候选",
                "scenario": "quick",
                "items": [{"food": risky_food, "weight_g": 50}],
                "reason": "测试配料级拦截",
            }]

    monkeypatch.setattr(main_module, "advice_provider", OnlyRiskyAdvice())
    preview = client.post("/api/v1/meal-plans/preview", json={"meal_type": "dinner"}, headers=headers)
    assert preview.status_code == 409
    assert preview.json()["detail"]["code"] == "SAFETY_PROFILE_BLOCKED_RECOMMENDATION"

    risky_item = MealItem(
        food_id=UUID(risky["id"]),
        name=risky["name"],
        weight_g=50,
        nutrition=Nutrition.model_validate(risky["nutrition_per_100g"]),
    )
    risky_plan = MealPlan(
        profile_id=UUID(profile["id"]),
        meal_type="dinner",
        scenario="quick",
        title="含隐藏花生过敏原",
        items=[risky_item],
        nutrition=risky_item.nutrition,
        reason="测试",
    )
    main_module.store.plans[risky_plan.id] = risky_plan
    blocked = client.post(f"/api/v1/meal-plans/{risky_plan.id}/save", headers=headers)
    assert blocked.status_code == 409
    blocked_risk = blocked.json()["detail"]["risks"][0]
    assert blocked_risk["match_source"] == "allergen"
    assert blocked_risk["evaluation_mode"] == "recommendation"
    assert blocked_risk["decision_effect"] == "blocked"
    assert blocked_risk["message"].startswith("已阻止")

    safe_item = MealItem(
        food_id=UUID(safe["id"]),
        name=safe["name"],
        weight_g=40,
        nutrition=Nutrition.model_validate(safe["nutrition_per_100g"]),
    )
    safe_plan = MealPlan(
        profile_id=UUID(profile["id"]),
        meal_type="dinner",
        scenario="quick",
        title="普通米果",
        items=[safe_item],
        nutrition=safe_item.nutrition,
        reason="测试",
    )
    main_module.store.plans[safe_plan.id] = safe_plan
    saved = client.post(f"/api/v1/meal-plans/{safe_plan.id}/save", headers=headers)
    assert saved.status_code == 200
    assert not any(risk["level"] == "block" for risk in saved.json()["data"]["risks"])


def test_manual_and_example_records_keep_declared_allergen_risk_as_factual_history():
    headers = setup_ready_user(f"declared-allergen-records-{uuid4().hex}")
    food = create_hidden_peanut_food(headers, "黑巧蛋白棒")
    assert client.put(
        "/api/v1/profiles/me",
        json={"allergies": ["花生", "鸡蛋"], "safety_profile_reviewed": True},
        headers=headers,
    ).status_code == 200

    manual = client.post(
        "/api/v1/meals/manual",
        json={"meal_type": "lunch", "items": [{"food_id": food["id"], "weight_g": 50, "consumed_ratio": 1}]},
        headers=headers,
    )
    assert manual.status_code == 200
    manual_risk = next(risk for risk in manual.json()["data"]["risks"] if risk.get("match_source") == "allergen" and risk.get("matched_value") == "花生")
    assert manual_risk["evaluation_mode"] == "record"
    assert manual_risk["decision_effect"] == "recorded_with_warning"
    assert "已阻止" not in manual_risk["message"]

    draft = client.post("/api/v1/meal-drafts/example", json={"meal_type": "dinner"}, headers=headers).json()["data"]
    confirmed = client.post(
        f"/api/v1/meal-drafts/{draft['id']}/confirm",
        json={"items": [
            {"item_id": item["id"], "food_id": item["food_id"], "name": item["name"], "weight_g": item["estimated_weight_g"], "consumed_ratio": 1}
            for item in draft["items"]
        ]},
        headers=headers,
    )
    assert confirmed.status_code == 200
    example_risk = next(
        risk for risk in confirmed.json()["data"]["risks"]
        if risk.get("match_source") == "allergen"
        and risk.get("food_name") == "番茄炒蛋"
        and risk.get("matched_value") == "鸡蛋"
    )
    assert example_risk["evaluation_mode"] == "record"
    assert example_risk["decision_effect"] == "recorded_with_warning"
    assert "已阻止" not in example_risk["message"]


def test_risk_decision_effect_separates_recording_from_recommendation_for_name_and_allergen_matches():
    headers = setup_ready_user(f"risk-effect-{uuid4().hex}")
    profile_id = UUID(client.get("/api/v1/profiles/me", headers=headers).json()["data"]["id"])
    profile = main_module.store.profiles[profile_id]
    profile.allergies = ["花生"]

    cases = [
        ("花生", {}),
        ("谷物能量棒", {"谷物能量棒": ["花生"]}),
    ]
    for food_name, allergen_context in cases:
        recorded = detect_profile_food_risks(profile, [food_name], mode="record", food_allergens=allergen_context)[0]
        blocked = detect_profile_food_risks(profile, [food_name], mode="recommendation", food_allergens=allergen_context)[0]
        assert recorded["level"] == blocked["level"] == "block"
        assert recorded["evaluation_mode"] == "record"
        assert recorded["decision_effect"] == "recorded_with_warning"
        assert recorded["message"].startswith("高风险记录")
        assert "已阻止" not in recorded["message"]
        assert blocked["evaluation_mode"] == "recommendation"
        assert blocked["decision_effect"] == "blocked"
        assert blocked["message"].startswith("已阻止")
