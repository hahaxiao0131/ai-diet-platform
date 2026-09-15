from __future__ import annotations

from datetime import datetime, timedelta
import json
from types import SimpleNamespace
from uuid import uuid4

from fastapi.testclient import TestClient

from app import main as main_module
from app.assessment import ModelAssessmentProvider
from app.main import app
from app.services import business_date


client = TestClient(app)


def test_deepseek_assessment_uses_non_thinking_json_mode_and_pydantic_schema():
    payload = {
        "overall_score": 70,
        "general_analysis": ["食物种类仍可增加。"],
        "personalized_analysis": ["当前个人体感数据较少。"],
        "optimization_suggestions": ["下一餐增加蔬菜。"],
        "nutrition_estimates": [],
        "evidence": ["测试依据"],
        "confidence": "medium",
    }

    class FakeCompletions:
        def __init__(self):
            self.kwargs = None

        def create(self, **kwargs):
            self.kwargs = kwargs
            message = SimpleNamespace(content=json.dumps(payload, ensure_ascii=False))
            return SimpleNamespace(choices=[SimpleNamespace(message=message)])

    completions = FakeCompletions()
    provider = ModelAssessmentProvider(
        "deepseek",
        "test-model",
        SimpleNamespace(chat=SimpleNamespace(completions=completions)),
        30,
    )

    result = provider.assess("不含用户数据的测试")

    assert result.overall_score == 70
    assert completions.kwargs["extra_body"] == {"thinking": {"type": "disabled"}}
    assert completions.kwargs["response_format"] == {"type": "json_object"}
    assert "overall_score" in completions.kwargs["messages"][1]["content"]


def setup_ready_user(key: str):
    login = client.post("/api/v1/auth/mock-login", json={"mock_user_key": key})
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    client.put(
        "/api/v1/profiles/me",
        json={
            "sex": "female",
            "age": 30,
            "height_cm": 165,
            "current_weight_kg": 50,
            "activity_level": "moderate",
            "primary_goal": "muscle_gain",
            "goal_pace": "standard",
            "food_intolerances": ["乳糖不耐受"],
            "digestive_sensitivities": ["大量玉米容易腹泻", "大量螃蟹容易肠胃不适"],
            "exercise_habits": ["每周打网球"],
        },
        headers=headers,
    )
    proposal_id = client.post("/api/v1/goals/recalculate", headers=headers).json()["data"]["id"]
    client.post(f"/api/v1/goals/{proposal_id}/confirm", headers=headers)
    return headers


def test_profile_feedback_trend_and_structured_assessment(monkeypatch):
    headers = setup_ready_user(f"health-assessment-{uuid4().hex}")
    monkeypatch.setattr(main_module, "assessment_provider", None)
    profile = client.get("/api/v1/profiles/me", headers=headers).json()["data"]
    assert profile["food_intolerances"] == ["乳糖不耐受"]
    assert profile["digestive_sensitivities"][0] == "大量玉米容易腹泻"
    assert profile["exercise_habits"] == ["每周打网球"]

    crab = client.get("/api/v1/foods/search?q=螃蟹", headers=headers).json()["data"][0]
    meal = client.post(
        "/api/v1/meals/manual",
        headers=headers,
        json={
            "meal_type": "lunch",
            "items": [{"food_id": crab["id"], "weight_g": 300, "consumed_ratio": 1}],
            "body_feedback": {
                "symptoms": ["腹泻"],
                "severity": "moderate",
                "occurred_after_minutes": 60,
                "notes": "一次吃得比较多",
            },
        },
    ).json()["data"]
    assert meal["body_feedback"]["symptoms"] == ["腹泻"]

    trend = client.get("/api/v1/trends/7-days", headers=headers).json()["data"]
    assert trend["total_meals"] == 1
    assert trend["food_variety"] == 1
    assert trend["feedback_completion_rate"] == 100
    assert "腹泻" in trend["common_symptoms"]
    assert trend["reaction_insights"][0]["evidence_level"] == "observation"
    assert "单次观察" in trend["reaction_insights"][0]["summary"]

    assessment = client.post(
        "/api/v1/ai/assess",
        headers=headers,
        json={"meal_id": meal["id"]},
    ).json()["data"]
    assert 0 <= assessment["overall_score"] <= 100
    assert assessment["general_analysis"]
    assert assessment["personalized_analysis"]
    assert any("体感" in item or "不耐受" in item or "肠胃" in item for item in assessment["personalized_analysis"])
    assert assessment["nutrition_estimates"]
    assert all(isinstance(item["minimum"], int) and isinstance(item["maximum"], int) for item in assessment["nutrition_estimates"])
    assert all(item["minimum"] <= item["maximum"] for item in assessment["nutrition_estimates"])
    assert assessment["disclaimer"].startswith("本结果仅供健康参考")
    assert "【通用膳食分析】" in assessment["markdown"]
    assert "【你的个人专属提示】" in assessment["markdown"]


