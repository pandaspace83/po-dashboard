"""Security and regression for Phase 8 (PD-8.6): hostile uploads are refused in plain words and keep nothing, every
route that changes something needs a session, the security token and a local Origin, every change leaves an audit row,
and a populated database upgrades without touching a purchasing record."""

from __future__ import annotations

import io
import re
import zipfile
from decimal import Decimal as D
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from po_dashboard.config import Settings
from po_dashboard.customers import intake, service
from po_dashboard.customers.intake import LineData, PoData
from po_dashboard.db import customers as c
from po_dashboard.db.engine import make_engine
from po_dashboard.db.ledger import POLine, PurchaseOrder, Vendor
from po_dashboard.db.migrate import upgrade
from po_dashboard.db.models import AuditLog
from test_customer_pages import rows
from test_customer_screens import audited, bolts, invoice_id, link, make, sales
from test_gate import ORIGIN, env, new_client, post, token_in  # noqa: F401
from test_orders_web import client  # noqa: F401
from test_sales_import import line, report
from test_sales_pages import keep
from test_sheets import NS, SHEET, replace_part, workbook

PREFIXES = ("/customer-orders", "/customers", "/sales-invoices")


# ---- hostile uploads, through both upload pages


def hostile_files() -> dict[str, tuple[bytes, str]]:
    secret_part = (
        '<?xml version="1.0"?><!DOCTYPE x [<!ENTITY s SYSTEM "file:///etc/hosts">]>'
        f'<worksheet {NS}><sheetData><row r="1"><c r="A1" t="inlineStr"><is><t>&s;</t></is></c></row>'
        "</sheetData></worksheet>"
    ).encode()
    huge = (
        f'<?xml version="1.0"?><worksheet {NS}><dimension ref="A1:XFD1048576"/><sheetData>'
        '<row r="1"><c r="A1" t="inlineStr"><is><t>a</t></is></c></row>'
        '<row r="1048576"><c r="XFD1048576" t="inlineStr"><is><t>z</t></is></c></row></sheetData></worksheet>'
    ).encode()
    plain = workbook(["a"])
    return {
        "a zip bomb": (replace_part(plain, "xl/media/padding.bin", b"\0" * (21 * 1024 * 1024)), "x.xlsx"),
        "a macro workbook": (replace_part(plain, "xl/vbaProject.bin", b"\0" * 100), "x.xlsx"),
        "an outside-file trick": (replace_part(plain, SHEET, secret_part), "x.xlsx"),
        "a sheet that claims to be huge": (replace_part(plain, SHEET, huge), "x.xlsx"),
        "a zip with no workbook": (zipped("readme.txt", b"hello"), "x.xlsx"),
        "binary junk named csv": (b"\x00\x01\x02\xff" * 50, "x.csv"),
        "a broken workbook": (b"PK\x03\x04 broken", "x.xlsx"),
        "sixty-one columns": ((",".join(f"c{n}" for n in range(61)) + "\n1\n").encode(), "x.csv"),
        "an empty file": (b"", "x.csv"),
    }


def zipped(name: str, body: bytes) -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as target:
        target.writestr(name, body)
    return out.getvalue()


@pytest.mark.parametrize("which", list(hostile_files()))
def test_a_hostile_file_is_refused_in_plain_words_by_both_upload_pages_and_keeps_nothing(
    which: str,
    client: TestClient,
    env,  # noqa: ANN001, F811
) -> None:
    raw, name = hostile_files()[which]
    token = token_in(client.get("/customer-orders/import").text)
    headers = {**ORIGIN, "X-CSRF-Token": token}
    for path in ("/customer-orders/import", "/sales-invoices/upload"):
        response = client.post(path, files={"file": (name, raw, "application/octet-stream")}, headers=headers)
        assert response.status_code == 400, (path, response.status_code)
        assert 'name="token"' not in response.text  # nothing to confirm
        assert "root:" not in response.text and "Traceback" not in response.text
    assert not rows(env, c.SalesImport) and not rows(env, c.SalesInvoice) and not rows(env, c.CustomerPO)


def test_a_file_that_sends_no_size_or_too_many_parts_is_refused_not_crashed(client: TestClient) -> None:
    token = token_in(client.get("/sales-invoices/upload").text)
    headers = {**ORIGIN, "X-CSRF-Token": token}
    many = {f"f{n}": ("a.csv", b"a,b\n", "text/csv") for n in range(8)}
    for path in ("/customer-orders/import", "/sales-invoices/upload"):
        assert client.post(path, files=many, headers=headers).status_code < 500
        assert client.post(path, data={"file": "not a file"}, headers=headers).status_code in (400, 411)


