"""Store one message and its attachments (PD-3.2, ARCHITECTURE §6 step 2). The original goes to the file store first,
then the rows are written in one transaction. A crash between the two leaves a stored file nobody refers to, which
does no harm, and the scanner repeats the message because its position has not moved (PD-3.4).

Nothing here puts message text in a log or an error (DEC-13): only ids, counts and reasons."""

from __future__ import annotations

import email
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from email import policy
from email.message import EmailMessage, Message
from email.utils import parsedate_to_datetime
from pathlib import PurePosixPath

from sqlalchemy import select
from sqlalchemy.orm import Session

from po_dashboard.db.mail import Mailbox, SourceDocument, SourceMessage
from po_dashboard.intake.store import FileStore
from po_dashboard.mail.source import RawMessage

MAX_FILE_BYTES = 20 * 1024 * 1024
MAX_ATTACHMENTS = 25

STORED, DUPLICATE, TOO_OLD = "stored", "duplicate", "too_old"

EXTENSIONS = {
    "pdf": {"pdf"}, "image": {"png", "jpg", "jpeg", "tif", "tiff", "gif", "bmp", "heic", "webp"},
    "spreadsheet": {"xlsx", "xlsm", "csv", "tsv"}, "word": {"docx"}, "text": {"txt"}, "email": {"eml"},
}
TYPES = {
    "application/pdf": "pdf", "text/csv": "spreadsheet", "text/plain": "text", "message/rfc822": "email",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "spreadsheet",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "word",
}
UNREADABLE = {"xlsm"}  # carries macros: stored, never opened as a spreadsheet


@dataclass(frozen=True)
class Outcome:
    result: str  # stored, duplicate or too_old
    message_id: int | None = None
    documents: int = 0  # documents written, the body included
    duplicate_attachments: int = 0
    not_read: int = 0


def clean(value: object, limit: int) -> str | None:
    """A header value or file name made safe to keep and show: no control characters, no line breaks, cut to size."""
    if value is None:
        return None
    text = re.sub(r"[\x00-\x1f\x7f  ‪-‮]", " ", str(value)).strip()
    return text[:limit] or None


def display_name(raw: str | None, n: int) -> str:
    """A file name for display only. Directory parts are dropped; it never becomes a path."""
    name = clean(raw, 400) or ""
    name = PurePosixPath(name.replace("\\", "/")).name
    return (name or f"attachment {n}")[:255]


def kind_of(filename: str, content_type: str) -> str:
    extension = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    for kind, extensions in EXTENSIONS.items():
        if extension in extensions:
            return kind
    if content_type in TYPES:
        return TYPES[content_type]
    return "image" if content_type.startswith("image/") else "other"


def parse(raw: bytes) -> Message:
    try:
        return email.message_from_bytes(raw, policy=policy.default)
    except Exception:  # a hostile message must never stop intake; it is still stored
        return EmailMessage()


def header(msg: Message, name: str) -> str | None:
    try:
        return clean(msg[name], 1000)
    except Exception:
        return None


def received_at(msg: Message) -> datetime:
    try:
        value = parsedate_to_datetime(str(msg["Date"]))
        return value.astimezone(UTC) if value.tzinfo else value.replace(tzinfo=UTC)
    except (TypeError, ValueError, IndexError, AttributeError, OverflowError):
        return datetime.now(UTC)


def part_file(part: Message) -> tuple[str, str, bytes] | None:
    """(file name, content type, bytes) if the part is a file; None for the text or HTML body and for anything odd."""
    try:
        filename = part.get_filename() or ""
        disposition = part.get_content_disposition()
        content_type = part.get_content_type()
        if content_type == "message/rfc822":  # a forwarded message: its payload is the inner message
            inner = part.get_payload()
            if isinstance(inner, list) and inner and isinstance(inner[0], Message):
                return filename or "forwarded.eml", content_type, inner[0].as_bytes()
            data = part.get_payload(decode=True)  # a forwarded message that was sent encoded
            return (filename or "forwarded.eml", content_type, data) if isinstance(data, bytes) else None
        if content_type in ("text/plain", "text/html") and disposition != "attachment" and not filename:
            return None
        data = part.get_payload(decode=True)
        if isinstance(data, bytes) and (filename or disposition == "attachment"):
            return filename, content_type, data
    except Exception:  # a hostile part is left out; the message is still stored
        return None
    return None


def attachments(msg: Message) -> list[tuple[str, str, bytes]]:
    """Every part of the message that is a file."""
    try:
        parts = [p for p in msg.walk() if not p.is_multipart() or p.get_content_type() == "message/rfc822"]
    except Exception:
        return []
    return [found for found in (part_file(p) for p in parts) if found is not None]


def store_message(db: Session, store: FileStore, mailbox: Mailbox, message: RawMessage) -> Outcome:
    """Store one message. Does not commit: the caller commits, so a batch is one transaction or many as it chooses."""
    digest = store.digest(message.raw)
    if db.scalar(select(SourceMessage.id).where(SourceMessage.content_hash == digest)) is not None:
        return Outcome(DUPLICATE)
    msg = parse(message.raw)
    when = received_at(msg)
    if mailbox.read_since is not None and when < mailbox.read_since:
        return Outcome(TOO_OLD)
    _, raw_path = store.put(message.raw)
    recipients = ", ".join(v for v in (header(msg, "To"), header(msg, "Cc")) if v)
    row = SourceMessage(
        mailbox_id=mailbox.id, folder=message.folder, provider_id=message.provider_id[:200],
        sender=clean(header(msg, "From"), 500), recipients=recipients[:4000] or None, subject=header(msg, "Subject"),
        received_at=when, message_id_header=clean(header(msg, "Message-ID"), 500), raw_path=raw_path,
        content_hash=digest, size=len(message.raw),
    )
    db.add(row)
    db.flush()
    db.add(SourceDocument(
        message_id=row.id, filename="Message body", kind="body", content_type="message/rfc822",
        stored_path=raw_path, content_hash=digest, size=len(message.raw), state="stored",
    ))
    written, duplicates, unread = 1, 0, 0
    for n, (name, content_type, data) in enumerate(attachments(msg), 1):
        file_digest = store.digest(data)
        if db.scalar(select(SourceDocument.id).where(
            SourceDocument.content_hash == file_digest, SourceDocument.kind != "body"
        )) is not None:
            duplicates += 1
            continue
        shown = display_name(name, n)
        kind = kind_of(shown, content_type)
        _, path = store.put(data)
        state, reason = "stored", None
        extension = shown.rsplit(".", 1)[-1].lower() if "." in shown else ""
        if n > MAX_ATTACHMENTS:
            state, reason = "not_read", f"more than {MAX_ATTACHMENTS} attachments in the message"
        elif len(data) > MAX_FILE_BYTES:
            state, reason = "not_read", "larger than the 20 MB limit"
        elif kind == "other" or extension in UNREADABLE:
            state, reason = "not_read", "a kind of file the app does not read"
        if state == "not_read":
            unread += 1
        db.add(SourceDocument(
            message_id=row.id, filename=shown, kind=kind, content_type=clean(content_type, 200),
            stored_path=path, content_hash=file_digest, size=len(data), state=state, not_read_reason=reason,
        ))
        db.flush()
        written += 1
    return Outcome(STORED, row.id, written, duplicates, unread)
