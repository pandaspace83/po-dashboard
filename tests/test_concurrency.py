"""Fixes from the engineering review (2026-11-10): what happens when two people, or two requests, collide."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from po_dashboard.auth import service
from po_dashboard.db import ledger as L
from po_dashboard.db.mail import Mailbox, SourceMessage
from po_dashboard.db.models import AppUser, AuditLog
from po_dashboard.ledger import closing, events
from po_dashboard.ledger.errors import LedgerError
from po_dashboard.ledger.status import LineStatus, line_states
from test_auth import PW, T0, admin, db, engine  # noqa: F401
from test_gate import env  # noqa: F401
from test_status import line_ids, new_po


@pytest.fixture
def session(env):  # noqa: ANN001, ANN201, F811
    with Session(env[1]) as s:
        yield s


def invoice(db: Session, order: L.PurchaseOrder, number: str) -> L.Invoice:
    line = line_ids(db, order)[0]
    entry = events.InvoiceLineInput(line, Decimal(1), Decimal(1))
    return events.record_invoice(db, order, [entry], invoice_number=number)


def test_the_database_itself_treats_inv_1_and_INV_1_as_one_invoice(session: Session) -> None:
    order = new_po(session)
    invoice(session, order, "INV-1")
    session.commit()
    session.add(L.Invoice(po_id=order.id, invoice_number="inv-1"))
    with pytest.raises(IntegrityError):
        session.flush()


def test_the_app_still_says_so_in_words(session: Session) -> None:
    order = new_po(session, lines=("100", "50"))
    invoice(session, order, "INV-1")
    with pytest.raises(LedgerError, match="already recorded"):
        invoice(session, order, "inv-1")


def test_a_clash_the_app_check_misses_is_a_message_not_a_crash(session: Session, monkeypatch) -> None:  # noqa: ANN001
    """The app's own check and the insert are two steps; if the check passes and the insert clashes, say so."""
    order = new_po(session)
    invoice(session, order, "INV-1")
    session.commit()
    real = session.scalars
    monkeypatch.setattr(
        session, "scalars", lambda *a, **k: iter([]) if "invoice_number" in str(a[0]) else real(*a, **k)
    )
    with pytest.raises(LedgerError, match="already recorded"):
        invoice(session, order, "Inv-1")
    # and the session is still usable afterwards
    monkeypatch.undo()
    assert session.scalars(select(L.Invoice)).all()


def test_the_second_of_two_people_closing_a_line_is_told_and_nothing_is_overwritten(
    session: Session, monkeypatch,  # noqa: ANN001
) -> None:
    order = new_po(session)
    (line,) = line_ids(session, order)
    boss = AppUser(email="a@example.test", password_hash="x", role="admin")
    other = AppUser(email="b@example.test", password_hash="x", role="buyer")
    session.add_all([boss, other])
    session.flush()
    stale = line_states(session, order.id)  # what the second person's page saw: the line open
    closing.close_lines(session, order, {line: "received"}, "Arrived", boss)
    session.commit()
    monkeypatch.setattr(closing, "line_states", lambda *a, **k: stale)  # their check ran before the first save
    with pytest.raises(LedgerError, match="just closed by someone else"):
        closing.close_lines(session, order, {line: "cancelled"}, "Mistake", other)
    row = session.get(L.POLine, line)
    session.refresh(row)
    assert row.closed_reason == "received" and row.closed_by == boss.id and row.close_note == "Arrived"
    assert len(session.scalars(select(AuditLog).where(AuditLog.action == "line.close")).all()) == 1
    assert stale[line].status is not LineStatus.CLOSED


def test_wrong_passwords_are_counted_by_the_database(db: Session, admin: AppUser) -> None:  # noqa: F811
    for _ in range(service.LOCKOUT_ATTEMPTS - 1):
        with pytest.raises(service.InvalidCredentials):
            service.sign_in(db, admin.email, "wrong password here", now=T0)
    assert db.get(AppUser, admin.id).failed_attempts == service.LOCKOUT_ATTEMPTS - 1
    with pytest.raises(service.InvalidCredentials):
        service.sign_in(db, admin.email, "wrong password here", now=T0)
    with pytest.raises(service.Locked):
        service.sign_in(db, admin.email, PW, now=T0 + timedelta(seconds=1))


def test_the_session_is_written_once_a_minute_and_idle_time_still_counts(db: Session, admin: AppUser) -> None:  # noqa: F811
    token = service.sign_in(db, admin.email, PW, now=T0).token
    row = service.resolve_session(db, token, now=T0 + timedelta(seconds=30))
    assert row is not None and row.last_seen_at == T0  # too soon: no write
    row = service.resolve_session(db, token, now=T0 + timedelta(seconds=61))
    assert row is not None and row.last_seen_at == T0 + timedelta(seconds=61)
    # 31 idle minutes after the last write is still a sign-out
    assert service.resolve_session(db, token, now=T0 + timedelta(seconds=61, minutes=31)) is None


def test_a_message_id_is_stored_once_per_mailbox(session: Session) -> None:
    box = Mailbox(address="p@example.test", provider="folder", location="/x")
    session.add(box)
    session.flush()
    from datetime import UTC, datetime

    def message(content_hash: str) -> SourceMessage:
        return SourceMessage(mailbox_id=box.id, folder="Inbox", provider_id="gmail-id-1", received_at=datetime.now(UTC),
                             raw_path="aa/" + "a" * 64, content_hash=content_hash, size=1)

    session.add(message("a" * 64))
    session.flush()
    session.add(message("b" * 64))
    with pytest.raises(IntegrityError):
        session.flush()
