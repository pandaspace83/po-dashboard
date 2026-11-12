"""The Gmail provider (PD-3.5, DEC-50): the shared mail contract against a pretend Gmail, plus read-only proof, retries,
the sign-in (PKCE, state, loopback) and the Keychain. Nothing here reaches Google or the real Keychain."""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import re
import threading
import urllib.request
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from mail_contract import MailSourceContract, message
from po_dashboard.db.mail import Mailbox, ScanState
from po_dashboard.intake.store import FileStore
from po_dashboard.mail import gmail as gmail_module
from po_dashboard.mail import gmail_auth, gmail_connect
from po_dashboard.mail.gmail import GmailSource
from po_dashboard.mail.gmail_auth import AccessTokens, Client, GmailError, NeedsReconnect
from po_dashboard.mail.source import FOLDERS, INBOX, SENT, MailSource
from po_dashboard.scheduler.scanner import Scanner
from test_gate import env  # noqa: F401

ACCESS, REFRESH, CLIENT_SECRET = "ya29.ACCESS-SECRET", "1//REFRESH-SECRET", "GOCSPX-client-secret-value"
CLIENT = Client("123-abc.apps.googleusercontent.com", CLIENT_SECRET)


class Memory:
    """The Keychain, in memory."""

    def __init__(self) -> None:
        self.items: dict[str, str] = {}

    def get(self, name: str) -> str | None:
        return self.items.get(name)

    def put(self, name: str, value: str) -> None:
        self.items[name] = value

    def forget(self, name: str) -> None:
        self.items.pop(name, None)


def b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode()


class FakeGmail:
    """Just enough of the Gmail API: profile, message list, history, raw message, and the token endpoint."""

    def __init__(self) -> None:
        self.mail: dict[str, tuple[set[str], bytes]] = {}
        self.history: list[tuple[int, str, set[str]]] = []  # (history id, message id, labels)
        self.clock = 1000
        self.requests: list[httpx.Request] = []
        self.script: list[httpx.Response] = []  # answers to give first, before behaving
        self.history_gone = False
        self.tokens_given = 0
        self.refuse_token = False

    def put(self, label: str, raw: bytes) -> str:
        self.clock += 1
        message_id = f"m{len(self.mail) + 1:04}"
        self.mail[message_id] = ({label}, raw)
        self.history.append((self.clock, message_id, {label}))
        return message_id

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.url.host == "oauth2.googleapis.com":
            if self.refuse_token:
                return httpx.Response(400, json={"error": "invalid_grant"})
            self.tokens_given += 1
            return httpx.Response(200, json={"access_token": ACCESS, "expires_in": 3600, "refresh_token": REFRESH})
        if self.script:
            return self.script.pop(0)
        assert request.url.host == "gmail.googleapis.com", request.url
        if request.headers.get("authorization") != f"Bearer {ACCESS}":
            return httpx.Response(401, json={"error": {"code": 401}})
        path, query = request.url.path.removeprefix("/gmail/v1/users/me"), dict(request.url.params)
        if path == "/profile":
            return httpx.Response(200, json={"emailAddress": "Test.Account@gmail.com", "historyId": str(self.clock)})
        if path == "/messages":
            ids = [i for i, (labels, _) in self.mail.items() if query["labelIds"] in labels]
            return httpx.Response(200, json={"messages": [{"id": i} for i in reversed(ids)]})
        if path == "/history":
            if self.history_gone:
                return httpx.Response(404, json={"error": {"code": 404}})
            start = int(query["startHistoryId"])
            added = [(h, i) for h, i, labels in self.history if h > start and query["labelId"] in labels]
            return httpx.Response(200, json={
                "historyId": str(self.clock),
                "history": [{"id": str(h), "messagesAdded": [{"message": {"id": i}}]} for h, i in added],
            })  # fmt: skip
        match = re.fullmatch(r"/messages/([0-9A-Za-z_-]+)", path)
        if match:
            if match[1] not in self.mail:
                return httpx.Response(404, json={"error": {"code": 404}})
            assert query.get("format") == "raw"
            return httpx.Response(200, json={"id": match[1], "raw": b64(self.mail[match[1]][1])})
        return httpx.Response(404)


