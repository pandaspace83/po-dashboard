"""The keys that carry "unique, ignoring case" (DEC-57, DEC-59): how a name or a PO number is compared. Never fuzzy: two
values are the same only when these keys are equal."""

from __future__ import annotations


def name_key(name: str) -> str:
    """A customer or QuickBooks name: lower case, runs of spaces squeezed."""
    return " ".join(name.lower().split())


def po_key(number: str) -> str:
    """A customer's PO number as letters and digits only, lower case, a leading "PO" ignored: PO-1001, po 1001 and 1001
    are one order. A number that is only "PO" keeps it, so no key is empty."""
    key = "".join(ch for ch in number.lower() if ch.isalnum())
    return key[2:] if key.startswith("po") and len(key) > 2 else key
