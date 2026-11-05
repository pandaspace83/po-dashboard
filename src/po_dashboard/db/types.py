"""Exact decimals for quantities and money (DEC-24).

SQLite has no exact decimal type and SQLAlchemy's Numeric goes through floats on SQLite. So Python holds `Decimal`
and the database holds a scaled integer: quantities x 10,000 (four decimal places), prices and amounts x 1,000,000
(six). SQL can still add them up. A value with more places than the scale allows is refused, never rounded; a float
is refused because it is already inexact.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from sqlalchemy.engine import Dialect
from sqlalchemy.types import Integer, TypeDecorator

SQLITE_INTEGER_MAX = 2**63 - 1


class ScaledDecimal(TypeDecorator[Decimal]):
    """A Decimal stored as an integer scaled by 10 ** places."""

    impl = Integer
    cache_ok = True
    places = 0

    def process_bind_param(self, value: Any, dialect: Dialect) -> int | None:
        if value is None:
            return None
        if isinstance(value, bool) or not isinstance(value, Decimal | int):
            raise TypeError(f"use Decimal or int, not {type(value).__name__}: a float is not exact")
        number = Decimal(value)
        if not number.is_finite():
            raise ValueError("a number is needed, not NaN or infinity")
        scaled = number.scaleb(self.places)
        if scaled != scaled.to_integral_value():
            raise ValueError(f"at most {self.places} decimal places are kept; nothing is rounded")
        result = int(scaled)
        if abs(result) > SQLITE_INTEGER_MAX:
            raise ValueError("the number is too large")
        return result

    def process_result_value(self, value: int | None, dialect: Dialect) -> Decimal | None:
        return None if value is None else Decimal(value).scaleb(-self.places)


class Quantity(ScaledDecimal):
    """Four decimal places."""

    places = 4


class Money(ScaledDecimal):
    """Six decimal places, for unit prices and amounts. The currency is on the purchase order."""

    places = 6
