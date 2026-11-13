"""The extraction work queue (DEC-54, ARCHITECTURE section 6.1). The `extraction` table is the queue: there is no
other queue software. A row is `queued` when the text step has read its document, `running` while the extractor holds
it, then `done` or `failed`.

Every write here is short and none holds a transaction across a model call. A row is claimed with one guarded UPDATE
and counts as the extractor's only if exactly one row changed, so two claimers can never hold the same row."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any, cast

from sqlalchemy import Engine, func, select, update
from sqlalchemy.orm import Session

from po_dashboard.auth.service import audit
from po_dashboard.db.base import utcnow
from po_dashboard.db.mail import Extraction, SourceDocument, SourceMessage
from po_dashboard.db.models import AppUser
from po_dashboard.extraction import prompts, schema
from po_dashboard.ledger.errors import LedgerError, check_seen, text

if TYPE_CHECKING:
    from sqlalchemy.engine import CursorResult

RETRY_SECONDS = (60, 300, 1800)  # after the 1st, 2nd and 3rd failed try; the 4th failure is final (DEC-54)
MAX_ATTEMPTS = len(RETRY_SECONDS) + 1


def enqueue(db: Session, document: SourceDocument, model: str) -> bool:
    """Queue a read document once. A document that already has an extraction, of any prompt version, is left alone:
    a new prompt never re-reads or replaces a finished one on its own (DEC-54). Does not commit."""
    if document.state != "read":
        return False
    taken = db.scalar(select(Extraction.id).where(Extraction.document_id == document.id).limit(1))
    if taken is not None:
        return False
    db.add(Extraction(
        document_id=document.id, model=model, prompt_version=prompts.PROMPT_VERSION,
        schema_version=schema.SCHEMA_VERSION, state="queued",
    ))  # fmt: skip
    db.flush()
    return True


def queue_missing(db: Session, model: str, limit: int = 500) -> int:
    """Queue every read document that has no extraction yet: documents read before the queue existed, or whose queueing
    a crash interrupted. Returns how many. Does not commit."""
    has_one = select(Extraction.id).where(Extraction.document_id == SourceDocument.id).exists()
    found = db.scalars(
        select(SourceDocument).where(SourceDocument.state == "read", ~has_one).order_by(SourceDocument.id).limit(limit)
    )
    return sum(enqueue(db, document, model) for document in list(found))


def recover_running(engine: Engine) -> int:
    """At start: a row left `running` by a crash goes back to `queued`. Its attempt is not counted against it."""
    with Session(engine) as db:
        db.connection().exec_driver_sql("BEGIN IMMEDIATE")
        changed = cast("CursorResult[Any]", db.execute(
            update(Extraction).where(Extraction.state == "running").values(
                state="queued", attempts=func.max(Extraction.attempts - 1, 0), started_at=None, next_attempt_at=None,
            )
        ))  # fmt: skip
        db.commit()
    return int(changed.rowcount or 0)


def claim(engine: Engine, now: datetime | None = None) -> int | None:
    """Take the oldest row that is due. One guarded UPDATE: the row is ours only if one row changed."""
    moment = now or utcnow()
    with Session(engine) as db:
        db.connection().exec_driver_sql("BEGIN IMMEDIATE")
        found = db.scalar(
            select(Extraction.id).where(
                Extraction.state == "queued",
                (Extraction.next_attempt_at.is_(None)) | (Extraction.next_attempt_at <= moment),
            ).order_by(Extraction.id).limit(1)
        )  # fmt: skip
        if found is None:
            db.rollback()
            return None
        changed = cast("CursorResult[Any]", db.execute(
            update(Extraction).where(Extraction.id == found, Extraction.state == "queued").values(
                state="running", attempts=Extraction.attempts + 1, started_at=moment, last_error=None,
                version=Extraction.version + 1,
            )
        ))  # fmt: skip
        db.commit()
    return found if changed.rowcount == 1 else None


def waiting_counts(db: Session) -> dict[str, int]:
    """For the health strip: how many documents are queued, running or failed (a failed one a person dismissed is not
    counted: it is dealt with)."""
    rows = db.execute(
        select(Extraction.state, func.count()).where(
            (Extraction.state != "failed") | Extraction.review_state.is_(None)
        ).group_by(Extraction.state)
    ).all()
    counts = {state: 0 for state in ("queued", "running", "failed")}
    counts.update({state: n for state, n in rows if state in counts})
    return counts


# ---- the Failed list (the dead-letter list): a person looks, then Retry or Dismiss


def failed_list(db: Session) -> list[tuple[Extraction, SourceDocument, SourceMessage]]:
    """Every failed document nobody has dismissed, newest first, with the message it came in."""
    return [
        (row, document, message)
        for row, document, message in db.execute(
            select(Extraction, SourceDocument, SourceMessage)
            .join(SourceDocument, SourceDocument.id == Extraction.document_id)
            .join(SourceMessage, SourceMessage.id == SourceDocument.message_id)
            .where(Extraction.state == "failed", Extraction.review_state.is_(None))
            .order_by(Extraction.id.desc())
        )
    ]


def _open_failure(db: Session, extraction_id: int, seen: int | None) -> Extraction:
    row = db.get(Extraction, extraction_id)
    if row is None or row.state != "failed" or row.review_state is not None:
        raise LedgerError("That document is not on the Failed list any more.")
    check_seen(row.version, seen, "This document")
    return row


def retry(db: Session, extraction_id: int, seen: int | None, actor: AppUser) -> None:
    """Put a failed document back in the queue with its tries reset. Does not commit."""
    row = _open_failure(db, extraction_id, seen)
    row.state, row.attempts, row.last_error = "queued", 0, None
    row.next_attempt_at = row.finished_at = None
    audit(db, "extraction.retry", actor, f"extraction:{row.id}", before={"error": row.last_error})


def dismiss(db: Session, extraction_id: int, seen: int | None, reason: str | None, actor: AppUser) -> None:
    """File a failed document as dealt with, with who and why. It stays failed, out of the list and the count. Does not
    commit."""
    note = text(reason, "Reason", longest=500, required=True)
    row = _open_failure(db, extraction_id, seen)
    row.review_state, row.reviewed_by, row.reviewed_at, row.review_note = "dismissed", actor.id, utcnow(), note
    audit(db, "extraction.dismiss", actor, f"extraction:{row.id}", after={"reason": note, "error": row.last_error})


def next_retry(attempts: int, now: datetime) -> datetime | None:
    """When to try again after the `attempts`-th failed try, or None if that was the last."""
    return now + timedelta(seconds=RETRY_SECONDS[attempts - 1]) if 1 <= attempts <= len(RETRY_SECONDS) else None


__all__ = ["MAX_ATTEMPTS", "RETRY_SECONDS", "claim", "dismiss", "enqueue", "failed_list", "next_retry", "queue_missing",
          "recover_running", "retry", "waiting_counts"]  # fmt: skip
