"""What the Review screens show and what a person can do there (ARCHITECTURE section 6.4, DEC-54, PD-4.4).

A read document waits in one of three places: **Waiting for review** (matched, or an emailed PO whose vendor was
found), **Unmatched** (a person picks the PO), or **No order facts** (kept, never queued). A person can Reject a
document (a reason is required), File it as no order facts, or Attach it to a PO, which matches and validates it
again. Accept is in `accept.py` (PD-4.5), through the code that records entries by hand. Every action is audited
and refuses a stale page (DEC-52). Nothing here sends, moves or deletes mail (DEC-07)."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from po_dashboard.auth.service import audit
from po_dashboard.db.base import utcnow
from po_dashboard.db.ledger import POLine, PurchaseOrder
from po_dashboard.db.mail import Extraction, SourceDocument, SourceMessage
from po_dashboard.db.models import AppUser
from po_dashboard.extraction import matcher, schema, window
from po_dashboard.extraction.matching import PoLine, match_lines
from po_dashboard.extraction.scoring import norm_id
from po_dashboard.extraction.validate import BLOCKS_ACCEPT, NEEDS_TICK, VENDOR_DOCS
from po_dashboard.intake.store import FileStore
from po_dashboard.ledger.errors import LedgerError, check_seen, text

DEFAULT_CURRENCY = "USD"  # the company default; schema v1 reads no currency (DEC-54)
OPEN_STATES = (matcher.WAITING, matcher.UNMATCHED)  # the states a person can still act on
SHOWS = {"waiting": matcher.WAITING, "unmatched": matcher.UNMATCHED, "no_facts": matcher.NO_FACTS}
TITLES = {"waiting": "Waiting for review", "unmatched": "Unmatched", "no_facts": "No order facts"}

# What each flag says, in plain words. The flag holds only its name, a line and a field, never document text.
FLAG_WORDS = {
    "sender_not_vendor": "The sender is not a known address of this PO's vendor.",
    "value_not_in_source": "A value the model gave was not found in the document's text.",
    "planted_instruction": "The text has words addressed to software that reads it (\"ignore previous instructions\").",
    "line_unassigned": "This line matched no line on the PO. Assign it or leave it out.",
    "po_already_exists": "This PO number already exists. Accepting changes nothing; edit the PO by hand.",
    "duplicate": "The same document, invoice number or tracking number is already recorded or waiting.",
    "qty_below_order": "The quantity is below what is still open on the line.",
    "confirmed_over_order": "The vendor confirmed more than was ordered.",
    "shipped_over_order": "More is shipped than is still open on the line.",
    "billed_over_order": "More is billed than is still open on the line.",
    "price_differs": "The unit price is not the PO line's.",
    "unit_differs": "The unit is not the PO line's (units are never converted).",
    "no_promised_date": "The acknowledgement gives no promised date for this line.",
    "date_changed": "The promised date is not the one last accepted for this line.",
    "date_implausible": "A date is over a year away, or before the PO's own date.",
    "totals_disagree": "Goods plus charges do not equal the printed total.",
    "read_in_part": "The document was longer than the reading window, so part of it was not read.",
    "lines_not_covered": "The document covers some, not all, of the PO's open lines.",
    "charge_present": "The document lists a charge (freight, tariff, tax or other). Shown only.",
}
FIELD_WORDS = {
    "po_number": "PO number", "vendor_order_number": "vendor order number", "invoice_number": "invoice number",
    "tracking_numbers": "tracking number", "invoice_date": "invoice date", "ship_date": "ship date",
    "arrival_date": "arrival date", "total": "total", "qty": "quantity", "unit_price": "unit price",
    "promised_date": "promised date", "requested_date": "requested date", "amount": "charge amount", "unit": "unit",
}

# Which top-level fields and which line date each kind of document shows
TOP_FIELDS = {
    "purchase_order": (),
    "order_acknowledgement": ("vendor_order_number",),
    "shipment_notice": ("vendor_order_number", "carrier", "tracking_numbers", "ship_date", "arrival_date"),
    "invoice": ("vendor_order_number", "invoice_number", "invoice_date", "total"),
}
LINE_DATE = {"purchase_order": "requested_date", "order_acknowledgement": "promised_date",
             "shipment_notice": "arrival_date"}  # fmt: skip

# Why Accept cannot be used on a document, in the words the page and the refusal both use
PO_EXISTS = "This PO number already exists. Accepting changes nothing; edit the PO by hand."
PO_FROM_OUTSIDE = (
    "This PO did not come from the company (it is not in Sent Items, and the sender is not at the company's own "
    "domain), so it cannot be created from this email. If the PO is real, enter it by hand."
)
PO_NO_VENDOR = "No vendor was found for this PO. It cannot be accepted."
NOT_AN_ORDER = "Only a PO, an acknowledgement, a shipment notice or an invoice can be accepted."
NEEDS_A_PO = "Attach this document to a PO before accepting it."


def why_not_accepted(row: Extraction, doc_type: str, message: SourceMessage, own_domain: str | None) -> str | None:
    """The reason a document cannot be accepted as it stands, or None if it can (DEC-54, DEC-73)."""
    if doc_type == "purchase_order":
        if row.po_id is not None:
            return PO_EXISTS
        if not matcher.from_company(message, own_domain):
            return PO_FROM_OUTSIDE
        return PO_NO_VENDOR if row.vendor_id is None else None
    if doc_type not in VENDOR_DOCS:
        return NOT_AN_ORDER
    return NEEDS_A_PO if row.po_id is None else None


def shown(value: Any) -> str:
    """A value as a person reads it: no exponent on a Decimal, no trailing zeros after a point, null as empty."""
    if value is None:
        return ""
    if isinstance(value, Decimal):
        plain = format(value, "f")
        return plain.rstrip("0").rstrip(".") if "." in plain else plain
    if isinstance(value, list):
        return ", ".join(shown(v) for v in value)
    return str(value)


@dataclass(frozen=True)
class FlagView:
    flag: str
    level: str
    words: str
    field: str | None = None
    tick: bool = False
    blocks: bool = False


@dataclass
class LineView:
    index: int
    values: dict[str, str]
    po_line: PoLine | None = None
    requested: date | None = None
    flags: list[FlagView] = field(default_factory=list)
    differs: set[str] = field(default_factory=set)  # unit_price, unit, qty


@dataclass
class DocView:
    row: Extraction
    document: SourceDocument
    text: str
    answer: Mapping[str, Any] | None
    po: PurchaseOrder | None
    top: list[tuple[str, str, str]]  # (field, label, value)
    lines: list[LineView]
    charges: list[dict[str, str]]
    date_field: str | None
    flags: list[FlagView]
    ticks: list[FlagView]
    unassigned: int
    differences: list[str]  # an emailed PO that already exists: what the document says differently (never applied)
    candidates: list[PurchaseOrder]
    actionable: bool
    cannot_accept: str | None = None  # why Accept is not offered, in plain words


def counts(db: Session) -> dict[str, int]:
    """Documents in each place, for Home and the list's links."""
    rows = db.execute(
        select(Extraction.review_state, func.count()).where(Extraction.state == "done").group_by(
            Extraction.review_state)
    ).all()
    found = {state: n for state, n in rows}
    return {key: found.get(state, 0) for key, state in SHOWS.items()}


