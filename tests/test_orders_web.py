"""The vendor and purchase-order pages through the real app (PD-1.2)."""

import re
from datetime import UTC, datetime
from decimal import Decimal as D

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from po_dashboard.db import ledger as L
from po_dashboard.db.models import AuditLog
from po_dashboard.ledger import orders as rules
from test_gate import ORIGIN, PW, env, new_client, post, sign_in, token_in  # noqa: F401  (env is a fixture)

HEADER = ",".join(rules.COLUMNS)


@pytest.fixture
def client(env) -> TestClient:  # noqa: ANN001
    """Signed in as a buyer, not an admin: these pages are for everyone (DEC-29)."""
    client = new_client(env[0])
    assert sign_in(client, "buyer@example.com").status_code == 303
    return client


def make_vendor(client: TestClient, name: str = "Acme Fasteners", domains: str = "acme.com") -> int:
    response = post(client, "/vendors/create", {"name": name, "domains": domains})
    assert response.status_code == 303, response.text
    return int(response.headers["location"].rsplit("/", 1)[1])


LINE_COLUMNS = ("item", "description", "quantity", "unit", "unit_price", "requested_date")
TWO_LINES = [("Bolt", "", "10", "EA", "2.50", "2026-11-30"), ("Nut", "", "2.5", "EA", "0.10", ""), ("",) * 6]


def columns_of(rows: list[tuple[str, ...]]) -> dict[str, str | list[str]]:
    """The form's per-line fields, one list per column, from rows of (item, description, qty, unit, price, date)."""
    return {f"line_{name}": [row[i] for row in rows] for i, name in enumerate(LINE_COLUMNS)}


def order_form(vendor: int | str, rows: list[tuple[str, ...]] | None = None, **over: str | list[str]):  # noqa: ANN201
    data: dict[str, str | list[str]] = {
        "po_number": "PO-1", "vendor_id": str(vendor), "currency": "USD", "date_sent": "2026-11-04",
        "buyer": "buyer@example.com", **columns_of(rows or TWO_LINES),
    }  # fmt: skip
    data.update(over)
    return data


def place(client: TestClient, data: dict[str, str | list[str]]):  # noqa: ANN201
    page = client.get("/orders/new")
    return client.post("/orders/create", data={"csrf_token": token_in(page.text), **data}, headers=ORIGIN)


FROM_PAGE = object()  # "take the token from the import page, as the browser script does"


def upload(client: TestClient, raw: bytes, token: object = FROM_PAGE, name: str = "pos.csv"):  # noqa: ANN201
    headers = dict(ORIGIN)
    if token is FROM_PAGE:
        token = token_in(client.get("/orders/import").text)
    if token is not None:
        headers["X-CSRF-Token"] = str(token)
    return client.post("/orders/import", files={"file": (name, raw, "text/csv")}, headers=headers)


def csv_of(*rows: str, header: str = HEADER) -> bytes:
    return ("\n".join([header, *rows]) + "\n").encode()


def csv_row(po: str = "PO-1", vendor: str = "Acme Fasteners", line: str = "1", qty: str = "10") -> str:
    return f"{po},{vendor},USD,{line},Bolt,{qty},EA,2.50,2026-11-04,,,"


# ---- who may use them


@pytest.mark.parametrize("path", ["/vendors", "/orders", "/orders/new", "/orders/import", "/orders/import/template"])
def test_the_pages_need_a_sign_in(env, path: str) -> None:  # noqa: ANN001
    assert new_client(env[0]).get(path, headers={"Accept": "text/html"}).status_code == 303


def test_a_buyer_not_only_an_admin_can_use_them(client: TestClient) -> None:
    for path in ("/vendors", "/orders", "/orders/new", "/orders/import"):
        assert client.get(path).status_code == 200, path


def test_the_top_bar_links_to_them_and_marks_the_current_one(client: TestClient) -> None:
    page = client.get("/orders").text
    assert 'href="/vendors"' in page and re.search(r'href="/orders" aria-current="page"', page)


