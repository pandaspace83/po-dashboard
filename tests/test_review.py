# ruff: noqa: F811 (fixtures are imported by name, as in the other test files)
"""The Review screens (PD-4.4, DEC-54): the lists, one message's documents with their text, fields and PO lines, Reject,
File and Attach, and the original served only as a download. A stub model gives each answer; no model is called."""

from __future__ import annotations

import json
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from po_dashboard.db.mail import Extraction, SourceDocument, SourceMessage
from po_dashboard.db.models import AuditLog
from po_dashboard.extraction import queue
from po_dashboard.intake.reading import read_pending
from po_dashboard.intake.store import FileStore
from test_extraction_queue import Stub, extractor, rows
from test_gate import ORIGIN, env  # noqa: F401
from test_intake import db, mail, put  # noqa: F401
from test_matching import answer, line, world  # noqa: F401
from test_orders_web import client  # noqa: F401


@pytest.fixture
def store(env) -> FileStore:  # noqa: ANN001
    """The app's own file store, so the pages read what the test stored."""
    return FileStore(env[2].data_dir / "originals")


def read(env, db: Session, store: FileStore, reply: dict[str, Any], body: str = "Our order SO-1 for PO 4569586.",  # noqa: ANN001
         subject: str = "Ack", files: tuple[tuple[str, str, bytes], ...] = ()) -> list[Extraction]:  # fmt: skip
    """Store one message, read it, queue it and let the stub model answer: the way a document reaches the screens."""
    put(db, store, mail(subject=subject, body=body, files=files))
    read_pending(db, store, limit=500)
    queue.queue_missing(db, "stub:1b")
    db.commit()
    reply = json.loads(json.dumps(reply, default=float))
    run = extractor(env, store, Stub(*[reply] * 5))
    while run.run_once().kind == "done":
        pass
    return rows(db)


def good(**changes: Any) -> dict[str, Any]:
    return answer(po_number="4569586", vendor_order_number="SO-1", lines=[line(1, "BLT-M8x40", 100, "14.90")]) | changes


def test_the_list_shows_a_waiting_document_and_home_counts_it(env, db, store, world, client: TestClient) -> None:  # noqa: ANN001
    read(env, db, store, good())
    page = client.get("/review")
    assert page.status_code == 200 and "Ack" in page.text and "v@vendor.test" in page.text
    assert "Waiting for review (1)" in page.text and "Unmatched (0)" in page.text and "4569586" in page.text
    home = client.get("/").text
    assert 'href="/review"' in home and "Waiting for review" in home and 'href="/review?show=unmatched"' in home
    assert client.get("/review?show=nonsense").status_code == 200  # an unknown place shows the first one


def test_the_message_page_shows_text_fields_and_the_po_line_beside_each_line(
    env, db, store, world, client: TestClient
) -> None:  # noqa: ANN001
    (row,) = read(env, db, store, good(), body="Confirming <b>PO 4569586</b> <script>alert(1)</script> BLT-M8x40 100")
    message = db.scalars(select(SourceMessage)).one()
    page = client.get(f"/review/message/{message.id}").text
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in page and "<script>alert(1)" not in page  # text is data, escaped
    assert 'name="f_po_number" value="4569586"' in page and 'name="line_0_item" value="BLT-M8x40"' in page
    assert 'name="line_0_unit_price" value="14.9"' in page and "ordered 100, open 100 EA" in page
    assert "PO 4569586" in page and "sender is not a known address" in page.lower().replace("  ", " ")
    assert 'name="tick_sender_not_vendor"' in page  # v@vendor.test is not a known domain of the vendor
    assert f'action="/review/{row.id}/accept"' in page and 'name="version"' in page
    assert "Reject" in page and "File as no order facts" in page
    assert "Download the original" in page and row.review_state == "waiting"


def test_differences_with_the_po_are_marked_on_the_line(env, db, store, world, client: TestClient) -> None:  # noqa: ANN001
    read(env, db, store, good(lines=[line(1, "BLT-M8x40", 100, "15.90")]), body="PO 4569586 BLT-M8x40 100 at $15.90")
    message = db.scalars(select(SourceMessage)).one()
    page = client.get(f"/review/message/{message.id}").text
    assert 'class="differs"' in page and "price differs" in page


def test_a_message_that_is_not_there_is_not_found(client: TestClient) -> None:
    assert client.get("/review/message/999").status_code == 404
    assert client.get("/review/original/999").status_code == 404


# ---- Reject and File


def test_reject_needs_a_reason_keeps_the_extraction_and_audits_it(env, db, store, world, client: TestClient) -> None:  # noqa: ANN001
    (row,) = read(env, db, store, good())
    path = f"/review/{row.id}/reject"
    assert (
        client.post(path, data={"version": str(row.version), "reason": " "}, headers=ORIGIN).status_code == 403
    )  # no token
    from test_gate import post

    refused = post(client, path, {"version": str(row.version), "reason": ""})
    assert refused.status_code == 400 and "Reason" in refused.text
    done = post(client, path, {"version": str(row.version), "reason": "Wrong vendor"})
    assert done.status_code == 303 and done.headers["location"] == "/review"
    (kept,) = rows(db)
    assert (kept.review_state, kept.review_note, kept.answer is not None) == ("rejected", "Wrong vendor", True)
    assert kept.reviewed_by is not None and kept.reviewed_at is not None
    assert (
        db.scalars(select(AuditLog).where(AuditLog.action == "extraction.reject")).one().object
        == f"extraction:{row.id}"
    )
    assert "Waiting for review (0)" in client.get("/review").text
    again = post(client, path, {"version": str(kept.version), "reason": "again"})
    assert again.status_code == 400 and "not waiting" in again.text


