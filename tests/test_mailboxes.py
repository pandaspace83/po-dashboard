"""The Mailboxes screen (PD-3.7, REQ-IN-07): connect, reconnect, disconnect, with a pretend Google."""

from __future__ import annotations

import logging
import re
import time
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from po_dashboard.auth import service
from po_dashboard.config import Settings
from po_dashboard.db.mail import Mailbox, ScanState
from po_dashboard.db.migrate import upgrade
from po_dashboard.db.models import AuditLog
from po_dashboard.mail import gmail_auth, gmail_connect
from po_dashboard.pages import mailboxes
from po_dashboard.web import create_app
from test_gate import ORIGIN, PW, env, new_client, post, sign_in  # noqa: F401
from test_gmail import ACCESS, CLIENT, CLIENT_SECRET, REFRESH, FakeGmail, Memory

ADDRESS = "test.account@gmail.com"  # what the pretend Google says the signed-in account is


@pytest.fixture(autouse=True)
def nothing_pending() -> None:
    mailboxes._pending.clear()
    mailboxes._flash.clear()


class Setup:
    def __init__(self, env) -> None:  # noqa: ANN001
        self.app, self.engine, self.settings = env
        self.keys, self.google = Memory(), FakeGmail()
        self.keys.put(gmail_auth.CLIENT_ID_KEY, CLIENT.client_id)
        self.keys.put(gmail_auth.CLIENT_SECRET_KEY, CLIENT.client_secret)
        self.app.state.secrets = self.keys
        self.app.state.google_http = httpx.Client(transport=self.google.transport())

    def admin(self) -> TestClient:
        client = new_client(self.app)
        assert sign_in(client, "boss@example.com").status_code == 303
        return client

    def boxes(self) -> list[Mailbox]:
        with Session(self.engine) as db:
            return list(db.scalars(select(Mailbox)))

    def audited(self) -> list[str]:
        with Session(self.engine) as db:
            return [a.action for a in db.scalars(select(AuditLog)) if a.action.startswith("mailbox.")]


@pytest.fixture
def setup(env) -> Setup:  # noqa: ANN001
    return Setup(env)


def start(admin: TestClient, days: str = "0") -> dict[str, str]:
    """Press Connect and read Google's address from the Continue page, as the browser would."""
    response = post(admin, "/mailboxes/connect", {"days": days})
    assert response.status_code == 303 and response.headers["location"] == "/mailboxes/continue", response.text
    page = admin.get("/mailboxes/continue")
    link = re.search(r'href="(https://accounts\.google\.com[^"]+)"', page.text)
    assert link, page.text
    return {k: v[0] for k, v in parse_qs(urlparse(link.group(1).replace("&amp;", "&")).query).items()}


def come_back(app, query: dict[str, str], code: str = "ONE-TIME-CODE", **more: str):  # noqa: ANN001, ANN201
    """Google sends the browser back with no cookie at all (the session cookie is SameSite=Strict)."""
    parts = {"state": query["state"], "code": code, **more}
    path = urlparse(query["redirect_uri"]).path
    return new_client(app).get(path, params=parts)


def connect_fully(s: Setup, admin: TestClient, days: str = "0") -> dict[str, str]:
    query = start(admin, days)
    assert come_back(s.app, query).status_code == 200
    done = admin.get("/mailboxes/finish")
    assert done.status_code == 303 and done.headers["location"] == "/mailboxes"
    return query


# ---- who may use it


def test_only_an_admin_sees_the_screen_and_its_link(setup: Setup, env) -> None:  # noqa: ANN001
    admin = setup.admin()
    page = admin.get("/mailboxes")
    assert page.status_code == 200 and 'href="/mailboxes"' in page.text and "No mailbox is connected yet." in page.text
    buyer = new_client(setup.app)
    assert sign_in(buyer, "buyer@example.com").status_code == 303
    assert buyer.get("/mailboxes").status_code == 403 and 'href="/mailboxes"' not in buyer.get("/").text
    for path in ("/mailboxes/continue", "/mailboxes/finish"):
        assert buyer.get(path).status_code == 403
    assert post(buyer, "/mailboxes/connect", {"days": "0"}).status_code == 403
    assert post(buyer, "/mailboxes/1/disconnect").status_code == 403
    assert new_client(setup.app).get("/mailboxes", headers={"Accept": "text/html"}).status_code == 303


