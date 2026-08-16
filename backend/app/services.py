from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Iterable
from zoneinfo import ZoneInfo

from .models import (
    Food,
    GoalProposal,
    Meal,
    MealDraft,
    MealDraftItem,
    MealItem,
    MealPlan,
    Nutrition,
    Profile,
    TodaySummary,
)


NUTRITION_VERSION = "nutrition-v1"
SCORING_VERSION = "scoring-v1"
RISK_VERSION = "risk-v1"
BUSINESS_TIMEZONE = ZoneInfo("Asia/Shanghai")


def business_date(value: datetime | None = None) -> date:
    current = value or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return current.astimezone(BUSINESS_TIMEZONE).date()


def scale_nutrition(per_100g: Nutrition, weight_g: float, ratio: float = 1) -> Nutrition:
    factor = weight_g * ratio / 100
    values = per_100g.model_dump()
    return Nutrition(**{key: (value * factor if value is not None else None) for key, value in values.items()})


def sum_nutrition(values: Iterable[Nutrition]) -> Nutrition:
    total = Nutrition(added_sugar_g=0)
    for value in values:
        for key, amount in value.model_dump().items():
            if amount is not None:
                current = getattr(total, key)
                setattr(total, key, (current or 0) + amount)
    return total


def calculate_goal(profile: Profile, old_goal: dict | None = None) -> GoalProposal:
    if not all([profile.sex, profile.age, profile.height_cm, profile.current_weight_kg, profile.activity_level, profile.primary_goal]):
        raise ValueError("PROFILE_INCOMPLETE")
    sex_offset = 5 if profile.sex == "male" else -161
    bmr = 10 * profile.current_weight_kg + 6.25 * profile.height_cm - 5 * profile.age + sex_offset
    activity = {"sedentary": 1.2, "light": 1.375, "moderate": 1.55, "high": 1.725}[profile.activity_level]
    tdee = bmr * activity
    pace = profile.goal_pace or "standard"
    rates = {
        "steady": {"fat_loss": 0.10, "muscle_gain": 0.05},
        "standard": {"fat_loss": 0.15, "muscle_gain": 0.08},
        "aggressive": {"fat_loss": 0.20, "muscle_gain": 0.12},
    }
    rate = rates[pace].get(profile.primary_goal, 0)
    calories = tdee * (1 - rate if profile.primary_goal == "fat_loss" else 1 + rate)
    protein_ratio = 0.28 if profile.primary_goal in {"fat_loss", "muscle_gain"} else 0.22
    fat_ratio = 0.28
    carbs_ratio = 1 - protein_ratio - fat_ratio
    target = Nutrition(
        energy_kcal=round(calories),
        protein_g=round(calories * protein_ratio / 4),
        fat_g=round(calories * fat_ratio / 9),
        carbs_g=round(calories * carbs_ratio / 4),
        fiber_g=25,
        sodium_mg=2000,
        added_sugar_g=25,
        vegetable_g=300,
        fruit_g=200,
    )
    reasons = [
        f"基础代谢约{round(bmr)} kcal，活动系数为{activity}。",
        f"基于“{profile.primary_goal}”和“{pace}”节奏生成推荐目标。",
        "目标仅作为建议，用户确认后才会生效。",
    ]
    return GoalProposal(
        profile_id=profile.id,
        old_goal=old_goal,
        target=target,
        pace=pace,
        goal_type=profile.primary_goal,
        reasons=reasons,
    )


def calculate_meal(items: list[MealItem]) -> Nutrition:
    return sum_nutrition(item.nutrition for item in items)


