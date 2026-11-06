"""Recording acknowledgements, shipments and invoices through the pages (PD-1.4)."""

import re

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from po_dashboard.db import ledger as L
from po_dashboard.db.models import AuditLog
from test_gate import (  # noqa: F401
    ORIGIN,
    env,  # noqa: F401
    new_client,
    post,
    token_in,
)
from test_orders_web import client, make_vendor, order_form, place  # noqa: F401


@pytest.fixture
def po(client: TestClient, env) -> tuple[int, int, int]:  # noqa: ANN001
    """A PO of two lines (10 Bolt at 2.50, 2.5 Nut at 0.10). Returns (po id, line 1 id, line 2 id)."""
    vendor_id = make_vendor(client)
    po_id = int(place(client, order_form(vendor_id)).headers["location"].rsplit("/", 1)[1])
    with Session(env[1]) as db:
        first, second = db.scalars(select(L.POLine.id).where(L.POLine.po_id == po_id).order_by(L.POLine.line_no))
    return po_id, first, second


def ack(po: tuple[int, int, int], rows: list[tuple[str, str, str]], **extra: str) -> dict:  # type: ignore[type-arg]
    _, first, second = po
    return {"line_id": [str(first), str(second)], "line_qty": [r[0] for r in rows], "line_price": [r[1] for r in rows],
            "line_promised": [r[2] for r in rows], **extra}  # fmt: skip


def page(client: TestClient, po_id: int) -> str:
    return client.get(f"/orders/{po_id}").text


def statuses(client: TestClient, po_id: int) -> list[str]:
    html = page(client, po_id)
    return re.findall(r'<span class="pill">([A-Za-z ]+)</span>', html)


NONE = ("", "", "")


# ---- the forms


@pytest.mark.parametrize("path", ["ack/new", "shipments/new", "invoices/new"])
def test_each_form_opens_with_the_cursor_ready_and_lists_the_lines(client: TestClient, po, path: str) -> None:  # noqa: ANN001
    html = client.get(f"/orders/{po[0]}/{path}").text
    assert html.count(" autofocus") == 1
    assert "Bolt" in html and "Nut" in html and 'name="line_id"' in html


def test_the_invoice_form_starts_with_the_price_on_the_po(client: TestClient, po) -> None:  # noqa: ANN001
    html = client.get(f"/orders/{po[0]}/invoices/new").text
    assert 'value="2.5"' in html and 'value="0.1"' in html  # shown without trailing zeros, editable


@pytest.mark.parametrize("path", ["ack/new", "shipments/new", "invoices/new"])
def test_a_missing_po_is_a_404_page(client: TestClient, path: str) -> None:
    assert client.get(f"/orders/999/{path}").status_code == 404


def test_every_entry_route_needs_a_sign_in(env, po) -> None:  # noqa: ANN001
    anonymous = new_client(env[0])
    for method, path in (("GET", "ack/new"), ("POST", "ack"), ("POST", "shipments"), ("POST", "invoices"),
                         ("POST", "entries/ack/1/remove")):  # fmt: skip
        assert anonymous.request(method, f"/orders/{po[0]}/{path}", headers=ORIGIN).status_code in (303, 401, 403)


# ---- the whole life of a PO, by hand


def test_a_po_goes_from_sent_to_closed_through_the_pages(client: TestClient, po) -> None:  # noqa: ANN001
    po_id, first, _ = po
    assert statuses(client, po_id)[:2] == ["Sent", "Sent"]

    assert post(client, f"/orders/{po_id}/ack", ack(po, [("10", "", "2026-12-01"), NONE])).status_code == 303
    assert statuses(client, po_id)[:2] == ["Acknowledged", "Sent"]

    shipment = {"carrier": "UPS", "tracking_number": "1Z999", "eta": "2026-12-10", "line_id": [str(first)],
                "line_qty": ["4"], "line_eta": [""]}  # fmt: skip
    assert post(client, f"/orders/{po_id}/shipments", shipment).status_code == 303
    assert statuses(client, po_id)[0] == "Partially shipped"
    shipment["line_qty"] = ["6"]
    post(client, f"/orders/{po_id}/shipments", shipment)
    assert statuses(client, po_id)[0] == "Shipped"

    invoice = {"invoice_number": "INV-1", "invoice_date": "2026-12-12", "total": "25", "line_id": [str(first)],
               "line_qty": ["10"], "line_price": ["2.50"]}  # fmt: skip
    assert post(client, f"/orders/{po_id}/invoices", invoice).status_code == 303
    html = page(client, po_id)
    assert "Closed" in html and "partly invoiced" in html  # one line closed, the other still open
    for text in ("UPS 1Z999", "INV-1", "Total 25 USD", "Line 1: 4", "Line 1: 6"):
        assert text in html


