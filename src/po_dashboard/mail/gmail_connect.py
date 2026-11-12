"""Connecting and disconnecting the Gmail mailbox (PD-3.5; the Mailboxes screen of PD-3.7 calls the same functions).

The person signs in on Google's page; this saves the refresh token in the Keychain and the mailbox in the database.
Nothing secret is returned, printed or logged."""

from __future__ import annotations

from collections.abc import Callable
from datetime import timedelta
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from po_dashboard.db.base import utcnow
from po_dashboard.db.mail import Mailbox, ScanState
from po_dashboard.mail import gmail_auth
from po_dashboard.mail.gmail import GmailSource
from po_dashboard.mail.gmail_auth import Client, GmailError, Secrets

SignIn = Callable[[Client], tuple[dict[str, Any], str]]


def save_client(secrets: Secrets, client_id: str, client_secret: str) -> None:
    client_id, client_secret = client_id.strip(), client_secret.strip()
    if not client_id.endswith(".apps.googleusercontent.com") or len(client_secret) < 10:
        raise GmailError("That does not look like a Google OAuth client id and secret.")
    secrets.put(gmail_auth.CLIENT_ID_KEY, client_id)
    secrets.put(gmail_auth.CLIENT_SECRET_KEY, client_secret)


def connect(
    db: Session, secrets: Secrets, *, days: int = 0, connected_by: int | None = None,
    sign_in: SignIn | None = None, http: httpx.Client | None = None,
) -> Mailbox:  # fmt: skip
    """Sign in (in Terminal: the loopback listener), then save the token and the mailbox. `days` is how many days of
    older mail to read as well (DEC-51)."""
    check_days(days)
    client = gmail_auth.load_client(secrets)
    tokens, _ = (sign_in or (lambda c: gmail_auth.sign_in(c, http=http)))(client)
    return finish_connect(db, secrets, tokens, days=days, connected_by=connected_by, http=http)


def check_days(days: int) -> None:
    if not 0 <= days <= 365:
        raise GmailError("The number of days to read back must be between 0 and 365.")


def finish_connect(
    db: Session, secrets: Secrets, tokens: dict[str, Any], *, days: int = 0, connected_by: int | None = None,
    http: httpx.Client | None = None, one_mailbox: bool = False,
) -> Mailbox:  # fmt: skip
    """Save a finished sign-in: the token in the Keychain (only after Google has said whose it is) and the mailbox in
    the database. The Mailboxes screen calls this too (PD-3.7), with `one_mailbox` set: version 1 reads one mailbox
    (DEC-33), so a different Gmail account is refused while another is connected."""
    check_days(days)
    refresh, access = tokens.get("refresh_token"), str(tokens["access_token"])
    if not refresh:
        raise GmailError("Google sent no long-lived sign-in. Try again and allow access when asked.")

    def refused() -> str:
        raise gmail_auth.NeedsReconnect("The sign-in was refused.")

    address = GmailSource(lambda: access, refused, http=http).account()
    others = select(Mailbox.id).where(
        Mailbox.provider == "gmail", Mailbox.state != "disconnected", Mailbox.address != address
    )
    if one_mailbox and db.scalar(others) is not None:
        raise GmailError("A different mailbox is already connected. Disconnect it first.")
    secrets.put(gmail_auth.token_key(address), str(refresh))
    now = utcnow()
    mailbox = db.scalar(select(Mailbox).where(Mailbox.address == address))
    if mailbox is None:
        mailbox = Mailbox(address=address, provider="gmail", read_since=now - timedelta(days=days))
        db.add(mailbox)
    else:
        wider = now - timedelta(days=days)
        if days and (mailbox.read_since is None or wider < mailbox.read_since):
            # Reading further back: forget the saved positions so the next scan lists the wider window. Mail already
            # stored is recognised and skipped, so nothing is read twice.
            mailbox.read_since = wider
            for state in db.scalars(select(ScanState).where(ScanState.mailbox_id == mailbox.id)):
                state.position = None
    mailbox.provider, mailbox.state = "gmail", "connected"
    mailbox.connected_at, mailbox.connected_by = now, connected_by
    db.commit()
    return mailbox


def disconnect(db: Session, secrets: Secrets, address: str) -> None:
    """Forget the saved sign-in and stop scanning. The mail already stored stays."""
    mailbox = db.scalar(select(Mailbox).where(Mailbox.address == address.strip().lower(), Mailbox.provider == "gmail"))
    if mailbox is None:
        raise GmailError("No Gmail mailbox with that address is connected.")
    secrets.forget(gmail_auth.token_key(mailbox.address))
    mailbox.state = "disconnected"
    db.commit()