def score_meal(nutrition: Nutrition, target: Nutrition, item_names: list[str]) -> dict:
    has_protein = nutrition.protein_g >= 10 or any(name in {"鸡胸肉", "虾仁", "鸡蛋", "牛奶", "豆腐"} for name in item_names)
    has_vegetable = nutrition.vegetable_g >= 80
    has_carb = nutrition.carbs_g >= 20
    structure = 30 - (0 if has_protein else 10) - (0 if has_vegetable else 10) - (0 if has_carb else 5)
    energy_ratio = nutrition.energy_kcal / max(target.energy_kcal * 0.3, 1)
    nutrition_match = max(0, 25 - min(abs(energy_ratio - 1) * 20, 25))
    quality = min(20, 10 + (5 if has_protein else 0) + (5 if has_vegetable else 0))
    oil_salt_sugar = max(0, 15 - (5 if nutrition.sodium_mg > 900 else 0) - (5 if (nutrition.added_sugar_g or 0) > 12 else 0))
    personal = 10
    total = round(structure + nutrition_match + quality + oil_salt_sugar + personal)
    problems = []
    if not has_protein:
        problems.append("优质蛋白不足")
    if not has_vegetable:
        problems.append("蔬菜份量不足")
    if nutrition.sodium_mg > 900:
        problems.append("钠摄入偏高")
    return {
        "score": total,
        "grade": "excellent" if total >= 90 else "good" if total >= 75 else "needs_attention",
        "status": "partial",
        "confidence": "medium",
        "dimensions": {
            "structure": round(structure),
            "nutrition_match": round(nutrition_match),
            "quality": round(quality),
            "oil_salt_sugar": round(oil_salt_sugar),
            "personal": personal,
        },
        "negative_points": problems[:3],
        "next_actions": (["下一餐增加优质蛋白"] if not has_protein else [])
        + (["下一餐增加绿叶蔬菜"] if not has_vegetable else []),
        "rule_version": SCORING_VERSION,
    }


def detect_risks(profile: Profile, item_names: list[str], nutrition: Nutrition) -> list[dict]:
    risks = []
    hard = {name.lower() for name in profile.hard_exclusions + profile.allergies}
    for name in item_names:
        if name.lower() in hard:
            risks.append({"level": "block", "type": "hard_exclusion", "message": f"可能命中禁用食物：{name}"})
    if nutrition.sodium_mg > 1200:
        risks.append({"level": "notice", "type": "sodium_high", "message": "本餐钠摄入偏高"})
    if (nutrition.added_sugar_g or 0) > 15:
        risks.append({"level": "notice", "type": "added_sugar_high", "message": "本餐添加糖偏高"})
    return risks


def build_today(profile: Profile, meals: list[Meal], target: Nutrition) -> TodaySummary:
    today_date = business_date()
    active = [
        meal for meal in meals
        if meal.status == "active" and business_date(meal.eaten_at) == today_date
    ]
    consumed = sum_nutrition(meal.nutrition for meal in active)
    remaining = Nutrition(**{
        key: max(0, value - getattr(consumed, key))
        if value is not None else None
        for key, value in target.model_dump().items()
    })
    gaps = []
    if consumed.protein_g < target.protein_g * 0.6:
        gaps.append("蛋白质")
    if consumed.vegetable_g < target.vegetable_g * 0.6:
        gaps.append("蔬菜")
    if consumed.fiber_g < target.fiber_g * 0.6:
        gaps.append("膳食纤维")
    near_limits = []
    if consumed.sodium_mg >= target.sodium_mg * 0.8:
        near_limits.append("钠")
    if (consumed.added_sugar_g or 0) >= (target.added_sugar_g or 25) * 0.8:
        near_limits.append("添加糖")
    status = "empty" if not active else "partial"
    score = None
    score_status = "insufficient_data" if not active else "partial"
    confidence = "medium" if active else "low"
    if len(active) >= 3:
        status = "complete"
        score_status = "final"
        score = round(sum((meal.score or {}).get("score", 0) for meal in active) / len(active))
    return TodaySummary(
        date=today_date,
        status=status,
        score=score,
        score_status=score_status,
        confidence=confidence,
        completeness={"recorded_meals": len(active), "expected_meals": 3},
        consumed=consumed,
        target=target,
        remaining=remaining,
        gaps=gaps,
        near_limits=near_limits,
        meals=active,
    )
