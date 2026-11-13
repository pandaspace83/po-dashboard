"""The validator (ARCHITECTURE section 6.3, DEC-54, PD-4.2): plain code that computes the flags a person sees on the
Review screen. Pure functions: no database, no model, no files. The caller (PD-4.3) gives it the model's answer, the
document's text and what is known about the PO.

A **check** asks a person to look; a **note** is information only. Three checks need a tick before Accept
(`NEEDS_TICK`) and an unassigned line blocks Accept until it is assigned or left out (`BLOCKS_ACCEPT`). A flag never
changes anything by itself (DEC-38). The flag names are the golden set's, so PD-4.7 can score them (T-5).

The answer is data (DEC-09): nothing here follows anything the document says. A flag holds a field name and a line
number, never document text (DEC-13)."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from po_dashboard.extraction.in_source import SourceText, ocr_view
from po_dashboard.extraction.matching import PoLine, item_key, match_lines
from po_dashboard.extraction.scoring import norm_id, number

CHECK, NOTE = "check", "note"

FLAG_NAMES = (
    "sender_not_vendor", "value_not_in_source", "planted_instruction", "line_unassigned", "po_already_exists",
    "duplicate", "qty_below_order", "confirmed_over_order", "shipped_over_order", "billed_over_order",
    "price_differs", "unit_differs", "no_promised_date", "date_changed", "date_implausible", "totals_disagree",
    "read_in_part", "lines_not_covered", "charge_present",
)  # fmt: skip
NEEDS_TICK = frozenset({"sender_not_vendor", "value_not_in_source", "planted_instruction"})
BLOCKS_ACCEPT = frozenset({"line_unassigned"})

VENDOR_DOCS = ("order_acknowledgement", "shipment_notice", "invoice")
OVER_ORDER = {
    "order_acknowledgement": "confirmed_over_order", "shipment_notice": "shipped_over_order",
    "invoice": "billed_over_order",
}  # fmt: skip
TOO_FAR_AHEAD = timedelta(days=365)
CENT = Decimal("0.01")


@dataclass(frozen=True)
class Flag:
    """One flag. `line` is the index of a goods line in the answer's `lines`; `charge` the index in its `charges`."""

    flag: str
    level: str
    line: int | None = None
    charge: int | None = None
    field: str | None = None

    def as_json(self) -> dict[str, Any]:
        """The shape stored in `extraction.flags`: {flag, level} and, when they apply, line, charge and field."""
        where = {"line": self.line, "charge": self.charge, "field": self.field}
        return {"flag": self.flag, "level": self.level} | {k: v for k, v in where.items() if v is not None}


@dataclass(frozen=True)
class Earlier:
    """An extraction already accepted or waiting for the same PO, as `duplicate` compares it: its type and its lines
    (each by the PO line it matched, or its item, with its quantity). Made by `fingerprint`."""

    doc_type: str
    lines: tuple[tuple[str, Decimal | None], ...]


@dataclass(frozen=True)
class Context:
    """What the caller knows. `po_lines` is None when no PO was matched (the Unmatched queue, an emailed PO), and
    then nothing is compared with a PO. `sender_is_vendor` is None when it does not apply."""

    today: date
    po_lines: tuple[PoLine, ...] | None = None
    po_date: date | None = None
    sender_is_vendor: bool | None = None
    po_exists: bool = False  # an emailed purchase order whose number is already a PO
    earlier: tuple[Earlier, ...] = ()
    recorded_invoice_numbers: frozenset[str] = frozenset()
    recorded_tracking_numbers: frozenset[str] = frozenset()
    read_in_part: bool = False
    ocr: bool = False  # the text came from OCR: look-alike letters and digits count as equal


# ---- planted instructions: words addressed to software that reads the document (DEC-09)

# A fixed list, matched on the text in lower case with its spacing and quote marks ("> ") taken out. Tested against
# the golden set's eight planted documents and its 92 clean ones, and against everyday vendor wording ("This is an
# automated message", "please disregard our earlier invoice") that must not be caught.
PLANTED_PATTERNS = tuple(re.compile(p) for p in (
    # ignore / disregard / forget ... instructions (or rules, prompts)
    r"\b(?:ignore|disregard|forget|override|bypass)\b(?:\s+[\w']+){0,4}?\s+"
    r"(?:instructions?|rules|prompts?|guidelines|directives)\b",
    r"\bnew instructions\b",
    # a note to the software: "note to automated readers", "note to the assistant"
    r"\b(?:note|message|instructions?|attention)\s+(?:to|for)\s+(?:the\s+|any\s+|all\s+)?"
    r"(?:ai|assistants?|automated|bots?|models?|llms?|language models?|readers?|parsers?)\b",
    r"\bautomated\s+(?:readers?|parsers?|assistants?|agents?)\b",
    r"\b(?:ai|llm|gpt|chatbot)\s+(?:processing|readers?|assistants?|notes?|instructions?|agents?)\b",
    r"(?:^|\n)\s*(?:assistant|system|ai|llm)\s*:",
    r"\byou are (?:an? |the )?(?:ai|assistant|language model|chatbot|llm)\b",
    r"\b(?:system|developer)\s+(?:prompt|note|override|instructions?)\b",
    r"\bprompt injection\b|\bjailbreak\b",
))  # fmt: skip


