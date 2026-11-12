"""The Customers and Customer orders screens (PD-8.2): everyone signed in can use them, every change is checked and
audited, and nothing is filed, replaced or guessed without a person."""

from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from po_dashboard.customers import intake, po_import, service
from po_dashboard.customers.intake import LineData, PoData
from po_dashboard.db import customers as c
from po_dashboard.db.models import AuditLog
from test_gate import ORIGIN, env, new_client, post, sign_in, token_in  # noqa: F401
from test_orders_web import client  # noqa: F401

from decimal import Decimal  # isort: skip

HEADER = ",".join(po_import.COLUMNS)


def made(env, name: str = "Globex Racing", **more: str) -> int:  # noqa: ANN001, F811
    with Session(env[1]) as db:
        customer = service.create_customer(db, name=name, **more)
        db.commit()
        return customer.id


def rows(env, model):  # noqa: ANN001, ANN201, F811
    with Session(env[1]) as db:
        return list(db.scalars(select(model)))


def audited(env) -> list[str]:  # noqa: ANN001, F811
    return [a.action for a in rows(env, AuditLog) if a.action.startswith("customer")]


def order_form(customer_id: int | str, number: str = "CP-100", price: str = "100", **more: str) -> dict[str, object]:
    form: dict[str, object] = {
        "po_number": number, "customer_id": str(customer_id), "currency": "USD", "po_date": "2026-11-10",
        "stated_total": "", "line_item": ["WIDGET-1", "WIDGET-2", ""], "line_description": ["", "", ""],
        "line_quantity": ["10", "5", ""], "line_unit": ["EA", "EA", ""], "line_unit_price": [price, "20", ""],
        "line_line_amount": ["", "", ""],
    }  # fmt: skip
    form.update(more)
    return form


def file_order(client: TestClient, **form: object):  # noqa: ANN201
    page = client.get("/customer-orders/new")
    return client.post("/customer-orders/create", data={"csrf_token": token_in(page.text), **form}, headers=ORIGIN)


def upload(client: TestClient, raw: bytes, path: str = "/customer-orders/import", name: str = "orders.csv"):  # noqa: ANN201
    token = token_in(client.get("/customer-orders/import").text)
    return client.post(path, files={"file": (name, raw, "text/csv")}, headers={**ORIGIN, "X-CSRF-Token": token})


def csv(*body: str) -> bytes:
    return ("\n".join([HEADER, *body]) + "\n").encode()


ROW1 = "Globex Racing,CP-1001,USD,1,Hex bolt M8,10,2.50,2026-11-04,26.50,EA,Zinc plated,25.00"
ROW2 = "Globex Racing,CP-1001,USD,2,Washer M8,100,0.015,2026-11-04,26.50,EA,,1.50"


# ---- who and where


def test_everyone_signed_in_can_use_the_customer_screens_and_the_nav_shows_them(client: TestClient, env) -> None:  # noqa: ANN001, F811
    cid = made(env)
    for path in ("/customer-orders", "/customer-orders/new", "/customer-orders/import", "/customer-orders/waiting",
                 "/customers", "/customers/new", f"/customers/{cid}"):  # fmt: skip
        assert client.get(path).status_code == 200, path  # a buyer, not only an admin (DEC-56)
    assert 'href="/customer-orders"' in client.get("/").text
    stranger = new_client(env[0])
    assert stranger.get("/customer-orders", headers={"Accept": "text/html"}).status_code == 303


def test_every_change_needs_the_security_token(client: TestClient, env) -> None:  # noqa: ANN001, F811
    cid = made(env)
    for path in ("/customers/create", f"/customers/{cid}/update", f"/customers/{cid}/names/add",
                 f"/customers/{cid}/domains/add", f"/customers/{cid}/active", "/customer-orders/create",
                 "/customer-orders/import/confirm", "/customer-orders/1/approve", "/customer-orders/1/reject",
                 "/customer-orders/waiting/1/resolve", "/customer-orders/waiting/1/reject"):  # fmt: skip
        assert client.post(path, data={"name": "x"}, headers=ORIGIN).status_code == 403, path
        assert client.post(path, data={"name": "x", "csrf_token": "no"}, headers=ORIGIN).status_code == 403, path
    assert len(rows(env, c.Customer)) == 1


# ---- customers