def flag_count(raw: str | None) -> int:
    """How many flags a stored flag list holds (a note counts, as on the list)."""
    return len(json.loads(raw)) if raw else 0


def listing(db: Session, show: str) -> list[tuple[Extraction, SourceDocument, SourceMessage]]:
    return matcher.by_state(db, SHOWS[show])


def _flag_view(item: Mapping[str, Any]) -> FlagView:
    name = str(item.get("flag", ""))
    return FlagView(
        name, str(item.get("level", "check")), FLAG_WORDS.get(name, name), item.get("field"),
        tick=name in NEEDS_TICK, blocks=name in BLOCKS_ACCEPT,
    )  # fmt: skip


def _flag_views(raw: str | None) -> list[FlagView]:
    return [_flag_view(item) for item in json.loads(raw)] if raw else []


def _lines(db: Session, row: Extraction, answer: Mapping[str, Any], flags: list[dict[str, Any]]) -> list[LineView]:
    doc_type = str(answer.get("doc_type"))
    doc_lines = [x for x in answer.get("lines") or [] if isinstance(x, Mapping)]
    po_lines = matcher.po_lines_for(db, row.po_id, doc_type) if row.po_id else ()
    matched = match_lines(doc_lines, po_lines)
    requested = {}
    if row.po_id:
        requested = {n: d for n, d in db.execute(select(POLine.line_no, POLine.requested_date).where(
            POLine.po_id == row.po_id))}
    by_no = {p.line_no: p for p in po_lines}
    out: list[LineView] = []
    for i, (line, m) in enumerate(zip(doc_lines, matched, strict=True)):
        view = LineView(i, {k: shown(line.get(k)) for k in schema.LINE_FIELDS})
        if m is not None:
            view.po_line, view.requested = by_no[m], requested.get(m)
        for item in flags:
            if item.get("line") != i:
                continue
            view.flags.append(_flag_view(item))
            if item.get("field") in ("unit_price", "unit", "qty"):
                view.differs.add(item["field"])
        out.append(view)
    return out


def _document_text(store: FileStore, document: SourceDocument) -> str:
    if not document.text_path:
        return ""
    try:
        return store.read(document.text_path).decode("utf-8", errors="replace")
    except (OSError, ValueError):
        return ""


