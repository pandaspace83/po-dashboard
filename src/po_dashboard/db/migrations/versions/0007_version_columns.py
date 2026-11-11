"""A version number on the records people edit (DEC-52)

A form carries the version it was drawn from; a save from an out-of-date page is refused instead of overwriting.

Revision ID: 0007
Revises: 0006
"""

import sqlalchemy as sa
from alembic import op

revision = "0007"
down_revision = "0006"


def upgrade() -> None:
    for table in ("vendor", "po_line"):
        op.add_column(table, sa.Column("version", sa.Integer(), nullable=False, server_default="1"))


def downgrade() -> None:
    for table in ("po_line", "vendor"):
        with op.batch_alter_table(table) as batch:
            batch.drop_column("version")
