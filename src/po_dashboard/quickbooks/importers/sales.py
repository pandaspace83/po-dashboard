"""Reading a QuickBooks Desktop sales export into plain records (DEC-58).

The input is the table the upload reader produced (any CSV or Excel file) and a mapping of which column holds what. The
output is normalised invoice records and, for every row of the file, what became of it. Nothing is invented: a value the
file does not give stays empty, and anything that cannot be read with certainty is said so and, where it touches an
invoice, marks that invoice for review instead of being quietly dropped.

What this knows about QuickBooks reports, all of it checked by tests on made-up files: column names may sit below a few
title rows; a customer's name may appear on a heading row above its rows; "Total ..." rows end a group; a transaction
type column tells an invoice from a credit memo and from things that are not sales documents (payments, deposits)."""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation

from po_dashboard.customers.keys import name_key
from po_dashboard.db.types import Money, Quantity
from po_dashboard.ledger.errors import LedgerError, when
from po_dashboard.quickbooks.importers.mapping import LABELS, REQUIRED, Mapping, header_index, norm

MAX_CELL = 500  # a cell kept in the stored row is cut to this
MAX_CELLS = 60
CURRENCY = re.compile(r"[A-Za-z]{3}")
FREIGHT_WORDS = ("freight", "shipping", "handling", "fuel")


class ImportProblem(ValueError):
    """The file cannot be read as a sales export. The message says why, in words for the person."""


@dataclass(frozen=True)
class LineRecord:
    position: int
    item: str | None
    description: str | None
    qty: Decimal | None
    unit_price: Decimal | None
    amount: Decimal
    kind: str  # goods, freight, tax, discount, other


@dataclass(frozen=True)
class InvoiceRecord:
    kind: str  # invoice or credit_memo
    number: str
    customer_name: str | None
    date: date
    currency: str
    po_reference: str | None
    qb_reference: str | None
    level: str  # line or invoice
    total: Decimal
    lines: tuple[LineRecord, ...]
    first_row: int
    review: tuple[str, ...] = ()  # why a person must look before this is trusted
    notes: tuple[str, ...] = ()  # worth knowing, not a reason to doubt it (an exact repeat in the file)

    @property
    def key(self) -> str:
        return self.number.strip().lower()


@dataclass
class ParsedInvoice:
    primary: InvoiceRecord
    others: list[InvoiceRecord] = field(default_factory=list)  # different invoices that share the number: a conflict


@dataclass
class RowRecord:
    row_number: int
    cells: list[str]
    outcome: str  # line, heading, total, unusable
    reason: str | None = None


@dataclass
class Parsed:
    mapping: Mapping
    header_row: int  # the spreadsheet row that holds the column names, counting from 1
    invoices: list[ParsedInvoice]
    rows: list[RowRecord]

    def count(self, outcome: str) -> int:
        return sum(1 for row in self.rows if row.outcome == outcome)

    @property
    def period(self) -> tuple[date | None, date | None]:
        dates = [group.primary.date for group in self.invoices]
        return (min(dates), max(dates)) if dates else (None, None)

    def unreadable_reasons(self) -> Counter[str]:
        return Counter(row.reason or "" for row in self.rows if row.outcome == "unusable")


# ---- numbers


def read_money(value: str, label: str) -> Decimal:
    """An amount as QuickBooks writes it: a currency sign, thousands commas, a minus sign or brackets for a negative."""
    return _number(value, label, Money)


def read_quantity(value: str, label: str) -> Decimal:
    return _number(value, label, Quantity)


def _number(value: str, label: str, kind: type[Money] | type[Quantity]) -> Decimal:
    raw = value.strip().replace("−", "-")
    negative = raw.startswith("(") and raw.endswith(")")
    cleaned = raw.strip("()").replace(",", "").replace("$", "").replace("€", "").replace("£", "").strip()
    if not cleaned:
        raise LedgerError(f"{label} is empty.")
    if negative:
        cleaned = "-" + cleaned.lstrip("-")
    try:
        number = Decimal(cleaned)
        kind().to_stored(number)
    except (InvalidOperation, ValueError, TypeError):
        raise LedgerError(f"{label} is not a number this import can keep exactly: {value.strip()[:30]!r}.") from None
    return number


def line_kind(item: str | None, qty: Decimal | None, charge_names: frozenset[str]) -> str:
    """Goods, or one of the things that are not goods (DEC-58): by the item's name, from the settings list."""
    name = (item or "").strip().lower()
    if name in charge_names:
        if "tax" in name:
            return "tax"
        if "discount" in name:
            return "discount"
        if any(word in name for word in FREIGHT_WORDS):
            return "freight"
        return "other"
    if not name and qty is None:
        return "other"  # a line with no item and no quantity is not goods
    return "goods"


