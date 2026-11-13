"""Matching a document's lines to its PO's lines (ARCHITECTURE section 6.2, DEC-54). Exactly or not at all, never
"close": a line matches the PO line with the company line number the document states; else the one PO line whose item
is the same, ignoring case and spaces; else it is left for a person to assign.

The validator needs the line rule for its flags (PD-4.2). Finding the PO and the vendor comes with PD-4.3."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any


@dataclass(frozen=True)
class PoLine:
    """One line of the matched PO, as the validator sees it. The caller works out `open` for the kind of document:
    for a shipment notice, what is not yet shipped; for an invoice, what is not yet billed; for an acknowledgement, the
    ordered quantity (it restates the whole line). None means nothing is recorded yet: the ordered quantity."""

    line_no: int
    item: str | None
    ordered: Decimal
    open: Decimal | None = None
    unit: str | None = None
    unit_price: Decimal | None = None
    promised_date: date | None = None  # the last accepted one, if any
    closed: bool = False  # a person closed the line by hand: it is not "open" for `lines_not_covered`

    @property
    def still_open(self) -> Decimal:
        return self.ordered if self.open is None else self.open


def item_key(item: Any) -> str:
    """An item as compared: upper case, with no spaces."""
    return re.sub(r"\s+", "", str(item)).upper() if item is not None else ""


def match_lines(doc_lines: Sequence[Mapping[str, Any]], po_lines: Sequence[PoLine]) -> list[int | None]:
    """For each document line, the PO line number it matches, or None (`line_unassigned`)."""
    numbers = {line.line_no for line in po_lines}
    found: list[int | None] = []
    for line in doc_lines:
        stated = line.get("line_no")
        if isinstance(stated, int) and not isinstance(stated, bool) and stated in numbers:
            found.append(stated)
            continue
        key = item_key(line.get("item"))
        same = [po.line_no for po in po_lines if key and item_key(po.item) == key]
        found.append(same[0] if len(same) == 1 else None)
    return found
