"""A person's decisions about matches (DEC-59, DEC-62, REQ-CO-13).

**Link:** this invoice belongs to this PO (and, for one line, this invoice line to this PO line; for an invoice with
only a total or a credit memo, how much of it). **Reject:** this invoice does not belong to this PO, so no rule puts it
there again. **Withdraw:** take a decision back. Each is audited with before and after and re-runs the reconciliation; a
re-run never undoes one.

A reason is required for a rejection, and for a link to a PO the invoice itself does not name (an override). Linking an
invoice to a PO it names needs none. The app's own matches cannot be withdrawn: they follow the rules, and a person
changes them by deciding."""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from po_dashboard.auth.service import audit
from po_dashboard.db.base import utcnow
from po_dashboard.db.customers import Allocation, CustomerPO, CustomerPOLine, SalesInvoice, SalesInvoiceLine
from po_dashboard.db.models import AppUser
from po_dashboard.db.types import Money
from po_dashboard.ledger.errors import LedgerError, text
from po_dashboard.quickbooks.reconciliation import store
from po_dashboard.quickbooks.reconciliation.engine import reference_keys

IN_FORCE = ("active", "closed", "cancelled")


def _shown(row: Allocation) -> dict[str, object]:
    return {
        "invoice": row.sales_invoice_id, "invoice_line": row.sales_invoice_line_id, "po": row.customer_po_id,
        "po_line": row.customer_po_line_id, "state": row.state,
        "amount": None if row.amount is None else str(row.amount),
        "reason": row.reason,
    }  # fmt: skip


def _pair(db: Session, invoice: SalesInvoice, order: CustomerPO) -> None:
    if order.state not in IN_FORCE:
        raise LedgerError("Decide against the revision of the PO that is in force, not a pending or earlier one.")
    if invoice.customer_id is None:
        raise LedgerError("Choose the invoice's customer first, under Sales invoices.")
    if invoice.customer_id != order.customer_id:
        raise LedgerError("The invoice is for a different customer than the PO. Correct the customer first.")


def _names_it(invoice: SalesInvoice, order: CustomerPO) -> bool:
    return order.po_key in {key for key, _ in reference_keys(invoice.po_reference)}


def link(
    db: Session, invoice: SalesInvoice, order: CustomerPO, *, invoice_line_id: int | None = None,
    po_line_id: int | None = None, amount: Decimal | None = None, reason: str | None = None,
    actor: AppUser | None = None, client_address: str | None = None,
) -> Allocation:  # fmt: skip
    """Say the invoice (or one of its lines) belongs to the PO (or one of its lines). The caller commits."""
    _pair(db, invoice, order)
    if invoice.currency != order.currency:
        raise LedgerError(
            f"The invoice is in {invoice.currency} and the PO in {order.currency}. Currencies are never converted."
        )
    if (invoice_line_id is None) != (po_line_id is None):
        raise LedgerError("Choose both lines, the invoice's and the PO's, or neither.")
    if invoice_line_id is not None:
        if invoice.kind == "credit_memo" or invoice.level != "line":
            raise LedgerError("This invoice has no lines to match one by one; link it as a whole.")
        line = db.get(SalesInvoiceLine, invoice_line_id)
        target = db.get(CustomerPOLine, po_line_id)
        if line is None or line.invoice_id != invoice.id or target is None or target.customer_po_id != order.id:
            raise LedgerError("Those lines do not belong to this invoice and this PO.")
        taken = db.scalar(select(Allocation).where(
            Allocation.basis == "person", Allocation.state == "confirmed", Allocation.sales_invoice_line_id == line.id
        ))  # fmt: skip
        if taken is not None:
            raise LedgerError("A person already matched that invoice line. Withdraw that decision first.")
    if amount is not None:
        if invoice.kind != "credit_memo" and invoice.level != "invoice":
            raise LedgerError("An amount is given only for an invoice with just a total, or a credit memo.")
        try:
            Money().to_stored(amount)
        except ValueError:
            raise LedgerError("The amount has more than six decimal places.") from None
        total = invoice.total or Decimal(0)
        if amount == 0 or (amount > 0) != (total > 0) or abs(amount) > abs(total):
            raise LedgerError("The amount must be part of the invoice's total, with the same sign.")
    existing = db.scalar(select(Allocation).where(
        Allocation.basis == "person", Allocation.sales_invoice_id == invoice.id, Allocation.customer_po_id == order.id,
        Allocation.sales_invoice_line_id.is_(None) if invoice_line_id is None
        else Allocation.sales_invoice_line_id == invoice_line_id,
    ))  # fmt: skip
    if existing is not None:
        raise LedgerError("A person already decided about this invoice and this PO. Withdraw that decision first.")
    why = text(reason, "Reason", longest=500, required=not _names_it(invoice, order))
    row = Allocation(
        customer_po_id=order.id, customer_po_line_id=po_line_id, sales_invoice_id=invoice.id,
        sales_invoice_line_id=invoice_line_id, amount=amount, basis="person", state="confirmed",
        decided_by=actor.id if actor else None, decided_at=utcnow(), reason=why or None,
    )  # fmt: skip
    db.add(row)
    db.flush()
    audit(db, "reconciliation.link", actor, f"allocation:{row.id}", client_address, after=_shown(row))
    store.run(db, cause="person", cause_ref=row.id)
    return row


def reject(
    db: Session, invoice: SalesInvoice, order: CustomerPO, *, reason: str | None,
    actor: AppUser | None = None, client_address: str | None = None,
) -> Allocation:  # fmt: skip
    """Say the invoice does not belong to the PO. A reason is always needed. The caller commits."""
    _pair(db, invoice, order)
    why = text(reason, "Reason", longest=500, required=True)
    existing = db.scalar(select(Allocation).where(
        Allocation.basis == "person", Allocation.sales_invoice_id == invoice.id, Allocation.customer_po_id == order.id,
    ))  # fmt: skip
    if existing is not None:
        raise LedgerError("A person already decided about this invoice and this PO. Withdraw that decision first.")
    row = Allocation(
        customer_po_id=order.id, sales_invoice_id=invoice.id, basis="person", state="rejected",
        decided_by=actor.id if actor else None, decided_at=utcnow(), reason=why,
    )  # fmt: skip
    db.add(row)
    db.flush()
    audit(db, "reconciliation.reject", actor, f"allocation:{row.id}", client_address, after=_shown(row))
    store.run(db, cause="person", cause_ref=row.id)
    return row


def withdraw(
    db: Session, row: Allocation, *, reason: str | None = None, actor: AppUser | None = None,
    client_address: str | None = None,
) -> None:  # fmt: skip
    """Take a person's decision back; the rules then apply again. The caller commits."""
    if row.basis != "person":
        raise LedgerError("The app's own matches follow the rules; a person changes them by deciding, not withdrawing.")
    why = text(reason, "Reason", longest=500, required=False)
    before, ref = _shown(row), row.id
    db.delete(row)
    db.flush()
    audit(db, "reconciliation.withdraw", actor, f"allocation:{ref}", client_address, before=before,
          after={"withdrawn": True, "reason": why or None})  # fmt: skip
    store.run(db, cause="person", cause_ref=ref)
