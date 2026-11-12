"""The customer side: customers, customer POs, sales invoices, allocations, history and settings (PD-8.1)

New tables only: nothing existing is changed, so every purchasing record is untouched (DEC-56).

Revision ID: 0010
Revises: 0009
"""

from typing import Any

import sqlalchemy as sa
from alembic import op

from po_dashboard.customers.config import DEFAULTS
from po_dashboard.db.base import UTCDateTime, utcnow
from po_dashboard.db.types import Money, Quantity

revision = "0010"
down_revision = "0009"

TABLES = (
    "customer",
    "customer_name",
    "customer_domain",
    "customer_po",
    "customer_po_line",
    "customer_po_intake",
    "sales_import",
    "sales_import_row",
    "sales_invoice",
    "sales_invoice_observation",
    "sales_invoice_line",
    "allocation",
    "reconciliation_event",
    "setting",
)


def fk(table: str, column: str, target: str, ondelete: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint([column], [f"{target}.id"], name=f"fk_{table}_{column}_{target}", ondelete=ondelete)


def ck(table: str, name: str, rule: str) -> sa.CheckConstraint:
    return sa.CheckConstraint(rule, name=f"ck_{table}_{name}")


def one_of(column: str, values: tuple[str, ...]) -> str:
    return f"{column} in ({', '.join(repr(v) for v in values)})"


def pk(table: str) -> sa.PrimaryKeyConstraint:
    return sa.PrimaryKeyConstraint("id", name=f"pk_{table}")


def idcol() -> "sa.Column[int]":
    return sa.Column("id", sa.Integer(), nullable=False)


def created() -> "sa.Column[Any]":
    return sa.Column("created_at", UTCDateTime(), nullable=False)


def upgrade() -> None:
    op.create_table(
        "customer",
        idcol(),
        created(),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("name_key", sa.String(200), nullable=False),
        sa.Column("contact_name", sa.String(200), nullable=True),
        sa.Column("contact_email", sa.String(254), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        pk("customer"),
        sa.UniqueConstraint("name_key", name="uq_customer_name_key"),
    )

    op.create_table(
        "customer_name",
        idcol(),
        created(),
        sa.Column("customer_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("name_key", sa.String(200), nullable=False),
        fk("customer_name", "customer_id", "customer", "CASCADE"),
        pk("customer_name"),
        sa.UniqueConstraint("name_key", name="uq_customer_name_name_key"),
    )
    op.create_index("ix_customer_name_customer_id", "customer_name", ["customer_id"])

    op.create_table(
        "customer_domain",
        idcol(),
        created(),
        sa.Column("customer_id", sa.Integer(), nullable=False),
        sa.Column("domain", sa.String(253), nullable=False),
        ck("customer_domain", "lower_case", "domain = lower(domain)"),
        fk("customer_domain", "customer_id", "customer", "CASCADE"),
        pk("customer_domain"),
        sa.UniqueConstraint("domain", name="uq_customer_domain_domain"),
    )
    op.create_index("ix_customer_domain_customer_id", "customer_domain", ["customer_id"])

    op.create_table(
        "customer_po",
        idcol(),
        created(),
        sa.Column("customer_id", sa.Integer(), nullable=False),
        sa.Column("po_number", sa.String(64), nullable=False),
        sa.Column("po_key", sa.String(64), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("po_date", sa.Date(), nullable=True),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("stated_total", Money(), nullable=True),
        sa.Column("source", sa.String(16), nullable=False),
        sa.Column("source_document_id", sa.Integer(), nullable=True),
        sa.Column("source_ref", sa.String(200), nullable=True),
        sa.Column("created_by", sa.Integer(), nullable=True),
        sa.Column("approved_by", sa.Integer(), nullable=True),
        sa.Column("approved_at", UTCDateTime(), nullable=True),
        sa.Column("closed_by", sa.Integer(), nullable=True),
        sa.Column("closed_at", UTCDateTime(), nullable=True),
        sa.Column("close_note", sa.String(500), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        ck(
            "customer_po",
            "state_known",
            one_of("state", ("pending", "active", "superseded", "rejected", "closed", "cancelled")),
        ),
        ck("customer_po", "source_known", one_of("source", ("manual", "import", "document"))),
        ck("customer_po", "revision_not_negative", "revision >= 0"),
        ck("customer_po", "currency_iso_code", "length(currency) = 3 and currency = upper(currency)"),
        fk("customer_po", "customer_id", "customer", "RESTRICT"),
        fk("customer_po", "source_document_id", "source_document", "RESTRICT"),
        fk("customer_po", "created_by", "app_user", "SET NULL"),
        fk("customer_po", "approved_by", "app_user", "SET NULL"),
        fk("customer_po", "closed_by", "app_user", "SET NULL"),
        pk("customer_po"),
        sa.UniqueConstraint("customer_id", "po_key", "revision", name="uq_customer_po_customer_id_po_key_revision"),
    )
    op.create_index("ix_customer_po_customer_id", "customer_po", ["customer_id"])
    op.create_index("ix_customer_po_source_document_id", "customer_po", ["source_document_id"])

    op.create_table(
        "customer_po_line",
        idcol(),
        created(),
        sa.Column("customer_po_id", sa.Integer(), nullable=False),
        sa.Column("line_no", sa.Integer(), nullable=False),
        sa.Column("item", sa.String(100), nullable=False),
        sa.Column("description", sa.String(500), nullable=True),
        sa.Column("qty", Quantity(), nullable=False),
        sa.Column("unit", sa.String(16), nullable=True),
        sa.Column("unit_price", Money(), nullable=False),
        sa.Column("stated_amount", Money(), nullable=True),
        ck("customer_po_line", "qty_positive", "qty > 0"),
        ck("customer_po_line", "price_not_negative", "unit_price >= 0"),
        fk("customer_po_line", "customer_po_id", "customer_po", "CASCADE"),
        pk("customer_po_line"),
        sa.UniqueConstraint("customer_po_id", "line_no", name="uq_customer_po_line_customer_po_id_line_no"),
    )
    op.create_index("ix_customer_po_line_customer_po_id", "customer_po_line", ["customer_po_id"])

    op.create_table(
        "customer_po_intake",
        idcol(),
        created(),
        sa.Column("source", sa.String(16), nullable=False),
        sa.Column("payload", sa.Text(), nullable=False),
        sa.Column("reason", sa.String(200), nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("created_by", sa.Integer(), nullable=True),
        sa.Column("resolved_by", sa.Integer(), nullable=True),
        sa.Column("resolved_at", UTCDateTime(), nullable=True),
        sa.Column("customer_po_id", sa.Integer(), nullable=True),
        ck("customer_po_intake", "state_known", one_of("state", ("waiting", "done", "rejected"))),
        ck("customer_po_intake", "source_known", one_of("source", ("manual", "import", "document"))),
        fk("customer_po_intake", "created_by", "app_user", "SET NULL"),
        fk("customer_po_intake", "resolved_by", "app_user", "SET NULL"),
        fk("customer_po_intake", "customer_po_id", "customer_po", "SET NULL"),
        pk("customer_po_intake"),
    )

    op.create_table(
        "sales_import",
        idcol(),
        created(),
        sa.Column("created_by", sa.Integer(), nullable=True),
        sa.Column("file_name", sa.String(255), nullable=False),
        sa.Column("file_hash", sa.String(64), nullable=False),
        sa.Column("stored_path", sa.String(200), nullable=False),
        sa.Column("mapping", sa.Text(), nullable=False),
        sa.Column("period_from", sa.Date(), nullable=True),
        sa.Column("period_to", sa.Date(), nullable=True),
        sa.Column("counts", sa.Text(), nullable=True),
        fk("sales_import", "created_by", "app_user", "SET NULL"),
        pk("sales_import"),
    )
    op.create_index("ix_sales_import_file_hash", "sales_import", ["file_hash"])

    op.create_table(
        "sales_import_row",
        idcol(),
        sa.Column("import_id", sa.Integer(), nullable=False),
        sa.Column("row_number", sa.Integer(), nullable=False),
        sa.Column("cells", sa.Text(), nullable=False),
        sa.Column("outcome", sa.String(16), nullable=False),
        sa.Column("reason", sa.String(200), nullable=True),
        ck("sales_import_row", "outcome_known", one_of("outcome", ("line", "heading", "total", "unusable"))),
        fk("sales_import_row", "import_id", "sales_import", "CASCADE"),
        pk("sales_import_row"),
        sa.UniqueConstraint("import_id", "row_number", name="uq_sales_import_row_import_id_row_number"),
    )
    op.create_index("ix_sales_import_row_import_id", "sales_import_row", ["import_id"])

    op.create_table(
        "sales_invoice",
        idcol(),
        created(),
        sa.Column("kind", sa.String(12), nullable=False),
        sa.Column("invoice_number", sa.String(64), nullable=False),
        sa.Column("invoice_key", sa.String(64), nullable=False),
        sa.Column("customer_id", sa.Integer(), nullable=True),
        sa.Column("customer_name_exported", sa.String(200), nullable=True),
        sa.Column("invoice_date", sa.Date(), nullable=True),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("total", Money(), nullable=True),
        sa.Column("po_reference", sa.String(200), nullable=True),
        sa.Column("qb_reference", sa.String(64), nullable=True),
        sa.Column("level", sa.String(8), nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        ck("sales_invoice", "kind_known", one_of("kind", ("invoice", "credit_memo"))),
        ck("sales_invoice", "level_known", one_of("level", ("line", "invoice"))),
        ck("sales_invoice", "state_known", one_of("state", ("accepted", "needs_review"))),
        ck("sales_invoice", "currency_iso_code", "length(currency) = 3 and currency = upper(currency)"),
        fk("sales_invoice", "customer_id", "customer", "RESTRICT"),
        pk("sales_invoice"),
        sa.UniqueConstraint("kind", "invoice_key", name="uq_sales_invoice_kind_invoice_key"),
    )
    op.create_index("ix_sales_invoice_customer_id", "sales_invoice", ["customer_id"])

    op.create_table(
        "sales_invoice_observation",
        idcol(),
        created(),
        sa.Column("invoice_id", sa.Integer(), nullable=False),
        sa.Column("import_id", sa.Integer(), nullable=False),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("outcome", sa.String(16), nullable=False),
        ck(
            "sales_invoice_observation", "outcome_known", one_of("outcome", ("first", "same", "changed", "conflicting"))
        ),
        fk("sales_invoice_observation", "invoice_id", "sales_invoice", "CASCADE"),
        fk("sales_invoice_observation", "import_id", "sales_import", "CASCADE"),
        pk("sales_invoice_observation"),
    )
    op.create_index("ix_sales_invoice_observation_invoice_id", "sales_invoice_observation", ["invoice_id"])
    op.create_index("ix_sales_invoice_observation_import_id", "sales_invoice_observation", ["import_id"])

    op.create_table(
        "sales_invoice_line",
        idcol(),
        created(),
        sa.Column("invoice_id", sa.Integer(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("item", sa.String(100), nullable=True),
        sa.Column("description", sa.String(500), nullable=True),
        sa.Column("qty", Quantity(), nullable=True),
        sa.Column("unit_price", Money(), nullable=True),
        sa.Column("amount", Money(), nullable=False),
        sa.Column("kind", sa.String(12), nullable=False),
        ck("sales_invoice_line", "kind_known", one_of("kind", ("goods", "freight", "tax", "discount", "other"))),
        fk("sales_invoice_line", "invoice_id", "sales_invoice", "CASCADE"),
        pk("sales_invoice_line"),
        sa.UniqueConstraint("invoice_id", "position", name="uq_sales_invoice_line_invoice_id_position"),
    )
    op.create_index("ix_sales_invoice_line_invoice_id", "sales_invoice_line", ["invoice_id"])

    op.create_table(
        "allocation",
        idcol(),
        created(),
        sa.Column("customer_po_id", sa.Integer(), nullable=False),
        sa.Column("customer_po_line_id", sa.Integer(), nullable=True),
        sa.Column("sales_invoice_id", sa.Integer(), nullable=False),
        sa.Column("sales_invoice_line_id", sa.Integer(), nullable=True),
        sa.Column("qty", Quantity(), nullable=True),
        sa.Column("amount", Money(), nullable=True),
        sa.Column("basis", sa.String(8), nullable=False),
        sa.Column("state", sa.String(12), nullable=False),
        sa.Column("decided_by", sa.Integer(), nullable=True),
        sa.Column("decided_at", UTCDateTime(), nullable=True),
        sa.Column("reason", sa.String(500), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        ck("allocation", "basis_known", one_of("basis", ("rule", "person"))),
        ck("allocation", "state_known", one_of("state", ("proposed", "confirmed", "rejected"))),
        fk("allocation", "customer_po_id", "customer_po", "CASCADE"),
        fk("allocation", "customer_po_line_id", "customer_po_line", "CASCADE"),
        fk("allocation", "sales_invoice_id", "sales_invoice", "CASCADE"),
        fk("allocation", "sales_invoice_line_id", "sales_invoice_line", "CASCADE"),
        fk("allocation", "decided_by", "app_user", "SET NULL"),
        pk("allocation"),
    )
    for column in ("customer_po_id", "customer_po_line_id", "sales_invoice_id", "sales_invoice_line_id"):
        op.create_index(f"ix_allocation_{column}", "allocation", [column])

    op.create_table(
        "reconciliation_event",
        idcol(),
        created(),
        sa.Column("customer_po_id", sa.Integer(), nullable=False),
        sa.Column("status_before", sa.String(24), nullable=True),
        sa.Column("status_after", sa.String(24), nullable=False),
        sa.Column("exceptions", sa.Text(), nullable=True),
        sa.Column("cause", sa.String(24), nullable=False),
        sa.Column("cause_ref", sa.Integer(), nullable=True),
        fk("reconciliation_event", "customer_po_id", "customer_po", "CASCADE"),
        pk("reconciliation_event"),
    )
    op.create_index("ix_reconciliation_event_customer_po_id", "reconciliation_event", ["customer_po_id"])

    setting = op.create_table(
        "setting",
        sa.Column("key", sa.String(64), nullable=False),
        sa.Column("value", sa.Text(), nullable=False),
        sa.Column("updated_at", UTCDateTime(), nullable=False),
        sa.Column("updated_by", sa.Integer(), nullable=True),
        fk("setting", "updated_by", "app_user", "SET NULL"),
        sa.PrimaryKeyConstraint("key", name="pk_setting"),
    )
    now = utcnow()
    op.bulk_insert(setting, [{"key": k, "value": v, "updated_at": now} for k, v in DEFAULTS.items()])


def downgrade() -> None:
    for table in reversed(TABLES):
        op.drop_table(table)
