from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from sqlalchemy import delete, inspect, select

from app.database_store import DatabaseStore
from app.db_models import AgentFeedbackRow, AgentMemoryRow, AgentTraceRow, AIActionRow, FoodRow, GoalRow, MealDraftRow, MealPlanRow, MealRow, ProfileRow, SessionRow, UserIdentityRow, UserRow, WeightRecordRow
from app.models import Food, Meal, MealItem, Nutrition
from app.services import calculate_goal
from app.store import MemoryStore


def test_seed_food_ids_are_stable():
    first = MemoryStore(None)
    second = MemoryStore(None)
    assert {food.name: food.id for food in first.foods} == {food.name: food.id for food in second.foods}


def temporary_database(request, name: str) -> str:
    path = Path(__file__).parent / f".{name}_{uuid4().hex}.db"
    request.addfinalizer(lambda: path.unlink(missing_ok=True))
    return f"sqlite+pysqlite:///{path.as_posix()}"


def test_database_store_round_trip(request):
    url = temporary_database(request, "round_trip")
    first = DatabaseStore(url)
    request.addfinalizer(first.engine.dispose)
    user_id = first.get_or_create_user("database-user")
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
    food = Food(
        name="数据库测试食物",
        food_type="custom",
        source="user_nutrition_label",
        tags=[f"profile:{profile.id}"],
        nutrition_per_100g=Nutrition(energy_kcal=100, protein_g=10),
    )
    first.foods.append(food)
    item = MealItem(food_id=food.id, name=food.name, weight_g=120, nutrition=Nutrition(energy_kcal=120, protein_g=12))
    meal = Meal(
        profile_id=profile.id,
        meal_type="lunch",
        eaten_at=datetime.now(timezone.utc),
        record_source="manual",
        items=[item],
        nutrition=item.nutrition,
        confidence="high",
    )
    first.meals[meal.id] = meal
    first.persist()

    restored = DatabaseStore(url, auto_create=False)
    request.addfinalizer(restored.engine.dispose)
    assert restored.users["database-user"] == user_id
    assert restored.profiles[profile.id].onboarding_completed is True
    assert restored.active_goals[profile.id].id == goal.id
    assert restored.meals[meal.id].nutrition.energy_kcal == 120
    assert next(item for item in restored.foods if item.id == food.id).name == food.name


def test_database_schema_has_ownership_and_action_tables(request):
    url = temporary_database(request, "schema")
    store = DatabaseStore(url)
    request.addfinalizer(store.engine.dispose)
    tables = set(inspect(store.engine).get_table_names())
    assert {"users", "profiles", "foods", "meals", "ai_actions", "agent_traces", "sessions"} <= tables


def test_database_store_merges_new_seed_catalog_entries(request):
    url = temporary_database(request, "catalog_merge")
    first = DatabaseStore(url)
    request.addfinalizer(first.engine.dispose)
    beef_pie = next(food for food in first.foods if food.name == "牛肉馅饼")
    with first._session_factory.begin() as session:
        session.execute(delete(FoodRow).where(FoodRow.id == str(beef_pie.id)))
    first.engine.dispose()

    restored = DatabaseStore(url, auto_create=False)
    request.addfinalizer(restored.engine.dispose)

    assert next(food for food in restored.foods if food.name == "牛肉馅饼").id == beef_pie.id
    with restored._session_factory() as session:
        assert session.get(FoodRow, str(beef_pie.id)) is not None


def test_database_store_delete_user_removes_owned_rows_in_one_transaction(request):
    url = temporary_database(request, "delete_user")
    first = DatabaseStore(url)
    request.addfinalizer(first.engine.dispose)
    user_id = first.get_or_create_user(f"delete-user-{uuid4().hex}")
    profile = first.get_profile(user_id)
    profile.onboarding_completed = True
    custom_food = Food(
        name="待删除食品",
        food_type="custom",
        source="user_nutrition_label",
        tags=[f"profile:{profile.id}"],
        nutrition_per_100g=Nutrition(energy_kcal=100, protein_g=10),
    )
    first.foods.append(custom_food)
    item = MealItem(food_id=custom_food.id, name=custom_food.name, weight_g=100, nutrition=custom_food.nutrition_per_100g)
    meal = Meal(profile_id=profile.id, meal_type="lunch", eaten_at=datetime.now(timezone.utc), record_source="manual", items=[item], nutrition=item.nutrition)
    first.meals[meal.id] = meal
    first.sessions["delete-token-hash"] = {"user_id": str(user_id), "expires_at": "2099-01-01T00:00:00+00:00"}
    first.persist()

    result = first.delete_user(user_id)
    assert result and result["counts"]["meals"] == 1
    assert profile.id not in first.profiles
    assert any(food.name == "米饭" for food in first.foods)
    with first._session_factory() as session:
        for row_type in (ProfileRow, GoalRow, MealDraftRow, MealRow, MealPlanRow, AIActionRow, AgentMemoryRow, AgentTraceRow, AgentFeedbackRow, WeightRecordRow, SessionRow, UserIdentityRow, UserRow):
            assert session.scalars(select(row_type)).all() == []
        assert session.get(FoodRow, str(custom_food.id)) is None
        assert session.get(FoodRow, str(next(food for food in first.foods if food.name == "米饭").id)) is not None
