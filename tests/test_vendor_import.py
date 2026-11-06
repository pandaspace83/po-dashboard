"""Uploading vendors in one fixed format: create the new, add to the existing, never refuse a known vendor (DEC-44)."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from po_dashboard.db import ledger as L
from po_dashboard.db.models import AuditLog
from po_dashboard.ledger import vendor_import as vi
from test_gate import ORIGIN, env, new_client, token_in  # noqa: F401
from test_ledger import db, engine  # noqa: F401
from test_orders_web import client, make_vendor  # noqa: F401

HEAD = "vendor_name,contact_name,contact_email,email_domains,notes\n"


def domains_of(db: Session, name: str) -> list[str]:
    return sorted(db.scalars(select(L.VendorDomain.domain).join(L.Vendor).where(L.Vendor.name == name)))


def vendor(db: Session, name: str) -> L.Vendor:
    return db.scalars(select(L.Vendor).where(L.Vendor.name == name)).one()


# ---- creating


def test_a_new_vendor_is_created_with_its_domains_and_the_domain_of_its_contact_email(db: Session) -> None:
    result = vi.import_vendors(db, (HEAD + "Acme Fasteners,Pat Lee,pat@acme.com,mail.acme.com,Net 30\n").encode())
    assert result.created == ["Acme Fasteners"] and not result.skipped
    assert domains_of(db, "Acme Fasteners") == ["acme.com", "mail.acme.com"]
    assert (
        vendor(db, "Acme Fasteners").contact_email == "pat@acme.com" and vendor(db, "Acme Fasteners").notes == "Net 30"
    )


def test_a_shared_mail_service_is_not_made_a_vendor_domain(db: Session) -> None:
    vi.import_vendors(db, (HEAD + "Small Shop,Al,al@gmail.com,,\n").encode())
    assert domains_of(db, "Small Shop") == [] and vendor(db, "Small Shop").contact_email == "al@gmail.com"


def test_the_vendor_name_and_an_email_are_required(db: Session) -> None:
    result = vi.import_vendors(db, b"vendor_name,contact_email\nJust A Name,a@just.example\nNo Email,\n")
    assert result.created == ["Just A Name"] and [e.row for e in result.skipped] == [3]
    assert "email is required" in result.skipped[0].message
    assert db.scalars(select(L.Vendor).where(L.Vendor.name == "No Email")).first() is None


def test_an_email_already_the_contact_of_another_vendor_is_refused(db: Session) -> None:
    vi.import_vendors(db, (HEAD + "Acme,Pat,pat@shared.example,,\n").encode())
    result = vi.import_vendors(db, (HEAD + "Beta,Al,PAT@shared.example,,\nGamma,Bo,bo@gamma.example,,\n").encode())
    assert result.created == ["Gamma"] and [e.row for e in result.skipped] == [2]
    assert "already the contact email of Acme" in result.skipped[0].message
    # the vendor's own email again is not a clash
    assert vi.import_vendors(db, (HEAD + "Acme,Pat,pat@shared.example,,\n").encode()).unchanged == ["Acme"]


# ---- an existing vendor is added to, never refused


def test_an_existing_vendor_with_a_new_email_gets_the_new_domain_added(db: Session) -> None:
    vi.import_vendors(db, (HEAD + "Acme Fasteners,Pat,pat@acme.com,,\n").encode())
    result = vi.import_vendors(db, (HEAD + "ACME FASTENERS,Sam,sam@acme.co.uk,,\n").encode())
    assert result.created == [] and not result.skipped
    assert result.updated and "acme.co.uk" in result.updated[0][1]
    assert domains_of(db, "Acme Fasteners") == ["acme.co.uk", "acme.com"]
    assert vendor(db, "Acme Fasteners").contact_email == "pat@acme.com"  # a person's entry is never overwritten
    assert any("kept the contact email pat@acme.com" in note for note in result.notes)
    assert db.scalar(select(func.count()).select_from(L.Vendor)) == 1


def test_blank_contact_details_are_filled_but_existing_ones_are_not_overwritten(db: Session) -> None:
    vi.import_vendors(db, b"vendor_name,contact_email,notes\nAcme,pat@acme.com,Original note\n")
    vi.import_vendors(db, (HEAD + "Acme,Pat,pat@acme.com,,Different note\n").encode())
    found = vendor(db, "Acme")
    assert (found.contact_name, found.contact_email, found.notes) == ("Pat", "pat@acme.com", "Original note")


def test_uploading_the_same_file_again_changes_nothing(db: Session) -> None:
    raw = (HEAD + "Acme,Pat,pat@acme.com,mail.acme.com,\nBeta Co,,b@beta.example,beta.example,\n").encode()
    assert len(vi.import_vendors(db, raw).created) == 2
    again = vi.import_vendors(db, raw)
    assert again.created == [] and again.updated == [] and sorted(again.unchanged) == ["Acme", "Beta Co"]


def test_the_same_vendor_twice_in_one_file_is_one_vendor(db: Session) -> None:
    result = vi.import_vendors(db, (HEAD + "Acme,,a@acme.com,,\nAcme,,b@acme.co.uk,,\n").encode())
    assert result.created == ["Acme"] and len(result.updated) == 1
    assert domains_of(db, "Acme") == ["acme.co.uk", "acme.com"]


# ---- a bad row is listed and the rest still go in


def test_a_domain_owned_by_another_vendor_skips_only_that_row(db: Session) -> None:
    vi.import_vendors(db, (HEAD + "Acme,,a@acme.com,acme.com,\n").encode())
    result = vi.import_vendors(
        db,
        (
            HEAD + "Beta Co,,b@beta.example,beta.example,\nGamma,,g@gamma.example,acme.com,\n"
            "Delta,,d@delta.example,delta.example,\n"
        ).encode(),
    )
    assert result.created == ["Beta Co", "Delta"]
    assert [e.row for e in result.skipped] == [3] and "already belongs to Acme" in result.skipped[0].message
    assert (
        domains_of(db, "Acme") == ["acme.com"]
        and db.scalars(select(L.Vendor).where(L.Vendor.name == "Gamma")).first() is None
    )


def test_a_row_with_no_name_a_bad_email_or_a_bad_domain_is_listed(db: Session) -> None:
    raw = (
        HEAD + ",Pat,p@x.example,,\nOne,,not-an-email,,\nTwo,,t@two.example,not a domain,\n"
        "Good,,g@good.example,good.example,\n"
    )
    result = vi.import_vendors(db, raw.encode())
    assert result.created == ["Good"] and [e.row for e in result.skipped] == [2, 3, 4]
    assert "needed" in result.skipped[0].message and "email address" in result.skipped[1].message


# ---- the file as a whole


def test_columns_are_found_by_name_in_any_order_and_extras_are_skipped(db: Session) -> None:
    raw = b"Phone,notes,VENDOR_NAME,email_domains,Contact_Email\n555,Net 30,Acme,acme.com,a@acme.com\n"
    result = vi.import_vendors(db, raw)
    assert result.created == ["Acme"] and result.ignored_columns == ["phone"]
    assert vendor(db, "Acme").notes == "Net 30"


def test_a_windows_1252_file_with_accents_reads_as_it_is(db: Session) -> None:
    raw = (HEAD + "Société Précis,,s@precis.example,precis.example,\n").encode("cp1252")
    assert vi.import_vendors(db, raw).created == ["Société Précis"]


@pytest.mark.parametrize(
    ("raw", "message"),
    [(b"", "is empty"), (HEAD.encode(), "no rows"),
     (b"name,notes\nAcme,x\n", "Missing column: vendor_name, contact_email"),
     (b"vendor_name,vendor_name,contact_email\nA,B,c\n", "appears twice"),
     (b"\x00\x01\x02", "does not look like a CSV"), (b"x" * (2 * 1024 * 1024 + 1), "larger than")],
)  # fmt: skip
def test_a_file_that_cannot_be_read_is_refused_and_writes_nothing(db: Session, raw: bytes, message: str) -> None:
    with pytest.raises(vi.VendorFileRejected, match=message):
        vi.import_vendors(db, raw)
    assert db.scalar(select(func.count()).select_from(L.Vendor)) == 0


def test_every_change_is_audited_with_a_summary(db: Session) -> None:
    vi.import_vendors(db, (HEAD + "Acme,Pat,pat@acme.com,,\n").encode())
    actions = set(db.scalars(select(AuditLog.action)))
    assert {"vendor.create", "vendor.import"} <= actions


# ---- the pages


def upload(client: TestClient, raw: bytes, token: object = ...):  # noqa: ANN201
    headers = dict(ORIGIN)
    if token is ...:
        token = token_in(client.get("/vendors/import").text)
    if token:
        headers["X-CSRF-Token"] = str(token)
    return client.post("/vendors/import", files={"file": ("vendors.csv", raw, "text/csv")}, headers=headers)


def test_the_page_opens_with_the_cursor_on_the_file_chooser_and_offers_a_template(client: TestClient) -> None:
    page = client.get("/vendors/import")
    assert page.status_code == 200 and page.text.count(" autofocus") == 1 and "multipart/form-data" in page.text
    template = client.get("/vendors/import/template")
    assert template.headers["content-type"].startswith("text/csv") and template.text.startswith("vendor_name,")
    assert "Import vendors" in client.get("/vendors").text


def test_an_upload_shows_what_was_created_updated_and_skipped(client: TestClient) -> None:
    make_vendor(client)  # Acme Fasteners, acme.com
    raw = (
        HEAD + "Acme Fasteners,,sam@acme.co.uk,,\nNew Co,,n@newco.example,newco.example,\n"
        "Bad,,b@bad.example,not a domain,\n"
    ).encode()
    response = upload(client, raw)
    assert response.status_code == 200
    text = response.text
    assert "1 created, 1 updated" in text and "1 row skipped" in text and "New Co" in text
    assert "Row 4:" in text and "added email domain acme.co.uk" in text
    assert "newco.example" in client.get("/vendors").text


def test_a_bad_file_is_a_clear_message_and_a_missing_token_is_refused(client: TestClient) -> None:
    assert "Missing column: vendor_name, contact_email" in upload(client, b"a,b\n1,2\n").text
    assert upload(client, b"vendor_name,contact_email\nX,x@x.example\n", token=None).status_code in (400, 403)


def test_the_import_needs_a_sign_in(env) -> None:  # noqa: ANN001
    anonymous = new_client(env[0])
    for method, path in (("GET", "/vendors/import"), ("GET", "/vendors/import/template"), ("POST", "/vendors/import")):
        assert anonymous.request(method, path, headers=ORIGIN, follow_redirects=False).status_code in (303, 401, 403)


def test_markup_in_a_vendor_name_is_shown_as_text(client: TestClient) -> None:
    response = upload(client, (HEAD + "<script>alert(1)</script>,,s@x.example,x.example,\n").encode())
    assert "<script>alert(1)</script>" not in response.text and "&lt;script&gt;" in response.text


# ---- the vendor search box used on the PO form and the filters


def test_vendor_search_finds_by_name_or_domain_and_treats_wildcards_as_text(client: TestClient) -> None:
    make_vendor(client, "Acme Fasteners", "acme.com")
    make_vendor(client, "Northwind Metals", "northwind.example")
    names = lambda q: [v["name"] for v in client.get("/vendors/search", params={"q": q}).json()]  # noqa: E731
    assert names("acm") == ["Acme Fasteners"] and names("NORTH") == ["Northwind Metals"]
    assert names("northwind.ex") == ["Northwind Metals"] and names("%") == [] and names("zzz") == []
    assert len(names("")) == 2


def test_search_needs_a_sign_in(env) -> None:  # noqa: ANN001
    assert new_client(env[0]).get("/vendors/search", follow_redirects=False).status_code in (303, 401, 403)


def test_the_po_form_and_filters_use_the_search_box_not_a_long_list(client: TestClient) -> None:
    vendor_id = make_vendor(client)
    for path in ("/orders/new", "/orders", "/reports/open-items"):
        page = client.get(path).text
        assert "data-picker" in page and "<select id=\"vendor" not in page, path
    shown = client.get(f"/orders?vendor={vendor_id}").text
    assert 'value="Acme Fasteners"' in shown and f'name="vendor" value="{vendor_id}"' in shown
