"""The scanner (PD-3.4, ARCHITECTURE §6 steps 1 to 3): ask each connected mailbox for new mail, store it, read it.

- **No loss, no duplicate.** A batch of messages and the saved position that covers it are written in one transaction.
  A crash before the commit repeats the batch; the dedupe in intake (REQ-IN-05) absorbs the repeat.
- **One scan at a time.** A tick that arrives while a scan is running is skipped, not queued.
- **A cap per tick**, so a burst of mail is spread over several ticks and never starves the web pages.
- **Short write locks.** Network and OCR work happen outside any database transaction; each write transaction starts
  as `BEGIN IMMEDIATE`, so it waits for the lock (busy timeout) instead of failing on a read that turned into a write.
- **No mail text anywhere it should not be** (DEC-13): a failure is saved as a fixed reason and the error's type.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from po_dashboard.db.base import utcnow
from po_dashboard.db.mail import Mailbox, ScanState, SourceDocument
from po_dashboard.intake import reading
from po_dashboard.intake.messages import DUPLICATE, STORED, TOO_OLD, store_message
from po_dashboard.intake.store import FileStore
from po_dashboard.mail import open_source
from po_dashboard.mail.source import MailSource

log = logging.getLogger("po_dashboard.scanner")

BATCH = 50
MAX_MESSAGES_PER_SCAN = 200
MAX_READS_PER_SCAN = 25


@dataclass
class ScanReport:
    ran: bool = True  # false: another scan was already running
    stored: int = 0
    duplicates: int = 0
    too_old: int = 0
    skipped: int = 0
    read: int = 0
    not_read: int = 0
    failed: int = 0
    waiting: int = 0
    more: bool = False  # the cap stopped the scan: the next tick carries on
    errors: list[str] = field(default_factory=list)


def begin_immediate(db: Session) -> None:
    """Start this transaction as a write transaction. Call it first, before any statement."""
    db.connection().exec_driver_sql("BEGIN IMMEDIATE")


class Scanner:
    def __init__(
        self, engine: Engine, store: FileStore, *, open_mailbox: Callable[[Mailbox], MailSource] = open_source,
        batch: int = BATCH, max_messages: int = MAX_MESSAGES_PER_SCAN, max_reads: int = MAX_READS_PER_SCAN,
        now: Callable[[], datetime] = utcnow,
    ) -> None:  # fmt: skip
        self.engine, self.store, self.open_mailbox = engine, store, open_mailbox
        self.batch, self.max_messages, self.max_reads, self.now = batch, max_messages, max_reads, now
        self._running = threading.Lock()

    def scan(self) -> ScanReport:
        if not self._running.acquire(blocking=False):
            return ScanReport(ran=False)
        try:
            report = ScanReport()
            budget = self.max_messages
            with Session(self.engine) as db:
                mailboxes = [m.id for m in db.scalars(select(Mailbox).where(Mailbox.state == "connected"))]
            for mailbox_id in mailboxes:
                budget = self._scan_mailbox(mailbox_id, budget, report)
            self._read(report)
            return report
        finally:
            self._running.release()

    # ---- storing

    def _scan_mailbox(self, mailbox_id: int, budget: int, report: ScanReport) -> int:
        with Session(self.engine) as db:
            mailbox = db.get(Mailbox, mailbox_id)
            if mailbox is None:
                return budget
            db.expunge(mailbox)
        try:
            source = self.open_mailbox(mailbox)
            folders = source.folders()
        except Exception as problem:  # the mailbox could not be opened: say so, carry on with the others
            self._failed(mailbox_id, "", problem, report)
            return budget
        for folder in folders:
            if budget <= 0:
                report.more = True
                break
            try:
                budget = self._scan_folder(mailbox, source, folder, budget, report)
            except Exception as problem:
                self._failed(mailbox_id, folder, problem, report)
        return budget

    def _scan_folder(self, mailbox: Mailbox, source: MailSource, folder: str, budget: int, report: ScanReport) -> int:
        while budget > 0:
            with Session(self.engine) as db:
                state = db.scalar(
                    select(ScanState).where(ScanState.mailbox_id == mailbox.id, ScanState.folder == folder)
                )
                position = state.position if state else None
            fetched = source.fetch_new(folder, position, min(self.batch, budget))  # network or disk, no lock held
            with Session(self.engine) as db:
                begin_immediate(db)
                state = db.scalar(
                    select(ScanState).where(ScanState.mailbox_id == mailbox.id, ScanState.folder == folder)
                )
                if state is None:
                    state = ScanState(mailbox_id=mailbox.id, folder=folder)
                    db.add(state)
                for message in fetched.messages:
                    outcome = store_message(db, self.store, mailbox, message)
                    report.stored += outcome.result == STORED
                    report.duplicates += outcome.result == DUPLICATE
                    report.too_old += outcome.result == TOO_OLD
                report.skipped += len(fetched.skipped)
                state.position = fetched.position  # in the same transaction as the messages it covers
                state.last_attempt = state.last_success = self.now()
                state.last_error, state.failures = None, 0
                db.commit()
            budget -= max(len(fetched.messages) + len(fetched.skipped), 1)
            if not fetched.more:
                return budget
        report.more = True
        return budget

    def _failed(self, mailbox_id: int, folder: str, problem: Exception, report: ScanReport) -> None:
        reason = f"could not read the mailbox ({type(problem).__name__})"
        report.errors.append(reason)
        log.warning("scan failed for mailbox %s: %s", mailbox_id, type(problem).__name__)
        with Session(self.engine) as db:
            begin_immediate(db)
            state = db.scalar(select(ScanState).where(ScanState.mailbox_id == mailbox_id, ScanState.folder == folder))
            if state is None:
                state = ScanState(mailbox_id=mailbox_id, folder=folder)
                db.add(state)
            state.last_attempt, state.last_error, state.failures = self.now(), reason, (state.failures or 0) + 1
            db.commit()

    # ---- reading

    def _read(self, report: ScanReport) -> None:
        with Session(self.engine) as db:
            waiting = [
                (d.id, d.kind, d.filename, d.stored_path)
                for d in db.scalars(
                    select(SourceDocument).where(SourceDocument.state == reading.WAITING)
                    .order_by(SourceDocument.id).limit(self.max_reads + 1)
                )
            ]  # fmt: skip
        if len(waiting) > self.max_reads:
            report.more = True
        for doc_id, kind, filename, path in waiting[: self.max_reads]:
            result = reading.read_stored(self.store, kind, filename, path)  # may take minutes (OCR): no lock held
            with Session(self.engine) as db:
                begin_immediate(db)
                document = db.get(SourceDocument, doc_id)
                state = reading.apply(document, result) if document and document.state == reading.WAITING else None
                db.commit()
            if state == reading.READ:
                report.read += 1
            elif state == reading.NOT_READ:
                report.not_read += 1
            elif state == reading.FAILED:
                report.failed += 1
            else:
                report.waiting += 1


def last_scan(db: Session) -> datetime | None:
    """When the scanner last finished a folder without error: the health strip's 'last scan'."""
    return db.scalar(select(ScanState.last_success).order_by(ScanState.last_success.desc()).limit(1))
