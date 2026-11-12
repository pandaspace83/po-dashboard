"""Signing in to Gmail and keeping the sign-in (PD-3.5, DEC-50, ARCHITECTURE §7).

Google's own sign-in page does the sign-in (the authorisation-code flow with PKCE); the app never sees the Google
password. The only scope asked for is `gmail.readonly`, so Google itself refuses any send, move or delete (DEC-07).
The owner's OAuth client id and secret, and each mailbox's refresh token, live in the macOS Keychain through
`keyring`: never in a file, the database, a log or an error message (DEC-13). Every address called is a constant
here; nothing is taken from a message or from Google's replies.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import http.server
import secrets
import threading
import time
import webbrowser
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol
from urllib.parse import parse_qs, urlencode, urlparse

import httpx

SCOPE = "https://www.googleapis.com/auth/gmail.readonly"
AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"  # noqa: S105 - an address, not a secret
SERVICE = "po-dashboard"
CLIENT_ID_KEY, CLIENT_SECRET_KEY = "gmail-client-id", "gmail-client-secret"
CALLBACK_PATH = "/mailboxes/callback"
LOOPBACK = "127.0.0.1"
TIMEOUT = httpx.Timeout(20.0, connect=5.0)
SIGN_IN_WAIT_SECONDS = 300


class GmailError(Exception):
    """Gmail could not be read. The message is fixed words, never mail text, a token or a Google reply."""


class NeedsReconnect(GmailError):
    """Google no longer accepts the saved sign-in (it lapsed, or was withdrawn). The person signs in again."""


class Secrets(Protocol):
    def get(self, name: str) -> str | None: ...
    def put(self, name: str, value: str) -> None: ...
    def forget(self, name: str) -> None: ...


class Keychain:
    """The macOS Keychain through `keyring`."""

    def get(self, name: str) -> str | None:
        import keyring

        return keyring.get_password(SERVICE, name)

    def put(self, name: str, value: str) -> None:
        import keyring

        keyring.set_password(SERVICE, name, value)

    def forget(self, name: str) -> None:
        import keyring
        from keyring.errors import PasswordDeleteError

        try:
            keyring.delete_password(SERVICE, name)
        except PasswordDeleteError:
            pass


def token_key(address: str) -> str:
    return f"gmail-refresh-token:{address.lower()}"


@dataclass(frozen=True)
class Client:
    client_id: str
    client_secret: str


def load_client(secrets_: Secrets) -> Client:
    client_id, client_secret = secrets_.get(CLIENT_ID_KEY), secrets_.get(CLIENT_SECRET_KEY)
    if not client_id or not client_secret:
        raise GmailError("The Google client id and secret are not saved yet. Run: po-dashboard mailbox gmail-client")
    return Client(client_id, client_secret)


# ---------------------------------------------------------------- the sign-in


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def new_verifier() -> str:
    return _b64(secrets.token_bytes(48))  # 64 characters, within the 43 to 128 PKCE allows


def challenge_of(verifier: str) -> str:
    return _b64(hashlib.sha256(verifier.encode("ascii")).digest())


def auth_url(client: Client, redirect_uri: str, state: str, verifier: str) -> str:
    return AUTH_URL + "?" + urlencode({
        "client_id": client.client_id, "redirect_uri": redirect_uri, "response_type": "code", "scope": SCOPE,
        "state": state, "code_challenge": challenge_of(verifier), "code_challenge_method": "S256",
        "access_type": "offline", "prompt": "consent",
    })  # fmt: skip


def _tokens(call: Callable[[], httpx.Response]) -> dict[str, Any]:
    try:
        response = call()
    except httpx.HTTPError:
        raise GmailError("Could not reach Google to sign in.") from None
    return _tokens_from(response)


def _tokens_from(response: httpx.Response) -> dict[str, Any]:
    try:
        body = response.json()
    except ValueError:
        body = {}
    if response.status_code == 200 and isinstance(body, dict) and body.get("access_token"):
        return body
    if isinstance(body, dict) and body.get("error") == "invalid_grant":
        raise NeedsReconnect("Google no longer accepts this sign-in. Connect the mailbox again.")
    raise GmailError(f"Google refused the sign-in (status {response.status_code}).")


def exchange_code(client: Client, code: str, verifier: str, redirect_uri: str,
                  http: httpx.Client | None = None) -> dict[str, Any]:  # fmt: skip
    """Swap the one-time code for tokens. The refresh token is in the answer."""
    data = {"client_id": client.client_id, "client_secret": client.client_secret, "code": code,
            "code_verifier": verifier, "grant_type": "authorization_code", "redirect_uri": redirect_uri}  # fmt: skip
    return _tokens(lambda: (http or httpx).post(TOKEN_URL, data=data, timeout=TIMEOUT))


class _Receiver(http.server.BaseHTTPRequestHandler):
    server: _Server
    timeout = 10  # seconds a connection may stay silent: one that never speaks cannot hold up Google's return (PD-3.6)

    def do_GET(self) -> None:
        url = urlparse(self.path)
        ok = url.path == CALLBACK_PATH
        if ok:
            query = {k: v[0] for k, v in parse_qs(url.query).items() if v}
            if query.get("state") and hmac.compare_digest(query["state"], self.server.expected_state):
                self.server.result = query
            else:
                ok = False
        self.send_response(200 if ok else 400)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(b"You can close this tab and go back to Terminal." if ok else b"Not expected here.")
        if ok:
            self.server.done.set()

    def log_message(self, format: str, *args: object) -> None:  # the address holds the one-time code: say nothing
        return


class _Server(http.server.HTTPServer):
    expected_state: str
    result: dict[str, str] | None
    done: threading.Event


def sign_in(
    client: Client, *, open_page: Callable[[str], object] = webbrowser.open, wait: float = SIGN_IN_WAIT_SECONDS,
    http: httpx.Client | None = None,
) -> tuple[dict[str, Any], str]:  # fmt: skip
    """Send the person to Google, wait for Google to send them back to this Mac, and return (tokens, redirect_uri).
    The listener is on the loopback address only, answers one expected request and is closed afterwards."""
    server = _Server((LOOPBACK, 0), _Receiver)
    server.expected_state, server.result, server.done = secrets.token_urlsafe(32), None, threading.Event()
    redirect = f"http://{LOOPBACK}:{server.server_port}{CALLBACK_PATH}"
    verifier = new_verifier()
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        open_page(auth_url(client, redirect, server.expected_state, verifier))
        if not server.done.wait(wait) or server.result is None:
            raise GmailError("The sign-in was not finished in time.")
        if "error" in server.result:
            raise GmailError("Google did not allow the sign-in.")
        code = server.result.get("code")
        if not code:
            raise GmailError("Google sent no sign-in code.")
        return exchange_code(client, code, verifier, redirect, http), redirect
    finally:
        server.shutdown()
        server.server_close()


# ---------------------------------------------------------------- keeping it going


class AccessTokens:
    """Gives a current access token for one mailbox, refreshing it from the Keychain's refresh token when needed."""

    def __init__(self, address: str, secrets_: Secrets, http: httpx.Client | None = None,
                 clock: Callable[[], float] = time.monotonic) -> None:  # fmt: skip
        self._address, self._secrets, self._http, self._clock = address, secrets_, http, clock
        self._token: str | None = None
        self._expires = 0.0

    def current(self) -> str:
        if self._token and self._clock() < self._expires - 60:
            return self._token
        return self.renew()

    def renew(self) -> str:
        refresh = self._secrets.get(token_key(self._address))
        if not refresh:
            raise NeedsReconnect("This mailbox has no saved sign-in. Connect it again.")
        client = load_client(self._secrets)
        data = {"client_id": client.client_id, "client_secret": client.client_secret, "refresh_token": refresh,
                "grant_type": "refresh_token"}  # fmt: skip
        body = _tokens(lambda: (self._http or httpx).post(TOKEN_URL, data=data, timeout=TIMEOUT))
        self._token = str(body["access_token"])
        self._expires = self._clock() + float(body.get("expires_in", 3600))
        return self._token
