"""Drafted follow-up messages (PD-5.3, REQ-FU-03, DEC-40): fixed templates, from the ledger, never sent."""

from datetime import date
from decimal import Decimal

from fastapi.testclient import TestClient

from po_dashboard.ledger import drafts, reports
from po_dashboard.ledger.expected import Expectation
from test_entries_web import NONE, ack, po  # noqa: F401
from test_gate import ORIGIN, env, new_client, post  # noqa: F401
from test_orders_web import client, make_vendor, order_form, place  # noqa: F401

LATER = date(2026, 12, 1)


def item(**over: object) -> reports.OpenItem:
    base: dict = dict(  # type: ignore[type-arg]
        po_id=1, po_number="PO-1", vendor="Acme", line_no=1, item="Bolt", description="", customer="", unit="ea",
        ordered=Decimal("10"), open_qty=Decimal("10"), status="Sent",
        expectation=Expectation(None, None, None, None, 0), slipped=False, no_ack=False, needs_update=False,
        last_activity=None,
    )  # fmt: skip
    base.update(over)
    return reports.OpenItem(**base)


FACTS = drafts.DraftFacts("Sam", "sam@acme.test", date(2026, 11, 10), Decimal("6"))


def test_a_line_with_no_flag_has_no_draft() -> None:
    assert drafts.drafts_for(item(), FACTS) == []


def test_a_late_draft_names_the_po_line_quantity_and_date() -> None:
    late = Expectation(date(2026, 11, 10), None, date(2026, 11, 10), "promised", 3)
    (d,) = drafts.drafts_for(item(expectation=late), FACTS)
    assert d.kind == "late" and d.to == "sam@acme.test" and d.has_contact
    assert "PO-1" in d.subject and "Hello Sam," in d.body
    assert "line 1, Bolt, 10 ea open" in d.body and "11/10/2026" in d.body


def test_each_flag_gets_its_own_draft() -> None:
    late = Expectation(date(2026, 11, 20), None, date(2026, 11, 20), "promised", 11)
    kinds = [d.kind for d in drafts.drafts_for(
        item(expectation=late, no_ack=True, slipped=True, needs_update=True), FACTS)]  # fmt: skip
    assert kinds == ["late", "no_ack", "slipped", "quantity"]


def test_the_slip_and_quantity_drafts_state_both_values() -> None:
    promised = Expectation(date(2026, 11, 20), None, date(2026, 11, 20), "promised", 0)
    slip, qty = drafts.drafts_for(item(expectation=promised, slipped=True, needs_update=True), FACTS)
    assert "from 11/10/2026 to 11/20/2026" in slip.body
    assert "states 6 ea" in qty.body and "We ordered 10 ea" in qty.body


def test_no_contact_is_said_and_the_body_is_still_given() -> None:
    (d,) = drafts.drafts_for(item(no_ack=True), drafts.DraftFacts(None, None, None, None))
    assert d.to == drafts.NO_CONTACT and not d.has_contact and d.body.startswith("Hello,")


def test_record_text_cannot_add_lines_to_the_message() -> None:
    (d,) = drafts.drafts_for(item(no_ack=True, item="Bolt\nBcc: x@evil.test"), FACTS)
    assert "Bolt Bcc: x@evil.test" in d.body and "\nBcc:" not in d.body


def test_the_draft_page_for_a_flagged_line(client: TestClient, po) -> None:  # noqa: ANN001
    post(client, f"/orders/{po[0]}/ack", ack(po, [("6", "", "2026-11-10"), NONE]))
    page = client.get(f"/reports/draft/{po[0]}/1")
    assert page.status_code == 200 and "DRAFT" in page.text and "Copy message" in page.text
    assert "no contact on file" in page.text or "@" in page.text
    assert "late" in page.text.lower()


def test_the_open_items_list_links_to_the_draft(client: TestClient, po) -> None:  # noqa: ANN001
    assert f"/reports/draft/{po[0]}/1" in client.get("/reports/open-items").text


def test_a_missing_or_closed_line_gets_a_404_and_the_page_needs_a_sign_in(client: TestClient, po, env) -> None:  # noqa: ANN001
    assert client.get(f"/reports/draft/{po[0]}/99").status_code == 404
    assert client.get("/reports/draft/99999/1").status_code == 404
    assert new_client(env[0]).get(f"/reports/draft/{po[0]}/1", headers=ORIGIN).status_code in (303, 401, 403)
