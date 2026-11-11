"""Intake (PD-3.2): every original is kept once, unchanged, under its hash; the rows say what each message held."""

from __future__ import annotations

import email
import hashlib
import logging
import os
import stat
from datetime import UTC, datetime
from email import policy
from email.message import EmailMessage
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from po_dashboard.db.mail import Mailbox, SourceDocument, SourceMessage
from po_dashboard.intake import messages
from po_dashboard.intake.messages import DUPLICATE, STORED, TOO_OLD, store_message
from po_dashboard.intake.store import FileStore
from po_dashboard.mail.source import INBOX, RawMessage
from test_gate import env  # noqa: F401

GOLDEN = Path(__file__).parent / "data" / "golden"


@pytest.fixture
def db(env, tmp_path: Path):  # noqa: ANN001, ANN201, F811
    with Session(env[1]) as session:
        session.add(Mailbox(address="purchasing@example-buyer.test", provider="folder", location=str(tmp_path)))
        session.commit()
        yield session


@pytest.fixture
def store(tmp_path: Path) -> FileStore:
    return FileStore(tmp_path / "originals")


def mailbox(db: Session) -> Mailbox:
    return db.scalars(select(Mailbox)).one()


def mail(subject: str = "Hello", body: str = "Body text", date: str | None = "Mon, 02 Nov 2026 09:30:00 +0000",
         files: tuple[tuple[str, str, bytes], ...] = ()) -> bytes:
    msg = EmailMessage()
    msg["From"], msg["To"] = "V <v@vendor.test>", "p@example-buyer.test"
    msg["Subject"], msg["Message-ID"] = subject, "<m1@vendor.test>"
    if date:
        msg["Date"] = date
    msg.set_content(body)
    for name, content_type, data in files:
        main, _, sub = content_type.partition("/")
        msg.add_attachment(data, maintype=main, subtype=sub, filename=name)
    return msg.as_bytes()


def put(db: Session, store: FileStore, raw: bytes, folder: str = INBOX) -> messages.Outcome:
    outcome = store_message(db, store, mailbox(db), RawMessage(hashlib.sha256(raw).hexdigest(), folder, raw))
    db.commit()
    return outcome


def test_a_message_is_stored_unchanged_with_its_facts(db: Session, store: FileStore) -> None:
    raw = mail(subject="Order Acknowledgement for PO 4500123")
    outcome = put(db, store, raw)
    assert outcome.result == STORED and outcome.documents == 1
    row = db.scalars(select(SourceMessage)).one()
    assert row.subject == "Order Acknowledgement for PO 4500123" and row.sender == "V <v@vendor.test>"
    assert row.received_at == datetime(2026, 11, 2, 9, 30, tzinfo=UTC) and row.folder == INBOX
    assert row.content_hash == hashlib.sha256(raw).hexdigest() and row.size == len(raw)
    assert store.read(row.raw_path) == raw
    body = db.scalars(select(SourceDocument)).one()
    assert body.kind == "body" and body.stored_path == row.raw_path and body.state == "stored"


def test_an_attachment_is_stored_under_its_hash(db: Session, store: FileStore) -> None:
    pdf = b"%PDF-1.4 made up"
    outcome = put(db, store, mail(files=(("ack.pdf", "application/pdf", pdf),)))
    assert outcome.documents == 2
    doc = db.scalars(select(SourceDocument).where(SourceDocument.kind == "pdf")).one()
    assert doc.filename == "ack.pdf" and doc.content_hash == hashlib.sha256(pdf).hexdigest() and doc.size == len(pdf)
    assert store.read(doc.stored_path) == pdf and doc.state == "stored"


def test_the_same_message_twice_is_stored_once(db: Session, store: FileStore) -> None:
    raw = mail()
    assert put(db, store, raw).result == STORED
    assert put(db, store, raw).result == DUPLICATE
    assert len(db.scalars(select(SourceMessage)).all()) == 1


def test_the_same_bytes_from_another_folder_are_still_one_message(db: Session, store: FileStore) -> None:
    raw = mail()
    put(db, store, raw, "Inbox")
    assert put(db, store, raw, "Sent Items").result == DUPLICATE


def test_an_attachment_already_stored_is_not_stored_again_but_the_new_message_is(db: Session, store: FileStore) -> None:
    pdf = ("ack.pdf", "application/pdf", b"%PDF same bytes")
    put(db, store, mail(subject="First", files=(pdf,)))
    outcome = put(db, store, mail(subject="Second", files=(pdf,)))
    assert outcome.result == STORED and outcome.duplicate_attachments == 1 and outcome.documents == 1
    assert len(db.scalars(select(SourceMessage)).all()) == 2
    assert len(db.scalars(select(SourceDocument).where(SourceDocument.kind == "pdf")).all()) == 1
    assert len(db.scalars(select(SourceDocument).where(SourceDocument.kind == "body")).all()) == 2


