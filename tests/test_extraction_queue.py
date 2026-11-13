"""The extraction queue and the extractor (PD-4.0a, DEC-54): documents are queued once, claimed one at a time, read with
a stub model, and every failure ends in the state the design says, with fixed words and no document text."""

from __future__ import annotations

import asyncio
import json
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
from sqlalchemy import inspect, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from po_dashboard.config import Settings
from po_dashboard.db.ledger import Acknowledgement, Invoice, InvoiceCharge, PurchaseOrder, Shipment, ShipmentTracking
from po_dashboard.db.mail import Extraction, SourceDocument
from po_dashboard.extraction import prompts, queue, runner, schema, window
from po_dashboard.extraction.runner import Extractor
from po_dashboard.intake import text as intake_text
from po_dashboard.intake.reading import read_pending
from po_dashboard.intake.store import FileStore
from po_dashboard.models import llm
from po_dashboard.scheduler.loop import run_extractor
from test_extraction import fake_post
from test_gate import env  # noqa: F401
from test_intake import db, mail, put, store  # noqa: F401
from test_scanner import drop, mail_dir, scan  # noqa: F401

MODEL = "stub-model:1b"
T0 = datetime(2026, 11, 12, 12, 0, tzinfo=UTC)
GOOD = {"doc_type": "order_acknowledgement", "po_number": "4521"}


def documents(db: Session) -> list[SourceDocument]:  # noqa: F811
    return list(db.scalars(select(SourceDocument).order_by(SourceDocument.id)))


def reader(db: Session, store: FileStore, count: int = 1) -> list[int]:  # noqa: F811
    """Store `count` messages and read their text; returns the document ids."""
    for n in range(count):
        put(db, store, mail(subject=f"Order {n}", body=f"Acknowledging PO 4521, item {n}"))
        # a different Message-ID makes a different message; the body text differs, so the hash does
    read_pending(db, store, limit=500)
    db.commit()
    return [d.id for d in documents(db)]


def rows(db: Session) -> list[Extraction]:  # noqa: F811
    db.expire_all()
    return list(db.scalars(select(Extraction).order_by(Extraction.id)))


class Stub:
    """The model call, scripted: each reply is a result to return or an exception to raise."""

    def __init__(self, *replies: Any) -> None:
        self.replies, self.calls = list(replies), []

    def __call__(self, settings: Settings, text: str, *, model: str | None = None, **_: Any) -> llm.Extraction:
        self.calls.append((text, model))
        reply = self.replies.pop(0) if self.replies else GOOD
        if isinstance(reply, Exception):
            raise reply
        if isinstance(reply, llm.Extraction):
            return reply
        if callable(reply):
            return reply()  # type: ignore[no-any-return]
        return llm.Extraction(answer=reply, seconds=1.5, raw=json.dumps(reply), prompt_tokens=321)


def extractor(env, store: FileStore, stub: Stub, clock: list[datetime] | None = None) -> Extractor:  # noqa: ANN001, F811
    now = clock or [T0]
    return Extractor(env[1], store, env[2], extract=stub, now=lambda: now[0])


# ---- the tables


def test_the_new_tables_and_links_exist_and_refuse_nonsense(env, db: Session) -> None:  # noqa: ANN001, F811
    names = set(inspect(env[1]).get_table_names())
    assert {"extraction", "shipment_tracking", "invoice_charge"} <= names
    for table in ("acknowledgement", "shipment", "invoice"):
        keys = inspect(env[1]).get_foreign_keys(table)
        links = [fk for fk in keys if fk["constrained_columns"] == ["source_document_id"]]
        assert links and links[0]["referred_table"] == "source_document", table
    db.add(Acknowledgement(po_id=1, source_document_id=999))  # no such PO, no such document
    with pytest.raises(IntegrityError):
        db.flush()
    db.rollback()