def test_the_po_closes_when_every_line_is_invoiced(client: TestClient, po) -> None:  # noqa: ANN001
    po_id, first, second = po
    invoice = {"invoice_number": "INV-1", "line_id": [str(first), str(second)], "line_qty": ["10", "2.5"],
               "line_price": ["2.5", "0.1"]}  # fmt: skip
    post(client, f"/orders/{po_id}/invoices", invoice)
    assert '<span class="pill ok">' in page(client, po_id)
    assert "Closed" in client.get("/orders").text


def test_recording_then_reloading_repeats_nothing(client: TestClient, po, env) -> None:  # noqa: ANN001
    po_id = po[0]
    response = post(client, f"/orders/{po_id}/ack", ack(po, [("10", "", ""), NONE]))
    assert response.status_code == 303 and response.headers["location"] == f"/orders/{po_id}"
    for _ in range(3):
        client.get(response.headers["location"])
    with Session(env[1]) as db:
        assert db.scalar(select(func.count()).select_from(L.Acknowledgement)) == 1


def test_an_ack_with_no_details_acknowledges_every_line(client: TestClient, po) -> None:  # noqa: ANN001
    post(client, f"/orders/{po[0]}/ack", ack(po, [NONE, NONE], without_details="1"))
    assert statuses(client, po[0])[:2] == ["Acknowledged", "Acknowledged"]


# ---- what the page tells the purchaser


def test_a_short_confirmation_tells_the_purchaser_to_update_the_original_po(client: TestClient, po) -> None:  # noqa: ANN001
    post(client, f"/orders/{po[0]}/ack", ack(po, [("6", "", ""), NONE]))
    assert "confirmed 6, not 10. Update the original purchase order" in page(client, po[0])


def test_a_later_promised_date_is_shown_as_a_slip(client: TestClient, po) -> None:  # noqa: ANN001
    post(client, f"/orders/{po[0]}/ack", ack(po, [("", "", "2026-12-01"), NONE]))
    post(client, f"/orders/{po[0]}/ack", ack(po, [("", "", "2026-12-15"), NONE]))
    html = page(client, po[0])
    assert "2026-12-01" in html and "2026-12-15" in html and "slipped" in html


def test_a_different_price_and_over_billing_are_flagged_and_still_recorded(client: TestClient, po) -> None:  # noqa: ANN001
    po_id, first, _ = po
    post(client, f"/orders/{po_id}/ack", ack(po, [("", "2.75", ""), NONE]))
    post(client, f"/orders/{po_id}/invoices", {"invoice_number": "INV-9", "line_id": [str(first)], "line_qty": ["12"],
                                               "line_price": ["3"]})  # fmt: skip
    html = page(client, po_id)
    assert "price 2.75 differs from the PO price 2.5" in html
    assert "Invoiced 12, more than the 10 ordered" in html and "above the PO price 2.5" in html
    assert "INV-9" in html


# ---- refusals keep what was typed


def test_a_refused_ack_lists_each_problem_and_keeps_every_box(client: TestClient, po) -> None:  # noqa: ANN001
    response = post(client, f"/orders/{po[0]}/ack", ack(po, [("-1", "", ""), ("", "", "soon")]))
    assert response.status_code == 400
    for text in ("Line 1: Confirmed quantity cannot be negative", "Line 2: Promised date"):
        assert text in response.text
    assert 'value="-1"' in response.text and 'value="soon"' in response.text
    rounded = post(client, f"/orders/{po[0]}/ack", ack(po, [("1.00001", "", ""), NONE]))
    assert "nothing is rounded" in rounded.text