def test_a_customer_is_added_and_its_page_shows_the_names_and_domains(client: TestClient, env) -> None:  # noqa: ANN001, F811
    entered = {"name": "Globex Racing", "qb_names": "Globex Racing Inc\nGlobex", "domains": "globex.test",
               "contact_email": "buyer@globex.test"}  # fmt: skip
    response = post(client, "/customers/create", entered)
    assert response.status_code == 303
    page = client.get(response.headers["location"]).text
    assert "Globex Racing Inc" in page and "globex.test" in page and "buyer@globex.test" in page
    assert audited(env) == ["customer.create"] and "Globex Racing" in client.get("/customers").text


def test_a_bad_customer_is_refused_and_the_boxes_keep_what_was_typed(client: TestClient, env) -> None:  # noqa: ANN001, F811
    made(env)
    refused = post(
        client, "/customers/create", {"name": "globex   RACING", "domains": "kept.test", "notes": "kept note"}
    )
    assert refused.status_code == 400 and "already exists" in refused.text
    assert 'value="kept.test"' in refused.text and "kept note" not in refused.text.replace("kept note</textarea>", "")
    assert len(rows(env, c.Customer)) == 1


def test_a_customer_is_changed_with_a_saved_note_and_a_stale_page_is_refused(client: TestClient, env) -> None:  # noqa: ANN001, F811
    cid = made(env)
    version = rows(env, c.Customer)[0].version
    done = post(
        client,
        f"/customers/{cid}/update",
        {"name": "Globex Racing LLC", "contact_name": "Pat", "version": str(version)},
    )
    assert done.status_code == 303 and "Saved" in client.get(done.headers["location"]).text
    stale = post(client, f"/customers/{cid}/update", {"name": "Again", "version": str(version)})
    assert stale.status_code == 400 and "changed by someone else" in stale.text
    assert rows(env, c.Customer)[0].name == "Globex Racing LLC"


def test_names_and_domains_are_added_and_removed_from_the_page(client: TestClient, env) -> None:  # noqa: ANN001, F811
    cid = made(env)
    assert post(client, f"/customers/{cid}/names/add", {"names": "Globex East"}).status_code == 303
    assert post(client, f"/customers/{cid}/domains/add", {"domains": "globex.test"}).status_code == 303
    page = client.get(f"/customers/{cid}").text
    assert "Globex East" in page and "globex.test" in page
    other = made(env, "Initech")
    clash = post(client, f"/customers/{other}/names/add", {"names": "globex east"})
    assert clash.status_code == 400 and "already used for Globex Racing" in clash.text
    name, domain = rows(env, c.CustomerName)[0], rows(env, c.CustomerDomain)[0]
    assert post(client, f"/customers/{cid}/names/{name.id}/remove").status_code == 303
    assert post(client, f"/customers/{cid}/domains/{domain.id}/remove").status_code == 303
    assert not rows(env, c.CustomerName) and not rows(env, c.CustomerDomain)
    assert post(client, "/customers/99999/names/add", {"names": "x"}).status_code == 404


def test_a_customer_can_be_made_inactive_and_then_takes_no_new_orders(client: TestClient, env) -> None:  # noqa: ANN001, F811
    cid = made(env)
    version = rows(env, c.Customer)[0].version
    assert post(client, f"/customers/{cid}/active", {"active": "0", "version": str(version)}).status_code == 303
    assert "inactive" in client.get("/customers").text
    refused = file_order(client, **order_form(cid))
    assert refused.status_code == 400 and "not active" in refused.text and not rows(env, c.CustomerPO)


def test_the_customer_search_answers_with_names_only_and_a_wildcard_is_just_text(client: TestClient, env) -> None:  # noqa: ANN001, F811
    made(env)
    assert client.get("/customers/search?q=glob").json() == [
        {"id": rows(env, c.Customer)[0].id, "name": "Globex Racing"}
    ]
    assert client.get("/customers/search?q=%25").json() == [] and client.get("/customers/search?q=").json() == []


def test_markup_in_a_customer_or_po_number_is_shown_as_text(client: TestClient, env) -> None:  # noqa: ANN001, F811
    cid = made(env, "<script>alert(1)</script>")
    assert post(client, "/customer-orders/create", {}).status_code in (400, 422)  # nothing to file
    filed = file_order(client, **order_form(cid, "<b>CP</b>-1"))
    page = client.get(filed.headers["location"]).text
    assert "<script>alert(1)" not in page and "<b>CP</b>" not in page and "&lt;b&gt;CP&lt;/b&gt;-1" in page


# ---- by hand