def test_the_invoice_number_index_from_migration_0006_survived_the_rebuild(env) -> None:  # noqa: ANN001
    """The batch that adds the document link rebuilds the invoice table, which loses an index on an expression."""
    with env[1].connect() as conn:
        found = conn.exec_driver_sql(
            "select sql from sqlite_master where type = 'index' and name = 'uq_invoice_po_id_lower_invoice_number'"
        ).scalar()
    assert found is not None and "lower(invoice_number)" in found and "UNIQUE" in found.upper()


def test_a_document_an_entry_came_from_cannot_be_deleted(env, db: Session, store: FileStore) -> None:  # noqa: ANN001, F811
    from test_orders_web import make_vendor  # noqa: F401  (only to show a PO exists in the same database)

    (document_id,) = reader(db, store)
    from po_dashboard.db.ledger import Vendor

    vendor = Vendor(name="Acme Fasteners")
    db.add(vendor)
    db.flush()
    po = PurchaseOrder(po_number="4521", vendor_id=vendor.id, currency="USD")
    db.add(po)
    db.flush()
    db.add(Acknowledgement(po_id=po.id, source_document_id=document_id))
    db.commit()
    with pytest.raises(IntegrityError):
        db.delete(db.get(SourceDocument, document_id))
        db.flush()
    db.rollback()


def test_tracking_numbers_and_charges_keep_their_rules(env, db: Session) -> None:  # noqa: ANN001, F811
    from po_dashboard.db.ledger import Vendor

    vendor = Vendor(name="Acme")
    db.add(vendor)
    db.flush()
    po = PurchaseOrder(po_number="1", vendor_id=vendor.id, currency="USD")
    db.add(po)
    db.flush()
    ship = Shipment(po_id=po.id)
    invoice = Invoice(po_id=po.id, invoice_number="INV-1")
    db.add_all([ship, invoice])
    db.flush()
    db.add_all([ShipmentTracking(shipment_id=ship.id, tracking_number="1Z999"),
                InvoiceCharge(invoice_id=invoice.id, kind="other", label="Discount", amount=-5)])  # fmt: skip
    db.commit()
    assert db.scalar(select(InvoiceCharge.amount)) == -5  # a discount is a negative charge
    db.add(ShipmentTracking(shipment_id=ship.id, tracking_number="1Z999"))
    with pytest.raises(IntegrityError):
        db.flush()
    db.rollback()
    db.add(InvoiceCharge(invoice_id=invoice.id, kind="bribe", label="x", amount=1))
    with pytest.raises(IntegrityError):
        db.flush()
    db.rollback()


def test_an_extraction_is_one_per_document_prompt_and_model_and_has_known_states(
    env, db: Session, store: FileStore  # noqa: ANN001, F811
) -> None:
    (document_id,) = reader(db, store)
    base: dict[str, Any] = dict(document_id=document_id, model=MODEL, prompt_version=1, schema_version=1)
    db.add(Extraction(**base))
    db.commit()
    db.add(Extraction(**{**base, "prompt_version": 2}))  # a new prompt is a new row
    db.commit()
    db.add(Extraction(**base))
    with pytest.raises(IntegrityError):
        db.flush()
    db.rollback()
    db.add(Extraction(**{**base, "model": "other:7b", "state": "stuck"}))
    with pytest.raises(IntegrityError):
        db.flush()
    db.rollback()


# ---- queueing


def test_every_read_document_is_queued_once_and_unread_ones_are_not(db: Session, store: FileStore) -> None:  # noqa: F811
    put(db, store, mail(subject="Has a file", files=(("a.docx", "application/zip", b"PK nonsense"),)))
    read_pending(db, store)  # the body is read; the bad Word file is not_read
    db.commit()
    states = {d.filename: d.state for d in documents(db)}
    assert states == {"Message body": "read", "a.docx": "not_read"}
    assert queue.queue_missing(db, MODEL) == 1
    db.commit()
    (row,) = rows(db)
    assert (row.state, row.attempts, row.model, row.prompt_version) == ("queued", 0, MODEL, prompts.PROMPT_VERSION)
    assert row.schema_version == schema.SCHEMA_VERSION and row.review_state is None
    assert queue.queue_missing(db, MODEL) == 0  # once


