"""Matching a read document to a PO, a vendor and its lines, then validating it (ARCHITECTURE section 6.2, DEC-54,
DEC-71, PD-4.3). Plain code: the model only read the document; nothing here is a guess (DEC-09).

For a vendor's document (acknowledgement, shipment notice, invoice), each step stops at one clear candidate:

1. the PO number, exactly (letters and digits, case and a leading "PO" ignored);
2. else, or when it names no PO we have: a vendor order number, tracking number or invoice number already recorded
   against a PO. **One number that ties to exactly one PO is enough to propose the match;** numbers that point at more
   than one PO are left to a person;
3. else, only when the document carries no number at all: the one open PO of the sender's vendor whose lines have the
   document's items and quantities (and prices, for an invoice).

The sender never ties a document by itself. It narrows the suggestions and decides the tick "I checked the sender"
(`sender_not_vendor`): the sender is a known domain of a *different* vendor, or unknown and only a PO number backs the
match. A person still accepts every document (DEC-08).

The outcome is a review state: `waiting` (a match, or a purchase order whose vendor was found), `unmatched` (a person
picks the PO), or `no_facts` (kept and searchable, never queued). Two rules hold whatever the model says (DEC-73, the
owner, 2026-11-12): a purchase order counts only when the company sent it (Sent Items, or a sender at the mailbox's own
domain), and a document whose text has planted words is never filed as `no_facts`: it goes to `unmatched`, flagged, so
"this is not an order" cannot hide it. Nothing here writes a document's text anywhere."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from email.utils import getaddresses, parseaddr
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from po_dashboard.db.ledger import (
    AckLine,
    Acknowledgement,
    Invoice,
    InvoiceLine,
    POLine,
    PurchaseOrder,
    Shipment,
    ShipmentLine,
    ShipmentTracking,
    VendorDomain,
)
from po_dashboard.db.mail import Extraction, SourceDocument, SourceMessage
from po_dashboard.extraction import schema
from po_dashboard.extraction.matching import PoLine, item_key
from po_dashboard.extraction.scoring import norm_id, number
from po_dashboard.extraction.validate import (
    VENDOR_DOCS,
    Context,
    Flag,
    fingerprint,
    has_planted_instruction,
    validate,
)
from po_dashboard.mail.source import SENT

WAITING, UNMATCHED, NO_FACTS = "waiting", "unmatched", "no_facts"
ACCEPTED_OR_WAITING = ("waiting", "accepted")  # what `duplicate` compares with


@dataclass(frozen=True)
class Decision:
    """What matching decided. `po_id` is set for a match (and for an emailed PO that exists already); `vendor_id` for a
    match, an emailed PO's vendor, or the sender's vendor on an unmatched document."""

    review_state: str
    po_id: int | None = None
    vendor_id: int | None = None
    flags: list[Flag] = field(default_factory=list)

    def flags_json(self) -> str | None:
        return json.dumps([f.as_json() for f in self.flags]) if self.flags else None


# ---- addresses


def address_domain(address: str | None) -> str | None:
    """The domain of a `Name <user@host>` address, lower case; None if there is none."""
    _, found = parseaddr(address or "")
    if "@" not in found:
        return None
    domain = found.rsplit("@", 1)[1].strip().lower().rstrip(".")
    return domain or None


def from_company(message: SourceMessage, own_domain: str | None) -> bool:
    """The company sent this message: it is in Sent Items, or its sender is at the mailbox's own domain (DEC-73). Only
    such a message can bring a new PO; anyone can write "purchase order" in an email."""
    return message.folder == SENT or (own_domain is not None and address_domain(message.sender) == own_domain)


def vendor_of_domain(db: Session, domain: str | None) -> int | None:
    return db.scalar(select(VendorDomain.vendor_id).where(VendorDomain.domain == domain)) if domain else None


def recipient_vendors(db: Session, recipients: str | None, own_domain: str | None) -> set[int]:
    """The vendors an emailed PO was sent to: those with a domain on a To or Cc address, not counting the company's."""
    found: set[int] = set()
    for _, address in getaddresses([recipients or ""]):
        domain = address_domain(address)
        if domain and domain != own_domain and (vendor := vendor_of_domain(db, domain)) is not None:
            found.add(vendor)
    return found


# ---- what the ledger holds about a PO


def _by_number(db: Session) -> dict[str, int]:
    return {norm_id(n) or "": i for i, n in db.execute(select(PurchaseOrder.id, PurchaseOrder.po_number))}


