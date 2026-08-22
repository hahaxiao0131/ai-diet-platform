from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, JSON, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


Payload = JSON().with_variant(JSONB(), "postgresql")


class Base(DeclarativeBase):
    pass


class UserRow(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)


class UserIdentityRow(Base):
    __tablename__ = "user_identities"

    identity_key: Mapped[str] = mapped_column(String(255), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)


class ProfileRow(Base):
    __tablename__ = "profiles"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), unique=True, index=True)
    payload: Mapped[dict] = mapped_column(Payload)


class FoodRow(Base):
    __tablename__ = "foods"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    owner_profile_id: Mapped[str | None] = mapped_column(ForeignKey("profiles.id", ondelete="CASCADE"), nullable=True, index=True)
    name: Mapped[str] = mapped_column(String(160), index=True)
    source: Mapped[str] = mapped_column(String(80), index=True)
    barcode: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    payload: Mapped[dict] = mapped_column(Payload)


class GoalRow(Base):
    __tablename__ = "goal_proposals"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    profile_id: Mapped[str] = mapped_column(ForeignKey("profiles.id", ondelete="CASCADE"), index=True)
    status: Mapped[str] = mapped_column(String(24), index=True)
    payload: Mapped[dict] = mapped_column(Payload)


class ActiveGoalRow(Base):
    __tablename__ = "active_goals"

    profile_id: Mapped[str] = mapped_column(ForeignKey("profiles.id", ondelete="CASCADE"), primary_key=True)
    goal_id: Mapped[str] = mapped_column(ForeignKey("goal_proposals.id", ondelete="CASCADE"), unique=True)


class MealDraftRow(Base):
    __tablename__ = "meal_drafts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    profile_id: Mapped[str] = mapped_column(ForeignKey("profiles.id", ondelete="CASCADE"), index=True)
    status: Mapped[str] = mapped_column(String(24), index=True)
    payload: Mapped[dict] = mapped_column(Payload)


class MealRow(Base):
    __tablename__ = "meals"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    profile_id: Mapped[str] = mapped_column(ForeignKey("profiles.id", ondelete="CASCADE"), index=True)
    status: Mapped[str] = mapped_column(String(24), index=True)
    eaten_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    payload: Mapped[dict] = mapped_column(Payload)


class MealPlanRow(Base):
    __tablename__ = "meal_plans"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    profile_id: Mapped[str] = mapped_column(ForeignKey("profiles.id", ondelete="CASCADE"), index=True)
    status: Mapped[str] = mapped_column(String(24), index=True)
    payload: Mapped[dict] = mapped_column(Payload)


class AIActionRow(Base):
    __tablename__ = "ai_actions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    profile_id: Mapped[str] = mapped_column(ForeignKey("profiles.id", ondelete="CASCADE"), index=True)
    action_type: Mapped[str] = mapped_column(String(40), index=True)
    status: Mapped[str] = mapped_column(String(24), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    payload: Mapped[dict] = mapped_column(Payload)


class AgentMemoryRow(Base):
    __tablename__ = "agent_memories"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    profile_id: Mapped[str] = mapped_column(ForeignKey("profiles.id", ondelete="CASCADE"), index=True)
    category: Mapped[str] = mapped_column(String(40), index=True)
    status: Mapped[str] = mapped_column(String(24), index=True)
    payload: Mapped[dict] = mapped_column(Payload)


class AgentTraceRow(Base):
    __tablename__ = "agent_traces"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    profile_id: Mapped[str] = mapped_column(ForeignKey("profiles.id", ondelete="CASCADE"), index=True)
    provider: Mapped[str] = mapped_column(String(40), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    payload: Mapped[dict] = mapped_column(Payload)


class AgentFeedbackRow(Base):
    __tablename__ = "agent_feedback"

    trace_id: Mapped[str] = mapped_column(ForeignKey("agent_traces.id", ondelete="CASCADE"), primary_key=True)
    profile_id: Mapped[str] = mapped_column(ForeignKey("profiles.id", ondelete="CASCADE"), index=True)
    payload: Mapped[dict] = mapped_column(Payload)


class WeightRecordRow(Base):
    __tablename__ = "weight_records"

    profile_id: Mapped[str] = mapped_column(ForeignKey("profiles.id", ondelete="CASCADE"), primary_key=True)
    position: Mapped[int] = mapped_column(primary_key=True)
    payload: Mapped[dict] = mapped_column(Payload)


class SessionRow(Base):
    __tablename__ = "sessions"

    token_hash: Mapped[str] = mapped_column(String(128), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    payload: Mapped[dict] = mapped_column(Payload)


Index("ix_foods_owner_barcode", FoodRow.owner_profile_id, FoodRow.barcode)
Index("ix_meals_profile_eaten_at", MealRow.profile_id, MealRow.eaten_at)
Index("ix_actions_profile_status", AIActionRow.profile_id, AIActionRow.status)
