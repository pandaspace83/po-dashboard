"""Mail sources (PD-3.1). Read-only by design (DEC-07)."""

from __future__ import annotations

from pathlib import Path

from po_dashboard.db.mail import Mailbox
from po_dashboard.mail.folder import FolderSource
from po_dashboard.mail.source import MailSource


def open_source(mailbox: Mailbox) -> MailSource:
    """The reader for a mailbox row. Gmail comes in PD-3.5 and Microsoft 365 at the switch (Phase 6)."""
    if mailbox.provider == "folder" and mailbox.location:
        return FolderSource(Path(mailbox.location))
    raise NotImplementedError(f"no reader for the {mailbox.provider} provider yet")
