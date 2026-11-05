"""The purchase-order ledger (ARCHITECTURE §4): vendors, POs and lines, and the accepted acknowledgements, shipments and
invoices. Status is computed from these (§5) and is never a column.

Plain facts only. The tables for mail, documents and extraction arrive with Phases 3 and 4; until then
`source_document_id` is an empty, unchecked number, and entries are made by a person (PD-1.4).
A purchase order is cancelled, never deleted: rows that record what a vendor said refuse to lose their purchase order.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import CheckConstraint, Date, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from po_dashboard.db.base import Base, UTCDateTime, utcnow
from po_dashboard.db.types import Money, Quantity

SOURCES = ("manual", "csv", "document")
CLOSE_REASONS = ("received", "short", "cancelled")


class Vendor(Base):
    __tablename__ = "vendor"

    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    name: Mapped[str] = mapped_column(String(200), unique=True)
    contact_name: Mapped[str | None] = mapped_column(String(200), default=None)
    contact_email: Mapped[str | None] = mapped_column(String(254), default=None)
    notes: Mapped[str | None] = mapped_column(Text, default=None)


class VendorDomain(Base):
    """An email domain a vendor writes from. A domain belongs to one vendor."""

    __tablename__ = "vendor_domain"
    __table_args__ = (CheckConstraint("domain = lower(domain)", name="lower_case"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    vendor_id: Mapped[int] = mapped_column(ForeignKey("vendor.id", ondelete="CASCADE"), index=True)
    domain: Mapped[str] = mapped_column(String(253), unique=True)


class PurchaseOrder(Base):
    __tablename__ = "purchase_order"
    __table_args__ = (
        CheckConstraint("length(currency) = 3 and currency = upper(currency)", name="currency_iso_code"),
        CheckConstraint("source in ('manual', 'csv', 'document')", name="source_known"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    po_number: Mapped[str] = mapped_column(String(64), unique=True)
    vendor_id: Mapped[int] = mapped_column(ForeignKey("vendor.id", ondelete="RESTRICT"), index=True)
    date_sent: Mapped[date | None] = mapped_column(Date, default=None)
    currency: Mapped[str] = mapped_column(String(3))  # ISO 4217; one per PO, never converted (DEC-24)
    buyer: Mapped[str | None] = mapped_column(String(254), default=None)
    source: Mapped[str] = mapped_column(String(16), default="manual")
    created_by: Mapped[int | None] = mapped_column(ForeignKey("app_user.id", ondelete="SET NULL"), default=None)


class POLine(Base):
    __tablename__ = "po_line"
    __table_args__ = (
        UniqueConstraint("po_id", "line_no", name="uq_po_line_po_id_line_no"),
        CheckConstraint("ordered_qty > 0", name="ordered_positive"),
        CheckConstraint("unit_price >= 0", name="price_not_negative"),
        CheckConstraint("closed_reason in ('received', 'short', 'cancelled')", name="close_reason_known"),
        # A line closed by hand has a reason, a time and a note; an open line has none of them (REQ-INV-04).
        CheckConstraint(
            "(closed_reason is null) = (closed_at is null) and (closed_reason is null) = (close_note is null)",
            name="closed_by_hand_is_complete",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    po_id: Mapped[int] = mapped_column(ForeignKey("purchase_order.id", ondelete="CASCADE"), index=True)
    line_no: Mapped[int] = mapped_column(Integer)
    item: Mapped[str] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(Text, default=None)
    ordered_qty: Mapped[Decimal] = mapped_column(Quantity)
    unit: Mapped[str] = mapped_column(String(16))  # never converted: a confirmation in another unit is flagged
    unit_price: Mapped[Decimal] = mapped_column(Money)
    requested_date: Mapped[date | None] = mapped_column(Date, default=None)
    closed_reason: Mapped[str | None] = mapped_column(String(16), default=None)  # empty while the line is open
    closed_at: Mapped[datetime | None] = mapped_column(UTCDateTime, default=None)
    closed_by: Mapped[int | None] = mapped_column(ForeignKey("app_user.id", ondelete="SET NULL"), default=None)
    close_note: Mapped[str | None] = mapped_column(Text, default=None)


class Acknowledgement(Base):
    __tablename__ = "acknowledgement"

    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    po_id: Mapped[int] = mapped_column(ForeignKey("purchase_order.id", ondelete="RESTRICT"), index=True)
    source_document_id: Mapped[int | None] = mapped_column(Integer, default=None)  # a real link from Phase 4
    source_ref: Mapped[str | None] = mapped_column(String(64), default=None)  # page or cell
    accepted_by: Mapped[int | None] = mapped_column(ForeignKey("app_user.id", ondelete="SET NULL"), default=None)
    accepted_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class AckLine(Base):
    """What the vendor confirmed for one line. A line may be confirmed short, or with no date (REQ-ACK-02)."""

    __tablename__ = "ack_line"
    __table_args__ = (
        UniqueConstraint("ack_id", "po_line_id", name="uq_ack_line_ack_id_po_line_id"),
        CheckConstraint("confirmed_qty >= 0", name="confirmed_not_negative"),
        CheckConstraint("confirmed_price >= 0", name="confirmed_price_not_negative"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    ack_id: Mapped[int] = mapped_column(ForeignKey("acknowledgement.id", ondelete="CASCADE"), index=True)
    po_line_id: Mapped[int] = mapped_column(ForeignKey("po_line.id", ondelete="RESTRICT"), index=True)
    confirmed_qty: Mapped[Decimal | None] = mapped_column(Quantity, default=None)
    confirmed_price: Mapped[Decimal | None] = mapped_column(Money, default=None)
    promised_date: Mapped[date | None] = mapped_column(Date, default=None)


class PromisedDateHistory(Base):
    """One row per accepted promised date, so a slip (a later date) is visible (REQ-ACK-04)."""

    __tablename__ = "promised_date_history"

    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    po_line_id: Mapped[int] = mapped_column(ForeignKey("po_line.id", ondelete="RESTRICT"), index=True)
    promised_date: Mapped[date] = mapped_column(Date)
    ack_id: Mapped[int | None] = mapped_column(ForeignKey("acknowledgement.id", ondelete="RESTRICT"), default=None)
    recorded_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class Shipment(Base):
    __tablename__ = "shipment"

    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    po_id: Mapped[int] = mapped_column(ForeignKey("purchase_order.id", ondelete="RESTRICT"), index=True)
    carrier: Mapped[str | None] = mapped_column(String(100), default=None)
    tracking_number: Mapped[str | None] = mapped_column(String(100), default=None)
    shipped_at: Mapped[date | None] = mapped_column(Date, default=None)
    eta: Mapped[date | None] = mapped_column(Date, default=None)  # applies to every line unless the line has its own
    source_document_id: Mapped[int | None] = mapped_column(Integer, default=None)
    source_ref: Mapped[str | None] = mapped_column(String(64), default=None)
    accepted_by: Mapped[int | None] = mapped_column(ForeignKey("app_user.id", ondelete="SET NULL"), default=None)
    accepted_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class ShipmentLine(Base):
    __tablename__ = "shipment_line"
    __table_args__ = (
        UniqueConstraint("shipment_id", "po_line_id", name="uq_shipment_line_shipment_id_po_line_id"),
        CheckConstraint("qty > 0", name="qty_positive"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    shipment_id: Mapped[int] = mapped_column(ForeignKey("shipment.id", ondelete="CASCADE"), index=True)
    po_line_id: Mapped[int] = mapped_column(ForeignKey("po_line.id", ondelete="RESTRICT"), index=True)
    qty: Mapped[Decimal] = mapped_column(Quantity)
    eta: Mapped[date | None] = mapped_column(Date, default=None)  # this line's own date, if it has one (REQ-SHP-02)


class Invoice(Base):
    __tablename__ = "invoice"
    __table_args__ = (
        UniqueConstraint("po_id", "invoice_number", name="uq_invoice_po_id_invoice_number"),
        CheckConstraint("total >= 0", name="total_not_negative"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    po_id: Mapped[int] = mapped_column(ForeignKey("purchase_order.id", ondelete="RESTRICT"), index=True)
    invoice_number: Mapped[str] = mapped_column(String(64))
    invoice_date: Mapped[date | None] = mapped_column(Date, default=None)
    total: Mapped[Decimal | None] = mapped_column(Money, default=None)  # as written; no tax field until OPEN-11
    source_document_id: Mapped[int | None] = mapped_column(Integer, default=None)
    source_ref: Mapped[str | None] = mapped_column(String(64), default=None)
    accepted_by: Mapped[int | None] = mapped_column(ForeignKey("app_user.id", ondelete="SET NULL"), default=None)
    accepted_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class InvoiceLine(Base):
    __tablename__ = "invoice_line"
    __table_args__ = (
        UniqueConstraint("invoice_id", "po_line_id", name="uq_invoice_line_invoice_id_po_line_id"),
        CheckConstraint("qty > 0", name="qty_positive"),
        CheckConstraint("price >= 0", name="price_not_negative"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    invoice_id: Mapped[int] = mapped_column(ForeignKey("invoice.id", ondelete="CASCADE"), index=True)
    po_line_id: Mapped[int] = mapped_column(ForeignKey("po_line.id", ondelete="RESTRICT"), index=True)
    qty: Mapped[Decimal] = mapped_column(Quantity)
    price: Mapped[Decimal] = mapped_column(Money)
