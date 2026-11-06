"""The purchase-order list and its filters (REQ-DB-02, DEC-39 item 7).

Search, vendor and the date sent are applied in the database. Status, late and the expected-date range depend on
computed line state, so they are applied here on the lines of each PO. A filter the person typed wrongly is reported
and left out, never guessed.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from po_dashboard.db.ledger import POLine, PurchaseOrder, Vendor
from po_dashboard.ledger.errors import LedgerError, when
from po_dashboard.ledger.expected import Expectation, line_expectations
from po_dashboard.ledger.status import LineState, LineStatus, POState, line_states, po_state

STATUS_CHOICES = {
    "open": "Open",
    "closed": "Closed",
    "sent": "Has a line: Sent",
    "acknowledged": "Has a line: Acknowledged",
    "partially_shipped": "Has a line: Partially shipped",
    "shipped": "Has a line: Shipped",
}
_LINE_STATUS = {
    "sent": LineStatus.SENT,
    "acknowledged": LineStatus.ACKNOWLEDGED,
    "partially_shipped": LineStatus.PARTIALLY_SHIPPED,
    "shipped": LineStatus.SHIPPED,
}


@dataclass(frozen=True)
class Filters:
    q: str = ""
    status: str = ""
    vendor_id: int | None = None
    late: bool = False
    sent_from: date | None = None
    sent_to: date | None = None
    expected_from: date | None = None
    expected_to: date | None = None


@dataclass(frozen=True)
class OrderRow:
    order: PurchaseOrder
    vendor_name: str
    line_count: int
    state: POState
    next_expected: date | None  # the earliest expected date among the open lines
    late_lines: int
    days_late: int  # the longest wait among the late lines


def parse_filters(raw: Mapping[str, str]) -> tuple[Filters, list[str]]:
    """Turn the typed values into filters. Each problem is a sentence for the person; that filter is left out."""
    problems: list[str] = []

    def day(key: str, label: str) -> date | None:
        try:
            return when(raw.get(key), label)
        except LedgerError as problem:
            problems.append(str(problem))
            return None

    status = raw.get("status", "").strip()
    if status and status not in STATUS_CHOICES:
        problems.append("That status is not one of the choices, so it was left out.")
        status = ""
    vendor = raw.get("vendor", "").strip()
    vendor_id = int(vendor) if vendor.isascii() and vendor.isdigit() and len(vendor) < 10 else None
    if vendor and vendor_id is None:
        problems.append("That vendor is not one of the choices, so it was left out.")
    found = Filters(
        q=raw.get("q", "").strip()[:100],
        status=status,
        vendor_id=vendor_id,
        late=raw.get("late", "") == "1",
        sent_from=day("sent_from", "Sent from"),
        sent_to=day("sent_to", "Sent to"),
        expected_from=day("expected_from", "Expected from"),
        expected_to=day("expected_to", "Expected to"),
    )
    for first, last, label in (
        (found.sent_from, found.sent_to, "Sent"),
        (found.expected_from, found.expected_to, "Expected"),
    ):
        if first and last and first > last:
            problems.append(f"{label}: the 'from' date is after the 'to' date, so nothing can match.")
    return found, problems


def _wanted(row: OrderRow, lines: list[tuple[LineState, Expectation]], found: Filters) -> bool:
    open_lines = [(state, exp) for state, exp in lines if not state.status.done]
    if found.status in ("open", "closed") and row.state.status.value.lower() != found.status:
        return False
    if found.status in _LINE_STATUS and not any(state.status is _LINE_STATUS[found.status] for state, _ in open_lines):
        return False
    if found.late and row.late_lines == 0:
        return False
    if found.expected_from or found.expected_to:
        dates = [exp.expected for _, exp in open_lines if exp.expected is not None]
        if not any(
            (not found.expected_from or d >= found.expected_from) and (not found.expected_to or d <= found.expected_to)
            for d in dates
        ):
            return False
    return True


def list_orders(db: Session, found: Filters, today: date) -> list[OrderRow]:
    query = select(PurchaseOrder, Vendor.name).join(Vendor, Vendor.id == PurchaseOrder.vendor_id)
    if found.q:
        query = query.where(
            or_(
                PurchaseOrder.po_number.icontains(found.q, autoescape=True),
                Vendor.name.icontains(found.q, autoescape=True),
            )
        )
    if found.vendor_id is not None:
        query = query.where(PurchaseOrder.vendor_id == found.vendor_id)
    if found.sent_from:
        query = query.where(PurchaseOrder.date_sent >= found.sent_from)
    if found.sent_to:
        query = query.where(PurchaseOrder.date_sent <= found.sent_to)
    candidates = db.execute(query.order_by(PurchaseOrder.id.desc())).all()

    owner = {row[0]: row[1] for row in db.execute(select(POLine.id, POLine.po_id))}
    states = line_states(db)
    expectations = line_expectations(db, today, states=states)
    by_po: dict[int, list[tuple[LineState, Expectation]]] = {}
    for line_id, state in states.items():
        by_po.setdefault(owner[line_id], []).append((state, expectations[line_id]))

    shown: list[OrderRow] = []
    for order, vendor_name in candidates:
        lines = by_po.get(order.id, [])
        open_exp = [exp for state, exp in lines if not state.status.done]
        dates = [exp.expected for exp in open_exp if exp.expected is not None]
        row = OrderRow(
            order=order,
            vendor_name=vendor_name,
            line_count=len(lines),
            state=po_state(state for state, _ in lines),
            next_expected=min(dates) if dates else None,
            late_lines=sum(1 for exp in open_exp if exp.late),
            days_late=max((exp.days_late for exp in open_exp), default=0),
        )
        if _wanted(row, lines, found):
            shown.append(row)
    return shown


def vendor_choices(db: Session) -> list[Vendor]:
    return list(db.scalars(select(Vendor).order_by(func.lower(Vendor.name))))
