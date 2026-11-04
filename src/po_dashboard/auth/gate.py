"""The default-deny gate (ARCHITECTURE §10.1): host check, session check, CSRF and Origin checks, security headers.

It sits in front of every route, so a route added later is protected without anyone remembering to. Only the exact
paths in PUBLIC answer without a session. Pure ASGI, so it can read a form body and hand it on unchanged.
"""

from __future__ import annotations

import secrets
from collections.abc import Awaitable, Callable, MutableMapping
from http.cookies import SimpleCookie
from typing import Any
from urllib.parse import parse_qs, urlsplit

from sqlalchemy import Engine
from sqlalchemy.orm import Session

from po_dashboard.auth import service
from po_dashboard.config import Settings
from po_dashboard.db.models import AppUser

Scope = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[MutableMapping[str, Any]]]
Send = Callable[[MutableMapping[str, Any]], Awaitable[None]]

SESSION_COOKIE = "po_session"
SIGN_IN_COOKIE = "po_csrf"  # double-submit token for the sign-in form, where no session exists yet
PUBLIC = frozenset({"/sign-in", "/health", "/static/app.css"})
PENDING_ALLOWED = frozenset({"/change-password", "/sign-out", "/static/app.css"})
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
MAX_FORM = 64 * 1024
CSP = (
    "default-src 'self'; frame-ancestors 'none'; base-uri 'none'; object-src 'none'; form-action 'self'"
)
HEADERS = {
    b"content-security-policy": CSP.encode(),
    b"x-content-type-options": b"nosniff",
    b"referrer-policy": b"no-referrer",
}


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

        async def respond(status: int, body: bytes = b"", extra: list[tuple[bytes, bytes]] | None = None) -> None:
            out = [(b"content-type", b"text/plain; charset=utf-8"), (b"content-length", str(len(body)).encode())]
            await self._send_with_headers(send, path, status, out + (extra or []))
            await send({"type": "http.response.body", "body": body})

        host = headers.get(b"host", b"").decode("latin-1").lower()
        hostname = host.rsplit(":", 1)[0].strip("[]") if not host.startswith("[") else host.split("]")[0].strip("[")
        if hostname not in self.settings.accepted_hosts:
            await respond(400, b"Unknown host name.")
            return

        method = scope["method"]
        state = scope.setdefault("state", {})
        token = cookie_value(headers, SESSION_COOKIE)
        user: AppUser | None = None
        if token:
            with Session(self.engine) as db:
                row = service.resolve_session(db, token)
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

        async def wrapped_send(message: MutableMapping[str, Any]) -> None:
            if message["type"] == "http.response.start":
                message["headers"] = self._headers(path, list(message.get("headers", [])))
            await send(message)

        await self.app(scope, receive, wrapped_send)

    async def _deny(self, scope: Scope, headers: dict[bytes, bytes], respond: Any) -> None:
        wants_page = b"text/html" in headers.get(b"accept", b"") and b"hx-request" not in headers
        if wants_page and scope["method"] in SAFE_METHODS:
            await respond(303, b"", [(b"location", b"/sign-in")])
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
        if path != "/static/app.css" and b"cache-control" not in names:
            out.append((b"cache-control", b"no-store"))
        return out

    async def _send_with_headers(self, send: Send, path: str, status: int, headers: list[tuple[bytes, bytes]]) -> None:
        await send({"type": "http.response.start", "status": status, "headers": self._headers(path, headers)})
