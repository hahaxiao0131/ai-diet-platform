from __future__ import annotations

from typing import Any

from .models import AgentContext, AgentMemory, TodaySummary
from .nutrition import scale_nutrition


def build_agent_context(today: TodaySummary, memories: list[AgentMemory]) -> AgentContext:
    recorded = today.completeness["recorded_meals"]
    expected = today.completeness["expected_meals"]
    missing_data: list[str] = []
    if recorded == 0:
        missing_data.append("今天还没有已确认的餐食记录")
    elif recorded < expected:
        missing_data.append(f"今天还有 {expected - recorded} 个基础餐次未记录")
    if today.confidence != "high":
        missing_data.append("当前结论会随新增记录更新")

    labels = {"preference": "喜欢", "avoidance": "避免", "habit": "习惯"}
    return AgentContext(
        recorded_meals=recorded,
        expected_meals=expected,
        remaining_energy_kcal=max(0, round(today.remaining.energy_kcal, 1)),
        remaining_protein_g=max(0, round(today.remaining.protein_g, 1)),
        remaining_fiber_g=max(0, round(today.remaining.fiber_g, 1)),
        gaps=today.gaps,
        near_limits=today.near_limits,
        data_confidence=today.confidence,
        missing_data=missing_data,
        active_memories=[f"{labels[memory.category]}：{memory.value}" for memory in memories],
    )


def portion_clarification_options(items: list[dict]) -> list[str]:
    estimated = "、".join(f"{item['food'].name}{item['weight_g']:g}克" for item in items)
    return [f"按{estimated}记录", "我来补充每种食物的克数"]


def answer_food_question(message: str, items: list[dict[str, Any]]) -> dict[str, Any]:
    compare = len(items) > 1 and any(word in message for word in ["比", "比较", "哪个", "区别", "更高", "更低"])
    nutrient = next((entry for entry in [
        ("protein_g", "蛋白质", "g"),
        ("carbs_g", "碳水", "g"),
        ("fat_g", "脂肪", "g"),
        ("fiber_g", "膳食纤维", "g"),
        ("sodium_mg", "钠", "mg"),
    ] if entry[1] in message), ("energy_kcal", "热量", "kcal"))

    if compare:
        use_given_portions = any(item["weight_source"] != "default_estimate" for item in items)
        values = []
        for item in items:
            weight = item["weight_g"] if use_given_portions and item["weight_source"] != "default_estimate" else 100
            nutrition = scale_nutrition(item["food"].nutrition_per_100g, weight)
            values.append((item["food"].name, weight, getattr(nutrition, nutrient[0])))
        scope = "按你给出的份量" if use_given_portions else "按每 100g"
        details = "；".join(f"{name} {weight:g}g 约 {round(value, 1):g}{nutrient[2]}" for name, weight, value in values)
        highest = max(values, key=lambda entry: entry[2])
        return {
            "message": f"{scope}比较：{details}。其中 {highest[0]} 的{nutrient[1]}更高。",
            "basis": ["比较时统一份量口径", "数值来自当前食物营养数据库"],
            "confidence": "high" if all(item["food"].confidence == "high" for item in items) else "medium",
        }

    item = items[0]
    food = item["food"]
    has_portion = item["weight_source"] != "default_estimate"
    weight = item["weight_g"] if has_portion else 100
    nutrition = scale_nutrition(food.nutrition_per_100g, weight)
    portion = f"{weight:g}g" if has_portion else "每 100g"
    asks_sugar = any(word in message for word in ["糖分", "糖含量", "含糖", "多少糖"])
    if asks_sugar and nutrition.sugars_g is not None:
        basis = [f"{food.name}数据源直接提供每 100g 糖 {food.nutrition_per_100g.sugars_g:g}g"]
        if item.get("assumption"):
            basis.append(item["assumption"] + f"，本次约 {weight:g}g")
        return {
            "message": f"{portion}{food.name}约含糖 {round(nutrition.sugars_g, 1):g}g。",
            "basis": basis,
            "confidence": "high" if food.verified_by_user else food.confidence,
        }
    if asks_sugar and nutrition.added_sugar_g is None:
        basis = [
            f"{food.name}常见标签每 100g 碳水约 {food.nutrition_per_100g.carbs_g:g}g",
            "标签未直接提供糖含量，碳水化合物不能等同于糖",
        ]
        if item.get("assumption"):
            basis.append(item["assumption"] + f"，本次约 {weight:g}g")
        return {
            "message": f"无法仅凭当前标签准确算出糖分。{portion}{food.name}约含 {round(nutrition.carbs_g, 1):g}g 碳水，但其中多少属于糖需要看具体包装的糖含量标示。",
            "basis": basis,
            "confidence": "low",
        }
    message_text = (
        f"{portion}{food.name}约含 {round(nutrition.energy_kcal):g} kcal，"
        f"蛋白质 {round(nutrition.protein_g, 1):g}g、碳水 {round(nutrition.carbs_g, 1):g}g、"
        f"脂肪 {round(nutrition.fat_g, 1):g}g。"
    )
    basis = [f"数据库口径：{food.name}每 100g 约 {round(food.nutrition_per_100g.energy_kcal):g} kcal"]
    if item.get("assumption"):
        basis.append(item["assumption"] + f"，本次约 {weight:g}g")
    confidence = "high" if has_portion and item["weight_source"] == "explicit_weight" and food.confidence == "high" else "medium"
    if "（干）" in food.name:
        basis.append("这里按未煮的干重计算；若 200g 指煮熟后的重量，应改用熟面条数据")
    elif "（熟）" in food.name:
        basis.append("这里按煮熟后的可食重量计算，不能与干重直接比较")
    if food.food_type in {"standard_dish", "packaged"}:
        basis.append("菜谱、品牌和调味差异会使实际数值变化，包装食品优先以标签为准")
        confidence = "medium"
    return {"message": message_text, "basis": basis, "confidence": confidence}


