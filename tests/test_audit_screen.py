"""The audit trail screen, and a guard that every change to the ledger leaves a record (NFR-03, PD-1.7)."""

import re

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from po_dashboard.db.models import AuditLog
from test_entries_web import NONE, ack, po  # noqa: F401
from test_gate import ORIGIN, env, new_client, post, sign_in  # noqa: F401
from test_orders_web import client, make_vendor, order_form, place  # noqa: F401


@pytest.fixture
def boss(env) -> TestClient:  # noqa: ANN001
    admin = new_client(env[0])
    assert sign_in(admin, "boss@example.com").status_code == 303
    return admin


def rows(env) -> int:  # noqa: ANN001
    with Session(env[1]) as db:
        return db.scalar(select(func.count()).select_from(AuditLog)) or 0


# ---- who may see it


def test_an_admin_sees_the_trail_and_the_cursor_starts_in_the_first_box(boss: TestClient, po) -> None:  # noqa: ANN001
    page = boss.get("/admin/audit")
    assert page.status_code == 200 and page.text.count(" autofocus") == 1
    assert "<strong>sign_in</strong>" in page.text  # the sign-ins are in it
    assert "buyer@example.com" in page.text


def test_a_buyer_and_a_stranger_cannot_see_it(env, client: TestClient) -> None:  # noqa: ANN001
    assert client.get("/admin/audit").status_code == 403
    anonymous = new_client(env[0])
    assert anonymous.get("/admin/audit", headers=ORIGIN, follow_redirects=False).status_code in (303, 401, 403)


def test_the_screen_only_reads(boss: TestClient) -> None:
    for method in ("post", "put", "delete", "patch"):
        assert getattr(boss, method)("/admin/audit", headers=ORIGIN).status_code in (403, 404, 405)


# ---- filters, paging, safety


def test_filters_narrow_the_list_and_a_wrong_date_is_reported(boss: TestClient, client: TestClient, po) -> None:  # noqa: ANN001
    post(
        client, f"/orders/{po[0]}/close", {"line_id": [str(po[1])], "line_action": ["short"], "reason": "Vendor short"}
    )
    only = boss.get("/admin/audit?action=line.close").text
    assert "line.close" in only and "vendor.update" not in only and "Vendor short" in only
    assert "po_line:" + str(po[1]) in boss.get(f"/admin/audit?q=po_line:{po[1]}").text
    assert "No records match" in boss.get("/admin/audit?q=nothing-like-this").text
    assert "No records match" in boss.get("/admin/audit?date_from=2099-01-01").text
    bad = boss.get("/admin/audit?date_from=soon")
    assert bad.status_code == 200 and "not a date" in bad.text
    assert boss.get("/admin/audit?person=x%27&page=1").status_code == 200


def test_the_trail_pages_and_a_page_beyond_the_end_is_empty_not_an_error(boss: TestClient, client: TestClient) -> None:
    for number in range(60):
        post(client, "/vendors/create", {"name": f"Vendor {number}", "domains": f"v{number}.example"})
    first = boss.get("/admin/audit")
    assert "Page 1 of" in first.text and "Older" in first.text
    assert boss.get("/admin/audit?page=2").status_code == 200
    assert "No records match" in boss.get("/admin/audit?page=900").text


def test_what_a_record_holds_is_shown_as_text_never_as_markup(boss: TestClient, client: TestClient) -> None:
    post(client, "/vendors/create", {"name": "<script>alert(1)</script>", "domains": "x.example"})
    page = boss.get("/admin/audit?action=vendor.create").text
    assert "<script>alert(1)</script>" not in page and "&lt;script&gt;" in page


# ---- every change leaves a record


def test_every_change_to_the_ledger_writes_an_audit_record(env, client: TestClient, po) -> None:  # noqa: ANN001
    po_id, first, second = po
    vendor_id = int(re.search(r"/vendors/(\d+)", client.get(f"/orders/{po_id}").text).group(1))  # type: ignore[union-attr]
    ship = {"carrier": "UPS", "tracking_number": "1Z999AA10123456784", "eta": "", "line_id": [str(first)],
            "line_qty": ["1"], "line_eta": [""]}  # fmt: skip
    bill = {"invoice_number": "INV-1", "line_id": [str(first)], "line_qty": ["1"], "line_price": ["2.5"]}
    steps = [
        ("create vendor", "/vendors/create", {"name": "Guard Co", "domains": "guard.example"}),
        ("update vendor", f"/vendors/{vendor_id}/update", {"name": "Acme Fasteners", "notes": "n"}),
        ("add domain", f"/vendors/{vendor_id}/domains/add", {"domains": "more.example"}),
        ("acknowledgement", f"/orders/{po_id}/ack", ack(po, [("10", "", "2026-12-01"), NONE])),
        ("shipment", f"/orders/{po_id}/shipments", ship),
        ("invoice", f"/orders/{po_id}/invoices", bill),
        (
            "close by hand",
            f"/orders/{po_id}/close",
            {"line_id": [str(second)], "line_action": ["short"], "reason": "r"},
        ),
        ("reopen", f"/orders/{po_id}/lines/{second}/reopen", {"reason": "r"}),
    ]
    for label, path, data in steps:
        before = rows(env)
        assert post(client, path, data).status_code == 303, label
        assert rows(env) > before, f"{label} left no audit record"
    before = rows(env)
    entry = re.search(r"/entries/(?:ack|shipment|invoice)/\d+/remove", client.get(f"/orders/{po_id}").text)
    assert entry is not None
    assert post(client, f"/orders/{po_id}{entry.group(0)}", {"reason": "why"}).status_code == 303
    assert rows(env) > before
