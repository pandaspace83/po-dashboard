"""The mailbox table (PD-3.1)

Revision ID: 0004
Revises: 0003
"""

import sqlalchemy as sa
from alembic import op

from po_dashboard.db.base import UTCDateTime

revision = "0004"
down_revision = "0003"


def upgrade() -> None:
    op.create_table(
        "mailbox",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("created_at", UTCDateTime(), nullable=False),
        sa.Column("address", sa.String(254), nullable=False),
        sa.Column("provider", sa.String(16), nullable=False),
        sa.Column("location", sa.String(1000), nullable=True),
        sa.Column("connected_by", sa.Integer(), nullable=True),
        sa.Column("connected_at", UTCDateTime(), nullable=True),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("read_since", UTCDateTime(), nullable=True),
        sa.ForeignKeyConstraint(
            ["connected_by"], ["app_user.id"], name="fk_mailbox_connected_by_app_user", ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_mailbox"),
        sa.UniqueConstraint("address", name="uq_mailbox_address"),
        sa.CheckConstraint("provider in ('gmail', 'graph', 'folder')", name="ck_mailbox_provider_known"),
        sa.CheckConstraint(
            "state in ('connected', 'needs_reconnect', 'disconnected')", name="ck_mailbox_state_known"
        ),
        sa.CheckConstraint("address = lower(address)", name="ck_mailbox_lower_case"),
    )


def downgrade() -> None:
    op.drop_table("mailbox")
