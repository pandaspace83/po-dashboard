"""The dashboard counts and the open-items report (REQ-DB-01, REQ-DB-03, DEC-42).

Every figure is computed from accepted facts, so the dashboard and the report can never disagree: the dashboard counts
are the lengths of the same lists the report shows. Nothing here changes data.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from po_dashboard.db.ledger import (
    AckLine,
    Acknowledgement,
    Invoice,
    InvoiceLine,
    POLine,
    PromisedDateHistory,
    PurchaseOrder,
    Shipment,
    ShipmentLine,
    Vendor,
)
from po_dashboard.ledger.expected import Expectation, line_expectations
from po_dashboard.ledger.status import LineState, LineStatus, line_states

NO_ACK_BUSINESS_DAYS = 2  # DEC-22: a sent PO with no acknowledgement for more than this is flagged
SHOWS = {
    "all": "All open lines",
    "late": "Late",
    "no_ack": "No acknowledgement",
    "slipped": "Promised date slipped",
    "needs_update": "Original PO needs updating",
}
CSV_COLUMNS = (
    "PO", "Vendor", "Line", "Item", "Description", "Unit", "Ordered", "Open quantity", "Status", "Expected date",
    "Expected from", "Days late", "Promised date slipped", "Last vendor activity (UTC)", "Flags",
)  # fmt: skip


def business_days_since(start: date, today: date) -> int:
    """Whole weekdays after `start` up to and including `today` (weekends only; there is no holiday calendar)."""
    days, day = 0, start
    while day < today:
        day += timedelta(days=1)
        if day.weekday() < 5:
            days += 1
    return days


@dataclass(frozen=True)
class OpenItem:
    po_id: int
    po_number: str
    vendor: str
    line_no: int
    item: str
    description: str
    unit: str
    ordered: Decimal
    open_qty: Decimal
    status: str
    expectation: Expectation
    slipped: bool
    no_ack: bool
    needs_update: bool
    last_activity: datetime | None

    @property
    def flags(self) -> list[str]:
        found = []
        if self.expectation.late:
            found.append(f"{self.expectation.days_late} days late" if self.expectation.days_late != 1 else "1 day late")
        if self.no_ack:
            found.append("no acknowledgement")
        if self.slipped:
            found.append("promised date slipped")
        if self.needs_update:
            found.append("update the original PO")
        return found


@dataclass(frozen=True)
class Counts:
    open_pos: int
    open_lines: int
    late: int
    no_ack: int
    slipped: int
    needs_update: int


def _slipped_lines(db: Session) -> set[int]:
    last: dict[int, date] = {}
    slipped: set[int] = set()
    rows = select(PromisedDateHistory.po_line_id, PromisedDateHistory.promised_date).order_by(
        PromisedDateHistory.recorded_at, PromisedDateHistory.id
    )
    for line_id, day in db.execute(rows):
        if line_id in last and day > last[line_id]:
            slipped.add(line_id)
        last[line_id] = day
    return slipped


def _last_activity(db: Session) -> dict[int, datetime]:
    """The latest time anything was recorded for a PO's lines: an acknowledgement, a shipment or an invoice."""
    latest: dict[int, datetime] = {}
    for line_id, when_ in db.execute(
        select(AckLine.po_line_id, func.max(Acknowledgement.accepted_at))
        .join(Acknowledgement)
        .group_by(AckLine.po_line_id)
    ):
        latest[line_id] = when_
    for model, line_model, parent in (
        (Shipment, ShipmentLine, ShipmentLine.shipment_id),
        (Invoice, InvoiceLine, InvoiceLine.invoice_id),
    ):
        for line_id, when_ in db.execute(
            select(line_model.po_line_id, func.max(model.accepted_at))
            .join(model, model.id == parent)
            .group_by(line_model.po_line_id)
        ):
            if line_id not in latest or when_ > latest[line_id]:
                latest[line_id] = when_
    return latest


def open_items(db: Session, today: date, show: str = "all", vendor_id: int | None = None) -> list[OpenItem]:
    """Every open line, oldest PO first, optionally narrowed to one kind of trouble or one vendor."""
    states: dict[int, LineState] = line_states(db)
    expectations = line_expectations(db, today, states=states)
    slipped = _slipped_lines(db)
    activity = _last_activity(db)
    query = (
        select(POLine, PurchaseOrder, Vendor.name)
        .join(PurchaseOrder, PurchaseOrder.id == POLine.po_id)
        .join(Vendor, Vendor.id == PurchaseOrder.vendor_id)
        .order_by(PurchaseOrder.id, POLine.line_no)
    )
    if vendor_id is not None:
        query = query.where(PurchaseOrder.vendor_id == vendor_id)
    found: list[OpenItem] = []
    for line, order, vendor_name in db.execute(query):
        state = states[line.id]
        if state.status.done:
            continue
        waiting = (
            state.status is LineStatus.SENT
            and order.date_sent is not None
            and business_days_since(order.date_sent, today) > NO_ACK_BUSINESS_DAYS
        )
        item = OpenItem(
            po_id=order.id,
            po_number=order.po_number,
            vendor=vendor_name,
            line_no=line.line_no,
            item=line.item,
            description=line.description or "",
            unit=line.unit,
            ordered=line.ordered_qty,
            open_qty=state.open_qty,
            status=state.status.value,
            expectation=expectations[line.id],
            slipped=line.id in slipped,
            no_ack=waiting,
            needs_update=state.po_needs_update,
            last_activity=activity.get(line.id),
        )
        keep = {
            "all": True,
            "late": item.expectation.late,
            "no_ack": item.no_ack,
            "slipped": item.slipped,
            "needs_update": item.needs_update,
        }
        if keep.get(show, True):
            found.append(item)
    return found


def counts(db: Session, today: date) -> Counts:
    items = open_items(db, today)
    open_pos = len({item.po_id for item in items})
    return Counts(
        open_pos=open_pos,
        open_lines=len(items),
        late=sum(1 for item in items if item.expectation.late),
        no_ack=sum(1 for item in items if item.no_ack),
        slipped=sum(1 for item in items if item.slipped),
        needs_update=sum(1 for item in items if item.needs_update),
    )


FORMULA_STARTS = ("=", "+", "-", "@", "\t", "\r")


def safe_cell(value: object) -> str:
    """A spreadsheet runs a cell that starts with = + - @ as a formula. A leading apostrophe makes it plain text."""
    shown = "" if value is None else str(value)
    return "'" + shown if shown.startswith(FORMULA_STARTS) else shown


def to_csv(items: list[OpenItem]) -> bytes:
    """UTF-8 with a byte-order mark, so Excel reads accents correctly. Every text cell is made safe first."""
    out = io.StringIO(newline="")
    writer = csv.writer(out, lineterminator="\r\n")
    writer.writerow(CSV_COLUMNS)
    for item in items:
        expected = item.expectation
        writer.writerow(
            safe_cell(cell)
            for cell in (
                item.po_number,
                item.vendor,
                item.line_no,
                item.item,
                item.description,
                item.unit,
                format(item.ordered.normalize(), "f"),
                format(item.open_qty.normalize(), "f"),
                item.status,
                expected.expected.isoformat() if expected.expected else "",
                {"shipment": "shipment", "promised": "promised date"}.get(expected.source or "", ""),
                expected.days_late or "",
                "yes" if item.slipped else "",
                item.last_activity.strftime("%Y-%m-%d %H:%M") if item.last_activity else "",
                "; ".join(item.flags),
            )
        )
    return b"\xef\xbb\xbf" + out.getvalue().encode("utf-8")
