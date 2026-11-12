"""The Sales invoices screens (PD-8.3): upload, check the columns, see what it would do, keep it. Everyone signed in
can use them; nothing is kept before it is confirmed; nothing accepted is overwritten."""

from __future__ import annotations

from decimal import Decimal

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from po_dashboard.customers import service
from po_dashboard.db import customers as c
from po_dashboard.db.models import AuditLog
from test_gate import ORIGIN, env, new_client, post, token_in  # noqa: F401
from test_orders_web import client  # noqa: F401
from test_sales_import import GOOD, line, report

DAYS = {"period_from": "2026-11-01", "period_to": "2026-11-30"}


def rows(env, model):  # noqa: ANN001, ANN201, F811
    with Session(env[1]) as db:
        return list(db.scalars(select(model)))


def upload(client: TestClient, raw: bytes = GOOD, name: str = "sales.csv"):  # noqa: ANN201
    token = token_in(client.get("/sales-invoices/upload").text)
    return client.post(
        "/sales-invoices/upload", files={"file": (name, raw, "text/csv")}, headers={**ORIGIN, "X-CSRF-Token": token}
    )


def held(preview) -> str:  # noqa: ANN001
    return preview.text.split('name="token" value="')[1].split('"')[0]


def mapping_of(preview) -> dict[str, str]:  # noqa: ANN001
    """The mapping the preview shows as chosen, as the form would post it back."""
    out: dict[str, str] = {}
    for field in (
        "type",
        "number",
        "date",
        "customer",
        "po_reference",
        "item",
        "description",
        "qty",
        "unit_price",
        "amount",
    ):
        marker = f'<select id="map_{field}"'
        block = preview.text.split(marker)[1].split("</select>")[0]
        if "selected>" in block:
            out[f"map_{field}"] = block.split(" selected>")[0].split('value="')[-1].rstrip('"')
    return out


def keep(client: TestClient, raw: bytes = GOOD, name: str = "sales.csv", **form: str):  # noqa: ANN201
    preview = upload(client, raw, name)
    assert preview.status_code == 200, preview.text[:300]
    body = {"token": held(preview), **mapping_of(preview), **DAYS, **form}
    return post(client, "/sales-invoices/confirm", body)


# ---- who and where


def test_everyone_signed_in_can_use_the_screens_and_each_change_needs_the_token(client: TestClient, env) -> None:  # noqa: ANN001, F811
    for path in ("/sales-invoices", "/sales-invoices/upload", "/sales-invoices/names"):
        assert client.get(path).status_code == 200, path
    assert 'href="/sales-invoices"' in client.get("/customer-orders").text
    stranger = new_client(env[0])
    assert stranger.get("/sales-invoices", headers={"Accept": "text/html"}).status_code == 303
    for path in ("/sales-invoices/preview", "/sales-invoices/confirm", "/sales-invoices/names/map"):
        assert client.post(path, data={"token": "x"}, headers=ORIGIN).status_code == 403, path
        assert client.post(path, data={"token": "x", "csrf_token": "no"}, headers=ORIGIN).status_code == 403, path
    refused = client.post("/sales-invoices/upload", files={"file": ("a.csv", GOOD, "text/csv")}, headers=ORIGIN)
    assert refused.status_code == 403 and not rows(env, c.SalesImport)


def test_a_status_or_message_in_the_address_cannot_change_a_page(client: TestClient) -> None:
    page = client.get("/sales-invoices/names?status=500&error=%3Cb%3Efake%3C/b%3E")
    assert page.status_code == 200 and "fake" not in page.text


# ---- the preview


def test_the_preview_says_what_the_file_would_do_and_keeps_nothing(client: TestClient, env) -> None:  # noqa: ANN001, F811
    preview = upload(client)
    text = preview.text
    assert (
        preview.status_code == 200
        and "What the file would do: sales.csv" in text
        and "The column names are on row 5" in text
    )
    assert (
        "by line" in text
        and "<strong>2</strong> invoices found: 2 new" in text
        and "Rows: 3 read, 6 headings, 2 totals" in text
    )
    assert 'value="2026-11-03"' in text and 'value="2026-11-05"' in text  # the period, suggested from the dates
    assert "not known: Globex Racing" in text and "choose the customer afterwards" in text
    assert not rows(env, c.SalesImport) and not rows(env, c.SalesInvoice)