def gmail_calls(fake: FakeGmail) -> list[httpx.Request]:
    return [r for r in fake.requests if r.url.host == "gmail.googleapis.com"]


def source_for(fake: FakeGmail, **kwargs) -> GmailSource:  # noqa: ANN003
    tokens = AccessTokens("test.account@gmail.com", seeded(), http=httpx.Client(transport=fake.transport()))
    return GmailSource(tokens.current, tokens.renew, http=httpx.Client(transport=fake.transport()),
                       sleep=lambda _s: None, **kwargs)  # fmt: skip


def seeded() -> Memory:
    keys = Memory()
    keys.put(gmail_auth.CLIENT_ID_KEY, CLIENT.client_id)
    keys.put(gmail_auth.CLIENT_SECRET_KEY, CLIENT.client_secret)
    keys.put(gmail_auth.token_key("test.account@gmail.com"), REFRESH)
    return keys


LABEL = {INBOX: "INBOX", SENT: "SENT"}


class TestGmailSource(MailSourceContract):
    fakes: dict[int, FakeGmail] = {}

    def make(self, tmp_path: Path) -> MailSource:
        fake = FakeGmail()
        source = source_for(fake, since=datetime(2026, 1, 1, tzinfo=UTC))
        self.fakes[id(source)] = fake
        return source

    def add(self, source: MailSource, folder: str, raw: bytes) -> None:
        self.fakes[id(source)].put(LABEL[folder], raw)

    def break_it(self, source: MailSource) -> None:
        self.fakes[id(source)].mail.clear()
        self.fakes[id(source)].script = [httpx.Response(401), httpx.Response(401)]

    def snapshot(self, source: MailSource) -> object:
        fake = self.fakes[id(source)]
        return (sorted(fake.mail), fake.history, [r.method for r in gmail_calls(fake) if r.method != "GET"])


# ---- read-only, fixed host, scope


def test_every_request_to_gmail_is_a_get_to_the_one_host_with_only_the_read_scope() -> None:
    fake = FakeGmail()
    source = source_for(fake, since=datetime(2026, 1, 1, tzinfo=UTC))
    for n in range(3):
        fake.put("INBOX", message(n))
    for folder in FOLDERS:
        source.fetch_new(folder, None)
    source.health()
    assert fake.requests
    gmail_calls = [r for r in fake.requests if r.url.host != "oauth2.googleapis.com"]
    assert {r.method for r in gmail_calls} == {"GET"} and {r.url.host for r in gmail_calls} == {"gmail.googleapis.com"}
    assert all(r.url.scheme == "https" for r in fake.requests)
    assert gmail_auth.SCOPE == "https://www.googleapis.com/auth/gmail.readonly"
    assert "gmail.readonly" in gmail_auth.auth_url(CLIENT, "http://127.0.0.1:1/x", "s", "v")
    assert not any(
        w in gmail_auth.auth_url(CLIENT, "http://127.0.0.1:1/x", "s", "v") for w in ("modify", "send", "compose")
    )


