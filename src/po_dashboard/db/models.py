"""Tables for sign-in and the audit trail (ARCHITECTURE §4, §10.1). Ledger tables arrive in PD-1.1b."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import CheckConstraint, ForeignKey, Integer, LargeBinary, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from po_dashboard.db.base import Base, UTCDateTime, utcnow


class AppUser(Base):
    __tablename__ = "app_user"
    __table_args__ = (CheckConstraint("role in ('admin', 'buyer')", name="role_known"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    email: Mapped[str] = mapped_column(String(254), unique=True)  # stored lower case
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(16))
    totp_secret_sealed: Mapped[bytes | None] = mapped_column(LargeBinary, default=None)
    totp_confirmed_at: Mapped[datetime | None] = mapped_column(UTCDateTime, default=None)
    last_totp_step: Mapped[int | None] = mapped_column(Integer, default=None)
    failed_attempts: Mapped[int] = mapped_column(Integer, default=0)
    locked_until: Mapped[datetime | None] = mapped_column(UTCDateTime, default=None)
    must_change_password: Mapped[bool] = mapped_column(default=False)
    temp_password_expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime, default=None)
    disabled_at: Mapped[datetime | None] = mapped_column(UTCDateTime, default=None)


class SignInSession(Base):
    __tablename__ = "session"
    __table_args__ = (CheckConstraint("stage in ('pending', 'full')", name="stage_known"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    user_id: Mapped[int] = mapped_column(ForeignKey("app_user.id", ondelete="CASCADE"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)  # SHA-256 hex; the token is never stored
    stage: Mapped[str] = mapped_column(String(8))
    csrf_token: Mapped[str] = mapped_column(String(64))
    last_seen_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime)


class RecoveryCode(Base):
    __tablename__ = "recovery_code"

    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    user_id: Mapped[int] = mapped_column(ForeignKey("app_user.id", ondelete="CASCADE"), index=True)
    code_hash: Mapped[str] = mapped_column(String(64), unique=True)
    used_at: Mapped[datetime | None] = mapped_column(UTCDateTime, default=None)


class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, index=True)
    # Empty for a command-line action or an unknown account. Kept if the person is later removed.
    user_id: Mapped[int | None] = mapped_column(ForeignKey("app_user.id", ondelete="SET NULL"), default=None)
    client_address: Mapped[str | None] = mapped_column(String(64), default=None)
    action: Mapped[str] = mapped_column(String(64), index=True)
    object: Mapped[str | None] = mapped_column(String(128), default=None)
    before: Mapped[str | None] = mapped_column(Text, default=None)
    after: Mapped[str | None] = mapped_column(Text, default=None)