def test_a_new_prompt_does_not_queue_a_document_that_already_has_a_reading(
    db: Session, store: FileStore, monkeypatch: pytest.MonkeyPatch  # noqa: F811
) -> None:
    reader(db, store)
    queue.queue_missing(db, MODEL)
    db.commit()
    monkeypatch.setattr(prompts, "PROMPT_VERSION", prompts.PROMPT_VERSION + 1)
    assert queue.queue_missing(db, MODEL) == 0 and len(rows(db)) == 1  # DEC-54 (9)


def test_the_scanner_queues_each_document_it_reads_and_catches_up_on_older_ones(scan, mail_dir: Path) -> None:  # noqa: ANN001, F811
    drop(mail_dir, 2)
    report = scan.scan()
    assert report.read == 2
    with Session(scan.engine) as db:
        assert [r.state for r in rows(db)] == ["queued", "queued"]
        db.query(Extraction).delete()  # as if the documents were read before the queue existed
        db.commit()
    scan.scan()  # nothing new arrives, but the sweep queues what has no reading yet
    with Session(scan.engine) as db:
        assert len(rows(db)) == 2


def test_the_model_name_in_the_queue_is_the_one_in_settings(scan, mail_dir: Path) -> None:  # noqa: ANN001, F811
    scan.model = "some-other:3b"
    drop(mail_dir, 1)
    scan.scan()
    with Session(scan.engine) as db:
        assert rows(db)[0].model == "some-other:3b"


# ---- claiming


def queued(db: Session, store: FileStore, count: int) -> None:  # noqa: F811
    reader(db, store, count)
    queue.queue_missing(db, MODEL)
    db.commit()


def test_the_oldest_due_row_is_claimed_first_and_only_once(env, db: Session, store: FileStore) -> None:  # noqa: ANN001, F811
    queued(db, store, 2)
    first = queue.claim(env[1], T0)
    second = queue.claim(env[1], T0)
    assert first is not None and second is not None and first < second
    assert queue.claim(env[1], T0) is None  # nothing left
    got = rows(db)
    assert [(r.state, r.attempts, r.started_at is not None) for r in got] == [("running", 1, True)] * 2


def test_a_row_is_not_claimed_before_its_time(env, db: Session, store: FileStore) -> None:  # noqa: ANN001, F811
    queued(db, store, 1)
    row = rows(db)[0]
    row.next_attempt_at = T0 + timedelta(minutes=5)
    db.commit()
    assert queue.claim(env[1], T0) is None and queue.claim(env[1], T0 + timedelta(minutes=4)) is None
    assert queue.claim(env[1], T0 + timedelta(minutes=5)) == row.id


def test_many_claimers_at_once_never_hold_the_same_row(env, db: Session, store: FileStore) -> None:  # noqa: ANN001, F811
    queued(db, store, 12)
    got: list[int] = []
    guard = threading.Lock()

    def worker() -> None:
        while (found := queue.claim(env[1], T0)) is not None:
            with guard:
                got.append(found)

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(got) == 12 and len(set(got)) == 12


def test_a_row_a_crash_left_running_goes_back_without_counting_the_try(env, db: Session, store: FileStore) -> None:  # noqa: ANN001, F811
    queued(db, store, 2)
    queue.claim(env[1], T0)
    assert queue.recover_running(env[1]) == 1
    assert [(r.state, r.attempts) for r in rows(db)] == [("queued", 0), ("queued", 0)]


def test_the_counts_for_the_health_strip(env, db: Session, store: FileStore) -> None:  # noqa: ANN001, F811
    queued(db, store, 3)
    queue.claim(env[1], T0)
    rows(db)[2].state = "failed"
    db.commit()
    assert queue.waiting_counts(db) == {"queued": 1, "running": 1, "failed": 1}


# ---- the extractor


