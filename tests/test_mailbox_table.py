"""The mailbox table (PD-3.1): the rules the database keeps itself."""

from __future__ import annotations

import pytest
from sqlalchemy import delete
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from po_dashboard.db.mail import Mailbox
from po_dashboard.db.models import AppUser
from test_database import add_user  # noqa: F401
from test_gate import env  # noqa: F401


def box(**more: object) -> Mailbox:
    base: dict[str, object] = {"address": "purchasing@example-buyer.test", "provider": "folder", "state": "connected"}
    return Mailbox(**{**base, **more})


def test_a_mailbox_is_stored_and_read_back(env) -> None:  # noqa: ANN001
    with Session(env[1]) as db:
        db.add(box(location="/mail/box"))
        db.commit()
        row = db.query(Mailbox).one()
        assert row.provider == "folder" and row.state == "connected" and row.read_since is None


@pytest.mark.parametrize("change", [
    {"provider": "pop3"}, {"state": "paused"}, {"address": "Purchasing@Example.test"},
])
def test_unknown_providers_and_states_and_upper_case_addresses_are_refused(env, change) -> None:  # noqa: ANN001
    with Session(env[1]) as db:
        db.add(box(**change))
        with pytest.raises(IntegrityError):
            db.commit()


def test_one_row_per_address(env) -> None:  # noqa: ANN001
    with Session(env[1]) as db:
        db.add(box())
        db.commit()
        db.add(box(provider="gmail"))
        with pytest.raises(IntegrityError):
            db.commit()


def test_removing_the_person_who_connected_it_keeps_the_mailbox(env) -> None:  # noqa: ANN001
    with Session(env[1]) as db:
        user = AppUser(email="a@example.test", password_hash="x", role="admin")
        db.add(user)
        db.flush()
        db.add(box(connected_by=user.id))
        db.commit()
        db.execute(delete(AppUser).where(AppUser.id == user.id))
        db.commit()
        assert db.query(Mailbox).one().connected_by is None
