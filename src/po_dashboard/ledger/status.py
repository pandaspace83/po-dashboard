"""Line and PO status, computed from accepted facts (ARCHITECTURE §5, DEC-36). Never stored, so it cannot drift.

`line_state` holds every rule and touches no database. `line_states` and `po_states` load the facts for it.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from po_dashboard.db.ledger import AckLine, Acknowledgement, InvoiceLine, POLine, PurchaseOrder, ShipmentLine

ZERO = Decimal(0)


class LineStatus(StrEnum):
    SENT = "Sent"
    ACKNOWLEDGED = "Acknowledged"
    PARTIALLY_SHIPPED = "Partially shipped"
    SHIPPED = "Shipped"
    CLOSED = "Closed"
    CANCELLED = "Cancelled"  # closed by hand because the line will not be supplied (DEC-39)

    @property
    def done(self) -> bool:
        """Nothing more is expected on the line: it is closed or cancelled."""
        return self in (LineStatus.CLOSED, LineStatus.CANCELLED)


class POStatus(StrEnum):
    OPEN = "Open"
    CLOSED = "Closed"


@dataclass(frozen=True)
class LineFacts:
    """What is known about one line. Every figure comes from accepted records or from the PO."""

    ordered: Decimal
    confirmed: Decimal | None = None  # the latest quantity a vendor stated; None if none has
    acknowledged: bool = False  # an acknowledgement covers this line, even one that states no quantity
    shipped: Decimal = ZERO
    invoiced: Decimal = ZERO
    closed_reason: str | None = None  # received, short or cancelled, when a person closed the line by hand


@dataclass(frozen=True)
class LineState:
    status: LineStatus
    open_qty: Decimal  # what is still owed on the PO: zero once the line is closed
    po_needs_update: bool  # open, and the vendor confirmed a different quantity: the purchaser is told (DEC-36)
    facts: LineFacts


@dataclass(frozen=True)
class POState:
    status: POStatus
    partially_invoiced: bool
    open_lines: int


def line_state(facts: LineFacts) -> LineState:
    """Apply the table in ARCHITECTURE §5. The rows are checked from the top and the first match wins."""
    closed = facts.closed_reason is not None or facts.invoiced >= facts.ordered
    # An invoice comes after the goods, so it is evidence they moved even with no shipping notice (DEC-36).
    moved = max(facts.shipped, facts.invoiced)
    # A line closes against what was ordered, so a vendor confirming more does not raise the bar.
    target = facts.ordered if facts.confirmed is None else min(facts.confirmed, facts.ordered)

    if facts.closed_reason == "cancelled":
        status = LineStatus.CANCELLED
    elif closed:
        status = LineStatus.CLOSED
    elif moved > 0 and moved >= target:
        status = LineStatus.SHIPPED
    elif moved > 0:
        status = LineStatus.PARTIALLY_SHIPPED
    elif facts.acknowledged:
        status = LineStatus.ACKNOWLEDGED
    else:
        status = LineStatus.SENT

    return LineState(
        status=status,
        open_qty=ZERO if closed else facts.ordered - facts.invoiced,
        po_needs_update=not closed and facts.confirmed is not None and facts.confirmed != facts.ordered,
        facts=facts,
    )


def po_state(lines: Iterable[LineState]) -> POState:
    """A PO closes when every line has. A PO with no lines has nothing closed, so it is Open."""
    states = list(lines)
    open_lines = sum(1 for state in states if not state.status.done)
    closed = bool(states) and open_lines == 0
    return POState(
        status=POStatus.CLOSED if closed else POStatus.OPEN,
        partially_invoiced=not closed and any(state.facts.invoiced > 0 for state in states),
        open_lines=open_lines,
    )


def _on_po(statement: Any, line_id: Any, po_id: int | None) -> Any:
    return statement if po_id is None else statement.join(POLine, POLine.id == line_id).where(POLine.po_id == po_id)


def _totals(db: Session, line_id: Any, qty: Any, po_id: int | None) -> dict[int, Decimal]:
    statement = _on_po(select(line_id, func.sum(qty)).group_by(line_id), line_id, po_id)
    return {row[0]: row[1] for row in db.execute(statement)}


def line_states(db: Session, po_id: int | None = None) -> dict[int, LineState]:
    """The state of every line, or of one PO's lines, keyed by line id. Four queries however many lines there are."""
    shipped = _totals(db, ShipmentLine.po_line_id, ShipmentLine.qty, po_id)
    invoiced = _totals(db, InvoiceLine.po_line_id, InvoiceLine.qty, po_id)

    acks = _on_po(
        select(AckLine.po_line_id, AckLine.confirmed_qty)
        .join(Acknowledgement, AckLine.ack_id == Acknowledgement.id)
        .order_by(Acknowledgement.accepted_at, Acknowledgement.id),
        AckLine.po_line_id,
        po_id,
    )
    acknowledged: set[int] = set()
    confirmed: dict[int, Decimal] = {}
    for line_id, quantity in db.execute(acks):
        acknowledged.add(line_id)
        if quantity is not None:  # a later ack that only moves a date keeps the quantity already confirmed
            confirmed[line_id] = quantity

    lines = select(POLine.id, POLine.ordered_qty, POLine.closed_reason)
    if po_id is not None:
        lines = lines.where(POLine.po_id == po_id)
    return {
        line_id: line_state(
            LineFacts(
                ordered=ordered,
                confirmed=confirmed.get(line_id),
                acknowledged=line_id in acknowledged,
                shipped=shipped.get(line_id, ZERO),
                invoiced=invoiced.get(line_id, ZERO),
                closed_reason=closed_reason,
            )
        )
        for line_id, ordered, closed_reason in db.execute(lines)
    }


def po_states(db: Session) -> dict[int, POState]:
    """The state of every PO, keyed by PO id, including a PO that has no lines yet."""
    by_po: dict[int, list[LineState]] = {po_id: [] for po_id in db.scalars(select(PurchaseOrder.id))}
    owner = {row[0]: row[1] for row in db.execute(select(POLine.id, POLine.po_id))}
    for line_id, state in line_states(db).items():
        by_po[owner[line_id]].append(state)
    return {po_id: po_state(states) for po_id, states in by_po.items()}