def test_a_stale_page_is_refused(env, db, store, world, client: TestClient) -> None:  # noqa: ANN001
    from test_gate import post

    (row,) = read(env, db, store, good())
    refused = post(client, f"/review/{row.id}/file", {"version": str(row.version + 5)})
    assert refused.status_code == 400 and "changed by someone else" in refused.text.lower()
    assert rows(db)[0].review_state == "waiting"


def test_file_moves_a_document_to_no_order_facts(env, db, store, world, client: TestClient) -> None:  # noqa: ANN001
    from test_gate import post

    (row,) = read(env, db, store, good())
    done = post(client, f"/review/{row.id}/file", {"version": str(row.version)})
    assert done.status_code == 303 and done.headers["location"] == "/review?show=no_facts"
    assert rows(db)[0].review_state == "no_facts" and rows(db)[0].po_id is None
    listed = client.get("/review?show=no_facts").text
    assert "Ack" in listed and "No order facts (1)" in listed
    assert (
        "File as no order facts" not in client.get(f"/review/message/{db.scalars(select(SourceMessage)).one().id}").text
    )


# ---- Unmatched and Attach


def unmatched_reply() -> dict[str, Any]:
    return answer(po_number="9999999", lines=[line(None, "BLT-M8x40", 100, "14.90")])


def test_an_unmatched_document_offers_the_senders_open_pos_and_attach_validates_again(
    env, db, store, world, client: TestClient
) -> None:  # noqa: ANN001
    from test_gate import post

    (row,) = read(env, db, store, unmatched_reply(), body="Re PO 9999999 BLT-M8x40 100", subject="Which PO?")
    assert row.review_state == "unmatched" and "Which PO?" in client.get("/review?show=unmatched").text
    message = db.scalars(select(SourceMessage)).one()
    page = client.get(f"/review/message/{message.id}").text
    assert 'name="po_number"' in page and "Attach to a PO" in page
    refused = post(client, f"/review/{row.id}/attach", {"version": str(row.version), "po_number": "1234567"})
    assert refused.status_code == 400 and "No PO has that number" in refused.text
    nothing = post(client, f"/review/{row.id}/attach", {"version": str(row.version)})
    assert nothing.status_code == 400 and "Choose a PO" in nothing.text
    done = post(client, f"/review/{row.id}/attach", {"version": str(row.version), "po_number": "po 4569586"})
    assert done.status_code == 303 and done.headers["location"] == f"/review/message/{message.id}"
    (after,) = rows(db)
    assert (after.review_state, after.po_id) == ("waiting", world["first"].id)
    flags = {f["flag"] for f in json.loads(after.flags or "[]")}
    assert "value_not_in_source" in flags  # 9999999 is not a PO number in the text, and the person sees it
    assert db.scalars(select(AuditLog).where(AuditLog.action == "extraction.attach")).one() is not None


def test_attach_by_choice_from_the_list(env, db, store, world, client: TestClient) -> None:  # noqa: ANN001
    from test_gate import post

    (row,) = read(env, db, store, unmatched_reply())
    done = post(client, f"/review/{row.id}/attach", {"version": str(row.version), "po_id": str(world["second"].id)})
    assert done.status_code == 303 and rows(db)[0].po_id == world["second"].id


# ---- the original, as a download


def test_a_body_downloads_as_an_eml_and_a_pdf_may_open_under_a_sandbox(
    env, db, store, world, client: TestClient
) -> None:  # noqa: ANN001
    pdf = b"%PDF-1.4 made up"
    read(env, db, store, good(), files=(('a"b;c.pdf', "application/pdf", pdf),))
    docs = {d.kind: d for d in db.scalars(select(SourceDocument))}
    body = client.get(f"/review/original/{docs['body'].id}")
    assert body.status_code == 200 and body.headers["content-type"].startswith("message/rfc822")
    assert (
        body.headers["content-disposition"].startswith("attachment")
        and body.headers["x-content-type-options"] == "nosniff"
    )
    assert (
        client.get(f"/review/original/{docs['body'].id}?view=1").headers["content-disposition"].startswith("attachment")
    )
    download = client.get(f"/review/original/{docs['pdf'].id}")
    assert download.content == pdf and download.headers["content-disposition"].startswith("attachment")
    assert download.headers["content-disposition"] == 'attachment; filename="abc.pdf"'  # nothing that could break out
    viewed = client.get(f"/review/original/{docs['pdf'].id}?view=1")
    assert (
        viewed.headers["content-disposition"].startswith("inline")
        and viewed.headers["content-security-policy"] == "sandbox"
    )
    assert viewed.headers["content-type"] == "application/pdf" and viewed.headers["cache-control"] == "no-store"


def test_a_file_that_is_not_a_pdf_or_picture_is_never_opened_in_the_browser(
    env, db, store, world, client: TestClient
) -> None:  # noqa: ANN001
    html = b"<html><script>alert(1)</script></html>"
    read(env, db, store, good(), files=(("page.html", "text/html", html),))
    doc = next(d for d in db.scalars(select(SourceDocument)) if d.kind not in ("body", "pdf"))
    shown = client.get(f"/review/original/{doc.id}?view=1")
    assert shown.headers["content-type"] == "application/octet-stream"
    assert (
        shown.headers["content-disposition"].startswith("attachment")
        and shown.headers.get("content-security-policy") != "sandbox"
    )


def test_the_review_routes_need_a_signed_in_person(env) -> None:  # noqa: ANN001
    from test_gate import new_client

    anonymous = new_client(env[0])
    for path in ("/review", "/review/message/1", "/review/original/1"):
        assert anonymous.get(path, follow_redirects=False).status_code in (303, 401), path
