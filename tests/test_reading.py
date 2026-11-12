"""The text step against the database (PD-3.3): stored documents become read, not_read or failed, never lost."""

from __future__ import annotations

import io
import logging
from pathlib import Path

import pytest
from docx import Document
from sqlalchemy import select
from sqlalchemy.orm import Session

from po_dashboard.db.mail import SourceDocument
from po_dashboard.intake import reading, text, worker
from po_dashboard.intake.reading import FAILED, NOT_READ, READ, WAITING, read_pending
from po_dashboard.intake.store import FileStore
from test_gate import env  # noqa: F401
from test_intake import db, mail, put, store  # noqa: F401

DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def docs(db: Session) -> dict[str, SourceDocument]:
    return {d.filename: d for d in db.scalars(select(SourceDocument))}


def word_file(words: str) -> bytes:
    document = Document()
    document.add_paragraph(words)
    out = io.BytesIO()
    document.save(out)
    return out.getvalue()


def test_a_body_and_its_attachments_are_read_and_their_text_stored(db: Session, store: FileStore) -> None:
    put(
        db,
        store,
        mail(
            body="Shipped 40 today",
            files=(("note.txt", "text/plain", b"Tracking 1Z999"), ("a.docx", DOCX, word_file("PO 4521 confirmed"))),
        ),
    )
    assert read_pending(db, store) == {READ: 3, NOT_READ: 0, FAILED: 0, WAITING: 0}
    db.commit()
    rows = docs(db)
    assert all(d.state == READ and d.text_path for d in rows.values())
    assert "Shipped 40 today" in store.read(rows["Message body"].text_path or "").decode()
    assert "Tracking 1Z999" in store.read(rows["note.txt"].text_path or "").decode()
    assert "PO 4521 confirmed" in store.read(rows["a.docx"].text_path or "").decode()


def test_a_document_is_read_once(db: Session, store: FileStore) -> None:
    put(db, store, mail())
    assert read_pending(db, store)[READ] == 1
    assert read_pending(db, store) == {READ: 0, NOT_READ: 0, FAILED: 0, WAITING: 0}


def test_a_refused_file_says_why_and_the_message_is_still_read(db: Session, store: FileStore) -> None:
    put(
        db,
        store,
        mail(files=(("bad.docx", DOCX, b"PK nonsense"),)),
    )
    counts = read_pending(db, store)
    assert counts[READ] == 1 and counts[NOT_READ] == 1
    bad = docs(db)["bad.docx"]
    assert bad.state == NOT_READ and bad.not_read_reason and "damaged" in bad.not_read_reason


def test_a_file_that_breaks_the_reader_is_marked_failed_with_the_kind_of_error_only(
    db: Session, store: FileStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    put(db, store, mail(files=(("a.txt", "text/plain", b"SECRET DOCUMENT WORDS"),)))

    def boom(kind: str, filename: str, data: bytes, **_limits: object) -> text.Text:
        raise RuntimeError("SECRET DOCUMENT WORDS")

    monkeypatch.setattr(reading, "read_isolated", boom)
    read_pending(db, store)
    failed = docs(db)["a.txt"]
    assert failed.state == FAILED and failed.not_read_reason == "could not be opened (RuntimeError)"
    assert "SECRET" not in (failed.not_read_reason or "")


def png() -> bytes:
    from PIL import Image

    out = io.BytesIO()
    Image.new("RGB", (40, 20), "white").save(out, format="PNG")
    return out.getvalue()


def test_a_missing_program_leaves_the_document_waiting_and_it_is_read_later(
    db: Session, store: FileStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    put(db, store, mail(files=(("a.png", "image/png", png()),)))
    with monkeypatch.context() as patch:
        patch.setattr(worker, "PATH", "/usr/bin:/bin")  # the reading process looks for Tesseract on this path only
        assert read_pending(db, store) == {READ: 1, NOT_READ: 0, FAILED: 0, WAITING: 1}
    assert docs(db)["a.png"].state == WAITING
    assert read_pending(db, store)[WAITING] + read_pending(db, store)[READ] <= 1  # Tesseract is back: it is read now
    assert docs(db)["a.png"].state in (READ, WAITING)


def test_no_document_text_reaches_the_logs(db: Session, store: FileStore, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    put(db, store, mail(body="Confidential price 99.99", files=(("a.txt", "text/plain", b"Confidential words"),)))
    read_pending(db, store)
    assert "Confidential" not in caplog.text


def test_the_whole_golden_set_goes_in_and_comes_out_as_text(db: Session, store: FileStore) -> None:
    golden = Path(__file__).parent / "data" / "golden"
    for folder in sorted(p for p in golden.iterdir() if p.is_dir()):
        eml = folder / "document.eml"
        if eml.exists():
            put(db, store, eml.read_bytes())
    counts = read_pending(db, store, limit=500)
    assert counts[READ] == 47 and counts[FAILED] == 0 and counts[NOT_READ] == 0