def test_an_ack_with_nothing_filled_in_is_refused(client: TestClient, po, env) -> None:  # noqa: ANN001
    response = post(client, f"/orders/{po[0]}/ack", ack(po, [NONE, NONE]))
    assert response.status_code == 400 and "at least one line" in response.text
    with Session(env[1]) as db:
        assert db.scalar(select(func.count()).select_from(L.Acknowledgement)) == 0


def test_a_refused_shipment_keeps_its_header(client: TestClient, po) -> None:  # noqa: ANN001
    response = post(client, f"/orders/{po[0]}/shipments",
                    {"carrier": "UPS", "tracking_number": "1Z999", "eta": "tomorrow", "line_id": [str(po[1])],
                     "line_qty": ["1"], "line_eta": [""]})  # fmt: skip
    assert response.status_code == 400 and "Expected arrival" in response.text and 'value="1Z999"' in response.text


def test_a_shipment_date_with_no_quantity_is_refused(client: TestClient, po) -> None:  # noqa: ANN001
    response = post(client, f"/orders/{po[0]}/shipments",
                    {"line_id": [str(po[1])], "line_qty": [""], "line_eta": ["2026-12-01"]})  # fmt: skip
    assert response.status_code == 400 and "Quantity is needed" in response.text


def test_an_invoice_needs_a_number_and_a_price(client: TestClient, po) -> None:  # noqa: ANN001
    base = {"line_id": [str(po[1])], "line_qty": ["1"], "line_price": ["2.5"]}
    assert "Invoice number is needed" in post(client, f"/orders/{po[0]}/invoices", {**base, "invoice_number": " "}).text
    no_price = post(client, f"/orders/{po[0]}/invoices", {**base, "invoice_number": "A", "line_price": [""]})
    assert no_price.status_code == 400 and "Price is needed" in no_price.text
    assert post(client, f"/orders/{po[0]}/invoices", {**base, "invoice_number": "A"}).status_code == 303
    dup = post(client, f"/orders/{po[0]}/invoices", {**base, "invoice_number": "a"})
    assert dup.status_code == 400 and "already recorded" in dup.text


# ---- a tampered form is refused, never trusted


def test_a_form_that_leaves_a_line_out_shows_every_line_again_not_a_crash(client: TestClient, po) -> None:  # noqa: ANN001
    short = {"line_id": [str(po[1])], "line_qty": ["-5"], "line_price": [""], "line_promised": [""]}
    response = post(client, f"/orders/{po[0]}/ack", short)
    assert response.status_code == 400 and "Bolt" in response.text and "Nut" in response.text


def test_a_form_with_uneven_or_odd_line_ids_is_refused_not_crashed(client: TestClient, po, env) -> None:  # noqa: ANN001
    po_id, first, second = po
    bad_forms = [
        {"line_id": [str(first), str(second)], "line_qty": ["1"], "line_price": ["1", "1"], "line_promised": ["", ""]},
        {"line_id": ["abc"], "line_qty": ["1"], "line_price": [""], "line_promised": [""]},
        {"line_id": ["-1"], "line_qty": ["1"], "line_price": [""], "line_promised": [""]},
        {"line_id": [""], "line_qty": ["1"], "line_price": [""], "line_promised": [""]},
        {"line_qty": ["1"], "line_price": [""], "line_promised": [""]},
    ]
    for form in bad_forms:
        assert post(client, f"/orders/{po_id}/ack", form).status_code == 400
    with Session(env[1]) as db:
        assert db.scalar(select(func.count()).select_from(L.Acknowledgement)) == 0


