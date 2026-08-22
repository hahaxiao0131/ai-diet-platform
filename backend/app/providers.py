from __future__ import annotations

from typing import Protocol

import re

from .models import Food, IntentFoodEntity, IntentInterpretation, MealDraftItem


class VisionProvider(Protocol):
    def analyze_meal(self, assets: list[dict[str, str]], foods: list[Food]) -> list[MealDraftItem]:
        ...


class AdviceProvider(Protocol):
    def propose(self, context: dict, foods: list[Food]) -> list[dict]:
        ...


class AssistantProvider(Protocol):
    def interpret(self, message: str, foods: list[Food]) -> dict:
        ...


class MockVisionProvider:
    def analyze_meal(self, assets: list[dict[str, str]], foods: list[Food]) -> list[MealDraftItem]:
        lookup = {food.name: food for food in foods}
        suggestions = [
            ("米饭", 180, "1碗"),
            ("番茄炒蛋", 180, "1份"),
            ("清炒青菜", 150, "1盘"),
        ]
        items: list[MealDraftItem] = []
        for name, weight, unit in suggestions:
            food = lookup[name]
            items.append(
                MealDraftItem(
                    food_id=food.id,
                    name=name,
                    estimated_weight_g=weight,
                    household_unit=unit,
                    confidence="medium",
                    nutrition=food.nutrition_per_100g,
                )
            )
        return items


class MockAdviceProvider:
    def propose(self, context: dict, foods: list[Food]) -> list[dict]:
        lookup = {food.name: food for food in foods}
        remaining = context["remaining"]
        avoidances = set(context.get("avoidances", []))
        replacement_map = {
            "鸡胸肉": ["虾仁", "豆腐", "鸡蛋"],
            "虾仁": ["鸡胸肉", "豆腐", "鸡蛋"],
            "牛奶": ["无糖豆浆", "原味无糖酸奶", "鸡蛋"],
            "鸡蛋": ["豆腐", "虾仁", "鸡胸肉"],
            "米饭": ["糙米饭", "红薯", "燕麦"],
            "香蕉": ["苹果", "橙子", "蓝莓"],
        }
        options = [
            {
                "title": "家常均衡版",
                "scenario": "home",
                "items": [("鸡胸肉", 150), ("米饭", 120), ("清炒青菜", 200)],
                "reason": "优先补足蛋白质和蔬菜，份量清晰，适合正常做饭。",
            },
            {
                "title": "10分钟快手版",
                "scenario": "quick",
                "items": [("虾仁", 140), ("燕麦", 60), ("牛奶", 250)],
                "reason": "准备时间短，优先补充蛋白质和可执行的碳水。",
            },
            {
                "title": "便利快捷版",
                "scenario": "convenience",
                "items": [("牛奶", 250), ("鸡蛋", 100), ("香蕉", 100)],
                "reason": "无需复杂烹饪，适合时间紧张的场景。",
            },
        ]
        result = []
        for option in options:
            items = []
            applied_avoidances: list[str] = []
            for name, weight in option["items"]:
                if name in avoidances:
                    replacement = next((candidate for candidate in replacement_map.get(name, []) if candidate not in avoidances and candidate in lookup), None)
                    if replacement:
                        applied_avoidances.append(name)
                        name = replacement
                food = lookup[name]
                items.append({"food": food, "weight_g": weight})
            reason = option["reason"]
            if applied_avoidances:
                reason += f" 已按你的偏好避开{'、'.join(applied_avoidances)}。"
            result.append({**option, "reason": reason, "items": items, "remaining": remaining})
        return result


SAFETY_KEYWORDS = (
    "糖尿病", "高血压", "怀孕", "孕期", "哺乳", "厌食", "暴食", "进食障碍",
    "用药", "药物", "治疗", "诊断", "处方", "胰岛素",
)


def is_safety_message(message: str) -> bool:
    return any(word in message.strip() for word in SAFETY_KEYWORDS)


