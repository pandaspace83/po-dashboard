"""The reconciliation screens (PD-8.5): the order list with its status and CSV, the order page and a person's decisions,
the exception queue, and settling an invoice that waits for review. Everything a person does is a form post with the
security token, answered by a redirect, audited, and re-runs the reconciliation."""

from __future__ import annotations

from decimal import Decimal as D

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from po_dashboard.customers import intake, service
from po_dashboard.customers.intake import LineData, PoData
from po_dashboard.db import customers as c
from po_dashboard.db.models import AuditLog
from po_dashboard.quickbooks.reconciliation import store
from test_customer_pages import rows
from test_gate import ORIGIN, env, post, token_in  # noqa: F401
from test_orders_web import client  # noqa: F401
from test_sales_import import line, report
from test_sales_pages import keep


def make(
    env, name: str = "Globex Racing", number: str = "CP-1001", qty: str = "10", price: str = "2.50"
) -> tuple[int, int]:  # noqa: ANN001, F811
    """A customer and one PO of one line, "Bolts:M8", through the real intake."""
    with Session(env[1]) as db:
        customer = service.create_customer(db, name=name)
        data = PoData(number, "USD", (LineData("Bolts:M8", D(qty), D(price)),), customer_id=customer.id)
        order = intake.file_po(db, data).po
        assert order is not None
        store.run(db, cause="po_change")
        db.commit()
        return customer.id, order.id


def invoice_id(env, number: str) -> int:  # noqa: ANN001, F811
    return next(i.id for i in rows(env, c.SalesInvoice) if i.invoice_number == number)


def audited(env, prefix: str) -> list[str]:  # noqa: ANN001, F811
    return [a.action for a in rows(env, AuditLog) if a.action.startswith(prefix)]


def bolts(number: str, qty: str, price: str, po: str = "CP-1001") -> str:
    return line(number, "Bolts:M8", qty, price, str(D(qty) * D(price)), po=po)


def sales(*invoices: str) -> bytes:
    return report("Globex Racing", *invoices)


# ---- the list


def test_the_list_shows_status_invoiced_and_what_remains_and_filters_by_status(client: TestClient, env) -> None:  # noqa: ANN001, F811
    make(env)
    keep(client, sales(bolts("2001", "4", "2.50")))
    page = client.get("/customer-orders").text
    assert "Partially invoiced" in page and "10.00" in page and "15.00" in page  # invoiced 10, remaining 15
    assert "No customer order matches" in client.get("/customer-orders?status=matched").text
    assert "CP-1001" in client.get("/customer-orders?status=partially_invoiced").text


def test_an_order_with_no_upload_says_so_and_a_waiting_revision_is_its_own_status(client: TestClient, env) -> None:  # noqa: ANN001, F811
    customer, _ = make(env)
    with Session(env[1]) as db:
        data = PoData("CP-1001", "USD", (LineData("Bolts:M8", D(20), D(2)),), customer_id=customer)
        assert intake.file_po(db, data).kind == "revision"
        db.commit()
    page = client.get("/customer-orders").text
    assert "Not covered by any upload" in page and "revision 1 waiting" in page
    assert "CP-1001" in client.get("/customer-orders?status=revision").text


def test_the_csv_follows_the_filters_and_neutralises_formulas(client: TestClient, env) -> None:  # noqa: ANN001, F811
    make(env, name="=HYPERLINK(1)")
    make_other = make(env, name="Initech Corp", number="IN-7")
    assert make_other
    response = client.get("/customer-orders/export.csv")
    assert response.status_code == 200 and response.headers["content-type"].startswith("text/csv")
    body = response.content.decode("utf-8-sig")
    assert body.splitlines()[0].startswith("Customer,PO number,Revision")
    assert "'=HYPERLINK(1)" in body and "\r\n=" not in body and ",=" not in body
    one = client.get("/customer-orders/export.csv?q=initech").content.decode("utf-8-sig")
    assert "Initech Corp" in one and "HYPERLINK" not in one


# ---- the exception queue