def test_a_read_is_stored_exactly_with_the_numbers_the_model_gave(env, db: Session, store: FileStore) -> None:  # noqa: ANN001, F811
    queued(db, store, 1)
    answer = {**GOOD, "lines": [{"qty": 14.9}], "total": 1234.5000}
    raw = '{"doc_type": "order_acknowledgement", "po_number": "4521", "total": 1234.5000}'
    stub = Stub(llm.Extraction(answer=answer, seconds=2.5, raw=raw, prompt_tokens=700))
    outcome = extractor(env, store, stub).run_once()
    assert outcome.kind == "done"
    (row,) = rows(db)
    assert (row.state, row.doc_type, row.po_number, row.seconds, row.prompt_tokens) == (
        "done", "order_acknowledgement", "4521", 2.5, 700)
    assert "1234.5000" in (row.answer or "")  # the reply as it came: no float in between
    assert row.attempts == 1 and row.last_error is None and row.finished_at == T0
    assert row.review_state == "unmatched" and row.po_id is None  # PO 4521 is not in this database (PD-4.3)
    assert stub.calls[0][1] == row.model


def test_the_text_the_model_reads_is_the_stored_text_of_the_document(env, db: Session, store: FileStore) -> None:  # noqa: ANN001, F811
    queued(db, store, 1)
    stub = Stub(GOOD)
    extractor(env, store, stub).run_once()
    assert "Acknowledging PO 4521, item 0" in stub.calls[0][0]


def test_an_unknown_document_type_or_a_blank_po_number_is_kept_as_nothing(env, db: Session, store: FileStore) -> None:  # noqa: ANN001, F811
    queued(db, store, 1)
    extractor(env, store, Stub({"doc_type": "poem", "po_number": "   "})).run_once()
    (row,) = rows(db)
    assert row.state == "done" and row.doc_type is None and row.po_number is None


def test_idle_when_nothing_is_queued(env, store: FileStore) -> None:  # noqa: ANN001, F811
    assert extractor(env, store, Stub()).run_once().kind == "idle"


def test_a_model_that_cannot_be_reached_costs_the_document_nothing(env, db: Session, store: FileStore) -> None:  # noqa: ANN001, F811
    queued(db, store, 1)
    stub = Stub(*[llm.ModelUnavailable("the model server is not reachable")] * 6)
    for _ in range(6):  # as many times as the model stays down
        assert extractor(env, store, stub).run_once().kind == "model_down"
    (row,) = rows(db)
    assert (row.state, row.attempts, row.last_error, row.next_attempt_at) == ("queued", 0, None, None)
    assert extractor(env, store, Stub(GOOD)).run_once().kind == "done"  # and it is read when the model is back


def test_a_server_error_is_tried_again_after_1_5_and_30_minutes_then_fails(env, db: Session, store: FileStore) -> None:  # noqa: ANN001, F811
    queued(db, store, 1)
    clock = [T0]
    work = extractor(env, store, Stub(*[llm.ModelError("the model server gave no answer (ReadTimeout)")] * 4), clock)
    for wait, attempts in ((60, 1), (300, 2), (1800, 3)):
        assert work.run_once().kind == "retry"
        (row,) = rows(db)
        assert (row.state, row.attempts, row.last_error) == ("queued", attempts, runner.NO_ANSWER)
        assert row.next_attempt_at is not None
        assert row.next_attempt_at.replace(tzinfo=UTC) == clock[0] + timedelta(seconds=wait)
        assert work.run_once().kind == "idle"  # not yet
        clock[0] += timedelta(seconds=wait)
    assert work.run_once().kind == "failed"
    (row,) = rows(db)
    assert (row.state, row.attempts, row.last_error) == ("failed", 4, runner.GAVE_UP) and row.finished_at is not None
    assert work.run_once().kind == "idle"  # a failed row is not tried again by itself


