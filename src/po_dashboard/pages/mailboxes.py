"""The Mailboxes screen (PD-3.7, REQ-IN-07, DEC-50): connect, reconnect and disconnect the one mailbox.

Signing in happens on Google's own page and Google sends the person back to this app on the loopback address
(`http://127.0.0.1:<port>/mailboxes/callback`), which is the only kind of return address Google allows for an app like
this. So connecting works from a browser on the office Mac itself; from a laptop the page says so.

Three things shape the flow (ARCHITECTURE section 7):
- The session cookie is `SameSite=Strict`, so it is not sent when Google's page sends the browser back. The callback is
  therefore public: it only checks that the one-time `state` belongs to a sign-in started here, keeps the code in
  memory and hands over, by a page the browser then follows from this site, to `/mailboxes/finish`, which needs the
  signed-in admin who started it.
- The page's security policy (`form-action 'self'`) and HTMX would stop a form post or boosted link from leaving for
  Google. Connect therefore shows one plain link, "Continue to Google".
- Everything pending lives in memory, for ten minutes, once: a restart means starting again. The code, the PKCE
  verifier and the state are never written to the database, a log or the page (DEC-13).
"""

from __future__ import annotations

import secrets
import threading
import time
from dataclasses import dataclass
from typing import Annotated

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import Session

from po_dashboard.auth import service
from po_dashboard.config import is_loopback_host
from po_dashboard.db.mail import Mailbox, ScanState
from po_dashboard.db.models import AppUser
from po_dashboard.display import day
from po_dashboard.mail import gmail_auth, gmail_connect
from po_dashboard.pages.common import Admin, Db, context, render, require_admin

router = APIRouter(prefix="/mailboxes", dependencies=[Depends(require_admin)])
public = APIRouter(prefix="/mailboxes")  # the callback only; see the module note

PENDING_SECONDS = 600
MAX_PENDING = 20
FLASH_SECONDS = 120
PROVIDERS = {"gmail": "Gmail", "graph": "Microsoft 365", "folder": "A folder of mail files"}
STATES = {"connected": "connected", "needs_reconnect": "needs reconnect", "disconnected": "disconnected"}

_lock = threading.Lock()


@dataclass
class Pending:
    admin_id: int
    verifier: str
    redirect: str
    url: str  # Google's sign-in address, shown only as the Continue link
    days: int
    expires: float
    code: str | None = None
    error: bool = False


_pending: dict[str, Pending] = {}  # by `state`
_flash: dict[int, tuple[float, bool, str]] = {}  # by admin id: (expires, is_error, words)


def _purge(now: float) -> None:
    for state in [s for s, p in _pending.items() if p.expires <= now]:
        del _pending[state]
    for admin in [a for a, (until, _, _) in _flash.items() if until <= now]:
        del _flash[admin]


def _say(admin: AppUser, words: str, *, error: bool = False) -> RedirectResponse:
    """Words for the next view of the page. The form post answers with a redirect, so Reload never repeats an action."""
    with _lock:
        _flash[admin.id] = (time.monotonic() + FLASH_SECONDS, error, words)
    return RedirectResponse("/mailboxes", status_code=303)


def _pending_of(admin: AppUser) -> tuple[str, Pending] | None:
    with _lock:
        _purge(time.monotonic())
        return next(((s, p) for s, p in _pending.items() if p.admin_id == admin.id), None)


def _how_connect_works(request: Request) -> str | None:
    """Why this browser cannot connect, in plain words, or None if it can."""
    settings = request.app.state.settings
    if settings.https:
        return ("Connecting is done in Terminal while HTTPS is on, because Google only sends the browser back to an "
                "address on this Mac that starts with http://.")  # fmt: skip
    if not is_loopback_host(request.url.hostname or ""):
        return (f"Open this page on the office Mac itself, at http://127.0.0.1:{settings.port}/mailboxes, to connect: "
                "Google sends the browser back to the Mac you are on.")  # fmt: skip
    return None


def _rows(db: Session) -> list[dict[str, object]]:
    rows = []
    for box in db.scalars(select(Mailbox).order_by(Mailbox.address)):
        scans = list(db.scalars(select(ScanState).where(ScanState.mailbox_id == box.id)))
        done = [s.last_success for s in scans if s.last_success is not None]
        who = db.get(AppUser, box.connected_by) if box.connected_by else None
        rows.append({
            "box": box, "provider": PROVIDERS.get(box.provider, box.provider), "state": STATES[box.state],
            "by": who.email if who else None,
            "last_check": min(done) if len(done) == len(scans) and done else None,
            "problem": next((s.last_error for s in scans if s.last_error), None),
        })  # fmt: skip
    return rows


def _show(request: Request, db: Db, admin: AppUser, status: int = 200) -> HTMLResponse:
    with _lock:
        _purge(time.monotonic())
        _, error, words = _flash.pop(admin.id, (0.0, False, ""))
    active = db.scalars(select(Mailbox).where(Mailbox.provider == "gmail", Mailbox.state != "disconnected")).all()
    return render(request, "mailboxes.html", admin, status, rows=_rows(db), said=words, said_is_error=error,
                  can_connect=all(box.state == "needs_reconnect" for box in active),  # none, or one that has lapsed
                  reconnecting=bool(active), cannot=_how_connect_works(request))  # fmt: skip