def test_a_wrong_mapping_is_shown_again_without_losing_the_file_and_a_chosen_one_changes_the_level(
    client: TestClient, env
) -> None:  # noqa: ANN001, F811
    preview = upload(client)
    token, chosen = held(preview), mapping_of(preview)
    invoice_level = {k: v for k, v in chosen.items() if k not in ("map_item", "map_qty", "map_unit_price")}
    again = post(client, "/sales-invoices/preview", {"token": token, **invoice_level, **DAYS})
    assert (
        again.status_code == 200
        and "by invoice total only" in again.text
        and "no item, quantity and price columns" in again.text
    )
    bad = post(client, "/sales-invoices/preview", {"token": token, **chosen, "map_amount": "Nope", **DAYS})
    assert bad.status_code == 400 and "no column called Nope" in bad.text
    twice = post(client, "/sales-invoices/preview", {"token": token, **chosen, "map_date": "Amount"})
    assert twice.status_code == 400 and "cannot be both" in twice.text
    lost = post(client, "/sales-invoices/preview", {"token": "wrong", **chosen})
    assert lost.status_code == 400 and "expired or was already used" in lost.text


def test_rows_that_are_not_read_are_counted_with_their_reasons(client: TestClient) -> None:
    raw = report(
        line("2001", "Bolt", "1", "1", "1"),
        line("P-1", "", "", "", "30.00", kind="Payment"),
        line("2002", "Gear", "1", "1", "1", day="not a date"),
    )
    text = upload(client, raw).text
    assert "2 not read" in text and "not a sales document this import reads: Payment" in text and "Invoice date" in text


def test_a_file_that_is_not_a_sales_export_or_is_damaged_gets_one_plain_message(client: TestClient, env) -> None:  # noqa: ANN001, F811
    for raw, words in (
        (b"a,b\n1,2\n", "does not look like a sales export"),
        (b"\x00\x01\x02 junk", "does not look like"),
        (b"PK\x03\x04 broken", "could not be opened"),
    ):
        bad = upload(client, raw, "x.xlsx" if raw.startswith(b"PK") else "x.csv")
        assert bad.status_code == 400 and words in bad.text and 'name="token"' not in bad.text
    big = upload(client, b"x" * (2 * 1024 * 1024 + 70 * 1024))
    assert big.status_code == 413 and "larger than" in big.text
    token = token_in(client.get("/sales-invoices/upload").text)
    none = client.post("/sales-invoices/upload", data={}, headers={**ORIGIN, "X-CSRF-Token": token})
    assert none.status_code in (400, 422) and not rows(env, c.SalesImport)


# ---- keeping it


def test_confirming_keeps_the_upload_and_the_pages_show_it(client: TestClient, env) -> None:  # noqa: ANN001, F811
    done = keep(client, remember="sales by customer")
    assert done.status_code == 303 and done.headers["location"] == "/sales-invoices?imported=2"
    page = client.get(done.headers["location"]).text
    assert "Kept the upload: 2 new invoices" in page and "2001" in page and "2002" in page and "sales.csv" in page
    assert "2 invoices name a customer the app does not know" in page
    batch = rows(env, c.SalesImport)[0]
    detail = client.get(f"/sales-invoices/imports/{batch.id}")
    assert (
        "Upload: sales.csv" in detail.text
        and "11/01/2026 to 11/30/2026" in detail.text
        and "Sales Price" in detail.text
    )
    assert "2 new, 0 unchanged" in detail.text
    totals = client.get(f"/sales-invoices/imports/{batch.id}?outcome=total").text
    assert "a total row" in totals and "a heading row" not in totals
    original = client.get(f"/sales-invoices/imports/{batch.id}/file")
    assert original.content == GOOD and "attachment" in original.headers["content-disposition"]
    assert (
        original.headers["x-content-type-options"] == "nosniff"
        and original.headers["content-type"] == "application/octet-stream"
    )
    audit = [a for a in rows(env, AuditLog) if a.action == "sales_import.apply"]
    assert len(audit) == 1 and '"new": 2' in (audit[0].after or "")


