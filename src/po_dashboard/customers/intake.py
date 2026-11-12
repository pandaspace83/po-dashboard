"""The one service every customer PO goes through: the form, a file, and later the email reader (REQ-CO-05, DEC-57).

It checks the data with the purchasing rules (exact decimals, nothing rounded), finds the customer without guessing,
then does exactly one of four things:

- **created**: a new number for that customer is filed active, the person filing it being the one who approves it;
- **skipped**: the same order with the same terms is already here (any revision): nothing changes;
- **revision**: the same number with different terms is stored as the next revision, **pending**: nothing already
  approved is replaced until a person approves it;
- **waiting**: no customer could be found (no explicit choice, no exact QuickBooks name, no known email domain), so the
  data is kept as it came for a person to resolve.

Nothing here commits and nothing here calls a model."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from po_dashboard.auth.service import audit
from po_dashboard.customers import config, service
from po_dashboard.customers.keys import po_key
from po_dashboard.db.base import utcnow
from po_dashboard.db.customers import Customer, CustomerPO, CustomerPOIntake, CustomerPOLine
from po_dashboard.db.models import AppUser
from po_dashboard.db.types import Money, Quantity
from po_dashboard.ledger.errors import LedgerError, check_seen, text
from po_dashboard.ledger.orders import clean_currency

MAX_LINES = 500
SOURCES = ("manual", "import", "document")


@dataclass(frozen=True)
class LineData:
    item: str
    qty: Decimal
    unit_price: Decimal
    line_no: int | None = None  # numbered 1, 2, 3 when left out
    unit: str | None = None
    description: str | None = None
    stated_amount: Decimal | None = None  # as the PO writes it; it must equal quantity times price


@dataclass(frozen=True)
class PoData:
    po_number: str
    currency: str
    lines: tuple[LineData, ...]
    customer_id: int | None = None  # an explicit choice, by a person
    customer_name: str | None = None  # a name as written, matched exactly against a customer or a QuickBooks name
    sender_domain: str | None = None  # the sender's email domain, from the email reader (PD-8.8)
    po_date: date | None = None
    stated_total: Decimal | None = None  # as written; the lines decide, and a difference is flagged
    source: str = "manual"
    source_ref: str | None = None
    source_document_id: int | None = None


@dataclass
class Classification:
    kind: str  # created, skipped, revision, waiting
    customer: Customer | None = None
    matched: CustomerPO | None = None  # for skipped: the revision it equals; for revision: the latest revision
    revision: int = 0
    reason: str | None = None


@dataclass
class Result:
    kind: str
    po: CustomerPO | None = None
    intake: CustomerPOIntake | None = None
    warnings: list[str] = field(default_factory=list)


# ---- checking


def _exact(value: Decimal, kind: type[Quantity] | type[Money], label: str) -> None:
    try:
        kind().to_stored(value)
    except (ValueError, TypeError) as problem:
        raise LedgerError(f"{label}: {problem}") from None


def check(db: Session, data: PoData) -> PoData:
    """The data, cleaned and numbered, or a LedgerError naming the first problem."""
    number = text(data.po_number, "PO number", longest=64, required=True) or ""
    if not po_key(number):
        raise LedgerError("PO number needs at least one letter or digit.")
    code = clean_currency(data.currency)
    if data.source not in SOURCES:
        raise LedgerError("Source must be manual, import or document.")
    if not data.lines:
        raise LedgerError("A customer PO needs at least one line.")
    if len(data.lines) > MAX_LINES:
        raise LedgerError(f"A customer PO can have at most {MAX_LINES} lines.")
    if data.stated_total is not None:
        _exact(data.stated_total, Money, "Total")
    tolerance = config.decimal(db, "amount_tolerance")
    used: set[int] = set()
    lines: list[LineData] = []
    for position, line in enumerate(data.lines, start=1):
        label = f"Line {line.line_no or position}"
        wanted = line.line_no if line.line_no is not None else position
        if wanted < 1 or wanted > 100000 or wanted in used:
            raise LedgerError(f"{label}: the line number must be a whole number from 1 to 100000, used once.")
        used.add(wanted)
        _exact(line.qty, Quantity, f"{label}: Quantity")
        _exact(line.unit_price, Money, f"{label}: Unit price")
        if line.qty <= 0:
            raise LedgerError(f"{label}: Quantity must be more than zero.")
        if line.unit_price < 0:
            raise LedgerError(f"{label}: Unit price cannot be below zero.")
        if line.stated_amount is not None:
            _exact(line.stated_amount, Money, f"{label}: Amount")
            expected = line.qty * line.unit_price
            if abs(line.stated_amount - expected) > tolerance:
                raise LedgerError(
                    f"{label}: the amount {line.stated_amount} is not quantity times price ({expected}). "
                    "Nothing is guessed: correct the amount or the price."
                )
        try:
            lines.append(LineData(
                item=text(line.item, f"{label}: Item", longest=100, required=True) or "", qty=line.qty,
                unit_price=line.unit_price, line_no=wanted, unit=text(line.unit, f"{label}: Unit", longest=16),
                description=text(line.description, f"{label}: Description", longest=500, multiline=True),
                stated_amount=line.stated_amount,
            ))  # fmt: skip
        except LedgerError:
            raise
    return PoData(
        po_number=number, currency=code, lines=tuple(lines), customer_id=data.customer_id,
        customer_name=text(data.customer_name, "Customer", longest=200), sender_domain=data.sender_domain,
        po_date=data.po_date, stated_total=data.stated_total, source=data.source,
        source_ref=text(data.source_ref, "Source", longest=200), source_document_id=data.source_document_id,
    )  # fmt: skip


def warnings_for(db: Session, data: PoData) -> list[str]:
    """Things worth a look that are not errors: a stated total that is not the sum of the lines (the lines decide)."""
    found: list[str] = []
    if data.stated_total is not None:
        total = sum((line.qty * line.unit_price for line in data.lines), Decimal(0))
        if abs(total - data.stated_total) > config.decimal(db, "amount_tolerance"):
            found.append(f"The total on the PO ({data.stated_total}) is not the sum of its lines ({total}).")
    return found


# ---- finding the customer and the earlier revisions


def _terms(po_date: date | None, currency: str, total: Decimal | None, lines: list[tuple[Any, ...]]) -> tuple[Any, ...]:
    return po_date, currency, total, tuple(sorted(lines))


def _terms_of_data(data: PoData) -> tuple[Any, ...]:
    return _terms(
        data.po_date, data.currency, data.stated_total,
        [(ln.line_no, ln.item.strip().lower(), ln.qty, ln.unit_price, (ln.unit or "").lower()) for ln in data.lines],
    )  # fmt: skip


def _terms_of_po(db: Session, order: CustomerPO) -> tuple[Any, ...]:
    rows = db.scalars(select(CustomerPOLine).where(CustomerPOLine.customer_po_id == order.id))
    return _terms(
        order.po_date, order.currency, order.stated_total,
        [(ln.line_no, ln.item.strip().lower(), ln.qty, ln.unit_price, (ln.unit or "").lower()) for ln in rows],
    )  # fmt: skip


def resolve_customer(db: Session, data: PoData) -> Customer | None:
    """An explicit choice, else an exact QuickBooks name, else a known email domain, else nobody."""
    if data.customer_id is not None:
        chosen = db.get(Customer, data.customer_id)
        if chosen is None:
            raise LedgerError("That customer was not found.")
        return chosen
    if data.customer_name and (found := service.find_for_export(db, data.customer_name)) is not None:
        return found
    if data.sender_domain:
        try:
            return service.find_by_domain(db, data.sender_domain)
        except LedgerError:
            return None
    return None


def classify(db: Session, data: PoData) -> Classification:
    """What filing this data would do. Writes nothing, so a preview can ask."""
    customer = resolve_customer(db, data)
    if customer is None:
        shown = (data.customer_name or data.sender_domain or "no customer given")[:60]
        return Classification("waiting", reason=f"No customer found for {shown}.")
    if not customer.active:
        raise LedgerError(f"{customer.name} is not active. Make it active first, or choose another customer.")
    earlier = list(db.scalars(
        select(CustomerPO).where(CustomerPO.customer_id == customer.id, CustomerPO.po_key == po_key(data.po_number))
        .order_by(CustomerPO.revision)
    ))  # fmt: skip
    if not earlier:
        return Classification("created", customer=customer)
    wanted = _terms_of_data(data)
    for order in earlier:
        if _terms_of_po(db, order) == wanted:
            return Classification("skipped", customer=customer, matched=order)
    return Classification("revision", customer=customer, matched=earlier[-1], revision=earlier[-1].revision + 1)


# ---- filing


def file_po(db: Session, data: PoData, *, actor: AppUser | None = None, client_address: str | None = None) -> Result:
    """Check the data and file it. The caller commits."""
    data = check(db, data)
    found = classify(db, data)
    warnings = warnings_for(db, data)
    if found.kind == "waiting":
        waiting = CustomerPOIntake(
            source=data.source, payload=to_json(data), reason=found.reason or "No customer found.",
            created_by=actor.id if actor else None,
        )  # fmt: skip
        db.add(waiting)
        db.flush()
        audit(db, "customer_po.waiting", actor, f"customer_po_intake:{waiting.id}", client_address,
              after={"po_number": data.po_number, "reason": waiting.reason})  # fmt: skip
        return Result("waiting", intake=waiting, warnings=warnings)
    if found.kind == "skipped":
        return Result("skipped", po=found.matched, warnings=warnings)
    if found.customer is None:  # classify() gives a customer for every kind but waiting
        raise LedgerError("No customer was found. Nothing was filed.")
    now = utcnow()
    active = found.kind == "created"
    order = CustomerPO(
        customer_id=found.customer.id, po_number=data.po_number, po_key=po_key(data.po_number),
        revision=found.revision, state="active" if active else "pending", po_date=data.po_date,
        currency=data.currency, stated_total=data.stated_total, source=data.source,
        source_document_id=data.source_document_id, source_ref=data.source_ref,
        created_by=actor.id if actor else None, approved_by=actor.id if actor and active else None,
        approved_at=now if active else None,
    )  # fmt: skip
    db.add(order)
    try:
        db.flush()
    except IntegrityError:
        raise LedgerError("That customer PO was filed a moment ago by someone else. Reload and look again.") from None
    for line in data.lines:
        db.add(CustomerPOLine(
            customer_po_id=order.id, line_no=line.line_no or 0, item=line.item, description=line.description,
            qty=line.qty, unit=line.unit, unit_price=line.unit_price, stated_amount=line.stated_amount,
        ))  # fmt: skip
    db.flush()
    summary = {"customer": found.customer.name, "po_number": data.po_number, "revision": found.revision,
               "lines": len(data.lines), "source": data.source}  # fmt: skip
    audit(db, "customer_po.create" if active else "customer_po.revision", actor, f"customer_po:{order.id}",
          client_address, after=summary)  # fmt: skip
    return Result(found.kind, po=order, warnings=warnings)


# ---- revisions


def _pending(db: Session, order: CustomerPO, seen: int | None, check_version: bool) -> None:
    if order.state != "pending":
        raise LedgerError("That revision is not waiting for approval any more.")
    if check_version:
        check_seen(order.version, seen, "This revision")


def approve_revision(
    db: Session, order: CustomerPO, *, actor: AppUser | None = None, client_address: str | None = None,
    seen: int | None = None, check: bool = False,
) -> None:  # fmt: skip
    """The new terms become the order's terms; the old active revision is kept as superseded."""
    _pending(db, order, seen, check)
    before = []
    for other in db.scalars(
        select(CustomerPO).where(
            CustomerPO.customer_id == order.customer_id, CustomerPO.po_key == order.po_key, CustomerPO.state == "active"
        )
    ):
        other.state = "superseded"
        before.append({"revision": other.revision, "state": "active"})
    order.state, order.approved_by, order.approved_at = "active", actor.id if actor else None, datetime.now(UTC)
    db.flush()
    audit(db, "customer_po.approve", actor, f"customer_po:{order.id}", client_address,
          before=before, after={"revision": order.revision, "state": "active"})  # fmt: skip


