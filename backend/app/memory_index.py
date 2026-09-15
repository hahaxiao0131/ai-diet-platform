from __future__ import annotations

import hashlib
import math
import os
from pathlib import Path
from typing import Any, Iterable

from .models import AgentMemory, AgentTrace, Meal, Profile


GUIDE_FRAGMENTS = [
    "中国居民膳食指南建议食物多样、合理搭配，日常饮食应包含谷薯类、蔬菜水果、畜禽鱼蛋奶和豆类。",
    "规律进餐并保持清淡饮食，少盐、少油、控制添加糖；饮水和运动应与个人日常活动相匹配。",
    "健康评估不应只看单一营养素，应同时关注一段时间内的食材多样性、蔬菜水果、全谷物和优质蛋白来源。",
    "出现持续或严重腹泻、腹痛、呕吐、过敏或其他明显不适时，应停止自行饮食试验并及时就医。",
]


def _embedding(text: str, dimensions: int = 256) -> list[float]:
    """Small deterministic embedding for a local demo; no model download required."""
    normalized = "".join(text.lower().split())
    tokens = [normalized[index:index + 2] for index in range(max(1, len(normalized) - 1))]
    vector = [0.0] * dimensions
    for token in tokens:
        digest = hashlib.sha256(token.encode("utf-8")).digest()
        index = int.from_bytes(digest[:4], "big") % dimensions
        vector[index] += 1.0 if digest[4] % 2 == 0 else -1.0
    length = math.sqrt(sum(value * value for value in vector)) or 1.0
    return [value / length for value in vector]


def feedback_document(profile: Profile, meal: Meal) -> str | None:
    feedback = meal.body_feedback
    if feedback is None:
        return None
    foods = "、".join(item.name for item in meal.items)
    symptoms = "、".join(feedback.symptoms) or "未填写"
    timing = f"约{feedback.occurred_after_minutes}分钟后" if feedback.occurred_after_minutes is not None else "餐后"
    note = f"；补充：{feedback.notes}" if feedback.notes else ""
    return (
        f"用户饮食-体感记录：{meal.eaten_at.date().isoformat()}吃了{foods}，{timing}出现{symptoms}，"
        f"程度{feedback.severity}{note}。用户不耐受：{'、'.join(profile.food_intolerances) or '无'}；"
        f"肠胃敏感：{'、'.join(profile.digestive_sensitivities) or '无'}。"
    )


def preference_document(memory: AgentMemory) -> str:
    labels = {"preference": "偏好", "avoidance": "避免", "habit": "习惯"}
    return f"用户已确认长期{labels[memory.category]}：{memory.value}。确认来源：{memory.source_message[:180]}"


def conversation_document(trace: AgentTrace) -> str:
    user_text = trace.resolved_message or trace.message
    assistant_text = trace.response_message or "未生成可用回答"
    return (
        f"历史对话（仅用于回忆讨论内容，不代表事实已确认）：{trace.created_at.date().isoformat()}，"
        f"用户说：{user_text[:300]}；助手回答：{assistant_text[:300]}"
    )


