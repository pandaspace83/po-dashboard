"""Where the scanner has got to, per mailbox folder (PD-3.4)

Revision ID: 0008
Revises: 0007
"""

import sqlalchemy as sa
from alembic import op

from po_dashboard.db.base import UTCDateTime

revision = "0008"
down_revision = "0007"


def upgrade() -> None:
    op.create_table(
        "scan_state",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("mailbox_id", sa.Integer(), nullable=False),
        sa.Column("folder", sa.String(40), nullable=False),
        sa.Column("position", sa.Text(), nullable=True),
        sa.Column("last_attempt", UTCDateTime(), nullable=True),
        sa.Column("last_success", UTCDateTime(), nullable=True),
        sa.Column("last_error", sa.String(200), nullable=True),
        sa.Column("failures", sa.Integer(), nullable=False, server_default="0"),
        sa.ForeignKeyConstraint(
            ["mailbox_id"], ["mailbox.id"], name="fk_scan_state_mailbox_id_mailbox", ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_scan_state"),
        sa.UniqueConstraint("mailbox_id", "folder", name="uq_scan_state_mailbox_id_folder"),
    )


def downgrade() -> None:
    op.drop_table("scan_state")