def po_differences(db: Session, po_id: int, answer: Mapping[str, Any]) -> list[str]:
    """A revised emailed PO: only what the document says differently from the PO already recorded, in words. Nothing
    is applied; a person edits the PO by hand. Quantity, unit, price and requested date are compared; a line the PO
    has and the document lacks, or the reverse, is named."""
    doc_lines = [x for x in answer.get("lines") or [] if isinstance(x, Mapping)]
    po_lines = matcher.po_lines_for(db, po_id, "purchase_order")
    matched = match_lines(doc_lines, po_lines)
    rows = {r.line_no: r for r in db.scalars(select(POLine).where(POLine.po_id == po_id))}
    notes: list[str] = []
    for i, (line, m) in enumerate(zip(doc_lines, matched, strict=True)):
        if m is None:
            notes.append(f"Document line {i + 1} is not on the PO.")
            continue
        have = rows[m]
        pairs = (
            ("quantity", have.ordered_qty, line.get("qty")), ("unit", have.unit, line.get("unit")),
            ("unit price", have.unit_price, line.get("unit_price")), ("requested date", have.requested_date,
                                                                       line.get("requested_date")),
        )  # fmt: skip
        for name, recorded, said in pairs:
            if said is not None and shown(said) != shown(recorded):
                was = shown(recorded) or "empty"
                notes.append(f"Line {m}: {name} is {was} on the PO, {shown(said)} in the document.")
    covered = {m for m in matched if m is not None}
    notes += [f"PO line {n} is not in the document." for n in sorted(rows) if n not in covered]
    return notes


def _show_accepted(final: Mapping[str, Any], top: list[tuple[str, str, str]], lines: list[LineView]) -> None:
    """Replace the model's values with the accepted ones kept in `final` (the model's answer stays in the row)."""
    fields = final.get("fields") or {}
    top[:] = [(f, label, str(fields.get(f, value))) for f, label, value in top]
    for view, kept in zip(lines, final.get("lines") or [], strict=False):
        view.values.update({k: str(v) for k, v in kept.items() if k in view.values})


def message_view(db: Session, store: FileStore, message_id: int) -> tuple[SourceMessage, list[DocView]] | None:
    """One message and each of its documents that has been read, with what the Review screen shows."""
    message = db.get(SourceMessage, message_id)
    if message is None:
        return None
    views: list[DocView] = []
    own_domain = matcher.own_domain_of(db, message)
    rows = db.execute(
        select(Extraction, SourceDocument).join(SourceDocument, SourceDocument.id == Extraction.document_id)
        .where(SourceDocument.message_id == message_id, Extraction.state == "done")
        .order_by(SourceDocument.id, Extraction.id)
    )
    for row, document in rows:
        answer = None
        if row.answer:
            try:
                answer = schema.parse_answer(row.answer)
            except schema.NotAnAnswer:
                answer = None
        raw = json.loads(row.flags) if row.flags else []
        flags = _flag_views(row.flags)
        doc_type = str((answer or {}).get("doc_type"))
        top = [("po_number", "PO number", shown((answer or {}).get("po_number")))]
        if doc_type == "purchase_order" and row.po_id is None:
            top.append(("currency", "Currency", DEFAULT_CURRENCY))  # schema v1 reads none; the person may change it
        read = answer or {}
        top += [(f, FIELD_WORDS.get(f, f).capitalize(), shown(read.get(f))) for f in TOP_FIELDS.get(doc_type, ())]
        charges = [{k: shown(c.get(k)) for k in ("kind", "label", "amount")}
                   for c in (answer or {}).get("charges") or [] if isinstance(c, Mapping)]  # fmt: skip
        lines = _lines(db, row, answer, raw) if answer else []
        if row.review_state == "accepted" and row.final:  # what a person accepted, not what the model read
            _show_accepted(json.loads(row.final), top, lines)
        unassigned = sum(1 for f in flags if f.blocks)
        po = db.get(PurchaseOrder, row.po_id) if row.po_id else None
        views.append(DocView(
            row=row, document=document, text=_document_text(store, document), answer=answer, po=po, top=top,
            lines=lines, charges=charges, date_field=LINE_DATE.get(doc_type),
            flags=flags, ticks=list({f.flag: f for f in flags if f.tick}.values()),
            unassigned=unassigned,
            differences=po_differences(db, po.id, answer) if po and doc_type == "purchase_order" and answer else [],
            candidates=matcher.candidates(db, row, message) if row.review_state == matcher.UNMATCHED else [],
            actionable=row.review_state in OPEN_STATES,
            cannot_accept=why_not_accepted(row, doc_type, message, own_domain),
        ))  # fmt: skip
    return message, views


# ---- actions: each checks the page is current, changes one extraction and audits it. They do not commit.


