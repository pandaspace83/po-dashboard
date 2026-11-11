"""Stored messages and their documents (PD-3.2)

Revision ID: 0005
Revises: 0004
"""

import sqlalchemy as sa
from alembic import op

from po_dashboard.db.base import UTCDateTime

revision = "0005"
down_revision = "0004"


def upgrade() -> None:
    op.create_table(
        "source_message",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("created_at", UTCDateTime(), nullable=False),
        sa.Column("mailbox_id", sa.Integer(), nullable=False),
        sa.Column("folder", sa.String(40), nullable=False),
        sa.Column("provider_id", sa.String(200), nullable=False),
        sa.Column("sender", sa.String(500), nullable=True),
        sa.Column("recipients", sa.Text(), nullable=True),
        sa.Column("subject", sa.String(1000), nullable=True),
        sa.Column("received_at", UTCDateTime(), nullable=False),
        sa.Column("message_id_header", sa.String(500), nullable=True),
        sa.Column("raw_path", sa.String(200), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("size", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["mailbox_id"], ["mailbox.id"], name="fk_source_message_mailbox_id_mailbox", ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_source_message"),
        sa.UniqueConstraint("content_hash", name="uq_source_message_content_hash"),
    )
    op.create_index("ix_source_message_mailbox_id", "source_message", ["mailbox_id"])
    op.create_table(
        "source_document",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("created_at", UTCDateTime(), nullable=False),
        sa.Column("message_id", sa.Integer(), nullable=False),
        sa.Column("filename", sa.String(255), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("content_type", sa.String(200), nullable=True),
        sa.Column("stored_path", sa.String(200), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("size", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("not_read_reason", sa.String(200), nullable=True),
        sa.Column("text_path", sa.String(200), nullable=True),
        sa.Column("pages", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(
            ["message_id"], ["source_message.id"], name="fk_source_document_message_id_source_message",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_source_document"),
        sa.CheckConstraint(
            "kind in ('body', 'pdf', 'image', 'spreadsheet', 'word', 'text', 'email', 'other')",
            name="ck_source_document_kind_known",
        ),
        sa.CheckConstraint(
            "state in ('stored', 'not_read', 'read', 'failed')", name="ck_source_document_state_known"
        ),
    )
    op.create_index("ix_source_document_message_id", "source_document", ["message_id"])
    op.create_index(
        "uq_source_document_attachment_hash", "source_document", ["content_hash"], unique=True,
        sqlite_where=sa.text("kind != 'body'"),
    )


def downgrade() -> None:
    op.drop_table("source_document")
    op.drop_table("source_message")
