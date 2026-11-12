"""What the screens show of a reconciliation (PD-8.5): the order list's status and figures, the exception queue and
one invoice's place. Everything is read from `store.current`, so a page shows today's view; nothing is written."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from po_dashboard.customers import listing
from po_dashboard.db.customers import Customer, CustomerPO, SalesImport, SalesInvoice, SalesInvoiceObservation
from po_dashboard.quickbooks.reconciliation.engine import CODES, STATUSES, Order, Problem, Result

WAITING = "waiting"  # a customer PO with no approved revision yet: nothing to reconcile
STATUS_WORDS = {**STATUSES, WAITING: "Waiting for approval", "rejected": "Rejected"}
LEVELS = {"confirmed": "Confirmed discrepancy", "preliminary": "Preliminary alert"}
CSV_COLUMNS = (
    "Customer", "PO number", "Revision", "PO date", "Currency", "Ordered", "Invoiced", "Apparent remaining",
    "Status", "Exceptions", "Latest upload",
)  # fmt: skip


@dataclass
class Row:
    row: listing.OrderRow
    reconciled: Order | None
    status: str
    invoiced: Decimal | None
    remaining: Decimal | None
    last_upload: datetime | None
    problems: int

    @property
    def label(self) -> str:
        return STATUS_WORDS.get(self.status, self.status)


def last_uploads(db: Session) -> dict[int, datetime]:
    """For each invoice, when the latest upload that showed it was kept."""
    return {
        invoice_id: seen
        for invoice_id, seen in db.execute(
            select(SalesInvoiceObservation.invoice_id, func.max(SalesImport.created_at))
            .join(SalesImport, SalesImport.id == SalesInvoiceObservation.import_id)
            .group_by(SalesInvoiceObservation.invoice_id)
        )
    }


def order_rows(db: Session, result: Result) -> list[Row]:
    uploads = last_uploads(db)
    rows = []
    for row in listing.order_rows(db):
        found = result.orders.get(row.order.id)
        if found is None:
            rows.append(
                Row(row, None, WAITING if row.order.state == "pending" else row.order.state, None, None, None, 0)
            )
            continue
        seen = [uploads[i] for i in found.invoice_ids if i in uploads]
        rows.append(Row(row, found, found.status, found.invoiced, found.remaining, max(seen) if seen else None,
                        len(found.problems)))  # fmt: skip
    return rows


def plain(value: Decimal | None) -> str:
    """An amount as a person writes it: two decimals, or more only when the cent is not enough; no thousands commas."""
    if value is None:
        return ""
    cents = value.quantize(Decimal("0.01"))
    return f"{cents:f}" if cents == value else f"{value.normalize():f}"


def csv_rows(rows: list[Row]) -> list[tuple[object, ...]]:
    out: list[tuple[object, ...]] = []
    for r in rows:
        o = r.row.order
        out.append((
            r.row.customer.name, r.row.number, o.revision, o.po_date.strftime("%m/%d/%Y") if o.po_date else "",
            o.currency, plain(r.row.total), plain(r.invoiced),
            plain(r.remaining), r.label,
            "; ".join(p.label for p in r.reconciled.problems) if r.reconciled else "",
            r.last_upload.astimezone().strftime("%m/%d/%Y") if r.last_upload else "",
        ))  # fmt: skip
    return out


@dataclass
class QueueRow:
    problem: Problem
    customer: Customer | None
    number: str | None  # the PO's number, when the exception is about a PO
    invoice: SalesInvoice | None
    status: str  # the PO's status, or "" for an exception about an invoice that reaches no PO
    day: date | None

    @property
    def label(self) -> str:
        return self.problem.label

    @property
    def level_word(self) -> str:
        return LEVELS[self.problem.level]


def queue(db: Session, result: Result) -> list[QueueRow]:
    customers = {c.id: c for c in db.scalars(select(Customer))}
    invoices = {i.id: i for i in db.scalars(select(SalesInvoice))}
    pos = {o.po.id: o for o in result.orders.values()}
    dates = {
        o.id: o.po_date for o in db.scalars(select(CustomerPO).where(CustomerPO.id.in_(list(pos) or [0])))
    }  # fmt: skip
    rows = []
    for problem in result.problems():
        invoice = invoices.get(problem.invoice_id) if problem.invoice_id else None
        order = pos.get(problem.po_id) if problem.po_id else None
        customer_id = order.po.customer_id if order else invoice.customer_id if invoice else None
        day = (dates.get(order.po.id) or order.po.since) if order else invoice.invoice_date if invoice else None
        rows.append(QueueRow(
            problem, customers.get(customer_id) if customer_id else None, order.po.number if order else None,
            invoice, order.status if order else "", day,
        ))  # fmt: skip
    return rows


def codes() -> dict[str, str]:
    return {code: words[0] for code, words in CODES.items()}
