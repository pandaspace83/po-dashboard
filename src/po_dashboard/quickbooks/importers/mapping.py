"""Which column of an export holds what (DEC-58). Columns are found by name, so the export's order does not matter, and
the person can change any of it in the preview. Nothing here assumes a report: two starting presets are written down
(QuickBooks Desktop's "Sales by Customer Detail", one row per invoice line, and an invoice list, one row per invoice)
and the mapping is saved with each upload so it can be seen later.

A mapping is data: {field: the header text of its column}. A field that is not mapped is simply not read."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from po_dashboard.db.customers import Setting

FIELDS = (
    "type", "number", "date", "customer", "po_reference", "qb_reference", "item", "description", "qty", "unit_price",
    "amount", "currency",
)  # fmt: skip
LABELS = {
    "type": "Transaction type", "number": "Invoice number", "date": "Invoice date", "customer": "Customer",
    "po_reference": "Customer's PO number", "qb_reference": "QuickBooks transaction reference", "item": "Item",
    "description": "Description", "qty": "Quantity", "unit_price": "Unit price", "amount": "Amount",
    "currency": "Currency",
}  # fmt: skip
REQUIRED = ("number", "date", "amount")  # a customer is needed too, from a column or from a heading row above its rows
ALIASES: dict[str, tuple[str, ...]] = {
    "type": ("type", "transaction type", "txn type"),
    "number": ("num", "number", "invoice #", "invoice no", "invoice number", "ref no", "ref number", "doc number"),
    "date": ("date", "invoice date", "txn date", "transaction date"),
    "customer": ("name", "customer", "customer name", "source name", "bill to"),
    "po_reference": ("po #", "po number", "po no", "customer po", "cust po", "purchase order", "p o #"),
    "qb_reference": ("txn id", "transaction id", "qb ref", "transaction ref", "trans #", "txn #"),
    "item": ("item", "item name", "sku", "product/service", "product", "item description"),
    "description": ("memo", "description", "memo/description"),
    "qty": ("qty", "quantity"),
    "unit_price": ("sales price", "price", "rate", "unit price", "sales price each"),
    "amount": ("amount", "invoice amount", "total amount", "line amount"),
    "currency": ("currency", "curr"),
}  # fmt: skip
PRESETS = {
    "sales_by_customer_detail": "QuickBooks Sales by Customer Detail (one row per invoice line)",
    "invoice_list": "An invoice list (one row per invoice)",
}
HEADER_SEARCH_ROWS = 25  # a report may have a company name, a title and a date range above its column names
PROFILE_PREFIX = "sales_mapping."
PROFILE_NAME = re.compile(r"[a-z0-9][a-z0-9 _-]{0,39}")


def norm(header: str) -> str:
    """A column name as compared: lower case, dots dropped, spaces squeezed (so "P. O. #" and "PO #" are alike)."""
    return " ".join(header.lower().replace(".", "").split())


@dataclass(frozen=True)
class Mapping:
    fields: dict[str, str] = field(default_factory=dict)  # field name -> the column's header text

    @property
    def level(self) -> str:
        """Matching is by line when item, quantity and price are all read, else by invoice (DEC-58)."""
        return "line" if all(name in self.fields for name in ("item", "qty", "unit_price")) else "invoice"

    @property
    def preset(self) -> str:
        return "sales_by_customer_detail" if self.level == "line" else "invoice_list"

    def to_json(self) -> str:
        return json.dumps(self.fields, sort_keys=True)

    @staticmethod
    def from_json(raw: str) -> Mapping:
        found = json.loads(raw)
        return Mapping({k: v for k, v in found.items() if k in FIELDS and isinstance(v, str)})

    def missing(self) -> list[str]:
        """The required fields that are not mapped, by their plain names."""
        return [LABELS[name] for name in REQUIRED if name not in self.fields]


def header_index(table: list[list[str]]) -> int | None:
    """The row that holds the column names: the first of the top rows that names an invoice number, a date and an
    amount. None if no row does."""
    for index, cells in enumerate(table[:HEADER_SEARCH_ROWS]):
        names = {norm(cell) for cell in cells if cell.strip()}
        if all(names & set(ALIASES[name]) for name in REQUIRED):
            return index
    return None


def detect(headers: list[str]) -> Mapping:
    """The mapping a header row suggests: each field takes the first column whose name is one of its usual names."""
    found: dict[str, str] = {}
    taken: set[int] = set()
    for name in FIELDS:
        for position, header in enumerate(headers):
            if position not in taken and header.strip() and norm(header) in ALIASES[name]:
                found[name] = header.strip()
                taken.add(position)
                break
    return Mapping(found)


def from_form(posted: dict[str, str], headers: list[str]) -> tuple[Mapping, list[str]]:
    """The mapping a person chose in the preview, and what is wrong with it. A header must be one the file has, and one
    column cannot be read as two fields."""
    have = {header.strip(): header.strip() for header in headers if header.strip()}
    chosen: dict[str, str] = {}
    problems: list[str] = []
    for name in FIELDS:
        value = posted.get(name, "").strip()
        if not value:
            continue
        if value not in have:
            problems.append(f"{LABELS[name]}: the file has no column called {value[:60]}.")
            continue
        chosen[name] = value
    used: dict[str, str] = {}
    for name, header in chosen.items():
        if header in used:
            problems.append(f"The column {header[:60]} cannot be both {LABELS[used[header]]} and {LABELS[name]}.")
        used[header] = name
    return Mapping(chosen), problems


# ---- saved mappings: one setting each, so the person can reuse what they confirmed


def save_profile(db: Session, name: str, mapping: Mapping) -> None:
    key = name.strip().lower()
    if not PROFILE_NAME.fullmatch(key):
        raise ValueError("A saved mapping's name uses letters, digits, spaces, dashes and underscores (40 at most).")
    row = db.get(Setting, PROFILE_PREFIX + key)
    if row is None:
        db.add(Setting(key=PROFILE_PREFIX + key, value=mapping.to_json()))
    else:
        row.value = mapping.to_json()
    db.flush()


def profiles(db: Session) -> dict[str, Mapping]:
    rows = db.scalars(select(Setting).where(Setting.key.like(PROFILE_PREFIX + "%"))).all()
    return {row.key.removeprefix(PROFILE_PREFIX): Mapping.from_json(row.value) for row in rows}


def usable(mapping: Mapping, headers: list[str]) -> bool:
    """A saved mapping suits a file when every column it names is there."""
    have = {header.strip() for header in headers}
    return bool(mapping.fields) and all(header in have for header in mapping.fields.values())
