"""The Failed documents page (PD-4.0a, DEC-54): what the model could not read, with Retry and Dismiss."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from po_dashboard.db.mail import Extraction, SourceMessage
from po_dashboard.db.models import AuditLog
from po_dashboard.extraction import queue, runner
from po_dashboard.intake.reading import read_pending
from po_dashboard.intake.store import FileStore
from test_gate import ORIGIN, env, new_client, post  # noqa: F401
from test_intake import db, mail, put, store  # noqa: F401
from test_orders_web import client  # noqa: F401

MODEL = "stub-model:1b"


def failures(db: Session, store: FileStore, *subjects: str) -> list[int]:  # noqa: F811
    """Store a message for each subject, read it, queue it and mark it failed; returns the extraction ids."""
    for n, subject in enumerate(subjects):
        put(db, store, mail(subject=subject, body=f"Words {n}"))
    read_pending(db, store, limit=500)
    queue.queue_missing(db, MODEL)
    for row in db.scalars(select(Extraction)):
        row.state, row.attempts, row.last_error, row.finished_at = "failed", 4, runner.GAVE_UP, datetime.now(UTC)
    db.commit()
    return [row.id for row in db.scalars(select(Extraction).order_by(Extraction.id))]


def state(db: Session, extraction_id: int) -> Extraction:  # noqa: F811
    db.flush()  # keep what the test changed, then read the rest again
    db.expire_all()
    return db.get(Extraction, extraction_id)  # type: ignore[return-value]


def test_the_list_shows_each_failed_document_with_its_message_and_why(
    db: Session, store: FileStore, client: TestClient
) -> None:  # noqa: F811
    failures(db, store, "Ack for PO 4521")
    page = client.get("/documents/failed")
    assert page.status_code == 200 and "Ack for PO 4521" in page.text and "v@vendor.test" in page.text
    assert (
        runner.GAVE_UP in page.text and "Message body" in page.text and "Retry" in page.text and "Dismiss" in page.text
    )


def test_only_failed_documents_that_nobody_dismissed_are_listed(
    db: Session, store: FileStore, client: TestClient
) -> None:  # noqa: F811
    first, second, third = failures(db, store, "One", "Two", "Three")
    state(db, second).state = "queued"
    state(db, third).review_state = "dismissed"
    db.commit()
    text = client.get("/documents/failed").text
    assert "One" in text and "Two" not in text and "Three" not in text


def test_markup_in_a_subject_is_shown_as_text(db: Session, store: FileStore, client: TestClient) -> None:  # noqa: F811
    failures(db, store, "<script>alert(1)</script> & <b>bold</b>")
    text = client.get("/documents/failed").text
    assert "<script>alert(1)" not in text and "&lt;script&gt;" in text and "<b>bold</b>" not in text


def test_an_empty_list_says_so(client: TestClient) -> None:
    assert "No failed documents." in client.get("/documents/failed").text


def test_retry_puts_the_document_back_with_its_tries_reset_and_it_is_audited(
    db: Session,
    store: FileStore,
    client: TestClient,
    env,  # noqa: ANN001, F811
) -> None:
    (found,) = failures(db, store, "Retry me")
    version = state(db, found).version
    response = post(client, f"/documents/failed/{found}/retry", {"version": str(version)})
    assert response.status_code == 303 and response.headers["location"] == "/documents/failed"
    row = state(db, found)
    assert (row.state, row.attempts, row.last_error, row.next_attempt_at, row.finished_at) == (
        "queued",
        0,
        None,
        None,
        None,
    )
    assert row.review_state is None and row.version == version + 1
    assert "Retry me" not in client.get("/documents/failed").text
    assert [a.action for a in db.scalars(select(AuditLog)) if a.action.startswith("extraction.")] == [
        "extraction.retry"
    ]
    assert queue.claim(env[1]) == found  # and the extractor can take it


def test_dismiss_needs_a_reason_files_the_document_and_says_who_and_why(
    db: Session,
    store: FileStore,
    client: TestClient,  # noqa: F811
) -> None:
    (found,) = failures(db, store, "Dismiss me")
    version = str(state(db, found).version)
    refused = post(client, f"/documents/failed/{found}/dismiss", {"version": version, "reason": "   "})
    assert refused.status_code == 400 and "Reason is needed" in refused.text and state(db, found).review_state is None
    done = post(
        client, f"/documents/failed/{found}/dismiss", {"version": version, "reason": "A newsletter, not an order"}
    )
    assert done.status_code == 303
    row = state(db, found)
    assert (row.state, row.review_state, row.review_note) == ("failed", "dismissed", "A newsletter, not an order")
    assert row.reviewed_by is not None and row.reviewed_at is not None
    assert "Dismiss me" not in client.get("/documents/failed").text
    audit = [a for a in db.scalars(select(AuditLog)) if a.action == "extraction.dismiss"]
    assert len(audit) == 1 and "A newsletter, not an order" in (audit[0].after or "")


def test_a_page_that_is_out_of_date_is_refused_and_changes_nothing(
    db: Session, store: FileStore, client: TestClient
) -> None:  # noqa: F811
    (found,) = failures(db, store, "Stale")
    old = str(state(db, found).version)
    state(db, found).last_error = "changed meanwhile"  # someone else touched it: the version moves
    db.commit()
    for action, extra in (("retry", {}), ("dismiss", {"reason": "why"})):
        refused = post(client, f"/documents/failed/{found}/{action}", {"version": old, **extra})
        assert refused.status_code == 400 and "changed by someone else" in refused.text, action
    assert state(db, found).state == "failed" and state(db, found).review_state is None
    missing = post(client, f"/documents/failed/{found}/retry", {})  # a form with no version at all
    assert missing.status_code == 400


def test_a_document_that_is_not_failed_cannot_be_retried_or_dismissed(
    db: Session, store: FileStore, client: TestClient
) -> None:  # noqa: F811
    (found,) = failures(db, store, "Fine now")
    row = state(db, found)
    row.state = "queued"
    db.commit()
    for action in ("retry", "dismiss"):
        refused = post(client, f"/documents/failed/{found}/{action}", {"version": str(row.version), "reason": "x"})
        assert refused.status_code == 400 and "not on the Failed list any more" in refused.text
    assert post(client, "/documents/failed/99999/retry", {"version": "1"}).status_code == 400


def test_everyone_signed_in_may_use_it_and_nobody_else(db: Session, store: FileStore, client: TestClient, env) -> None:  # noqa: ANN001, F811
    (found,) = failures(db, store, "Shared")
    assert client.get("/documents/failed").status_code == 200  # a buyer, not only an admin (DEC-29)
    stranger = new_client(env[0])
    assert stranger.get("/documents/failed", headers={"Accept": "text/html"}).status_code == 303
    assert stranger.post(f"/documents/failed/{found}/retry", headers=ORIGIN).status_code in (401, 403)


def test_every_action_needs_the_security_token(db: Session, store: FileStore, client: TestClient) -> None:  # noqa: F811
    (found,) = failures(db, store, "Token")
    for action in ("retry", "dismiss"):
        path = f"/documents/failed/{found}/{action}"
        assert client.post(path, data={"version": "1", "reason": "x"}, headers=ORIGIN).status_code == 403
        assert (
            client.post(path, data={"version": "1", "reason": "x", "csrf_token": "no"}, headers=ORIGIN).status_code
            == 403
        )
    assert state(db, found).state == "failed"


def test_home_counts_the_waiting_and_the_failed(db: Session, store: FileStore, client: TestClient) -> None:  # noqa: F811
    first, second, third = failures(db, store, "A", "B", "C")
    state(db, second).state = "queued"
    state(db, third).review_state = "dismissed"  # dealt with: not counted
    db.commit()
    page = client.get("/").text
    assert 'href="/documents/failed"' in page
    assert "Waiting to be read" in page and "Failed documents" in page
    counts = queue.waiting_counts(db)
    assert counts == {"queued": 1, "running": 0, "failed": 1}
    only = [m.subject for m in db.scalars(select(SourceMessage))]
    assert set(only) == {"A", "B", "C"}


def test_home_says_when_the_model_is_not_reachable(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    from po_dashboard.models import llm

    page = client.get("/").text
    assert 'class="value bad">not reachable' in page  # the test setup makes the server unreachable
    monkeypatch.setattr(llm, "is_reachable", lambda settings, timeout=1.0: True)
    assert 'class="value ok">reachable' in client.get("/").text


def test_the_sweep_of_every_route_still_finds_nothing_open(env, client: TestClient) -> None:  # noqa: ANN001, F811
    paths = env[0].openapi()["paths"]
    assert "/documents/failed" in paths and "/documents/failed/{extraction_id}/retry" in paths


def test_no_document_words_are_in_the_page_except_the_message_it_came_in(
    db: Session, store: FileStore, client: TestClient
) -> None:  # noqa: F811
    failures(db, store, "Subject line")
    text = client.get("/documents/failed").text
    assert "Words 0" not in text  # the body text of the document is never shown here
    pytest.importorskip("jinja2")