def test_two_messages_with_the_same_words_each_keep_their_body(db: Session, store: FileStore) -> None:
    put(db, store, mail(subject="One", body="Thanks, received."))
    put(db, store, mail(subject="Two", body="Thanks, received."))
    assert len(db.scalars(select(SourceDocument).where(SourceDocument.kind == "body")).all()) == 2


def test_the_database_itself_refuses_a_second_copy_of_an_attachment(db: Session, store: FileStore) -> None:
    put(db, store, mail(files=(("a.pdf", "application/pdf", b"%PDF x"),)))
    first = db.scalars(select(SourceDocument).where(SourceDocument.kind == "pdf")).one()
    db.add(SourceDocument(message_id=first.message_id, filename="b.pdf", kind="pdf", stored_path=first.stored_path,
                          content_hash=first.content_hash, size=1))
    with pytest.raises(IntegrityError):
        db.commit()


@pytest.mark.parametrize("name", [
    "../../etc/passwd", "..\\..\\windows\\system32\\evil.pdf", "/etc/shadow", "x" * 1000 + ".pdf",
    "\u202egnp.exe", "a%00b%0Ac%0D.pdf", "%2E%2E%2F%2E%2E%2Fetc%2Fpasswd",
])
def test_a_hostile_file_name_never_becomes_a_path(db: Session, store: FileStore, tmp_path: Path, name: str) -> None:
    raw = mail(files=(("PLACEHOLDER.pdf", "application/pdf", b"%PDF hostile"),))
    # a name the mail library refuses to write (a line break, a NUL) is sent in RFC 2231 form, as an attacker would
    header = f"filename*=UTF-8''{name}" if "%" in name else f'filename="{name}"'
    raw = raw.replace(b'filename="PLACEHOLDER.pdf"', header.encode())
    put(db, store, raw)
    doc = db.scalars(select(SourceDocument).where(SourceDocument.kind != "body")).one()
    assert "/" not in doc.filename and "\\" not in doc.filename and len(doc.filename) <= 255
    assert not any(ch in doc.filename for ch in "\x00\r\n")
    inside = {p.relative_to(store.root).as_posix() for p in store.root.rglob("*") if p.is_file()}
    assert all(len(p.split("/")[-1]) == 64 for p in inside)  # every stored file is named by its hash
    outside = [p for p in tmp_path.rglob("*") if p.is_file() and store.root not in p.parents and "data" not in p.parts]
    assert outside == []


def test_files_the_app_does_not_read_are_stored_and_say_so(db: Session, store: FileStore) -> None:
    put(db, store, mail(files=(("tool.exe", "application/octet-stream", b"MZ"),
                               ("macro.xlsm", "application/vnd.ms-excel.sheet.macroEnabled.12", b"PK"))))
    docs = db.scalars(select(SourceDocument).where(SourceDocument.kind != "body")).all()
    assert {d.state for d in docs} == {"not_read"} and all(d.not_read_reason for d in docs)
    assert all(store.read(d.stored_path) for d in docs)