def test_a_saved_mapping_is_suggested_for_the_next_file_that_has_its_columns(client: TestClient) -> None:
    keep(client, remember="sales by customer")
    again = upload(client, report(line("3001", "Gear", "1", "1", "1")), "next.csv")
    assert again.status_code == 200 and "sales by customer" in again.text  # offered in the remember box


def test_the_dates_are_needed_and_a_forgotten_one_leaves_the_preview_to_try_again(client: TestClient, env) -> None:  # noqa: ANN001, F811
    preview = upload(client)
    token, chosen = held(preview), mapping_of(preview)
    wrong = post(
        client,
        "/sales-invoices/confirm",
        {"token": token, **chosen, "period_from": "2026-11-30", "period_to": "2026-11-01"},
    )
    assert wrong.status_code == 400 and "starts after it ends" in wrong.text and "What the file would do" in wrong.text
    assert not rows(env, c.SalesImport)
    fixed = post(client, "/sales-invoices/confirm", {"token": token, **chosen, **DAYS})  # the same preview still works
    assert fixed.status_code == 303 and len(rows(env, c.SalesInvoice)) == 2
    reused = post(client, "/sales-invoices/confirm", {"token": token, **chosen, **DAYS})
    assert (
        reused.status_code == 400
        and "expired or was already used" in reused.text
        and len(rows(env, c.SalesImport)) == 1
    )


def test_the_same_file_again_is_recognised_and_cannot_be_kept(client: TestClient, env) -> None:  # noqa: ANN001, F811
    keep(client)
    again = upload(client)
    assert "already imported on" in again.text and "disabled" in again.text
    direct = post(client, "/sales-invoices/confirm", {"token": held(again), **mapping_of(again), **DAYS})
    assert direct.status_code == 400 and "already imported" in direct.text
    assert len(rows(env, c.SalesImport)) == 1


def test_a_later_export_that_changes_an_invoice_leaves_the_accepted_figures_and_says_so(
    client: TestClient, env
) -> None:  # noqa: ANN001, F811
    keep(client)
    later = report(line("2001", "Bolts:M8", "10", "3.00", "30.00"), line("2001", "Washers:M8", "100", "0.015", "1.50"))
    preview = upload(client, later, "later.csv")
    assert "1 changed since they were accepted" in preview.text and "changed: waits for review" in preview.text
    post(client, "/sales-invoices/confirm", {"token": held(preview), **mapping_of(preview), **DAYS})
    invoice = next(i for i in rows(env, c.SalesInvoice) if i.invoice_key == "2001")
    assert invoice.state == "needs_review" and invoice.total == Decimal("26.5")
    page = client.get(f"/sales-invoices/{invoice.id}").text
    assert "needs a person to look" in page and "different" in page and "first seen" in page
    listed = client.get("/sales-invoices?state=needs_review").text
    assert "2001" in listed and "2002" not in listed


def test_an_invoice_page_shows_its_lines_and_what_is_not_goods(client: TestClient, env) -> None:  # noqa: ANN001, F811
    raw = report(
        line("2001", "Bolt", "10", "2.50", "25.00", memo="<b>zinc</b>"),
        line("2001", "Shipping", "", "", "5.00"),
        line("2001", "Sales Tax", "", "", "2.00"),
        line("C-5", "Bolt", "-1", "2.50", "-2.50", kind="Credit Memo"),
    )
    keep(client, raw)
    invoice = next(i for i in rows(env, c.SalesInvoice) if i.invoice_key == "2001")
    page = client.get(f"/sales-invoices/{invoice.id}").text
    assert "Invoice 2001" in page and "freight: not goods" in page and "tax: not goods" in page and "32.00 USD" in page
    assert "&lt;b&gt;zinc&lt;/b&gt;" in page and "<b>zinc</b>" not in page
    memo = next(i for i in rows(env, c.SalesInvoice) if i.kind == "credit_memo")
    assert (
        "Credit memo C-5" in client.get(f"/sales-invoices/{memo.id}").text
        and "-2.50 USD" in client.get(f"/sales-invoices/{memo.id}").text
    )