class PersonalMemoryIndex:
    """Chroma-backed long-term memory with an isolated, lexical fallback."""

    def __init__(self, path: str | Path | None = None) -> None:
        default_path = Path(__file__).resolve().parents[1] / "data" / "chroma"
        self.path = Path(path or os.getenv("DIET_CHROMA_PATH", default_path))
        self._guide = None
        self._reactions = None
        self._preferences = None
        self._conversations = None
        self._indexed_documents: dict[str, str] = {}
        self.available = False

    def _already_indexed(self, document_id: str, document: str) -> bool:
        fingerprint = hashlib.sha256(document.encode("utf-8")).hexdigest()
        return self._indexed_documents.get(document_id) == fingerprint

    def _mark_indexed(self, document_id: str, document: str) -> None:
        self._indexed_documents[document_id] = hashlib.sha256(document.encode("utf-8")).hexdigest()

    def _ensure(self) -> bool:
        if all(collection is not None for collection in [
            self._guide, self._reactions, self._preferences, self._conversations,
        ]):
            return True
        if os.getenv("DIET_DISABLE_PERSISTENCE", "0") == "1":
            return False
        try:
            import chromadb

            self.path.mkdir(parents=True, exist_ok=True)
            client = chromadb.PersistentClient(path=str(self.path))
            self._guide = client.get_or_create_collection("diet_guide_v1", metadata={"hnsw:space": "cosine"})
            self._reactions = client.get_or_create_collection("body_reactions_v1", metadata={"hnsw:space": "cosine"})
            self._preferences = client.get_or_create_collection("confirmed_preferences_v1", metadata={"hnsw:space": "cosine"})
            self._conversations = client.get_or_create_collection("agent_conversations_v1", metadata={"hnsw:space": "cosine"})
            self._guide.upsert(
                ids=[f"guide-{index}" for index in range(len(GUIDE_FRAGMENTS))],
                documents=GUIDE_FRAGMENTS,
                embeddings=[_embedding(text) for text in GUIDE_FRAGMENTS],
                metadatas=[{"source": "中国居民膳食指南（Demo精简片段）"} for _ in GUIDE_FRAGMENTS],
            )
            self.available = True
            return True
        except Exception:
            self.available = False
            return False

    def upsert_feedback(self, profile: Profile, meal: Meal) -> None:
        document = feedback_document(profile, meal)
        if document is None or not self._ensure():
            return
        document_id = f"meal-{meal.id}"
        if self._already_indexed(document_id, document):
            return
        try:
            self._reactions.upsert(
                ids=[document_id],
                documents=[document],
                embeddings=[_embedding(document)],
                metadatas=[{"profile_id": str(profile.id), "meal_id": str(meal.id), "eaten_at": meal.eaten_at.isoformat()}],
            )
            self._mark_indexed(document_id, document)
        except Exception:
            self.available = False

    def upsert_preference(self, memory: AgentMemory) -> None:
        if memory.status != "active" or not self._ensure():
            return
        document = preference_document(memory)
        document_id = f"memory-{memory.id}"
        if self._already_indexed(document_id, document):
            return
        try:
            self._preferences.upsert(
                ids=[document_id],
                documents=[document],
                embeddings=[_embedding(document)],
                metadatas=[{"profile_id": str(memory.profile_id), "memory_id": str(memory.id), "category": memory.category}],
            )
            self._mark_indexed(document_id, document)
        except Exception:
            self.available = False

    def delete_preference(self, memory_id) -> None:
        document_id = f"memory-{memory_id}"
        if self._ensure():
            try:
                self._preferences.delete(ids=[document_id])
                self._indexed_documents.pop(document_id, None)
            except Exception:
                self.available = False

    def delete_profile(self, profile_id, *, meal_ids=(), memory_ids=(), trace_ids=()) -> None:
        """Delete user-derived vectors while preserving the shared guide collection."""
        if self._ensure():
            try:
                where = {"profile_id": str(profile_id)}
                self._reactions.delete(where=where)
                self._preferences.delete(where=where)
                self._conversations.delete(where=where)
                for prefix, identifiers in (("meal", meal_ids), ("memory", memory_ids), ("trace", trace_ids)):
                    for identifier in identifiers:
                        self._indexed_documents.pop(f"{prefix}-{identifier}", None)
            except Exception:
                self.available = False

    def upsert_conversation(self, trace: AgentTrace) -> None:
        if not self._ensure():
            return
        document = conversation_document(trace)
        document_id = f"trace-{trace.id}"
        if self._already_indexed(document_id, document):
            return
        try:
            self._conversations.upsert(
                ids=[document_id],
                documents=[document],
                embeddings=[_embedding(document)],
                metadatas=[{
                    "profile_id": str(trace.profile_id),
                    "trace_id": str(trace.id),
                    "created_at": trace.created_at.isoformat(),
                }],
            )
            self._mark_indexed(document_id, document)
        except Exception:
            self.available = False

    def retrieve(
        self,
        profile: Profile,
        meals: Iterable[Meal],
        query: str,
        limit: int = 4,
        *,
        memories: Iterable[AgentMemory] = (),
        traces: Iterable[AgentTrace] = (),
    ) -> dict[str, list[str]]:
        meal_list = [meal for meal in meals if meal.profile_id == profile.id and meal.status == "active"]
        memory_list = [memory for memory in memories if memory.profile_id == profile.id and memory.status == "active"]
        trace_list = sorted(
            (trace for trace in traces if trace.profile_id == profile.id),
            key=lambda trace: trace.created_at,
            reverse=True,
        )[:100]
        for meal in meal_list:
            self.upsert_feedback(profile, meal)
        for memory in memory_list:
            self.upsert_preference(memory)
        for trace in trace_list:
            self.upsert_conversation(trace)

        if self._ensure():
            try:
                return {
                    "guide": self._query(self._guide, query, min(2, limit)),
                    "reactions": self._query(self._reactions, query, limit, where={"profile_id": str(profile.id)}),
                    "preferences": self._query(self._preferences, query, limit, where={"profile_id": str(profile.id)}),
                    "conversations": self._query(self._conversations, query, limit, where={"profile_id": str(profile.id)}),
                    "backend": ["chroma"],
                }
            except Exception:
                self.available = False

        reaction_docs = [document for meal in meal_list if (document := feedback_document(profile, meal))]
        preference_docs = [preference_document(memory) for memory in memory_list]
        conversation_docs = [conversation_document(trace) for trace in trace_list]
        rank = lambda documents: sorted(documents, key=lambda item: self._overlap_score(query, item), reverse=True)[:limit]
        return {
            "guide": sorted(GUIDE_FRAGMENTS, key=lambda item: self._overlap_score(query, item), reverse=True)[:2],
            "reactions": rank(reaction_docs),
            "preferences": rank(preference_docs),
            "conversations": rank(conversation_docs),
            "backend": ["local_fallback"],
        }

    @staticmethod
    def _query(collection, query: str, limit: int, where: dict | None = None) -> list[str]:
        if collection.count() == 0:
            return []
        query_args: dict[str, Any] = {
            "query_embeddings": [_embedding(query)],
            "n_results": min(limit, collection.count()),
            "include": ["documents"],
        }
        if where is not None:
            query_args["where"] = where
        result = collection.query(**query_args)
        return list((result.get("documents") or [[]])[0])

    @staticmethod
    def _overlap_score(query: str, document: str) -> int:
        return len(set("".join(query.split())).intersection(document))
