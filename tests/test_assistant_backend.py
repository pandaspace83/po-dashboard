"""The assistant's read interface and its synthetic adapter (PD-9.1, ARCHITECTURE §17.3, §17.4; DEC-64, DEC-13): fixed
read functions over made-up records, found only by an equal key, with nothing that writes."""

from __future__ import annotations

import ast
import inspect
import itertools
import json
import re
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import get_args

import pytest
from pydantic import ValidationError

from po_dashboard.assistant.backend import (
    DEFAULT_FIXTURES,
    Fixtures,
    LineRow,
    RecordsBackend,
    Show,
    SyntheticBackend,
)
from po_dashboard.ledger.expected import expectation
from po_dashboard.ledger.reports import SHOWS, OpenItem

ASSISTANT = Path(__file__).resolve().parent.parent / "src" / "po_dashboard" / "assistant"


@pytest.fixture(scope="module")
def backend() -> SyntheticBackend:
    return SyntheticBackend.from_file()


def raw() -> dict:  # type: ignore[type-arg]
    return json.loads(DEFAULT_FIXTURES.read_text(encoding="utf-8"))  # type: ignore[no-any-return]


def test_the_fixtures_are_in_tests_data_and_made_up() -> None:
    assert DEFAULT_FIXTURES.parent.parts[-3:] == ("tests", "data", "assistant")
    text = DEFAULT_FIXTURES.read_text(encoding="utf-8")
    addresses = re.findall(r"[\w.+-]+@([\w.-]+)", text)
    assert addresses and all(domain.endswith("example.com") for domain in addresses)


def test_the_synthetic_adapter_is_a_records_backend(backend: SyntheticBackend) -> None:
    assert isinstance(backend, RecordsBackend)
    assert backend.as_of() == date(2026, 11, 13)


def test_a_po_is_found_only_by_its_number_never_a_near_one(backend: SyntheticBackend) -> None:
    po = backend.po_status(" po-1005 ")
    assert po is not None and po.po_number == "PO-1005" and po.status == "Open"
    assert po.vendor_contact == "orders@acme.example.com"
    assert [line.flags for line in po.lines] == [("3 days late", "promised date slipped"), ()]
    assert po.lines[1].expected == date(2026, 11, 18)  # the shipment estimate outranks the promised date
    for near in ("1005", "PO-10055", "PO-1006", "PO 1005", ""):
        assert backend.po_status(near) is None
    closed = backend.po_status("PO-1015")
    assert closed is not None and closed.status == "Closed"


@pytest.mark.parametrize(
    ("show", "expected"),
    [
        ("all", [("PO-1005", 1), ("PO-1005", 2), ("PO-1007", 1), ("PO-1012", 1), ("PO-1020", 1)]),
        ("attention", [("PO-1005", 1), ("PO-1007", 1), ("PO-1012", 1), ("PO-1020", 1)]),
        ("late", [("PO-1005", 1), ("PO-1020", 1)]),
        ("no_ack", [("PO-1007", 1)]),
        ("slipped", [("PO-1005", 1)]),
        ("needs_update", [("PO-1012", 1)]),
    ],
)
def test_open_items_filter_as_the_report_does(backend: SyntheticBackend, show: Show, expected: list[object]) -> None:
    assert [(r.po_number, r.line_no) for r in backend.open_items(show)] == expected


def test_open_items_by_vendor_compare_names_as_the_app_does(backend: SyntheticBackend) -> None:
    rows = backend.open_items("all", vendor="  ACME   fasteners ")
    assert [(r.po_number, r.line_no) for r in rows] == [("PO-1005", 1), ("PO-1005", 2), ("PO-1007", 1)]
    assert backend.open_items(vendor="Acme") == []  # never a near match
    assert all(r.po_number != "PO-1015" for r in backend.open_items())  # a closed PO has no open items


def test_the_show_choices_are_the_open_items_pages() -> None:
    assert get_args(Show) == tuple(SHOWS)


def test_line_flags_and_next_step_are_the_reports_own_words() -> None:
    history_slipped = (date(2026, 11, 3), date(2026, 11, 10))
    history_kept = (date(2026, 11, 10),)
    for days_late, no_ack, history, needs_update in itertools.product(
        (0, 1, 3), (False, True), (history_kept, history_slipped), (False, True)
    ):
        promised = history[-1]
        today = date.fromordinal(promised.toordinal() + days_late)
        line = LineRow(line_no=1, item="Hex bolt M8", unit="EA", ordered=Decimal(5), open_qty=Decimal(5),
                       status="Acknowledged", promised_history=history, days_late=days_late, no_ack=no_ack,
                       needs_update=needs_update)  # fmt: skip
        item = OpenItem(po_id=1, po_number="PO-1005", vendor="Acme Fasteners", line_no=1, item="Hex bolt M8",
                        description="", customer="", unit="EA", ordered=Decimal(5), open_qty=Decimal(5),
                        status="Acknowledged", expectation=expectation(promised, None, today),
                        slipped=history is history_slipped, no_ack=no_ack, needs_update=needs_update,
                        last_activity=None)  # fmt: skip
        assert list(line.flags) == item.flags
        assert line.next_step == item.next_step