def test_each_new_form_page_has_the_cursor_ready_in_one_box(client: TestClient) -> None:
    vendor_id = make_vendor(client)
    for path in ("/vendors", f"/vendors/{vendor_id}", "/orders", "/orders/new", "/orders/import"):
        assert client.get(path).text.count(" autofocus") == 1, path


# ---- vendors


def test_a_vendor_is_added_and_listed_with_its_domains(client: TestClient) -> None:
    vendor_id = make_vendor(client, domains="acme.com, mail.acme.com")
    listing = client.get("/vendors").text
    assert "Acme Fasteners" in listing and "acme.com, mail.acme.com" in listing
    assert client.get(f"/vendors/{vendor_id}").status_code == 200


def test_a_refused_vendor_keeps_what_was_typed_and_says_why(client: TestClient) -> None:
    make_vendor(client)
    response = post(
        client, "/vendors/create", {"name": "ACME FASTENERS", "domains": "other.com", "contact_name": "Pat"}
    )
    assert response.status_code == 400 and "already exists" in response.text
    assert 'value="Pat"' in response.text and 'value="other.com"' in response.text


def test_domains_are_added_and_removed_from_the_vendor_page(client: TestClient, env) -> None:  # noqa: ANN001
    vendor_id = make_vendor(client)
    assert post(client, f"/vendors/{vendor_id}/domains/add", {"domains": "acme.co.uk"}).status_code == 303
    page = client.get(f"/vendors/{vendor_id}").text
    assert "acme.co.uk" in page
    domain_id = re.search(r"/domains/(\d+)/remove", page).group(1)  # type: ignore[union-attr]
    assert post(client, f"/vendors/{vendor_id}/domains/{domain_id}/remove").status_code == 303
    bad = post(client, f"/vendors/{vendor_id}/domains/add", {"domains": "not a domain"})
    assert bad.status_code == 400 and "not an email domain" in bad.text


def test_adding_a_domain_already_held_in_any_case_says_so(client: TestClient) -> None:
    vendor_id = make_vendor(client)  # holds acme.com
    again = post(client, f"/vendors/{vendor_id}/domains/add", {"domains": "ACME.com"})
    assert again.status_code == 400 and "acme.com is already added to this vendor" in again.text


def test_a_save_shows_a_fixed_saved_note_that_cannot_be_forged(client: TestClient) -> None:
    vendor_id = make_vendor(client)
    response = post(client, f"/vendors/{vendor_id}/update", {"name": "Acme Fasteners"})
    assert response.headers["location"].endswith("?saved=1")
    assert "Saved" in client.get(f"/vendors/{vendor_id}?saved=1").text
    assert 'role="status"' not in client.get(f"/vendors/{vendor_id}").text
    forged = client.get(f"/vendors/{vendor_id}?saved=%3Cb%3Ehi")
    assert forged.status_code == 200 and 'role="status"' not in forged.text


def test_a_vendor_can_be_edited_and_the_change_is_audited_with_who_and_where(client: TestClient, env) -> None:  # noqa: ANN001
    vendor_id = make_vendor(client)
    response = post(
        client, f"/vendors/{vendor_id}/update", {"name": "Acme Fasteners", "contact_name": "Pat", "notes": "Net 30"}
    )
    assert response.status_code == 303 and "Net 30" in client.get(f"/vendors/{vendor_id}").text
    with Session(env[1]) as db:
        entry = db.scalars(select(AuditLog).where(AuditLog.action == "vendor.update")).one()
        assert entry.user_id is not None and entry.client_address


def test_a_missing_vendor_is_a_404_page_not_a_crash(client: TestClient) -> None:
    assert client.get("/vendors/999").status_code == 404
    assert post(client, "/vendors/999/update", {"name": "x"}).status_code == 404
    assert post(client, "/vendors/999/domains/add", {"domains": "x.com"}).status_code == 404


# ---- a PO by hand