def test_formulas_and_markup_in_a_file_stay_text_on_every_page_and_in_the_csv(client: TestClient, env) -> None:  # noqa: ANN001, F811
    make(env, name="<script>alert(1)</script> Co")
    evil = "=cmd|' /C calc'!A0"
    raw = (
        "\n".join(
            [
                "Test Co",
                "Sales by Customer Detail",
                "November 2026",
                "",
                ",Type,Date,Num,P. O. #,Name,Memo,Item,Qty,Sales Price,Amount",
                "<img src=x onerror=alert(1)>",
                f",Invoice,11/03/2026,<b>2001</b>,CP-1001,<img src=x onerror=alert(1)>,{evil},{evil},4,2.50,10.00",
            ]
        )
        + "\n"
    ).encode()
    assert keep(client, raw).status_code == 303
    for path in ("/customer-orders", "/customer-orders/exceptions", "/sales-invoices", "/sales-invoices/names"):
        page = client.get(path).text
        assert "<script>alert" not in page and "<img src=x" not in page and "<b>2001" not in page, path
    invoice = rows(env, c.SalesInvoice)[0]
    detail = client.get(f"/sales-invoices/{invoice.id}").text
    assert "<img src=x" not in detail and "&lt;b&gt;2001" in detail
    body = client.get("/customer-orders/export.csv").content.decode("utf-8-sig")
    assert "\r\n=" not in body and ",=" not in body and "'<script>" not in body.replace("<script>", "")


# ---- every changing route needs a session, the token and a local Origin


def changing_routes(app) -> list[tuple[str, str]]:  # noqa: ANN001
    found = []
    for template, methods in app.openapi()["paths"].items():
        if template.startswith(PREFIXES):
            found += [(m.upper(), template) for m in methods if m != "get"]
    return found


def test_every_changing_route_needs_the_token_and_a_local_origin_and_never_answers_500(client: TestClient, env) -> None:  # noqa: ANN001, F811
    routes = changing_routes(env[0])
    assert len(routes) >= 24
    token = token_in(client.get("/customer-orders").text)
    for method, template in routes:
        path = re.sub(r"\{[^}]+\}", "1", template)
        before = len(rows(env, AuditLog))
        no_token = client.request(method, path, data={}, headers=ORIGIN)
        assert no_token.status_code in (400, 403), (method, path, no_token.status_code)
        foreign = client.request(method, path, data={"csrf_token": token}, headers={"Origin": "http://evil.example"})
        assert foreign.status_code in (400, 403), (method, path, foreign.status_code)
        empty = client.request(method, path, data={"csrf_token": token}, headers=ORIGIN)
        assert empty.status_code < 500, (method, path, empty.status_code, empty.text[:200])
        assert len(rows(env, AuditLog)) == before, (method, path)  # a refused or empty request changes nothing


def test_every_route_refuses_a_request_with_no_session_in_these_sections(env) -> None:  # noqa: ANN001, F811
    anonymous = new_client(env[0])
    for method, template in changing_routes(env[0]):
        assert anonymous.request(method, re.sub(r"\{[^}]+\}", "1", template), headers=ORIGIN).status_code in (
            303,
            401,
            403,
        )


# ---- an audit row for every action


