"""The QuickBooks starting load (DEC-46): the Open POs report as exported, read safely, previewed, then created.

The file in tests/data is made up, with the shape of the owner's real report: item headings and "Total" rows, a PO's
lines spread over several item groups, a line partly received, a price that does not divide exactly, a customer name
with an accent (Windows-1252), the same item twice on one PO with different memos, and a vendor not in the app.
"""

import io
import re
import time
from datetime import date
from decimal import Decimal as D
from pathlib import Path

import openpyxl
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from po_dashboard.db import ledger as L
from po_dashboard.db.models import AuditLog
from po_dashboard.ledger import events, reports
from po_dashboard.ledger import quickbooks as qb
from po_dashboard.ledger.errors import LedgerError
from po_dashboard.ledger.sheets import read_table
from po_dashboard.ledger.status import line_states
from po_dashboard.pages import quickbooks as qb_page
from test_gate import ORIGIN, PW, env, new_client, sign_in, token_in  # noqa: F401
from test_orders_web import client, make_vendor  # noqa: F401

SAMPLE = Path(__file__).parent / "data" / "quickbooks_open_pos_110526.csv"
NAME = "Open POs 110526 Sample.CSV"
EXPORTED = date(2026, 11, 5)


def raw() -> bytes:
    return SAMPLE.read_bytes()


@pytest.fixture
def vendors(client: TestClient) -> TestClient:
    make_vendor(client, "Acme Fasteners", "acme.example")
    make_vendor(client, "NORTHWIND METALS", "northwind.example")  # another case: still the same vendor
    return client


def plan(env, data: bytes | None = None, name: str = NAME, exported: date | None = None) -> qb.LoadPlan:  # noqa: ANN001
    with Session(env[1]) as db:
        return qb.plan_load(db, raw() if data is None else data, name, exported)


def load(env, data: bytes | None = None) -> qb.LoadResult:  # noqa: ANN001
    with Session(env[1]) as db:
        result = qb.apply_load(db, qb.plan_load(db, raw() if data is None else data, NAME))
        db.commit()
        return result


# ---- reading the report


def test_the_export_date_comes_from_the_file_name() -> None:
    assert qb.export_date_from_name("Open POs 110526 Project.CSV") == date(2026, 11, 5)
    assert qb.export_date_from_name("open-pos-123126.xlsx") == date(2026, 12, 31)
    assert qb.export_date_from_name("Open POs Project.CSV") is None
    assert qb.export_date_from_name("Open POs 133226.csv") is None  # not a real date
    assert qb.export_date_from_name("PO 1105260.csv") is None  # seven digits are not a date


@pytest.mark.parametrize(
    ("amount", "qty", "price", "rounded"),
    [("25.00", "100", "0.25", False), ("10.00", "3", "3.3333", True), ("20.00", "3", "6.6667", True),
     ("1.234567", "1", "1.234567", False), ("500.00", "10", "50", False)],
)  # fmt: skip
def test_a_unit_price_is_exact_or_rounded_to_four_places(amount: str, qty: str, price: str, rounded: bool) -> None:
    assert qb.unit_price(D(amount), D(qty)) == (D(price), rounded)


def test_the_plan_reads_lines_and_skips_headings_and_totals(env, vendors: TestClient) -> None:  # noqa: ANN001
    found = plan(env)
    assert not found.problems and found.export_date == EXPORTED and found.ready
    assert [o.po_number for o in found.create] == ["5001", "5002", "5004"]
    assert [o.po_number for o in found.waiting] == ["5003"] and found.waiting_vendors == ["Globex Wire"]
    first = found.create[0]
    assert [(line.line_no, line.item) for line in first.lines] == [(1, "Bolts:M8"), (2, "Nuts:M8"), (3, "Washers:M8")]
    assert first.lines[2].qty == D("1000") and first.lines[2].amount == D("1000.00")  # "1,000.00" read as a number
    assert first.po_date == date(2026, 10, 20) and first.vendor_name == "Acme Fasteners"
    assert [(o.po_number, line.item, line.price) for o, line in found.rounded] == [("5001", "Nuts:M8", D("3.3333"))]
    assert [(o.po_number, line.received) for o, line in found.received] == [("5002", D("4"))]
    assert found.create[1].lines[0].customer == "Café Racing" and first.lines[0].customer is None
    assert [line.memo for line in found.create[2].lines] == ["Red, 10 ft", "Blue, 10 ft"]
    assert "Other 1" in found.ignored_columns and "Print" in found.ignored_columns