def test_a_po_is_entered_and_its_page_shows_lines_in_exact_numbers(client: TestClient) -> None:
    vendor_id = make_vendor(client)
    response = place(client, order_form(vendor_id))
    assert response.status_code == 303
    page = client.get(response.headers["location"]).text
    assert "PO PO-1" in page and "Acme Fasteners" in page and "2026-11-04" in page
    assert "10 EA" in page and "2.5 EA" in page and "2.50 USD" in page and "0.10 USD" in page
    assert page.count("Sent") >= 2  # both lines have had nothing happen yet


def test_the_blank_rows_are_skipped_and_the_line_numbers_follow_the_form(client: TestClient, env) -> None:  # noqa: ANN001
    vendor_id = make_vendor(client)
    rows = [("",) * 6, ("Bolt", "", "1", "EA", "1", ""), ("Nut", "", "1", "EA", "1", "")]
    place(client, order_form(vendor_id, rows))
    with Session(env[1]) as db:
        assert list(db.scalars(select(L.POLine.line_no).order_by(L.POLine.line_no))) == [2, 3]


def test_reloading_after_creating_a_po_does_not_create_another(client: TestClient, env) -> None:  # noqa: ANN001
    vendor_id = make_vendor(client)
    location = place(client, order_form(vendor_id)).headers["location"]
    for _ in range(3):
        assert client.get(location).status_code == 200
    with Session(env[1]) as db:
        assert db.scalar(select(func.count()).select_from(L.PurchaseOrder)) == 1


def test_a_refused_po_keeps_every_box_and_lists_every_problem(client: TestClient) -> None:
    vendor_id = make_vendor(client)
    rows = [("Bolt", "", "0", "EA", "1", ""), ("Nut", "", "1.00001", "EA", "1", ""), ("",) * 6]
    response = place(client, order_form(vendor_id, rows, po_number="PO-7", currency="us"))
    assert response.status_code == 400
    text = response.text
    assert "Line 1: Quantity must be more than zero" in text
    assert "Line 2: Quantity" in text and "nothing is rounded" in text
    assert "three-letter" in text
    assert 'value="PO-7"' in text and 'value="Bolt"' in text and 'value="Nut"' in text
    assert f'<option value="{vendor_id}" selected>' in text


def test_a_po_without_a_vendor_a_number_or_a_line_is_refused(client: TestClient) -> None:
    vendor_id = make_vendor(client)
    assert "Choose a vendor" in place(client, order_form(vendor_id, vendor_id="")).text
    assert "Choose a vendor" in place(client, order_form(vendor_id, vendor_id="abc")).text
    assert "Choose a vendor" in place(client, order_form(vendor_id, vendor_id="99999")).text
    assert "PO number is needed" in place(client, order_form(vendor_id, po_number=" ")).text
    empty = place(client, order_form(vendor_id, [("",) * 6] * 3))
    assert empty.status_code == 400 and "at least one line" in empty.text


def test_an_existing_po_number_is_refused_in_any_case(client: TestClient) -> None:
    vendor_id = make_vendor(client)
    assert place(client, order_form(vendor_id)).status_code == 303
    again = place(client, order_form(vendor_id, po_number="po-1"))
    assert again.status_code == 400 and "already exists" in again.text


def test_a_tampered_form_with_uneven_columns_is_refused_not_crashed(client: TestClient) -> None:
    vendor_id = make_vendor(client)
    response = place(client, order_form(vendor_id, line_item=["Bolt", "Nut"], line_quantity=["1"]))
    assert response.status_code == 400 and "incomplete" in response.text


def test_too_many_line_rows_are_refused(client: TestClient) -> None:
    vendor_id = make_vendor(client)
    response = place(client, order_form(vendor_id, [("",) * 6] * 101))
    assert response.status_code == 400 and "incomplete" in response.text


