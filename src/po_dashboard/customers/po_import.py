"""Importing customer POs from CSV or Excel (REQ-CO-03, DEC-57): read, show a plan, then file.

The file is read with the upload reader every upload uses (size, zip and macro limits, DEC-47). `read` changes nothing
and says, for each PO, what filing it would do (created, skipped as a copy, a pending revision, or waiting for a
customer). `apply` files every PO through the intake service, or none: any problem in the file stops it, listed by row.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from sqlalchemy.orm import Session

from po_dashboard.auth.service import audit
from po_dashboard.customers import intake
from po_dashboard.customers.intake import LineData, PoData
from po_dashboard.customers.keys import name_key, po_key
from po_dashboard.db.models import AppUser
from po_dashboard.db.types import Money, Quantity
from po_dashboard.ledger.errors import LedgerError, number, text, when
from po_dashboard.ledger.orders import MAX_ERRORS, ImportRejected, RowError
from po_dashboard.ledger.sheets import SheetRejected, read_table

REQUIRED = ("customer", "po_number", "currency", "line_no", "item", "quantity", "unit_price")
OPTIONAL = ("po_date", "stated_total", "unit", "description", "line_amount")
COLUMNS = REQUIRED + OPTIONAL
PO_LEVEL = ("customer", "currency", "po_date", "stated_total")
TEMPLATE = (
    ",".join(COLUMNS) + "\n"
    "Example Customer,CP-1001,USD,1,Hex bolt M8,10,2.50,2026-11-04,26.50,EA,Zinc plated,25.00\n"
    "Example Customer,CP-1001,USD,2,Washer M8,100,0.015,2026-11-04,26.50,EA,,1.50\n"
)


@dataclass
class PlannedPO:
    row: int  # the first spreadsheet row of this PO
    data: PoData
    outcome: str  # created, skipped, revision, waiting
    customer: str | None = None  # the customer it would be filed under
    warnings: list[str] = field(default_factory=list)


@dataclass
class Plan:
    pos: list[PlannedPO] = field(default_factory=list)
    ignored_columns: list[str] = field(default_factory=list)
    rows: int = 0

    def count(self, outcome: str) -> int:
        return sum(1 for planned in self.pos if planned.outcome == outcome)


def _table(raw: bytes, filename: str | None) -> tuple[list[tuple[int, dict[str, str]]], list[RowError], list[str]]:
    try:
        table = read_table(raw, filename)
    except SheetRejected as problem:
        return [], [RowError(None, str(problem))], []
    header = [cell.strip().lower() for cell in (table[0] if table else [])]
    if not any(header):
        return [], [RowError(None, "The file is empty. The first row must name the columns.")], []
    errors: list[RowError] = []
    repeated = sorted({name for name in header if name in COLUMNS and header.count(name) > 1})
    ignored = list(dict.fromkeys(name for name in header if name and name not in COLUMNS))
    missing = [name for name in REQUIRED if name not in header]
    if repeated:
        errors.append(RowError(1, f"A column appears twice: {', '.join(repeated)}."))
    if missing:
        errors.append(
            RowError(1, f"Missing column: {', '.join(missing)}. The columns it reads are: {', '.join(COLUMNS)}.")
        )
    if errors:
        return [], errors, []
    rows: list[tuple[int, dict[str, str]]] = []
    for index, cells in enumerate(table[1:], start=2):
        if not any(cell.strip() for cell in cells):
            continue
        if len(cells) > len(header):
            errors.append(RowError(index, "This row has more cells than the header has columns."))
            continue
        record = {column: "" for column in COLUMNS}
        record.update(zip(header, cells, strict=False))
        rows.append((index, record))
    if not rows and not errors:
        errors.append(RowError(None, "The file has a header but no rows."))
    return rows, errors, ignored


def _decimal(value: str, label: str, kind: type[Quantity] | type[Money], *, positive: bool) -> Decimal:
    return number(value, label, kind, positive=positive)


def read(db: Session, raw: bytes, filename: str | None = None, *, source_ref: str | None = None) -> Plan:
    """The plan for a file. Raises ImportRejected, listing every problem by row, if any PO cannot be read."""
    rows, errors, ignored = _table(raw, filename)
    if errors:
        raise ImportRejected(errors)
    groups: dict[tuple[str, str], list[tuple[int, dict[str, str]]]] = {}
    for index, record in rows:
        who, number_ = record["customer"].strip(), record["po_number"].strip()
        if not who:
            errors.append(RowError(index, "Customer is needed."))
            continue
        if not number_ or not po_key(number_):
            errors.append(RowError(index, "PO number is needed, with at least one letter or digit."))
            continue
        groups.setdefault((name_key(who), po_key(number_)), []).append((index, record))

    plan = Plan(ignored_columns=ignored, rows=len(rows))
    for entries in groups.values():
        first_row, first = entries[0]
        for index, record in entries[1:]:
            for column in PO_LEVEL:
                if record[column].strip().lower() != first[column].strip().lower():
                    errors.append(RowError(index, f"{column} differs from row {first_row}. A PO has one {column}."))
        lines: list[LineData] = []
        bad = False
        for index, record in entries:
            try:
                raw_no = record["line_no"].strip()
                if not raw_no.isascii() or not raw_no.isdigit():
                    raise LedgerError("line_no must be a whole number like 1, 2, 3.")
                stated = record["line_amount"].strip()
                lines.append(LineData(
                    item=record["item"], qty=_decimal(record["quantity"], "Quantity", Quantity, positive=True),
                    unit_price=_decimal(record["unit_price"], "Unit price", Money, positive=False),
                    line_no=int(raw_no), unit=record["unit"] or None, description=record["description"] or None,
                    stated_amount=_decimal(stated, "line_amount", Money, positive=False) if stated else None,
                ))  # fmt: skip
            except LedgerError as problem:
                errors.append(RowError(index, str(problem)))
                bad = True
        try:
            sent: date | None = when(first["po_date"], "po_date")
            total = first["stated_total"].strip()
            data = PoData(
                po_number=first["po_number"].strip(), currency=first["currency"], lines=tuple(lines),
                customer_name=text(first["customer"], "Customer", longest=200), po_date=sent,
                stated_total=_decimal(total, "stated_total", Money, positive=False) if total else None,
                source="import", source_ref=source_ref,
            )  # fmt: skip
            if bad:
                continue
            data = intake.check(db, data)
            found = intake.classify(db, data)
        except LedgerError as problem:
            errors.append(RowError(first_row, str(problem)))
            continue
        plan.pos.append(PlannedPO(first_row, data, found.kind, found.customer.name if found.customer else None,
                                  intake.warnings_for(db, data)))  # fmt: skip
    if errors:
        errors.sort(key=lambda e: (e.row is None, e.row or 0))
        raise ImportRejected(errors[:MAX_ERRORS], truncated=len(errors) > MAX_ERRORS)
    return plan


def apply(
    db: Session, raw: bytes, filename: str | None = None, *, source_ref: str | None = None,
    actor: AppUser | None = None, client_address: str | None = None,
) -> Plan:  # fmt: skip
    """File every PO in the file through the intake service, or none. The caller commits. Returns the plan as done."""
    plan = read(db, raw, filename, source_ref=source_ref)
    for planned in plan.pos:
        result = intake.file_po(db, planned.data, actor=actor, client_address=client_address)
        planned.outcome = result.kind
    counts = {"purchase_orders": len(plan.pos), "created": plan.count("created"), "skipped": plan.count("skipped"),
              "revisions": plan.count("revision"), "waiting": plan.count("waiting"), "lines": plan.rows}  # fmt: skip
    audit(db, "customer_po.import", actor, None, client_address, after=counts)
    return plan
