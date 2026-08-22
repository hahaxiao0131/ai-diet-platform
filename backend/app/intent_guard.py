from __future__ import annotations

from pydantic import BaseModel

from .models import IntentInterpretation


RECORD_COMMANDS = ("帮我记录", "帮我记", "记一下", "记录下来", "加入早餐", "加入午餐", "加入晚餐", "加入加餐")
FORBIDDEN_MEAL_ACTION_PHRASES = (
    "可以吃吗", "可以再吃", "想吃", "想喝", "准备吃", "准备喝", "打算吃", "打算喝",
    "计划吃", "计划喝", "如果吃", "如果再吃", "再吃会不会", "会不会超标",
)


class ActionGuardResult(BaseModel):
    allowed: bool
    conflict_reason: str | None = None


def validate_action_intent(message: str, interpretation: IntentInterpretation) -> ActionGuardResult:
    normalized = message.strip()
    if interpretation.intent == "memory_preference":
        explicit = any(word in normalized for word in ["我不喜欢", "我不吃", "我喜欢", "我爱吃", "以后不要", "以后优先"])
        if interpretation.should_create_action and explicit:
            return ActionGuardResult(allowed=True)
        return ActionGuardResult(allowed=False, conflict_reason="长期偏好不是用户明确表达的事实")

    if interpretation.intent != "meal_record":
        reason = "当前意图不是已发生的记餐任务"
        if interpretation.should_create_action:
            reason += "，模型的 should_create_action 与业务意图冲突"
        return ActionGuardResult(allowed=False, conflict_reason=reason)

    explicit_command = interpretation.speech_act == "command" and (
        any(command in normalized for command in RECORD_COMMANDS)
        or ("按" in normalized and normalized.rstrip("。！!").endswith("记录"))
    )
    if explicit_command:
        return ActionGuardResult(allowed=True)

    forbidden_state = (
        interpretation.speech_act in {"question", "hypothetical"}
        or interpretation.temporal_status in {"planned", "hypothetical", "unknown"}
        or interpretation.modality in {"possible", "desired", "conditional", "unknown"}
        or any(phrase in normalized for phrase in FORBIDDEN_MEAL_ACTION_PHRASES)
    )
    if forbidden_state:
        return ActionGuardResult(allowed=False, conflict_reason="疑问、计划、愿望或假设表达禁止生成记餐动作")

    completed_statement = (
        interpretation.speech_act == "statement"
        and interpretation.temporal_status in {"completed", "current"}
        and interpretation.modality == "actual"
    )
    if completed_statement:
        return ActionGuardResult(allowed=True)
    return ActionGuardResult(allowed=False, conflict_reason="没有检测到已食用事实或明确记餐命令")
