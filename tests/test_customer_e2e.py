"""PD-8.7, end to end through the web screens only: customers, customer POs by file, two QuickBooks uploads on different
days, and every scenario of T-7 meeting its expected status, then a person settling what waits. Synthetic records only.
Dates are worked out from today, so the 30-day rule is real and the test is not tied to a calendar day."""

from __future__ import annotations

import csv
import io
from datetime import date, timedelta

from fastapi.testclient import TestClient

from po_dashboard.db import customers as c
from test_customer_pages import rows
from test_customer_screens import invoice_id
from test_gate import ORIGIN, env, post, token_in  # noqa: F401
from test_orders_web import client  # noqa: F401
from test_sales_import import line, report
from test_sales_pages import keep

TODAY = date.today()
HEAD = "customer,po_number,currency,line_no,item,quantity,unit_price,po_date"


def ago(days: int) -> str:
    return (TODAY - timedelta(days=days)).isoformat()


def us(days: int) -> str:
    return (TODAY - timedelta(days=days)).strftime("%m/%d/%Y")


def import_orders(client: TestClient, *body: str) -> None:
    raw = ("\n".join([HEAD, *body]) + "\n").encode()
    token = token_in(client.get("/customer-orders/import").text)
    held = client.post(
        "/customer-orders/import",
        files={"file": ("orders.csv", raw, "text/csv")},
        headers={**ORIGIN, "X-CSRF-Token": token},
    )
    assert held.status_code == 200, held.text[:300]
    given = held.text.split('name="token" value="')[1].split('"')[0]
    assert post(client, "/customer-orders/import/confirm", {"token": given}).status_code == 303


def inv(number: str, item: str, qty: str, price: str, *, po: str, day: int = 10, who: str = "Globex Racing",
        kind: str = "Invoice", amount: str | None = None) -> str:  # fmt: skip
    return line(
        number, item, qty, price, amount or str(float(qty) * float(price)), kind=kind, po=po, day=us(day), who=who
    )


def upload(client: TestClient, *body: str, start: int, end: int, name: str) -> None:
    response = keep(client, report(*body), name=name, period_from=ago(start), period_to=ago(end))
    assert response.status_code == 303, response.text[:400]


def board(client: TestClient) -> dict[str, dict[str, str]]:
    body = client.get("/customer-orders/export.csv").content.decode("utf-8-sig")
    return {r["PO number"]: r for r in csv.DictReader(io.StringIO(body))}


def exceptions_of(client: TestClient) -> str:
    return client.get("/customer-orders/exceptions").text


