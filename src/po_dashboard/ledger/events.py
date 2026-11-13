"""Recording what a vendor said and did, by hand: acknowledgements, shipments and invoices (PD-1.4).

A person typing an entry is the person accepting it, so each entry is stored as accepted, with who and when. Entries are
never edited. A wrong one is removed with a reason, and the audit trail keeps what it said. Status follows
automatically (ledger/status.py). Odd figures are flagged, not refused: a vendor may really bill more than was ordered.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from po_dashboard.auth.service import audit
from po_dashboard.db.base import utcnow
from po_dashboard.db.ledger import (
    AckLine,
    Acknowledgement,
    Invoice,
    InvoiceCharge,
    InvoiceLine,
    POLine,
    PromisedDateHistory,
    PurchaseOrder,
    Shipment,
    ShipmentLine,
    ShipmentTracking,
)
from po_dashboard.db.mail import SourceDocument
from po_dashboard.db.models import AppUser, AuditLog
from po_dashboard.db.types import Money, Quantity
from po_dashboard.display import day
from po_dashboard.ledger.errors import LedgerError, number, text, when
from po_dashboard.ledger.tracking import tracking_url

CHARGE_KINDS = ("freight", "tariff", "tax", "other")
Kind = Literal["ack", "shipment", "invoice"]
KINDS: tuple[Kind, ...] = ("ack", "shipment", "invoice")


@dataclass(frozen=True)
class AckInput:
    po_line_id: int
    confirmed_qty: Decimal | None = None
    confirmed_price: Decimal | None = None
    promised_date: date | None = None


@dataclass(frozen=True)
class ShipLineInput:
    po_line_id: int
    qty: Decimal
    eta: date | None = None


@dataclass(frozen=True)
class InvoiceLineInput:
    po_line_id: int
    qty: Decimal
    price: Decimal


@dataclass(frozen=True)
class ChargeInput:
    kind: str
    label: str
    amount: Decimal  # a discount is `other` with a negative amount


def _blank(*values: str | None) -> bool:
    return not any((value or "").strip() for value in values)


def _labelled[T](label: str, build: Callable[[], T]) -> T:
    try:
        return build()
    except LedgerError as problem:
        raise LedgerError(f"{label}: {problem}") from None


def parse_ack_line(label: str, po_line_id: int, qty: str, price: str, promised: str) -> AckInput | None:
    """None when the row is empty. A confirmed quantity of 0 is allowed: the vendor can not supply the line."""
    if _blank(qty, price, promised):
        return None
    return _labelled(
        label,
        lambda: AckInput(
            po_line_id,
            number(qty, "Confirmed quantity", Quantity, positive=False) if (qty or "").strip() else None,
            number(price, "Confirmed price", Money, positive=False) if (price or "").strip() else None,
            when(promised, "Promised date"),
        ),
    )


def parse_ship_line(label: str, po_line_id: int, qty: str, eta: str) -> ShipLineInput | None:
    if _blank(qty, eta):
        return None
    return _labelled(
        label,
        lambda: ShipLineInput(po_line_id, number(qty, "Quantity", Quantity, positive=True), when(eta, "Arrival date")),
    )


def parse_invoice_line(label: str, po_line_id: int, qty: str, price: str) -> InvoiceLineInput | None:
    """A row counts only when its quantity is filled. The form pre-fills the price, so a price alone is nothing."""
    if _blank(qty):
        return None
    return _labelled(
        label,
        lambda: InvoiceLineInput(
            po_line_id, number(qty, "Quantity", Quantity, positive=True), number(price, "Price", Money, positive=False)
        ),
    )


def _from_document(source_document_id: int | None) -> dict[str, int]:
    return {"document": source_document_id} if source_document_id else {}


def _check_lines(db: Session, order: PurchaseOrder, line_ids: Sequence[int]) -> None:
    if not line_ids:
        raise LedgerError("Fill in at least one line.")
    if len(set(line_ids)) != len(line_ids):
        raise LedgerError("A line appears twice.")
    own = set(db.scalars(select(POLine.id).where(POLine.po_id == order.id)))
    if not set(line_ids) <= own:
        raise LedgerError("A line does not belong to this purchase order.")


def record_ack(
    db: Session, order: PurchaseOrder, lines: Sequence[AckInput], *, without_details: bool = False,
    source_ref: str | None = None, actor: AppUser | None = None, client_address: str | None = None,
    source_document_id: int | None = None,
) -> Acknowledgement:  # fmt: skip
    """The vendor acknowledged the order. `without_details` records every unlisted line as acknowledged, no figures."""
    entries = list(lines)
    if without_details:
        listed = {entry.po_line_id for entry in entries}
        everything = db.scalars(select(POLine.id).where(POLine.po_id == order.id).order_by(POLine.line_no))
        entries += [AckInput(line_id) for line_id in everything if line_id not in listed]
    _check_lines(db, order, [entry.po_line_id for entry in entries])
    ack = Acknowledgement(
        po_id=order.id, source_ref=text(source_ref, "Reference", longest=64), accepted_by=actor.id if actor else None,
        accepted_at=utcnow(), source_document_id=source_document_id,
    )  # fmt: skip
    db.add(ack)
    db.flush()
    for entry in entries:
        db.add(
            AckLine(
                ack_id=ack.id,
                po_line_id=entry.po_line_id,
                confirmed_qty=entry.confirmed_qty,
                confirmed_price=entry.confirmed_price,
                promised_date=entry.promised_date,
            )  # fmt: skip
        )
        if entry.promised_date is not None:
            db.add(PromisedDateHistory(po_line_id=entry.po_line_id, promised_date=entry.promised_date, ack_id=ack.id))
    db.flush()
    audit(db, "ack.record", actor, f"po:{order.id}", client_address,
          after={"entry": ack.id, "lines": len(entries), **_from_document(source_document_id)})
    return ack


def record_shipment(
    db: Session, order: PurchaseOrder, lines: Sequence[ShipLineInput], *, carrier: str | None = None,
    tracking_number: str | None = None, shipped_at: str | None = None, eta: str | None = None,
    actor: AppUser | None = None, client_address: str | None = None, source_ref: str | None = None,
    source_document_id: int | None = None, tracking_numbers: Sequence[str] = (),
) -> Shipment:  # fmt: skip
    """A shipping notice. Its arrival date applies to every line unless a line has its own (REQ-SHP-02). A notice from
    a document may list several boxes: `tracking_numbers` keeps them all, and the first is the one shown."""
    header = {
        "carrier": text(carrier, "Carrier", longest=100),
        "tracking_number": text(tracking_number, "Tracking number", longest=100),
        "shipped_at": when(shipped_at, "Shipped on"),
        "eta": when(eta, "Expected arrival"),
    }
    boxes = list(dict.fromkeys(text(number_, "Tracking number", longest=100) or "" for number_ in tracking_numbers))
    boxes = [box for box in boxes if box]
    if boxes and not header["tracking_number"]:
        header["tracking_number"] = boxes[0]
    _check_lines(db, order, [entry.po_line_id for entry in lines])
    shipment = Shipment(
        po_id=order.id, accepted_by=actor.id if actor else None, accepted_at=utcnow(), source_ref=source_ref,
        source_document_id=source_document_id, **header,
    )
    db.add(shipment)
    db.flush()
    for box in boxes:
        db.add(ShipmentTracking(shipment_id=shipment.id, tracking_number=box, carrier=header["carrier"]))
    for entry in lines:
        db.add(ShipmentLine(shipment_id=shipment.id, po_line_id=entry.po_line_id, qty=entry.qty, eta=entry.eta))
    db.flush()
    audit(
        db,
        "shipment.record",
        actor,
        f"po:{order.id}",
        client_address,
        after={
            "entry": shipment.id, "lines": len(lines), **({"source": source_ref} if source_ref else {}),
            **_from_document(source_document_id),
        },
    )
    return shipment


QUICKBOOKS_REF = "quickbooks:"  # a shipment's source_ref for "received per QuickBooks export of <date>" (DEC-46)


def _shipment_title(ship: Shipment, verb: str = "Shipment") -> str:
    """How a shipment is named: by its carrier and tracking number, or as received per a QuickBooks export."""
    if ship.source_ref and ship.source_ref.startswith(QUICKBOOKS_REF):
        try:
            exported = date.fromisoformat(ship.source_ref.removeprefix(QUICKBOOKS_REF))
        except ValueError:
            return "Received per QuickBooks"
        return f"Received per QuickBooks export of {day(exported)}"
    label = " ".join(part for part in (ship.carrier, ship.tracking_number) if part)
    return f"{verb} {label}".strip()


def record_invoice(
    db: Session, order: PurchaseOrder, lines: Sequence[InvoiceLineInput], *, invoice_number: str | None,
    invoice_date: str | None = None, total: str | None = None, actor: AppUser | None = None,
    client_address: str | None = None, source_document_id: int | None = None, charges: Sequence[ChargeInput] = (),
) -> Invoice:  # fmt: skip
    """An invoice. The total is kept as written; there is no tax field until OPEN-11 is answered."""
    number_ = text(invoice_number, "Invoice number", longest=64, required=True) or ""
    dated = when(invoice_date, "Invoice date")
    written_total = number(total, "Total", Money, positive=False) if (total or "").strip() else None
    _check_lines(db, order, [entry.po_line_id for entry in lines])
    for charge in charges:
        if charge.kind not in CHARGE_KINDS:
            raise LedgerError(f"Charge kind must be one of {', '.join(CHARGE_KINDS)}.")
    taken = db.scalars(select(Invoice.invoice_number).where(Invoice.po_id == order.id))
    if number_.lower() in {existing.lower() for existing in taken}:
        raise LedgerError(f"Invoice {number_} is already recorded for this purchase order.")
    invoice = Invoice(
        po_id=order.id, invoice_number=number_, invoice_date=dated, total=written_total,
        accepted_by=actor.id if actor else None, accepted_at=utcnow(), source_document_id=source_document_id,
    )  # fmt: skip
    try:
        with db.begin_nested():  # two people sending the same invoice at once: the database refuses the second
            db.add(invoice)
            db.flush()
    except IntegrityError as clash:
        raise LedgerError(f"Invoice {number_} is already recorded for this purchase order.") from clash
    for entry in lines:
        db.add(InvoiceLine(invoice_id=invoice.id, po_line_id=entry.po_line_id, qty=entry.qty, price=entry.price))
    for charge in charges:
        db.add(InvoiceCharge(invoice_id=invoice.id, kind=charge.kind, label=charge.label, amount=charge.amount))
    db.flush()
    audit(db, "invoice.record", actor, f"po:{order.id}", client_address,
          after={"entry": invoice.id, "invoice_number": number_, "lines": len(lines),
                 **_from_document(source_document_id)})  # fmt: skip
    return invoice


# ---- reading it back


def _shown(value: Decimal) -> str:
    return format(value.normalize(), "f")


def line_warnings(db: Session, order: PurchaseOrder) -> dict[int, list[str]]:
    """Figures that differ from the PO. Shown next to the line; nothing is blocked (REQ-ACK-06, REQ-INV-05)."""
    notes: dict[int, list[str]] = {}
    lines = {line.id: line for line in db.scalars(select(POLine).where(POLine.po_id == order.id))}

    def note(line_id: int, message: str) -> None:
        if message not in notes.setdefault(line_id, []):
            notes[line_id].append(message)

    latest_price: dict[int, Decimal] = {}
    acks = (
        select(AckLine.po_line_id, AckLine.confirmed_price)
        .join(Acknowledgement)
        .where(Acknowledgement.po_id == order.id)
        .order_by(Acknowledgement.accepted_at, Acknowledgement.id)
    )
    for line_id, price in db.execute(acks):
        if price is not None:
            latest_price[line_id] = price
    for line_id, price in latest_price.items():
        if price != lines[line_id].unit_price:
            note(
                line_id,
                f"The vendor's price {_shown(price)} differs from the PO price {_shown(lines[line_id].unit_price)}.",
            )

    shipped: dict[int, Decimal] = {}
    for line_id, qty in db.execute(
        select(ShipmentLine.po_line_id, ShipmentLine.qty).join(Shipment).where(Shipment.po_id == order.id)
    ):
        shipped[line_id] = shipped.get(line_id, Decimal(0)) + qty
    invoiced: dict[int, Decimal] = {}
    for line_id, qty, price in db.execute(
        select(InvoiceLine.po_line_id, InvoiceLine.qty, InvoiceLine.price)
        .join(Invoice)
        .where(Invoice.po_id == order.id)
    ):
        invoiced[line_id] = invoiced.get(line_id, Decimal(0)) + qty
        if price > lines[line_id].unit_price:
            note(line_id, f"Invoiced at {_shown(price)}, above the PO price {_shown(lines[line_id].unit_price)}.")
    for line_id, line in lines.items():
        if shipped.get(line_id, Decimal(0)) > line.ordered_qty:
            note(line_id, f"Shipped {_shown(shipped[line_id])}, more than the {_shown(line.ordered_qty)} ordered.")
        if invoiced.get(line_id, Decimal(0)) > line.ordered_qty:
            note(line_id, f"Invoiced {_shown(invoiced[line_id])}, more than the {_shown(line.ordered_qty)} ordered.")
    return notes


@dataclass(frozen=True)
class PromisedStep:
    promised: date
    slipped: bool  # later than the date before it (REQ-ACK-04)


def promised_history(db: Session, order: PurchaseOrder) -> dict[int, list[PromisedStep]]:
    steps: dict[int, list[PromisedStep]] = {}
    history = (
        select(PromisedDateHistory.po_line_id, PromisedDateHistory.promised_date)
        .join(POLine, POLine.id == PromisedDateHistory.po_line_id)
        .where(POLine.po_id == order.id)
        .order_by(PromisedDateHistory.recorded_at, PromisedDateHistory.id)
    )
    for line_id, promised in db.execute(history):
        previous = steps.get(line_id, [])
        steps.setdefault(line_id, []).append(
            PromisedStep(promised, bool(previous) and promised > previous[-1].promised)
        )
    return steps


@dataclass
class EntryView:
    kind: Kind
    id: int
    at: datetime
    who: str | None
    title: str
    details: list[str] = field(default_factory=list)
    link: str | None = None  # a carrier tracking page, for a shipment


def entries(db: Session, order: PurchaseOrder) -> list[EntryView]:
    """Everything recorded against the PO, oldest first, for the history list."""
    people = {user.id: user.email for user in db.scalars(select(AppUser))}
    numbers = {line.id: line.line_no for line in db.scalars(select(POLine).where(POLine.po_id == order.id))}
    found: list[EntryView] = []

    for ack in db.scalars(select(Acknowledgement).where(Acknowledgement.po_id == order.id)):
        view = EntryView("ack", ack.id, ack.accepted_at, people.get(ack.accepted_by or 0), "Acknowledgement")
        for ack_row in db.scalars(select(AckLine).where(AckLine.ack_id == ack.id)):
            parts = []
            if ack_row.confirmed_qty is not None:
                parts.append(f"confirmed {_shown(ack_row.confirmed_qty)}")
            if ack_row.confirmed_price is not None:
                parts.append(f"price {_shown(ack_row.confirmed_price)}")
            if ack_row.promised_date is not None:
                parts.append(f"promised {day(ack_row.promised_date)}")
            view.details.append(f"Line {numbers[ack_row.po_line_id]}: {', '.join(parts) or 'acknowledged'}")
        found.append(view)

    for ship in db.scalars(select(Shipment).where(Shipment.po_id == order.id)):
        who = people.get(ship.accepted_by or 0)
        view = EntryView("shipment", ship.id, ship.accepted_at, who, _shipment_title(ship))
        view.link = tracking_url(ship.carrier, ship.tracking_number)
        if ship.eta:
            view.details.append(f"Expected {day(ship.eta)}")
        for ship_row in db.scalars(select(ShipmentLine).where(ShipmentLine.shipment_id == ship.id)):
            extra = f", expected {day(ship_row.eta)}" if ship_row.eta else ""
            view.details.append(f"Line {numbers[ship_row.po_line_id]}: {_shown(ship_row.qty)}{extra}")
        found.append(view)

    for bill in db.scalars(select(Invoice).where(Invoice.po_id == order.id)):
        view = EntryView(
            "invoice", bill.id, bill.accepted_at, people.get(bill.accepted_by or 0), f"Invoice {bill.invoice_number}"
        )
        if bill.total is not None:
            view.details.append(f"Total {_shown(bill.total)} {order.currency}")
        for bill_row in db.scalars(select(InvoiceLine).where(InvoiceLine.invoice_id == bill.id)):
            line_no = numbers[bill_row.po_line_id]
            view.details.append(f"Line {line_no}: {_shown(bill_row.qty)} at {_shown(bill_row.price)}")
        found.append(view)

    return sorted(found, key=lambda view: (view.at, view.kind, view.id))


def remove_entry(
    db: Session,
    order: PurchaseOrder,
    kind: str,
    entry_id: int,
    reason: str | None,
    actor: AppUser | None = None,
    client_address: str | None = None,
) -> None:
    """Take back an entry made by mistake. The reason is required and the audit trail keeps what the entry said."""
    why = text(reason, "Reason", longest=500, required=True)
    match = next((view for view in entries(db, order) if view.kind == kind and view.id == entry_id), None)
    if match is None:
        raise LedgerError("That entry was not found on this purchase order.")
    if kind == "ack":
        for history in db.scalars(select(PromisedDateHistory).where(PromisedDateHistory.ack_id == entry_id)):
            db.delete(history)
        db.flush()
        db.delete(db.get(Acknowledgement, entry_id))
    elif kind == "shipment":
        db.delete(db.get(Shipment, entry_id))
    else:
        db.delete(db.get(Invoice, entry_id))
    db.flush()
    audit(db, f"{kind}.remove", actor, f"po:{order.id}", client_address,
          before={"title": match.title, "details": match.details}, after={"reason": why})  # fmt: skip


@dataclass(frozen=True)
class TimelineStep:
    at: datetime
    text: str
    source_url: str | None = None  # the Review page of the message an accepted entry came from (REQ-DB-02)


def _source_url(db: Session, document_id: int | None) -> str | None:
    document = db.get(SourceDocument, document_id) if document_id else None
    return f"/review/message/{document.message_id}" if document else None


def line_timeline(db: Session, order: PurchaseOrder) -> dict[int, list[TimelineStep]]:
    """What happened to each line, oldest first: the order, acknowledgements, shipments, invoices, a hand-close."""
    lines = list(db.scalars(select(POLine).where(POLine.po_id == order.id)))
    steps: dict[int, list[TimelineStep]] = {line.id: [] for line in lines}
    for line in lines:
        steps[line.id].append(TimelineStep(line.created_at, f"Ordered {_shown(line.ordered_qty)} {line.unit}"))
    for ack_at, ack_row, ack_doc in db.execute(
        select(Acknowledgement.accepted_at, AckLine, Acknowledgement.source_document_id)
        .join(Acknowledgement).where(Acknowledgement.po_id == order.id)
    ):
        parts = []
        if ack_row.confirmed_qty is not None:
            parts.append(f"confirmed {_shown(ack_row.confirmed_qty)}")
        if ack_row.confirmed_price is not None:
            parts.append(f"price {_shown(ack_row.confirmed_price)}")
        if ack_row.promised_date is not None:
            parts.append(f"promised {day(ack_row.promised_date)}")
        steps[ack_row.po_line_id].append(
            TimelineStep(ack_at, "Vendor acknowledged: " + (", ".join(parts) or "no figures"), _source_url(db, ack_doc))
        )
    for ship, ship_row in db.execute(
        select(Shipment, ShipmentLine)
        .join(ShipmentLine, ShipmentLine.shipment_id == Shipment.id)
        .where(Shipment.po_id == order.id)
    ):
        arrival = ship_row.eta or ship.eta
        label = " ".join(part for part in (ship.carrier, ship.tracking_number) if part)
        text_ = f"Shipped {_shown(ship_row.qty)}" + (f" ({label})" if label else "")
        if ship.source_ref and ship.source_ref.startswith(QUICKBOOKS_REF):
            text_ = f"{_shipment_title(ship)}: {_shown(ship_row.qty)}"
        steps[ship_row.po_line_id].append(
            TimelineStep(
                ship.accepted_at, text_ + (f", expected {day(arrival)}" if arrival else ""),
                _source_url(db, ship.source_document_id),
            )
        )
    for bill, bill_row in db.execute(
        select(Invoice, InvoiceLine)
        .join(InvoiceLine, InvoiceLine.invoice_id == Invoice.id)
        .where(Invoice.po_id == order.id)
    ):
        steps[bill_row.po_line_id].append(
            TimelineStep(
                bill.accepted_at, f"Invoiced {_shown(bill_row.qty)} on {bill.invoice_number}",
                _source_url(db, bill.source_document_id),
            )
        )
    _add_history_from_audit(db, order, lines, steps)
    return {line_id: sorted(found, key=lambda step: step.at) for line_id, found in steps.items()}


def _add_history_from_audit(
    db: Session, order: PurchaseOrder, lines: list[POLine], steps: dict[int, list[TimelineStep]]
) -> None:
    """Hand-closes, reopens and entries taken back leave the live tables, so the timeline reads them from the audit
    trail, which keeps them for good. A line never loses a step because someone later undid it."""
    people = {user.id: user.email for user in db.scalars(select(AppUser))}
    by_object = {f"po_line:{line.id}": line.id for line in lines}
    number_to_id = {line.line_no: line.id for line in lines}

    def parsed(raw: str | None) -> dict[str, object]:
        try:
            value = json.loads(raw) if raw else {}
        except ValueError:
            return {}
        return value if isinstance(value, dict) else {}

    wanted = select(AuditLog).where(
        (
            AuditLog.object.in_(list(by_object))
            & AuditLog.action.in_(("line.close", "line.reopen", "line.price_rounded"))
        )
        | (
            (AuditLog.object == f"po:{order.id}")
            & AuditLog.action.in_(("ack.remove", "shipment.remove", "invoice.remove"))
        )
    )
    for row in db.scalars(wanted.order_by(AuditLog.created_at, AuditLog.id)):
        who = f" ({people[row.user_id]})" if row.user_id in people else ""
        before, after = parsed(row.before), parsed(row.after)
        if row.action == "line.close" and row.object in by_object:
            message = f"Settled by hand ({after.get('closed')}): {after.get('reason')}{who}"
            steps[by_object[row.object]].append(TimelineStep(row.created_at, message))
        elif row.action == "line.reopen" and row.object in by_object:
            message = f"Reopened: {after.get('reason')}{who}"
            steps[by_object[row.object]].append(TimelineStep(row.created_at, message))
        elif row.action == "line.price_rounded" and row.object in by_object:
            steps[by_object[row.object]].append(TimelineStep(row.created_at, str(after.get("note"))))
        elif row.action.endswith(".remove"):
            details = before.get("details")
            title = before.get("title")
            for detail in details if isinstance(details, list) else []:
                match = re.match(r"Line (\d+):", str(detail))
                if match and int(match.group(1)) in number_to_id:
                    what = str(detail)[match.end() :].strip()
                    message = f"Entry taken back: {title} ({what}). Reason: {after.get('reason')}{who}"
                    steps[number_to_id[int(match.group(1))]].append(TimelineStep(row.created_at, message))