def _sums(db: Session, qty: Any, line_id: Any, po_id: int | None) -> dict[int, Decimal]:
    statement = select(line_id, func.sum(qty)).group_by(line_id)
    if po_id is not None:
        statement = statement.join(POLine, POLine.id == line_id).where(POLine.po_id == po_id)
    return {row[0]: row[1] for row in db.execute(statement)}


def po_lines_for(db: Session, po_id: int, doc_type: str) -> tuple[PoLine, ...]:
    """The PO's lines as the validator compares them. `open` is what the document can still add: for a shipment notice
    what is not yet shipped, for an invoice what is not yet billed, for an acknowledgement the whole ordered quantity
    (it restates the line). The promised date is the latest one a person accepted for the line."""
    shipped = _sums(db, ShipmentLine.qty, ShipmentLine.po_line_id, po_id)
    billed = _sums(db, InvoiceLine.qty, InvoiceLine.po_line_id, po_id)
    promised: dict[int, date] = {}
    acks = (
        select(AckLine.po_line_id, AckLine.promised_date)
        .join(Acknowledgement, AckLine.ack_id == Acknowledgement.id)
        .join(POLine, POLine.id == AckLine.po_line_id)
        .where(POLine.po_id == po_id, AckLine.promised_date.is_not(None))
        .order_by(Acknowledgement.accepted_at, Acknowledgement.id)
    )
    for line_id, when in db.execute(acks):
        if when is not None:
            promised[line_id] = when  # the later ack replaces the earlier one
    found: list[PoLine] = []
    for line in db.scalars(select(POLine).where(POLine.po_id == po_id).order_by(POLine.line_no)):
        taken = {"shipment_notice": shipped, "invoice": billed}.get(doc_type)
        left = None if taken is None else max(line.ordered_qty - taken.get(line.id, Decimal(0)), Decimal(0))
        found.append(PoLine(
            line.line_no, line.item, line.ordered_qty, open=left, unit=line.unit, unit_price=line.unit_price,
            promised_date=promised.get(line.id), closed=line.closed_reason is not None,
        ))  # fmt: skip
    return tuple(found)


def _open_po_ids(db: Session, vendor_id: int) -> list[int]:
    """The vendor's POs that still have a line nobody closed and nothing finished (newest first)."""
    from po_dashboard.ledger.status import POStatus, po_states

    mine = list(db.scalars(select(PurchaseOrder.id).where(PurchaseOrder.vendor_id == vendor_id).order_by(
        PurchaseOrder.id.desc())))
    states = po_states(db)
    return [i for i in mine if states.get(i) is not None and states[i].status == POStatus.OPEN]


# ---- the numbers that tie a document to a PO (step 2)


def _tracking_key(value: Any) -> str:
    return re.sub(r"\s+", "", str(value)).upper()


def _accepted_answers(db: Session) -> list[tuple[int, Mapping[str, Any]]]:
    """The accepted extractions with a PO: what a person accepted, else what the model read."""
    out: list[tuple[int, Mapping[str, Any]]] = []
    rows = db.execute(select(Extraction.po_id, Extraction.final, Extraction.answer).where(
        Extraction.review_state == "accepted", Extraction.po_id.is_not(None)))
    for po_id, final, answer in rows:
        for raw in (final, answer):
            if not raw:
                continue
            try:
                value = json.loads(raw)
            except ValueError:
                continue
            if isinstance(value, dict) and po_id is not None:
                out.append((po_id, value))
                break
    return out


def tying_pos(db: Session, answer: Mapping[str, Any]) -> set[int]:
    """The POs a recorded vendor order number, tracking number or invoice number of this document points at."""
    order, invoice = norm_id(answer.get("vendor_order_number")), norm_id(answer.get("invoice_number"))
    tracking = {_tracking_key(t) for t in answer.get("tracking_numbers") or [] if str(t).strip()}
    found: set[int] = set()
    if order:
        found |= {po for po, accepted in _accepted_answers(db) if norm_id(accepted.get("vendor_order_number")) == order}
    if invoice:
        found |= {po for po, n in db.execute(select(Invoice.po_id, Invoice.invoice_number))
                  if norm_id(n) == invoice}  # fmt: skip
    if tracking:
        rows = db.execute(select(Shipment.po_id, ShipmentTracking.tracking_number).join(
            ShipmentTracking, ShipmentTracking.shipment_id == Shipment.id))
        found |= {po for po, n in rows if _tracking_key(n) in tracking}
        found |= {po for po, n in db.execute(select(Shipment.po_id, Shipment.tracking_number))
                  if n and _tracking_key(n) in tracking}  # fmt: skip
    return found