# ---- one row


@dataclass(frozen=True)
class _Row:
    number_row: int
    cells: tuple[str, ...]
    kind: str
    number: str
    customer: str | None
    date: date
    currency: str
    po_reference: str | None
    qb_reference: str | None
    item: str | None
    description: str | None
    qty: Decimal | None
    unit_price: Decimal | None
    amount: Decimal

    @property
    def key(self) -> tuple[str, str]:
        return self.kind, self.number.strip().lower()


def _text(cells: list[str], at: dict[str, int], name: str) -> str | None:
    position = at.get(name)
    if position is None or position >= len(cells):
        return None
    value = " ".join(cells[position].split())
    return value or None


def _classify(type_text: str | None) -> str | None:
    """The kind of transaction, or None for one that is not a sales document (a payment, a deposit)."""
    kind = (type_text or "").strip().lower()
    if kind in ("", "invoice"):
        return "invoice"
    if "credit" in kind:
        return "credit_memo"
    return None


def read(table: list[list[str]], mapping: Mapping, *, default_currency: str, charge_names: frozenset[str]) -> Parsed:
    """Read every row after the column names. Raises ImportProblem if the file is not readable as a sales export."""
    head = header_index(table)
    if head is None:
        raise ImportProblem(
            "No row of the file names an invoice number, a date and an amount, so it does not look like a sales "
            "export. "
            "Export the report with its column names and try again."
        )
    headers = table[head]
    positions: dict[str, int] = {}
    for name, header in mapping.fields.items():
        wanted = norm(header)
        found = [i for i, cell in enumerate(headers) if norm(cell) == wanted]
        if not found:
            raise ImportProblem(
                f"The mapping reads {LABELS[name]} from a column called {header[:60]}, which the file lacks."
            )
        positions[name] = found[0]
    missing = [LABELS[name] for name in REQUIRED if name not in positions]
    if missing:
        raise ImportProblem(
            f"The column for {', '.join(missing)} is not set. Choose which column holds it (the file has: "
            f"{', '.join(h.strip() for h in headers if h.strip())})."
        )

    rows: list[RowRecord] = []
    good: list[_Row] = []
    bad_by_invoice: dict[tuple[str, str], list[str]] = {}
    current_customer: str | None = None
    for index, raw in enumerate(table, start=1):
        cells = [cell[:MAX_CELL] for cell in raw[:MAX_CELLS]]
        if index <= head + 1:
            if index == head + 1:
                rows.append(RowRecord(index, cells, "heading", "the column names"))
            elif any(cell.strip() for cell in raw):
                rows.append(RowRecord(index, cells, "heading", "above the column names"))
            continue
        if not any(cell.strip() for cell in raw):
            continue
        padded = [*raw, *([""] * (len(headers) - len(raw)))]
        number = _text(padded, positions, "number")
        filled = [cell.strip() for cell in padded if cell.strip()]
        if number is None:
            if filled[0].lower().startswith("total"):
                rows.append(RowRecord(index, cells, "total", "a total row"))
            elif len(filled) == 1:
                current_customer = filled[0]
                rows.append(
                    RowRecord(index, cells, "heading", "a heading row, read as the customer for the rows below")
                )
            else:
                rows.append(RowRecord(index, cells, "unusable", f"{LABELS['number']} is missing"))
            continue
        kind = _classify(_text(padded, positions, "type"))
        if kind is None:
            shown = (_text(padded, positions, "type") or "")[:30]
            rows.append(RowRecord(index, cells, "unusable", f"not a sales document this import reads: {shown}"))
            continue
        try:
            row_date = when(_text(padded, positions, "date"), LABELS["date"])
            if row_date is None:
                raise LedgerError(f"{LABELS['date']} is empty.")
            amount = read_money(_text(padded, positions, "amount") or "", LABELS["amount"])
            qty_text, price_text = _text(padded, positions, "qty"), _text(padded, positions, "unit_price")
            qty = read_quantity(qty_text, LABELS["qty"]) if qty_text else None
            price = read_money(price_text, LABELS["unit_price"]) if price_text else None
            who = _text(padded, positions, "customer") or current_customer
            if who is None:
                raise LedgerError(f"{LABELS['customer']} is missing (no column value and no heading row above).")
            code = _text(padded, positions, "currency")
            if code is not None and not CURRENCY.fullmatch(code):
                raise LedgerError(f"{LABELS['currency']} is not a three-letter code: {code[:10]!r}.")
        except LedgerError as problem:
            rows.append(RowRecord(index, cells, "unusable", str(problem)))
            bad_by_invoice.setdefault((kind, number.strip().lower()), []).append(f"row {index}: {problem}")
            continue
        good.append(_Row(
            index, tuple(c.strip() for c in padded), kind, number, who, row_date, (code or default_currency).upper(),
            _text(padded, positions, "po_reference"), _text(padded, positions, "qb_reference"),
            _text(padded, positions, "item"), _text(padded, positions, "description"), qty, price, amount,
        ))  # fmt: skip
        rows.append(RowRecord(index, cells, "line", None))
    invoices = _group(good, rows, mapping, charge_names, bad_by_invoice)
    return Parsed(mapping, head + 1, invoices, sorted(rows, key=lambda r: r.row_number))


