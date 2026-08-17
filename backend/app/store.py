from __future__ import annotations

import json
import os
import shutil
from datetime import date, datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from .food_catalog import extended_foods
from .models import AgentFeedback, AgentMemory, AgentTrace, AIAction, Food, GoalProposal, Meal, MealDraft, MealPlan, Profile


def seeded_foods() -> list[Food]:
    def food(
        name,
        energy,
        protein,
        fat,
        carbs,
        fiber,
        sodium,
        *,
        category,
        default_weight=100,
        vegetable=0,
        fruit=0,
        aliases=None,
        food_type="ingredient",
    ):
        return Food(
            name=name,
            aliases=aliases or [],
            default_unit="1份",
            default_weight_g=default_weight,
            food_type=food_type,
            tags=[category, "常见食材"],
            nutrition_per_100g={
                "energy_kcal": energy,
                "protein_g": protein,
                "fat_g": fat,
                "carbs_g": carbs,
                "fiber_g": fiber,
                "sodium_mg": sodium,
                "added_sugar_g": 0,
                "vegetable_g": vegetable,
                "fruit_g": fruit,
            },
        )

    foods = [
        food("米饭", 116, 2.6, 0.3, 25.9, 0.3, 2, category="主食", default_weight=150, aliases=["白米饭", "大米饭"]),
        food("鸡蛋", 144, 13.3, 8.8, 2.8, 0, 125, category="肉蛋", default_weight=50, aliases=["水煮蛋", "煮鸡蛋"]),
        food("鸡胸肉", 165, 31, 3.6, 0, 0, 75, category="肉蛋", default_weight=150, aliases=["鸡肉", "鸡胸"]),
        food("牛奶", 54, 3, 3.2, 3.4, 0, 43, category="奶豆", default_weight=250, aliases=["纯牛奶", "鲜奶"]),
        food("番茄", 18, 0.9, 0.2, 3.9, 1.2, 5, category="蔬菜", default_weight=150, vegetable=100, aliases=["西红柿"]),
        food("西兰花", 34, 2.8, 0.4, 6.6, 2.6, 33, category="蔬菜", default_weight=150, vegetable=100, aliases=["绿菜花"]),
        food("香蕉", 93, 1.4, 0.2, 22, 1.2, 1, category="水果", default_weight=100, fruit=85),
        food("燕麦", 377, 15, 6.7, 66.7, 10.1, 3, category="主食", default_weight=50, aliases=["燕麦片"]),
        food("豆腐", 81, 8.1, 4.2, 3.8, 0.3, 7, category="奶豆", default_weight=150, aliases=["北豆腐", "老豆腐"]),
        food("红薯", 86, 1.6, 0.1, 20.1, 3, 55, category="主食", default_weight=180, aliases=["地瓜", "番薯"]),
        food("苹果", 53, 0.3, 0.2, 13.7, 2.4, 1, category="水果", default_weight=180, fruit=85),
        food("黄瓜", 15, 0.7, 0.1, 3.6, 0.5, 2, category="蔬菜", default_weight=150, vegetable=100, aliases=["青瓜"]),
        food("牛肉", 125, 20.4, 4.2, 0, 0, 56, category="肉蛋", default_weight=150, aliases=["瘦牛肉"]),
        food("虾仁", 99, 20.3, 1.7, 0.2, 0, 111, category="肉蛋", default_weight=150, aliases=["虾", "鲜虾"]),
        food("原味无糖酸奶", 63, 3.5, 3.3, 4.7, 0, 48, category="奶豆", default_weight=180, aliases=["酸奶", "无糖酸奶"]),
        food("面条（熟）", 110, 3.3, 0.7, 22.8, 1.2, 5, category="主食", default_weight=200, aliases=["面条", "煮面"]),
        food("玉米", 112, 4, 1.2, 22.8, 2.9, 15, category="主食", default_weight=180, aliases=["甜玉米", "玉米棒"]),
        food("菠菜", 23, 2.9, 0.4, 3.6, 2.2, 79, category="蔬菜", default_weight=150, vegetable=100),
        food("橙子", 48, 0.9, 0.1, 11.8, 2.4, 0, category="水果", default_weight=180, fruit=75, aliases=["橙", "脐橙"]),
        food("全麦面包", 246, 9.7, 4.2, 46.1, 6.4, 450, category="主食", default_weight=70, aliases=["面包", "全麦吐司"]),
        food("糙米饭", 116, 2.7, 0.9, 24, 1.8, 4, category="主食", default_weight=150, aliases=["糙米"]),
        food("馒头", 223, 7, 1.1, 47, 1.3, 165, category="主食", default_weight=100, aliases=["白馒头"]),
        food("土豆", 77, 2, 0.1, 17.5, 2.2, 6, category="主食", default_weight=180, aliases=["马铃薯"]),
        food("鸡腿肉（去皮）", 177, 24, 8, 0, 0, 84, category="肉蛋", default_weight=150, aliases=["鸡腿", "去皮鸡腿"]),
        food("瘦猪肉", 143, 20.3, 6.2, 0, 0, 58, category="肉蛋", default_weight=150, aliases=["猪肉", "猪里脊", "里脊肉"]),
        food("三文鱼", 208, 20.4, 13.4, 0, 0, 59, category="肉蛋", default_weight=150, aliases=["鲑鱼"]),
        food("鳕鱼", 82, 17.8, 0.7, 0, 0, 54, category="肉蛋", default_weight=150, aliases=["银鳕鱼"]),
        food("生菜", 15, 1.4, 0.2, 2.9, 1.3, 28, category="蔬菜", default_weight=150, vegetable=100, aliases=["叶生菜"]),
        food("大白菜", 17, 1.5, 0.2, 3.2, 1, 18, category="蔬菜", default_weight=200, vegetable=100, aliases=["白菜"]),
        food("油菜", 20, 1.8, 0.5, 3.3, 1.1, 55, category="蔬菜", default_weight=150, vegetable=100, aliases=["小油菜", "上海青"]),
        food("胡萝卜", 41, 0.9, 0.2, 9.6, 2.8, 69, category="蔬菜", default_weight=100, vegetable=100),
        food("菜花", 25, 1.9, 0.3, 5, 2, 30, category="蔬菜", default_weight=150, vegetable=100, aliases=["花菜", "花椰菜"]),
        food("芹菜", 16, 0.7, 0.2, 3, 1.6, 80, category="蔬菜", default_weight=150, vegetable=100),
        food("茄子", 25, 1, 0.2, 5.9, 3, 2, category="蔬菜", default_weight=180, vegetable=100),
        food("青椒", 22, 1, 0.2, 5.4, 1.7, 3, category="蔬菜", default_weight=120, vegetable=100, aliases=["甜椒", "柿子椒"]),
        food("蘑菇", 22, 3.1, 0.3, 3.3, 1, 5, category="蔬菜", default_weight=150, vegetable=100, aliases=["鲜蘑", "白蘑菇"]),
        food("梨", 51, 0.4, 0.1, 13.3, 3.1, 1, category="水果", default_weight=200, fruit=85, aliases=["雪梨"]),
        food("葡萄", 69, 0.7, 0.2, 18.1, 0.9, 2, category="水果", default_weight=150, fruit=90),
        food("草莓", 32, 0.7, 0.3, 7.7, 2, 1, category="水果", default_weight=150, fruit=95),
        food("猕猴桃", 61, 1.1, 0.5, 14.7, 3, 3, category="水果", default_weight=120, fruit=80, aliases=["奇异果"]),
        food("蓝莓", 57, 0.7, 0.3, 14.5, 2.4, 1, category="水果", default_weight=100, fruit=95),
        food("无糖豆浆", 31, 3, 1.6, 1.2, 0.6, 4, category="奶豆", default_weight=250, aliases=["豆浆"]),
        food("豆干", 140, 16.2, 7.2, 3.6, 0.8, 490, category="奶豆", default_weight=100, aliases=["豆腐干", "香干"]),
        food("毛豆", 131, 11.9, 5.7, 10.1, 5.2, 6, category="奶豆", default_weight=120, aliases=["青豆"]),
        food("番茄炒蛋", 132, 6.8, 7.2, 8.5, 1.2, 320, category="常见菜", default_weight=180, vegetable=55, food_type="standard_dish"),
        food("清炒青菜", 70, 2.2, 3.5, 6.5, 2.8, 360, category="常见菜", default_weight=180, vegetable=85, aliases=["炒青菜"], food_type="standard_dish"),
    ]
    return foods + extended_foods()


