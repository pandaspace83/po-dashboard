"""Reading customer POs back for the screens: one row per order number (not per revision), a revision's differences from
the order it would replace, and an order's total (the lines decide; a stated total is only a note)."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from po_dashboard.db.customers import Customer, CustomerPO, CustomerPOLine
from po_dashboard.display import day

LIVE = ("active", "closed", "cancelled")  # the revision whose terms count


@dataclass
class OrderRow:
    order: CustomerPO  # the revision that counts: the approved one, else the latest
    number: str  # the order's number as first written: one order keeps one spelling, whatever a revision says
    customer: Customer
    lines: int
    total: Decimal
    pending: CustomerPO | None  # a revision waiting for a person, if any
    revisions: int


def line_total(line: CustomerPOLine) -> Decimal:
    return line.qty * line.unit_price


def order_rows(db: Session) -> list[OrderRow]:
    """Every customer PO number once, with its counting revision, its total and any revision waiting."""
    orders = list(db.scalars(select(CustomerPO).order_by(CustomerPO.revision)))
    customers = {cust.id: cust for cust in db.scalars(select(Customer))}
    totals: dict[int, Decimal] = defaultdict(Decimal)
    counts: dict[int, int] = defaultdict(int)
    for po_id, qty, price in db.execute(
        select(CustomerPOLine.customer_po_id, CustomerPOLine.qty, CustomerPOLine.unit_price)
    ):
        totals[po_id] += qty * price
        counts[po_id] += 1
    groups: dict[tuple[int, str], list[CustomerPO]] = defaultdict(list)
    for order in orders:
        groups[(order.customer_id, order.po_key)].append(order)
    rows = []
    for revisions in groups.values():
        counting = [o for o in revisions if o.state in LIVE]
        shown = counting[-1] if counting else revisions[-1]
        pending = next((o for o in reversed(revisions) if o.state == "pending"), None)
        rows.append(OrderRow(shown, revisions[0].po_number, customers[shown.customer_id], counts[shown.id],
                             totals[shown.id], pending, len(revisions)))  # fmt: skip
    return rows


def revisions_of(db: Session, order: CustomerPO) -> list[CustomerPO]:
    return list(db.scalars(
        select(CustomerPO).where(CustomerPO.customer_id == order.customer_id, CustomerPO.po_key == order.po_key)
        .order_by(CustomerPO.revision)
    ))  # fmt: skip


def lines_of(db: Session, order: CustomerPO) -> list[CustomerPOLine]:
    return list(db.scalars(
        select(CustomerPOLine).where(CustomerPOLine.customer_po_id == order.id).order_by(CustomerPOLine.line_no)
    ))  # fmt: skip


def _shown(value: Decimal | None) -> str:
    return "none" if value is None else format(value.normalize(), "f")


def differences(db: Session, old: CustomerPO, new: CustomerPO) -> list[str]:
    """What the new revision changes, in plain words, for the person deciding whether to approve it."""
    found: list[str] = []
    if old.po_date != new.po_date:
        found.append(f"PO date: {day(old.po_date) or 'none'} to {day(new.po_date) or 'none'}")
    if old.currency != new.currency:
        found.append(f"Currency: {old.currency} to {new.currency}")
    if old.stated_total != new.stated_total:
        found.append(f"Stated total: {_shown(old.stated_total)} to {_shown(new.stated_total)}")
    before = {line.line_no: line for line in lines_of(db, old)}
    after = {line.line_no: line for line in lines_of(db, new)}
    for number in sorted(before.keys() | after.keys()):
        was, now = before.get(number), after.get(number)
        if was is None and now is not None:
            found.append(f"Line {number} added: {now.item}, {_shown(now.qty)} at {_shown(now.unit_price)}")
        elif now is None and was is not None:
            found.append(f"Line {number} removed: {was.item}")
        elif was is not None and now is not None:
            if was.item != now.item:
                found.append(f"Line {number} item: {was.item} to {now.item}")
            if was.qty != now.qty:
                found.append(f"Line {number} quantity: {_shown(was.qty)} to {_shown(now.qty)}")
            if was.unit_price != now.unit_price:
                found.append(f"Line {number} unit price: {_shown(was.unit_price)} to {_shown(now.unit_price)}")
            if (was.unit or "") != (now.unit or ""):
                found.append(f"Line {number} unit: {was.unit or 'none'} to {now.unit or 'none'}")
    return found