def test_a_message_id_that_is_not_plain_is_never_put_in_an_address() -> None:
    fake = FakeGmail()
    source = source_for(fake, since=datetime(2026, 1, 1, tzinfo=UTC))
    fake.put("INBOX", message(1))
    real = fake.handle

    def hostile(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/messages"):
            return httpx.Response(200, json={"messages": [{"id": "../../evil"}, {"id": "a/b"}, {"id": "m0001"}]})
        return real(request)

    source._http = httpx.Client(transport=httpx.MockTransport(hostile))  # noqa: SLF001
    assert [m.provider_id for m in source.fetch_new(INBOX, None).messages] == ["m0001"]
    assert all("evil" not in str(r.url) for r in fake.requests)


# ---- positions


def test_the_position_is_the_history_id_and_only_new_mail_is_read_afterwards() -> None:
    fake = FakeGmail()
    source = source_for(fake, since=datetime(2026, 1, 1, tzinfo=UTC))
    fake.put("INBOX", message(1))
    first = source.fetch_new(INBOX, None)
    assert json.loads(first.position)["h"] == str(fake.clock)
    fake.requests.clear()
    assert source.fetch_new(INBOX, first.position).messages == []
    assert not [r for r in fake.requests if r.url.path.endswith("/messages")]  # no listing: history only


def test_a_position_google_no_longer_keeps_means_the_recent_window_is_read_again() -> None:
    fake = FakeGmail()
    source = source_for(fake, since=datetime(2026, 1, 1, tzinfo=UTC))
    fake.put("INBOX", message(1))
    position = source.fetch_new(INBOX, None).position
    fake.history_gone = True
    again = source.fetch_new(INBOX, position)
    assert [m.raw for m in again.messages] == [message(1)]  # a repeat, which the scanner's dedupe absorbs


def test_a_message_deleted_before_it_is_read_is_skipped_not_fatal() -> None:
    fake = FakeGmail()
    source = source_for(fake, since=datetime(2026, 1, 1, tzinfo=UTC))
    gone = fake.put("INBOX", message(1))
    fake.put("INBOX", message(2))
    real = fake.handle

    def vanish(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith(f"/messages/{gone}"):
            return httpx.Response(404)
        return real(request)

    source._http = httpx.Client(transport=httpx.MockTransport(vanish))  # noqa: SLF001
    result = source.fetch_new(INBOX, None)
    assert [m.raw for m in result.messages] == [message(2)] and [s.provider_id for s in result.skipped] == [gone]


def test_a_first_read_asks_only_for_mail_since_the_start_date() -> None:
    fake = FakeGmail()
    since = datetime(2026, 11, 1, tzinfo=UTC)
    source_for(fake, since=since).fetch_new(INBOX, None)
    listing = next(r for r in fake.requests if r.url.path.endswith("/messages"))
    assert f"after:{int(since.timestamp())}" in listing.url.params["q"]


# ---- retries


def retrying(fake: FakeGmail) -> tuple[GmailSource, list[float]]:
    waits: list[float] = []
    source = source_for(fake, since=datetime(2026, 1, 1, tzinfo=UTC))
    source._sleep, source._jitter = waits.append, lambda: 1.0  # noqa: SLF001
    return source, waits


def test_a_429_is_retried_after_the_wait_google_asks_for() -> None:
    fake = FakeGmail()
    fake.script = [httpx.Response(429, headers={"Retry-After": "7"})]
    source, waits = retrying(fake)
    assert source.health().ok and waits == [7.0]


def test_a_500_is_retried_with_growing_waits_and_then_gives_up() -> None:
    fake = FakeGmail()
    fake.script = [httpx.Response(503)] * 10
    source, waits = retrying(fake)
    health = source.health()
    assert not health.ok and "several tries" in health.detail
    assert waits == [1.0, 2.0, 4.0, 8.0] and len(gmail_calls(fake)) == 5


def test_waits_are_capped() -> None:
    fake = FakeGmail()
    fake.script = [httpx.Response(429, headers={"Retry-After": "99999"})]
    source, waits = retrying(fake)
    source.health()
    assert waits == [gmail_module.MAX_RETRY_AFTER]


def test_a_connection_error_is_retried() -> None:
    fake = FakeGmail()
    calls = {"n": 0}

    def flaky(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            raise httpx.ConnectError("down")
        return fake.handle(request)

    source, waits = retrying(fake)
    source._http = httpx.Client(transport=httpx.MockTransport(flaky))  # noqa: SLF001
    assert source.health().ok and len(waits) == 1


def test_a_rate_limit_sent_as_403_is_retried_but_any_other_403_is_not() -> None:
    fake = FakeGmail()
    fake.script = [httpx.Response(403, json={"error": {"errors": [{"reason": "userRateLimitExceeded"}]}})]
    source, waits = retrying(fake)
    assert source.health().ok and len(waits) == 1
    fake2 = FakeGmail()
    fake2.script = [httpx.Response(403, json={"error": {"errors": [{"reason": "insufficientPermissions"}]}})]
    source2, waits2 = retrying(fake2)
    assert not source2.health().ok and waits2 == [] and len(gmail_calls(fake2)) == 1


def test_a_401_refreshes_the_token_once_then_needs_a_reconnect() -> None:
    fake = FakeGmail()
    fake.script = [httpx.Response(401)]
    source, _ = retrying(fake)
    assert source.health().ok and fake.tokens_given == 2  # one to start, one after the 401
    fake2 = FakeGmail()
    fake2.script = [httpx.Response(401), httpx.Response(401)]
    source2, waits = retrying(fake2)
    with pytest.raises(NeedsReconnect):
        source2.fetch_new(INBOX, None)
    assert waits == []  # a 401 is never waited on


# ---- tokens and secrets


def test_the_access_token_is_reused_until_it_is_nearly_out() -> None:
    fake = FakeGmail()
    now = [0.0]
    tokens = AccessTokens("test.account@gmail.com", seeded(), http=httpx.Client(transport=fake.transport()),
                          clock=lambda: now[0])  # fmt: skip
    tokens.current()
    tokens.current()
    assert fake.tokens_given == 1
    now[0] = 3600 - 30
    tokens.current()
    assert fake.tokens_given == 2


def test_a_withdrawn_sign_in_and_a_missing_one_both_mean_reconnect() -> None:
    fake = FakeGmail()
    fake.refuse_token = True
    with pytest.raises(NeedsReconnect):
        AccessTokens("test.account@gmail.com", seeded(), http=httpx.Client(transport=fake.transport())).current()
    with pytest.raises(NeedsReconnect):
        AccessTokens("nobody@gmail.com", seeded()).current()


def test_no_token_or_secret_reaches_a_log_or_an_error(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    fake = FakeGmail()
    fake.script = [httpx.Response(401), httpx.Response(401)]
    source, _ = retrying(fake)
    with pytest.raises(NeedsReconnect) as refused:
        source.fetch_new(INBOX, None)
    fake2 = FakeGmail()
    fake2.script = [httpx.Response(500, text=f"boom {ACCESS} {REFRESH}")] * 6
    source2, _ = retrying(fake2)
    problems = [str(refused.value), source2.health().detail]
    for text in [*problems, caplog.text]:
        assert not any(secret in text for secret in (ACCESS, REFRESH, CLIENT_SECRET))


def test_the_secrets_are_asked_for_in_the_keychain_never_a_file() -> None:
    source = (Path(gmail_auth.__file__)).read_text()
    assert "keyring" in source and "open(" not in source and "write_text" not in source


def test_the_client_must_be_saved_first_and_must_look_right() -> None:
    keys = Memory()
    with pytest.raises(GmailError, match="not saved"):
        gmail_auth.load_client(keys)
    with pytest.raises(GmailError, match="does not look"):
        gmail_connect.save_client(keys, "not-a-client", "short")
    assert keys.items == {}
    gmail_connect.save_client(keys, f" {CLIENT.client_id} ", f" {CLIENT_SECRET} ")
    assert gmail_auth.load_client(keys) == CLIENT


# ---- the sign-in


def test_pkce_challenge_is_the_hash_of_the_verifier_and_every_sign_in_is_different() -> None:
    verifier = gmail_auth.new_verifier()
    assert 43 <= len(verifier) <= 128 and re.fullmatch(r"[A-Za-z0-9_-]+", verifier)
    digest = hashlib.sha256(verifier.encode()).digest()
    assert gmail_auth.challenge_of(verifier) == base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    assert gmail_auth.new_verifier() != verifier


def test_the_sign_in_address_asks_for_read_only_with_pkce_and_a_state() -> None:
    url = urlparse(gmail_auth.auth_url(CLIENT, "http://127.0.0.1:5555/mailboxes/callback", "STATE", "VERIFIER"))
    query = {k: v[0] for k, v in parse_qs(url.query).items()}
    assert (url.scheme, url.netloc, url.path) == ("https", "accounts.google.com", "/o/oauth2/v2/auth")
    assert query["scope"] == gmail_auth.SCOPE and query["state"] == "STATE" and query["code_challenge_method"] == "S256"
    assert query["response_type"] == "code" and query["access_type"] == "offline"
    assert CLIENT_SECRET not in url.query and "VERIFIER" not in url.query  # only the challenge goes to Google's page


def run_sign_in(callback: Callable[[str, str], str], fake: FakeGmail | None = None, wait: float = 5):  # noqa: ANN202
    """Drive gmail_auth.sign_in the way Google's page would: `callback(redirect_uri, state)` returns the address to
    visit on the loopback listener."""
    fake = fake or FakeGmail()
    seen: dict[str, str] = {}

    def browser(url: str) -> None:
        query = {k: v[0] for k, v in parse_qs(urlparse(url).query).items()}
        seen.update(query)
        target = callback(query["redirect_uri"], query["state"])

        def visit() -> None:
            try:
                urllib.request.urlopen(target, timeout=5).read()  # noqa: S310
            except OSError:  # a 400 from the listener is an expected answer in some tests
                return  # noqa: TRY300

        threading.Thread(target=visit).start()

    result = gmail_auth.sign_in(CLIENT, open_page=browser, wait=wait, http=httpx.Client(transport=fake.transport()))
    return result, seen, fake


def test_a_sign_in_returns_the_tokens_and_the_listener_is_on_loopback_only() -> None:
    (tokens, redirect), seen, fake = run_sign_in(lambda uri, state: f"{uri}?code=ONE-TIME&state={state}")
    assert tokens["refresh_token"] == REFRESH and urlparse(redirect).hostname == "127.0.0.1"
    assert urlparse(redirect).path == "/mailboxes/callback"
    exchange = next(r for r in fake.requests if r.url.host == "oauth2.googleapis.com")
    body = parse_qs(exchange.content.decode())
    assert body["code"] == ["ONE-TIME"] and body["grant_type"] == ["authorization_code"]
    assert gmail_auth.challenge_of(body["code_verifier"][0]) == seen["code_challenge"]  # the verifier matches


def test_a_return_with_the_wrong_state_is_refused() -> None:
    with pytest.raises(GmailError, match="not finished"):
        run_sign_in(lambda uri, state: f"{uri}?code=EVIL&state=WRONG", wait=1)


def test_a_return_to_another_path_is_ignored() -> None:
    with pytest.raises(GmailError, match="not finished"):
        run_sign_in(lambda uri, state: uri.replace("/mailboxes/callback", "/other") + f"?code=x&state={state}", wait=1)


def test_a_sign_in_google_refuses_says_so_in_fixed_words() -> None:
    with pytest.raises(GmailError, match="did not allow"):
        run_sign_in(lambda uri, state: f"{uri}?error=access_denied&state={state}")


# ---- connecting


def test_connect_saves_the_token_in_the_keychain_and_the_mailbox_in_the_database(env, caplog) -> None:  # noqa: ANN001, F811
    caplog.set_level(logging.DEBUG)
    keys, fake = Memory(), FakeGmail()
    keys.put(gmail_auth.CLIENT_ID_KEY, CLIENT.client_id)
    keys.put(gmail_auth.CLIENT_SECRET_KEY, CLIENT.client_secret)
    sign_in = lambda client: ({"access_token": ACCESS, "refresh_token": REFRESH}, "http://127.0.0.1:1/x")  # noqa: E731
    with Session(env[1]) as db:
        box = gmail_connect.connect(db, keys, days=3, sign_in=sign_in, http=httpx.Client(transport=fake.transport()))
        assert box.address == "test.account@gmail.com" and box.provider == "gmail" and box.state == "connected"
        assert box.read_since is not None
        assert datetime.now(UTC) - timedelta(days=3, minutes=1) < box.read_since.replace(tzinfo=UTC)
        stored = " ".join(str(v) for row in db.execute(select(Mailbox)).all() for v in row[0].__dict__.values())
    assert keys.items[gmail_auth.token_key("test.account@gmail.com")] == REFRESH
    assert REFRESH not in stored and ACCESS not in stored and REFRESH not in caplog.text
    for path in Path(env[2].data_dir).rglob("*"):
        if path.is_file():
            assert REFRESH.encode() not in path.read_bytes() and CLIENT_SECRET.encode() not in path.read_bytes()


def test_reconnecting_with_more_days_reads_further_back_and_never_narrows(env) -> None:  # noqa: ANN001, F811
    keys, fake = seeded(), FakeGmail()
    sign_in = lambda client: ({"access_token": ACCESS, "refresh_token": REFRESH}, "x")  # noqa: E731
    http = httpx.Client(transport=fake.transport())
    with Session(env[1]) as db:
        first = gmail_connect.connect(db, keys, sign_in=sign_in, http=http)
        db.add(ScanState(mailbox_id=first.id, folder="Inbox", position="saved"))
        db.commit()
        today = first.read_since
        gmail_connect.connect(db, keys, days=0, sign_in=sign_in, http=http)
        assert db.scalars(select(ScanState)).one().position == "saved"  # nothing widened, nothing reset
        box = gmail_connect.connect(db, keys, days=7, sign_in=sign_in, http=http)
        assert box.read_since < today - timedelta(days=6)
        assert db.scalars(select(ScanState)).one().position is None
        wide = box.read_since
        assert gmail_connect.connect(db, keys, days=1, sign_in=sign_in, http=http).read_since == wide


def test_connecting_without_a_refresh_token_or_with_bad_days_is_refused(env) -> None:  # noqa: ANN001, F811
    keys = seeded()
    with Session(env[1]) as db:
        with pytest.raises(GmailError, match="long-lived"):
            gmail_connect.connect(db, keys, sign_in=lambda c: ({"access_token": ACCESS}, "x"),
                                  http=httpx.Client(transport=FakeGmail().transport()))  # fmt: skip
        with pytest.raises(GmailError, match="between 0 and 365"):
            gmail_connect.connect(db, keys, days=999)
        assert db.scalars(select(Mailbox)).all() == []


def test_disconnecting_forgets_the_token_and_stops_scanning(env) -> None:  # noqa: ANN001, F811
    keys, fake = seeded(), FakeGmail()
    with Session(env[1]) as db:
        gmail_connect.connect(db, keys, sign_in=lambda c: ({"access_token": ACCESS, "refresh_token": REFRESH}, "x"),
                              http=httpx.Client(transport=fake.transport()))  # fmt: skip
        gmail_connect.disconnect(db, keys, "Test.Account@gmail.com")
        assert db.scalars(select(Mailbox)).one().state == "disconnected"
    assert gmail_auth.token_key("test.account@gmail.com") not in keys.items


# ---- the scanner with Gmail


def test_the_scanner_reads_gmail_and_a_lapsed_sign_in_marks_the_mailbox_needs_reconnect(env, tmp_path: Path) -> None:  # noqa: ANN001, F811
    fake, keys = FakeGmail(), seeded()
    fake.put("INBOX", message(1))
    fake.put("SENT", message(2))
    with Session(env[1]) as db:
        db.add(Mailbox(address="test.account@gmail.com", provider="gmail", read_since=datetime(2026, 1, 1, tzinfo=UTC)))
        db.commit()

    def open_gmail(mailbox: Mailbox) -> MailSource:
        tokens = AccessTokens(mailbox.address, keys, http=httpx.Client(transport=fake.transport()))
        return GmailSource(tokens.current, tokens.renew, since=mailbox.read_since,
                           http=httpx.Client(transport=fake.transport()), sleep=lambda _s: None)  # fmt: skip

    scanner = Scanner(env[1], FileStore(tmp_path / "originals"), open_mailbox=open_gmail)
    first = scanner.scan()
    assert first.stored == 2 and not first.errors
    assert scanner.scan().stored == 0
    keys.items.pop(gmail_auth.token_key("test.account@gmail.com"))  # the saved sign-in is gone
    report = scanner.scan()
    assert report.errors and all("connect the mailbox again" in e for e in report.errors)
    with Session(env[1]) as db:
        assert db.scalars(select(Mailbox)).one().state == "needs_reconnect"
    again = scanner.scan()  # a mailbox that needs reconnecting is left alone, not retried every five minutes
    assert again.ran and again.errors == [] and again.stored == 0


def test_the_terminal_commands_ask_for_secrets_at_a_hidden_prompt_and_print_none(
    env,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],  # noqa: ANN001, F811
) -> None:
    import argparse

    from po_dashboard import cli

    keys, fake = Memory(), FakeGmail()
    monkeypatch.setattr(gmail_auth, "Keychain", lambda: keys)
    answers = iter([CLIENT.client_id, CLIENT.client_secret])
    monkeypatch.setattr(cli.getpass, "getpass", lambda prompt="": next(answers))
    monkeypatch.setattr(
        gmail_auth, "sign_in",
        lambda client, http=None: ({"access_token": ACCESS, "refresh_token": REFRESH}, "x"),
    )  # fmt: skip
    real_connect = gmail_connect.connect
    pretend = httpx.Client(transport=fake.transport())
    monkeypatch.setattr(cli, "connect_gmail", lambda db, kc, days: real_connect(db, kc, days=days, http=pretend))
    with Session(env[1]) as db:
        assert cli.run_mailbox_command(db, argparse.Namespace(mailbox_command="gmail-client")) == 0
        assert cli.run_mailbox_command(db, argparse.Namespace(mailbox_command="connect-gmail", days=0)) == 0
        out = capsys.readouterr()
        assert "Connected test.account@gmail.com" in out.out
        assert not any(x in out.out + out.err for x in (ACCESS, REFRESH, CLIENT_SECRET))
        assert (
            cli.run_mailbox_command(db, argparse.Namespace(mailbox_command="disconnect-gmail", address="nobody@x.com"))
            == 1
        )
        assert "No Gmail mailbox" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("body", "words"),
    [
        ({"error": {"errors": [{"reason": "accessNotConfigured"}]}}, "not turned on"),
        ({"error": {"details": [{"reason": "SERVICE_DISABLED"}]}}, "not turned on"),
        ({"error": {"errors": [{"reason": "insufficientPermissions"}]}}, "tick the box"),
        ({"error": {"details": [{"reason": "ACCESS_TOKEN_SCOPE_INSUFFICIENT"}]}}, "tick the box"),
        ({"error": {"errors": [{"reason": "SOMETHING-ELSE secret"}]}}, "status 403"),
    ],
)
def test_a_403_says_what_to_fix_in_fixed_words(body: dict, words: str) -> None:  # type: ignore[type-arg]
    fake = FakeGmail()
    fake.script = [httpx.Response(403, json=body)]
    source, waits = retrying(fake)
    detail = source.health().detail
    assert words in detail and waits == [] and "secret" not in detail
