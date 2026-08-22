from __future__ import annotations

import argparse
import os
from pathlib import Path
from uuid import UUID

from app.database_store import DatabaseStore
from app.store import MemoryStore


COLLECTIONS = (
    "users",
    "profiles",
    "goal_proposals",
    "active_goals",
    "drafts",
    "meals",
    "plans",
    "ai_actions",
    "agent_memories",
    "agent_traces",
    "agent_feedback",
    "weights",
    "sessions",
)


def remap_seed_food_references(source: MemoryStore) -> int:
    by_name = {food.name: food for food in source.foods}
    changed = 0
    for meal in source.meals.values():
        for item in meal.items:
            match = by_name.get(item.name)
            if match and item.food_id != match.id:
                item.food_id = match.id
                changed += 1
    for plan in source.plans.values():
        for item in plan.items:
            match = by_name.get(item.name)
            if match and item.food_id != match.id:
                item.food_id = match.id
                changed += 1
    for draft in source.drafts.values():
        for item in draft.items:
            match = by_name.get(item.name)
            if match and item.food_id != match.id:
                item.food_id = match.id
                changed += 1
    return changed


def copy_store(source: MemoryStore, target: DatabaseStore) -> None:
    target.foods = list(source.foods)
    for name in COLLECTIONS:
        value = getattr(source, name)
        setattr(target, name, value.copy() if isinstance(value, dict) else list(value))
    target.persist()


def main() -> None:
    parser = argparse.ArgumentParser(description="Import the local JSON store into the configured SQL database.")
    parser.add_argument("--source", type=Path, default=Path(__file__).resolve().parents[1] / "data" / "local_store.json")
    parser.add_argument("--database-url", default=os.getenv("DATABASE_URL", ""))
    parser.add_argument("--allow-nonempty", action="store_true")
    args = parser.parse_args()
    if not args.source.exists():
        raise SystemExit(f"JSON 数据文件不存在: {args.source}")
    if not args.database_url:
        raise SystemExit("请通过 DATABASE_URL 或 --database-url 提供目标数据库")

    source = MemoryStore(args.source)
    target = DatabaseStore(args.database_url, auto_create=True)
    has_data = bool(target.users or target.profiles or target.meals or target.ai_actions)
    if has_data and not args.allow_nonempty:
        raise SystemExit("目标数据库已有业务数据；确认合并时请显式添加 --allow-nonempty")
    remapped = remap_seed_food_references(source)
    copy_store(source, target)
    print(
        f"导入完成：{len(source.profiles)} 个档案，{len(source.meals)} 条餐食，"
        f"{len(source.ai_actions)} 个 AI 动作，修复 {remapped} 个食物引用。"
    )


if __name__ == "__main__":
    main()
