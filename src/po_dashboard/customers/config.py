"""Settings for customer orders (DEC-59): the defaults, and typed reading of the `setting` table. A value in the table
wins; with no row the default applies, so nothing breaks if a row is missing."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

from sqlalchemy.orm import Session

from po_dashboard.db.customers import Setting

DEFAULTS: dict[str, str] = {
    "amount_tolerance": "0.01",  # a price or amount may differ by this much and still match (OPEN-23: assumed)
    "quantity_tolerance": "0",  # quantities must match exactly
    "unbilled_days": "30",  # a PO with no invoice is "possibly unbilled" only after this many days (owner, 2026-11-12)
    "charge_items": "shipping,freight,handling,fuel surcharge,tax,sales tax,discount",  # not goods (DEC-55, DEC-58)
    "default_currency": "USD",  # when an export has no currency column (DEC-41)
    "customer_job_split": "1",  # "Customer:Job" counts as the customer before the colon (OPEN-24: assumed)
}


def text(db: Session, key: str) -> str:
    row = db.get(Setting, key)
    return row.value if row is not None else DEFAULTS[key]


def decimal(db: Session, key: str) -> Decimal:
    """A number setting; a damaged value falls back to the default rather than stopping reconciliation."""
    try:
        value = Decimal(text(db, key))
        return value if value.is_finite() and value >= 0 else Decimal(DEFAULTS[key])
    except InvalidOperation:
        return Decimal(DEFAULTS[key])


def whole(db: Session, key: str) -> int:
    value = decimal(db, key)
    return int(value) if value == value.to_integral_value() else int(Decimal(DEFAULTS[key]))


def flag(db: Session, key: str) -> bool:
    return text(db, key).strip().lower() in ("1", "true", "yes", "on")


def names(db: Session, key: str) -> frozenset[str]:
    """A list setting, comma separated, compared lower case."""
    return frozenset(part.strip().lower() for part in text(db, key).split(",") if part.strip())
