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
            for name, weight in option["items"]:
                food = lookup[name]
                items.append({"food": food, "weight_g": weight})
            result.append({**option, "items": items, "remaining": remaining})
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
        "碗": {"米饭": 150},
        "杯": {"牛奶": 250},
        "盒": {"牛奶": 250},
        "份": {},
    }
    chinese_numbers = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10, "半": 0.5}

    def interpret(self, message: str, foods: list[Food]) -> dict:
        normalized = message.strip()
        if any(word in normalized for word in ["换", "替代", "代替"]):
            return {"intent": "food_replacement", "items": self._extract_items(normalized, foods)}
        if any(word in normalized for word in ["还能吃", "下一餐", "安排", "推荐", "吃什么"]):
            return {"intent": "plan_recommendation", "items": []}
        if "蛋白" in normalized or ("为什么" in normalized and any(word in normalized for word in ["不足", "低", "少"] )):
            return {"intent": "protein_explanation", "items": []}

        items = self._extract_items(normalized, foods)
        if items and any(word in normalized for word in ["吃", "喝", "记录", "刚刚", "早餐", "午餐", "晚餐", "加餐"]):
            return {"intent": "meal_record", "items": items, "meal_type": self._meal_type(normalized)}
        return {"intent": "clarification", "items": items}

    def _extract_items(self, message: str, foods: list[Food]) -> list[dict]:
        found: list[tuple[int, dict]] = []
        for food in foods:
            names = [food.name, *food.aliases]
            match = next((re.search(re.escape(name), message, re.IGNORECASE) for name in names if re.search(re.escape(name), message, re.IGNORECASE)), None)
            if not match:
                continue
            weight = self._weight_from_message(message, match.start(), match.end(), food)
            found.append((match.start(), {"food": food, "weight_g": weight}))
        return [item for _, item in sorted(found, key=lambda entry: entry[0])]

    def _weight_from_message(self, message: str, start: int, end: int, food: Food) -> float:
        before = message[max(0, start - 14):start]
        after = message[end:min(len(message), end + 14)]
        direct = re.search(r"(\d+(?:\.\d+)?)\s*(?:g|克|ml|毫升)\s*$", before, re.IGNORECASE)
        if not direct:
            direct = re.match(r"^\s*(\d+(?:\.\d+)?)\s*(?:g|克|ml|毫升)", after, re.IGNORECASE)
        if direct:
            return float(direct.group(1))
        unit = re.search(r"([一二两三四五六七八九十半\d]+)\s*(个|只|碗|杯|盒|份)\s*$", before)
        if not unit:
            unit = re.match(r"^\s*([一二两三四五六七八九十半\d]+)\s*(个|只|碗|杯|盒|份)", after)
        if unit:
            raw_count = unit.group(1)
            count = float(raw_count) if raw_count.isdigit() else self.chinese_numbers.get(raw_count, 1)
            per_unit = self.unit_weights.get(unit.group(2), {}).get(food.name)
            if per_unit:
                return count * per_unit
        return food.default_weight_g or self.default_weights.get(food.name, 100)

    @staticmethod
    def _meal_type(message: str) -> str:
        if "早餐" in message or "早上" in message:
            return "breakfast"
        if "晚餐" in message or "晚上" in message:
            return "dinner"
        if "加餐" in message or "零食" in message:
            return "snack"
        return "lunch"
