"""Creating purchase orders by hand or from a CSV file (REQ-PO-01, REQ-PO-02, DEC-24).

Nothing here guesses. An unknown vendor, an existing PO number or a malformed value is refused with a message that names
the row and the field, and a CSV file is imported whole or not at all.
"""

from __future__ import annotations

import csv
import io
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from po_dashboard.auth.service import audit
from po_dashboard.db.ledger import POLine, PurchaseOrder, Vendor
from po_dashboard.db.models import AppUser
from po_dashboard.db.types import Money, Quantity
from po_dashboard.ledger.errors import LedgerError, number, text, when
from po_dashboard.ledger.vendors import vendor_for_name

CURRENCY = re.compile(r"[A-Z]{3}")
MAX_CSV_BYTES = 2 * 1024 * 1024
MAX_CSV_ROWS = 5000
MAX_ERRORS = 40
COMMON_CURRENCIES = ("USD", "EUR", "GBP", "CAD", "MXN", "JPY", "CNY", "AUD", "CHF", "INR")

REQUIRED = ("po_number", "vendor", "currency", "line_no", "item", "quantity", "unit", "unit_price")
OPTIONAL = ("date_sent", "buyer", "description", "requested_date")
COLUMNS = REQUIRED + OPTIONAL
PO_LEVEL = ("vendor", "currency", "date_sent", "buyer")
TEMPLATE = (
    ",".join(COLUMNS) + "\n"
    "PO-1001,Example Vendor,USD,1,Hex bolt M8,10,EA,2.50,2026-11-04,buyer@example.com,Zinc plated,2026-11-30\n"
    "PO-1001,Example Vendor,USD,2,Washer M8,100,EA,0.04,2026-11-04,buyer@example.com,,2026-11-30\n"
)


@dataclass(frozen=True)
class LineInput:
    item: str
    quantity: Decimal
    unit: str
    unit_price: Decimal
    description: str | None = None
    requested_date: date | None = None
    line_no: int | None = None  # filled in 1, 2, 3 ... when left out


@dataclass(frozen=True)
class RowError:
    row: int | None  # the spreadsheet row, counting the header as row 1; None for a problem with the whole file
    message: str


class ImportRejected(ValueError):
    def __init__(self, errors: list[RowError], *, truncated: bool = False) -> None:
        super().__init__(f"{len(errors)} problems")
        self.errors = errors
        self.truncated = truncated


@dataclass
class ImportResult:
    po_numbers: list[str] = field(default_factory=list)


def clean_currency(value: str | None) -> str:
    code = (value or "").strip().upper()
    if not CURRENCY.fullmatch(code):
        raise LedgerError("Currency is needed, as a three-letter code like USD or EUR.")
    return code


def parse_line(
    label: str, *, item: str | None, quantity: str | None, unit: str | None, unit_price: str | None,
    description: str | None = None, requested_date: str | None = None, line_no: int | None = None,
) -> LineInput:  # fmt: skip
    """Check one line. Errors are prefixed with the label, such as 'Line 2'."""
    try:
        return LineInput(
            item=text(item, "Item", longest=200, required=True) or "",
            quantity=number(quantity, "Quantity", Quantity, positive=True),
            unit=text(unit, "Unit", longest=16, required=True) or "",
            unit_price=number(unit_price, "Unit price", Money, positive=False),
            description=text(description, "Description", longest=2000, multiline=True),
            requested_date=when(requested_date, "Requested date"),
            line_no=line_no,
        )
    except LedgerError as problem:
        raise LedgerError(f"{label}: {problem}") from None


def _number_taken(db: Session, po_number: str) -> bool:
    return (
        db.scalar(select(PurchaseOrder.id).where(func.lower(PurchaseOrder.po_number) == po_number.lower())) is not None
    )


@dataclass(frozen=True)
class Header:
    po_number: str
    vendor: Vendor
    currency: str
    date_sent: date | None
    buyer: str | None