def test_the_queue_lists_each_exception_with_its_level_and_filters(client: TestClient, env) -> None:  # noqa: ANN001, F811
    make(env)
    assert "All clear" in client.get("/customer-orders/exceptions").text
    keep(client, sales(bolts("2001", "10", "3.00")))  # a price mismatch: confirmed
    page = client.get("/customer-orders/exceptions").text
    assert "Unit price mismatch" in page and "Confirmed" in page and "CP-1001" in page
    assert "Unit price mismatch" in client.get("/customer-orders/exceptions?level=confirmed").text
    assert "No exception matches" in client.get("/customer-orders/exceptions?level=preliminary").text
    assert "No exception matches" in client.get("/customer-orders/exceptions?code=not_found").text
    assert "Unit price mismatch" in client.get("/customer-orders/exceptions?code=price_mismatch").text
    assert "No exception matches" in client.get("/customer-orders/exceptions?customer=9999").text
    assert "Unit price mismatch" in client.get("/customer-orders/exceptions?from=2000-01-01&to=2999-01-01").text
    assert "No exception matches" in client.get("/customer-orders/exceptions?to=2000-01-01").text
    assert "Unit price mismatch" in client.get("/customer-orders/exceptions?from=garbage").text  # a bad date is ignored


def test_an_invoice_that_reaches_no_order_is_in_the_queue_with_a_link_to_it(client: TestClient, env) -> None:  # noqa: ANN001, F811
    make(env)
    keep(client, sales(bolts("2003", "10", "2.50", po="")))
    page = client.get("/customer-orders/exceptions").text
    assert "Unmatched invoice" in page and f"/sales-invoices/{invoice_id(env, '2003')}" in page


# ---- the order page and a person's decisions


def test_the_order_page_shows_the_figures_the_lines_and_the_invoices_counted(client: TestClient, env) -> None:  # noqa: ANN001, F811
    _, order = make(env)
    keep(client, sales(bolts("2001", "4", "2.50")))
    page = client.get(f"/customer-orders/{order}").text
    assert "Reconciliation: Partially invoiced" in page and "Invoiced quantity" in page
    assert "Invoices counted against this order" in page and "2001" in page and "the rules (exact evidence)" in page
    assert "History" in page


def link(client: TestClient, order: int, invoice: int, **more: str):  # noqa: ANN201
    return post(client, f"/customer-orders/{order}/link", {"invoice_id": str(invoice), **more})


def test_a_person_links_an_invoice_that_names_no_po_and_the_page_and_audit_say_who(client: TestClient, env) -> None:  # noqa: ANN001, F811
    _, order = make(env)
    keep(client, sales(bolts("2003", "10", "2.50", po="")))
    invoice = invoice_id(env, "2003")
    refused = link(client, order, invoice)
    assert refused.status_code == 400 and "Reason" in refused.text  # the invoice does not name the PO: a reason
    done = link(client, order, invoice, reason="Customer confirmed it by phone")
    assert done.status_code == 303 and done.headers["location"] == f"/customer-orders/{order}"
    page = client.get(f"/customer-orders/{order}").text
    assert (
        "Reconciliation: Matched" in page and "Customer confirmed it by phone" in page and "buyer@example.com" in page
    )
    assert audited(env, "reconciliation.link") == ["reconciliation.link"]
    # the same decision twice is refused; withdrawing it lets the rules apply again
    assert link(client, order, invoice, reason="again").status_code == 400
    decision = rows(env, c.Allocation)[0]
    assert decision.basis == "person"
    gone = post(client, f"/customer-orders/{order}/withdraw", {"decision_id": str(decision.id)})
    assert gone.status_code == 303
    assert "Reconciliation: Matched" not in client.get(f"/customer-orders/{order}").text
    assert audited(env, "reconciliation.withdraw") == ["reconciliation.withdraw"]