def reject_revision(
    db: Session, order: CustomerPO, reason: str | None, *, actor: AppUser | None = None,
    client_address: str | None = None, seen: int | None = None, check: bool = False,
) -> None:  # fmt: skip
    why = text(reason, "Reason", longest=500, required=True)
    _pending(db, order, seen, check)
    order.state, order.closed_by, order.closed_at, order.close_note = (
        "rejected",
        actor.id if actor else None,
        utcnow(),
        why,
    )
    db.flush()
    audit(db, "customer_po.reject", actor, f"customer_po:{order.id}", client_address,
          before={"revision": order.revision, "state": "pending"},
          after={"state": "rejected", "reason": why})  # fmt: skip


# ---- the waiting list


def to_json(data: PoData) -> str:
    def plain(value: Any) -> Any:
        if isinstance(value, Decimal):
            return str(value)
        if isinstance(value, date):
            return value.isoformat()
        raise TypeError(type(value).__name__)

    return json.dumps(data.__dict__ | {"lines": [ln.__dict__ for ln in data.lines]}, default=plain, sort_keys=True)


def from_json(payload: str) -> PoData:
    raw = json.loads(payload)

    def dec(value: str | None) -> Decimal | None:
        return None if value is None else Decimal(value)

    lines = tuple(
        LineData(item=ln["item"], qty=Decimal(ln["qty"]), unit_price=Decimal(ln["unit_price"]), line_no=ln["line_no"],
                 unit=ln["unit"], description=ln["description"], stated_amount=dec(ln["stated_amount"]))
        for ln in raw["lines"]
    )  # fmt: skip
    return PoData(
        po_number=raw["po_number"], currency=raw["currency"], lines=lines, customer_id=raw["customer_id"],
        customer_name=raw["customer_name"], sender_domain=raw["sender_domain"],
        po_date=date.fromisoformat(raw["po_date"]) if raw["po_date"] else None, stated_total=dec(raw["stated_total"]),
        source=raw["source"], source_ref=raw["source_ref"], source_document_id=raw["source_document_id"],
    )  # fmt: skip


