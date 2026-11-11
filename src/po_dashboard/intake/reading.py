"""Read the stored documents (PD-3.3): the text step between storing and extracting (ARCHITECTURE §6 step 3).

A document in state `stored` is read once: its text goes to the file store, and it becomes `read`; a file that will not
be read (password, too many pages, too large) becomes `not_read` with a fixed reason; a file that fails to open becomes
`failed` with the kind of error only (never document text, DEC-13). If a program the reader needs is missing the
document stays `stored`, so it is read later, not lost. Nothing here commits: the scanner (PD-3.4) does.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from po_dashboard.db.mail import SourceDocument
from po_dashboard.intake.store import FileStore
from po_dashboard.intake.text import NotReadable, ReaderUnavailable, extract_text

READ, NOT_READ, FAILED, WAITING = "read", "not_read", "failed", "stored"


@dataclass(frozen=True)
class Result:
    state: str
    text_path: str | None = None
    pages: int | None = None
    reason: str | None = None


def read_stored(store: FileStore, kind: str, filename: str, stored_path: str) -> Result:
    """Read one file and store its text. Touches no database, so it can take as long as OCR needs without holding a
    lock; `apply` then records the result in a moment."""
    try:
        found = extract_text(kind, filename, store.read(stored_path))
    except ReaderUnavailable:
        return Result(WAITING)
    except NotReadable as why:
        return Result(NOT_READ, reason=str(why)[:200])
    except Exception as problem:  # a hostile or broken file: the kind of error is kept, never its contents
        return Result(FAILED, reason=f"could not be opened ({type(problem).__name__})"[:200])
    _, path = store.put(found.text.encode("utf-8"))
    return Result(READ, text_path=path, pages=found.pages)


def apply(document: SourceDocument, result: Result) -> str:
    if result.state == WAITING:
        return WAITING
    document.state, document.not_read_reason = result.state, result.reason
    if result.state == READ:
        document.text_path, document.pages = result.text_path, result.pages
    return result.state


def read_document(db: Session, store: FileStore, document: SourceDocument) -> str:
    """Read one document and record the result. Returns its new state."""
    if document.state != WAITING:
        return document.state
    return apply(document, read_stored(store, document.kind, document.filename, document.stored_path))


def read_pending(db: Session, store: FileStore, limit: int = 50) -> dict[str, int]:
    """Read up to `limit` waiting documents, oldest first. Returns how many ended in each state."""
    counts = {READ: 0, NOT_READ: 0, FAILED: 0, WAITING: 0}
    waiting = db.scalars(
        select(SourceDocument).where(SourceDocument.state == WAITING).order_by(SourceDocument.id).limit(limit)
    )
    for document in list(waiting):
        counts[read_document(db, store, document)] += 1
    db.flush()
    return counts
