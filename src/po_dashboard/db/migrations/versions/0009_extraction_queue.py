"""The extraction queue, tracking numbers, invoice charges, and a real link from entries to their document (PD-4.0a)

`extraction` is the work queue and the review record (DEC-54). `shipment_tracking` holds every tracking number of a
shipment notice, `invoice_charge` the charges that are not goods (DEC-48). The `source_document_id` on an
acknowledgement, shipment or invoice was an empty, unchecked number; it becomes a foreign key to `source_document`
(entries typed by hand keep it empty).

Revision ID: 0009
Revises: 0008
"""

import sqlalchemy as sa
from alembic import op

from po_dashboard.db.base import UTCDateTime
from po_dashboard.db.types import Money

revision = "0009"
down_revision = "0008"

LINKED = ("acknowledgement", "shipment", "invoice")


def upgrade() -> None:
    op.create_table(
        "extraction",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("created_at", UTCDateTime(), nullable=False),
        sa.Column("document_id", sa.Integer(), nullable=False),
        sa.Column("model", sa.String(100), nullable=False),
        sa.Column("prompt_version", sa.Integer(), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("last_error", sa.String(200), nullable=True),
        sa.Column("next_attempt_at", UTCDateTime(), nullable=True),
        sa.Column("started_at", UTCDateTime(), nullable=True),
        sa.Column("finished_at", UTCDateTime(), nullable=True),
        sa.Column("seconds", sa.Float(), nullable=True),
        sa.Column("prompt_tokens", sa.Integer(), nullable=True),
        sa.Column("answer", sa.Text(), nullable=True),
        sa.Column("doc_type", sa.String(32), nullable=True),
        sa.Column("po_number", sa.String(100), nullable=True),
        sa.Column("review_state", sa.String(16), nullable=True),
        sa.Column("po_id", sa.Integer(), nullable=True),
        sa.Column("vendor_id", sa.Integer(), nullable=True),
        sa.Column("flags", sa.Text(), nullable=True),
        sa.Column("final", sa.Text(), nullable=True),
        sa.Column("entry_kind", sa.String(16), nullable=True),
        sa.Column("entry_id", sa.Integer(), nullable=True),
        sa.Column("reviewed_by", sa.Integer(), nullable=True),
        sa.Column("reviewed_at", UTCDateTime(), nullable=True),
        sa.Column("review_note", sa.String(500), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.CheckConstraint("state in ('queued', 'running', 'done', 'failed')", name="ck_extraction_state_known"),
        sa.CheckConstraint(
            "review_state is null or review_state in "
            "('waiting', 'unmatched', 'no_facts', 'accepted', 'rejected', 'dismissed', 'superseded')",
            name="ck_extraction_review_state_known",
        ),
        sa.CheckConstraint("attempts >= 0", name="ck_extraction_attempts_not_negative"),
        sa.ForeignKeyConstraint(
            ["document_id"], ["source_document.id"], name="fk_extraction_document_id_source_document",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["po_id"], ["purchase_order.id"], name="fk_extraction_po_id_purchase_order", ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["vendor_id"], ["vendor.id"], name="fk_extraction_vendor_id_vendor", ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["reviewed_by"], ["app_user.id"], name="fk_extraction_reviewed_by_app_user", ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_extraction"),
        sa.UniqueConstraint("document_id", "prompt_version", "model", name="uq_extraction_document_prompt_model"),
    )
    op.create_index("ix_extraction_document_id", "extraction", ["document_id"])
    op.create_index("ix_extraction_queue", "extraction", ["state", "next_attempt_at"])

    op.create_table(
        "shipment_tracking",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("created_at", UTCDateTime(), nullable=False),
        sa.Column("shipment_id", sa.Integer(), nullable=False),
        sa.Column("tracking_number", sa.String(100), nullable=False),
        sa.Column("carrier", sa.String(100), nullable=True),
        sa.ForeignKeyConstraint(
            ["shipment_id"], ["shipment.id"], name="fk_shipment_tracking_shipment_id_shipment", ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_shipment_tracking"),
        sa.UniqueConstraint("shipment_id", "tracking_number", name="uq_shipment_tracking_shipment_id_number"),
    )
    op.create_index("ix_shipment_tracking_shipment_id", "shipment_tracking", ["shipment_id"])

    op.create_table(
        "invoice_charge",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("created_at", UTCDateTime(), nullable=False),
        sa.Column("invoice_id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("label", sa.String(200), nullable=False),
        sa.Column("amount", Money(), nullable=False),
        sa.CheckConstraint("kind in ('freight', 'tariff', 'tax', 'other')", name="ck_invoice_charge_kind_known"),
        sa.ForeignKeyConstraint(
            ["invoice_id"], ["invoice.id"], name="fk_invoice_charge_invoice_id_invoice", ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_invoice_charge"),
    )
    op.create_index("ix_invoice_charge_invoice_id", "invoice_charge", ["invoice_id"])

    for table in LINKED:
        with op.batch_alter_table(table) as batch:
            batch.create_foreign_key(
                f"fk_{table}_source_document_id_source_document", "source_document", ["source_document_id"], ["id"],
                ondelete="RESTRICT",
            )  # fmt: skip
            batch.create_index(f"ix_{table}_source_document_id", ["source_document_id"])
    # SQLite rebuilds the table for the batch, and a rebuild cannot copy an index on an expression: migration 0006's
    # "INV-1 and inv-1 are one invoice" index is put back here (tests/test_concurrency.py proves it holds).
    op.create_index(
        "uq_invoice_po_id_lower_invoice_number", "invoice", ["po_id", sa.text("lower(invoice_number)")], unique=True
    )


def downgrade() -> None:
    op.drop_index("uq_invoice_po_id_lower_invoice_number", table_name="invoice")  # the rebuild below loses it anyway
    for table in reversed(LINKED):
        with op.batch_alter_table(table) as batch:
            batch.drop_index(f"ix_{table}_source_document_id")
            batch.drop_constraint(f"fk_{table}_source_document_id_source_document", type_="foreignkey")
    op.create_index(
        "uq_invoice_po_id_lower_invoice_number", "invoice", ["po_id", sa.text("lower(invoice_number)")], unique=True
    )
    op.drop_table("invoice_charge")
    op.drop_table("shipment_tracking")
    op.drop_table("extraction")