def test_an_answer_that_cannot_be_used_fails_at_once_in_fixed_words(env, db: Session, store: FileStore) -> None:  # noqa: ANN001, F811
    queued(db, store, 1)
    extractor(env, store, Stub(llm.BadAnswer("SECRET DOCUMENT WORDS in the reply"))).run_once()
    (row,) = rows(db)
    assert (row.state, row.attempts, row.last_error) == ("failed", 1, runner.BAD_ANSWER)


def test_a_bug_in_the_call_fails_the_document_with_the_kind_of_error_only(env, db: Session, store: FileStore) -> None:  # noqa: ANN001, F811
    queued(db, store, 1)
    extractor(env, store, Stub(RuntimeError("SECRET DOCUMENT WORDS"))).run_once()
    (row,) = rows(db)
    assert row.state == "failed" and row.last_error == "could not be read (RuntimeError)"


def test_a_document_with_no_stored_text_fails_in_fixed_words(env, db: Session, store: FileStore) -> None:  # noqa: ANN001, F811
    queued(db, store, 1)
    document = documents(db)[0]
    document.text_path = None
    db.commit()
    assert extractor(env, store, Stub()).run_once().kind == "failed"
    assert rows(db)[0].last_error == runner.NO_TEXT


def test_a_change_made_while_the_model_works_is_not_overwritten(env, db: Session, store: FileStore) -> None:  # noqa: ANN001, F811
    """A person who retries or dismisses a document while it is being read is not undone by the late answer."""
    queued(db, store, 1)

    def meanwhile() -> llm.Extraction:
        with Session(env[1]) as other:
            other.connection().exec_driver_sql("UPDATE extraction SET state = 'failed', last_error = 'dismissed'")
            other.commit()
        return llm.Extraction(answer=GOOD, seconds=1, raw="{}")

    assert extractor(env, store, Stub(meanwhile)).run_once().kind == "idle"
    (row,) = rows(db)
    assert (row.state, row.last_error, row.answer) == ("failed", "dismissed", None)


def test_the_model_is_called_with_no_transaction_open(env, db: Session, store: FileStore) -> None:  # noqa: ANN001, F811
    """Another connection can write while the model works: no lock is held across the call."""
    queued(db, store, 2)

    def meanwhile() -> llm.Extraction:
        with Session(env[1]) as other:
            other.connection().exec_driver_sql("BEGIN IMMEDIATE")  # would block (and time out) if a lock were held
            other.execute(select(Extraction.id))
            other.rollback()
        return llm.Extraction(answer=GOOD, seconds=1, raw=json.dumps(GOOD))

    assert extractor(env, store, Stub(meanwhile)).run_once().kind == "done"


def test_a_row_a_crash_left_running_is_read_after_recovery(env, db: Session, store: FileStore) -> None:  # noqa: ANN001, F811
    queued(db, store, 1)
    queue.claim(env[1], T0)  # claimed, then the app died
    work = extractor(env, store, Stub(GOOD))
    assert work.run_once().kind == "idle"
    assert work.recover() == 1 and work.run_once().kind == "done"


def test_a_long_document_is_cut_to_the_window_and_flagged(env, db: Session, store: FileStore) -> None:  # noqa: ANN001, F811
    put(db, store, mail(subject="Big", body="Order 4521 confirmed.\n\n" + "line of words\n" * 4000))
    read_pending(db, store)
    queue.queue_missing(db, MODEL)
    db.commit()
    stub = Stub(GOOD)
    extractor(env, store, stub).run_once()
    sent = stub.calls[0][0]
    assert len(sent) <= window.WINDOW_CHARS and window.CUT_OFF in sent and "Order 4521 confirmed." in sent
    (row,) = rows(db)
    assert json.loads(row.flags or "[]") == [{"flag": "read_in_part", "level": "check"}]


# ---- the reading window


def test_a_short_text_is_untouched() -> None:
    fitted = window.fit("short text")
    assert fitted == window.Windowed("short text")


