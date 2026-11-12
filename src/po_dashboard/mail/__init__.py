"""Mail sources (PD-3.1). Read-only by design (DEC-07)."""

from __future__ import annotations

from pathlib import Path

from po_dashboard.db.mail import Mailbox
from po_dashboard.mail.folder import FolderSource
from po_dashboard.mail.gmail import GmailSource
from po_dashboard.mail.gmail_auth import AccessTokens, Keychain, Secrets
from po_dashboard.mail.source import MailSource


def open_source(mailbox: Mailbox, secrets: Secrets | None = None) -> MailSource:
    """The reader for a mailbox row. Microsoft 365 comes at the switch (Phase 6)."""
    if mailbox.provider == "folder" and mailbox.location:
        return FolderSource(Path(mailbox.location))
    if mailbox.provider == "gmail":
        tokens = AccessTokens(mailbox.address, secrets or Keychain())
        return GmailSource(tokens.current, tokens.renew, since=mailbox.read_since)
    raise NotImplementedError(f"no reader for the {mailbox.provider} provider yet")