def test_a_po_created_by_hand_records_who_made_it(client: TestClient, env) -> None:  # noqa: ANN001
    vendor_id = make_vendor(client)
    place(client, order_form(vendor_id))
    with Session(env[1]) as db:
        order = db.scalars(select(L.PurchaseOrder)).one()
        assert order.source == "manual" and order.created_by is not None
        entry = db.scalars(select(AuditLog).where(AuditLog.action == "po.create")).one()
        assert entry.user_id == order.created_by


# ---- the list and the detail page


def test_the_list_shows_open_and_closed_pos_and_searches_literally(client: TestClient, env) -> None:  # noqa: ANN001
    vendor_id = make_vendor(client)
    place(client, order_form(vendor_id, po_number="PO-100"))
    place(client, order_form(vendor_id, po_number="PO_200"))
    listing = client.get("/orders").text
    assert "PO-100" in listing and "PO_200" in listing and "Open" in listing
    assert "PO-100" not in client.get("/orders", params={"q": "200"}).text
    underscore = client.get("/orders", params={"q": "O_2"}).text  # an underscore is not a wildcard
    assert "PO_200" in underscore and "PO-100" not in underscore
    assert "No purchase orders match" in client.get("/orders", params={"q": "%"}).text
    assert "Acme" in client.get("/orders", params={"q": "acme"}).text  # vendor names are searched too


def test_the_detail_page_tells_the_purchaser_when_the_vendor_confirmed_a_different_quantity(
    client: TestClient, env
) -> None:  # noqa: ANN001
    vendor_id = make_vendor(client)
    location = place(client, order_form(vendor_id)).headers["location"]
    with Session(env[1]) as db:
        order = db.scalars(select(L.PurchaseOrder)).one()
        first = db.scalars(select(L.POLine).where(L.POLine.po_id == order.id).order_by(L.POLine.line_no)).first()
        ack = L.Acknowledgement(po_id=order.id, accepted_at=datetime.now(UTC))
        db.add(ack)
        db.flush()
        db.add(L.AckLine(ack_id=ack.id, po_line_id=first.id, confirmed_qty=D("6")))  # type: ignore[union-attr]
        db.commit()
    page = client.get(location).text
    assert "Update the original purchase order" in page and "confirmed 6, not 10" in page
    assert "Acknowledged" in page


def test_missing_pages_are_404s(client: TestClient) -> None:
    assert client.get("/orders/999").status_code == 404
    assert "not found" in client.get("/orders/999").text
    assert client.get("/orders/abc").status_code == 422


def test_the_imported_count_in_the_address_cannot_inject_anything(client: TestClient) -> None:
    assert "Imported 3 purchase orders" in client.get("/orders", params={"imported": "3"}).text
    for bad in ("-1", "x", "<script>alert(1)</script>", "999999999"):
        assert client.get("/orders", params={"imported": bad}).status_code == 422


# ---- text typed by a person is shown as text


EVIL = '<script>alert("x")</script><img src=x onerror=alert(1)>'


def test_what_people_type_is_escaped_everywhere_it_is_shown(client: TestClient) -> None:
    vendor_id = make_vendor(client, name=EVIL, domains="evil.com")
    place(client, order_form(vendor_id, [(EVIL, EVIL, "1", "EA", "1", "")], po_number=EVIL, buyer=EVIL))
    po_id = re.search(r"/orders/(\d+)", client.get("/orders").text).group(1)  # type: ignore[union-attr]
    for path in ("/vendors", f"/vendors/{vendor_id}", "/orders", f"/orders/{po_id}", "/orders/new"):
        html = client.get(path).text
        assert "<script>alert" not in html and "<img src=x" not in html, path
    assert "&lt;script&gt;" in client.get(f"/orders/{po_id}").text


# ---- CSV upload (the file goes up with the token in a header, sent by static/app.js)


def test_a_csv_file_is_uploaded_and_the_person_lands_on_the_list(client: TestClient, env) -> None:  # noqa: ANN001
    make_vendor(client)
    response = upload(client, csv_of(csv_row("PO-1", line="1"), csv_row("PO-1", line="2"), csv_row("PO-2")))
    assert response.status_code == 303 and response.headers["location"] == "/orders?imported=2"
    with Session(env[1]) as db:
        assert db.scalar(select(func.count()).select_from(L.PurchaseOrder)) == 2
        assert db.scalar(select(func.count()).select_from(L.POLine)) == 3
        assert db.scalars(select(L.PurchaseOrder.source)).first() == "csv"