def test_a_line_match_by_number_and_a_rejection_need_what_they_need(client: TestClient, env) -> None:  # noqa: ANN001, F811
    _, order = make(env)
    keep(client, sales(bolts("2003", "10", "2.50", po="")))
    invoice = invoice_id(env, "2003")
    assert link(client, order, invoice, invoice_line="1", po_line="", reason="x").status_code == 400
    assert link(client, order, invoice, invoice_line="9", po_line="1", reason="x").status_code == 400
    assert link(client, order, invoice, invoice_line="1", po_line="1", reason="same bolts").status_code == 303
    other = post(client, f"/customer-orders/{order}/reject-invoice", {"invoice_id": str(invoice), "reason": ""})
    assert other.status_code == 400  # a rejection always needs a reason
    assert (
        post(client, f"/customer-orders/{order}/reject-invoice", {"invoice_id": "0", "reason": "x"}).status_code == 400
    )


def test_a_rejection_keeps_the_rules_from_matching_that_invoice_again(client: TestClient, env) -> None:  # noqa: ANN001, F811
    _, order = make(env)
    keep(client, sales(bolts("2001", "10", "2.50")))
    assert "Reconciliation: Matched" in client.get(f"/customer-orders/{order}").text
    done = post(
        client,
        f"/customer-orders/{order}/reject-invoice",
        {"invoice_id": str(invoice_id(env, "2001")), "reason": "Wrong PO on the invoice"},
    )
    assert done.status_code == 303
    page = client.get(f"/customer-orders/{order}").text
    assert "Reconciliation: Matched" not in page and "does not belong to this PO" in page
    assert audited(env, "reconciliation.reject") == ["reconciliation.reject"]


def test_closing_cancelling_and_reopening_an_order_needs_a_reason_and_the_current_version(
    client: TestClient, env
) -> None:  # noqa: ANN001, F811
    _, order = make(env)
    version = rows(env, c.CustomerPO)[0].version
    assert (
        post(
            client, f"/customer-orders/{order}/state", {"to": "closed", "reason": "", "version": str(version)}
        ).status_code
        == 400
    )
    assert (
        post(
            client,
            f"/customer-orders/{order}/state",
            {"to": "closed", "reason": "Shipped", "version": str(version + 5)},
        ).status_code
        == 400
    )
    assert (
        post(
            client, f"/customer-orders/{order}/state", {"to": "weird", "reason": "x", "version": str(version)}
        ).status_code
        == 400
    )
    done = post(
        client, f"/customer-orders/{order}/state", {"to": "closed", "reason": "Shipped", "version": str(version)}
    )
    assert done.status_code == 303
    assert "Closed" in client.get("/customer-orders?status=closed").text
    again = post(client, f"/customer-orders/{order}/state", {"to": "closed", "reason": "Shipped", "version": "99"})
    assert again.status_code == 400
    version = rows(env, c.CustomerPO)[0].version
    assert (
        post(
            client, f"/customer-orders/{order}/state", {"to": "active", "reason": "Reopened", "version": str(version)}
        ).status_code
        == 303
    )
    assert audited(env, "customer_po.state") == ["customer_po.state", "customer_po.state"]


def test_every_action_needs_the_security_token_and_a_known_order(client: TestClient, env) -> None:  # noqa: ANN001, F811
    _, order = make(env)
    for path in ("link", "reject-invoice", "withdraw", "state"):
        assert client.post(f"/customer-orders/{order}/{path}", data={}, headers=ORIGIN).status_code in (400, 403)
        assert post(client, f"/customer-orders/9999/{path}", {}).status_code == 404
    assert client.get("/customer-orders/9999").status_code == 404


# ---- an invoice that waits for review


def changed(client: TestClient, env, *, qty: str = "12") -> int:  # noqa: ANN001, F811
    make(env)
    keep(client, sales(bolts("2001", "10", "2.50")))
    keep(client, sales(bolts("2001", qty, "2.50")), name="later.csv")  # the same invoice, different figures
    return invoice_id(env, "2001")


def test_a_changed_invoice_keeps_its_figures_until_a_person_decides_and_the_page_offers_both_ways(
    client: TestClient, env
) -> None:  # noqa: ANN001, F811
    invoice = changed(client, env)
    page = client.get(f"/sales-invoices/{invoice}").text
    assert (
        "Settle the review" in page and "Keep the accepted figures" in page and "Use the figures of an upload" in page
    )
    assert "Invoice changed since it was accepted" in page


