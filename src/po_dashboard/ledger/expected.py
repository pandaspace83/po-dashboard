"""When a line is expected, and whether it is late (DEC-39 items 3 and 5). Computed from accepted facts, never stored.

The promised date is the vendor's, from an acknowledgement. Once a shipment carries an arrival estimate, that estimate
is the better guide and takes over, because it can move the date earlier or later. Both are kept so the page can show
each. A line that is closed, or has nothing open, is never late.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from po_dashboard.db.ledger import POLine, PromisedDateHistory, Shipment, ShipmentLine
from po_dashboard.ledger.status import LineState, line_states

Source = Literal["shipment", "promised"]


@dataclass(frozen=True)
class Expectation:
    promised: date | None  # the vendor's latest promised date
    eta: date | None  # the latest shipment estimate for the line
    expected: date | None  # the date to plan on: the estimate if there is one, else the promised date
    source: Source | None
    days_late: int  # whole days past `expected` while the line is still open; zero otherwise

    @property
    def late(self) -> bool:
        return self.days_late > 0


def expectation(promised: date | None, eta: date | None, today: date, closed: bool = False) -> Expectation:
    expected: date | None = None
    source: Source | None = None
    if eta:
        expected, source = eta, "shipment"
    elif promised:
        expected, source = promised, "promised"
    late = 0 if closed or expected is None else max((today - expected).days, 0)
    return Expectation(promised=promised, eta=eta, expected=expected, source=source, days_late=late)


def line_expectations(
    db: Session, today: date, po_id: int | None = None, states: dict[int, LineState] | None = None
) -> dict[int, Expectation]:
    """The expectation of every line, or of one PO's lines, keyed by line id."""
    promised_query = select(PromisedDateHistory.po_line_id, PromisedDateHistory.promised_date).order_by(
        PromisedDateHistory.recorded_at, PromisedDateHistory.id
    )
    eta_query = (
        select(ShipmentLine.po_line_id, ShipmentLine.eta, Shipment.eta)
        .join(Shipment, Shipment.id == ShipmentLine.shipment_id)
        .order_by(Shipment.accepted_at, Shipment.id)
    )
    if po_id is not None:
        promised_query = promised_query.join(POLine, POLine.id == PromisedDateHistory.po_line_id).where(
            POLine.po_id == po_id
        )
        eta_query = eta_query.where(Shipment.po_id == po_id)
    promised = {line_id: day for line_id, day in db.execute(promised_query)}  # the last row for a line wins
    etas: dict[int, date] = {}
    for line_id, own, shared in db.execute(eta_query):
        chosen = own or shared
        if chosen:
            etas[line_id] = chosen
    states = line_states(db, po_id) if states is None else states
    return {
        line_id: expectation(promised.get(line_id), etas.get(line_id), today, state.status.done)
        for line_id, state in states.items()
    }
