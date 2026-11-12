"""The extractor: reads one queued document at a time with the model (DEC-54, ARCHITECTURE section 6.1).

A model call is never inside a transaction. The row is claimed, the text is read and the call is made with no lock
held, and the answer is stored in one short write. What happens to a failure:

- the model server cannot be reached: the row goes back to `queued` and the try is not counted; documents wait;
- a timeout or a server error: tried again after 1, 5 and 30 minutes, then `failed`;
- a reply that cannot be used (not the schema, or cut off): `failed` at once, because the same text gives the same one.

`last_error` is fixed words, never document text (DEC-13)."""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any, cast

from sqlalchemy import Engine, update
from sqlalchemy.orm import Session

from po_dashboard.config import Settings
from po_dashboard.db.base import utcnow
from po_dashboard.db.mail import Extraction, SourceDocument
from po_dashboard.extraction import queue, schema, window
from po_dashboard.intake.store import FileStore
from po_dashboard.models import llm

if TYPE_CHECKING:
    from sqlalchemy.engine import CursorResult

log = logging.getLogger("po_dashboard.extractor")

# What the Failed list shows. Fixed words: nothing here can carry document text.
NO_TEXT = "the document has no text to read"
BAD_ANSWER = "the model's answer could not be used"
NO_ANSWER = "the model server gave no answer"
GAVE_UP = f"{NO_ANSWER}, even after {queue.MAX_ATTEMPTS} tries"


@dataclass(frozen=True)
class Outcome:
    kind: str  # idle, done, retry, failed, model_down
    extraction_id: int | None = None


class Extractor:
    def __init__(
        self, engine: Engine, store: FileStore, settings: Settings, *,
        extract: Callable[..., llm.Extraction], now: Callable[[], datetime] = utcnow,
    ) -> None:  # fmt: skip
        """`extract` is the model call, always passed in: the app passes `llm.extract`, and no test can reach a real
        model by forgetting to."""
        self.engine, self.store, self.settings, self.extract, self.now = engine, store, settings, extract, now

    def recover(self) -> int:
        """At start: a row a crash left `running` goes back to `queued`."""
        return queue.recover_running(self.engine)

    def run_once(self) -> Outcome:
        """Claim the oldest due row and read it. Returns what happened; `idle` means nothing was due."""
        found = queue.claim(self.engine, self.now())
        if found is None:
            return Outcome("idle")
        with Session(self.engine) as db:
            row = db.get(Extraction, found)
            document = db.get(SourceDocument, row.document_id) if row else None
            if row is None or document is None or not document.text_path:
                return self._settle(found, "failed", NO_TEXT)
            model, attempts, text_path = row.model, row.attempts, document.text_path
        try:
            fitted = window.fit(self.store.read(text_path).decode("utf-8", errors="replace"))
        except OSError:
            return self._settle(found, "failed", NO_TEXT)
        try:
            result = self.extract(self.settings, fitted.text, model=model)  # no lock, no transaction (may take minutes)
        except llm.ModelUnavailable:
            return self._settle(found, "queued", None, count_try=False, kind="model_down")
        except llm.BadAnswer:
            return self._settle(found, "failed", BAD_ANSWER)
        except llm.ModelError:
            again = queue.next_retry(attempts, self.now())
            if again is None:
                return self._settle(found, "failed", GAVE_UP)
            return self._settle(found, "queued", NO_ANSWER, retry_at=again, kind="retry")
        except Exception as problem:  # a bug in the call, not in the document: fail it once and say what kind
            log.warning("extraction %s: %s", found, type(problem).__name__)
            return self._settle(found, "failed", f"could not be read ({type(problem).__name__})"[:200])
        return self._store(found, result, fitted)

    # ---- writing the result: one short transaction each

    def _store(self, found: int, result: llm.Extraction, fitted: window.Windowed) -> Outcome:
        answer = result.answer
        doc_type, po_number = answer.get("doc_type"), answer.get("po_number")
        flags = [{"flag": "read_in_part", "level": "check"}] if fitted.read_in_part else []
        values: dict[str, Any] = dict(
            state="done", answer=result.raw, doc_type=doc_type if doc_type in schema.DOC_TYPES else None,
            po_number=str(po_number)[:100] if isinstance(po_number, str) and po_number.strip() else None,
            seconds=result.seconds, prompt_tokens=result.prompt_tokens, finished_at=self.now(), next_attempt_at=None,
            last_error=None, flags=json.dumps(flags) if flags else None, review_state=None,
        )  # fmt: skip
        return self._write(found, values, Outcome("done", found))

    def _settle(
        self, found: int, state: str, error: str | None, *, count_try: bool = True,
        retry_at: datetime | None = None, kind: str | None = None,
    ) -> Outcome:  # fmt: skip
        values: dict[str, Any] = dict(state=state, last_error=error, next_attempt_at=retry_at)
        if state == "failed":
            values["finished_at"] = self.now()
        if not count_try:
            values["attempts"] = Extraction.attempts - 1
        outcome = Outcome(kind or ("failed" if state == "failed" else "retry"), found)
        return self._write(found, values, outcome)

    def _write(self, found: int, values: dict[str, Any], outcome: Outcome) -> Outcome:
        with Session(self.engine) as db:
            db.connection().exec_driver_sql("BEGIN IMMEDIATE")
            changed = cast("CursorResult[Any]", db.execute(
                update(Extraction).where(Extraction.id == found, Extraction.state == "running").values(
                    **values, version=Extraction.version + 1
                )
            ))  # only a row this extractor still holds
            db.commit()
        if changed.rowcount != 1:  # a person dismissed or retried it meanwhile: leave their change alone
            return Outcome("idle", found)
        return outcome