def test_the_whole_flow_from_customers_to_settled_orders(client: TestClient, env) -> None:  # noqa: ANN001, F811
    # ---- customers, by the screen
    for name in ("Globex Racing",):
        assert post(client, "/customers/create", {"name": name}).status_code == 303
    # ---- customer POs, by file: one file, ten orders (the dates make some of them old enough to be called unbilled)
    import_orders(
        client,
        f"Globex Racing,G-1,USD,1,Bolts:M8,10,2.50,{ago(40)}",
        f"Globex Racing,G-2,USD,1,Bolts:M8,10,2.50,{ago(60)}",
        f"Globex Racing,G-3,USD,1,Bolts:M8,20,5,{ago(40)}",
        f"Globex Racing,G-4,USD,1,Bolts:M8,10,10,{ago(40)}",
        f"Globex Racing,G-5,USD,1,Bolts:M8,10,10,{ago(40)}",
        f"Globex Racing,G-6,USD,1,Bolts:M8,10,3,{ago(40)}",
        f"Globex Racing,G-7,USD,1,Bolts:M8,10,2,{ago(40)}",
        f"Globex Racing,G-9,USD,1,Bolts:M8,10,10,{ago(5)}",
        f"Globex Racing,G-10,USD,1,Bolts:M8,10,2,{ago(40)}",
    )
    assert len(board(client)) == 9
    # ---- the first upload: invoices from the quarter so far
    upload(
        client,
        "Globex Racing",
        inv("9001", "Bolts:M8", "10", "2.50", po="G-1"),  # 1: exactly billed
        inv("9003", "Bolts:M8", "8", "5", po="G-3"),  # 3: partly billed
        inv("9004", "Bolts:M8", "12", "10", po="G-4"),  # 4: more than ordered
        inv("9005", "Bolts:M8", "10", "11", po="G-5"),  # 5: a higher price
        inv("9006", "Bolts:M8", "4", "3", po="G-6"),  # 7: the first instalment
        inv("9007", "Bolts:M8", "10", "2", po=""),  # 8: names no PO at all
        inv("9010", "Bolts:M8", "10", "2", po="G-10"),  # 10: goods and freight
        inv("9010", "Freight", "1", "50", po="G-10"),
        inv("9011", "Bolts:M8", "-2", "2", po="G-10", kind="Credit Memo", amount="-4.00"),  # 10: a credit memo
        start=90, end=1, name="first.csv",
    )  # fmt: skip
    first = board(client)
    assert first["G-1"]["Status"] == "Matched" and first["G-1"]["Invoiced"] == "25.00"
    assert first["G-2"]["Status"] == "No invoice in the uploaded files"  # 2: old, and the upload covers its time
    assert "Possibly unbilled" in first["G-2"]["Exceptions"]
    assert first["G-3"]["Status"] == "Partially invoiced" and float(first["G-3"]["Apparent remaining"]) == 60
    assert first["G-4"]["Status"] == "Over-invoiced"
    assert first["G-5"]["Status"] == "Needs review" and "Unit price mismatch" in first["G-5"]["Exceptions"]
    assert first["G-6"]["Status"] == "Partially invoiced"
    assert first["G-7"]["Status"] != "Matched" and "Unmatched invoice" in exceptions_of(client)  # 8: waits for a person
    assert first["G-10"]["Status"] == "Needs review" and "Credit memo waiting" in first["G-10"]["Exceptions"]  # 10
    assert (
        first["G-9"]["Status"] != "Possibly unbilled" and "unbilled" not in first["G-9"]["Exceptions"].lower()
    )  # young
    queue = exceptions_of(client)
    for words in (
        "Unit price mismatch",
        "Quantity mismatch",
        "Partially invoiced",
        "Possibly unbilled",
        "Confirmed",
        "Preliminary",
    ):
        assert words in queue, words
    # ---- the same file again adds nothing
    again = keep(
        client,
        report("Globex Racing", inv("9001", "Bolts:M8", "10", "2.50", po="G-1")),
        name="first.csv",
        period_from=ago(90),
        period_to=ago(1),
    )
    assert again.status_code in (303, 400)
    # ---- a revision of G-9 changes nothing until a person approves it
    import_orders(client, f"Globex Racing,G-9,USD,1,Bolts:M8,10,12,{ago(5)}")
    revision = next(o for o in rows(env, c.CustomerPO) if o.po_number == "G-9" and o.revision == 1)
    assert board(client)["G-9"]["Status"] != "Matched"
    assert (
        post(client, f"/customer-orders/{revision.id}/approve", {"version": str(revision.version)}).status_code == 303
    )
    # ---- the second upload, from a later day: the second instalment, a repeat, the revised order, a stranger
    upload(
        client,
        "Globex Racing",
        inv("9006", "Bolts:M8", "4", "3", po="G-6", day=10),  # 6: counted once, not twice
        inv("9008", "Bolts:M8", "6", "3", po="G-6", day=3),  # 7: the second instalment
        inv("9009", "Bolts:M8", "10", "12", po="G-9", day=2),  # 9: billed at the revised price
        "Initech Corp",
        inv("9020", "Gear", "2", "5", po="I-1", who="Initech Corp", day=2),
        start=20, end=0, name="second.csv",
    )  # fmt: skip
    second = board(client)
    assert second["G-6"]["Status"] == "Matched" and float(second["G-6"]["Invoiced"]) == 30  # 12 + 18, each once
    assert second["G-1"]["Status"] == "Matched"  # untouched by the other upload
    assert second["G-9"]["Status"] == "Matched"  # the revised terms are the ones reconciled
    # ---- a customer name the app does not know is chosen by a person, then its order can be filed and matched
    assert "Initech Corp" in client.get("/sales-invoices/names").text
    assert (
        post(client, "/sales-invoices/names/map", {"name": "Initech Corp", "new_customer": "Initech Corp"}).status_code
        == 303
    )
    import_orders(client, f"Initech Corp,I-1,USD,1,Gear,2,5,{ago(3)}")
    assert board(client)["I-1"]["Status"] == "Matched"
    # ---- a person settles what waited: the invoice that named no PO
    order = next(o for o in rows(env, c.CustomerPO) if o.po_number == "G-7").id
    done = post(
        client,
        f"/customer-orders/{order}/link",
        {"invoice_id": str(invoice_id(env, "9007")), "reason": "Confirmed by phone"},
    )
    assert done.status_code == 303
    assert board(client)["G-7"]["Status"] == "Matched"
    # ---- and the queue no longer lists that invoice as unmatched, while the real discrepancies remain
    final = exceptions_of(client)
    assert f'/sales-invoices/{invoice_id(env, "9007")}"' not in final  # placed, so no longer in the queue
    assert "Unit price mismatch" in final and "Quantity mismatch" in final
    # ---- every order has a history and an audited trail
    page = client.get(f"/customer-orders/{order}").text
    assert "Confirmed by phone" in page and "History" in page
    assert board(client)["G-6"]["Latest upload"] != ""