def _has_a_number(answer: Mapping[str, Any]) -> bool:
    singles = (answer.get(f) for f in ("po_number", "vendor_order_number", "invoice_number"))
    tracking = answer.get("tracking_numbers") or []
    return any(str(v or "").strip() for v in singles) or any(str(t).strip() for t in tracking)


# ---- step 3: no number at all, so items and quantities (and prices on an invoice)


def _same_lines(db: Session, po_id: int, answer: Mapping[str, Any]) -> bool:
    doc_type = str(answer.get("doc_type"))
    lines = [x for x in answer.get("lines") or [] if isinstance(x, Mapping)]
    if not lines:
        return False
    po_lines = [p for p in po_lines_for(db, po_id, doc_type) if not p.closed]
    for line in lines:
        same = [p for p in po_lines if item_key(line.get("item")) and item_key(p.item) == item_key(line.get("item"))]
        qty = number(line.get("qty"))
        if len(same) != 1 or qty is None or qty != same[0].still_open:
            return False
        if doc_type == "invoice" and number(line.get("unit_price")) != same[0].unit_price:
            return False
    return True


def find_po(db: Session, answer: Mapping[str, Any], sender_vendor: int | None) -> tuple[int | None, bool]:
    """(the PO, whether a PO number alone backed it). A PO of None means a person must choose."""
    by_number = _by_number(db)
    named = norm_id(answer.get("po_number"))
    if named and named in by_number:
        return by_number[named], by_number[named] not in tying_pos(db, answer)  # backed by the PO number alone?
    ties = tying_pos(db, answer)
    if len(ties) == 1:
        return next(iter(ties)), False
    if ties or _has_a_number(answer):
        return None, False
    if sender_vendor is None:
        return None, False
    fits = [i for i in _open_po_ids(db, sender_vendor) if _same_lines(db, i, answer)]
    return (fits[0], False) if len(fits) == 1 else (None, False)


# ---- the decision


def has_order_facts(answer: Mapping[str, Any]) -> bool:
    """No PO number, no lines, no tracking number and no invoice number means no order facts (owner, 2026-11-12)."""
    if answer.get("doc_type") == "other":
        return False
    return bool(
        str(answer.get("po_number") or "").strip() or str(answer.get("invoice_number") or "").strip()
        or [x for x in answer.get("lines") or [] if isinstance(x, Mapping)]
        or any(str(t).strip() for t in answer.get("tracking_numbers") or [])
    )  # fmt: skip


def _earlier(db: Session, po_id: int, own_id: int | None) -> list[Mapping[str, Any]]:
    found: list[Mapping[str, Any]] = []
    rows = db.execute(select(Extraction.id, Extraction.answer).where(
        Extraction.po_id == po_id, Extraction.review_state.in_(ACCEPTED_OR_WAITING), Extraction.answer.is_not(None)))
    for ident, raw in rows:
        if ident == own_id or not raw:
            continue
        try:
            value = schema.parse_answer(raw)
        except schema.NotAnAnswer:
            continue
        found.append(value)
    return found