def test_the_page_says_only_what_is_true(setup: Setup) -> None:
    text = setup.admin().get("/mailboxes").text
    for words in (
        "sign in on Google&rsquo;s own page, so PO Dashboard never sees your password",
        "read-only access: it cannot send, move or delete your mail",
        "stored on the office Mac, and everyone signed in to PO Dashboard can see them",
        "You can disconnect it at any time",
    ):
        assert words in text
    assert "Connect Gmail" in text and 'name="days"' in text


# ---- connecting


def test_connecting_goes_to_google_and_back_and_saves_the_mailbox(
    setup: Setup, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    admin = setup.admin()
    query = connect_fully(setup, admin, days="3")
    assert query["scope"] == gmail_auth.SCOPE and query["code_challenge_method"] == "S256"
    assert query["client_id"] == CLIENT.client_id and query["redirect_uri"] == "http://127.0.0.1:8000/mailboxes/callback"
    exchange = next(r for r in setup.google.requests if r.url.host == "oauth2.googleapis.com")
    body = parse_qs(exchange.content.decode())
    assert body["code"] == ["ONE-TIME-CODE"] and body["redirect_uri"] == [query["redirect_uri"]]
    assert gmail_auth.challenge_of(body["code_verifier"][0]) == query["code_challenge"]
    (box,) = setup.boxes()
    assert box.address == ADDRESS and box.provider == "gmail" and box.state == "connected"
    assert box.read_since is not None
    assert datetime.now(UTC) - timedelta(days=3, minutes=1) < box.read_since.replace(tzinfo=UTC)
    assert setup.keys.items[gmail_auth.token_key(ADDRESS)] == REFRESH
    assert setup.audited() == ["mailbox.connect"]
    page = admin.get("/mailboxes").text
    assert f"Connected {ADDRESS}. Mail from" in page and "connected" in page and "boss@example.com" in page
    assert "Disconnect" in page and "Connect Gmail" not in page  # one mailbox: no second connection offered
    assert "One mailbox is connected" in page
    # the app's own log lines (the test client's `httpx` lines do not exist in production, where uvicorn's access log
    # is off and the route is logged without its query string)
    ours = " ".join(r.getMessage() for r in caplog.records if r.name.startswith("po_dashboard"))
    assert "/mailboxes/callback" in ours  # the request was logged...
    for secret in ("ONE-TIME-CODE", body["code_verifier"][0], REFRESH, ACCESS, CLIENT_SECRET, query["state"]):
        assert secret not in page.replace(query["state"], "") and secret not in ours  # ...but without the code or state
    with Session(setup.engine) as db:
        assert "ONE-TIME-CODE" not in " ".join(f"{a.object} {a.before} {a.after}" for a in db.scalars(select(AuditLog)))


def test_the_return_from_google_needs_a_state_this_app_issued_and_works_once(setup: Setup) -> None:
    admin = setup.admin()
    query = start(admin)
    anonymous = new_client(setup.app)
    path = "/mailboxes/callback"
    assert anonymous.get(path, params={"state": "guessed", "code": "x"}).status_code == 400
    assert anonymous.get(path, params={"code": "x"}).status_code == 400
    assert anonymous.get(path, params={"state": query["state"]}).status_code == 400  # neither code nor error
    first = anonymous.get(path, params={"state": query["state"], "code": "ONE"})
    assert first.status_code == 200 and "/mailboxes/finish" in first.text
    assert first.headers["cache-control"] == "no-store"
    assert "default-src 'self'" in first.headers["content-security-policy"]
    assert anonymous.get(path, params={"state": query["state"], "code": "TWO"}).status_code == 400  # once only
    assert anonymous.post(path, headers=ORIGIN).status_code in (403, 405)  # a post needs a token the stranger lacks
    assert "no sign-in" not in admin.get("/mailboxes").text.lower()


def test_a_return_that_comes_too_late_is_refused(setup: Setup, monkeypatch: pytest.MonkeyPatch) -> None:
    admin = setup.admin()
    query = start(admin)
    monkeypatch.setattr(mailboxes.time, "monotonic", lambda: 1e12)  # long after the ten minutes
    assert come_back(setup.app, query).status_code == 400
    assert "expired" in admin.get("/mailboxes/continue", follow_redirects=True).text


def test_finishing_needs_the_same_admin_and_works_once(setup: Setup, env) -> None:  # noqa: ANN001
    with Session(setup.engine) as db:
        service.create_user(db, "second@example.com", "admin", password=PW)
    first, second = setup.admin(), new_client(setup.app)
    assert sign_in(second, "second@example.com").status_code == 303
    query = start(first)
    assert come_back(setup.app, query).status_code == 200
    taken = second.get("/mailboxes/finish", follow_redirects=True).text
    assert "No sign-in was waiting" in taken and setup.boxes() == []  # another admin cannot finish it
    assert first.get("/mailboxes/finish").status_code == 303 and len(setup.boxes()) == 1
    assert "No sign-in was waiting" in first.get("/mailboxes/finish", follow_redirects=True).text  # once only
    assert setup.google.tokens_given == 1


def test_a_refused_sign_in_says_so_in_fixed_words(setup: Setup) -> None:
    admin = setup.admin()
    query = start(admin)
    assert come_back(setup.app, query, code="", error="access_denied").status_code == 200
    page = admin.get("/mailboxes/finish", follow_redirects=True).text
    assert "Google did not allow the sign-in" in page and "access_denied" not in page and setup.boxes() == []


def test_google_refusing_the_code_leaves_nothing_saved(setup: Setup) -> None:
    admin = setup.admin()
    query = start(admin)
    assert come_back(setup.app, query).status_code == 200
    setup.google.refuse_token = True
    page = admin.get("/mailboxes/finish", follow_redirects=True).text
    assert "no longer accepts this sign-in" in page
    assert setup.boxes() == [] and gmail_auth.token_key(ADDRESS) not in setup.keys.items and setup.audited() == []


def test_starting_again_replaces_the_last_sign_in(setup: Setup) -> None:
    admin = setup.admin()
    old = start(admin)
    new = start(admin)
    assert old["state"] != new["state"]
    assert come_back(setup.app, old).status_code == 400
    assert come_back(setup.app, new).status_code == 200


def test_connect_checks_the_days_and_the_saved_client(setup: Setup) -> None:
    admin = setup.admin()
    for days in ("-1", "366", "abc", "1.5", "٣"):
        post(admin, "/mailboxes/connect", {"days": days})
        assert "whole number from 0 to 365" in admin.get("/mailboxes").text, days
    setup.keys.items.clear()  # the Google client was never saved
    assert post(admin, "/mailboxes/connect", {"days": "0"}).headers["location"] == "/mailboxes"
    assert "gmail-client" in admin.get("/mailboxes").text
    assert mailboxes._pending == {}


def test_a_second_different_mailbox_is_refused_while_one_is_connected(setup: Setup) -> None:
    with Session(setup.engine) as db:
        db.add(Mailbox(address="other@gmail.com", provider="gmail", state="connected"))
        db.commit()
    admin = setup.admin()
    assert "One mailbox is connected" in admin.get("/mailboxes").text  # no Connect card is even offered
    query = start(admin)  # a stale page could still post it
    assert come_back(setup.app, query).status_code == 200
    assert "A different mailbox is already connected" in admin.get("/mailboxes/finish", follow_redirects=True).text
    assert gmail_auth.token_key(ADDRESS) not in setup.keys.items and len(setup.boxes()) == 1


def test_the_message_gives_the_same_start_date_as_the_table_in_the_office_time_zone(
    setup: Setup, monkeypatch: pytest.MonkeyPatch
) -> None:
    """DEF-010: at 9 PM in New York it is already the next day in UTC; the message once showed the UTC date."""
    monkeypatch.setenv("TZ", "America/New_York")
    time.tzset()
    try:
        monkeypatch.setattr(gmail_connect, "utcnow", lambda: datetime(2026, 11, 12, 2, 0, tzinfo=UTC))
        admin = setup.admin()
        connect_fully(setup, admin)
        page = admin.get("/mailboxes").text
        assert "Mail from 11/11/2026 on will be read." in page  # 9 PM on 11/11 in the office, 2 AM 11/12 in UTC
        assert "11/12/2026" not in page
    finally:
        monkeypatch.undo()
        time.tzset()


# ---- from where it can be done


def test_connecting_from_another_computer_is_explained_not_attempted(tmp_path) -> None:  # noqa: ANN001
    settings = Settings(data_dir=tmp_path / "d", allowed_hosts=("office-mac.local",))
    engine = upgrade(settings)
    with Session(engine) as db:
        service.create_user(db, "boss@example.com", "admin", password=PW)
    app = create_app(settings, engine)
    client = TestClient(app, base_url="http://office-mac.local:8000", follow_redirects=False)
    assert sign_in(client, "boss@example.com").status_code == 303
    page = client.get("/mailboxes").text
    assert "Open this page on the office Mac itself, at http://127.0.0.1:8000/mailboxes" in page
    assert 'action="/mailboxes/connect"' not in page
    post(client, "/mailboxes/connect", {"days": "0"})  # a stale form is refused too
    assert "office Mac itself" in client.get("/mailboxes").text and mailboxes._pending == {}
    engine.dispose()


def test_connecting_is_done_in_terminal_while_https_is_on(tmp_path) -> None:  # noqa: ANN001
    settings = Settings(data_dir=tmp_path / "d", https=True)
    engine = upgrade(settings)
    with Session(engine) as db:
        service.create_user(db, "boss@example.com", "admin", password=PW)
    client = TestClient(create_app(settings, engine), base_url="https://127.0.0.1", follow_redirects=False)
    assert sign_in(client, "boss@example.com").status_code == 303
    assert "Connecting is done in Terminal while HTTPS is on" in client.get("/mailboxes").text
    engine.dispose()


# ---- state, reconnecting, disconnecting


def test_the_page_shows_the_state_the_last_check_and_why_it_failed(setup: Setup) -> None:
    now = datetime.now(UTC)
    with Session(setup.engine) as db:
        box = Mailbox(address=ADDRESS, provider="gmail", state="needs_reconnect", read_since=now, connected_at=now)
        db.add(box)
        db.flush()
        db.add_all([
            ScanState(mailbox_id=box.id, folder="Inbox", last_success=now - timedelta(minutes=7),
                      last_error="Gmail refused"),
            ScanState(mailbox_id=box.id, folder="Sent Items", last_success=now - timedelta(minutes=2)),
        ])  # fmt: skip
        db.commit()
    page = setup.admin().get("/mailboxes").text
    assert "needs reconnect" in page and "Gmail refused" in page and "Reconnect Gmail" in page
    assert "Reconnect your mailbox" in page and "Disconnect" in page


def test_reconnecting_a_lapsed_mailbox_makes_it_connected_again(setup: Setup) -> None:
    with Session(setup.engine) as db:
        db.add(Mailbox(address=ADDRESS, provider="gmail", state="needs_reconnect", read_since=datetime.now(UTC)))
        db.commit()
    connect_fully(setup, setup.admin())
    (box,) = setup.boxes()
    assert box.state == "connected" and setup.audited() == ["mailbox.connect"]


def test_disconnecting_forgets_the_token_and_keeps_the_mail(setup: Setup) -> None:
    admin = setup.admin()
    connect_fully(setup, admin)
    (box,) = setup.boxes()
    assert post(admin, f"/mailboxes/{box.id}/disconnect").status_code == 303
    assert setup.boxes()[0].state == "disconnected" and gmail_auth.token_key(ADDRESS) not in setup.keys.items
    assert setup.audited() == ["mailbox.connect", "mailbox.disconnect"]
    page = admin.get("/mailboxes").text
    assert f"Disconnected {ADDRESS}. Mail already stored stays." in page and "Connect Gmail" in page
    assert post(admin, "/mailboxes/999/disconnect").status_code == 303
    assert "was not found" in admin.get("/mailboxes").text


def test_a_folder_mailbox_has_no_connect_or_disconnect_here(setup: Setup) -> None:
    with Session(setup.engine) as db:
        db.add(Mailbox(address="demo@example.test", provider="folder", location="/no/folder", state="connected"))
        db.commit()
    admin = setup.admin()
    page = admin.get("/mailboxes").text
    assert "set up in Terminal" in page and "Disconnect" not in page and "Connect Gmail" in page
    (box,) = setup.boxes()
    assert post(admin, f"/mailboxes/{box.id}/disconnect").status_code == 303
    assert setup.boxes()[0].state == "connected"


def test_every_change_needs_the_security_token(setup: Setup) -> None:
    admin = setup.admin()
    connect_fully(setup, admin)
    (box,) = setup.boxes()
    for path in ("/mailboxes/connect", f"/mailboxes/{box.id}/disconnect"):
        assert admin.post(path, data={"days": "0"}, headers=ORIGIN).status_code == 403
        assert admin.post(path, data={"days": "0", "csrf_token": "wrong"}, headers=ORIGIN).status_code == 403
    assert setup.boxes()[0].state == "connected"
