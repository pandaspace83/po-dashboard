"""The mailbox table (PD-3.1, ARCHITECTURE §4, §7). A mailbox is only a source of documents. Its token or secret is in
the Keychain, never here. Version 1 has one row (DEC-33); the design allows more."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import CheckConstraint, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint, text
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


DOCUMENT_KINDS = ("body", "pdf", "image", "spreadsheet", "word", "text", "email", "other")
DOCUMENT_STATES = ("stored", "not_read", "read", "failed")


class SourceMessage(Base):
    """One message exactly as received. The original is kept unchanged in the file store (REQ-IN-04); a message whose
    bytes are already stored, even if it came through another mailbox, is never stored again (REQ-IN-05)."""

    __tablename__ = "source_message"
    __table_args__ = (
        # a provider's id is unique within its mailbox: a re-scan cannot store a message twice, even if scans race
        UniqueConstraint("mailbox_id", "provider_id", name="uq_source_message_mailbox_id_provider_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    mailbox_id: Mapped[int] = mapped_column(ForeignKey("mailbox.id", ondelete="RESTRICT"), index=True)
    folder: Mapped[str] = mapped_column(String(40))
    provider_id: Mapped[str] = mapped_column(String(200))
    sender: Mapped[str | None] = mapped_column(String(500), default=None)
    recipients: Mapped[str | None] = mapped_column(Text, default=None)  # To and Cc, as written
    subject: Mapped[str | None] = mapped_column(String(1000), default=None)
    received_at: Mapped[datetime] = mapped_column(UTCDateTime, index=True)  # lists are newest first
    message_id_header: Mapped[str | None] = mapped_column(String(500), default=None)
    raw_path: Mapped[str] = mapped_column(String(200))  # inside the file store
    content_hash: Mapped[str] = mapped_column(String(64), unique=True)  # SHA-256 of the raw message
    size: Mapped[int] = mapped_column(Integer)


class SourceDocument(Base):
    """A document inside a message: its body, or an attachment. Text is added by Phase 3's text step (PD-3.3)."""

    __tablename__ = "source_document"
    __table_args__ = (
        CheckConstraint(
            "kind in ('body', 'pdf', 'image', 'spreadsheet', 'word', 'text', 'email', 'other')", name="kind_known"
        ),
        CheckConstraint("state in ('stored', 'not_read', 'read', 'failed')", name="state_known"),
        # an attachment with the same bytes is stored once; bodies are per message (many say only "Thanks")
        Index("uq_source_document_attachment_hash", "content_hash", unique=True, sqlite_where=text("kind != 'body'")),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    message_id: Mapped[int] = mapped_column(ForeignKey("source_message.id", ondelete="CASCADE"), index=True)
    filename: Mapped[str] = mapped_column(String(255))  # for display only; never used as a path
    kind: Mapped[str] = mapped_column(String(16))
    content_type: Mapped[str | None] = mapped_column(String(200), default=None)
    stored_path: Mapped[str] = mapped_column(String(200))  # a body points at the raw message
    content_hash: Mapped[str] = mapped_column(String(64))
    size: Mapped[int] = mapped_column(Integer)
    state: Mapped[str] = mapped_column(String(16), default="stored")
    not_read_reason: Mapped[str | None] = mapped_column(String(200), default=None)
    text_path: Mapped[str | None] = mapped_column(String(200), default=None)
    pages: Mapped[int | None] = mapped_column(Integer, default=None)


class ScanState(Base):
    """Where the scanner has got to in one folder of one mailbox (PD-3.4). The position is opaque: it is saved in the
    same transaction as the messages it covers, so a crash repeats work and never loses mail."""

    __tablename__ = "scan_state"
    __table_args__ = (UniqueConstraint("mailbox_id", "folder", name="uq_scan_state_mailbox_id_folder"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    mailbox_id: Mapped[int] = mapped_column(ForeignKey("mailbox.id", ondelete="CASCADE"))
    folder: Mapped[str] = mapped_column(String(40))
    position: Mapped[str | None] = mapped_column(Text, default=None)
    last_attempt: Mapped[datetime | None] = mapped_column(UTCDateTime, default=None)
    last_success: Mapped[datetime | None] = mapped_column(UTCDateTime, default=None)
    last_error: Mapped[str | None] = mapped_column(String(200), default=None)  # a fixed reason, never mail text
    failures: Mapped[int] = mapped_column(Integer, default=0)  # in a row


EXTRACTION_STATES = ("queued", "running", "done", "failed")
REVIEW_STATES = ("waiting", "unmatched", "no_facts", "accepted", "rejected", "dismissed", "superseded")


class Extraction(Base):
    """One model reading of one document, and the work queue (DEC-54, ARCHITECTURE section 6.1). The row is `queued`
    when the text step has read the document; the extractor claims it, and a person's review state follows. A new
    prompt or model is a new row, so a document is read once per prompt version and model. `last_error` is fixed
    words, never document text (DEC-13)."""

    __tablename__ = "extraction"
    __table_args__ = (
        UniqueConstraint("document_id", "prompt_version", "model", name="uq_extraction_document_prompt_model"),
        CheckConstraint("state in ('queued', 'running', 'done', 'failed')", name="state_known"),
        CheckConstraint(
            "review_state is null or review_state in "
            "('waiting', 'unmatched', 'no_facts', 'accepted', 'rejected', 'dismissed', 'superseded')",
            name="review_state_known",
        ),
        CheckConstraint("attempts >= 0", name="attempts_not_negative"),
        Index("ix_extraction_queue", "state", "next_attempt_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    document_id: Mapped[int] = mapped_column(ForeignKey("source_document.id", ondelete="CASCADE"), index=True)
    model: Mapped[str] = mapped_column(String(100))
    prompt_version: Mapped[int] = mapped_column(Integer)
    schema_version: Mapped[int] = mapped_column(Integer)
    # the queue
    state: Mapped[str] = mapped_column(String(16), default="queued")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(String(200), default=None)
    next_attempt_at: Mapped[datetime | None] = mapped_column(UTCDateTime, default=None)
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime, default=None)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime, default=None)
    seconds: Mapped[float | None] = mapped_column(Float, default=None)
    prompt_tokens: Mapped[int | None] = mapped_column(Integer, default=None)
    # what was read: the model's reply exactly as it came, so numbers stay exact
    answer: Mapped[str | None] = mapped_column(Text, default=None)
    doc_type: Mapped[str | None] = mapped_column(String(32), default=None)
    po_number: Mapped[str | None] = mapped_column(String(100), default=None)
    # matching and review (Phase 4, PD-4.3 to PD-4.5)
    review_state: Mapped[str | None] = mapped_column(String(16), default=None)
    po_id: Mapped[int | None] = mapped_column(ForeignKey("purchase_order.id", ondelete="SET NULL"), default=None)
    vendor_id: Mapped[int | None] = mapped_column(ForeignKey("vendor.id", ondelete="SET NULL"), default=None)
    flags: Mapped[str | None] = mapped_column(Text, default=None)  # JSON list of {flag, level, line}
    final: Mapped[str | None] = mapped_column(Text, default=None)  # JSON: the values a person accepted
    entry_kind: Mapped[str | None] = mapped_column(String(16), default=None)  # ack, shipment, invoice, po
    entry_id: Mapped[int | None] = mapped_column(Integer, default=None)
    reviewed_by: Mapped[int | None] = mapped_column(ForeignKey("app_user.id", ondelete="SET NULL"), default=None)
    reviewed_at: Mapped[datetime | None] = mapped_column(UTCDateTime, default=None)
    review_note: Mapped[str | None] = mapped_column(String(500), default=None)
    version: Mapped[int] = mapped_column(Integer, server_default="1")  # see Vendor.version (DEC-52)

    __mapper_args__ = {"version_id_col": version}
