"""One error type for anything a person typed wrong. Its text is written for that person and is safe to show."""

from __future__ import annotations

import re
from datetime import date
from decimal import Decimal

from po_dashboard.db.types import Money, Quantity

CONTROL = re.compile(r"[\x00-\x1f\x7f]")
ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
US_DATE = re.compile(r"(\d{1,2})/(\d{1,2})/(\d{4})")  # as QuickBooks writes it: month/day/year (DEC-45)


class LedgerError(ValueError):
    """The input is not acceptable. The message names the field and says what is wrong."""


def text(value: str | None, field: str, *, longest: int, required: bool = False, multiline: bool = False) -> str | None:
    cleaned = (value or "").strip()
    if not cleaned:
        if required:
            raise LedgerError(f"{field} is needed.")
        return None
    if len(cleaned) > longest:
        raise LedgerError(f"{field} is longer than {longest} characters.")
    if CONTROL.search(cleaned.replace("\n", "").replace("\r", "")) if multiline else CONTROL.search(cleaned):
        raise LedgerError(f"{field} has a character that cannot be kept (a line break or control character).")
    return cleaned


def number(value: str | None, field: str, kind: type[Quantity] | type[Money], *, positive: bool) -> Decimal:
    """A decimal typed with digits and a dot. More places than the column keeps is refused, never rounded."""
    raw = (value or "").strip()
    if not raw:
        raise LedgerError(f"{field} is needed.")
    try:
        parsed = Decimal(raw)
    except ArithmeticError:
        raise LedgerError(f"{field}: '{raw[:30]}' is not a number. Use digits and a dot, like 1250.50.") from None
    try:
        kind().to_stored(parsed)  # refuses NaN, infinity, too many places, too large
    except ValueError as problem:
        raise LedgerError(f"{field}: {problem}.") from None
    if positive and parsed <= 0:
        raise LedgerError(f"{field} must be more than zero.")
    if not positive and parsed < 0:
        raise LedgerError(f"{field} cannot be negative.")
    return parsed


def when(value: str | None, field: str) -> date | None:
    raw = (value or "").strip()
    if not raw:
        return None
    us = US_DATE.fullmatch(raw)
    if not us and not ISO_DATE.fullmatch(raw):
        raise LedgerError(f"{field}: '{raw[:30]}' is not a date. Write it as month/day/year, like 12/31/2026.")
    try:
        parsed = date(int(us[3]), int(us[1]), int(us[2])) if us else date.fromisoformat(raw)
    except ValueError:
        raise LedgerError(f"{field}: '{raw}' is not a real date.") from None
    if not 1990 <= parsed.year <= 2100:
        raise LedgerError(f"{field}: the year {parsed.year} looks wrong.")
    return parsed
