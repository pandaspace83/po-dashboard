"""The customer side (Phase 8, ARCHITECTURE section 16.1, DEC-56 to DEC-59). Separate from `purchase_order`: a customer
PO is what a customer wants from RaceSpec, a purchase order is what RaceSpec wants from a vendor.

Names and numbers that must be unique "ignoring case" are held twice: as typed, and as a `*_key` (lower case, spaces
squeezed, or letters and digits only) that carries the unique rule, so the database itself refuses the second spelling
and SQLAlchemy can still compare the schema with the migration."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Boolean, CheckConstraint, Date, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from po_dashboard.db.base import Base, UTCDateTime, utcnow
from po_dashboard.db.types import Money, Quantity

PO_STATES = ("pending", "active", "superseded", "rejected", "closed", "cancelled")
PO_SOURCES = ("manual", "import", "document")
INTAKE_STATES = ("waiting", "done", "rejected")
INVOICE_KINDS = ("invoice", "credit_memo")
INVOICE_LEVELS = ("line", "invoice")
INVOICE_STATES = ("accepted", "needs_review")
OBSERVATION_OUTCOMES = ("first", "same", "changed", "conflicting")
LINE_KINDS = ("goods", "freight", "tax", "discount", "other")
ROW_OUTCOMES = ("line", "heading", "total", "unusable")
ALLOCATION_BASES = ("rule", "person")
ALLOCATION_STATES = ("proposed", "confirmed", "rejected")


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} in ({', '.join(repr(v) for v in values)})"


class Customer(Base):
    __tablename__ = "customer"

    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    name: Mapped[str] = mapped_column(String(200))
    name_key: Mapped[str] = mapped_column(String(200), unique=True)  # lower case, spaces squeezed
    contact_name: Mapped[str | None] = mapped_column(String(200), default=None)
    contact_email: Mapped[str | None] = mapped_column(String(254), default=None)
    notes: Mapped[str | None] = mapped_column(Text, default=None)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    version: Mapped[int] = mapped_column(Integer, server_default="1")  # DEC-52

    __mapper_args__ = {"version_id_col": version}


class CustomerName(Base):
    """A name QuickBooks writes for a customer in an export. An export finds its customer by an exact one of these."""

    __tablename__ = "customer_name"

    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customer.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    name_key: Mapped[str] = mapped_column(String(200), unique=True)


class CustomerDomain(Base):
    """An email domain a customer writes from, so the email reader can find the customer (PD-8.8)."""

    __tablename__ = "customer_domain"
    __table_args__ = (CheckConstraint("domain = lower(domain)", name="lower_case"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customer.id", ondelete="CASCADE"), index=True)
    domain: Mapped[str] = mapped_column(String(253), unique=True)


class CustomerPO(Base):
    """A purchase order from a customer. Revision 0 is the original; a later revision is pending until a person approves
    it (DEC-57)."""

    __tablename__ = "customer_po"
    __table_args__ = (
        UniqueConstraint("customer_id", "po_key", "revision", name="uq_customer_po_customer_id_po_key_revision"),
        CheckConstraint(_in("state", PO_STATES), name="state_known"),
        CheckConstraint(_in("source", PO_SOURCES), name="source_known"),
        CheckConstraint("revision >= 0", name="revision_not_negative"),
        CheckConstraint("length(currency) = 3 and currency = upper(currency)", name="currency_iso_code"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customer.id", ondelete="RESTRICT"), index=True)
    po_number: Mapped[str] = mapped_column(String(64))  # the customer's own number, as written
    po_key: Mapped[str] = mapped_column(String(64))  # letters and digits only, lower case, no leading "po"
    revision: Mapped[int] = mapped_column(Integer, default=0)
    state: Mapped[str] = mapped_column(String(16), default="active")
    po_date: Mapped[date | None] = mapped_column(Date, default=None)
    currency: Mapped[str] = mapped_column(String(3))  # ISO 4217, never converted (DEC-24)
    stated_total: Mapped[Decimal | None] = mapped_column(Money, default=None)  # as written; the lines decide
    source: Mapped[str] = mapped_column(String(16), default="manual")
    source_document_id: Mapped[int | None] = mapped_column(
        ForeignKey("source_document.id", ondelete="RESTRICT"), default=None, index=True
    )
    source_ref: Mapped[str | None] = mapped_column(String(200), default=None)  # an import file's stored name
    created_by: Mapped[int | None] = mapped_column(ForeignKey("app_user.id", ondelete="SET NULL"), default=None)
    approved_by: Mapped[int | None] = mapped_column(ForeignKey("app_user.id", ondelete="SET NULL"), default=None)
    approved_at: Mapped[datetime | None] = mapped_column(UTCDateTime, default=None)
    closed_by: Mapped[int | None] = mapped_column(ForeignKey("app_user.id", ondelete="SET NULL"), default=None)
    closed_at: Mapped[datetime | None] = mapped_column(UTCDateTime, default=None)
    close_note: Mapped[str | None] = mapped_column(String(500), default=None)
    version: Mapped[int] = mapped_column(Integer, server_default="1")  # DEC-52

    __mapper_args__ = {"version_id_col": version}


class CustomerPOLine(Base):
    __tablename__ = "customer_po_line"
    __table_args__ = (
        UniqueConstraint("customer_po_id", "line_no", name="uq_customer_po_line_customer_po_id_line_no"),
        CheckConstraint("qty > 0", name="qty_positive"),
        CheckConstraint("unit_price >= 0", name="price_not_negative"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    customer_po_id: Mapped[int] = mapped_column(ForeignKey("customer_po.id", ondelete="CASCADE"), index=True)
    line_no: Mapped[int] = mapped_column(Integer)
    item: Mapped[str] = mapped_column(String(100))
    description: Mapped[str | None] = mapped_column(String(500), default=None)
    qty: Mapped[Decimal] = mapped_column(Quantity)
    unit: Mapped[str | None] = mapped_column(String(16), default=None)
    unit_price: Mapped[Decimal] = mapped_column(Money)
    stated_amount: Mapped[Decimal | None] = mapped_column(Money, default=None)  # as written, when the PO gives one


class CustomerPOIntake(Base):
    """Structured customer PO data that could not be filed yet, usually because its customer is not known: it waits for
    a person. `payload` is the data as it came (JSON), nothing guessed (DEC-57)."""

    __tablename__ = "customer_po_intake"
    __table_args__ = (
        CheckConstraint(_in("state", INTAKE_STATES), name="state_known"),
        CheckConstraint(_in("source", PO_SOURCES), name="source_known"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    source: Mapped[str] = mapped_column(String(16))
    payload: Mapped[str] = mapped_column(Text)
    reason: Mapped[str] = mapped_column(String(200))
    state: Mapped[str] = mapped_column(String(16), default="waiting")
    created_by: Mapped[int | None] = mapped_column(ForeignKey("app_user.id", ondelete="SET NULL"), default=None)
    resolved_by: Mapped[int | None] = mapped_column(ForeignKey("app_user.id", ondelete="SET NULL"), default=None)
    resolved_at: Mapped[datetime | None] = mapped_column(UTCDateTime, default=None)
    customer_po_id: Mapped[int | None] = mapped_column(ForeignKey("customer_po.id", ondelete="SET NULL"), default=None)


class SalesImport(Base):
    """One upload of a QuickBooks sales export: who, when, which file, how it was read, which dates it covers."""

    __tablename__ = "sales_import"

    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("app_user.id", ondelete="SET NULL"), default=None)
    file_name: Mapped[str] = mapped_column(String(255))  # for display only, never a path
    file_hash: Mapped[str] = mapped_column(String(64), index=True)
    stored_path: Mapped[str] = mapped_column(String(200))
    mapping: Mapped[str] = mapped_column(Text)  # JSON: which column held what
    period_from: Mapped[date | None] = mapped_column(Date, default=None)
    period_to: Mapped[date | None] = mapped_column(Date, default=None)
    counts: Mapped[str | None] = mapped_column(Text, default=None)  # JSON: rows per outcome


class SalesImportRow(Base):
    """Every row of an upload as it was read, so any invoice can be traced to its source (nothing is thrown away)."""

    __tablename__ = "sales_import_row"
    __table_args__ = (
        UniqueConstraint("import_id", "row_number", name="uq_sales_import_row_import_id_row_number"),
        CheckConstraint(_in("outcome", ROW_OUTCOMES), name="outcome_known"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    import_id: Mapped[int] = mapped_column(ForeignKey("sales_import.id", ondelete="CASCADE"), index=True)
    row_number: Mapped[int] = mapped_column(Integer)
    cells: Mapped[str] = mapped_column(Text)  # JSON
    outcome: Mapped[str] = mapped_column(String(16))
    reason: Mapped[str | None] = mapped_column(String(200), default=None)


class SalesInvoice(Base):
    __tablename__ = "sales_invoice"
    __table_args__ = (
        UniqueConstraint("kind", "invoice_key", name="uq_sales_invoice_kind_invoice_key"),
        CheckConstraint(_in("kind", INVOICE_KINDS), name="kind_known"),
        CheckConstraint(_in("level", INVOICE_LEVELS), name="level_known"),
        CheckConstraint(_in("state", INVOICE_STATES), name="state_known"),
        CheckConstraint("length(currency) = 3 and currency = upper(currency)", name="currency_iso_code"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    kind: Mapped[str] = mapped_column(String(12), default="invoice")
    invoice_number: Mapped[str] = mapped_column(String(64))  # as exported
    invoice_key: Mapped[str] = mapped_column(String(64))  # lower case, trimmed
    customer_id: Mapped[int | None] = mapped_column(
        ForeignKey("customer.id", ondelete="RESTRICT"), default=None, index=True
    )  # empty until the exported name is mapped to a customer, exactly
    customer_name_exported: Mapped[str | None] = mapped_column(String(200), default=None)
    invoice_date: Mapped[date | None] = mapped_column(Date, default=None)
    currency: Mapped[str] = mapped_column(String(3))
    total: Mapped[Decimal | None] = mapped_column(Money, default=None)
    po_reference: Mapped[str | None] = mapped_column(String(200), default=None)  # as exported, never corrected
    qb_reference: Mapped[str | None] = mapped_column(String(64), default=None)
    level: Mapped[str] = mapped_column(String(8), default="invoice")
    state: Mapped[str] = mapped_column(String(16), default="accepted")
    version: Mapped[int] = mapped_column(Integer, server_default="1")  # DEC-52

    __mapper_args__ = {"version_id_col": version}


class SalesInvoiceObservation(Base):
    """Each time an upload showed an invoice. Comparing the content with what was accepted is how a changed or a
    conflicting invoice is found, and nothing accepted is overwritten (DEC-58)."""

    __tablename__ = "sales_invoice_observation"
    __table_args__ = (CheckConstraint(_in("outcome", OBSERVATION_OUTCOMES), name="outcome_known"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    invoice_id: Mapped[int] = mapped_column(ForeignKey("sales_invoice.id", ondelete="CASCADE"), index=True)
    import_id: Mapped[int] = mapped_column(ForeignKey("sales_import.id", ondelete="CASCADE"), index=True)
    fingerprint: Mapped[str] = mapped_column(String(64))
    content: Mapped[str] = mapped_column(Text)  # JSON
    outcome: Mapped[str] = mapped_column(String(16))


class SalesInvoiceLine(Base):
    __tablename__ = "sales_invoice_line"
    __table_args__ = (
        UniqueConstraint("invoice_id", "position", name="uq_sales_invoice_line_invoice_id_position"),
        CheckConstraint(_in("kind", LINE_KINDS), name="kind_known"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    invoice_id: Mapped[int] = mapped_column(ForeignKey("sales_invoice.id", ondelete="CASCADE"), index=True)
    position: Mapped[int] = mapped_column(Integer)
    item: Mapped[str | None] = mapped_column(String(100), default=None)
    description: Mapped[str | None] = mapped_column(String(500), default=None)
    qty: Mapped[Decimal | None] = mapped_column(Quantity, default=None)
    unit_price: Mapped[Decimal | None] = mapped_column(Money, default=None)
    amount: Mapped[Decimal] = mapped_column(Money)  # a credit memo or a discount may be negative
    kind: Mapped[str] = mapped_column(String(12), default="goods")


class Allocation(Base):
    """A link between customer PO evidence and sales invoice evidence. A PO may have many invoices and an invoice many
    POs (never assumed one to one). A line may be empty for an invoice-level match."""

    __tablename__ = "allocation"
    __table_args__ = (
        CheckConstraint(_in("basis", ALLOCATION_BASES), name="basis_known"),
        CheckConstraint(_in("state", ALLOCATION_STATES), name="state_known"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    customer_po_id: Mapped[int] = mapped_column(ForeignKey("customer_po.id", ondelete="CASCADE"), index=True)
    customer_po_line_id: Mapped[int | None] = mapped_column(
        ForeignKey("customer_po_line.id", ondelete="CASCADE"), default=None, index=True
    )
    sales_invoice_id: Mapped[int] = mapped_column(ForeignKey("sales_invoice.id", ondelete="CASCADE"), index=True)
    sales_invoice_line_id: Mapped[int | None] = mapped_column(
        ForeignKey("sales_invoice_line.id", ondelete="CASCADE"), default=None, index=True
    )
    qty: Mapped[Decimal | None] = mapped_column(Quantity, default=None)
    amount: Mapped[Decimal | None] = mapped_column(Money, default=None)
    basis: Mapped[str] = mapped_column(String(8), default="rule")
    state: Mapped[str] = mapped_column(String(12), default="proposed")
    decided_by: Mapped[int | None] = mapped_column(ForeignKey("app_user.id", ondelete="SET NULL"), default=None)
    decided_at: Mapped[datetime | None] = mapped_column(UTCDateTime, default=None)
    reason: Mapped[str | None] = mapped_column(String(500), default=None)
    version: Mapped[int] = mapped_column(Integer, server_default="1")  # DEC-52

    __mapper_args__ = {"version_id_col": version}


class ReconciliationEvent(Base):
    """The history of a customer PO's reconciliation: each status change, the exceptions then, and its cause."""

    __tablename__ = "reconciliation_event"

    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    customer_po_id: Mapped[int] = mapped_column(ForeignKey("customer_po.id", ondelete="CASCADE"), index=True)
    status_before: Mapped[str | None] = mapped_column(String(24), default=None)
    status_after: Mapped[str] = mapped_column(String(24))
    exceptions: Mapped[str | None] = mapped_column(Text, default=None)  # JSON
    cause: Mapped[str] = mapped_column(String(24))  # upload, person, revision, po_change
    cause_ref: Mapped[int | None] = mapped_column(Integer, default=None)


class Setting(Base):
    """A named setting: tolerances, the unbilled days, charge item names, the default currency (DEC-59)."""

    __tablename__ = "setting"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_by: Mapped[int | None] = mapped_column(ForeignKey("app_user.id", ondelete="SET NULL"), default=None)
