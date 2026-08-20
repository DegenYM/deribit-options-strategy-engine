from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def _uuid() -> str:
    return str(uuid.uuid4())


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    paper_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    live_unlocked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    waitlisted: Mapped[bool] = mapped_column(Boolean, default=True)
    approved: Mapped[bool] = mapped_column(Boolean, default=False)

    tenants: Mapped[list[Tenant]] = relationship(back_populates="user")


class MagicLink(Base):
    __tablename__ = "magic_links"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    email: Mapped[str] = mapped_column(String(320), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class SessionToken(Base):
    __tablename__ = "sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Tenant(Base):
    __tablename__ = "tenants"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    name: Mapped[str] = mapped_column(String(120), default="default")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    user: Mapped[User] = relationship(back_populates="tenants")
    credential: Mapped[Credential | None] = relationship(back_populates="tenant", uselist=False)
    subscription: Mapped[Subscription | None] = relationship(back_populates="tenant", uselist=False)
    bot_settings: Mapped[BotSettings | None] = relationship(back_populates="tenant", uselist=False)
    desired_state: Mapped[DesiredState | None] = relationship(back_populates="tenant", uselist=False)
    onboarding: Mapped[Onboarding | None] = relationship(back_populates="tenant", uselist=False)


class Credential(Base):
    __tablename__ = "credentials"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), unique=True)
    client_id: Mapped[str] = mapped_column(String(128))
    secret_encrypted: Mapped[str] = mapped_column(Text)
    last4: Mapped[str] = mapped_column(String(8), default="")
    validated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    tenant: Mapped[Tenant] = relationship(back_populates="credential")


class Subscription(Base):
    __tablename__ = "subscriptions"
    __table_args__ = (UniqueConstraint("tenant_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), unique=True)
    plan_id: Mapped[str] = mapped_column(String(32), default="scout")
    status: Mapped[str] = mapped_column(String(32), default="inactive")
    stripe_customer_id: Mapped[str] = mapped_column(String(64), default="")
    stripe_subscription_id: Mapped[str] = mapped_column(String(64), default="")
    trial_ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    tenant: Mapped[Tenant] = relationship(back_populates="subscription")


class Onboarding(Base):
    __tablename__ = "onboardings"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), unique=True)
    experience: Mapped[str] = mapped_column(String(32), default="")
    inventory: Mapped[str] = mapped_column(String(32), default="")
    coins: Mapped[str] = mapped_column(String(16), default="BTC")
    capital_band: Mapped[str] = mapped_column(String(16), default="")
    intent: Mapped[str] = mapped_column(String(16), default="")
    drawdown: Mapped[str] = mapped_column(String(16), default="")
    want_sweep: Mapped[bool] = mapped_column(Boolean, default=False)
    alerts: Mapped[bool] = mapped_column(Boolean, default=False)
    acknowledgements: Mapped[str] = mapped_column(Text, default="[]")
    recommended_plan_id: Mapped[str] = mapped_column(String(32), default="")
    recommended_tier: Mapped[str] = mapped_column(String(16), default="")
    recommended_coins: Mapped[str] = mapped_column(String(32), default="")
    recommended_sweep: Mapped[bool] = mapped_column(Boolean, default=False)
    recommend_reasons: Mapped[str] = mapped_column(Text, default="[]")
    intake_complete: Mapped[bool] = mapped_column(Boolean, default=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    tenant: Mapped[Tenant] = relationship(back_populates="onboarding")


class BotSettings(Base):
    __tablename__ = "bot_settings"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), unique=True)
    risk_tier: Mapped[str] = mapped_column(String(16), default="low")
    coins_csv: Mapped[str] = mapped_column(String(32), default="BTC")
    profit_sweep: Mapped[bool] = mapped_column(Boolean, default=False)
    telegram_chat_id: Mapped[str] = mapped_column(String(64), default="")
    telegram_token_encrypted: Mapped[str] = mapped_column(Text, default="")

    tenant: Mapped[Tenant] = relationship(back_populates="bot_settings")


class DesiredState(Base):
    __tablename__ = "desired_states"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), unique=True)
    desired: Mapped[str] = mapped_column(String(16), default="stopped")  # stopped|dry_run|live|paused|panic
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    tenant: Mapped[Tenant] = relationship(back_populates="desired_state")


class AuditEvent(Base):
    __tablename__ = "audit_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    user_id: Mapped[str] = mapped_column(String(36), default="")
    action: Mapped[str] = mapped_column(String(64), index=True)
    payload: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class WorkerHeartbeat(Base):
    __tablename__ = "worker_heartbeats"

    tenant_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    pid: Mapped[int] = mapped_column(Integer, default=0)
    desired: Mapped[str] = mapped_column(String(16), default="stopped")
    last_error: Mapped[str] = mapped_column(Text, default="")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
