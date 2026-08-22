from __future__ import annotations

from typing import Any

from .agent_tools import AgentToolRegistry, AgentToolResult
from .models import AgentToolCallAudit, AIChatResponse, IntentInterpretation, Nutrition
from .nutrition import sum_nutrition


def build_consumption_advice(
    message: str,
    interpretation: IntentInterpretation,
    tools: AgentToolRegistry,
) -> AIChatResponse:
    audits: list[AgentToolCallAudit] = []
    context_result = tools.execute("get_today_context", {})
    audits.append(_audit("get_today_context", {}, context_result))
    if not context_result.success:
        return _clarification("今天的营养上下文暂时不可用，请稍后再试。", audits)
    if not interpretation.foods:
        return _clarification("请告诉我计划吃什么以及大致份量。", audits)

    calculated: list[Nutrition] = []
    item_summaries: list[str] = []
    assumptions: list[str] = []
    confidence = "high"
    for entity in interpretation.foods:
        search_args = {"query": entity.raw_name, "barcode": None, "limit": 5}
        search_result = tools.execute("search_food", search_args)
        audits.append(_audit("search_food", search_args, search_result))
        if not search_result.success or not search_result.data or not search_result.data.get("matches"):
            return _clarification(f"没有在食物库中找到“{entity.raw_name}”，请补充更准确的名称或营养标签。", audits)
        if search_result.data.get("clarification_required"):
            names = "、".join(match["standard_name"] for match in search_result.data["matches"][:3])
            return _clarification(f"“{entity.raw_name}”可能指{names}，请确认具体食物。", audits)
        food = search_result.data["matches"][0]

        if entity.explicit_weight_g is not None:
            weight_g = entity.explicit_weight_g
        elif entity.quantity is not None and entity.unit is not None:
            portion_args = {"food_id": food["food_id"], "amount": entity.quantity, "unit": entity.unit}
            portion_result = tools.execute("convert_food_portion", portion_args)
            audits.append(_audit("convert_food_portion", portion_args, portion_result))
            if not portion_result.success or not portion_result.data:
                return _clarification(f"暂时无法换算{entity.quantity:g}{entity.unit}{food['standard_name']}，请补充克数。", audits)
            if portion_result.data.get("clarification_required") or portion_result.data.get("weight_g") is None:
                return _clarification(portion_result.data.get("clarification_message") or "请补充更明确的克数。", audits)
            weight_g = float(portion_result.data["weight_g"])
            if portion_result.data.get("assumption"):
                assumptions.append(portion_result.data["assumption"])
            if portion_result.data.get("confidence") != "high":
                confidence = "medium" if confidence == "high" else confidence
        else:
            return _clarification(f"请补充{food['standard_name']}的数量、单位或克数。", audits)

        nutrition_args = {"food_id": food["food_id"], "weight_g": weight_g}
        nutrition_result = tools.execute("calculate_nutrition", nutrition_args)
        audits.append(_audit("calculate_nutrition", nutrition_args, nutrition_result))
        if not nutrition_result.success or not nutrition_result.data:
            return _clarification(f"暂时无法计算{food['standard_name']}的营养，请稍后再试。", audits)
        nutrition = Nutrition.model_validate(nutrition_result.data["nutrition"])
        calculated.append(nutrition)
        item_summaries.append(f"{food['standard_name']}约 {weight_g:g}g")
        if nutrition_result.data.get("confidence") == "low":
            confidence = "low"
        elif nutrition_result.data.get("confidence") == "medium" and confidence == "high":
            confidence = "medium"

    planned = sum_nutrition(calculated)
    context = context_result.data or {}
    remaining = Nutrition.model_validate(context.get("remaining", {}))
    exceeds_energy = planned.energy_kcal > remaining.energy_kcal * 1.1
    exceeds_carbs = planned.carbs_g > remaining.carbs_g * 1.15 if remaining.carbs_g > 0 else planned.carbs_g > 0
    if exceeds_energy or exceeds_carbs:
        conclusion = "按当前已记录数据，这份计划摄入可能明显超过剩余额度，建议减少份量或换成更小份的选择。"
    else:
        conclusion = "按当前已记录数据，这份食物可以纳入今天的安排；仍要结合后续正餐和实际饥饿感控制总量。"
    if context.get("confidence") == "low":
        conclusion += " 今天的记录完整度较低，结论会随新增餐次变化。"
        confidence = "low"

    planned_name = "、".join(item_summaries)
    return AIChatResponse(
        kind="consumption_advice",
        message=(
            f"你计划吃的{planned_name}，预计约 {round(planned.energy_kcal):g} kcal，"
            f"蛋白质 {round(planned.protein_g, 1):g}g、碳水 {round(planned.carbs_g, 1):g}g。{conclusion}"
        ),
        basis=[
            f"当前剩余约 {round(remaining.energy_kcal):g} kcal、碳水 {round(remaining.carbs_g, 1):g}g",
            *assumptions,
            "计划摄入未写入今日记录",
        ],
        suggestions=["如果已经吃了，帮我记录", "查看今天还能怎么搭配"],
        confidence=confidence,
        decision_stage="inform",
        tool_calls=audits,
    )


def _clarification(message: str, audits: list[AgentToolCallAudit]) -> AIChatResponse:
    return AIChatResponse(
        kind="clarification",
        message=message,
        basis=["只有食物和份量足够明确时才计算计划摄入"],
        suggestions=[],
        confidence="low",
        decision_stage="clarify",
        needs_clarification=True,
        clarification_options=["我来补充具体食物和克数"],
        tool_calls=audits,
    )


def _audit(name: str, arguments: dict[str, Any], result: AgentToolResult) -> AgentToolCallAudit:
    return AgentToolCallAudit(
        name=name,
        effect=result.effect,
        arguments=arguments,
        success=result.success,
        result_summary="调用成功" if result.success else result.error.message if result.error else "调用失败",
        error_code=result.error.code if result.error else None,
    )