def _plain(text: str) -> str:
    text = re.sub(r"(?m)^[ \t]*(?:>[ \t]?)+", "", text)  # quote marks at the start of a line
    return "\n".join(re.sub(r"[ \t]+", " ", line).strip() for line in text.lower().splitlines())


def has_planted_instruction(text: str, *, ocr: bool = False) -> bool:
    views = [text, ocr_view(text)] if ocr else [text]
    for view in views:
        plain = _plain(view)
        joined = plain.replace("\n", " ")  # a phrase broken across lines, as small print often is
        if any(p.search(plain) or p.search(joined) for p in PLANTED_PATTERNS):
            return True
    return False


# ---- helpers


def _lines(answer: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    return [line for line in answer.get("lines") or [] if isinstance(line, Mapping)]


def _charges(answer: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    return [charge for charge in answer.get("charges") or [] if isinstance(charge, Mapping)]


def _date(value: Any) -> date | None:
    try:
        return date.fromisoformat(str(value)) if value is not None else None
    except ValueError:
        return None


def fingerprint(answer: Mapping[str, Any], po_lines: Sequence[PoLine] | None) -> Earlier:
    """How `duplicate` sees a document: its type and, for each line, the PO line it matched (or its item) and its
    quantity, in a fixed order."""
    lines = _lines(answer)
    matched = match_lines(lines, po_lines or ())
    keys = []
    for line, m in zip(lines, matched, strict=True):
        qty = number(line.get("qty"))
        keys.append((f"line {m}" if m is not None else f"item {item_key(line.get('item'))}",
                     qty.normalize() if qty is not None else None))  # fmt: skip
    return Earlier(str(answer.get("doc_type")), tuple(sorted(keys, key=lambda k: (k[0], str(k[1])))))


# ---- the flags


def _not_in_source(answer: Mapping[str, Any], source: SourceText) -> list[Flag]:
    found: list[Flag] = []

    def missing(field: str, line: int | None = None, charge: int | None = None) -> None:
        found.append(Flag("value_not_in_source", CHECK, line=line, charge=charge, field=field))

    for field in ("po_number", "vendor_order_number", "invoice_number"):
        if answer.get(field) is not None and not source.has_code(answer[field]):
            missing(field)
    for tracking in answer.get("tracking_numbers") or []:
        if not source.has_code(tracking):
            missing("tracking_numbers")
    for field in ("invoice_date", "ship_date", "arrival_date"):
        if answer.get(field) is not None and not source.has_date(answer[field]):
            missing(field)
    if answer.get("total") is not None and not source.has_number(answer["total"]):
        missing("total")
    for i, line in enumerate(_lines(answer)):
        for field in ("qty", "unit_price"):
            if line.get(field) is not None and not source.has_number(line[field]):
                missing(field, line=i)
        for field in ("promised_date", "arrival_date", "requested_date"):
            if line.get(field) is not None and not source.has_date(line[field]):
                missing(field, line=i)
    for i, charge in enumerate(_charges(answer)):
        if charge.get("amount") is not None and not source.has_number(charge["amount"]):
            missing("amount", charge=i)
    return found


def _against_po(answer: Mapping[str, Any], doc_type: str, po_lines: tuple[PoLine, ...]) -> list[Flag]:
    """The flags that compare a vendor's document with its PO's lines."""
    found: list[Flag] = []
    lines = _lines(answer)
    matched = match_lines(lines, po_lines)
    by_no = {po.line_no: po for po in po_lines}
    totals: dict[int, tuple[int, Decimal]] = {}  # PO line -> (its first document line, the quantity on all of them)
    for i, (line, m) in enumerate(zip(lines, matched, strict=True)):
        if m is None:
            found.append(Flag("line_unassigned", CHECK, line=i))
            continue
        po = by_no[m]
        price = number(line.get("unit_price"))
        if price is not None and po.unit_price is not None and price != po.unit_price:
            found.append(Flag("price_differs", CHECK, line=i, field="unit_price"))
        unit = line.get("unit")
        if unit and po.unit and item_key(unit) != item_key(po.unit):
            found.append(Flag("unit_differs", CHECK, line=i, field="unit"))  # never converted (DEC-24)
        promised = _date(line.get("promised_date"))
        if doc_type == "order_acknowledgement" and promised and po.promised_date and promised != po.promised_date:
            found.append(Flag("date_changed", CHECK, line=i, field="promised_date"))
        qty = number(line.get("qty"))
        if qty is not None:
            first, so_far = totals.get(m, (i, Decimal(0)))
            totals[m] = (first, so_far + qty)
    for m, (first, qty) in totals.items():
        still_open = by_no[m].still_open
        if qty > still_open:
            found.append(Flag(OVER_ORDER[doc_type], CHECK, line=first, field="qty"))
        elif qty < still_open:
            level = CHECK if doc_type == "order_acknowledgement" else NOTE  # confirmed short (DEC-36), else a partial
            found.append(Flag("qty_below_order", level, line=first, field="qty"))
    covered = {m for m in matched if m is not None}
    open_lines = {po.line_no for po in po_lines if po.still_open > 0 and not po.closed}
    if covered and open_lines - covered:
        found.append(Flag("lines_not_covered", NOTE))
    return found


def _duplicate(answer: Mapping[str, Any], context: Context) -> bool:
    if context.earlier and fingerprint(answer, context.po_lines) in context.earlier:
        return True
    invoice = norm_id(answer.get("invoice_number"))
    recorded = {norm_id(n) for n in context.recorded_invoice_numbers}
    if invoice and invoice in recorded:
        return True
    tracking = {re.sub(r"\s+", "", str(t)).upper() for t in answer.get("tracking_numbers") or []}
    return bool(tracking & {re.sub(r"\s+", "", t).upper() for t in context.recorded_tracking_numbers})


def _implausible_dates(answer: Mapping[str, Any], context: Context) -> list[Flag]:
    latest = context.today + TOO_FAR_AHEAD
    found: list[Flag] = []

    def check(value: Any, field: str, line: int | None = None) -> None:
        when = _date(value)
        if when and (when > latest or (context.po_date is not None and when < context.po_date)):
            found.append(Flag("date_implausible", CHECK, line=line, field=field))

    for field in ("invoice_date", "ship_date", "arrival_date"):
        check(answer.get(field), field)
    for i, line in enumerate(_lines(answer)):
        for field in ("promised_date", "arrival_date", "requested_date"):
            check(line.get(field), field, i)
    return found


def _totals_disagree(answer: Mapping[str, Any]) -> bool:
    """Goods (quantity times price) plus charges against the printed total (DEC-48). Not worked out when a line has no
    quantity or price: that line is already plain to see."""
    total = number(answer.get("total"))
    if total is None:
        return False
    goods = Decimal(0)
    for line in _lines(answer):
        qty, price = number(line.get("qty")), number(line.get("unit_price"))
        if qty is None or price is None:
            return False
        goods += qty * price
    charges = sum((number(c.get("amount")) or Decimal(0) for c in _charges(answer)), Decimal(0))
    return abs(goods + charges - total) >= CENT


def validate(answer: Mapping[str, Any], text: str, context: Context) -> list[Flag]:
    """Every flag for one document, in a fixed order. `text` is the document's text as the text step gave it."""
    doc_type = str(answer.get("doc_type"))
    flags: list[Flag] = []
    if context.sender_is_vendor is False:
        flags.append(Flag("sender_not_vendor", CHECK))
    flags += _not_in_source(answer, SourceText(text, ocr=context.ocr))
    if has_planted_instruction(text, ocr=context.ocr):
        flags.append(Flag("planted_instruction", CHECK))
    if doc_type == "purchase_order" and context.po_exists:
        flags.append(Flag("po_already_exists", CHECK))
    if doc_type in VENDOR_DOCS and _duplicate(answer, context):
        flags.append(Flag("duplicate", CHECK))
    if doc_type in VENDOR_DOCS and context.po_lines is not None:
        flags += _against_po(answer, doc_type, context.po_lines)
    if doc_type == "order_acknowledgement":
        flags += [Flag("no_promised_date", CHECK, line=i, field="promised_date")
                  for i, line in enumerate(_lines(answer)) if line.get("promised_date") is None]  # fmt: skip
    flags += _implausible_dates(answer, context)
    if _totals_disagree(answer):
        flags.append(Flag("totals_disagree", CHECK, field="total"))
    if context.read_in_part:
        flags.append(Flag("read_in_part", CHECK))
    if _charges(answer):
        flags.append(Flag("charge_present", NOTE))
    return flags
