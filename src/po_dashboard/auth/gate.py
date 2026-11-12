"""The default-deny gate (ARCHITECTURE §10.1): host check, session check, CSRF and Origin checks, security headers.

It sits in front of every route, so a route added later is protected without anyone remembering to. Only the exact
paths in PUBLIC answer without a session. Pure ASGI, so it can read a form body and hand it on unchanged.
"""

from __future__ import annotations

import logging
import re
import secrets
import time
from collections.abc import Awaitable, Callable, MutableMapping
from http.cookies import SimpleCookie
from typing import Any
from urllib.parse import parse_qs, urlsplit

from sqlalchemy import Engine
from sqlalchemy.orm import Session

from po_dashboard.auth import service
from po_dashboard.config import Settings
from po_dashboard.db.models import AppUser
from po_dashboard.ops import logs

Scope = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[MutableMapping[str, Any]]]
Send = Callable[[MutableMapping[str, Any]], Awaitable[None]]

SESSION_COOKIE = "po_session"
SIGN_IN_COOKIE = "po_csrf"  # double-submit token for the sign-in form, where no session exists yet
# A host name or address, with an optional port, and nothing else (no user info, no path).
HOST = re.compile(r"(?P<name>\[[0-9a-f:.]+\]|[a-z0-9]([a-z0-9.-]*[a-z0-9])?)(:[0-9]{1,5})?")
HTMX_FILE = "/static/htmx-2.0.11.min.js"  # pinned; its checksum is checked by a test (DEC-45)
CACHEABLE = frozenset({"/static/app.css", HTMX_FILE})
# "/mailboxes/callback" is Google's return after a mailbox sign-in. The session cookie is SameSite=Strict, so it is not
# sent on that return; the route only accepts a one-time `state` this app issued (pages/mailboxes.py, PD-3.7).
PUBLIC = frozenset(
    {
        "/sign-in", "/health", "/mailboxes/callback",
        "/static/app.css", "/static/brand/logo.png", "/static/brand/favicon.png",
    }
)  # fmt: skip
PENDING_ALLOWED = frozenset(
    {"/change-password", "/sign-out", "/static/app.css", "/static/brand/logo.png", "/static/brand/favicon.png"}
)
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
MAX_FORM = 64 * 1024
CSP = (
    "default-src 'self'; frame-ancestors 'none'; base-uri 'none'; object-src 'none'; form-action 'self'"
)
HEADERS = {
    b"content-security-policy": CSP.encode(),
    b"x-content-type-options": b"nosniff",
    b"referrer-policy": b"same-origin",
    b"permissions-policy": b"camera=(), microphone=(), geolocation=(), payment=(), usb=()",
    b"cross-origin-opener-policy": b"same-origin",
}
HSTS = b"max-age=31536000"  # sent only when HTTPS is really on (settings.https)


def cookie_value(headers: dict[bytes, bytes], name: str) -> str | None:
    raw = headers.get(b"cookie")
    if not raw:
        return None
    jar: SimpleCookie = SimpleCookie()
    try:
        jar.load(raw.decode("latin-1"))
    except Exception:  # a malformed cookie header is treated as no cookie
        return None
    return jar[name].value if name in jar else None


log = logging.getLogger("po_dashboard.request")


