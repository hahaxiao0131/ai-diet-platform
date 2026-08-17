from __future__ import annotations

from typing import Protocol

import re

from .models import Food, MealDraftItem


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


class MockAssistantProvider:
    """Deterministic V1 intent and food parser behind the future LLM boundary."""

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
        "个": {"鸡蛋": 50, "香蕉": 100},
        "只": {"鸡蛋": 50},
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

    def interpret(self, message: str, foods: list[Food]) -> dict:
        normalized = message.strip()
        if any(word in normalized for word in ["糖尿病", "高血压", "怀孕", "孕期", "厌食", "暴食", "用药", "药物", "治疗", "诊断"]):
            return {"intent": "safety", "items": [], "confidence": "high"}
        if any(word in normalized for word in ["我不喜欢", "我不吃", "不要给我", "以后不要"]):
            items = self._extract_items(normalized, foods)
            return {
                "intent": "memory_preference",
                "items": items,
                "memory_category": "avoidance",
                "memory_value": items[0]["food"].name if items else None,
                "confidence": "high" if items else "low",
            }
        if any(word in normalized for word in ["我喜欢", "我爱吃", "以后优先"]):
            items = self._extract_items(normalized, foods)
            return {
                "intent": "memory_preference",
                "items": items,
                "memory_category": "preference",
                "memory_value": items[0]["food"].name if items else None,
                "confidence": "high" if items else "low",
            }
        items = self._extract_items(normalized, foods)
        if any(word in normalized for word in ["换", "替代", "代替"]):
            return {"intent": "food_replacement", "items": items, "confidence": "medium"}
        if any(word in normalized for word in ["还能吃", "下一餐", "安排", "推荐", "吃什么"]):
            return {"intent": "plan_recommendation", "items": [], "confidence": "high"}
        if items and any(word in normalized for word in ["热量", "卡路里", "大卡", "千卡", "多少", "营养", "蛋白质", "碳水", "脂肪", "膳食纤维", "钠", "好处", "适合", "能吃", "区别", "比较", "哪个"]):
            return {"intent": "food_nutrition", "items": items, "confidence": "high"}
        if "蛋白" in normalized or ("为什么" in normalized and any(word in normalized for word in ["不足", "低", "少"] )):
            return {"intent": "protein_explanation", "items": [], "confidence": "high"}

        if items and any(word in normalized for word in ["吃", "喝", "记录", "刚刚", "早餐", "午餐", "晚餐", "加餐"]):
            confidence = "low" if any(item["confidence"] == "low" for item in items) else "medium" if any(item["confidence"] == "medium" for item in items) else "high"
            return {"intent": "meal_record", "items": items, "meal_type": self._meal_type(normalized), "confidence": confidence}
        if items or self._is_diet_question(normalized):
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
            if food.confidence == "low":
                confidence = "low"
            found.append((start, {
                "food": food,
                "weight_g": weight,
                "weight_source": source,
                "confidence": confidence,
                "assumption": assumption,
            }))
        return [item for _, item in sorted(found, key=lambda entry: entry[0])]

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