def check_header(
    db: Session, *, po_number: str | None, vendor_id: int | None, currency: str | None, date_sent: str | None,
    buyer: str | None,
) -> Header:  # fmt: skip
    """Check everything about a PO except its lines. Raises LedgerError at the first problem."""
    number_ = text(po_number, "PO number", longest=64, required=True) or ""
    vendor = db.get(Vendor, vendor_id) if vendor_id else None
    if vendor is None:
        raise LedgerError("Choose a vendor. Add the vendor first if it is not in the list.")
    code = clean_currency(currency)
    sent = when(date_sent, "Date sent")
    who = text(buyer, "Buyer", longest=254)
    if _number_taken(db, number_):
        raise LedgerError(f"PO number {number_} already exists. Nothing is overwritten.")
    return Header(number_, vendor, code, sent, who)


def create_po(
    db: Session, *, po_number: str | None, vendor_id: int | None, currency: str | None, date_sent: str | None,
    buyer: str | None, lines: Sequence[LineInput], source: str = "manual", actor: AppUser | None = None,
    client_address: str | None = None,
) -> PurchaseOrder:  # fmt: skip
    """Add a PO and its lines. The caller commits."""
    header = check_header(
        db, po_number=po_number, vendor_id=vendor_id, currency=currency, date_sent=date_sent, buyer=buyer
    )
    if not lines:
        raise LedgerError("A PO needs at least one line.")
    order = PurchaseOrder(
        po_number=header.po_number, vendor_id=header.vendor.id, currency=header.currency, date_sent=header.date_sent,
        buyer=header.buyer, source=source, created_by=actor.id if actor else None,
    )  # fmt: skip
    db.add(order)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise LedgerError(f"PO number {header.po_number} already exists. Nothing is overwritten.") from None
    for position, entry in enumerate(lines, start=1):
        db.add(
            POLine(
                po_id=order.id, line_no=entry.line_no or position, item=entry.item, description=entry.description,
                ordered_qty=entry.quantity, unit=entry.unit, unit_price=entry.unit_price,
                requested_date=entry.requested_date,
            )  # fmt: skip
        )
    db.flush()
    audit(
        db, "po.create", actor, f"po:{order.id}", client_address,
        after={
            "po_number": header.po_number, "vendor": header.vendor.name, "currency": header.currency,
            "lines": len(lines), "source": source,
        },
    )  # fmt: skip
    return order


# ---- CSV import


def _read(raw: bytes) -> tuple[list[tuple[int, dict[str, str]]], list[RowError]]:
    if len(raw) > MAX_CSV_BYTES:
        return [], [RowError(None, f"The file is larger than {MAX_CSV_BYTES // (1024 * 1024)} MB.")]
    try:
        content = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return [], [RowError(None, "The file is not UTF-8 text. In your spreadsheet, save it as CSV UTF-8.")]
    reader = csv.reader(io.StringIO(content))
    try:
        header = [cell.strip().lower() for cell in next(reader, [])]
    except csv.Error as problem:
        return [], [RowError(None, f"The file could not be read as CSV: {problem}")]
    if not any(header):
        return [], [RowError(None, "The file is empty. The first row must name the columns.")]
    errors: list[RowError] = []
    repeated = sorted({name for name in header if name and header.count(name) > 1})
    unknown = [name for name in header if name and name not in COLUMNS]
    missing = [name for name in REQUIRED if name not in header]
    if repeated:
        errors.append(RowError(1, f"A column appears twice: {', '.join(repeated)}."))
    if unknown:
        errors.append(RowError(1, f"Unknown column: {', '.join(unknown)}. The columns are: {', '.join(COLUMNS)}."))
    if missing:
        errors.append(RowError(1, f"Missing column: {', '.join(missing)}."))
    if errors:
        return [], errors
    rows: list[tuple[int, dict[str, str]]] = []
    try:
        for index, cells in enumerate(reader, start=2):
            if index > MAX_CSV_ROWS + 1:
                return [], [RowError(None, f"More than {MAX_CSV_ROWS} rows. Split the file.")]
            if not any(cell.strip() for cell in cells):
                continue
            if len(cells) > len(header):
                errors.append(RowError(index, "This row has more cells than the header has columns."))
                continue
            record = {column: "" for column in COLUMNS}  # a column left out of the file reads as blank
            record.update(zip(header, cells, strict=False))
            rows.append((index, record))
    except csv.Error as problem:
        return [], [RowError(None, f"The file could not be read as CSV: {problem}")]
    if not rows and not errors:
        errors.append(RowError(None, "The file has a header but no rows."))
    return rows, errors


