"""Accepting a read document (ARCHITECTURE section 6.4, DEC-54, DEC-08, PD-4.5).

Accepting is a person's act: the model proposes, the person confirms, and the values recorded are the ones in the form,
not the model's. The entry is made by the same code that records entries typed by hand (`ledger/events.py`, DEC-38), in
the caller's transaction, so the entry, its tracking numbers or charges, the link to the document, the person and the
extraction's new state are saved together or not at all. The extraction keeps the model's answer and, in `final`, what
was accepted, so a corrected field can be seen later. A second Accept of the same document is refused by the
extraction's `version` (DEC-52). Nothing here sends, moves or deletes mail (DEC-07)."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session
from sqlalchemy.orm.exc import StaleDataError

from po_dashboard.auth.service import audit
from po_dashboard.db.base import utcnow
from po_dashboard.db.ledger import POLine, PurchaseOrder
from po_dashboard.db.mail import SourceMessage
from po_dashboard.db.models import AppUser
from po_dashboard.db.types import Money
from po_dashboard.extraction import matcher, review, schema
from po_dashboard.extraction.matching import match_lines
from po_dashboard.extraction.review import DEFAULT_CURRENCY, TOP_FIELDS, _message_of, _open, shown
from po_dashboard.extraction.validate import NEEDS_TICK
from po_dashboard.ledger import events, orders
from po_dashboard.ledger.errors import LedgerError, number, text

LINE_KEYS = ("line_no", "item", "qty", "unit", "unit_price", "promised_date", "arrival_date", "requested_date")
MAX_LINES = 500  # the form is read only this far, whatever the model's answer holds
SPLIT = re.compile(r"[,;\s]+")


def _field(form: Mapping[str, str], name: str) -> str:
    return str(form.get(name, "")).strip()


def _line_values(form: Mapping[str, str], index: int) -> dict[str, str]:
    return {key: _field(form, f"line_{index}_{key}") for key in LINE_KEYS}


def _po_line_ids(db: Session, po: PurchaseOrder) -> dict[int, int]:
    return {number_: line_id for line_id, number_ in db.execute(
        select(POLine.id, POLine.line_no).where(POLine.po_id == po.id))}  # fmt: skip


def _assign(
    db: Session, po: PurchaseOrder, doc_type: str, form: Mapping[str, str], count: int
) -> list[tuple[int, int, dict[str, str]]]:
    """(form line index, PO line id, the line's values) for every line not left out. A line is matched the way the
    validator matched it, from what is in the form now, so typing a PO line number assigns it. A line that still
    matches nothing stops the Accept: nothing is guessed."""
    kept = [i for i in range(count) if not _field(form, f"line_{i}_skip")]
    values = [_line_values(form, i) for i in kept]
    as_lines = [
        {"line_no": int(v["line_no"]) if v["line_no"].isascii() and v["line_no"].isdigit() else None, "item": v["item"]}
        for v in values
    ]
    found = match_lines(as_lines, matcher.po_lines_for(db, po.id, doc_type))
    ids = _po_line_ids(db, po)
    out: list[tuple[int, int, dict[str, str]]] = []
    for index, value, matched in zip(kept, values, found, strict=True):
        if matched is None or matched not in ids:
            raise LedgerError(
                f"Line {index + 1} matches no line on PO {po.po_number}. Type the PO line number it belongs to, or "
                "tick 'Leave out'."
            )
        out.append((index, ids[matched], value))
    return out


def _tracking(raw: str) -> list[str]:
    return [part for part in SPLIT.split(raw) if part]


def _charges(form: Mapping[str, str], count: int) -> list[events.ChargeInput]:
    charges: list[events.ChargeInput] = []
    for i in range(count):
        kind, label, amount = (_field(form, f"charge_{i}_{k}") for k in ("kind", "label", "amount"))
        if not (kind or label or amount):
            continue
        where = f"Charge {i + 1}"
        if kind.lower() not in events.CHARGE_KINDS:
            raise LedgerError(f"{where}: kind must be one of {', '.join(events.CHARGE_KINDS)}.")
        try:
            negative = amount.startswith("-")  # a discount is `other` with a negative amount
            figure = number(amount.removeprefix("-"), "Amount", Money, positive=False)
            named = text(label, "Label", longest=200, required=True) or ""
        except LedgerError as problem:
            raise LedgerError(f"{where}: {problem}") from None
        charges.append(events.ChargeInput(kind.lower(), named, -figure if negative else figure))
    return charges


def _entry(
    db: Session,
    row: Any,
    answer: Mapping[str, Any],
    form: Mapping[str, str],
    actor: AppUser,
    client_address: str | None,
) -> tuple[str, int]:
    """Record the entry and return (kind, id)."""
    doc_type = str(answer.get("doc_type"))
    count = min(len([x for x in answer.get("lines") or [] if isinstance(x, Mapping)]), MAX_LINES)
    document_id = row.document_id
    if doc_type == "purchase_order":
        return _new_po(db, row, form, count, actor, client_address)
    if doc_type not in ("order_acknowledgement", "shipment_notice", "invoice"):
        raise LedgerError(review.NOT_AN_ORDER)
    po = db.get(PurchaseOrder, row.po_id) if row.po_id else None
    if po is None:
        raise LedgerError(review.NEEDS_A_PO)
    lines = _assign(db, po, doc_type, form, count)

    if doc_type == "order_acknowledgement":
        entries = [
            acked for index, line_id, v in lines
            if (acked := events.parse_ack_line(
                f"Line {index + 1}", line_id, v["qty"], v["unit_price"], v["promised_date"]))
        ]  # fmt: skip
        # a document that lists no lines at all is "acknowledged, no details" for every line (REQ-ACK-03)
        ack = events.record_ack(
            db,
            po,
            entries,
            without_details=count == 0,
            actor=actor,
            client_address=client_address,
            source_document_id=document_id,
        )
        return "ack", ack.id

    if doc_type == "shipment_notice":
        shipped = [
            sent for index, line_id, v in lines
            if (sent := events.parse_ship_line(f"Line {index + 1}", line_id, v["qty"], v["arrival_date"]))
        ]  # fmt: skip
        shipment = events.record_shipment(
            db,
            po,
            shipped,
            carrier=_field(form, "f_carrier"),
            shipped_at=_field(form, "f_ship_date"),
            eta=_field(form, "f_arrival_date"),
            actor=actor,
            client_address=client_address,
            source_document_id=document_id,
            tracking_numbers=_tracking(_field(form, "f_tracking_numbers")),
        )
        return "shipment", shipment.id

    billed = [
        charged for index, line_id, v in lines
        if (charged := events.parse_invoice_line(f"Line {index + 1}", line_id, v["qty"], v["unit_price"]))
    ]  # fmt: skip
    n_charges = len([c for c in answer.get("charges") or [] if isinstance(c, Mapping)])
    invoice = events.record_invoice(
        db,
        po,
        billed,
        invoice_number=_field(form, "f_invoice_number"),
        invoice_date=_field(form, "f_invoice_date"),
        total=_field(form, "f_total"),
        actor=actor,
        client_address=client_address,
        source_document_id=document_id,
        charges=_charges(form, n_charges),
    )
    return "invoice", invoice.id


def _new_po(
    db: Session, row: Any, form: Mapping[str, str], count: int, actor: AppUser, client_address: str | None
) -> tuple[str, int]:
    """An emailed PO (DEC-17): the vendor was found from the recipients. The PO number must be new; one that exists is
    shown as differences only and never changed (the person edits it by hand). Only the company's own mail brings a
    new PO (DEC-73), checked here again whatever state the row is in."""
    if row.po_id is not None:
        raise LedgerError(review.PO_EXISTS)
    message = db.get(SourceMessage, _message_of(db, row))
    if message is None or not matcher.from_company(message, matcher.own_domain_of(db, message)):
        raise LedgerError(review.PO_FROM_OUTSIDE)
    if row.vendor_id is None:
        raise LedgerError(review.PO_NO_VENDOR)
    entries: list[orders.LineInput] = []
    for i in range(count):
        if _field(form, f"line_{i}_skip"):
            continue
        v = _line_values(form, i)
        entries.append(orders.parse_line(
            f"Line {i + 1}", item=v["item"], quantity=v["qty"], unit=v["unit"], unit_price=v["unit_price"],
            requested_date=v["requested_date"],
            line_no=int(v["line_no"]) if v["line_no"].isascii() and v["line_no"].isdigit() else None,
        ))  # fmt: skip
    order = orders.create_po(
        db,
        po_number=_field(form, "f_po_number"),
        vendor_id=row.vendor_id,
        currency=_field(form, "f_currency") or DEFAULT_CURRENCY,
        date_sent=None,
        buyer=None,
        lines=entries,
        source="document",
        actor=actor,
        client_address=client_address,
    )
    row.po_id = order.id
    return "po", order.id


def _changed(answer: Mapping[str, Any], form: Mapping[str, str]) -> list[str]:
    """Which fields a person changed from what the model read, by name only (never a value)."""
    doc_type = str(answer.get("doc_type"))
    names = ["po_number", *TOP_FIELDS.get(doc_type, ())]
    changed = [n for n in names if f"f_{n}" in form and _field(form, f"f_{n}") != shown(answer.get(n))]
    for i, line in enumerate(x for x in answer.get("lines") or [] if isinstance(x, Mapping)):
        for key in LINE_KEYS:
            name = f"line_{i}_{key}"
            if name in form and _field(form, name) != shown(line.get(key)):
                changed.append(name)
    return changed


def _final(answer: Mapping[str, Any], form: Mapping[str, str], ticks: list[str]) -> str:
    """The values accepted, as the person had them in the form, for the record."""
    doc_type = str(answer.get("doc_type"))
    count = min(len([x for x in answer.get("lines") or [] if isinstance(x, Mapping)]), MAX_LINES)
    fields = {n: _field(form, f"f_{n}") for n in ["po_number", "currency", *TOP_FIELDS.get(doc_type, ())]
              if f"f_{n}" in form}  # fmt: skip
    return json.dumps({
        "doc_type": doc_type, "fields": fields,
        "lines": [_line_values(form, i) | {"left_out": bool(_field(form, f"line_{i}_skip"))} for i in range(count)],
        "charges": [{k: _field(form, f"charge_{i}_{k}") for k in ("kind", "label", "amount")}
                    for i in range(len(answer.get("charges") or []))],
        "ticks": ticks,
    }, default=str)  # fmt: skip


def accept(
    db: Session,
    extraction_id: int,
    seen: int | None,
    form: Mapping[str, str],
    actor: AppUser,
    client_address: str | None = None,
) -> int:
    """Accept one document with the values in `form`. Returns its message id. The caller commits, or rolls back when
    this raises LedgerError (the entry and the new state then both vanish)."""
    row = _open(db, extraction_id, seen)
    if not row.answer:
        raise LedgerError("This document has no answer to accept.")
    answer = schema.parse_answer(row.answer)
    flags = [f for f in json.loads(row.flags) if isinstance(f, dict)] if row.flags else []
    names = {str(f.get("flag")) for f in flags}
    if "po_already_exists" in names:
        raise LedgerError(review.PO_EXISTS)
    needed = sorted(n for n in names if n in NEEDS_TICK)
    missing = [n for n in needed if _field(form, f"tick_{n}") != "1"]
    if missing:
        raise LedgerError(
            "Tick each box to say you checked it before accepting: "
            + ", ".join(m.replace("_", " ") for m in missing)
            + "."
        )
    kind, entry_id = _entry(db, row, answer, form, actor, client_address)
    before = row.review_state
    row.review_state, row.reviewed_by, row.reviewed_at = "accepted", actor.id, utcnow()
    row.entry_kind, row.entry_id = kind, entry_id
    row.final = _final(answer, form, needed)
    try:
        db.flush()  # a second Accept racing this one fails here on the extraction's version, and nothing is kept
    except StaleDataError:
        raise LedgerError("This document was changed by someone else (or in another tab). Reload the page.") from None
    audit(
        db,
        "extraction.accept",
        actor,
        f"extraction:{row.id}",
        client_address,
        before={"state": before},
        after={"entry": kind, "entry_id": entry_id, "changed": _changed(answer, form), "ticks": needed},
    )
    return _message_of(db, row)
