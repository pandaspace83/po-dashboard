"""Expected dates, late lines, the list filters and the tracking link (PD-1.5a, DEC-39)."""

from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from po_dashboard.ledger.expected import expectation
from po_dashboard.ledger.listing import parse_filters
from po_dashboard.ledger.tracking import tracking_url
from test_entries_web import NONE, ack, po  # noqa: F401
from test_gate import env, post  # noqa: F401
from test_orders_web import client, make_vendor, order_form, place  # noqa: F401

TODAY = date(2026, 11, 10)
DAY = timedelta(days=1)


# ---- the rule, with no database


def test_the_promised_date_is_what_to_plan_on_until_a_shipment_has_an_estimate() -> None:
    only_promised = expectation(TODAY + 5 * DAY, None, TODAY)
    assert (only_promised.expected, only_promised.source, only_promised.late) == (TODAY + 5 * DAY, "promised", False)
    shipped = expectation(TODAY + 5 * DAY, TODAY + 2 * DAY, TODAY)
    assert (shipped.expected, shipped.source, shipped.promised) == (TODAY + 2 * DAY, "shipment", TODAY + 5 * DAY)


def test_a_line_is_late_the_day_after_its_date_and_not_on_the_day() -> None:
    assert not expectation(TODAY, None, TODAY).late
    one = expectation(TODAY - DAY, None, TODAY)
    assert one.late and one.days_late == 1
    assert expectation(TODAY - 9 * DAY, None, TODAY).days_late == 9


def test_a_late_estimate_makes_a_line_late_even_when_the_promise_was_kept() -> None:
    assert expectation(TODAY + DAY, TODAY - 3 * DAY, TODAY).days_late == 3  # the estimate moved earlier... and passed


def test_a_closed_line_or_one_with_no_date_is_never_late() -> None:
    assert not expectation(TODAY - 30 * DAY, None, TODAY, closed=True).late
    nothing = expectation(None, None, TODAY)
    assert nothing.expected is None and nothing.source is None and not nothing.late


# ---- the tracking link


@pytest.mark.parametrize(
    ("carrier", "number", "start"),
    [("UPS", "1Z999AA10123456784", "https://www.ups.com/"), ("FedEx Ground", "123456789012", "https://www.fedex.com/"),
     ("usps", "9400 1000 0000 0000 0000 00", "https://tools.usps.com/"), ("DHL Express", "JD0146000", "https://www.dhl.com/")],
)  # fmt: skip
def test_known_carriers_get_a_link_to_their_own_page(carrier: str, number: str, start: str) -> None:
    link = tracking_url(carrier, number)
    assert link is not None and link.startswith(start) and " " not in link


@pytest.mark.parametrize(
    ("carrier", "number"),
    [(None, "1Z999AA1"), ("UPS", None), ("Some Local Courier", "123456"), ("UPS", "12"), ("UPS", "1Z9/../x?y=1&z"),
     ("UPS", "<script>alert(1)</script>")],
)  # fmt: skip
def test_nothing_unknown_or_odd_becomes_a_link(carrier: str | None, number: str | None) -> None:
    assert tracking_url(carrier, number) is None


# ---- the filters as typed


def test_typed_filters_are_read_and_a_wrong_one_is_reported_and_left_out() -> None:
    found, problems = parse_filters({"q": " 12 ", "status": "shipped", "late": "1", "expected_from": "2026-11-01",
                                     "expected_to": "2026-11-30", "vendor": "7"})  # fmt: skip
    assert not problems and (found.q, found.status, found.late, found.vendor_id) == ("12", "shipped", True, 7)
    assert found.expected_from == date(2026, 11, 1) and found.expected_to == date(2026, 11, 30)
    bad, problems = parse_filters(
        {"status": "nope", "sent_from": "soon", "vendor": "x'; drop", "expected_from": "2026-12-01",
         "expected_to": "2026-11-01"}
    )  # fmt: skip
    assert len(problems) == 4 and bad.status == "" and bad.vendor_id is None and bad.sent_from is None


# ---- through the pages


def row_for(client: TestClient, query: str) -> str:
    return client.get(f"/orders?{query}").text


def promise(client: TestClient, po, day: date, qty: str = "10") -> None:  # noqa: ANN001
    assert post(client, f"/orders/{po[0]}/ack", ack(po, [(qty, "", day.isoformat()), NONE])).status_code == 303


def test_a_late_line_is_flagged_on_the_po_page_and_the_list_and_a_filter_finds_it(client: TestClient, po) -> None:  # noqa: ANN001
    promise(client, po, date.today() - 4 * DAY)
    detail = client.get(f"/orders/{po[0]}").text
    assert "4 days late" in detail and "as promised" in detail
    listed = row_for(client, "")
    assert "1 late, up to 4 days" in listed
    assert "PO-1" in row_for(client, "late=1")
    assert "No purchase orders match" in row_for(client, "late=1&vendor=99999")


def test_a_future_promise_is_not_late_and_late_only_hides_it(client: TestClient, po) -> None:  # noqa: ANN001
    promise(client, po, date.today() + 6 * DAY)
    assert "days late" not in client.get(f"/orders/{po[0]}").text
    assert "No purchase orders match" in row_for(client, "late=1")


def test_the_expected_range_matches_a_po_by_its_open_lines(client: TestClient, po) -> None:  # noqa: ANN001
    day = date.today() + 6 * DAY
    promise(client, po, day)
    inside = f"expected_from={day - DAY}&expected_to={day + DAY}"
    assert "PO-1" in row_for(client, inside)
    assert "No purchase orders match" in row_for(client, f"expected_from={day + DAY}")
    assert "No purchase orders match" in row_for(client, f"expected_to={day - 2 * DAY}")


def test_a_shipment_estimate_replaces_the_promise_and_shows_the_carrier_link(client: TestClient, po) -> None:  # noqa: ANN001
    promise(client, po, date.today() - 4 * DAY)  # the promise is already missed
    soon = date.today() + 3 * DAY
    shipment = {"carrier": "UPS", "tracking_number": "1Z999AA10123456784", "eta": soon.isoformat(),
                "line_id": [str(po[1])], "line_qty": ["4"], "line_eta": [""]}  # fmt: skip
    assert post(client, f"/orders/{po[0]}/shipments", shipment).status_code == 303
    detail = client.get(f"/orders/{po[0]}").text
    assert "from the shipment" in detail and "days late" not in detail
    assert 'href="https://www.ups.com/track?tracknum=1Z999AA10123456784"' in detail
    assert 'rel="noopener noreferrer"' in detail


def test_status_and_vendor_filters_choose_pos(client: TestClient, po) -> None:  # noqa: ANN001
    assert "PO-1" in row_for(client, "status=sent")
    assert "No purchase orders match" in row_for(client, "status=shipped")
    assert "PO-1" in row_for(client, "status=open") and "No purchase orders match" in row_for(client, "status=closed")
    other = make_vendor(client, "Northwind Metals", "northwind.example")
    assert "No purchase orders match" in row_for(client, f"vendor={other}")


def test_sent_dates_filter_and_a_wrong_value_is_reported_not_trusted(client: TestClient, po) -> None:  # noqa: ANN001
    assert "PO-1" in row_for(client, "sent_from=2026-11-01&sent_to=2026-11-30")
    assert "No purchase orders match" in row_for(client, "sent_from=2026-11-05")
    bad = row_for(client, "sent_from=soon")
    assert "not a date" in bad and "PO-1" in bad
    assert "<script>" not in row_for(client, "q=%3Cscript%3E&status=%3Cscript%3E")