def test_a_line_from_another_po_cannot_be_recorded_against_this_one(client: TestClient, po, env) -> None:  # noqa: ANN001
    po_id, first, _ = po
    vendor_id = make_vendor(client, "Other", "other.com")
    other = int(place(client, order_form(vendor_id, po_number="PO-2")).headers["location"].rsplit("/", 1)[1])
    with Session(env[1]) as db:
        foreign = db.scalars(select(L.POLine.id).where(L.POLine.po_id == other)).first()
    form = {"line_id": [str(foreign)], "line_qty": ["1"], "line_price": [""], "line_promised": [""]}
    response = post(client, f"/orders/{po_id}/ack", form)
    assert response.status_code == 400 and "does not belong" in response.text
    with Session(env[1]) as db:
        assert db.scalar(select(func.count()).select_from(L.AckLine)) == 0


# ---- taking an entry back


def test_an_entry_is_removed_with_a_reason_and_its_effect_goes(client: TestClient, po, env) -> None:  # noqa: ANN001
    po_id, first, _ = po
    invoice = {"invoice_number": "INV-1", "line_id": [str(first)], "line_qty": ["10"], "line_price": ["2.5"]}
    post(client, f"/orders/{po_id}/invoices", invoice)
    assert "Closed" in page(client, po_id)
    with Session(env[1]) as db:
        entry = db.scalars(select(L.Invoice.id)).one()
    needs_reason = post(client, f"/orders/{po_id}/entries/invoice/{entry}/remove", {"reason": " "})
    assert needs_reason.status_code == 400 and "Reason is needed" in needs_reason.text
    removed = post(client, f"/orders/{po_id}/entries/invoice/{entry}/remove", {"reason": "Wrong PO"})
    assert removed.status_code == 303
    live = page(client, po_id).split("Timeline by line")[0]  # the table of lines: the entry no longer counts
    assert "INV-1" not in live and statuses(client, po_id)[0] == "Sent"
    assert "Entry taken back: Invoice INV-1" in page(client, po_id)  # the timeline keeps the story
    with Session(env[1]) as db:
        audit = db.scalars(select(AuditLog).where(AuditLog.action == "invoice.remove")).one()
        assert "INV-1" in (audit.before or "") and "Wrong PO" in (audit.after or "") and audit.user_id is not None


def test_removing_through_another_po_or_with_a_wrong_kind_is_refused(client: TestClient, po, env) -> None:  # noqa: ANN001
    po_id, first, _ = po
    post(client, f"/orders/{po_id}/shipments", {"line_id": [str(first)], "line_qty": ["1"], "line_eta": [""]})
    with Session(env[1]) as db:
        shipment = db.scalars(select(L.Shipment.id)).one()
    paths = [
        f"/orders/{po_id}/entries/invoice/{shipment}/remove",
        f"/orders/{po_id}/entries/bogus/{shipment}/remove",
        f"/orders/999/entries/shipment/{shipment}/remove",
    ]
    for path in paths:
        assert post(client, path, {"reason": "x"}).status_code in (400, 404)
    with Session(env[1]) as db:
        assert db.scalar(select(func.count()).select_from(L.Shipment)) == 1


def test_a_failed_removal_message_cannot_be_forged_from_the_address(client: TestClient, po) -> None:  # noqa: ANN001
    html = client.get(f"/orders/{po[0]}", params={"problem": "Your account is locked. Call 555-0100"}).text
    assert "Your account is locked" not in html


# ---- text typed by a person is shown as text, and who did it is kept


def test_text_in_a_shipment_is_escaped_and_the_audit_keeps_who_and_where(client: TestClient, po, env) -> None:  # noqa: ANN001
    po_id, first, _ = po
    evil = '<script>alert("x")</script>'
    post(client, f"/orders/{po_id}/shipments", {"carrier": evil, "tracking_number": evil, "line_id": [str(first)],
                                                "line_qty": ["1"], "line_eta": [""]})  # fmt: skip
    html = page(client, po_id)
    assert "<script>alert" not in html and "&lt;script&gt;" in html
    with Session(env[1]) as db:
        entry = db.scalars(select(AuditLog).where(AuditLog.action == "shipment.record")).one()
        assert entry.user_id is not None and entry.client_address
