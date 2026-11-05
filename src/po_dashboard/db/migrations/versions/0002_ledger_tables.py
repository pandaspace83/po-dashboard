"""The purchase-order ledger: vendors, POs and lines, acknowledgements, shipments, invoices (PD-1.1b)

Revision ID: 0002
Revises: 0001
"""

import sqlalchemy as sa
from alembic import op

from po_dashboard.db.base import UTCDateTime
from po_dashboard.db.types import Money, Quantity

revision = "0002"
down_revision = "0001"


def _id() -> sa.Column:  # type: ignore[type-arg]
    return sa.Column("id", sa.Integer(), nullable=False)


def _created() -> sa.Column:  # type: ignore[type-arg]
    return sa.Column("created_at", UTCDateTime(), nullable=False)


def upgrade() -> None:
    op.create_table(
        "vendor",
        _id(),
        _created(),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("contact_name", sa.String(200), nullable=True),
        sa.Column("contact_email", sa.String(254), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_vendor"),
        sa.UniqueConstraint("name", name="uq_vendor_name"),
    )
    op.create_table(
        "vendor_domain",
        _id(),
        _created(),
        sa.Column("vendor_id", sa.Integer(), nullable=False),
        sa.Column("domain", sa.String(253), nullable=False),
        sa.ForeignKeyConstraint(
            ["vendor_id"], ["vendor.id"], name="fk_vendor_domain_vendor_id_vendor", ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_vendor_domain"),
        sa.UniqueConstraint("domain", name="uq_vendor_domain_domain"),
        sa.CheckConstraint("domain = lower(domain)", name="ck_vendor_domain_lower_case"),
    )
    op.create_index("ix_vendor_domain_vendor_id", "vendor_domain", ["vendor_id"])
    op.create_table(
        "purchase_order",
        _id(),
        _created(),
        sa.Column("po_number", sa.String(64), nullable=False),
        sa.Column("vendor_id", sa.Integer(), nullable=False),
        sa.Column("date_sent", sa.Date(), nullable=True),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("buyer", sa.String(254), nullable=True),
        sa.Column("source", sa.String(16), nullable=False),
        sa.Column("created_by", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(
            ["vendor_id"], ["vendor.id"], name="fk_purchase_order_vendor_id_vendor", ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["created_by"], ["app_user.id"], name="fk_purchase_order_created_by_app_user", ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_purchase_order"),
        sa.UniqueConstraint("po_number", name="uq_purchase_order_po_number"),
        sa.CheckConstraint(
            "length(currency) = 3 and currency = upper(currency)", name="ck_purchase_order_currency_iso_code"
        ),
        sa.CheckConstraint("source in ('manual', 'csv', 'document')", name="ck_purchase_order_source_known"),
    )
    op.create_index("ix_purchase_order_vendor_id", "purchase_order", ["vendor_id"])
    op.create_table(
        "po_line",
        _id(),
        _created(),
        sa.Column("po_id", sa.Integer(), nullable=False),
        sa.Column("line_no", sa.Integer(), nullable=False),
        sa.Column("item", sa.String(200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("ordered_qty", Quantity(), nullable=False),
        sa.Column("unit", sa.String(16), nullable=False),
        sa.Column("unit_price", Money(), nullable=False),
        sa.Column("requested_date", sa.Date(), nullable=True),
        sa.Column("closed_reason", sa.String(16), nullable=True),
        sa.Column("closed_at", UTCDateTime(), nullable=True),
        sa.Column("closed_by", sa.Integer(), nullable=True),
        sa.Column("close_note", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(
            ["po_id"], ["purchase_order.id"], name="fk_po_line_po_id_purchase_order", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["closed_by"], ["app_user.id"], name="fk_po_line_closed_by_app_user", ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_po_line"),
        sa.UniqueConstraint("po_id", "line_no", name="uq_po_line_po_id_line_no"),
        sa.CheckConstraint("ordered_qty > 0", name="ck_po_line_ordered_positive"),
        sa.CheckConstraint("unit_price >= 0", name="ck_po_line_price_not_negative"),
        sa.CheckConstraint("closed_reason in ('received', 'short', 'cancelled')", name="ck_po_line_close_reason_known"),
        sa.CheckConstraint(
            "(closed_reason is null) = (closed_at is null) and (closed_reason is null) = (close_note is null)",
            name="ck_po_line_closed_by_hand_is_complete",
        ),
    )
    op.create_index("ix_po_line_po_id", "po_line", ["po_id"])
    op.create_table(
        "acknowledgement",
        _id(),
        _created(),
        sa.Column("po_id", sa.Integer(), nullable=False),
        sa.Column("source_document_id", sa.Integer(), nullable=True),
        sa.Column("source_ref", sa.String(64), nullable=True),
        sa.Column("accepted_by", sa.Integer(), nullable=True),
        sa.Column("accepted_at", UTCDateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["po_id"], ["purchase_order.id"], name="fk_acknowledgement_po_id_purchase_order", ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["accepted_by"], ["app_user.id"], name="fk_acknowledgement_accepted_by_app_user", ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_acknowledgement"),
    )
    op.create_index("ix_acknowledgement_po_id", "acknowledgement", ["po_id"])
    op.create_table(
        "ack_line",
        _id(),
        _created(),
        sa.Column("ack_id", sa.Integer(), nullable=False),
        sa.Column("po_line_id", sa.Integer(), nullable=False),
        sa.Column("confirmed_qty", Quantity(), nullable=True),
        sa.Column("confirmed_price", Money(), nullable=True),
        sa.Column("promised_date", sa.Date(), nullable=True),
        sa.ForeignKeyConstraint(
            ["ack_id"], ["acknowledgement.id"], name="fk_ack_line_ack_id_acknowledgement", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["po_line_id"], ["po_line.id"], name="fk_ack_line_po_line_id_po_line", ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_ack_line"),
        sa.UniqueConstraint("ack_id", "po_line_id", name="uq_ack_line_ack_id_po_line_id"),
        sa.CheckConstraint("confirmed_qty >= 0", name="ck_ack_line_confirmed_not_negative"),
        sa.CheckConstraint("confirmed_price >= 0", name="ck_ack_line_confirmed_price_not_negative"),
    )
    op.create_index("ix_ack_line_ack_id", "ack_line", ["ack_id"])
    op.create_index("ix_ack_line_po_line_id", "ack_line", ["po_line_id"])
    op.create_table(
        "promised_date_history",
        _id(),
        _created(),
        sa.Column("po_line_id", sa.Integer(), nullable=False),
        sa.Column("promised_date", sa.Date(), nullable=False),
        sa.Column("ack_id", sa.Integer(), nullable=True),
        sa.Column("recorded_at", UTCDateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["po_line_id"], ["po_line.id"], name="fk_promised_date_history_po_line_id_po_line", ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["ack_id"],
            ["acknowledgement.id"],
            name="fk_promised_date_history_ack_id_acknowledgement",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_promised_date_history"),
    )
    op.create_index("ix_promised_date_history_po_line_id", "promised_date_history", ["po_line_id"])
    op.create_table(
        "shipment",
        _id(),
        _created(),
        sa.Column("po_id", sa.Integer(), nullable=False),
        sa.Column("carrier", sa.String(100), nullable=True),
        sa.Column("tracking_number", sa.String(100), nullable=True),
        sa.Column("shipped_at", sa.Date(), nullable=True),
        sa.Column("eta", sa.Date(), nullable=True),
        sa.Column("source_document_id", sa.Integer(), nullable=True),
        sa.Column("source_ref", sa.String(64), nullable=True),
        sa.Column("accepted_by", sa.Integer(), nullable=True),
        sa.Column("accepted_at", UTCDateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["po_id"], ["purchase_order.id"], name="fk_shipment_po_id_purchase_order", ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["accepted_by"], ["app_user.id"], name="fk_shipment_accepted_by_app_user", ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_shipment"),
    )
    op.create_index("ix_shipment_po_id", "shipment", ["po_id"])
    op.create_table(
        "shipment_line",
        _id(),
        _created(),
        sa.Column("shipment_id", sa.Integer(), nullable=False),
        sa.Column("po_line_id", sa.Integer(), nullable=False),
        sa.Column("qty", Quantity(), nullable=False),
        sa.Column("eta", sa.Date(), nullable=True),
        sa.ForeignKeyConstraint(
            ["shipment_id"], ["shipment.id"], name="fk_shipment_line_shipment_id_shipment", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["po_line_id"], ["po_line.id"], name="fk_shipment_line_po_line_id_po_line", ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_shipment_line"),
        sa.UniqueConstraint("shipment_id", "po_line_id", name="uq_shipment_line_shipment_id_po_line_id"),
        sa.CheckConstraint("qty > 0", name="ck_shipment_line_qty_positive"),
    )
    op.create_index("ix_shipment_line_shipment_id", "shipment_line", ["shipment_id"])
    op.create_index("ix_shipment_line_po_line_id", "shipment_line", ["po_line_id"])
    op.create_table(
        "invoice",
        _id(),
        _created(),
        sa.Column("po_id", sa.Integer(), nullable=False),
        sa.Column("invoice_number", sa.String(64), nullable=False),
        sa.Column("invoice_date", sa.Date(), nullable=True),
        sa.Column("total", Money(), nullable=True),
        sa.Column("source_document_id", sa.Integer(), nullable=True),
        sa.Column("source_ref", sa.String(64), nullable=True),
        sa.Column("accepted_by", sa.Integer(), nullable=True),
        sa.Column("accepted_at", UTCDateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["po_id"], ["purchase_order.id"], name="fk_invoice_po_id_purchase_order", ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["accepted_by"], ["app_user.id"], name="fk_invoice_accepted_by_app_user", ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_invoice"),
        sa.UniqueConstraint("po_id", "invoice_number", name="uq_invoice_po_id_invoice_number"),
        sa.CheckConstraint("total >= 0", name="ck_invoice_total_not_negative"),
    )
    op.create_index("ix_invoice_po_id", "invoice", ["po_id"])
    op.create_table(
        "invoice_line",
        _id(),
        _created(),
        sa.Column("invoice_id", sa.Integer(), nullable=False),
        sa.Column("po_line_id", sa.Integer(), nullable=False),
        sa.Column("qty", Quantity(), nullable=False),
        sa.Column("price", Money(), nullable=False),
        sa.ForeignKeyConstraint(
            ["invoice_id"], ["invoice.id"], name="fk_invoice_line_invoice_id_invoice", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["po_line_id"], ["po_line.id"], name="fk_invoice_line_po_line_id_po_line", ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_invoice_line"),
        sa.UniqueConstraint("invoice_id", "po_line_id", name="uq_invoice_line_invoice_id_po_line_id"),
        sa.CheckConstraint("qty > 0", name="ck_invoice_line_qty_positive"),
        sa.CheckConstraint("price >= 0", name="ck_invoice_line_price_not_negative"),
    )
    op.create_index("ix_invoice_line_invoice_id", "invoice_line", ["invoice_id"])
    op.create_index("ix_invoice_line_po_line_id", "invoice_line", ["po_line_id"])


def downgrade() -> None:
    for table in (
        "invoice_line", "invoice", "shipment_line", "shipment", "promised_date_history", "ack_line",
        "acknowledgement", "po_line", "purchase_order", "vendor_domain", "vendor",
    ):  # fmt: skip
        op.drop_table(table)