def test_an_excel_export_gives_the_same_plan(env, vendors: TestClient) -> None:  # noqa: ANN001
    book = openpyxl.Workbook()
    sheet = book.active
    assert sheet is not None
    for cells in read_table(raw(), NAME):
        sheet.append(cells)
    out = io.BytesIO()
    book.save(out)
    from_excel = plan(env, out.getvalue(), "Open POs 110526 Sample.xlsx")
    from_csv = plan(env)
    assert [(o.po_number, [(x.item, x.qty, x.price) for x in o.lines]) for o in from_excel.create] == [
        (o.po_number, [(x.item, x.qty, x.price) for x in o.lines]) for o in from_csv.create
    ]


def test_a_file_that_is_not_the_report_is_refused_naming_what_is_missing(env) -> None:  # noqa: ANN001
    with pytest.raises(qb.QuickBooksRejected, match="'num'.*'source name'"):
        plan(env, b"po_number,vendor,currency\nPO-1,Acme,USD\n", "pos.csv")


def _with(cells_change: dict[int, str], row: int = 2) -> bytes:
    """The sample with some cells of one data row changed (row 2 of the file is the first PO line, 5001 Bolts)."""
    table = read_table(raw(), NAME)
    for index, value in cells_change.items():
        table[row][index] = value
    out = io.StringIO()
    import csv

    csv.writer(out).writerows(table)
    return out.getvalue().encode("utf-8")


@pytest.mark.parametrize(
    ("change", "message"),
    [({8: "lots"}, "Qty: 'lots' is not a number"), ({8: "0"}, "Qty must be more than zero"),
     ({9: "200"}, "is more than Qty"), ({12: "-5"}, "Amount cannot be negative"), ({1: "soon"}, "is not a date"),
     ({5: "Northwind Metals"}, "disagree on the vendor"), ({8: "1.00001"}, "at most 4 decimal places")],
)  # fmt: skip
def test_a_bad_row_is_listed_and_nothing_can_be_created(env, vendors: TestClient, change: dict, message: str) -> None:  # noqa: ANN001
    found = plan(env, _with(change, row=2 if 5 not in change else 5))
    assert any(message in problem.message for problem in found.problems), found.problems
    assert not found.ready
    with Session(env[1]) as db, pytest.raises(LedgerError, match="nothing was created"):
        qb.apply_load(db, found)


# ---- creating


def test_the_load_creates_the_pos_as_the_owner_decided(env, vendors: TestClient) -> None:  # noqa: ANN001
    result = load(env)
    assert result.created == ["5001", "5002", "5004"] and result.lines == 6 and result.waiting == 1
    assert (result.received_lines, result.rounded_lines) == (1, 1)
    with Session(env[1]) as db:
        order = db.scalars(select(L.PurchaseOrder).where(L.PurchaseOrder.po_number == "5001")).one()
        assert (order.source, order.currency, order.date_sent, order.po_date) == ("quickbooks", "USD", None,
                                                                                  date(2026, 10, 20))  # fmt: skip
        lines = db.scalars(select(L.POLine).where(L.POLine.po_id == order.id).order_by(L.POLine.line_no)).all()
        assert [(x.item, x.unit, x.ordered_qty, x.unit_price) for x in lines] == [
            ("Bolts:M8", "EA", D("100"), D("0.25")), ("Nuts:M8", "EA", D("3"), D("3.3333")),
            ("Washers:M8", "EA", D("1000"), D("1")),
        ]  # fmt: skip
        assert lines[0].description == "M8 bolts, zinc"
        sheet = db.scalars(select(L.POLine).where(L.POLine.item == "Sheet:Alu 2mm")).one()
        assert sheet.customer == "Café Racing"
        state = line_states(db, sheet.po_id)[sheet.id]
        assert state.status.value == "Partially shipped" and state.facts.shipped == D("4")
        assert db.scalar(select(func.count()).select_from(L.PurchaseOrder)) == 3  # 5003 waits for its vendor
        actions = set(db.scalars(select(AuditLog.action)))
        assert {"po.create", "shipment.record", "line.price_rounded", "quickbooks.load"} <= actions