def _open(db: Session, extraction_id: int, seen: int | None) -> Extraction:
    row = db.get(Extraction, extraction_id)
    if row is None or row.state != "done" or row.review_state not in OPEN_STATES:
        raise LedgerError("That document is not waiting for review any more.")
    check_seen(row.version, seen, "This document")
    return row


def reject(db: Session, extraction_id: int, seen: int | None, reason: str | None, actor: AppUser) -> int:
    """Keep the extraction, marked rejected, with who and why. Returns its message id."""
    note = text(reason, "Reason", longest=500, required=True)
    row = _open(db, extraction_id, seen)
    before = row.review_state
    row.review_state, row.reviewed_by, row.reviewed_at, row.review_note = "rejected", actor.id, utcnow(), note
    audit(db, "extraction.reject", actor, f"extraction:{row.id}", before={"state": before}, after={"reason": note})
    return _message_of(db, row)


def file_as_no_facts(db: Session, extraction_id: int, seen: int | None, actor: AppUser) -> int:
    """Move a document to No order facts: kept and searchable, out of the review queue. Returns its message id."""
    row = _open(db, extraction_id, seen)
    before = row.review_state
    row.review_state, row.reviewed_by, row.reviewed_at, row.po_id = matcher.NO_FACTS, actor.id, utcnow(), None
    audit(db, "extraction.file", actor, f"extraction:{row.id}", before={"state": before}, after={"state": "no_facts"})
    return _message_of(db, row)


def find_po(db: Session, chosen_id: str | None, typed_number: str | None) -> PurchaseOrder:
    """The PO a person chose: the one typed (exactly, as a PO number is matched), else the one picked from the list."""
    wanted = norm_id(typed_number)
    if wanted:
        for po in db.scalars(select(PurchaseOrder)):
            if norm_id(po.po_number) == wanted:
                return po
        raise LedgerError("No PO has that number.")
    if chosen_id and chosen_id.isascii() and chosen_id.isdigit():
        chosen = db.get(PurchaseOrder, int(chosen_id))
        if chosen is not None:
            return chosen
    raise LedgerError("Choose a PO from the list or type its number.")


def attach(
    db: Session, store: FileStore, extraction_id: int, seen: int | None, po: PurchaseOrder, actor: AppUser,
    *, today: date | None = None,
) -> int:  # fmt: skip
    """A person's choice of PO for a document: it is matched to that PO and validated against it again. Returns its
    message id."""
    row = _open(db, extraction_id, seen)
    document = db.get(SourceDocument, row.document_id)
    message = db.get(SourceMessage, document.message_id) if document else None
    if document is None or message is None or not row.answer:
        raise LedgerError("That document cannot be attached.")
    answer = schema.parse_answer(row.answer)
    if answer.get("doc_type") not in VENDOR_DOCS:
        raise LedgerError("Only an acknowledgement, a shipment notice or an invoice is attached to a PO.")
    fitted = window.fit(_document_text(store, document))
    found = matcher.decide(
        db, answer, fitted.text, message, document, own_domain=matcher.own_domain_of(db, message),
        today=today or utcnow().date(), read_in_part=fitted.read_in_part, own_id=row.id, force_po_id=po.id,
    )
    before = {"state": row.review_state, "po_id": row.po_id}
    row.po_id, row.vendor_id, row.review_state = found.po_id, found.vendor_id, found.review_state
    row.flags = found.flags_json()
    after = {"po_id": po.id, "po": po.po_number}
    audit(db, "extraction.attach", actor, f"extraction:{row.id}", before=before, after=after)
    return message.id


def _message_of(db: Session, row: Extraction) -> int:
    document = db.get(SourceDocument, row.document_id)
    if document is None:
        raise LedgerError("That document is gone.")
    return document.message_id


# ---- the original, served as a download (ARCHITECTURE section 6.4)

VIEWABLE = {"pdf": "application/pdf"}
VIEWABLE_IMAGES = {"image/png", "image/jpeg", "image/gif"}


def original(
    document: SourceDocument, message: SourceMessage, store: FileStore, view: bool
) -> tuple[bytes, str, str, bool]:
    """(bytes, media type, download name, may open in the browser's viewer). A body is the raw message, saved as .eml.
    The type comes from the stored kind, never from the file's own claim; only a PDF or a plain picture may open in the
    browser (under a sandbox policy); everything else is only a download."""
    data = store.read(document.stored_path)
    if document.kind == "body":
        return data, "message/rfc822", f"message-{message.id}.eml", False
    clean = "".join(c for c in document.filename if c.isalnum() or c in " ._-")[:120].strip()
    name = clean or f"document-{document.id}"
    if document.kind == "pdf":
        return data, VIEWABLE["pdf"], name, view
    if document.kind == "image" and (document.content_type or "") in VIEWABLE_IMAGES:
        return data, document.content_type or "application/octet-stream", name, view
    return data, "application/octet-stream", name, False