class Gate:
    def __init__(self, app: Callable[[Scope, Receive, Send], Awaitable[None]], settings: Settings, engine: Engine):
        self.app = app
        self.settings = settings
        self.engine = engine

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "lifespan":
            await self.app(scope, receive, send)
            return
        if scope["type"] != "http":  # no websockets in this app
            return
        headers = {k.lower(): v for k, v in scope["headers"]}
        path = scope["path"]
        started = time.perf_counter()
        rid = logs.request_id()
        status_seen = 0
        send_plain = send

        async def send_logged(message: MutableMapping[str, Any]) -> None:
            """Adds the request id and the time taken to every response, and writes one log line when it starts."""
            nonlocal status_seen
            if message["type"] == "http.response.start":
                status_seen = int(message["status"])
                ms = (time.perf_counter() - started) * 1000
                message = {**message, "headers": [
                    *message.get("headers", []), (b"x-request-id", rid.encode()),
                    (b"server-timing", f"app;dur={ms:.0f}".encode()),
                ]}
                log.info("request id=%s %s %s status=%s ms=%.0f", rid, scope["method"], logs.route_of(path),
                         status_seen, ms)
            await send_plain(message)

        send = send_logged

        async def respond(status: int, body: bytes = b"", extra: list[tuple[bytes, bytes]] | None = None) -> None:
            out = [(b"content-type", b"text/plain; charset=utf-8"), (b"content-length", str(len(body)).encode())]
            await self._send_with_headers(send, path, status, out + (extra or []))
            await send({"type": "http.response.body", "body": body})

        match = HOST.fullmatch(headers.get(b"host", b"").decode("latin-1").lower())
        hostname = match.group("name").strip("[]") if match else ""
        if hostname not in self.settings.accepted_hosts:
            await respond(400, b"Unknown host name.")
            return

        method = scope["method"]
        state = scope.setdefault("state", {})
        token = cookie_value(headers, SESSION_COOKIE)
        user: AppUser | None = None
        if token:
            with Session(self.engine) as db:
                background = headers.get(b"x-background") == b"1"
                row = service.resolve_session(db, token, touch=not background)
                if row is not None:
                    user = db.get(AppUser, row.user_id)
                    state.update(session_id=row.id, user_id=row.user_id, stage=row.stage, csrf_token=row.csrf_token)
        authenticated = user is not None

        if path not in PUBLIC:
            if not authenticated:
                await self._deny(scope, headers, respond)
                return
            if state["stage"] == "pending" and path not in PENDING_ALLOWED:
                if method in SAFE_METHODS:
                    await respond(303, b"", [(b"location", b"/change-password")])
                else:
                    await respond(403, b"Change your password first.")
                return

        if method not in SAFE_METHODS:
            origin = headers.get(b"origin", b"").decode("latin-1")
            if not origin or (urlsplit(origin).hostname or "") not in self.settings.accepted_hosts:
                await respond(403, b"Cross-site request refused.")
                return
            expected = state.get("csrf_token") if authenticated else cookie_value(headers, SIGN_IN_COOKIE)
            receive, supplied = await self._supplied_token(headers, receive)
            if not expected or not supplied or not secrets.compare_digest(str(expected), supplied):
                await respond(403, b"Missing or wrong security token. Reload the page and try again.")
                return

        # A form sent by HTMX that is answered with a page, not a redirect (a refusal, or a page shown once such as
        # a temporary password), must not put the form's own address in the browser history: Back or Reload there
        # would ask for a page that only answers a POST.
        no_history = b"hx-request" in headers and method not in SAFE_METHODS

        async def wrapped_send(message: MutableMapping[str, Any]) -> None:
            if message["type"] == "http.response.start":
                extra = list(message.get("headers", []))
                if no_history and not 300 <= int(message["status"]) < 400:
                    extra.append((b"hx-push-url", b"false"))
                message["headers"] = self._headers(path, extra)
            await send(message)

        try:
            await self.app(scope, receive, wrapped_send)
        except Exception as problem:
            # the type and the route only: an error message can quote the data it choked on
            log.error("unhandled id=%s %s %s %s", rid, method, logs.route_of(path), type(problem).__name__)
            raise

    async def _deny(self, scope: Scope, headers: dict[bytes, bytes], respond: Any) -> None:
        wants_page = b"text/html" in headers.get(b"accept", b"") and b"hx-request" not in headers
        if wants_page and scope["method"] in SAFE_METHODS:
            await respond(303, b"", [(b"location", b"/sign-in")])
        elif b"hx-request" in headers:
            # HTMX does not show a 401; this header sends the whole browser tab to the sign-in page instead.
            await respond(401, b"Sign in required.", [(b"hx-redirect", b"/sign-in")])
        else:
            await respond(401, b"Sign in required.")

    async def _supplied_token(self, headers: dict[bytes, bytes], receive: Receive) -> tuple[Receive, str | None]:
        """The token from the X-CSRF-Token header or, for a urlencoded form, its csrf_token field."""
        header = headers.get(b"x-csrf-token")
        if header:
            return receive, header.decode("latin-1")
        if not headers.get(b"content-type", b"").startswith(b"application/x-www-form-urlencoded"):
            return receive, None
        chunks: list[bytes] = []
        size = 0
        while True:
            message = await receive()
            chunk = message.get("body", b"")
            size += len(chunk)
            if size > MAX_FORM:
                return receive, None
            chunks.append(chunk)
            if not message.get("more_body"):
                break
        body = b"".join(chunks)
        sent = False

        async def replay() -> MutableMapping[str, Any]:
            nonlocal sent
            if sent:
                return {"type": "http.disconnect"}
            sent = True
            return {"type": "http.request", "body": body, "more_body": False}

        fields = parse_qs(body.decode("utf-8", "replace"), keep_blank_values=True)
        values = fields.get("csrf_token")
        return replay, (values[0] if values else None)

    def _headers(self, path: str, existing: list[tuple[bytes, bytes]]) -> list[tuple[bytes, bytes]]:
        names = {k.lower() for k, _ in existing}
        out = list(existing)
        for key, value in HEADERS.items():
            if key not in names:
                out.append((key, value))
        if self.settings.https and b"strict-transport-security" not in names:
            out.append((b"strict-transport-security", HSTS))
        if path not in CACHEABLE and b"cache-control" not in names:
            out.append((b"cache-control", b"no-store"))
        return out

    async def _send_with_headers(self, send: Send, path: str, status: int, headers: list[tuple[bytes, bytes]]) -> None:
        await send({"type": "http.response.start", "status": status, "headers": self._headers(path, headers)})