def test_loaded_pos_are_not_flagged_for_an_acknowledgement_the_app_never_saw(env, vendors: TestClient) -> None:  # noqa: ANN001
    load(env)
    with Session(env[1]) as db:
        assert reports.counts(db, date(2027, 1, 31)).no_ack == 0


def test_the_po_page_shows_the_quickbooks_date_the_customer_and_the_history(env, vendors: TestClient) -> None:  # noqa: ANN001
    load(env)
    with Session(env[1]) as db:
        sheet_po = db.scalars(select(L.PurchaseOrder).where(L.PurchaseOrder.po_number == "5002")).one().id
        nuts_po = db.scalars(select(L.PurchaseOrder).where(L.PurchaseOrder.po_number == "5001")).one().id
    page = vendors.get(f"/orders/{sheet_po}").text
    assert "PO date (QuickBooks)" in page and "10/22/2026" in page and "· for Café Racing" in page
    assert "Received per QuickBooks export of 11/05/2026" in page and "Partially shipped" in page
    nuts = vendors.get(f"/orders/{nuts_po}").text
    assert "worked out from the QuickBooks total 10.00 ÷ 3, rounded (export of 11/05/2026)" in nuts
    assert "· for Café Racing" in vendors.get("/reports/open-items").text


def test_loading_again_changes_nothing_and_a_new_vendor_lets_a_waiting_po_in(env, vendors: TestClient) -> None:  # noqa: ANN001
    load(env)
    again = plan(env)
    assert again.create == [] and sorted(again.existing) == ["5001", "5002", "5004"] and not again.ready
    make_vendor(vendors, "Globex Wire", "globex.example")
    third = plan(env)
    assert [o.po_number for o in third.create] == ["5003"] and third.ready


def test_a_po_number_already_in_the_app_is_never_changed(env, vendors: TestClient) -> None:  # noqa: ANN001
    with Session(env[1]) as db:
        acme = db.scalars(select(L.Vendor).where(L.Vendor.name == "Acme Fasteners")).one()
        db.add(L.PurchaseOrder(po_number="5001", vendor_id=acme.id, currency="USD"))
        db.commit()
    found = plan(env)
    assert "5001" in found.existing and [o.po_number for o in found.create] == ["5002", "5004"]