def test_a_long_text_loses_its_quoted_earlier_messages_first_and_is_not_flagged() -> None:
    current = "Hello, PO 4521 ships Friday.\n"
    text = current + intake_text.QUOTE_NOTE + "\n" + "old quoted line\n" * 2000
    fitted = window.fit(text, limit=2000)
    assert fitted.left_out_quotes and not fitted.read_in_part
    assert fitted.text.startswith(current.strip()) and window.QUOTES_LEFT_OUT in fitted.text
    assert "old quoted line" not in fitted.text and intake_text.QUOTE_NOTE not in fitted.text


def test_a_text_still_too_long_is_cut_and_flagged_and_says_so() -> None:
    text = "word " * 5000
    fitted = window.fit(text, limit=1000)
    assert fitted.read_in_part and len(fitted.text) <= 1000 and window.CUT_OFF in fitted.text
    both = window.fit("current\n" + intake_text.QUOTE_NOTE + "\n" + "x" * 3000, limit=100)
    assert not both.read_in_part and both.left_out_quotes  # the quotes alone were enough


def test_a_current_message_longer_than_the_window_is_cut_even_when_it_has_quotes() -> None:
    fitted = window.fit("current " * 500 + "\n" + intake_text.QUOTE_NOTE + "\nold", limit=300)
    assert fitted.left_out_quotes and fitted.read_in_part and len(fitted.text) <= 300


# ---- the gateway's two kinds of error


def test_a_reply_that_is_not_the_schema_or_was_cut_off_will_not_be_tried_again(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_post(monkeypatch, httpx.Response(200, json={"message": {"content": "not json at all"}}))
    with pytest.raises(llm.BadAnswer):
        llm.extract(Settings(), "text")
    fake_post(monkeypatch, httpx.Response(200, json={"message": {"content": "[1, 2]"}}))
    with pytest.raises(llm.BadAnswer):
        llm.extract(Settings(), "text")
    cut = httpx.Response(200, json={"message": {"content": json.dumps(GOOD)}, "done_reason": "length"})
    fake_post(monkeypatch, cut)
    with pytest.raises(llm.BadAnswer, match="cut off"):
        llm.extract(Settings(), "text")


def test_a_server_error_can_be_tried_again_and_the_prompt_count_is_kept(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_post(monkeypatch, httpx.Response(500))
    with pytest.raises(llm.ModelError) as problem:
        llm.extract(Settings(), "text")
    assert not isinstance(problem.value, llm.BadAnswer | llm.ModelUnavailable)
    counted = {"message": {"content": json.dumps(GOOD)}, "prompt_eval_count": 4321, "done_reason": "stop"}
    ok = httpx.Response(200, json=counted)
    fake_post(monkeypatch, ok)
    assert llm.extract(Settings(), "text").prompt_tokens == 4321
    fake_post(monkeypatch, httpx.Response(200, json={"message": {"content": json.dumps(GOOD)}}))
    assert llm.extract(Settings(), "text").prompt_tokens is None


# ---- the loop


def test_the_loop_recovers_first_reads_on_without_a_pause_and_rests_when_idle_or_the_model_is_down() -> None:
    log: list[str] = []
    outcomes = [runner.Outcome("done"), runner.Outcome("model_down"), runner.Outcome("idle")]

    class Fake:
        def recover(self) -> int:
            log.append("recover")
            return 0

        def run_once(self) -> runner.Outcome:
            log.append("run")
            if len(log) == 3:
                raise RuntimeError("boom")  # the loop carries on
            return outcomes.pop(0) if outcomes else runner.Outcome("idle")

    async def go() -> None:
        task = asyncio.create_task(run_extractor(Fake(), idle=0.01, down=0.02))  # type: ignore[arg-type]
        await asyncio.sleep(0.4)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(go())
    assert log[0] == "recover" and log.count("run") >= 4


def test_the_extractor_needs_its_model_call_passed_in_so_no_test_reaches_a_real_model(env, store: FileStore) -> None:  # noqa: ANN001, F811
    with pytest.raises(TypeError):
        Extractor(env[1], store, env[2])  # type: ignore[call-arg]
