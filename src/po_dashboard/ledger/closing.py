"""Settling lines by hand: received, closed short, or cancelled, always with a reason (REQ-INV-04, DEC-39 item 2).

This is the last resort. Most lines close from the emails and the QuickBooks export (DEC-41). The page offers one
form for the whole PO, with one reason, so it is never a line-by-line chore. A hand-close can be taken back with a
reason, and the audit trail keeps what was said. Whole or nothing: if any pick is refused, nothing changes.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from po_dashboard.auth.service import audit
from po_dashboard.db.base import utcnow
from po_dashboard.db.ledger import POLine, PurchaseOrder
from po_dashboard.db.models import AppUser
from po_dashboard.ledger.errors import LedgerError, text
from po_dashboard.ledger.status import line_states

ACTIONS = {"received": "Received", "short": "Closed short", "cancelled": "Cancelled"}


def close_lines(
    db: Session,
    order: PurchaseOrder,
    picks: dict[int, str],
    reason: str | None,
    actor: AppUser | None = None,
    client_address: str | None = None,
) -> int:
    """Close the picked open lines of this PO. `picks` maps a line id to received, short or cancelled."""
    why = text(reason, "Reason", longest=500, required=True) or ""
    chosen = {line_id: action for line_id, action in picks.items() if action}
    if not chosen:
        raise LedgerError("Choose what happened to at least one line.")
    lines = {line.id: line for line in db.scalars(select(POLine).where(POLine.po_id == order.id))}
    states = line_states(db, order.id)
    for line_id, action in chosen.items():
        line = lines.get(line_id)
        if line is None:
            raise LedgerError("One of the lines does not belong to this purchase order.")
        if action not in ACTIONS:
            raise LedgerError(f"Line {line.line_no}: '{action[:20]}' is not one of the choices.")
        if states[line_id].status.done:
            raise LedgerError(f"Line {line.line_no} is already closed.")
    now = utcnow()
    for line_id, action in chosen.items():
        line = lines[line_id]
        line.closed_reason, line.closed_at, line.closed_by, line.close_note = (
            action,
            now,
            actor.id if actor else None,
            why,
        )
        audit(db, "line.close", actor, f"po_line:{line_id}", client_address,
              before={"status": states[line_id].status.value}, after={"closed": action, "reason": why})  # fmt: skip
    db.flush()
    return len(chosen)


def reopen_line(
    db: Session,
    order: PurchaseOrder,
    line_id: int,
    reason: str | None,
    actor: AppUser | None = None,
    client_address: str | None = None,
) -> None:
    """Take back a hand-close. A line closed by its invoices stays closed: only the hand-close is removed."""
    why = text(reason, "Reason", longest=500, required=True) or ""
    line = db.get(POLine, line_id)
    if line is None or line.po_id != order.id:
        raise LedgerError("That line was not found on this purchase order.")
    if line.closed_reason is None:
        raise LedgerError(f"Line {line.line_no} was not closed by hand.")
    before = {"closed": line.closed_reason, "reason": line.close_note}
    line.closed_reason = line.closed_at = line.closed_by = line.close_note = None
    db.flush()
    audit(db, "line.reopen", actor, f"po_line:{line_id}", client_address, before=before, after={"reason": why})