def resolve_waiting(
    db: Session, waiting: CustomerPOIntake, *, customer_id: int | None = None, new_customer_name: str | None = None,
    actor: AppUser | None = None, client_address: str | None = None,
) -> Result:  # fmt: skip
    """A person says which customer a waiting PO is for (or makes a new one) and it is filed as it would have been."""
    if waiting.state != "waiting":
        raise LedgerError("That PO is not waiting any more.")
    if new_customer_name and new_customer_name.strip():
        chosen = service.create_customer(db, name=new_customer_name, actor=actor, client_address=client_address)
        customer_id = chosen.id
    if customer_id is None:
        raise LedgerError("Choose a customer, or write the name of a new one.")
    data = from_json(waiting.payload)
    result = file_po(db, PoData(**{**data.__dict__, "customer_id": customer_id}), actor=actor,
                     client_address=client_address)  # fmt: skip
    if result.kind == "waiting":  # cannot happen with an explicit customer
        raise LedgerError("The customer could not be used. Nothing was filed.")
    waiting.state, waiting.resolved_by, waiting.resolved_at = "done", actor.id if actor else None, utcnow()
    waiting.customer_po_id = result.po.id if result.po else None
    db.flush()
    audit(db, "customer_po.waiting_resolve", actor, f"customer_po_intake:{waiting.id}", client_address,
          after={"customer_id": customer_id, "result": result.kind})  # fmt: skip
    return result


def reject_waiting(
    db: Session, waiting: CustomerPOIntake, reason: str | None, *, actor: AppUser | None = None,
    client_address: str | None = None,
) -> None:  # fmt: skip
    why = text(reason, "Reason", longest=500, required=True)
    if waiting.state != "waiting":
        raise LedgerError("That PO is not waiting any more.")
    waiting.state, waiting.resolved_by, waiting.resolved_at = "rejected", actor.id if actor else None, utcnow()
    db.flush()
    audit(db, "customer_po.waiting_reject", actor, f"customer_po_intake:{waiting.id}", client_address,
          after={"reason": why})  # fmt: skip
