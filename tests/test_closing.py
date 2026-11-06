"""Settling lines by hand, with a reason, and taking it back (PD-1.5b, REQ-INV-04, DEC-39)."""

from datetime import date, timedelta

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from po_dashboard.db.models import AuditLog
from po_dashboard.ledger.status import LineStatus, line_states
from test_entries_web import NONE, ack, po  # noqa: F401
from test_gate import ORIGIN, env, new_client, post  # noqa: F401
from test_orders_web import client, make_vendor, order_form, place  # noqa: F401


def settle(client: TestClient, po, actions: tuple[str, str], reason: str = "Spoke to the vendor"):  # noqa: ANN001, ANN201
    return post(client, f"/orders/{po[0]}/close",
                {"line_id": [str(po[1]), str(po[2])], "line_action": list(actions), "reason": reason})  # fmt: skip


def status(env, po, which: int) -> LineStatus:  # noqa: ANN001
    with Session(env[1]) as db:
        return line_states(db, po[0])[po[which]].status


def test_one_form_settles_several_lines_with_one_reason(client: TestClient, env, po) -> None:  # noqa: ANN001
    assert settle(client, po, ("received", "cancelled")).status_code == 303
    assert status(env, po, 1) is LineStatus.CLOSED and status(env, po, 2) is LineStatus.CANCELLED
    page = client.get(f"/orders/{po[0]}").text
    assert "Settled by hand: received" in page and "Spoke to the vendor" in page and "Cancelled" in page
    with Session(env[1]) as db:
        entry = db.scalars(select(AuditLog).where(AuditLog.action == "line.close")).all()
        assert len(entry) == 2 and all(e.user_id and e.client_address for e in entry)


def test_a_reason_is_required_and_a_choice_too(client: TestClient, env, po) -> None:  # noqa: ANN001
    assert "Reason is needed" in settle(client, po, ("received", ""), reason=" ").text
    assert "Choose what happened" in settle(client, po, ("", "")).text
    assert status(env, po, 1) is LineStatus.SENT


def test_one_bad_pick_changes_nothing(client: TestClient, env, po) -> None:  # noqa: ANN001
    response = settle(client, po, ("received", "lost"))
    assert response.status_code == 400 and "not one of the choices" in response.text
    assert status(env, po, 1) is LineStatus.SENT


def test_a_closed_line_cannot_be_settled_again_and_is_not_offered(client: TestClient, env, po) -> None:  # noqa: ANN001
    settle(client, po, ("short", ""))
    assert "already closed" in settle(client, po, ("received", "")).text
    assert f'value="{po[1]}"' not in client.get(f"/orders/{po[0]}").text.split("Settle lines by hand")[1]


def test_a_line_that_is_not_on_this_po_is_refused(client: TestClient, po) -> None:  # noqa: ANN001
    response = post(
        client, f"/orders/{po[0]}/close", {"line_id": ["999999"], "line_action": ["received"], "reason": "x"}
    )
    assert response.status_code == 400 and "does not belong" in response.text


def test_a_tampered_form_is_refused(client: TestClient, po) -> None:  # noqa: ANN001
    response = post(
        client, f"/orders/{po[0]}/close", {"line_id": ["1", "2"], "line_action": ["received"], "reason": "x"}
    )
    assert response.status_code == 400 and "incomplete" in response.text


def test_reopening_needs_a_reason_and_puts_the_line_back(client: TestClient, env, po) -> None:  # noqa: ANN001
    settle(client, po, ("received", ""))
    url = f"/orders/{po[0]}/lines/{po[1]}/reopen"
    assert "Reason is needed" in post(client, url, {"reason": ""}).text
    assert post(client, url, {"reason": "Wrong line"}).status_code == 303
    assert status(env, po, 1) is LineStatus.SENT
    with Session(env[1]) as db:
        reopened = db.scalars(select(AuditLog).where(AuditLog.action == "line.reopen")).one()
        assert "received" in (reopened.before or "") and "Wrong line" in (reopened.after or "")
    assert "was not closed by hand" in post(client, url, {"reason": "again"}).text


def test_a_cancelled_line_does_not_hold_the_po_open(client: TestClient, env, po) -> None:  # noqa: ANN001
    settle(client, po, ("cancelled", "cancelled"))
    assert (
        "Closed" in client.get("/orders").text and "No purchase orders match" in client.get("/orders?status=open").text
    )


def test_a_cancelled_line_is_never_late(client: TestClient, env, po) -> None:  # noqa: ANN001
    post(client, f"/orders/{po[0]}/ack", ack(po, [("10", "", (date.today() - timedelta(days=3)).isoformat()), NONE]))
    assert "3 days late" in client.get(f"/orders/{po[0]}").text
    settle(client, po, ("cancelled", ""))
    assert "days late" not in client.get(f"/orders/{po[0]}").text


def test_every_route_needs_a_sign_in(env, po) -> None:  # noqa: ANN001
    anonymous = new_client(env[0])
    for path in ("close", f"lines/{po[1]}/reopen"):
        assert anonymous.post(f"/orders/{po[0]}/{path}", headers=ORIGIN).status_code in (303, 401, 403)


def test_the_timeline_lists_what_happened_to_a_line_in_order(client: TestClient, po) -> None:  # noqa: ANN001
    post(client, f"/orders/{po[0]}/ack", ack(po, [("6", "", "2026-12-01"), NONE]))
    post(client, f"/orders/{po[0]}/shipments", {"carrier": "UPS", "tracking_number": "1Z999AA10123456784",
         "line_id": [str(po[1])], "line_qty": ["4"], "line_eta": [""]})  # fmt: skip
    settle(client, po, ("short", ""))
    page = client.get(f"/orders/{po[0]}").text
    section = page.split("Timeline by line")[1].split("<h2>History")[0]
    order = [section.index(word) for word in ("Ordered 10", "confirmed 6", "Shipped 4", "Settled by hand (short)")]
    assert order == sorted(order) and "Line 2: Nut" in section