def decide(
    db: Session, answer: Mapping[str, Any], text: str, message: SourceMessage, document: SourceDocument, *,
    own_domain: str | None, today: date, read_in_part: bool = False, own_id: int | None = None,
    force_po_id: int | None = None,
) -> Decision:  # fmt: skip
    """Match one read document and compute its flags. `force_po_id` is a person's own choice from the Unmatched list
    (PD-4.4's Attach to a PO): the PO is not searched for, and the document is validated against it."""
    doc_type = str(answer.get("doc_type"))
    ocr = document.kind == "image"  # scanned PDFs read by OCR are not told apart yet (PD-4.3 follow-up)
    sender_vendor = vendor_of_domain(db, address_domain(message.sender))

    def validated(po_id: int | None, vendor_id: int | None, state: str, **context: Any) -> Decision:
        flags = validate(answer, text, Context(today=today, ocr=ocr, read_in_part=read_in_part, **context))
        return Decision(state, po_id, vendor_id, flags)

    if doc_type == "purchase_order":  # an emailed PO (DEC-17): the vendor from the recipients
        existing = _by_number(db).get(norm_id(answer.get("po_number")) or "")
        if not from_company(message, own_domain):  # only the company sends its own POs (DEC-73)
            return validated(existing, None, UNMATCHED)
        vendors = recipient_vendors(db, message.recipients, own_domain)
        if len(vendors) != 1 or not norm_id(answer.get("po_number")):
            return validated(existing, None, UNMATCHED)
        return validated(existing, next(iter(vendors)), WAITING, po_exists=existing is not None)
    if message.folder == SENT or not has_order_facts(answer) or doc_type not in VENDOR_DOCS:
        if has_planted_instruction(text, ocr=ocr):  # never filed silently: a person looks at it (DEC-73)
            return validated(None, sender_vendor, UNMATCHED)
        # the company's own mail never changes a PO, so Sent Items that are not POs are filed here too
        return Decision(NO_FACTS, flags=[Flag("read_in_part", "check")] if read_in_part else [])
    if force_po_id is not None:
        po_id: int | None = force_po_id
        backed_by_number_only = False
    else:
        po_id, backed_by_number_only = find_po(db, answer, sender_vendor)
    if po_id is None:
        return validated(None, sender_vendor, UNMATCHED)
    po = db.get(PurchaseOrder, po_id)
    if po is None:
        return validated(None, sender_vendor, UNMATCHED)
    # the tick "I checked the sender" (DEC-71)
    if sender_vendor is not None:
        sender_is_vendor: bool | None = sender_vendor == po.vendor_id
    else:
        sender_is_vendor = not backed_by_number_only if force_po_id is None else None
    earlier = tuple(fingerprint(a, po_lines_for(db, po_id, doc_type)) for a in _earlier(db, po_id, own_id))
    invoices = frozenset(db.scalars(select(Invoice.invoice_number).where(Invoice.po_id == po_id)))
    tracking = frozenset(db.scalars(
        select(ShipmentTracking.tracking_number).join(Shipment, Shipment.id == ShipmentTracking.shipment_id)
        .where(Shipment.po_id == po_id)
    )) | frozenset(n for n in db.scalars(select(Shipment.tracking_number).where(Shipment.po_id == po_id)) if n)
    return validated(
        po_id, po.vendor_id, WAITING, po_lines=po_lines_for(db, po_id, doc_type), po_date=po.po_date or po.date_sent,
        sender_is_vendor=sender_is_vendor, earlier=earlier, recorded_invoice_numbers=invoices,
        recorded_tracking_numbers=tracking,
    )  # fmt: skip


def own_domain_of(db: Session, message: SourceMessage) -> str | None:
    from po_dashboard.db.mail import Mailbox

    mailbox = db.get(Mailbox, message.mailbox_id)
    return address_domain(mailbox.address) if mailbox else None


# ---- the lists a person works from (the screens come with PD-4.4)


def unmatched_list(db: Session) -> list[tuple[Extraction, SourceDocument, SourceMessage]]:
    """Documents waiting for a person to choose a PO, newest first."""
    return by_state(db, UNMATCHED)


def waiting_list(db: Session) -> list[tuple[Extraction, SourceDocument, SourceMessage]]:
    """Documents waiting for a person to review them, newest first."""
    return by_state(db, WAITING)


def no_facts_list(db: Session) -> list[tuple[Extraction, SourceDocument, SourceMessage]]:
    """Documents with no order facts, kept and searchable, never in the review queue, newest first."""
    return by_state(db, NO_FACTS)


def by_state(db: Session, state: str) -> list[tuple[Extraction, SourceDocument, SourceMessage]]:
    return [
        (row, document, message)
        for row, document, message in db.execute(
            select(Extraction, SourceDocument, SourceMessage)
            .join(SourceDocument, SourceDocument.id == Extraction.document_id)
            .join(SourceMessage, SourceMessage.id == SourceDocument.message_id)
            .where(Extraction.state == "done", Extraction.review_state == state)
            .order_by(Extraction.id.desc())
        )
    ]


def candidates(db: Session, extraction: Extraction, message: SourceMessage) -> list[PurchaseOrder]:
    """The POs to offer on an unmatched document: those its numbers point at, then the sender's vendor's open POs.
    A suggestion only: a person chooses (DEC-71)."""
    answer: Mapping[str, Any] = {}
    if extraction.answer:
        try:
            answer = schema.parse_answer(extraction.answer)
        except schema.NotAnAnswer:
            pass
    wanted: list[int] = sorted(tying_pos(db, answer))
    vendor = vendor_of_domain(db, address_domain(message.sender)) or extraction.vendor_id
    if vendor is not None:
        wanted += [i for i in _open_po_ids(db, vendor) if i not in wanted]
    pos = {p.id: p for p in db.scalars(select(PurchaseOrder).where(PurchaseOrder.id.in_(wanted)))} if wanted else {}
    return [pos[i] for i in wanted if i in pos]