def test_a_bad_file_imports_nothing_and_the_page_lists_the_rows(client: TestClient, env) -> None:  # noqa: ANN001
    make_vendor(client)
    response = upload(client, csv_of(csv_row("PO-1"), csv_row("PO-2", qty="0"), csv_row("PO-3", vendor="Nobody")))
    assert response.status_code == 400
    assert 'id="import-result"' in response.text
    assert "Nothing was imported" in response.text
    assert "Row 3: " in response.text and "Row 4: " in response.text and "not in the vendor list" in response.text
    with Session(env[1]) as db:
        assert db.scalar(select(func.count()).select_from(L.PurchaseOrder)) == 0


def test_an_upload_without_the_token_header_is_refused(client: TestClient, env) -> None:  # noqa: ANN001
    make_vendor(client)
    for token in (None, "wrong-token", ""):
        assert upload(client, csv_of(csv_row()), token=token).status_code == 403
    with Session(env[1]) as db:
        assert db.scalar(select(func.count()).select_from(L.PurchaseOrder)) == 0


def test_an_upload_from_another_site_is_refused(client: TestClient) -> None:
    token = token_in(client.get("/orders/import").text)
    response = client.post("/orders/import", files={"file": ("a.csv", csv_of(csv_row()), "text/csv")},
                           headers={"Origin": "https://evil.example", "X-CSRF-Token": token})  # fmt: skip
    assert response.status_code == 403


def test_an_upload_with_no_file_or_the_wrong_kind_of_file(client: TestClient) -> None:
    token = token_in(client.get("/orders/import").text)
    headers = {**ORIGIN, "X-CSRF-Token": token}
    nothing = client.post("/orders/import", data={"note": "hello"}, files={"x": ("x", b"")}, headers=headers)
    assert nothing.status_code == 400 and "Choose a CSV file" in nothing.text
    binary = upload(client, bytes(range(256)) * 20, name="photo.jpg")
    assert binary.status_code == 400 and "not UTF-8" in binary.text


def test_an_oversized_upload_is_refused_before_it_is_read(client: TestClient, env) -> None:  # noqa: ANN001
    make_vendor(client)
    big = csv_of(csv_row()) + b"x" * (rules.MAX_CSV_BYTES + 100_000)
    response = upload(client, big)
    assert response.status_code == 413 and "larger than 2 MB" in response.text


def test_an_upload_that_does_not_state_its_size_is_refused(client: TestClient) -> None:
    token = token_in(client.get("/orders/import").text)

    def chunks():  # noqa: ANN202
        yield b'--b\r\nContent-Disposition: form-data; name="file"; filename="a.csv"\r\n\r\n'
        yield b"x" * 100
        yield b"\r\n--b--\r\n"

    headers = {**ORIGIN, "X-CSRF-Token": token, "Content-Type": "multipart/form-data; boundary=b"}
    response = client.post("/orders/import", content=chunks(), headers=headers)
    assert response.status_code == 411


def test_the_template_downloads_as_csv_and_names_every_column(client: TestClient) -> None:
    response = client.get("/orders/import/template")
    assert response.status_code == 200 and response.headers["content-type"].startswith("text/csv")
    assert "attachment" in response.headers["content-disposition"]
    assert response.text.splitlines()[0] == HEADER


def test_the_import_page_script_is_a_local_file_the_policy_allows(client: TestClient) -> None:
    page = client.get("/orders/import")
    assert '<script src="/static/app.js" defer></script>' in page.text
    assert "script-src" not in page.headers["content-security-policy"]  # default-src 'self' covers it
    script = client.get("/static/app.js")
    assert script.status_code == 200 and "X-CSRF-Token" in script.text