def answer_general_diet_question(message: str) -> dict[str, Any]:
    if any(word in message for word in ["营养成分表", "配料表", "食品标签"]):
        answer = "看食品标签时先确认口径是每 100g 还是每份，再看能量、蛋白质、脂肪、碳水、钠和添加糖。比较同类产品时统一到每 100g，配料表越靠前通常用量越多。"
        basis = ["先统一计量口径再比较", "包装食品以实物标签为准"]
    elif "减脂" in message and any(word in message for word in ["主食", "碳水", "米饭", "面"]):
        answer = "减脂不需要完全戒掉主食。更重要的是总能量、份量和搭配：保留适量主食，同时搭配足量蔬菜和一份优质蛋白，通常比只吃菜更容易长期坚持。"
        basis = ["体重变化主要受长期能量平衡影响", "主食份量需要结合全天摄入判断"]
    elif any(word in message for word in ["高蛋白", "优质蛋白", "蛋白质食物"]):
        answer = "常见优质蛋白来源包括鱼虾、禽肉、瘦肉、鸡蛋、奶类、豆腐和豆制品。比起集中在一餐，更建议分散到三餐，并结合你的总能量目标安排份量。"
        basis = ["同时考虑蛋白质含量、脂肪和钠", "过敏与明确禁忌优先"]
    elif "早餐" in message:
        answer = "一份更稳妥的早餐可以由主食、蛋白质和果蔬组成，例如燕麦或全麦面包，加鸡蛋或奶豆类，再配一份水果。具体份量要根据全天目标调整。"
        basis = ["组合比单一食物更有利于营养完整", "早餐不需要追求固定菜单"]
    elif any(word in message for word in ["夜宵", "晚餐太晚", "晚上吃"]):
        answer = "晚上可以吃，重点是份量和选择。若临近睡前，优先选择较小份、少油、不过甜的食物，并避免把白天缺失的全部能量集中补在夜间。"
        basis = ["进食时间需要和全天总量一起判断", "睡前不适或反流人群需要更谨慎"]
    elif any(word in message for word in ["水果", "蔬菜"]):
        answer = "水果和蔬菜都重要，但不能相互完全替代。日常可以让每餐都有蔬菜，水果分成一到两次吃；果汁通常不如完整水果有饱腹感。"
        basis = ["优先选择完整食物", "注意品种多样和实际份量"]
    elif any(word in message for word in ["喝水", "饮水"]):
        answer = "饮水量会受体型、活动量、天气和饮食影响。可以把尿液颜色较浅、口渴感不明显作为日常观察信号，运动或高温环境下再增加；不必短时间大量灌水。"
        basis = ["需求存在明显个体差异", "心肾疾病等限水情况应遵医嘱"]
    else:
        answer = "这个问题可以从份量、进食频率、整体搭配和你的目标四个方面判断。告诉我具体食物、吃多少以及你更关心热量、营养还是减脂效果，我可以给出更准确的回答。"
        basis = ["普通饮食问题可以直接讨论", "越具体的食物和份量，结论越可靠"]
    return {"message": answer, "basis": basis, "confidence": "medium"}
