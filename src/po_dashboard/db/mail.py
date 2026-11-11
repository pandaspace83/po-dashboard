"""The mailbox table (PD-3.1, ARCHITECTURE §4, §7). A mailbox is only a source of documents. Its token or secret is in
the Keychain, never here. Version 1 has one row (DEC-33); the design allows more."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import CheckConstraint, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from po_dashboard.db.base import Base, UTCDateTime, utcnow

PROVIDERS = ("gmail", "graph", "folder")
STATES = ("connected", "needs_reconnect", "disconnected")


class Mailbox(Base):
    __tablename__ = "mailbox"
    __table_args__ = (
        CheckConstraint("provider in ('gmail', 'graph', 'folder')", name="provider_known"),
        CheckConstraint("state in ('connected', 'needs_reconnect', 'disconnected')", name="state_known"),
        CheckConstraint("address = lower(address)", name="lower_case"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    address: Mapped[str] = mapped_column(String(254), unique=True)  # stored lower case
    provider: Mapped[str] = mapped_column(String(16))
    # The folder a "folder" mailbox reads (tests, demo). Empty for Gmail and Microsoft 365.
    location: Mapped[str | None] = mapped_column(String(1000), default=None)
    connected_by: Mapped[int | None] = mapped_column(ForeignKey("app_user.id", ondelete="SET NULL"), default=None)
    connected_at: Mapped[datetime | None] = mapped_column(UTCDateTime, default=None)
    state: Mapped[str] = mapped_column(String(16), default="connected")
    # Only mail that arrived on or after this time is read (DEC-51): the day the mailbox was connected, or earlier
    # if the admin chose to read the last N days.
    read_since: Mapped[datetime | None] = mapped_column(UTCDateTime, default=None)