AUDITED = {
    ("POST", "/customers/create"): "customer.create",
    ("POST", "/customers/{customer_id}/update"): "customer.update",
    ("POST", "/customers/{customer_id}/active"): "customer.deactivate",
    ("POST", "/customers/{customer_id}/names/add"): "customer.name_add",
    ("POST", "/customers/{customer_id}/names/{name_id}/remove"): "customer.name_remove",
    ("POST", "/customers/{customer_id}/domains/add"): "customer.domain_add",
    ("POST", "/customers/{customer_id}/domains/{domain_id}/remove"): "customer.domain_remove",
    ("POST", "/customer-orders/create"): "customer_po.create",
    ("POST", "/customer-orders/import/confirm"): "customer_po.import",
    ("POST", "/customer-orders/waiting/{waiting_id}/resolve"): "customer_po.waiting_resolve",
    ("POST", "/customer-orders/waiting/{waiting_id}/reject"): "customer_po.waiting_reject",
    ("POST", "/customer-orders/{order_id}/approve"): "customer_po.approve",
    ("POST", "/customer-orders/{order_id}/reject"): "customer_po.reject",
    ("POST", "/customer-orders/{order_id}/state"): "customer_po.state",
    ("POST", "/customer-orders/{order_id}/link"): "reconciliation.link",
    ("POST", "/customer-orders/{order_id}/reject-invoice"): "reconciliation.reject",
    ("POST", "/customer-orders/{order_id}/withdraw"): "reconciliation.withdraw",
    ("POST", "/sales-invoices/confirm"): "sales_import.apply",
    ("POST", "/sales-invoices/names/map"): "sales_invoice.map_customer",
    ("POST", "/sales-invoices/{invoice_id}/resolve"): "sales_invoice.keep",
    ("POST", "/sales-invoices/{invoice_id}/place"): "reconciliation.link",
}
CHANGES_NOTHING = {  # a preview or a form check: it keeps nothing until a person confirms
    ("POST", "/customer-orders/import"),
    ("POST", "/sales-invoices/upload"),
    ("POST", "/sales-invoices/preview"),
}


def test_every_changing_route_is_either_audited_below_or_changes_nothing(env) -> None:  # noqa: ANN001, F811
    assert set(changing_routes(env[0])) == set(AUDITED) | CHANGES_NOTHING


def test_each_action_leaves_its_audit_row_with_who_did_it(client: TestClient, env) -> None:  # noqa: ANN001, F811
    csv = b"customer,po_number,currency,line_no,item,quantity,unit_price\nNobody Inc,NB-1,USD,1,Gear,4,25\n"
    for number in ("NB-2",):  # two orders for customers the app does not know: one is filed, one is turned away
        csv += f"Nobody Inc,{number},USD,1,Gear,1,1\n".encode()
    token = token_in(client.get("/customer-orders/import").text)
    held = client.post("/customer-orders/import", files={"file": ("o.csv", csv, "text/csv")},
                       headers={**ORIGIN, "X-CSRF-Token": token})  # fmt: skip
    assert (
        post(
            client,
            "/customer-orders/import/confirm",
            {"token": held.text.split('name="token" value="')[1].split('"')[0]},
        ).status_code
        == 303
    )
    waiting = rows(env, c.CustomerPOIntake)
    assert len(waiting) == 2
    assert (
        post(
            client,
            f"/customer-orders/waiting/{waiting[0].id}/resolve",
            {f"customer_{waiting[0].id}": "", "new_customer": "Nobody Inc"},
        ).status_code
        == 303
    )
    assert post(client, f"/customer-orders/waiting/{waiting[1].id}/reject", {"reason": "Not ours"}).status_code == 303
    customer = rows(env, c.Customer)[0].id
    assert post(client, f"/customers/{customer}/names/add", {"names": "Nobody Incorporated"}).status_code == 303
    assert post(client, f"/customers/{customer}/domains/add", {"domains": "nobody.example"}).status_code == 303
    name, domain = rows(env, c.CustomerName)[0].id, rows(env, c.CustomerDomain)[0].id
    assert post(client, f"/customers/{customer}/names/{name}/remove").status_code == 303
    assert post(client, f"/customers/{customer}/domains/{domain}/remove").status_code == 303
    version = rows(env, c.Customer)[0].version
    assert (
        post(
            client, f"/customers/{customer}/update", {"name": "Nobody Inc", "notes": "n", "version": str(version)}
        ).status_code
        == 303
    )
    version = rows(env, c.Customer)[0].version
    assert post(client, f"/customers/{customer}/active", {"active": "", "version": str(version)}).status_code == 303
    assert post(client, "/customers/create", {"name": "Side Co"}).status_code == 303
    version = rows(env, c.Customer)[0].version
    assert post(client, f"/customers/{customer}/active", {"active": "1", "version": str(version)}).status_code == 303
    # a revision of NB-1, approved and then another rejected
    with Session(env[1]) as db:
        for price in ("26", "27"):
            result = intake.file_po(
                db, PoData("NB-1", "USD", (LineData("Gear", D(4), D(price)),), customer_id=customer)
            )
            db.commit()
            assert result.kind == "revision"
            if price == "26":
                approve = result.po.id
            else:
                refuse = result.po.id
    post(client, f"/customer-orders/{approve}/approve", {"version": "1"})
    post(client, f"/customer-orders/{refuse}/reject", {"reason": "Wrong", "version": "1"})
    # the sales side
    _, order = make(env)
    assert keep(client, sales(bolts("2001", "10", "2.50"), bolts("2003", "10", "2.50", po=""))).status_code == 303
    inv_unref = invoice_id(env, "2003")
    assert link(client, order, inv_unref, reason="by phone").status_code == 303
    assert (
        post(
            client,
            f"/customer-orders/{order}/reject-invoice",
            {"invoice_id": str(invoice_id(env, "2001")), "reason": "wrong"},
        ).status_code
        == 303
    )
    decision = next(a for a in rows(env, c.Allocation) if a.basis == "person" and a.state == "confirmed")
    assert post(client, f"/customer-orders/{order}/withdraw", {"decision_id": str(decision.id)}).status_code == 303
    assert (
        post(
            client, f"/sales-invoices/{inv_unref}/place", {"po_id": str(order), "what": "link", "reason": "again"}
        ).status_code
        == 303
    )
    keep(client, sales(bolts("2001", "12", "2.50")), name="later.csv")
    changed = next(i for i in rows(env, c.SalesInvoice) if i.state == "needs_review")
    assert (
        post(
            client,
            f"/sales-invoices/{changed.id}/resolve",
            {"action": "keep", "reason": "ok", "version": str(changed.version)},
        ).status_code
        == 303
    )
    mine_now = next(o for o in rows(env, c.CustomerPO) if o.id == order)
    closed = post(
        client, f"/customer-orders/{order}/state", {"to": "closed", "reason": "Done", "version": str(mine_now.version)}
    )
    assert closed.status_code == 303
    stranger = report_for("Unknown Buyer")
    assert keep(client, stranger, name="stranger.csv").status_code == 303
    mapped = post(client, "/sales-invoices/names/map", {"name": "Unknown Buyer", "new_customer": "Unknown Buyer"})
    assert mapped.status_code == 303
    done = {a.action for a in rows(env, AuditLog)}
    missing = {action for action in AUDITED.values() if action not in done}
    assert not missing, missing
    mine = [a for a in rows(env, AuditLog) if a.action in set(AUDITED.values())]
    by_person = {a.action for a in mine if a.user_id is not None}
    assert by_person >= set(AUDITED.values()), set(AUDITED.values()) - by_person  # the screens name who did it
    assert audited(env, "customer_po.import") == ["customer_po.import"]


