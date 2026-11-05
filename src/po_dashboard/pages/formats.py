"""How exact numbers are shown: no trailing zeros on a quantity, and at least two places on a price."""

from __future__ import annotations

from decimal import Decimal


def quantity(value: Decimal | None) -> str:
    if value is None:
        return ""
    return format(value.normalize(), "f")


def money(value: Decimal | None, currency: str = "") -> str:
    if value is None:
        return ""
    shown = value.normalize()
    if shown.as_tuple().exponent > -2:  # type: ignore[operator]
        shown = shown.quantize(Decimal("0.01"))
    return f"{format(shown, 'f')} {currency}".strip()
