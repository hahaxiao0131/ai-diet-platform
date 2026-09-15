from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.main import app


client = TestClient(app)
SCENARIOS = json.loads((Path(__file__).parents[1] / "evals" / "product_scenarios.json").read_text(encoding="utf-8"))


def ready_user(case_id: str) -> dict[str, str]:
    login = client.post("/api/v1/auth/mock-login", json={"mock_user_key": f"eval-{case_id}-{uuid4().hex}"})
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    client.put(
        "/api/v1/profiles/me",
        headers=headers,
        json={
            "sex": "female",
            "age": 30,
            "height_cm": 165,
            "current_weight_kg": 55,
            "activity_level": "moderate",
            "primary_goal": "structure",
            "goal_pace": "standard",
        },
    )
    goal_id = client.post("/api/v1/goals/recalculate", headers=headers).json()["data"]["id"]
    client.post(f"/api/v1/goals/{goal_id}/confirm", headers=headers)
    return headers


@pytest.mark.parametrize("scenario", SCENARIOS, ids=[item["id"] for item in SCENARIOS])
def test_realistic_product_scenario_completes_with_expected_safety_boundary(scenario):
    response = client.post(
        "/api/v1/ai/chat",
        headers=ready_user(scenario["id"]),
        json={"message": scenario["message"]},
    )

    assert response.status_code == 200
    result = response.json()["data"]
    assert result["kind"] == scenario["kind"]
    assert result["decision_stage"] == scenario["stage"]
    assert (result["action"] is not None) is scenario["action"]
    assert result["trace_id"]
    if "confidence" in scenario:
        assert result["confidence"] == scenario["confidence"]
    if result["action"] is not None:
        assert result["action"]["status"] == "proposed"
        payload = result["action"]["payload"]
        if "meal_type" in scenario:
            assert payload["meal_type"] == scenario["meal_type"]
        if "item_name" in scenario:
            assert payload["items"][0]["name"] == scenario["item_name"]
        if "weight_g" in scenario:
            assert payload["items"][0]["weight_g"] == pytest.approx(scenario["weight_g"])