def test_an_order_is_filed_by_hand_and_its_page_shows_the_lines_and_totals(client: TestClient, env) -> None:  # noqa: ANN001, F811
    cid = made(env)
    response = file_order(client, **order_form(cid))
    assert response.status_code == 303
    page = client.get(response.headers["location"]).text
    assert "CP-100" in page and "WIDGET-1" in page and "WIDGET-2" in page and "1100.00 USD" in page
    assert "Revision 0: active" in page and "11/10/2026" in page
    assert [a for a in audited(env) if a.startswith("customer_po")] == ["customer_po.create"]
    listed = client.get("/customer-orders").text
    assert "Globex Racing" in listed and "CP-100" in listed and "Open" in listed


def test_a_bad_order_lists_every_problem_and_keeps_every_box(client: TestClient, env) -> None:  # noqa: ANN001, F811
    cid = made(env)
    form = order_form(
        cid, "CP-9", line_quantity=["0", "x", ""], po_date="not a date", line_unit_price=["100", "20", ""]
    )
    refused = file_order(client, **form)
    assert refused.status_code == 400
    for words in ("PO date", "Line 1: Quantity", "Line 2: Quantity"):
        assert words in refused.text
    assert 'value="CP-9"' in refused.text and 'value="WIDGET-2"' in refused.text and not rows(env, c.CustomerPO)
    nobody = file_order(client, **order_form("", "CP-10"))
    assert nobody.status_code == 400 and "Choose a customer" in nobody.text
    amount = file_order(client, **order_form(cid, "CP-11", line_line_amount=["999", "", ""]))
    assert "not quantity times price" in amount.text
    nothing = file_order(
        client,
        **order_form(
            cid,
            "CP-12",
            line_item=["", "", ""],
            line_quantity=["", "", ""],
            line_unit_price=["", "", ""],
            line_unit=["", "", ""],
        ),
    )
    assert nothing.status_code == 400 and "at least one line" in nothing.text  # fmt: skip


def test_the_same_order_again_changes_nothing_and_says_so(client: TestClient, env) -> None:  # noqa: ANN001, F811
    cid = made(env)
    first = file_order(client, **order_form(cid))
    again = file_order(client, **order_form(cid, "cp 100"))
    assert again.status_code == 303 and again.headers["location"].endswith("?filed=copy")
    assert "already here with the same terms" in client.get(again.headers["location"]).text
    assert len(rows(env, c.CustomerPO)) == 1 and first.headers["location"] in again.headers["location"]


def test_a_changed_order_is_a_revision_the_page_shows_the_changes_and_a_person_approves_it(
    client: TestClient, env
) -> None:  # noqa: ANN001, F811
    cid = made(env)
    original = file_order(client, **order_form(cid)).headers["location"]
    revised = file_order(client, **order_form(cid, "cp 100", price="110"))  # the customer spells it another way
    assert revised.headers["location"].endswith("?filed=revision")
    page = client.get(original).text
    assert "Revision 1 is waiting for approval" in page and "Line 1 unit price: 100 to 110" in page
    listed = client.get("/customer-orders").text
    assert "revision 1 waiting" in listed
    pending = next(o for o in rows(env, c.CustomerPO) if o.revision == 1)
    done = post(client, f"/customer-orders/{pending.id}/approve", {"version": str(pending.version)})
    assert done.status_code == 303
    assert "cp 100" not in client.get("/customer-orders").text  # one order keeps one spelling (DEF-015)
    assert "<h1>CP-100" in client.get(f"/customer-orders/{pending.id}").text
    assert [(o.revision, o.state) for o in sorted(rows(env, c.CustomerPO), key=lambda o: o.revision)] == [
        (0, "superseded"),
        (1, "active"),
    ]
    assert "waiting" not in client.get("/customer-orders").text.split("<tbody>")[1].split("</tbody>")[0]
    assert "customer_po.approve" in audited(env)


def test_a_revision_can_be_rejected_with_a_reason_and_a_stale_page_is_refused(client: TestClient, env) -> None:  # noqa: ANN001, F811
    cid = made(env)
    file_order(client, **order_form(cid))
    file_order(client, **order_form(cid, "CP-100", price="110"))
    pending = next(o for o in rows(env, c.CustomerPO) if o.revision == 1)
    needs = post(client, f"/customer-orders/{pending.id}/reject", {"version": str(pending.version), "reason": " "})
    assert needs.status_code == 400 and "Reason is needed" in needs.text
    stale = post(client, f"/customer-orders/{pending.id}/approve", {"version": str(pending.version + 3)})
    assert stale.status_code == 400 and "changed by someone else" in stale.text
    done = post(
        client, f"/customer-orders/{pending.id}/reject", {"version": str(pending.version), "reason": "Wrong price"}
    )
    assert done.status_code == 303
    assert {(o.revision, o.state) for o in rows(env, c.CustomerPO)} == {(0, "active"), (1, "rejected")}
    again = post(client, f"/customer-orders/{pending.id}/approve", {"version": str(pending.version)})
    assert again.status_code == 400 and "not waiting for approval" in again.text