@router.get("", response_class=HTMLResponse)
def mailboxes(request: Request, db: Db, admin: Admin) -> HTMLResponse:
    return _show(request, db, admin)


@router.post("/connect", response_class=HTMLResponse, response_model=None)
def connect(request: Request, admin: Admin, days: Annotated[str, Form()] = "0") -> RedirectResponse:
    if (cannot := _how_connect_works(request)) is not None:
        return _say(admin, cannot, error=True)
    try:
        number = int(days.strip() or "0") if days.strip().isascii() else -1
        gmail_connect.check_days(number)
    except (ValueError, gmail_auth.GmailError):
        return _say(admin, "The number of days must be a whole number from 0 to 365.", error=True)
    try:
        client = gmail_auth.load_client(request.app.state.secrets)
    except gmail_auth.GmailError as problem:
        return _say(admin, str(problem), error=True)
    state, verifier = secrets.token_urlsafe(32), gmail_auth.new_verifier()
    redirect = f"http://{request.url.hostname}:{request.url.port or request.app.state.settings.port}"
    redirect += gmail_auth.CALLBACK_PATH
    with _lock:
        now = time.monotonic()
        _purge(now)
        for old in [s for s, p in _pending.items() if p.admin_id == admin.id]:
            del _pending[old]  # one sign-in at a time per person: starting again replaces the last
        if len(_pending) >= MAX_PENDING:
            return _say(admin, "Too many sign-ins are waiting. Try again in a few minutes.", error=True)
        url = gmail_auth.auth_url(client, redirect, state, verifier)
        _pending[state] = Pending(admin.id, verifier, redirect, url, number, now + PENDING_SECONDS)
    return RedirectResponse("/mailboxes/continue", status_code=303)


@router.get("/continue", response_class=HTMLResponse, response_model=None)
def continue_to_google(request: Request, admin: Admin) -> HTMLResponse | RedirectResponse:
    found = _pending_of(admin)
    if found is None or found[1].code is not None:
        return _say(admin, "That sign-in has expired. Start again with Connect.", error=True)
    return render(request, "mailbox_continue.html", admin, google=found[1].url)


@public.get("/callback", response_class=HTMLResponse)
def callback(request: Request, state: str = "", code: str = "", error: str = "") -> HTMLResponse:
    """Google's return. Public (see the module note) and so strict: it needs a `state` we issued, once, in time."""
    templates: Jinja2Templates = request.app.state.templates
    with _lock:
        _purge(time.monotonic())
        found = _pending.get(state[:100])
        if found is None or found.code is not None or found.error or not (code or error):
            return templates.TemplateResponse(
                request, "mailbox_returned.html", context(request, ok=False), status_code=400
            )
        found.code, found.error = (code[:2000] or None), bool(error) or not code
    return templates.TemplateResponse(request, "mailbox_returned.html", context(request, ok=True))


@router.get("/finish", response_class=HTMLResponse, response_model=None)
def finish(request: Request, db: Db, admin: Admin) -> RedirectResponse:
    """The second half of connecting: the signed-in person who started it exchanges the code and saves the mailbox."""
    with _lock:
        _purge(time.monotonic())
        found = next(((s, p) for s, p in _pending.items() if p.admin_id == admin.id and (p.code or p.error)), None)
        if found is not None:
            del _pending[found[0]]  # once
    if found is None:
        return _say(admin, "No sign-in was waiting to be finished. Start again with Connect.", error=True)
    pending = found[1]
    if pending.error or not pending.code:
        return _say(admin, "Google did not allow the sign-in. Start again with Connect.", error=True)
    http = getattr(request.app.state, "google_http", None)
    keys = request.app.state.secrets
    try:
        client = gmail_auth.load_client(keys)
        tokens = gmail_auth.exchange_code(client, pending.code, pending.verifier, pending.redirect, http)
        box = gmail_connect.finish_connect(
            db, keys, tokens, days=pending.days, connected_by=admin.id, http=http, one_mailbox=True
        )
    except gmail_auth.GmailError as problem:
        return _say(admin, str(problem), error=True)
    service.audit(db, "mailbox.connect", admin, box.address, after={"provider": "gmail", "days": pending.days})
    db.commit()
    return _say(admin, f"Connected {box.address}. Mail from {day(box.read_since)} on will be read.")


@router.post("/{mailbox_id}/disconnect", response_class=HTMLResponse, response_model=None)
def disconnect(request: Request, db: Db, admin: Admin, mailbox_id: int) -> RedirectResponse:
    box = db.get(Mailbox, mailbox_id)
    if box is None or box.provider != "gmail":
        return _say(admin, "That mailbox was not found.", error=True)
    try:
        gmail_connect.disconnect(db, request.app.state.secrets, box.address)
    except gmail_auth.GmailError as problem:
        return _say(admin, str(problem), error=True)
    service.audit(db, "mailbox.disconnect", admin, box.address)
    db.commit()
    return _say(admin, f"Disconnected {box.address}. Mail already stored stays.")
