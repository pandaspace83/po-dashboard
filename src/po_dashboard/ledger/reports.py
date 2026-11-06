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
from functools import lru_cache

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
from po_dashboard.display import day, moment
from po_dashboard.ledger.expected import Expectation, line_expectations
from po_dashboard.ledger.status import LineState, LineStatus, line_states

NO_ACK_BUSINESS_DAYS = 2  # DEC-22: a sent PO with no acknowledgement for more than this is flagged
SHOWS = {
    "all": "All open lines",
    "attention": "Needs attention (any of the below)",
    "late": "Late",
    "no_ack": "No acknowledgement",
    "slipped": "Promised date slipped",
    "needs_update": "Original PO needs updating",
}
CSV_COLUMNS = (
    "PO", "Vendor", "Line", "Item", "Description", "Customer", "Unit", "Ordered", "Open quantity", "Status",
    "Expected date",
    "Expected from", "Days late", "Promised date slipped", "Last vendor activity", "Flags",
)  # fmt: skip


@lru_cache(maxsize=4096)
def business_days_since(start: date, today: date) -> int:
    """Whole weekdays after `start` up to and including `today` (weekends only; there is no holiday calendar).
    Kept for repeated pairs: many lines share a PO date, and the report asks for each line."""
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
    customer: str
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

    @property
    def next_step(self) -> str:
        """What a person does about it, most urgent first (DEC-45 item 8). Empty when nothing is needed."""
        if self.expectation.late:
            return "Ask the vendor for a new date"
        if self.no_ack:
            return "Ask the vendor to acknowledge the PO"
        if self.needs_update:
            return "Update the PO in QuickBooks to the confirmed quantity"
        if self.slipped:
            return "Check the new promised date"
        return ""


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
    for line_id, promised in db.execute(rows):
        if line_id in last and promised > last[line_id]:
            slipped.add(line_id)
        last[line_id] = promised
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
    # Plain columns, not whole objects: the report reads thousands of lines and needs only these (DEC-45 item 14).
    query = (
        select(
            POLine.id, POLine.line_no, POLine.item, POLine.description, POLine.customer, POLine.unit,
            POLine.ordered_qty,
            PurchaseOrder.id, PurchaseOrder.po_number, PurchaseOrder.date_sent, Vendor.name,
        )
        .join(PurchaseOrder, PurchaseOrder.id == POLine.po_id)
        .join(Vendor, Vendor.id == PurchaseOrder.vendor_id)
        .order_by(PurchaseOrder.id, POLine.line_no)
    )  # fmt: skip
    if vendor_id is not None:
        query = query.where(PurchaseOrder.vendor_id == vendor_id)
    found: list[OpenItem] = []
    for row in db.execute(query):
        line_id, line_no, item_text, description, customer, unit, ordered = row[:7]
        po_id, po_number, date_sent, vendor_name = row[7:]
        state = states[line_id]
        if state.status.done:
            continue
        waiting = (
            state.status is LineStatus.SENT
            and date_sent is not None
            and business_days_since(date_sent, today) > NO_ACK_BUSINESS_DAYS
        )
        item = OpenItem(
            po_id=po_id,
            po_number=po_number,
            vendor=vendor_name,
            line_no=line_no,
            item=item_text,
            description=description or "",
            customer=customer or "",
            unit=unit,
            ordered=ordered,
            open_qty=state.open_qty,
            status=state.status.value,
            expectation=expectations[line_id],
            slipped=line_id in slipped,
            no_ack=waiting,
            needs_update=state.po_needs_update,
            last_activity=activity.get(line_id),
        )
        keep = {
            "all": True,
            "attention": bool(item.next_step),
            "late": item.expectation.late,
            "no_ack": item.no_ack,
            "slipped": item.slipped,
            "needs_update": item.needs_update,
        }
        if keep.get(show, True):
            found.append(item)
    return found


def counts(db: Session, today: date, items: list[OpenItem] | None = None) -> Counts:
    items = open_items(db, today) if items is None else items
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
                item.customer,
                item.unit,
                format(item.ordered.normalize(), "f"),
                format(item.open_qty.normalize(), "f"),
                item.status,
                day(expected.expected),
                {"shipment": "shipment", "promised": "promised date"}.get(expected.source or "", ""),
                expected.days_late or "",
                "yes" if item.slipped else "",
                moment(item.last_activity),
                "; ".join(item.flags),
            )
        )
    return b"\xef\xbb\xbf" + out.getvalue().encode("utf-8")


def attention(items: list[OpenItem], limit: int = 10) -> tuple[list[OpenItem], int]:
    """The lines that need a person, most urgent first: the longest late, then no acknowledgement, then the rest.
    Returns the first `limit` and how many there are in all."""
    needing = [item for item in items if item.next_step]
    needing.sort(
        key=lambda i: (
            0 if i.expectation.late else 1 if i.no_ack else 2 if i.needs_update else 3,
            -(i.expectation.days_late or 0),
            i.po_number.lower(),
            i.line_no,
        )
    )
    return needing[:limit], len(needing)
