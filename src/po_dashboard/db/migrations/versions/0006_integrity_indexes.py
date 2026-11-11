"""Integrity and lookup indexes found in the engineering review (2026-11-10)

- one stored message per provider id within a mailbox (re-scans and racing scans cannot store it twice)
- stored messages sorted newest first
- one invoice number per PO whatever its capitals (the app already checked this; now the database does too)

Revision ID: 0006
Revises: 0005
"""

import sqlalchemy as sa
from alembic import op

revision = "0006"
down_revision = "0005"


def upgrade() -> None:
    with op.batch_alter_table("source_message") as batch:
        batch.create_unique_constraint("uq_source_message_mailbox_id_provider_id", ["mailbox_id", "provider_id"])
        batch.create_index("ix_source_message_received_at", ["received_at"])
    op.create_index(
        "uq_invoice_po_id_lower_invoice_number", "invoice", ["po_id", sa.text("lower(invoice_number)")], unique=True
    )


def downgrade() -> None:
    op.drop_index("uq_invoice_po_id_lower_invoice_number", table_name="invoice")
    with op.batch_alter_table("source_message") as batch:
        batch.drop_index("ix_source_message_received_at")
        batch.drop_constraint("uq_source_message_mailbox_id_provider_id", type_="unique")