def test_a_customer_po_number_can_belong_to_two_customers(backend: SyntheticBackend) -> None:
    both = backend.customer_orders("cp 2002")
    assert [(o.customer, o.status_words) for o in both] == [
        ("Northwind Racing", "Over-invoiced"),
        ("Tailspin Motorsport", "No invoice in the uploaded files"),
    ]
    one = backend.customer_orders("CP-2002", customer="tailspin motorsport")
    assert len(one) == 1 and one[0].customer_contact is None
    assert backend.customer_orders("2002") == []  # never a near match
    assert [o.number for o in backend.customer_orders(customer="Northwind Racing")] == ["CP-2001", "CP-2002"]


def test_exceptions_keep_a_confirmed_discrepancy_apart_from_preliminary_alerts(backend: SyntheticBackend) -> None:
    confirmed = backend.exceptions(level="confirmed")
    assert [(e.label, e.level_words, e.customer_po) for e in confirmed] == [
        ("Quantity mismatch", "Confirmed discrepancy", "CP-2002")
    ]
    preliminary = backend.exceptions(level="preliminary")
    assert {e.level_words for e in preliminary} == {"Preliminary alert"} and len(preliminary) == 3
    assert [e.code for e in backend.exceptions(customer="Tailspin Motorsport", customer_po="cp2002")] == [
        "possibly_unbilled"
    ]
    assert [e.invoice for e in backend.exceptions(customer_po="CP-2001")] == ["3002"]


def test_uploads_come_newest_first_with_the_days_they_cover(backend: SyntheticBackend) -> None:
    uploads = backend.uploads()
    assert [u.file_name for u in uploads.uploads] == ["sales_sep15_oct.csv", "sales_aug.csv"]
    assert uploads.latest is not None and uploads.latest.file_name == "sales_sep15_oct.csv"
    assert uploads.covered == [(date(2026, 8, 1), date(2026, 8, 31)), (date(2026, 9, 15), date(2026, 10, 31))]
    assert uploads.first_gap(date(2026, 8, 1), date(2026, 10, 31)) == (date(2026, 9, 1), date(2026, 9, 14))
    assert uploads.first_gap(date(2026, 9, 15), date(2026, 10, 31)) is None


def test_fixtures_that_say_what_the_app_never_would_are_refused() -> None:
    def broken(change: str) -> dict:  # type: ignore[type-arg]
        data = raw()
        line = data["pos"][0]["lines"][0]
        if change == "days_late":
            line["days_late"] = 2
        elif change == "closed_with_open_qty":
            line["status"] = "Closed"
        elif change == "late_without_a_date":
            line["promised_history"] = []
        elif change == "same_po_twice":
            data["pos"][1]["po_number"] = "po-1005"
        elif change == "same_customer_po_twice":
            data["customer_orders"][1]["number"] = "CP 2001"
        elif change == "unknown_status":
            data["customer_orders"][0]["status"] = "fine"
        elif change == "unknown_code":
            data["exceptions"][0]["code"] = "looks_odd"
        elif change == "half_a_period":
            data["uploads"][0]["period_to"] = None
        elif change == "unknown_field":
            line["sql"] = "DELETE FROM po_line"
        return data

    assert Fixtures.model_validate(raw())
    for change in (
        "days_late",
        "closed_with_open_qty",
        "late_without_a_date",
        "same_po_twice",
        "same_customer_po_twice",
        "unknown_status",
        "unknown_code",
        "half_a_period",
        "unknown_field",
    ):
        with pytest.raises(ValidationError):
            Fixtures.model_validate(broken(change))


def test_the_backend_only_reads(backend: SyntheticBackend) -> None:
    reads = {"as_of", "po_status", "open_items", "customer_orders", "exceptions", "uploads"}
    interface = {n for n, _ in inspect.getmembers(RecordsBackend, inspect.isfunction) if not n.startswith("_")}
    assert interface == reads
    public = {n for n, _ in inspect.getmembers(SyntheticBackend) if not n.startswith("_")}
    assert public == reads | {"from_file"}
    po = backend.po_status("PO-1005")
    assert po is not None
    with pytest.raises(ValidationError):
        po.lines[0].open_qty = Decimal(0)  # type: ignore[misc]
    rows = backend.open_items()
    rows.clear()  # a caller's list is its own: the records are untouched
    assert len(backend.open_items()) == 5


# What the assistant may never import: anything that writes records, reads mail or files documents (ARCHITECTURE §17.4).
NEVER_IMPORTED = (
    "po_dashboard.mail", "po_dashboard.intake", "po_dashboard.extraction.accept", "po_dashboard.extraction.runner",
    "po_dashboard.quickbooks.importers", "po_dashboard.quickbooks.reconciliation.store",
    "po_dashboard.quickbooks.reconciliation.decisions", "po_dashboard.ledger.events", "po_dashboard.ledger.closing",
    "po_dashboard.ledger.orders", "po_dashboard.ledger.vendor_import", "po_dashboard.ledger.quickbooks",
    "po_dashboard.customers.intake", "po_dashboard.customers.service", "po_dashboard.customers.po_import",
    "po_dashboard.auth", "po_dashboard.db.migrate", "sqlite3",
)  # fmt: skip


def test_the_assistant_imports_nothing_that_writes_mails_or_files_documents() -> None:
    found = sorted(ASSISTANT.rglob("*.py"))
    assert len(found) >= 3
    for path in found:
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module] + [f"{node.module}.{a.name}" for a in node.names]
            for name in names:
                bad = [n for n in NEVER_IMPORTED if name == n or name.startswith(n + ".")]
                assert not bad, f"{path.name} imports {name}"