def import_csv(
    db: Session, raw: bytes, *, actor: AppUser | None = None, client_address: str | None = None
) -> ImportResult:
    """Import every PO in the file, or none. Raises ImportRejected listing what is wrong, by row. The caller commits."""
    rows, errors = _read(raw)
    if errors:
        raise ImportRejected(errors)

    groups: dict[str, list[tuple[int, dict[str, str]]]] = {}
    seen_lower: dict[str, str] = {}
    for index, record in rows:
        number_ = record["po_number"].strip()
        if not number_:
            errors.append(RowError(index, "PO number is needed."))
            continue
        key = seen_lower.setdefault(number_.lower(), number_)
        groups.setdefault(key, []).append((index, record))

    vendor_cache: dict[str, int | None] = {}
    plans: list[tuple[str, int, str, str | None, str | None, list[LineInput]]] = []
    for number_, entries in groups.items():
        first_row, first = entries[0]
        for index, record in entries[1:]:
            for column in PO_LEVEL:
                if record[column].strip().lower() != first[column].strip().lower():
                    errors.append(RowError(index, f"{column} differs from row {first_row}. A PO has one {column}."))
        vendor_name = first["vendor"].strip()
        if vendor_name.lower() not in vendor_cache:
            found = vendor_for_name(db, vendor_name) if vendor_name else None
            vendor_cache[vendor_name.lower()] = found.id if found else None
        vendor_id = vendor_cache[vendor_name.lower()]
        if vendor_id is None:
            shown = vendor_name[:60] or "(blank)"
            errors.append(
                RowError(first_row, f"Vendor '{shown}' is not in the vendor list. Add it first; nothing is guessed.")
            )
        if _number_taken(db, number_):
            errors.append(RowError(first_row, f"PO number {number_} already exists. Nothing is overwritten."))
        try:
            clean_currency(first["currency"])
            when(first["date_sent"], "date_sent")
            text(first["buyer"], "buyer", longest=254)
            text(number_, "po_number", longest=64, required=True)
        except LedgerError as problem:
            errors.append(RowError(first_row, str(problem)))
        lines: list[LineInput] = []
        used: set[int] = set()
        for index, record in entries:
            try:
                raw_no = record["line_no"].strip()
                if not raw_no.isascii() or not raw_no.isdigit() or not 1 <= int(raw_no) <= 100000:
                    raise LedgerError("line_no must be a whole number like 1, 2, 3.")
                if int(raw_no) in used:
                    raise LedgerError(f"line_no {raw_no} is used twice in PO {number_}.")
                used.add(int(raw_no))
                lines.append(
                    parse_line(
                        "This row",
                        item=record["item"],
                        quantity=record["quantity"],
                        unit=record["unit"],
                        unit_price=record["unit_price"],
                        description=record["description"],
                        requested_date=record["requested_date"],
                        line_no=int(raw_no),
                    )  # fmt: skip
                )
            except LedgerError as problem:
                errors.append(RowError(index, str(problem).replace("This row: ", "")))
        if vendor_id is not None:
            plans.append((number_, vendor_id, first["currency"], first["date_sent"], first["buyer"], lines))

    if errors:
        errors.sort(key=lambda e: (e.row is None, e.row or 0))
        raise ImportRejected(errors[:MAX_ERRORS], truncated=len(errors) > MAX_ERRORS)

    result = ImportResult()
    for number_, vendor_id, currency, sent, buyer, lines in plans:
        create_po(
            db, po_number=number_, vendor_id=vendor_id, currency=currency, date_sent=sent, buyer=buyer, lines=lines,
            source="csv", actor=actor, client_address=client_address,
        )  # fmt: skip
        result.po_numbers.append(number_)
    audit(
        db,
        "po.import",
        actor,
        None,
        client_address,
        after={"purchase_orders": len(result.po_numbers), "lines": len(rows)},
    )
    return result