class RuleBasedAssistantProvider:
    """Deterministic interpreter used for local mode, tests and model fallback."""

    provider_name = "rule"

    default_weights = {
        "米饭": 150,
        "番茄炒蛋": 180,
        "清炒青菜": 180,
        "鸡胸肉": 150,
        "虾仁": 150,
        "鸡蛋": 50,
        "豆腐": 150,
        "牛奶": 250,
        "燕麦": 50,
        "香蕉": 100,
    }
    unit_weights = {
        "个": {"鸡蛋": 50, "香蕉": 100, "小笼包": 30},
        "只": {"鸡蛋": 50, "螃蟹": 50},
        "枚": {"鸡蛋": 50, "鹌鹑蛋": 10},
        "颗": {"圣女果": 15, "葡萄": 8, "草莓": 18, "蓝莓": 2, "鲜枣": 20, "龙眼": 10, "旺仔QQ糖": 3},
        "片": {"全麦面包": 35, "奶酪": 20},
        "根": {"香蕉": 100, "玉米": 180, "胡萝卜": 100},
        "勺": {"食用油": 10, "白砂糖": 5, "芝麻酱": 15},
        "块": {"豆腐": 100, "内酯豆腐": 100},
        "碗": {"米饭": 150},
        "杯": {"牛奶": 250},
        "盒": {"牛奶": 250},
        "份": {},
    }
    chinese_numbers = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10, "半": 0.5}

    def interpret_structured(self, message: str, foods: list[Food]) -> IntentInterpretation:
        normalized = message.strip()
        items = self._extract_items(normalized, foods)
        entities = [IntentFoodEntity(
            raw_name=item["raw_name"],
            quantity=item["quantity"],
            unit=item["unit"],
            explicit_weight_g=item["explicit_weight_g"],
        ) for item in items]
        speech_act = self._speech_act(normalized)
        temporal_status = self._temporal_status(normalized)
        modality = self._modality(normalized, temporal_status)

        if is_safety_message(normalized):
            intent = "safety"
        elif any(word in normalized for word in ["我不喜欢", "我不吃", "不要给我", "以后不要", "我喜欢", "我爱吃", "以后优先"]):
            intent = "memory_preference"
        elif any(word in normalized for word in ["换", "替代", "代替"]):
            intent = "food_replacement"
        elif entities and any(word in normalized for word in ["热量", "卡路里", "大卡", "千卡", "多少", "营养", "蛋白质", "碳水", "脂肪", "膳食纤维", "钠", "区别", "比较", "哪个"]):
            intent = "food_nutrition"
        elif entities and (
            temporal_status in {"planned", "hypothetical"}
            or modality in {"possible", "desired", "conditional"}
            or (speech_act == "question" and any(word in normalized for word in ["吃", "喝", "超标", "适合"]))
        ):
            intent = "consumption_advice"
        elif any(word in normalized for word in ["还能吃", "下一餐", "安排", "推荐", "吃什么"]):
            intent = "plan_recommendation"
        elif "蛋白" in normalized or ("为什么" in normalized and any(word in normalized for word in ["不足", "低", "少"])):
            intent = "protein_explanation"
        elif entities and (
            (temporal_status in {"completed", "current"} and speech_act == "statement")
            or (speech_act == "command" and self._has_record_command(normalized))
        ):
            intent = "meal_record"
        elif not entities and self._is_diet_question(normalized):
            intent = "dietary_knowledge"
        else:
            intent = "clarification"

        should_create_action = (
            intent == "memory_preference"
            or (
                intent == "meal_record"
                and (
                    (temporal_status in {"completed", "current"} and speech_act == "statement")
                    or (speech_act == "command" and self._has_record_command(normalized))
                )
            )
        )
        requires_clarification = intent == "clarification" or (intent == "meal_record" and not entities)
        return IntentInterpretation(
            intent=intent,
            speech_act=speech_act,
            temporal_status=temporal_status,
            modality=modality,
            foods=entities,
            meal_type=self._explicit_meal_type(normalized),
            should_create_action=should_create_action,
            requires_clarification=requires_clarification,
            clarification_question="请说明这是已经吃过、准备吃，还是想查询营养。" if requires_clarification else None,
            confidence="high" if intent not in {"clarification", "dietary_knowledge"} else "low" if intent == "clarification" else "medium",
        )

    def interpret(self, message: str, foods: list[Food]) -> dict:
        normalized = message.strip()
        structured = self.interpret_structured(normalized, foods)
        if structured.intent == "safety":
            return {"intent": "safety", "items": [], "confidence": "high"}
        if structured.intent == "memory_preference" and any(word in normalized for word in ["我不喜欢", "我不吃", "不要给我", "以后不要"]):
            items = self._extract_items(normalized, foods)
            return {
                "intent": "memory_preference",
                "items": items,
                "memory_category": "avoidance",
                "memory_value": items[0]["food"].name if items else None,
                "confidence": "high" if items else "low",
            }
        if structured.intent == "memory_preference":
            items = self._extract_items(normalized, foods)
            return {
                "intent": "memory_preference",
                "items": items,
                "memory_category": "preference",
                "memory_value": items[0]["food"].name if items else None,
                "confidence": "high" if items else "low",
            }
        items = self._extract_items(normalized, foods)
        if structured.intent == "food_replacement":
            return {"intent": "food_replacement", "items": items, "confidence": "medium"}
        if structured.intent == "plan_recommendation":
            return {"intent": "plan_recommendation", "items": [], "confidence": "high"}
        if structured.intent == "food_nutrition":
            return {"intent": "food_nutrition", "items": items, "confidence": "high"}
        if structured.intent == "protein_explanation":
            return {"intent": "protein_explanation", "items": [], "confidence": "high"}
        if structured.intent == "consumption_advice":
            return {"intent": "consumption_advice", "items": items, "confidence": structured.confidence}
        if structured.intent == "meal_record":
            confidence = "low" if any(item["confidence"] == "low" for item in items) else "medium" if any(item["confidence"] == "medium" for item in items) else "high"
            return {"intent": "meal_record", "items": items, "meal_type": self._meal_type(normalized), "confidence": confidence}
        if structured.intent == "dietary_knowledge":
            return {"intent": "dietary_knowledge", "items": items, "confidence": "medium"}
        return {"intent": "clarification", "items": items, "confidence": "low"}

    def _extract_items(self, message: str, foods: list[Food]) -> list[dict]:
        candidates: list[tuple[int, int, Food]] = []
        for food in foods:
            names = [food.name, *food.aliases]
            matches = [match for name in names for match in re.finditer(re.escape(name), message, re.IGNORECASE)]
            if not matches:
                continue
            match = min(matches, key=lambda entry: (entry.start(), -(entry.end() - entry.start())))
            candidates.append((match.start(), match.end(), food))

        found: list[tuple[int, dict]] = []
        occupied: list[tuple[int, int]] = []
        for start, end, food in sorted(candidates, key=lambda entry: (entry[0], -(entry[1] - entry[0]))):
            if any(start < used_end and end > used_start for used_start, used_end in occupied):
                continue
            occupied.append((start, end))
            weight, source, confidence, assumption = self._weight_from_message(message, start, end, food)
            quantity, unit, explicit_weight_g = self._portion_entity(message, start, end)
            if food.confidence == "low":
                confidence = "low"
            found.append((start, {
                "food": food,
                "raw_name": message[start:end],
                "quantity": quantity,
                "unit": unit,
                "explicit_weight_g": explicit_weight_g,
                "weight_g": weight,
                "weight_source": source,
                "confidence": confidence,
                "assumption": assumption,
            }))
        return [item for _, item in sorted(found, key=lambda entry: entry[0])]

    def _portion_entity(self, message: str, start: int, end: int) -> tuple[float | None, str | None, float | None]:
        before = message[max(0, start - 14):start]
        after = message[end:min(len(message), end + 14)]
        direct = re.search(r"(\d+(?:\.\d+)?)\s*(g|克|ml|毫升)\s*$", before, re.IGNORECASE)
        if not direct:
            direct = re.match(r"^\s*(\d+(?:\.\d+)?)\s*(g|克|ml|毫升)", after, re.IGNORECASE)
        if direct:
            amount = float(direct.group(1))
            return None, direct.group(2), amount
        unit = re.search(r"([一二两三四五六七八九十百半\d]+)\s*(个|只|枚|颗|片|根|勺|块|碗|杯|盒|份)\s*$", before)
        if not unit:
            unit = re.match(r"^\s*([一二两三四五六七八九十百半\d]+)\s*(个|只|枚|颗|片|根|勺|块|碗|杯|盒|份)", after)
        if unit:
            return self._parse_count(unit.group(1)), unit.group(2), None
        return None, None, None

    def _weight_from_message(self, message: str, start: int, end: int, food: Food) -> tuple[float, str, str, str | None]:
        before = message[max(0, start - 14):start]
        after = message[end:min(len(message), end + 14)]
        direct = re.search(r"(\d+(?:\.\d+)?)\s*(?:g|克|ml|毫升)\s*$", before, re.IGNORECASE)
        if not direct:
            direct = re.match(r"^\s*(\d+(?:\.\d+)?)\s*(?:g|克|ml|毫升)", after, re.IGNORECASE)
        if direct:
            return float(direct.group(1)), "explicit_weight", "high", None
        unit = re.search(r"([一二两三四五六七八九十百半\d]+)\s*(个|只|枚|颗|片|根|勺|块|碗|杯|盒|份)\s*$", before)
        if not unit:
            unit = re.match(r"^\s*([一二两三四五六七八九十百半\d]+)\s*(个|只|枚|颗|片|根|勺|块|碗|杯|盒|份)", after)
        if unit:
            raw_count = unit.group(1)
            count = self._parse_count(raw_count)
            per_unit = self.unit_weights.get(unit.group(2), {}).get(food.name)
            if per_unit:
                weight = count * per_unit
                if food.name == "螃蟹" and unit.group(2) == "只":
                    return weight, "household_unit", "low", "螃蟹按每只可食部分约 50g 估算，个体大小和可食率差异较大"
                return weight, "household_unit", "medium", f"{food.name}按每{unit.group(2)}约 {per_unit:g}g 换算"
        weight = food.default_weight_g or self.default_weights.get(food.name, 100)
        return weight, "default_estimate", "low", f"{food.name}暂按常用份量 {weight:g}g 估算"

    @staticmethod
    def _meal_type(message: str) -> str:
        if "早餐" in message or "早上" in message:
            return "breakfast"
        if "晚餐" in message or "晚上" in message:
            return "dinner"
        if "加餐" in message or "零食" in message:
            return "snack"
        return "lunch"

    @staticmethod
    def _explicit_meal_type(message: str) -> str | None:
        if "早餐" in message or "早上" in message:
            return "breakfast"
        if "午餐" in message or "中午" in message:
            return "lunch"
        if "晚餐" in message or "晚上" in message:
            return "dinner"
        if "加餐" in message or "零食" in message:
            return "snack"
        return None

    @staticmethod
    def _speech_act(message: str) -> str:
        if any(word in message for word in ["如果", "假如", "要是"]):
            return "hypothetical"
        if RuleBasedAssistantProvider._has_record_command(message):
            return "command"
        if any(word in message for word in ["吗", "？", "?", "可不可以", "能不能", "能否", "是否", "会不会"]):
            return "question"
        return "statement"

    @staticmethod
    def _temporal_status(message: str) -> str:
        if any(word in message for word in ["如果", "假如", "要是"]):
            return "hypothetical"
        if any(word in message for word in ["已经吃", "已经喝", "刚吃", "刚刚吃", "吃了", "喝了"]):
            return "completed"
        if any(word in message for word in ["正在吃", "正在喝"]):
            return "current"
        if any(word in message for word in ["想吃", "想喝", "准备吃", "准备喝", "打算吃", "打算喝", "计划吃", "计划喝", "待会吃"]) or re.search(r"(?:准备|打算|计划).{0,8}(?:吃|喝)", message):
            return "planned"
        return "unknown"

    @staticmethod
    def _modality(message: str, temporal_status: str) -> str:
        if temporal_status in {"completed", "current"} or RuleBasedAssistantProvider._has_record_command(message):
            return "actual"
        if temporal_status == "hypothetical":
            return "conditional"
        if temporal_status == "planned" or any(word in message for word in ["想", "准备", "打算", "计划"]):
            return "desired"
        if any(word in message for word in ["可以", "能不能", "能否", "会不会", "是否", "超标"]):
            return "possible"
        return "unknown"

    @staticmethod
    def _has_record_command(message: str) -> bool:
        return any(word in message for word in ["帮我记录", "帮我记", "记一下", "记录下来", "加入早餐", "加入午餐", "加入晚餐", "加入加餐"]) or ("按" in message and message.rstrip("。！!").endswith("记录"))

    @staticmethod
    def _is_diet_question(message: str) -> bool:
        return any(word in message for word in [
            "饮食", "营养", "食物", "食品", "减脂", "减肥", "增肌", "主食", "碳水",
            "蛋白", "脂肪", "早餐", "午餐", "晚餐", "夜宵", "水果", "蔬菜", "喝水",
            "饮水", "配料表", "成分表", "怎么吃", "吃多少", "健康",
        ])

    @classmethod
    def _parse_count(cls, raw: str) -> float:
        if raw.isdigit():
            return float(raw)
        if raw == "半":
            return 0.5
        digits = {key: int(value) for key, value in cls.chinese_numbers.items() if key != "半"}
        if "百" in raw:
            hundreds, remainder = raw.split("百", 1)
            value = digits.get(hundreds, 1) * 100
            return value + cls._parse_count(remainder) if remainder else float(value)
        if "十" in raw:
            tens, ones = raw.split("十", 1)
            value = digits.get(tens, 1) * 10
            return float(value + digits.get(ones, 0))
        return float(digits.get(raw, 1))


# Backwards-compatible name retained for existing imports and tests.
MockAssistantProvider = RuleBasedAssistantProvider