class MemoryStore:
    def __init__(self, persistence_path: Path | None = None) -> None:
        self.persistence_path = persistence_path
        self.users: dict[str, UUID] = {}
        self.profiles: dict[UUID, Profile] = {}
        self.foods: list[Food] = seeded_foods()
        self.goal_proposals: dict[UUID, GoalProposal] = {}
        self.active_goals: dict[UUID, GoalProposal] = {}
        self.drafts: dict[UUID, MealDraft] = {}
        self.meals: dict[UUID, Meal] = {}
        self.plans: dict[UUID, MealPlan] = {}
        self.ai_actions: dict[UUID, AIAction] = {}
        self.agent_memories: dict[UUID, AgentMemory] = {}
        self.agent_traces: dict[UUID, AgentTrace] = {}
        self.agent_feedback: dict[UUID, AgentFeedback] = {}
        self.weights: dict[UUID, list[dict]] = {}
        self.sessions: dict[str, dict[str, Any]] = {}
        self._load()

    def persist(self) -> None:
        if self.persistence_path is None:
            return
        payload = {
            "version": 1,
            "users": {key: str(value) for key, value in self.users.items()},
            "profiles": [item.model_dump(mode="json") for item in self.profiles.values()],
            "custom_foods": [item.model_dump(mode="json") for item in self.foods if item.food_type == "custom"],
            "dynamic_foods": [item.model_dump(mode="json") for item in self.foods if item.source in {"open_food_facts", "user_confirmed_label"}],
            "goal_proposals": [item.model_dump(mode="json") for item in self.goal_proposals.values()],
            "active_goals": {str(profile_id): str(goal.id) for profile_id, goal in self.active_goals.items()},
            "drafts": [item.model_dump(mode="json") for item in self.drafts.values()],
            "meals": [item.model_dump(mode="json") for item in self.meals.values()],
            "plans": [item.model_dump(mode="json") for item in self.plans.values()],
            "ai_actions": [item.model_dump(mode="json") for item in self.ai_actions.values()],
            "agent_memories": [item.model_dump(mode="json") for item in self.agent_memories.values()],
            "agent_traces": [item.model_dump(mode="json") for item in self.agent_traces.values()],
            "agent_feedback": [item.model_dump(mode="json") for item in self.agent_feedback.values()],
            "weights": _json_value({str(key): value for key, value in self.weights.items()}),
            "sessions": _json_value(self.sessions),
        }
        path = self.persistence_path
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        backup = path.with_suffix(path.suffix + ".bak")
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        if path.exists():
            shutil.copy2(path, backup)
        temporary.replace(path)

    def _load(self) -> None:
        if self.persistence_path is None or not self.persistence_path.exists():
            return
        try:
            payload = json.loads(self.persistence_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            backup = self.persistence_path.with_suffix(self.persistence_path.suffix + ".bak")
            if not backup.exists():
                raise RuntimeError("本地数据文件损坏且没有可用备份")
            payload = json.loads(backup.read_text(encoding="utf-8"))

        self.users = {key: UUID(value) for key, value in payload.get("users", {}).items()}
        profiles = [Profile.model_validate(item) for item in payload.get("profiles", [])]
        self.profiles = {item.id: item for item in profiles}
        custom_foods = [Food.model_validate(item) for item in payload.get("custom_foods", [])]
        dynamic_foods = [Food.model_validate(item) for item in payload.get("dynamic_foods", [])]
        existing_food_ids = {item.id for item in self.foods}
        self.foods.extend(item for item in [*custom_foods, *dynamic_foods] if item.id not in existing_food_ids)
        proposals = [GoalProposal.model_validate(item) for item in payload.get("goal_proposals", [])]
        self.goal_proposals = {item.id: item for item in proposals}
        self.active_goals = {}
        for profile_id, goal_id in payload.get("active_goals", {}).items():
            goal = self.goal_proposals.get(UUID(goal_id))
            if goal:
                self.active_goals[UUID(profile_id)] = goal
        drafts = [MealDraft.model_validate(item) for item in payload.get("drafts", [])]
        self.drafts = {item.id: item for item in drafts}
        meals = [Meal.model_validate(item) for item in payload.get("meals", [])]
        self.meals = {item.id: item for item in meals}
        plans = [MealPlan.model_validate(item) for item in payload.get("plans", [])]
        self.plans = {item.id: item for item in plans}
        actions = [AIAction.model_validate(item) for item in payload.get("ai_actions", [])]
        self.ai_actions = {item.id: item for item in actions}
        memories = [AgentMemory.model_validate(item) for item in payload.get("agent_memories", [])]
        self.agent_memories = {item.id: item for item in memories}
        traces = [AgentTrace.model_validate(item) for item in payload.get("agent_traces", [])]
        self.agent_traces = {item.id: item for item in traces}
        feedback = [AgentFeedback.model_validate(item) for item in payload.get("agent_feedback", [])]
        self.agent_feedback = {item.trace_id: item for item in feedback}
        self.weights = {UUID(key): value for key, value in payload.get("weights", {}).items()}
        self.sessions = payload.get("sessions", {})

    def get_or_create_user(self, mock_user_key: str) -> UUID:
        if mock_user_key not in self.users:
            self.users[mock_user_key] = uuid4()
            self.persist()
        return self.users[mock_user_key]

    def get_profile(self, user_id: UUID) -> Profile:
        profile = next((item for item in self.profiles.values() if item.user_id == user_id), None)
        if profile:
            return profile
        profile = Profile(user_id=user_id)
        self.profiles[profile.id] = profile
        self.persist()
        return profile

    def meals_for(self, profile_id: UUID) -> list[Meal]:
        return [meal for meal in self.meals.values() if meal.profile_id == profile_id and meal.status == "active"]

    def memories_for(self, profile_id: UUID) -> list[AgentMemory]:
        return [memory for memory in self.agent_memories.values() if memory.profile_id == profile_id and memory.status == "active"]


def _json_value(value: Any) -> Any:
    if isinstance(value, (UUID, datetime, date)):
        return value.isoformat() if hasattr(value, "isoformat") else str(value)
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    return value


if os.getenv("DIET_DISABLE_PERSISTENCE") == "1":
    store_path = None
else:
    configured_path = os.getenv("DIET_LOCAL_STORE_PATH")
    store_path = Path(configured_path) if configured_path else Path(__file__).resolve().parent.parent / "data" / "local_store.json"

store = MemoryStore(store_path)