def test_keeping_the_accepted_figures_needs_a_reason_and_is_audited(client: TestClient, env) -> None:  # noqa: ANN001, F811
    invoice = changed(client, env)
    version = rows(env, c.SalesInvoice)[0].version
    bare = post(client, f"/sales-invoices/{invoice}/resolve", {"action": "keep", "reason": "", "version": str(version)})
    assert bare.status_code == 400 and "Reason" in bare.text
    stale = post(
        client, f"/sales-invoices/{invoice}/resolve", {"action": "keep", "reason": "ok", "version": str(version + 3)}
    )
    assert stale.status_code == 400
    done = post(
        client,
        f"/sales-invoices/{invoice}/resolve",
        {"action": "keep", "reason": "The first was right", "version": str(version)},
    )
    assert done.status_code == 303
    assert rows(env, c.SalesInvoice)[0].state == "accepted" and rows(env, c.SalesInvoice)[0].total == D(25)
    assert audited(env, "sales_invoice.keep") == ["sales_invoice.keep"]
    assert (
        post(
            client, f"/sales-invoices/{invoice}/resolve", {"action": "keep", "reason": "again", "version": "9"}
        ).status_code
        == 400
    )


def test_using_an_uploads_figures_replaces_the_lines_and_the_order_follows(client: TestClient, env) -> None:  # noqa: ANN001, F811
    invoice = changed(client, env, qty="12")
    seen = [o for o in rows(env, c.SalesInvoiceObservation) if o.outcome == "changed"]
    version = rows(env, c.SalesInvoice)[0].version
    for wrong in ("", "999"):
        refused = post(
            client,
            f"/sales-invoices/{invoice}/resolve",
            {"action": "use", "upload": wrong, "reason": "x", "version": str(version)},
        )
        assert refused.status_code == 400
    done = post(
        client,
        f"/sales-invoices/{invoice}/resolve",
        {"action": "use", "upload": str(seen[0].id), "reason": "QuickBooks was corrected", "version": str(version)},
    )
    assert done.status_code == 303, done.text[:300]
    row = rows(env, c.SalesInvoice)[0]
    ln = rows(env, c.SalesInvoiceLine)[0]
    assert row.state == "accepted" and row.total == D(30) and ln.qty == D(12) and ln.item == "Bolts:M8"
    assert audited(env, "sales_invoice.use") == ["sales_invoice.use"]
    order = rows(env, c.CustomerPO)[0].id
    assert "Over-invoiced" in client.get("/customer-orders").text  # 12 billed against 10 ordered
    assert "Quantity mismatch" in client.get(f"/customer-orders/{order}").text


def test_an_invoice_can_be_placed_from_its_own_page_and_refusals_stay_on_it(client: TestClient, env) -> None:  # noqa: ANN001, F811
    _, order = make(env)
    keep(client, sales(bolts("2003", "10", "2.50", po="")))
    invoice = invoice_id(env, "2003")
    page = client.get(f"/sales-invoices/{invoice}").text
    assert "Where it is counted" in page and "not counted against any PO" in page and "CP-1001" in page
    refused = post(client, f"/sales-invoices/{invoice}/place", {"po_id": str(order), "what": "link", "reason": ""})
    assert refused.status_code == 400 and "Reason" in refused.text
    assert post(client, f"/sales-invoices/{invoice}/place", {"po_id": "", "what": "link"}).status_code == 400
    assert post(client, f"/sales-invoices/{invoice}/place", {"po_id": str(order), "what": "odd"}).status_code == 400
    done = post(client, f"/sales-invoices/{invoice}/place", {"po_id": str(order), "what": "link", "reason": "By phone"})
    assert done.status_code == 303 and done.headers["location"] == f"/customer-orders/{order}"
    assert "CP-1001" in client.get(f"/sales-invoices/{invoice}").text
    assert [a.basis for a in rows(env, c.Allocation) if a.basis == "person"] == ["person"]
