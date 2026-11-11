"""The mail source interface (PD-3.1, ARCHITECTURE §7, REQ-IN-06). Every provider (a folder of .eml files, Gmail,
Microsoft 365) implements it, so the rest of the app cannot tell which is in use.

Mail is read-only (DEC-07, REQ-IN-02): the interface has no method that sends, moves, flags or deletes, and a test
keeps it that way. A message is handed over as its raw bytes, unchanged; reading it is intake's job (PD-3.2)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

INBOX = "Inbox"
SENT = "Sent Items"
FOLDERS = (INBOX, SENT)

MAX_MESSAGE_BYTES = 50 * 1024 * 1024


@dataclass(frozen=True)
class RawMessage:
    provider_id: str  # the provider's own id for it; for a folder, the SHA-256 of its bytes
    folder: str
    raw: bytes  # the whole message as received, unchanged


@dataclass(frozen=True)
class Skipped:
    """A message the provider could not hand over. The reason never holds message text."""

    provider_id: str
    folder: str
    reason: str


@dataclass(frozen=True)
class FetchResult:
    messages: list[RawMessage]
    position: str  # opaque: save it, pass it back. It moves only past messages returned or skipped
    skipped: list[Skipped] = field(default_factory=list)
    more: bool = False  # true if the limit stopped the batch and another call would return more


@dataclass(frozen=True)
class Health:
    ok: bool
    detail: str  # short and safe to show on the Mailboxes screen


class MailSource(Protocol):
    """Read-only. Nothing here can change a mailbox."""

    def folders(self) -> tuple[str, ...]:
        """The folders to scan: the Inbox and Sent Items."""
        ...

    def fetch_new(self, folder: str, position: str | None, limit: int = 50) -> FetchResult:
        """Messages in `folder` that are new since `position` (None: from the start), at most `limit`. A position the
        provider no longer understands is treated as None: the caller's dedupe (REQ-IN-05) absorbs the repeats."""
        ...

    def health(self) -> Health:
        """Can the source be read right now? Never raises."""
        ...
