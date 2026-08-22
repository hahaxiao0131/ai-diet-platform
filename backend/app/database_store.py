from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime
from threading import RLock
from typing import Any
from uuid import UUID

from sqlalchemy import create_engine, delete, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from .db_models import (
    ActiveGoalRow,
    AgentFeedbackRow,
    AgentMemoryRow,
    AgentTraceRow,
    AIActionRow,
    Base,
    FoodRow,
    GoalRow,
    MealDraftRow,
    MealPlanRow,
    MealRow,
    ProfileRow,
    SessionRow,
    UserIdentityRow,
    UserRow,
    WeightRecordRow,
)
from .models import AgentFeedback, AgentMemory, AgentTrace, AIAction, Food, GoalProposal, Meal, MealDraft, MealPlan, Profile
from .store import MemoryStore, _json_value


def _payload(model) -> dict[str, Any]:
    return model.model_dump(mode="json")


def _owner_profile_id(food: Food) -> str | None:
    owner = next((tag.removeprefix("profile:") for tag in food.tags if tag.startswith("profile:")), None)
    return owner


class DatabaseStore(MemoryStore):
    """Compatibility store backed by one transactional SQL database.

    The in-memory collections preserve the existing service API. Each persist
    flushes the current aggregate state in one database transaction.
    """

    def __init__(self, database_url: str, *, auto_create: bool = True, engine: Engine | None = None) -> None:
        super().__init__(None)
        self.backend_name = "postgresql" if database_url.startswith("postgresql") else "database"
        self.database_url = database_url
        self.engine = engine or create_engine(database_url, pool_pre_ping=True)
        self._session_factory = sessionmaker(self.engine, expire_on_commit=False)
        self._write_lock = RLock()
        if auto_create:
            Base.metadata.create_all(self.engine)
        self._load_database()

    @contextmanager
    def atomic(self):
        with self._write_lock:
            yield

    def persist(self) -> None:
        with self._write_lock, self._session_factory.begin() as session:
            self._persist_users(session)
            self._persist_profiles(session)
            self._persist_foods(session)
            self._persist_goals(session)
            self._persist_model_map(session, MealDraftRow, self.drafts, self._draft_values)
            self._persist_model_map(session, MealRow, self.meals, self._meal_values)
            self._persist_model_map(session, MealPlanRow, self.plans, self._plan_values)
            self._persist_model_map(session, AIActionRow, self.ai_actions, self._action_values)
            self._persist_model_map(session, AgentMemoryRow, self.agent_memories, self._memory_values)
            self._persist_model_map(session, AgentTraceRow, self.agent_traces, self._trace_values)
            self._persist_feedback(session)
            self._persist_weights(session)
            self._persist_sessions(session)

    def _load_database(self) -> None:
        with self._session_factory() as session:
            identities = session.scalars(select(UserIdentityRow)).all()
            self.users = {item.identity_key: UUID(item.user_id) for item in identities}

            profiles = [Profile.model_validate(item.payload) for item in session.scalars(select(ProfileRow)).all()]
            self.profiles = {item.id: item for item in profiles}

            food_rows = session.scalars(select(FoodRow)).all()
            if food_rows:
                foods = [Food.model_validate(item.payload) for item in food_rows]
                self.foods = foods

            goals = [GoalProposal.model_validate(item.payload) for item in session.scalars(select(GoalRow)).all()]
            self.goal_proposals = {item.id: item for item in goals}
            self.active_goals = {}
            for item in session.scalars(select(ActiveGoalRow)).all():
                goal = self.goal_proposals.get(UUID(item.goal_id))
                if goal:
                    self.active_goals[UUID(item.profile_id)] = goal

            self.drafts = self._load_models(session, MealDraftRow, MealDraft)
            self.meals = self._load_models(session, MealRow, Meal)
            self.plans = self._load_models(session, MealPlanRow, MealPlan)
            self.ai_actions = self._load_models(session, AIActionRow, AIAction)
            self.agent_memories = self._load_models(session, AgentMemoryRow, AgentMemory)
            self.agent_traces = self._load_models(session, AgentTraceRow, AgentTrace)

            feedback = [AgentFeedback.model_validate(item.payload) for item in session.scalars(select(AgentFeedbackRow)).all()]
            self.agent_feedback = {item.trace_id: item for item in feedback}

            self.weights = {}
            for item in session.scalars(select(WeightRecordRow).order_by(WeightRecordRow.profile_id, WeightRecordRow.position)).all():
                self.weights.setdefault(UUID(item.profile_id), []).append(item.payload)
            self.sessions = {item.token_hash: item.payload for item in session.scalars(select(SessionRow)).all()}

        if not food_rows:
            self.persist()

    @staticmethod
    def _load_models(session: Session, row_type, model_type):
        models = [model_type.model_validate(item.payload) for item in session.scalars(select(row_type)).all()]
        return {item.id: item for item in models}

    @staticmethod
    def _upsert(session: Session, row_type, key, values: dict[str, Any]) -> None:
        row = session.get(row_type, key)
        if row is None:
            session.add(row_type(**values))
            return
        for name, value in values.items():
            setattr(row, name, value)

    def _persist_users(self, session: Session) -> None:
        user_ids = {str(value) for value in self.users.values()}
        user_ids.update(str(profile.user_id) for profile in self.profiles.values())
        user_ids.update(str(value["user_id"]) for value in self.sessions.values())
        for user_id in user_ids:
            self._upsert(session, UserRow, user_id, {"id": user_id})
        for identity_key, user_id in self.users.items():
            self._upsert(
                session,
                UserIdentityRow,
                identity_key,
                {"identity_key": identity_key, "user_id": str(user_id)},
            )

    def _persist_profiles(self, session: Session) -> None:
        for profile in self.profiles.values():
            values = {"id": str(profile.id), "user_id": str(profile.user_id), "payload": _payload(profile)}
            self._upsert(session, ProfileRow, str(profile.id), values)

    def _persist_foods(self, session: Session) -> None:
        for food in self.foods:
            values = {
                "id": str(food.id),
                "owner_profile_id": _owner_profile_id(food),
                "name": food.name,
                "source": food.source,
                "barcode": food.barcode,
                "payload": _payload(food),
            }
            self._upsert(session, FoodRow, str(food.id), values)

    def _persist_goals(self, session: Session) -> None:
        for goal in self.goal_proposals.values():
            values = {"id": str(goal.id), "profile_id": str(goal.profile_id), "status": goal.status, "payload": _payload(goal)}
            self._upsert(session, GoalRow, str(goal.id), values)
        active_ids = {str(profile_id) for profile_id in self.active_goals}
        for row in session.scalars(select(ActiveGoalRow)).all():
            if row.profile_id not in active_ids:
                session.delete(row)
        for profile_id, goal in self.active_goals.items():
            values = {"profile_id": str(profile_id), "goal_id": str(goal.id)}
            self._upsert(session, ActiveGoalRow, str(profile_id), values)

    def _persist_model_map(self, session: Session, row_type, values, value_builder) -> None:
        for model in values.values():
            payload = value_builder(model)
            self._upsert(session, row_type, str(model.id), payload)

    @staticmethod
    def _draft_values(item: MealDraft) -> dict[str, Any]:
        return {"id": str(item.id), "profile_id": str(item.profile_id), "status": item.draft_status, "payload": _payload(item)}

    @staticmethod
    def _meal_values(item: Meal) -> dict[str, Any]:
        return {
            "id": str(item.id), "profile_id": str(item.profile_id), "status": item.status,
            "eaten_at": item.eaten_at, "payload": _payload(item),
        }

    @staticmethod
    def _plan_values(item: MealPlan) -> dict[str, Any]:
        return {"id": str(item.id), "profile_id": str(item.profile_id), "status": item.status, "payload": _payload(item)}

    @staticmethod
    def _action_values(item: AIAction) -> dict[str, Any]:
        return {
            "id": str(item.id), "profile_id": str(item.profile_id), "action_type": item.action_type,
            "status": item.status, "created_at": item.created_at, "payload": _payload(item),
        }

    @staticmethod
    def _memory_values(item: AgentMemory) -> dict[str, Any]:
        return {
            "id": str(item.id), "profile_id": str(item.profile_id), "category": item.category,
            "status": item.status, "payload": _payload(item),
        }

    @staticmethod
    def _trace_values(item: AgentTrace) -> dict[str, Any]:
        return {
            "id": str(item.id), "profile_id": str(item.profile_id), "provider": item.provider,
            "created_at": item.created_at, "payload": _payload(item),
        }

    def _persist_feedback(self, session: Session) -> None:
        for feedback in self.agent_feedback.values():
            values = {
                "trace_id": str(feedback.trace_id),
                "profile_id": str(feedback.profile_id),
                "payload": _payload(feedback),
            }
            self._upsert(session, AgentFeedbackRow, str(feedback.trace_id), values)

    def _persist_weights(self, session: Session) -> None:
        session.execute(delete(WeightRecordRow))
        for profile_id, records in self.weights.items():
            for position, record in enumerate(records):
                session.add(WeightRecordRow(profile_id=str(profile_id), position=position, payload=_json_value(record)))

    def _persist_sessions(self, session: Session) -> None:
        active_hashes = set(self.sessions)
        for row in session.scalars(select(SessionRow)).all():
            if row.token_hash not in active_hashes:
                session.delete(row)
        for token_hash, payload in self.sessions.items():
            expires_at = datetime.fromisoformat(str(payload["expires_at"]))
            values = {
                "token_hash": token_hash,
                "user_id": str(payload["user_id"]),
                "expires_at": expires_at,
                "payload": _json_value(payload),
            }
            self._upsert(session, SessionRow, token_hash, values)
