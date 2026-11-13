"""Drafted follow-up messages for the buyer to copy (REQ-FU-03, DEC-40, PD-5.3).

Fixed templates filled by plain code from accepted records. No model, and nothing here sends mail (DEC-07): the
buyer reads the draft, edits it and sends it from their own mail. The address is the vendor's contact on record,
never one found in document text (DEC-40 (3)).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from po_dashboard.db.ledger import POLine, PromisedDateHistory, PurchaseOrder, Vendor
from po_dashboard.display import day
from po_dashboard.ledger.reports import OpenItem, open_items
from po_dashboard.ledger.status import line_states

NO_CONTACT = "no contact on file: add one on the Vendors page"
KINDS = ("late", "no_ack", "slipped", "quantity")


@dataclass(frozen=True)
class Draft:
    kind: str
    title: str
    to: str  # the contact's address, or NO_CONTACT
    has_contact: bool
    subject: str
    body: str


@dataclass(frozen=True)
class DraftFacts:
    """What the templates may use, all from the ledger."""

    contact_name: str | None
    contact_email: str | None
    previous_promised: date | None
    confirmed_qty: Decimal | None


def _one_line(value: str) -> str:
    """Text from a record goes into a mail header and body: no line breaks or control characters."""
    return " ".join("".join(ch if ch.isprintable() else " " for ch in value).split())


def _qty(value: Decimal) -> str:
    return format(value.normalize(), "f")


def _line(item: OpenItem) -> str:
    return f"line {item.line_no}, {_one_line(item.item)}, {_qty(item.open_qty)} {_one_line(item.unit)} open"


def _wrap(item: OpenItem, facts: DraftFacts, lines: list[str]) -> str:
    hello = f"Hello {_one_line(facts.contact_name)}," if facts.contact_name else "Hello,"
    return "\n".join([hello, "", *lines, "", "Thank you,", ""])


def drafts_for(item: OpenItem, facts: DraftFacts) -> list[Draft]:
    """One draft for each reason this line needs the vendor: late, no acknowledgement, a slipped date, a quantity
    the vendor confirmed that differs from the PO. A line with none of them has no draft."""
    po = _one_line(item.po_number)
    found: list[tuple[str, str, str, list[str]]] = []
    if item.expectation.late and item.expectation.expected:
        expected = item.expectation.expected
        found.append((
            "late", "Late: ask for a new date", f"PO {po}: new delivery date needed",
            [f"Our purchase order {po}, {_line(item)}, was expected on {day(expected)} and has not arrived.",
             "Could you tell me when it will ship and the date we can expect it?"],
        ))  # fmt: skip
    if item.no_ack:
        found.append((
            "no_ack", "No acknowledgement: ask them to confirm", f"PO {po}: please confirm receipt",
            [f"We have not yet received an acknowledgement for our purchase order {po}, {_line(item)}.",
             "Could you confirm you have it, with the quantity and the date you will ship?"],
        ))  # fmt: skip
    if item.slipped and item.expectation.promised:
        new = day(item.expectation.promised)
        before = f" from {day(facts.previous_promised)}" if facts.previous_promised else ""
        found.append((
            "slipped", "Promised date slipped: ask why", f"PO {po}: the promised date has moved",
            [f"For our purchase order {po}, {_line(item)}, the promised date moved{before} to {new}.",
             "Can you confirm the new date, and say whether it can be earlier?"],
        ))  # fmt: skip
    if item.needs_update and facts.confirmed_qty is not None:
        unit = _one_line(item.unit)
        found.append((
            "quantity", "Quantity differs: ask them to confirm", f"PO {po}: please confirm the quantity",
            [f"Your confirmation for our purchase order {po}, line {item.line_no}, {_one_line(item.item)}, states "
             f"{_qty(facts.confirmed_qty)} {unit}. We ordered {_qty(item.ordered)} {unit}.",
             "Could you confirm the quantity you will supply, and when the rest can ship, if any?"],
        ))  # fmt: skip
    to = facts.contact_email or NO_CONTACT
    return [
        Draft(kind, title, to, bool(facts.contact_email), subject, _wrap(item, facts, lines))
        for kind, title, subject, lines in found
    ]


def facts_for(db: Session, po_id: int, line_no: int) -> DraftFacts | None:
    """The vendor's contact and the line's own history, read from the ledger."""
    row = db.execute(
        select(POLine.id, Vendor.contact_name, Vendor.contact_email)
        .join(PurchaseOrder, PurchaseOrder.id == POLine.po_id)
        .join(Vendor, Vendor.id == PurchaseOrder.vendor_id)
        .where(POLine.po_id == po_id, POLine.line_no == line_no)
    ).first()
    if row is None:
        return None
    line_id, name, email = row
    dates = list(
        db.scalars(
            select(PromisedDateHistory.promised_date)
            .where(PromisedDateHistory.po_line_id == line_id)
            .order_by(PromisedDateHistory.recorded_at, PromisedDateHistory.id)
        )
    )
    return DraftFacts(
        contact_name=name,
        contact_email=email,
        previous_promised=dates[-2] if len(dates) > 1 else None,
        confirmed_qty=line_states(db)[line_id].facts.confirmed,
    )


def drafts_for_line(db: Session, po_id: int, line_no: int, today: date) -> tuple[OpenItem, list[Draft]] | None:
    """The open line and its drafts; None when there is no such open line."""
    facts = facts_for(db, po_id, line_no)
    if facts is None:
        return None
    for item in open_items(db, today):
        if item.po_id == po_id and item.line_no == line_no:
            return item, drafts_for(item, facts)
    return None
