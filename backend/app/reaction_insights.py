from __future__ import annotations

from collections import defaultdict

from .models import Meal, ReactionInsight


def build_reaction_insights(meals: list[Meal], limit: int = 5) -> list[ReactionInsight]:
    """Summarize repeated associations without claiming medical causality."""
    events: dict[tuple[str, str], list[Meal]] = defaultdict(list)
    for meal in meals:
        if meal.status != "active" or meal.body_feedback is None:
            continue
        symptoms = {item for item in meal.body_feedback.symptoms if item != "无不适"}
        foods = {item.name for item in meal.items}
        for food_name in foods:
            for symptom in symptoms:
                events[(food_name, symptom)].append(meal)

    insights: list[ReactionInsight] = []
    for (food_name, symptom), related in events.items():
        occurrences = len(related)
        if occurrences >= 3:
            level = "repeated"
            confidence = "high"
            summary = f"{food_name}后出现{symptom}已重复记录{occurrences}次，属于较稳定的时间关联，但仍不能直接证明因果。"
        elif occurrences == 2:
            level = "suspected"
            confidence = "medium"
            summary = f"{food_name}后出现{symptom}已记录2次，形成疑似关联，仍需要继续观察份量和其他同餐食物。"
        else:
            level = "observation"
            confidence = "low"
            summary = f"曾有1次在吃{food_name}后出现{symptom}，目前只是单次观察，不能判断为个人规律。"
        dates = sorted(meal.eaten_at.date() for meal in related)
        insights.append(ReactionInsight(
            food_name=food_name,
            symptom=symptom,
            occurrences=occurrences,
            evidence_level=level,
            confidence=confidence,
            first_seen=dates[0],
            last_seen=dates[-1],
            summary=summary,
            next_action=f"下次吃{food_name}时先减少单次份量并记录餐后体感；若{symptom}持续、加重或反复出现，请及时就医。",
        ))

    return sorted(
        insights,
        key=lambda item: (item.occurrences, item.last_seen, item.food_name),
        reverse=True,
    )[:limit]