def test_an_order_that_does_not_exist_is_a_readable_page(client: TestClient) -> None:
    assert "was not found" in client.get("/customer-orders/99999").text
    assert client.get("/customer-orders/99999").status_code == 404


# ---- from a file


def test_the_template_downloads_and_is_a_file_the_import_reads(client: TestClient, env) -> None:  # noqa: ANN001, F811
    made(env, "Example Customer")
    template = client.get("/customer-orders/import/template")
    assert template.status_code == 200 and "attachment" in template.headers["content-disposition"]
    preview = upload(client, template.content)
    assert preview.status_code == 200 and "will be filed" in preview.text and "CP-1001" in preview.text


def test_a_file_is_previewed_first_then_filed_when_confirmed_and_nothing_is_filed_before(
    client: TestClient, env
) -> None:  # noqa: ANN001, F811
    made(env)
    preview = upload(client, csv(ROW1, ROW2))
    assert preview.status_code == 200 and "What the file would do" in preview.text and "1 to file" in preview.text
    assert not rows(env, c.CustomerPO)  # nothing yet
    token = token_in(preview.text.replace('name="token"', 'name="csrf_token"'))  # the held-file token
    token = preview.text.split('name="token" value="')[1].split('"')[0]
    done = post(client, "/customer-orders/import/confirm", {"token": token})
    assert done.status_code == 303 and done.headers["location"].startswith("/customer-orders?imported=1")
    assert len(rows(env, c.CustomerPO)) == 1 and len(rows(env, c.CustomerPOLine)) == 2
    assert "Filed 1 customer order" in client.get(done.headers["location"]).text
    reused = post(client, "/customer-orders/import/confirm", {"token": token})  # a used preview is forgotten
    assert (
        reused.status_code == 400 and "expired or was already used" in reused.text and len(rows(env, c.CustomerPO)) == 1
    )


def test_a_wrong_or_missing_preview_token_files_nothing(client: TestClient, env) -> None:  # noqa: ANN001, F811
    made(env)
    upload(client, csv(ROW1, ROW2))
    for token in ("", "wrong"):
        refused = post(client, "/customer-orders/import/confirm", {"token": token})
        assert refused.status_code == 400 and "expired or was already used" in refused.text
    assert not rows(env, c.CustomerPO)


def test_the_same_file_again_is_skipped_and_a_changed_one_becomes_a_revision(client: TestClient, env) -> None:  # noqa: ANN001, F811
    made(env)
    for raw in (csv(ROW1, ROW2), csv(ROW1, ROW2), csv(ROW1.replace("2.50", "2.75").replace("25.00", "27.50"), ROW2)):
        preview = upload(client, raw)
        post(
            client,
            "/customer-orders/import/confirm",
            {"token": preview.text.split('name="token" value="')[1].split('"')[0]},
        )
    assert [(o.revision, o.state) for o in sorted(rows(env, c.CustomerPO), key=lambda o: o.revision)] == [
        (0, "active"),
        (1, "pending"),
    ]


def test_an_unknown_customer_waits_and_a_person_files_it_by_choosing_one(client: TestClient, env) -> None:  # noqa: ANN001, F811
    cid = made(env, "Initech")
    preview = upload(client, csv(ROW1, ROW2))
    assert "not found: Globex Racing" in preview.text and "waits until a customer is chosen" in preview.text
    done = post(
        client,
        "/customer-orders/import/confirm",
        {"token": preview.text.split('name="token" value="')[1].split('"')[0]},
    )
    assert "waiting=1" in done.headers["location"] and not rows(env, c.CustomerPO)
    assert "Waiting for a customer" in client.get("/customer-orders").text
    page = client.get("/customer-orders/waiting").text
    assert "PO CP-1001" in page and "No customer found for Globex Racing" in page
    waiting = rows(env, c.CustomerPOIntake)[0]
    nobody = post(client, f"/customer-orders/waiting/{waiting.id}/resolve", {})
    assert nobody.status_code == 400 and "Choose a customer" in nobody.text
    filed = post(client, f"/customer-orders/waiting/{waiting.id}/resolve", {f"customer_{waiting.id}": str(cid)})
    assert filed.status_code == 303 and filed.headers["location"].startswith("/customer-orders/")
    assert (
        rows(env, c.CustomerPO)[0].customer_id == cid
        and "Nothing is waiting" in client.get("/customer-orders/waiting").text
    )


