"""The QuickBooks starting load (DEC-46, PD-1.10a): open POs from QuickBooks' "Open POs" report, as exported.

The report is grouped by item: a heading row names the item, then its PO lines, then a "Total" row. A row with a PO
number (`Num`) is a line, unless its item is a charge (Shipping and Freight, and more names in the `po_charge_items`
setting: DEC-55), which would never close and is listed as skipped; every other row is skipped. Columns are found by
name. Reading never changes anything; `plan_load` says what would happen and `apply_load` does it, all or nothing.

What is not read, on purpose: `Other 1` (lead times typed by hand), `Print`, `Deliv Date`, `Due Date`, `P. O. #`,
`S. O. #`: dates and sending come from emails only (DEC-41, DEC-43).
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from sqlalchemy import func, select
from sqlalchemy import text as sql
from sqlalchemy.orm import Session

from po_dashboard.auth.service import audit
from po_dashboard.db.ledger import POLine, PurchaseOrder
from po_dashboard.db.models import AppUser
from po_dashboard.db.types import Quantity
from po_dashboard.display import day
from po_dashboard.ledger import events
from po_dashboard.ledger import orders as rules
from po_dashboard.ledger.errors import LedgerError, number, text, when
from po_dashboard.ledger.orders import RowError
from po_dashboard.ledger.sheets import SheetRejected, read_table
from po_dashboard.ledger.vendors import vendor_for_name

REQUIRED = ("date", "num", "source name", "item", "qty", "rcv'd", "amount")
OPTIONAL = ("memo", "name")
CURRENCY = "USD"  # the report has no currency; the owner's default (DEC-41)
UNIT = "EA"  # the report has no unit; the owner's default (DEC-46)
SIX_PLACES = Decimal("0.000001")
FOUR_PLACES = Decimal("0.0001")
NAME_DATE = re.compile(r"(?<!\d)(\d{2})(\d{2})(\d{2})(?!\d)")  # "Open POs 110526 Project": MMDDYY
NUMBER = re.compile(r"-?[\d,]*\.?\d+")
MAX_SHOWN = 200


class QuickBooksRejected(ValueError):
    """The file cannot be used at all. The message says why; own_format means it belongs on Import CSV or Excel."""

    def __init__(self, message: str, *, own_format: bool = False) -> None:
        super().__init__(message)
        self.own_format = own_format


def is_report(raw: bytes, filename: str | None) -> bool:
    """Whether the file is the QuickBooks Open POs report rather than the app's own import format, so a report
    uploaded on Import CSV or Excel can be shown as the QuickBooks load instead of being refused."""
    try:
        table = read_table(raw, filename)
    except SheetRejected:
        return False
    header = {cell.strip().lower() for cell in (table[0] if table else [])}
    return header.issuperset(REQUIRED) and not header.issuperset(rules.REQUIRED)


@dataclass
class QBLine:
    row: int
    line_no: int
    item: str
    memo: str | None
    customer: str | None
    qty: Decimal
    received: Decimal
    amount: Decimal
    price: Decimal
    rounded: bool


CHARGE_SETTING = "po_charge_items"
DEFAULT_CHARGES = "shipping,freight"  # rows that are charges, not goods (DEC-55)


def charge_names(db: Session) -> frozenset[str]:
    """The item names that are charges: the shared `setting` table's row if there is one, else the default. Read with
    plain SQL so purchasing code stays apart from the customer side (DEC-56)."""
    value = db.execute(sql("select value from setting where key = :key"), {"key": CHARGE_SETTING}).scalar()
    listed = value if isinstance(value, str) else DEFAULT_CHARGES
    return frozenset(part.strip().lower() for part in listed.split(",") if part.strip())


@dataclass(frozen=True)
class SkippedCharge:
    row: int
    po_number: str
    item: str


@dataclass
class QBOrder:
    po_number: str
    vendor_name: str
    vendor_id: int | None
    po_date: date
    lines: list[QBLine] = field(default_factory=list)


@dataclass
class LoadPlan:
    export_date: date | None
    create: list[QBOrder] = field(default_factory=list)
    waiting: list[QBOrder] = field(default_factory=list)  # the vendor is not in the app's vendor list yet
    existing: list[str] = field(default_factory=list)  # PO numbers already in the app: left alone
    problems: list[RowError] = field(default_factory=list)
    ignored_columns: list[str] = field(default_factory=list)
    skipped: list[SkippedCharge] = field(default_factory=list)  # charge rows: not goods, never a PO line (DEC-55)

    @property
    def lines_to_create(self) -> int:
        return sum(len(order.lines) for order in self.create)

    @property
    def rounded(self) -> list[tuple[QBOrder, QBLine]]:
        return [(order, line) for order in self.create for line in order.lines if line.rounded]

    @property
    def received(self) -> list[tuple[QBOrder, QBLine]]:
        return [(order, line) for order in self.create for line in order.lines if line.received > 0]

    @property
    def waiting_vendors(self) -> list[str]:
        return sorted({order.vendor_name for order in self.waiting}, key=str.lower)

    @property
    def ready(self) -> bool:
        return not self.problems and self.export_date is not None and bool(self.create)


def is_charge(item: str, charges: frozenset[str]) -> bool:
    """Whether a report row's item is a charge: its whole name, or the part before the first colon (a QuickBooks
    sub-item such as "Freight:Inbound"), is in the list, ignoring case."""
    name = item.strip().lower()
    return name in charges or name.split(":", 1)[0].strip() in charges


def export_date_from_name(filename: str | None) -> date | None:
    """The date in a name like "Open POs 110526 Project.CSV" (MMDDYY), or None."""
    for match in NAME_DATE.finditer(filename or ""):
        month, dom, year = (int(part) for part in match.groups())
        try:
            return date(2000 + year, month, dom)
        except ValueError:
            continue
    return None


def _amount(raw: str, field_name: str, *, positive: bool) -> Decimal:
    """A number as QuickBooks writes it: digits, maybe thousands commas, maybe a dollar sign."""
    cleaned = raw.strip().replace("$", "").replace(" ", "")
    if not cleaned or not NUMBER.fullmatch(cleaned):
        raise LedgerError(f"{field_name}: '{raw.strip()[:30]}' is not a number.")
    try:
        value = Decimal(cleaned.replace(",", ""))
    except InvalidOperation:
        raise LedgerError(f"{field_name}: '{raw.strip()[:30]}' is not a number.") from None
    if positive and value <= 0:
        raise LedgerError(f"{field_name} must be more than zero.")
    if value < 0:
        raise LedgerError(f"{field_name} cannot be negative.")
    return value


def unit_price(amount: Decimal, qty: Decimal) -> tuple[Decimal, bool]:
    """Amount ÷ Qty, exact when it fits six places; otherwise rounded to four places (the owner's choice, DEC-46)."""
    exact = amount / qty
    if exact == exact.quantize(SIX_PLACES):
        return exact.quantize(SIX_PLACES).normalize(), False
    return exact.quantize(FOUR_PLACES, rounding=ROUND_HALF_UP), True


def plan_load(db: Session, raw: bytes, filename: str | None, export_date: date | None = None) -> LoadPlan:
    """What the starting load would do with this file. Changes nothing. Raises QuickBooksRejected when the file is
    not a QuickBooks "Open POs" report at all."""
    try:
        table = read_table(raw, filename)
    except SheetRejected as problem:
        raise QuickBooksRejected(str(problem)) from None
    header = [cell.strip().lower() for cell in (table[0] if table else [])]
    missing = [name for name in REQUIRED if name not in header]
    if missing and set(rules.REQUIRED).issubset(header):
        raise QuickBooksRejected(
            "This is a purchase order file in the app's own import format, not the QuickBooks report. "
            "Nothing was read. Import it on Import CSV or Excel.",
            own_format=True,
        )
    if missing:
        raise QuickBooksRejected(
            "This does not look like the QuickBooks Open POs report: it has no "
            + ", ".join(f"'{name}'" for name in missing)
            + " column. Export the report from QuickBooks as it is and upload that file."
        )
    where = {name: header.index(name) for name in REQUIRED + OPTIONAL if name in header}
    known = set(REQUIRED + OPTIONAL)
    plan = LoadPlan(
        export_date=export_date or export_date_from_name(filename),
        # Named as QuickBooks spells them, for the person reading the preview.
        ignored_columns=list(
            dict.fromkeys(cell.strip() for cell in table[0] if cell.strip() and cell.strip().lower() not in known)
        ),
    )

    def cell(cells: list[str], name: str) -> str:
        index = where.get(name)
        return cells[index].strip() if index is not None and index < len(cells) else ""

    charges = charge_names(db)
    found: dict[str, QBOrder] = {}
    for row_number, cells in enumerate(table[1:], start=2):
        number_ = cell(cells, "num")
        if not number_:
            continue  # an item heading, a "Total" row or a blank row
        if is_charge(cell(cells, "item"), charges):
            plan.skipped.append(SkippedCharge(row_number, number_[:64], cell(cells, "item")[:200]))
            continue
        try:
            line = _line(cells, cell, row_number)
            vendor_name = text(cell(cells, "source name"), "Source Name (the vendor)", longest=200, required=True) or ""
            po_date = when(cell(cells, "date"), "Date")
            if po_date is None:
                raise LedgerError("Date is empty.")
            number_ = text(number_, "Num (the PO number)", longest=64, required=True) or ""
        except LedgerError as problem:
            plan.problems.append(RowError(row_number, f"PO {number_[:20]}: {problem}"))
            continue
        name = cell(cells, "name")
        line.customer = name[:200] if name and name.lower() != vendor_name.lower() else None
        order = found.get(number_)
        if order is None:
            order = found[number_] = QBOrder(number_, vendor_name, None, po_date)
        elif order.vendor_name.lower() != vendor_name.lower() or order.po_date != po_date:
            plan.problems.append(RowError(row_number, f"PO {number_}: its rows disagree on the vendor or the date."))
            continue
        line.line_no = len(order.lines) + 1  # the report has no line numbers: the order the file lists them in
        order.lines.append(line)

    taken = {
        number_.lower()
        for number_ in db.scalars(
            select(PurchaseOrder.po_number).where(func.lower(PurchaseOrder.po_number).in_([n.lower() for n in found]))
        )
    }
    for number_, order in found.items():
        if number_.lower() in taken:
            plan.existing.append(number_)
            continue
        vendor = vendor_for_name(db, order.vendor_name)
        if vendor is None:
            plan.waiting.append(order)
        else:
            order.vendor_id = vendor.id
            plan.create.append(order)
    if not found and not plan.problems and not plan.skipped:
        plan.problems.append(RowError(None, "The file has no PO lines (no row has a Num)."))
    return plan


def _line(cells: list[str], cell: Callable[[list[str], str], str], row_number: int) -> QBLine:
    item = text(cell(cells, "item"), "Item", longest=200, required=True) or ""
    memo = text(cell(cells, "memo"), "Memo", longest=2000, multiline=True)
    # Four places at most on a quantity, as for a typed one: more is refused, never rounded.
    qty = number(str(_amount(cell(cells, "qty"), "Qty", positive=True)), "Qty", Quantity, positive=True)
    received = _amount(cell(cells, "rcv'd") or "0", "Rcv'd", positive=False)
    amount = _amount(cell(cells, "amount"), "Amount", positive=False)
    if received > qty:
        raise LedgerError(f"Rcv'd ({received}) is more than Qty ({qty}).")
    price, rounded = unit_price(amount, qty)
    return QBLine(row_number, 0, item, memo, None, qty, received, amount, price, rounded)


@dataclass(frozen=True)
class LoadResult:
    created: list[str]
    lines: int
    waiting: int
    received_lines: int
    rounded_lines: int


def apply_load(
    db: Session, plan: LoadPlan, *, actor: AppUser | None = None, client_address: str | None = None
) -> LoadResult:
    """Create every PO in the plan, all or nothing. The caller commits; on any error it rolls back."""
    if plan.problems:
        raise LedgerError("The file has problems; nothing was created.")
    if plan.export_date is None:
        raise LedgerError("Give the date the report was exported, so the history can say which export it was.")
    exported = plan.export_date
    source_ref = f"{events.QUICKBOOKS_REF}{exported.isoformat()}"
    received_lines = rounded_lines = 0
    for order in plan.create:
        lines = [
            rules.LineInput(item=line.item, quantity=line.qty, unit=UNIT, unit_price=line.price, description=line.memo,
                            line_no=line.line_no, customer=line.customer)
            for line in order.lines
        ]  # fmt: skip
        created = rules.create_po(
            db, po_number=order.po_number, vendor_id=order.vendor_id, currency=CURRENCY, date_sent=None, buyer=None,
            lines=lines, source="quickbooks", actor=actor, client_address=client_address, po_date=order.po_date,
        )  # fmt: skip
        ids = {
            line_no: line_id
            for line_no, line_id in db.execute(select(POLine.line_no, POLine.id).where(POLine.po_id == created.id))
        }
        for line in order.lines:
            if line.rounded:
                rounded_lines += 1
                note = (
                    f"Unit price {line.price} worked out from the QuickBooks total {line.amount} ÷ {line.qty}, "
                    f"rounded (export of {day(exported)})"
                )
                audit(db, "line.price_rounded", actor, f"po_line:{ids[line.line_no]}", client_address,
                      after={"note": note, "total": str(line.amount), "qty": str(line.qty)})  # fmt: skip
        got = [
            events.ShipLineInput(ids[line.line_no], line.received) for line in order.lines if line.received > 0
        ]
        if got:
            received_lines += len(got)
            events.record_shipment(db, created, got, actor=actor, client_address=client_address, source_ref=source_ref)
    audit(
        db, "quickbooks.load", actor, None, client_address,
        after={
            "export": exported.isoformat(), "created": len(plan.create), "lines": plan.lines_to_create,
            "waiting_for_vendor": len(plan.waiting), "already_in_app": len(plan.existing),
            "charge_rows_skipped": len(plan.skipped),
        },
    )  # fmt: skip
    return LoadResult([order.po_number for order in plan.create], plan.lines_to_create, len(plan.waiting),
                      received_lines, rounded_lines)  # fmt: skip