def report_for(who: str) -> bytes:
    return report(who, line("3001", "Gear", "1", "5", "5.00", who=who, po="X-1"))


# ---- the migration on a populated database


def test_a_populated_database_upgrades_again_and_back_without_touching_a_purchasing_record(tmp_path: Path) -> None:
    from alembic import command

    from po_dashboard.db.migrate import alembic_config

    settings = Settings(data_dir=tmp_path / "data")
    engine = upgrade(settings)
    with Session(engine) as db:
        vendor = Vendor(name="Acme Fasteners")
        db.add(vendor)
        db.flush()
        order = PurchaseOrder(po_number="4521", vendor_id=vendor.id, currency="USD")
        db.add(order)
        db.flush()
        db.add(POLine(po_id=order.id, line_no=1, item="Bolt", ordered_qty=D("10"), unit="EA", unit_price=D("2.5")))
        customer = service.create_customer(db, name="Globex Racing")
        intake.file_po(db, PoData("CP-1", "USD", (LineData("Bolt", D(1), D(1)),), customer_id=customer.id))
        db.commit()

    def purchasing(eng) -> list[list[tuple]]:  # noqa: ANN001
        with eng.connect() as conn:
            return [conn.exec_driver_sql(f"select * from {t} order by 1").all()  # noqa: S608 - fixed names
                    for t in ("vendor", "purchase_order", "po_line")]  # fmt: skip

    before = purchasing(engine)
    engine.dispose()
    again = upgrade(settings)  # already at the newest version: nothing to do, nothing lost
    assert purchasing(again) == before
    command.downgrade(alembic_config(again), "0009")
    assert purchasing(again) == before  # going back removes only the customer side
    again.dispose()
    last = upgrade(settings)
    assert purchasing(last) == before
    with Session(last) as db:
        assert db.query(c.Customer).count() == 0  # the customer side was removed on the way back, and starts empty
    last.dispose()
    assert make_engine(settings) is not None