def test_a_waiting_order_can_make_its_customer_or_be_rejected_with_a_reason(client: TestClient, env) -> None:  # noqa: ANN001, F811
    with Session(env[1]) as db:
        a = intake.file_po(
            db,
            PoData(
                po_number="A-1",
                currency="USD",
                customer_name="Brand New",
                lines=(LineData(item="X", qty=Decimal(1), unit_price=Decimal(5)),),
            ),
        ).intake
        b = intake.file_po(
            db,
            PoData(
                po_number="B-1",
                currency="USD",
                customer_name="Spam Co",
                lines=(LineData(item="Y", qty=Decimal(1), unit_price=Decimal(5)),),
            ),
        ).intake
        db.commit()
        first, second = a.id, b.id  # type: ignore[union-attr]
    made_new = post(client, f"/customer-orders/waiting/{first}/resolve", {"new_customer": "Brand New"})
    assert made_new.status_code == 303 and service.find_by_name(Session(env[1]), "Brand New") is not None
    needs = post(client, f"/customer-orders/waiting/{second}/reject", {"reason": ""})
    assert needs.status_code == 400 and "Reason is needed" in needs.text
    assert post(client, f"/customer-orders/waiting/{second}/reject", {"reason": "Not ours"}).status_code == 303
    assert {w.state for w in rows(env, c.CustomerPOIntake)} == {"done", "rejected"}
    assert post(client, "/customer-orders/waiting/9999/reject", {"reason": "x"}).status_code == 404


def test_a_bad_file_files_nothing_and_every_problem_is_listed_by_row(client: TestClient, env) -> None:  # noqa: ANN001, F811
    made(env)
    bad = upload(
        client, csv(ROW1, ROW2, "Globex Racing,CP-2,USD,1,Gear,0,10,,,,,", "Globex Racing,CP-3,usd1,1,Gear,1,10,,,,,")
    )
    assert (
        bad.status_code == 400 and "Nothing was filed" in bad.text and "Row 4: " in bad.text and "Row 5: " in bad.text
    )
    assert not rows(env, c.CustomerPO) and not rows(env, c.CustomerPOIntake)
    wrong = upload(client, b"customer,po_number\nA,B\n")
    assert wrong.status_code == 400 and "Missing column" in wrong.text
    junk = upload(client, b"\x00\x01\x02 not a sheet")
    assert junk.status_code == 400 and "Nothing was filed" not in junk.text


def test_a_file_over_the_size_limit_or_with_no_file_is_refused(client: TestClient, env) -> None:  # noqa: ANN001, F811
    made(env)
    big = upload(client, b"x" * (2 * 1024 * 1024 + 70 * 1024))
    assert big.status_code == 413 and "larger than" in big.text
    token = token_in(client.get("/customer-orders/import").text)
    none = client.post("/customer-orders/import", data={}, headers={**ORIGIN, "X-CSRF-Token": token})
    assert none.status_code in (400, 422)


def test_an_upload_without_the_header_token_is_refused(client: TestClient, env) -> None:  # noqa: ANN001, F811
    made(env)
    response = client.post("/customer-orders/import", files={"file": ("a.csv", csv(ROW1), "text/csv")}, headers=ORIGIN)
    assert response.status_code == 403 and not rows(env, c.CustomerPO)


# ---- the list


def test_the_list_filters_by_search_customer_and_state_and_sorts(client: TestClient, env) -> None:  # noqa: ANN001, F811
    globex, initech = made(env), made(env, "Initech")
    file_order(client, **order_form(globex, "CP-1"))
    file_order(client, **order_form(initech, "ZZ-2", price="50"))
    file_order(client, **order_form(globex, "CP-1", price="60"))  # a revision waiting on the first
    everything = client.get("/customer-orders").text
    assert everything.count("CP-1") >= 1 and "ZZ-2" in everything
    assert (
        "ZZ-2" not in client.get("/customer-orders?q=cp").text
        and "CP-1" in client.get("/customer-orders?q=globex").text
    )
    only = client.get(f"/customer-orders?customer={initech}").text
    assert "ZZ-2" in only and "CP-1" not in only
    waiting = client.get("/customer-orders?state=revision").text
    assert "CP-1" in waiting and "ZZ-2" not in waiting
    assert "ZZ-2" in client.get("/customer-orders?state=open&sort=po").text
    assert "No customer order matches" in client.get("/customer-orders?q=nothing").text
    assert client.get("/customer-orders?customer=abc&page=zzz&sort=%00").status_code == 200  # odd input is harmless