def test_markup_in_a_file_name_a_customer_or_a_reference_is_shown_as_text(client: TestClient) -> None:
    raw = report(line("2001", "Bolt", "1", "1", "1", who="<script>x</script>", po="<i>CP</i>"))
    keep(client, raw, "<img src=x>.csv")
    page = client.get("/sales-invoices").text
    assert (
        "<script>x" not in page and "<i>CP</i>" not in page and "<img src=x>" not in page and "&lt;script&gt;" in page
    )


def test_the_list_filters_by_search_kind_state_and_whether_the_customer_is_chosen(client: TestClient, env) -> None:  # noqa: ANN001, F811
    service.create_customer(Session(env[1]), name="Globex Racing")  # noqa: SIM115
    keep(
        client,
        report(
            line("2001", "Bolt", "1", "1", "1"),
            line("2002", "Gear", "1", "1", "1", who="Initech Corp"),
            line("C-1", "Bolt", "-1", "1", "-1", kind="Credit Memo"),
        ),
    )
    assert (
        "2002" in client.get("/sales-invoices?q=initech").text
        and "2001" not in client.get("/sales-invoices?q=initech").text
    )
    assert (
        "C-1" in client.get("/sales-invoices?kind=credit_memo").text
        and "2001" not in client.get("/sales-invoices?kind=credit_memo").text
    )
    assert client.get("/sales-invoices?unmapped=1").text.count("not chosen") >= 1
    assert "No invoice matches" in client.get("/sales-invoices?q=zzz").text
    assert client.get("/sales-invoices?page=zz&kind=%00&state=x").status_code == 200


# ---- customers the export names that the app does not know


def test_a_name_the_app_does_not_know_is_linked_by_choosing_or_making_a_customer(client: TestClient, env) -> None:  # noqa: ANN001, F811
    keep(client)  # no customer exists yet, so both names are unknown
    page = client.get("/sales-invoices/names").text
    assert "Globex Racing" in page and "Initech Corp" in page and "1 invoice carries this name" in page
    with Session(env[1]) as db:
        globex = service.create_customer(db, name="Globex Racing Ltd")
        db.commit()
        gid = globex.id
    nothing = post(client, "/sales-invoices/names/map", {"name": "Globex Racing"})
    assert nothing.status_code == 400 and "Choose a customer" in nothing.text
    assert (
        post(client, "/sales-invoices/names/map", {"name": "Globex Racing", "customer_id": str(gid)}).status_code == 303
    )
    assert (
        post(client, "/sales-invoices/names/map", {"name": "Initech Corp", "new_customer": "Initech Inc"}).status_code
        == 303
    )
    assert all(i.customer_id for i in rows(env, c.SalesInvoice))
    assert "Every customer in the exports is known" in client.get("/sales-invoices/names").text
    assert "not chosen:" not in client.get("/sales-invoices").text  # the pill, not the filter's label
    actions = [a.action for a in rows(env, AuditLog)]
    assert actions.count("sales_invoice.map_customer") == 2 and "customer.name_add" in actions
    with Session(env[1]) as db:  # the next export finds both by itself
        assert service.find_for_export(db, "Globex Racing") is not None and service.find_for_export(db, "Initech Corp")


def test_an_invoice_or_upload_that_does_not_exist_is_a_readable_page(client: TestClient) -> None:
    for path in ("/sales-invoices/99999", "/sales-invoices/imports/99999"):
        page = client.get(path)
        assert page.status_code == 404 and "was not found" in page.text
    assert client.get("/sales-invoices/imports/99999/file").status_code == 404