def test_natural_language_meal_keeps_body_feedback_behind_confirmation():
    headers = setup_ready_user(f"health-natural-{uuid4().hex}")
    response = client.post(
        "/api/v1/ai/chat",
        headers=headers,
        json={"message": "昨天吃了10只大闸蟹，之后拉稀"},
    ).json()["data"]
    assert response["kind"] == "meal_record_proposal"
    assert response["action"]["payload"]["body_feedback"]["symptoms"] == ["腹泻"]
    assert response["action"]["payload"]["items"][0]["estimated_weight_range_g"] == [325, 675]
    assert business_date(datetime.fromisoformat(response["action"]["payload"]["eaten_at"])) == business_date() - timedelta(days=1)
    assert client.get("/api/v1/trends/7-days", headers=headers).json()["data"]["total_meals"] == 0

    confirmed = client.post(
        f"/api/v1/ai/actions/{response['action']['id']}/confirm",
        headers=headers,
    ).json()["data"]["meal"]
    assert confirmed["body_feedback"]["symptoms"] == ["腹泻"]
    assert business_date(datetime.fromisoformat(confirmed["eaten_at"])) == business_date() - timedelta(days=1)


def test_repeated_food_reaction_builds_cautious_evidence_and_one_weekly_action(monkeypatch):
    headers = setup_ready_user(f"health-reaction-evidence-{uuid4().hex}")
    monkeypatch.setattr(main_module, "assessment_provider", None)
    crab = client.get("/api/v1/foods/search?q=螃蟹", headers=headers).json()["data"][0]

    for days_ago in [5, 3, 1]:
        eaten_at = datetime.now().astimezone() - timedelta(days=days_ago)
        response = client.post(
            "/api/v1/meals/manual",
            headers=headers,
            json={
                "meal_type": "dinner",
                "eaten_at": eaten_at.isoformat(),
                "items": [{"food_id": crab["id"], "weight_g": 250, "consumed_ratio": 1}],
                "body_feedback": {
                    "symptoms": ["腹泻"],
                    "severity": "moderate",
                    "occurred_after_minutes": 90,
                },
            },
        )
        assert response.status_code == 200

    trend = client.get("/api/v1/trends/7-days", headers=headers).json()["data"]
    insight = trend["reaction_insights"][0]
    assert insight["food_name"] == "螃蟹"
    assert insight["symptom"] == "腹泻"
    assert insight["occurrences"] == 3
    assert insight["evidence_level"] == "repeated"
    assert "不能直接证明因果" in insight["summary"]
    assert trend["focus_action"] == insight["next_action"]

    assessment = client.post(
        "/api/v1/ai/assess",
        headers=headers,
        json={"days": 7},
    ).json()["data"]
    assert any("重复记录3次" in item for item in assessment["personalized_analysis"])
    assert any("不能直接证明因果" in item for item in assessment["evidence"])
