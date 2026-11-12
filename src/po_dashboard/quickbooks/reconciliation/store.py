"""Between the tables and the engine (DEC-60, DEC-62): build the book, run it, keep what it found.

`load` reads the revision in force of every customer PO, every sales invoice, every person's decision and every upload's
period into plain records. `run` reconciles them, replaces the app's own allocations (a person's are never touched), and
writes a history entry for each PO whose status or exceptions changed. It is called, inside the caller's transaction,
after every upload, PO change, revision decision and person's decision. Pages read `current`, which works the same
result out again without writing anything, so a page always shows today's view (the 30-day rule moves with the date)."""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import date
from decimal import Decimal

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from po_dashboard.customers import config
from po_dashboard.db.customers import (
    Allocation,
    CustomerPO,
    CustomerPOLine,
    ReconciliationEvent,
    SalesImport,
    SalesInvoice,
    SalesInvoiceLine,
    SalesInvoiceObservation,
)
from po_dashboard.quickbooks.reconciliation.engine import (
    Book,
    Decision,
    Invoice,
    InvoiceLine,
    Po,
    PoLine,
    Result,
    Rules,
    item_key,
    reconcile,
)

IN_FORCE = ("active", "closed", "cancelled")
CAUSES = ("upload", "person", "revision", "po_change")


def _local(value: object) -> date | None:
    return value.astimezone().date() if value is not None else None  # type: ignore[attr-defined]


def rules(db: Session, today: date | None = None) -> Rules:
    return Rules(
        today=today or date.today(), amount_tolerance=config.decimal(db, "amount_tolerance"),
        quantity_tolerance=config.decimal(db, "quantity_tolerance"), unbilled_days=config.whole(db, "unbilled_days"),
    )  # fmt: skip


def load(db: Session, today: date | None = None) -> Book:
    lines_of: dict[int, list[CustomerPOLine]] = defaultdict(list)
    for line in db.scalars(select(CustomerPOLine).order_by(CustomerPOLine.id)):
        lines_of[line.customer_po_id].append(line)
    families: dict[tuple[int, str], list[CustomerPO]] = defaultdict(list)
    for order in db.scalars(select(CustomerPO).order_by(CustomerPO.id)):
        families[(order.customer_id, order.po_key)].append(order)
    pos: list[Po] = []
    in_force_of: dict[int, CustomerPO] = {}  # any revision's id -> the revision in force
    line_now: dict[int, int | None] = {}  # any revision's line id -> the same line in the revision in force
    for revisions in families.values():
        in_force = [r for r in revisions if r.state in IN_FORCE]
        if not in_force:
            continue  # only a pending or rejected revision: nothing approved to reconcile yet
        current = max(in_force, key=lambda r: r.revision)
        original = min(revisions, key=lambda r: r.revision)
        pending = [r.revision for r in revisions if r.state == "pending" and r.revision > current.revision]
        index = {(ln.line_no, item_key(ln.item)): ln.id for ln in lines_of[current.id]}
        for revision in revisions:
            in_force_of[revision.id] = current
            for line in lines_of[revision.id]:
                line_now[line.id] = index.get((line.line_no, item_key(line.item)))
        pos.append(Po(
            id=current.id, customer_id=current.customer_id, number=original.po_number, revision=current.revision,
            state=current.state, since=original.po_date or _local(original.created_at) or date.today(),
            currency=current.currency,
            lines=tuple(PoLine(ln.id, ln.line_no, ln.item, ln.qty, ln.unit_price) for ln in lines_of[current.id]),
            pending_revision=max(pending) if pending else None,
            revised_on=_local(current.approved_at) if current.revision else None,
            earlier=tuple(r.id for r in revisions if r.id != current.id),
        ))  # fmt: skip
    invoice_lines: dict[int, list[SalesInvoiceLine]] = defaultdict(list)
    for item in db.scalars(select(SalesInvoiceLine).order_by(SalesInvoiceLine.id)):
        invoice_lines[item.invoice_id].append(item)
    seen: dict[int, set[str]] = defaultdict(set)
    for invoice_id, outcome in db.execute(
        select(SalesInvoiceObservation.invoice_id, SalesInvoiceObservation.outcome)
    ):  # fmt: skip
        seen[invoice_id].add(outcome)
    invoices = []
    for inv in db.scalars(select(SalesInvoice).order_by(SalesInvoice.id)):
        review = None
        if inv.state == "needs_review":
            outcomes = seen[inv.id]
            review = (
                "duplicate_number"
                if "conflicting" in outcomes
                else "changed"
                if "changed" in outcomes
                else "incomplete"
            )
        lines = tuple(
            InvoiceLine(ln.id, ln.position, ln.item, ln.qty, ln.unit_price, ln.amount, ln.kind)
            for ln in invoice_lines[inv.id]
        )
        total = inv.total if inv.total is not None else sum((ln.amount for ln in lines), Decimal(0))
        invoices.append(Invoice(
            id=inv.id, kind=inv.kind, number=inv.invoice_number, customer_id=inv.customer_id,
            customer_name=inv.customer_name_exported, date=inv.invoice_date, currency=inv.currency, total=total,
            po_reference=inv.po_reference, qb_reference=inv.qb_reference, level=inv.level, lines=lines, review=review,
        ))  # fmt: skip
    decisions = []
    for row in db.scalars(select(Allocation).where(Allocation.basis == "person").order_by(Allocation.id)):
        holder = in_force_of.get(row.customer_po_id)
        if holder is None:
            continue
        po_line, stale = None, None
        if row.customer_po_line_id is not None:
            po_line = line_now.get(row.customer_po_line_id)
            if po_line is None:
                stale = (
                    f"A person matched an invoice line to a PO line that revision {holder.revision} no longer has "
                    "in the same form. Look at the match again."
                )
        decisions.append(Decision(
            id=row.id, state=row.state, po_id=holder.id, invoice_id=row.sales_invoice_id, po_line_id=po_line,
            invoice_line_id=row.sales_invoice_line_id, amount=row.amount, stale=stale,
        ))  # fmt: skip
    periods = tuple(
        (start, end)
        for start, end in db.execute(select(SalesImport.period_from, SalesImport.period_to))
        if start is not None and end is not None
    )
    return Book(tuple(pos), tuple(invoices), rules(db, today), tuple(decisions), periods)


