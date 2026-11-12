"""Planning and keeping a sales import (DEC-58): what an upload would do, then writing it, all in one transaction.

`build_plan` changes nothing: it reads the file, compares each invoice with what the app already holds, and says which
are new, which are the same, which changed since they were accepted, and which share a number with a different invoice.
`apply_import` keeps the file under its hash, every row of it, the dates it covers, and each sighting of each invoice.

Nothing accepted is ever overwritten. An invoice seen again unchanged only adds a sighting. One that changed is marked
for review and keeps its accepted content until a person decides (PD-8.5). A file already imported is refused."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from po_dashboard.auth.service import audit
from po_dashboard.customers import config, service
from po_dashboard.customers.keys import name_key
from po_dashboard.db.customers import (
    Allocation,
    Customer,
    SalesImport,
    SalesImportRow,
    SalesInvoice,
    SalesInvoiceLine,
    SalesInvoiceObservation,
)
from po_dashboard.db.models import AppUser
from po_dashboard.intake.store import FileStore
from po_dashboard.ledger.errors import LedgerError, check_seen, text
from po_dashboard.ledger.sheets import SheetRejected, read_table
from po_dashboard.quickbooks.importers import mapping as mapping_module
from po_dashboard.quickbooks.importers.mapping import Mapping
from po_dashboard.quickbooks.importers.sales import ImportProblem, InvoiceRecord, Parsed, ParsedInvoice, read

NEW, SAME, CHANGED, CONFLICTING = "new", "same", "changed", "conflicting"


@dataclass
class PlannedInvoice:
    group: ParsedInvoice
    outcome: str
    existing: SalesInvoice | None
    customer: Customer | None


@dataclass
class ImportPlan:
    parsed: Parsed
    headers: list[str]
    items: list[PlannedInvoice]
    file_hash: str
    earlier: SalesImport | None
    unknown_names: Counter[str] = field(default_factory=Counter)

    def count(self, outcome: str) -> int:
        return sum(1 for item in self.items if item.outcome == outcome)

    @property
    def needs_review(self) -> int:
        return sum(1 for item in self.items if item.group.primary.review or item.outcome in (CHANGED, CONFLICTING))


# ---- the content of an invoice, as compared


def _fixed(value: Decimal | None) -> str:
    return "" if value is None else format(value.normalize(), "f")


def canonical(
    kind: str, key: str, customer: str | None, day: date | None, currency: str, po: str | None, qb: str | None,
    level: str, total: Decimal | None, lines: list[tuple[Any, ...]],
) -> dict[str, Any]:  # fmt: skip
    """What makes two sightings of an invoice the same: money, items, customer, date, references. A memo is left out."""
    return {
        "kind": kind, "key": key, "customer": name_key(customer or ""), "date": day.isoformat() if day else "",
        "currency": currency, "po": po or "", "qb": qb or "", "level": level, "total": _fixed(total),
        "lines": [[p, (i or "").strip().lower(), _fixed(q), _fixed(u), _fixed(a), k] for p, i, q, u, a, k in lines],
    }  # fmt: skip


def fingerprint_of(content: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(content, sort_keys=True).encode()).hexdigest()


def content_of_record(record: InvoiceRecord) -> dict[str, Any]:
    return canonical(
        record.kind, record.key, record.customer_name, record.date, record.currency, record.po_reference,
        record.qb_reference, record.level, record.total,
        [(ln.position, ln.item, ln.qty, ln.unit_price, ln.amount, ln.kind) for ln in record.lines],
    )  # fmt: skip


def content_of_invoice(db: Session, invoice: SalesInvoice) -> dict[str, Any]:
    lines = db.scalars(
        select(SalesInvoiceLine).where(SalesInvoiceLine.invoice_id == invoice.id).order_by(SalesInvoiceLine.position)
    )
    return canonical(
        invoice.kind, invoice.invoice_key, invoice.customer_name_exported, invoice.invoice_date, invoice.currency,
        invoice.po_reference, invoice.qb_reference, invoice.level, invoice.total,
        [(ln.position, ln.item, ln.qty, ln.unit_price, ln.amount, ln.kind) for ln in lines],
    )  # fmt: skip


# ---- the plan


def read_file(raw: bytes, filename: str | None) -> tuple[list[list[str]], list[str], int]:
    """The table, its column names and the row (counting from 1) that holds them."""
    try:
        table = read_table(raw, filename)
    except SheetRejected as problem:
        raise ImportProblem(str(problem)) from None
    head = mapping_module.header_index(table)
    if head is None:
        raise ImportProblem(
            "No row of the file names an invoice number, a date and an amount, so it does not look like a sales "
            "export. Export the report with its column names and try again."
        )
    return table, table[head], head + 1


def build_plan(db: Session, raw: bytes, filename: str | None, mapping: Mapping | None = None) -> ImportPlan:
    """What this file would do. Raises ImportProblem when it cannot be read as a sales export."""
    table, headers, _ = read_file(raw, filename)
    chosen = mapping if mapping is not None else mapping_module.detect(headers)
    parsed = read(
        table, chosen, default_currency=config.text(db, "default_currency"),
        charge_names=config.names(db, "charge_items"),
    )  # fmt: skip
    items: list[PlannedInvoice] = []
    unknown: Counter[str] = Counter()
    for group in parsed.invoices:
        record = group.primary
        existing = db.scalar(
            select(SalesInvoice).where(SalesInvoice.kind == record.kind, SalesInvoice.invoice_key == record.key)
        )
        if group.others:
            outcome = CONFLICTING
        elif existing is None:
            outcome = NEW
        else:
            same = fingerprint_of(content_of_invoice(db, existing)) == fingerprint_of(content_of_record(record))
            outcome = SAME if same else CHANGED
        customer = service.find_for_export(db, record.customer_name) if record.customer_name else None
        if customer is None and record.customer_name:
            unknown[record.customer_name] += 1
        items.append(PlannedInvoice(group, outcome, existing, customer))
    digest = hashlib.sha256(raw).hexdigest()
    earlier = db.scalar(select(SalesImport).where(SalesImport.file_hash == digest).order_by(SalesImport.id).limit(1))
    return ImportPlan(parsed, headers, items, digest, earlier, unknown)


# ---- keeping it


@dataclass
class ImportReport:
    record: SalesImport
    plan: ImportPlan
    counts: dict[str, int]


def _observation(db: Session, invoice: SalesInvoice, batch: SalesImport, record: InvoiceRecord, outcome: str) -> None:
    content = content_of_record(record)
    shown = {
        "record": content, "description": [ln.description for ln in record.lines],
        "items": [ln.item for ln in record.lines], "review": list(record.review), "notes": list(record.notes),
    }  # fmt: skip
    db.add(SalesInvoiceObservation(
        invoice_id=invoice.id, import_id=batch.id, fingerprint=fingerprint_of(content),
        content=json.dumps(shown, sort_keys=True), outcome=outcome,
    ))  # fmt: skip


def _create(db: Session, record: InvoiceRecord, customer: Customer | None, review: tuple[str, ...]) -> SalesInvoice:
    invoice = SalesInvoice(
        kind=record.kind, invoice_number=record.number, invoice_key=record.key,
        customer_id=customer.id if customer else None, customer_name_exported=record.customer_name,
        invoice_date=record.date, currency=record.currency, total=record.total, po_reference=record.po_reference,
        qb_reference=record.qb_reference, level=record.level, state="needs_review" if review else "accepted",
    )  # fmt: skip
    db.add(invoice)
    db.flush()
    for line in record.lines:
        db.add(SalesInvoiceLine(
            invoice_id=invoice.id, position=line.position, item=line.item, description=line.description,
            qty=line.qty, unit_price=line.unit_price, amount=line.amount, kind=line.kind,
        ))  # fmt: skip
    db.flush()
    return invoice


def apply_import(
    db: Session, store: FileStore, raw: bytes, filename: str | None, mapping: Mapping | None, *,
    period_from: date | None, period_to: date | None, actor: AppUser | None = None,
    client_address: str | None = None, save_mapping_as: str | None = None,
) -> ImportReport:  # fmt: skip
    """Keep the upload and every invoice in it, or raise and keep nothing. The caller commits."""
    plan = build_plan(db, raw, filename, mapping)
    if plan.earlier is not None:
        raise LedgerError(
            f"This exact file was already imported on {plan.earlier.created_at:%m/%d/%Y}. Nothing was added."
        )
    if not plan.items:
        raise LedgerError("Nothing in the file can be imported as an invoice. Look at the rows listed as not read.")
    if period_from is None or period_to is None:
        raise LedgerError(
            "State the dates this export covers, from and to. The app needs them to know what is missing."
        )
    if period_from > period_to:
        raise LedgerError("The period starts after it ends.")
    digest, path = store.put(raw)
    counts: dict[str, int] = {outcome: plan.count(outcome) for outcome in (NEW, SAME, CHANGED, CONFLICTING)}
    counts |= {outcome: plan.parsed.count(outcome) for outcome in ("line", "heading", "total", "unusable")}
    counts["unmapped_names"] = len(plan.unknown_names)
    batch = SalesImport(
        created_by=actor.id if actor else None, file_name=(filename or "export")[:255], file_hash=digest,
        stored_path=path, mapping=plan.parsed.mapping.to_json(), period_from=period_from, period_to=period_to,
        counts=json.dumps(counts, sort_keys=True),
    )  # fmt: skip
    db.add(batch)
    db.flush()
    for row in plan.parsed.rows:
        db.add(SalesImportRow(import_id=batch.id, row_number=row.row_number, cells=json.dumps(row.cells),
                              outcome=row.outcome, reason=row.reason))  # fmt: skip
    for item in plan.items:
        record = item.group.primary
        if item.existing is None:
            invoice = _create(db, record, item.customer, record.review)
            _observation(db, invoice, batch, record, "first")
        else:
            invoice = item.existing
            if invoice.customer_id is None and item.customer is not None:
                invoice.customer_id = item.customer.id  # linking a name to a customer is not a change to the invoice
            same = fingerprint_of(content_of_invoice(db, invoice)) == fingerprint_of(content_of_record(record))
            _observation(db, invoice, batch, record, SAME if same else CHANGED)
            if not same or record.review:
                invoice.state = "needs_review"  # the accepted content stays; a person decides
        for other in item.group.others:
            _observation(db, invoice, batch, other, "conflicting")
            invoice.state = "needs_review"
    db.flush()
    if save_mapping_as:
        mapping_module.save_profile(db, save_mapping_as, plan.parsed.mapping)
    audit(db, "sales_import.apply", actor, f"sales_import:{batch.id}", client_address,
          after={"file": batch.file_name[:60], "hash": digest[:12], "from": period_from.isoformat(),
                 "to": period_to.isoformat(), **counts})  # fmt: skip
    return ImportReport(batch, plan, counts)


# ---- customers the export names that the app does not know


def unmapped_names(db: Session) -> list[tuple[str, int]]:
    """Customer names exports used that match no customer, with how many invoices carry each."""
    found: Counter[str] = Counter()
    for (name,) in db.execute(select(SalesInvoice.customer_name_exported).where(SalesInvoice.customer_id.is_(None))):
        if name:
            found[name] += 1
    return sorted(found.items(), key=lambda pair: pair[0].lower())


def map_name(
    db: Session, name: str, *, customer_id: int | None = None, new_customer_name: str | None = None,
    actor: AppUser | None = None, client_address: str | None = None,
) -> int:  # fmt: skip
    """Say which customer an exported name is: an existing one, or a new one made now. The name becomes one of that
    customer's QuickBooks names (so the next export finds it by itself) and every invoice with that exact name is
    linked. Returns how many invoices were linked."""
    customer: Customer | None
    if new_customer_name and new_customer_name.strip():
        customer = service.create_customer(db, name=new_customer_name, actor=actor, client_address=client_address)
    else:
        customer = db.get(Customer, customer_id) if customer_id else None
    if customer is None:
        raise LedgerError("Choose a customer, or write the name of a new one.")
    key = name_key(name)
    holder = service.find_by_name(db, name)
    if holder is not None and holder.id != customer.id:
        raise LedgerError(f"The name {name[:60]} is already used for {holder.name}.")
    if holder is None:
        service.add_names(db, customer, name, actor, client_address)
    linked = 0
    for invoice in db.scalars(select(SalesInvoice).where(SalesInvoice.customer_id.is_(None))):
        if name_key(invoice.customer_name_exported or "") == key:
            invoice.customer_id = customer.id
            linked += 1
    db.flush()
    audit(db, "sales_invoice.map_customer", actor, f"customer:{customer.id}", client_address,
          after={"name": name[:100], "invoices": linked})  # fmt: skip
    return linked


# ---- a person settles an invoice that waits for review (PD-8.5)


def _decimal(value: str) -> Decimal | None:
    return Decimal(value) if value else None


def resolve_invoice(
    db: Session, invoice: SalesInvoice, action: str, reason: str | None, *, observation_id: int | None = None,
    actor: AppUser | None = None, client_address: str | None = None, seen: int | None = None, check: bool = False,
) -> None:  # fmt: skip
    """Settle an invoice marked for review. `keep` trusts the accepted figures and ignores what later uploads said;
    `use` replaces them with what one later upload showed. Either way a reason is kept, the invoice is accepted, and the
    caller re-runs the reconciliation and commits."""
    if invoice.state != "needs_review":
        raise LedgerError("That invoice is not waiting for review any more.")
    if action not in ("keep", "use"):
        raise LedgerError("Choose to keep the accepted figures or to use an upload's figures.")
    why = text(reason, "Reason", longest=500, required=True)
    if check:
        check_seen(invoice.version, seen, "This invoice")
    before = {"state": invoice.state, "total": None if invoice.total is None else str(invoice.total)}
    if action == "use":
        obs = db.get(SalesInvoiceObservation, observation_id) if observation_id else None
        if obs is None or obs.invoice_id != invoice.id or obs.outcome != CHANGED:
            raise LedgerError("Choose one of the uploads that showed this invoice with different figures.")
        shown = json.loads(obs.content)
        record = shown["record"]
        names = shown.get("items") or [None] * len(record["lines"])
        invoice.invoice_date = date.fromisoformat(record["date"]) if record["date"] else None
        invoice.currency, invoice.level = record["currency"], record["level"]
        invoice.po_reference, invoice.qb_reference = record["po"] or None, record["qb"] or None
        invoice.total = _decimal(record["total"])
        have = {
            ln.position: ln
            for ln in db.scalars(select(SalesInvoiceLine).where(SalesInvoiceLine.invoice_id == invoice.id))
        }
        descriptions = shown.get("description") or [None] * len(record["lines"])
        wanted = {line[0] for line in record["lines"]}
        for position, old in have.items():
            if position not in wanted:
                held = db.scalar(select(Allocation.id).where(
                    Allocation.sales_invoice_line_id == old.id, Allocation.basis == "person"
                ).limit(1))  # fmt: skip
                if held is not None:
                    raise LedgerError(
                        f"A person matched line {position}, which the upload no longer has. Withdraw that decision "
                        "first, or keep the accepted figures."
                    )
                db.delete(old)
        db.flush()
        for (position, item, qty, price, amount, kind), name, description in zip(
            record["lines"], names, descriptions, strict=False
        ):
            row = have.get(position)
            if row is None:
                row = SalesInvoiceLine(invoice_id=invoice.id, position=position, amount=Decimal(0))
                db.add(row)
            row.item, row.description = name if name is not None else (item or None), description
            row.qty, row.unit_price, row.amount, row.kind = (
                _decimal(qty),
                _decimal(price),
                _decimal(amount) or Decimal(0),
                kind,
            )
    invoice.state = "accepted"
    db.flush()
    used = observation_id if action == "use" else None
    audit(db, f"sales_invoice.{action}", actor, f"sales_invoice:{invoice.id}", client_address, before=before,
          after={"state": "accepted", "reason": why, "upload": used})  # fmt: skip