def test_a_failure_part_way_creates_nothing(env, vendors: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: ANN001
    def broken(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202
        raise LedgerError("disk full")

    monkeypatch.setattr(events, "record_shipment", broken)
    with Session(env[1]) as db:
        with pytest.raises(LedgerError):
            qb.apply_load(db, qb.plan_load(db, raw(), NAME))
        db.rollback()
    with Session(env[1]) as db:
        assert db.scalar(select(func.count()).select_from(L.PurchaseOrder)) == 0


# ---- the page


def _preview(client: TestClient, data: bytes | None = None, name: str = NAME, typed: str = ""):  # noqa: ANN202
    headers = {**ORIGIN, "X-CSRF-Token": token_in(client.get("/orders/quickbooks").text)}
    return client.post("/orders/quickbooks", files={"file": (name, raw() if data is None else data, "text/csv")},
                       data={"export_date": typed}, headers=headers)  # fmt: skip


def _create(client: TestClient, page: str, export_date: str = "2026-11-05"):  # noqa: ANN202
    token = re.search(r'name="token" value="([^"]+)"', page)
    assert token, "no create button"
    data = {"csrf_token": token_in(page), "token": token.group(1), "export_date": export_date}
    return client.post("/orders/quickbooks/create", headers=ORIGIN, data=data)


def test_the_page_previews_then_creates_and_reload_cannot_repeat_it(vendors: TestClient) -> None:
    start = vendors.get("/orders/quickbooks")
    assert start.status_code == 200 and start.text.count(" autofocus") == 1 and "Open Purchase Orders" in start.text
    assert 'href="/orders/quickbooks"' in vendors.get("/orders").text
    preview = _preview(vendors)
    text = preview.text
    assert preview.status_code == 200 and "Nothing has changed yet" in text
    assert "<strong>3</strong> purchase orders with 6 lines" in text and "Globex Wire" in text
    assert "10.00 ÷ 3 = 3.3333" in text and "Create 3 purchase orders" in text
    assert 'value="2026-11-05"' in text  # the export date, read from the name, can be changed
    assert vendors.get("/orders").text.count('<a href="/orders/') == 0  # nothing created by the preview
    done = _create(vendors, text)
    assert done.status_code == 303 and done.headers["location"] == "/orders?imported=3&waiting=1&source=quickbooks"
    listing = vendors.get(done.headers["location"]).text
    assert "Loaded 3 purchase orders from QuickBooks" in listing and "1 is waiting for its vendor" in listing
    again = _create(vendors, text)  # the same button pressed again, or Reload
    assert again.status_code == 400 and "expired or was already used" in again.text
    assert vendors.get("/orders").text.count('<a href="/orders/') == 3


def test_a_name_without_a_date_asks_for_it(vendors: TestClient) -> None:
    preview = _preview(vendors, name="Open POs.CSV")
    assert "has no date like 110526" in preview.text and 'name="token"' not in preview.text
    typed = _preview(vendors, name="Open POs.CSV", typed="2026-11-04")
    assert 'value="2026-11-04"' in typed.text and 'name="token"' in typed.text


def test_one_persons_preview_cannot_be_used_by_another(env, vendors: TestClient) -> None:  # noqa: ANN001
    page = _preview(vendors).text
    other = new_client(env[0])
    assert sign_in(other, "boss@example.com", PW).status_code == 303
    token = re.search(r'name="token" value="([^"]+)"', page).group(1)  # type: ignore[union-attr]
    stolen = other.post("/orders/quickbooks/create", headers=ORIGIN,
                        data={"csrf_token": token_in(other.get("/").text), "token": token})  # fmt: skip
    assert stolen.status_code == 400
    with Session(env[1]) as db:
        assert db.scalar(select(func.count()).select_from(L.PurchaseOrder)) == 0


def test_a_preview_expires_after_15_minutes(vendors: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    page = _preview(vendors).text
    later = time.monotonic() + qb_page.HOLD_SECONDS + 1
    monkeypatch.setattr(qb_page.time, "monotonic", lambda: later)
    assert _create(vendors, page).status_code == 400


def test_a_hostile_or_wrong_file_is_a_clear_message(vendors: TestClient) -> None:
    assert "does not look like the QuickBooks Open POs report" in _preview(vendors, b"a,b\n1,2\n", "x.csv").text
    assert "macros" in _preview(vendors, b"x", "book.xlsm").text
    assert "larger than 2 MB" in _preview(vendors, b"x" * (2 * 1024 * 1024 + 10), "big.csv").text


def test_the_load_needs_a_sign_in(env) -> None:  # noqa: ANN001
    anonymous = new_client(env[0])
    for method, path in (("GET", "/orders/quickbooks"), ("POST", "/orders/quickbooks"),
                         ("POST", "/orders/quickbooks/create")):  # fmt: skip
        assert anonymous.request(method, path, headers=ORIGIN).status_code in (303, 401, 403)