def current(db: Session, today: date | None = None) -> Result:
    """Today's result, worked out again without writing anything: what pages show."""
    return reconcile(load(db, today))


def summary(problems: list) -> str:  # type: ignore[type-arg]
    return json.dumps([[p.code, p.level, p.message] for p in problems])


def run(db: Session, *, cause: str, cause_ref: int | None = None, today: date | None = None) -> Result:
    """Reconcile, keep the app's own allocations and each PO's history. The caller commits."""
    if cause not in CAUSES:
        raise ValueError(f"unknown cause {cause}")
    result = reconcile(load(db, today))
    db.execute(delete(Allocation).where(Allocation.basis == "rule"))
    for link in result.links:
        if link.basis == "rule":
            db.add(Allocation(
                customer_po_id=link.po_id, customer_po_line_id=link.po_line_id, sales_invoice_id=link.invoice_id,
                sales_invoice_line_id=link.invoice_line_id, qty=link.qty, amount=link.amount, basis="rule",
                state="proposed",
            ))  # fmt: skip
    latest: dict[int, ReconciliationEvent] = {}
    for event in db.scalars(select(ReconciliationEvent).order_by(ReconciliationEvent.id)):
        latest[event.customer_po_id] = event
    for order in result.orders.values():
        family = [latest[i] for i in (order.po.id, *order.po.earlier) if i in latest]
        before = max(family, key=lambda e: e.id) if family else None
        now = summary(order.problems)
        if before is None or before.status_after != order.status or before.exceptions != now:
            db.add(ReconciliationEvent(
                customer_po_id=order.po.id, status_before=before.status_after if before else None,
                status_after=order.status, exceptions=now, cause=cause, cause_ref=cause_ref,
            ))  # fmt: skip
    db.flush()
    return result