def test_a_file_over_the_size_limit_is_stored_and_not_read(db: Session, store: FileStore, monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setattr(messages, "MAX_FILE_BYTES", 10)
    put(db, store, mail(files=(("big.pdf", "application/pdf", b"%PDF " + b"x" * 100),)))
    doc = db.scalars(select(SourceDocument).where(SourceDocument.kind == "pdf")).one()
    assert doc.state == "not_read" and "20 MB" in (doc.not_read_reason or "")


def test_attachments_past_the_25th_are_stored_and_not_read(db: Session, store: FileStore) -> None:
    files = tuple((f"f{n}.pdf", "application/pdf", f"%PDF {n}".encode()) for n in range(1, 28))
    outcome = put(db, store, mail(files=files))
    docs = db.scalars(select(SourceDocument).where(SourceDocument.kind == "pdf").order_by(SourceDocument.id)).all()
    assert len(docs) == 27 and outcome.not_read == 2
    assert [d.state for d in docs].count("stored") == 25 and docs[-1].state == "not_read"


@pytest.mark.parametrize("raw", [b"", b"\x00\x01\x02 not mail at all", b"Subject: only\n", b"\xff" * 5000,
                                 b"Content-Type: multipart/mixed; boundary=zz\n\n--zz\nContent-Type: \x00\n\n--zz--"])
def test_a_broken_message_is_stored_and_does_not_stop_intake(db: Session, store: FileStore, raw: bytes) -> None:
    outcome = put(db, store, raw)
    assert outcome.result == STORED
    assert store.read(db.scalars(select(SourceMessage)).one().raw_path) == raw


def test_a_forwarded_message_is_kept_as_an_attachment(db: Session, store: FileStore) -> None:
    inner = mail(subject="Inner PO 4500999")
    outer = EmailMessage()
    outer["From"], outer["To"], outer["Subject"] = "a@v.test", "p@b.test", "Fwd"
    outer["Date"] = "Mon, 02 Nov 2026 09:30:00 +0000"
    outer.set_content("see attached")
    outer.add_attachment(email.message_from_bytes(inner, policy=policy.default))
    put(db, store, outer.as_bytes())
    doc = db.scalars(select(SourceDocument).where(SourceDocument.kind == "email")).one()
    assert b"Inner PO 4500999" in store.read(doc.stored_path)


def test_mail_from_before_the_start_date_is_not_stored(db: Session, store: FileStore) -> None:
    box = mailbox(db)
    box.read_since = datetime(2026, 11, 3, tzinfo=UTC)
    db.commit()
    assert put(db, store, mail(date="Mon, 02 Nov 2026 09:30:00 +0000")).result == TOO_OLD
    assert db.scalars(select(SourceMessage)).all() == [] and not store.root.exists()
    assert put(db, store, mail(subject="later", date="Tue, 03 Nov 2026 09:30:00 +0000")).result == STORED


def test_a_message_with_no_date_is_stored_with_the_time_it_was_read(db: Session, store: FileStore) -> None:
    assert put(db, store, mail(date=None)).result == STORED
    row = db.scalars(select(SourceMessage)).one()
    assert (datetime.now(UTC) - row.received_at).total_seconds() < 60


def test_the_store_is_owner_only_and_never_half_written(db: Session, store: FileStore) -> None:
    put(db, store, mail(files=(("a.pdf", "application/pdf", b"%PDF y"),)))
    for path in [store.root, *store.root.rglob("*")]:
        mode = stat.S_IMODE(path.stat().st_mode)
        assert mode & 0o077 == 0, (path, oct(mode))
    assert not list(store.root.rglob("*.tmp"))


def test_a_failure_while_writing_rows_leaves_nothing_in_the_database_and_the_retry_works(
    db: Session, store: FileStore, monkeypatch,  # noqa: ANN001
) -> None:
    raw = mail(files=(("a.pdf", "application/pdf", b"%PDF z"),))
    real = messages.kind_of
    monkeypatch.setattr(messages, "kind_of", lambda *a: (_ for _ in ()).throw(RuntimeError("boom")))
    with pytest.raises(RuntimeError):
        store_message(db, store, mailbox(db), RawMessage("x", INBOX, raw))
    db.rollback()
    assert db.scalars(select(SourceMessage)).all() == []
    monkeypatch.setattr(messages, "kind_of", real)
    assert put(db, store, raw).result == STORED


def test_the_store_refuses_a_path_it_did_not_make(store: FileStore) -> None:
    for bad in ("../x", "/etc/passwd", "ab/cd", "aa/" + "g" * 64, "zz/" + "a" * 64):
        with pytest.raises(ValueError):
            store.read(bad)


def test_no_message_text_reaches_the_logs(db: Session, store: FileStore, caplog) -> None:  # noqa: ANN001
    caplog.set_level(logging.DEBUG)
    put(db, store, mail(subject="Secret subject 998877", body="Secret body 998877",
                        files=(("secret-998877.pdf", "application/pdf", b"%PDF 998877"),)))
    assert "998877" not in caplog.text


def test_every_golden_email_is_stored_and_the_originals_are_unchanged(db: Session, store: FileStore) -> None:
    paths = sorted(GOLDEN.glob("*/document.eml"))
    for path in paths:
        raw = path.read_bytes()
        put(db, store, raw)
        digest = hashlib.sha256(raw).hexdigest()
        row = db.scalars(select(SourceMessage).where(SourceMessage.content_hash == digest)).one()
        assert store.read(row.raw_path) == raw
    assert len(db.scalars(select(SourceMessage)).all()) == len({p.read_bytes() for p in paths})
    assert all(m.subject and m.sender for m in db.scalars(select(SourceMessage)))


def test_attachments_in_the_golden_set_come_through(db: Session, store: FileStore) -> None:
    """The golden PDFs, scans and spreadsheets, sent as attachments, are each stored once with the right kind."""
    kinds = {"document.pdf": ("application/pdf", "pdf"), "document.png": ("image/png", "image"),
             "document.xlsx": ("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "spreadsheet")}
    seen: dict[str, int] = {}
    for name, (content_type, kind) in kinds.items():
        for path in sorted(GOLDEN.glob(f"*/{name}"))[:5]:
            put(db, store, mail(subject=path.parent.name, files=((name, content_type, path.read_bytes()),)))
            seen[kind] = seen.get(kind, 0) + 1
    for kind, count in seen.items():
        assert len(db.scalars(select(SourceDocument).where(SourceDocument.kind == kind)).all()) == count


def test_the_original_is_hashed_after_storage_and_matches(db: Session, store: FileStore) -> None:
    put(db, store, mail(files=(("a.pdf", "application/pdf", b"%PDF q"),)))
    for doc in db.scalars(select(SourceDocument)):
        assert hashlib.sha256(store.read(doc.stored_path)).hexdigest() == doc.content_hash
    assert os.path.isdir(store.root)