# ---- rows into invoices


def _mark(rows: list[RowRecord], number: int, reason: str) -> None:
    for row in rows:
        if row.row_number == number:
            row.outcome, row.reason = "unusable", reason


def _signature(row: _Row, level: str) -> tuple[object, ...]:
    base = (name_key(row.customer or ""), row.date, row.currency)
    return (*base, row.amount) if level == "invoice" else base


def _distinct(values: list[str | None]) -> str | None:
    found = list(dict.fromkeys(v for v in values if v))
    return "; ".join(found)[:200] if found else None


def _group(
    good: list[_Row], rows: list[RowRecord], mapping: Mapping, charge_names: frozenset[str],
    bad: dict[tuple[str, str], list[str]],
) -> list[ParsedInvoice]:  # fmt: skip
    level = mapping.level
    groups: dict[tuple[str, str], list[_Row]] = {}
    for row in good:
        groups.setdefault(row.key, []).append(row)
    out: list[ParsedInvoice] = []
    for key, members in groups.items():
        notes: list[str] = []
        problems = list(bad.get(key, []))
        half = len(members) // 2
        if (
            level == "line"
            and len(members) % 2 == 0
            and all(members[i].cells == members[i + half].cells for i in range(half))
        ):  # the whole invoice listed twice: the same rows again, counted once
            for repeat in members[half:]:
                _mark(rows, repeat.number_row, "the whole invoice appears twice in this file: counted once")
            members, notes = members[:half], [*notes, "the whole invoice appears twice in the file"]
        elif level == "invoice":
            seen: dict[tuple[object, ...], int] = {}
            kept: list[_Row] = []
            for row in members:
                signature = _signature(row, level)
                if signature in seen:
                    _mark(rows, row.number_row, f"an exact repeat of row {seen[signature]}: counted once")
                    notes.append("a row appears more than once in the file")
                else:
                    seen[signature] = row.number_row
                    kept.append(row)
            members = kept
        variants: dict[tuple[object, ...], list[_Row]] = {}
        for row in members:
            variants.setdefault(_signature(row, level), []).append(row)
        records = [_record(rows_, level, charge_names, notes, problems) for rows_ in variants.values()]
        primary, *others = records
        if others:
            primary = _with(primary, review=(*primary.review, "two different invoices in this file share this number"))
        out.append(ParsedInvoice(primary, others))
    return out


def _with(record: InvoiceRecord, **change: object) -> InvoiceRecord:
    from dataclasses import replace

    return replace(record, **change)  # type: ignore[arg-type]


def _record(
    members: list[_Row], level: str, charge_names: frozenset[str], notes: list[str], problems: list[str]
) -> InvoiceRecord:  # fmt: skip
    first = members[0]
    review = [f"a line could not be read ({p})" for p in problems]
    lines: list[LineRecord] = []
    if level == "line":
        for position, row in enumerate(members, start=1):
            kind = line_kind(row.item, row.qty, charge_names)
            if kind == "other" and not row.item and row.qty is None and row.amount != 0:
                review.append(f"row {row.number_row} has an amount but no item or quantity")
            lines.append(LineRecord(position, row.item, row.description, row.qty, row.unit_price, row.amount, kind))
        total = sum((line.amount for line in lines), Decimal(0))
    else:
        total = first.amount
    return InvoiceRecord(
        kind=first.kind, number=first.number.strip(), customer_name=first.customer, date=first.date,
        currency=first.currency, po_reference=_distinct([m.po_reference for m in members]),
        qb_reference=_distinct([m.qb_reference for m in members]), level=level, total=total, lines=tuple(lines),
        first_row=first.number_row, review=tuple(review), notes=tuple(dict.fromkeys(notes)),
    )  # fmt: skip
