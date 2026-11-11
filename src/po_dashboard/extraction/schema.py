"""Extraction schema v1 (SCORING.md §2). The model must return this shape, and each golden answer file holds it.

Numbers are read as exact decimals, never floats. Every field is present; a field that does not apply is null."""

from __future__ import annotations

import json
from decimal import Decimal
from typing import Any

SCHEMA_VERSION = 1

DOC_TYPES = ("purchase_order", "order_acknowledgement", "shipment_notice", "invoice", "other")
CARRIERS = ("UPS", "FedEx", "USPS", "DHL", "other")
CHARGE_KINDS = ("freight", "tariff", "tax", "other")

_TEXT = {"type": ["string", "null"]}
_NUMBER = {"type": ["number", "null"]}

LINE_FIELDS = ("line_no", "item", "qty", "unit", "unit_price", "promised_date", "arrival_date", "requested_date")
TOP_FIELDS = (
    "doc_type", "po_number", "vendor_order_number", "invoice_number", "invoice_date", "carrier",
    "tracking_numbers", "ship_date", "arrival_date", "lines", "charges", "total",
)

EXTRACTION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "doc_type": {"type": "string", "enum": list(DOC_TYPES)},
        "po_number": _TEXT,
        "vendor_order_number": _TEXT,
        "invoice_number": _TEXT,
        "invoice_date": _TEXT,
        "carrier": {"type": ["string", "null"], "enum": [*CARRIERS, None]},
        "tracking_numbers": {"type": "array", "items": {"type": "string"}},
        "ship_date": _TEXT,
        "arrival_date": _TEXT,
        "lines": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "line_no": {"type": ["integer", "null"]},
                    "item": _TEXT,
                    "qty": _NUMBER,
                    "unit": _TEXT,
                    "unit_price": _NUMBER,
                    "promised_date": _TEXT,
                    "arrival_date": _TEXT,
                    "requested_date": _TEXT,
                },
                "required": list(LINE_FIELDS),
            },
        },
        "charges": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "kind": {"type": "string", "enum": list(CHARGE_KINDS)},
                    "label": {"type": "string"},
                    "amount": {"type": "number"},
                },
                "required": ["kind", "label", "amount"],
            },
        },
        "total": _NUMBER,
    },
    "required": list(TOP_FIELDS),
}


class NotAnAnswer(ValueError):
    """The model's reply is not JSON in the shape of schema v1. The message never holds document text."""


def parse_answer(raw: str) -> dict[str, Any]:
    """Read a reply as JSON with exact decimals. Raises NotAnAnswer if it is not an object."""
    try:
        value = json.loads(raw, parse_float=Decimal, parse_int=Decimal)
    except ValueError as problem:
        raise NotAnAnswer("the reply is not JSON") from problem
    if not isinstance(value, dict):
        raise NotAnAnswer("the reply is not a JSON object")
    # line_no is a whole number, not a Decimal, once read
    for line in value.get("lines") or []:
        if isinstance(line, dict) and isinstance(line.get("line_no"), Decimal):
            line["line_no"] = int(line["line_no"])
    return value
