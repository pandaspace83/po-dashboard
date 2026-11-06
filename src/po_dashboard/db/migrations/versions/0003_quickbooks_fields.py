"""The QuickBooks starting load (PD-1.10a, DEC-46): a PO's own date in QuickBooks, and the customer a line is for

Revision ID: 0003
Revises: 0002
"""

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"


SOURCES_BEFORE = "source in ('manual', 'csv', 'document')"
SOURCES = "source in ('manual', 'csv', 'document', 'quickbooks')"


def upgrade() -> None:
    # SQLite cannot change a CHECK in place; the batch copies the table with the new rule (no data is changed).
    with op.batch_alter_table("purchase_order", recreate="always") as batch:
        batch.add_column(sa.Column("po_date", sa.Date(), nullable=True))
        batch.drop_constraint("ck_purchase_order_source_known", type_="check")
        batch.create_check_constraint("ck_purchase_order_source_known", sa.text(SOURCES))
    with op.batch_alter_table("po_line") as batch:
        batch.add_column(sa.Column("customer", sa.String(200), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("po_line") as batch:
        batch.drop_column("customer")
    with op.batch_alter_table("purchase_order", recreate="always") as batch:
        batch.drop_constraint("ck_purchase_order_source_known", type_="check")
        batch.create_check_constraint("ck_purchase_order_source_known", sa.text(SOURCES_BEFORE))
        batch.drop_column("po_date")
