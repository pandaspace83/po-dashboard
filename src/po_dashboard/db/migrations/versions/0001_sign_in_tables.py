"""Sign-in tables and the audit trail (PD-1.1a)

Revision ID: 0001
Revises:
"""

import sqlalchemy as sa
from alembic import op

from po_dashboard.db.base import UTCDateTime

revision = "0001"
down_revision = None


def upgrade() -> None:
    op.create_table(
        "app_user",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("created_at", UTCDateTime(), nullable=False),
        sa.Column("email", sa.String(254), nullable=False),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("totp_secret_sealed", sa.LargeBinary(), nullable=True),
        sa.Column("totp_confirmed_at", UTCDateTime(), nullable=True),
        sa.Column("last_totp_step", sa.Integer(), nullable=True),
        sa.Column("failed_attempts", sa.Integer(), nullable=False),
        sa.Column("locked_until", UTCDateTime(), nullable=True),
        sa.Column("must_change_password", sa.Boolean(), nullable=False),
        sa.Column("temp_password_expires_at", UTCDateTime(), nullable=True),
        sa.Column("disabled_at", UTCDateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_app_user"),
        sa.UniqueConstraint("email", name="uq_app_user_email"),
        sa.CheckConstraint("role in ('admin', 'buyer')", name="ck_app_user_role_known"),
    )
    op.create_table(
        "session",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("created_at", UTCDateTime(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("stage", sa.String(8), nullable=False),
        sa.Column("csrf_token", sa.String(64), nullable=False),
        sa.Column("last_seen_at", UTCDateTime(), nullable=False),
        sa.Column("expires_at", UTCDateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"], ["app_user.id"], name="fk_session_user_id_app_user", ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_session"),
        sa.UniqueConstraint("token_hash", name="uq_session_token_hash"),
        sa.CheckConstraint("stage in ('pending', 'full')", name="ck_session_stage_known"),
    )
    op.create_index("ix_session_user_id", "session", ["user_id"])
    op.create_table(
        "recovery_code",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("created_at", UTCDateTime(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("code_hash", sa.String(64), nullable=False),
        sa.Column("used_at", UTCDateTime(), nullable=True),
        sa.ForeignKeyConstraint(
            ["user_id"], ["app_user.id"], name="fk_recovery_code_user_id_app_user", ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_recovery_code"),
        sa.UniqueConstraint("code_hash", name="uq_recovery_code_code_hash"),
    )
    op.create_index("ix_recovery_code_user_id", "recovery_code", ["user_id"])
    op.create_table(
        "audit_log",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("created_at", UTCDateTime(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("client_address", sa.String(64), nullable=True),
        sa.Column("action", sa.String(64), nullable=False),
        sa.Column("object", sa.String(128), nullable=True),
        sa.Column("before", sa.Text(), nullable=True),
        sa.Column("after", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(
            ["user_id"], ["app_user.id"], name="fk_audit_log_user_id_app_user", ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_audit_log"),
    )
    op.create_index("ix_audit_log_created_at", "audit_log", ["created_at"])
    op.create_index("ix_audit_log_action", "audit_log", ["action"])


def downgrade() -> None:
    op.drop_table("audit_log")
    op.drop_table("recovery_code")
    op.drop_table("session")
    op.drop_table("app_user")
