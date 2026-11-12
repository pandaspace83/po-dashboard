"""Sign-in, sign-out, change password and the home page."""

from __future__ import annotations

import secrets
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import Session

from po_dashboard import __version__
from po_dashboard.auth import passwords, service
from po_dashboard.auth.gate import SESSION_COOKIE, SIGN_IN_COOKIE, cookie_value
from po_dashboard.db.base import utcnow
from po_dashboard.db.models import AppUser, SignInSession
from po_dashboard.extraction import queue
from po_dashboard.ledger import reports
from po_dashboard.models import llm
from po_dashboard.pages.common import (
    CurrentUser,
    Db,
    client_address,
    context,
    set_session_cookie,
)
from po_dashboard.scheduler.scanner import last_scan

router = APIRouter()


def _page(request: Request, name: str, status: int = 200, **extra: object) -> HTMLResponse:
    templates: Jinja2Templates = request.app.state.templates
    return templates.TemplateResponse(request, name, context(request, **extra), status_code=status)


@router.get("/sign-in", response_class=HTMLResponse)
def sign_in_page(request: Request, db: Db) -> Response:
    state = request.scope.get("state", {})
    if state.get("user_id"):
        return RedirectResponse("/change-password" if state.get("stage") == "pending" else "/", status_code=303)
    headers = {k.lower(): v for k, v in request.scope["headers"]}
    token = cookie_value(headers, SIGN_IN_COOKIE) or secrets.token_urlsafe(32)
    no_accounts = db.scalar(select(AppUser.id).limit(1)) is None
    response = _page(request, "sign_in.html", form_token=token, no_accounts=no_accounts, error=None)
    secure = request.app.state.settings.https
    response.set_cookie(SIGN_IN_COOKIE, token, httponly=True, samesite="strict", secure=secure, path="/")
    return response


@router.post("/sign-in")
def sign_in(
    request: Request, db: Db, email: Annotated[str, Form()], password: Annotated[str, Form()]
) -> Response:
    settings = request.app.state.settings
    try:
        result = service.sign_in(db, email, password, client_address=client_address(request))
    except service.AuthError as problem:
        headers = {k.lower(): v for k, v in request.scope["headers"]}
        token = cookie_value(headers, SIGN_IN_COOKIE) or ""
        return _page(request, "sign_in.html", 401, form_token=token, no_accounts=False, error=str(problem))
    target = "/change-password" if result.session.stage == "pending" else "/"
    response = RedirectResponse(target, status_code=303)
    set_session_cookie(response, settings, result.token)
    response.delete_cookie(SIGN_IN_COOKIE)
    return response


@router.post("/sign-out")
def sign_out(request: Request, db: Db) -> Response:
    session_id = request.scope.get("state", {}).get("session_id")
    row = db.get(SignInSession, session_id) if session_id else None
    if row is not None:
        service.sign_out(db, row, client_address=client_address(request))
    response = RedirectResponse("/sign-in", status_code=303)
    response.delete_cookie(SESSION_COOKIE, path="/")
    return response


@router.get("/change-password", response_class=HTMLResponse)
def change_password_page(request: Request, user: CurrentUser) -> HTMLResponse:
    return _page(request, "change_password.html", me=user, error=None)


@router.post("/change-password")
def change_password(
    request: Request, db: Db, user: CurrentUser,
    current: Annotated[str, Form()], new: Annotated[str, Form()], again: Annotated[str, Form()],
) -> Response:  # fmt: skip
    settings = request.app.state.settings
    problem: str | None = None
    if new != again:
        problem = "The two new passwords did not match."
    else:
        try:
            # Ends every session of this person, including this one; a fresh session follows.
            service.change_password(db, user, current, new)
        except (service.AuthError, passwords.PasswordRejected) as error:
            problem = str(error)
    if problem:
        return _page(request, "change_password.html", 400, me=user, error=problem)
    fresh = service.start_session(db, user, utcnow())
    db.commit()
    response = RedirectResponse("/", status_code=303)
    set_session_cookie(response, settings, fresh.token)
    return response


def scan_text(db: Session) -> str:
    """The health strip's 'last scan': 'no scan yet', or how long ago in plain words."""
    when = last_scan(db)
    if when is None:
        return "no scan yet"
    minutes = int((utcnow() - when).total_seconds() // 60)
    return "just now" if minutes < 1 else f"{minutes} min ago" if minutes < 120 else f"{minutes // 60} hours ago"


def _queue_health(db: Session) -> dict[str, int]:
    counts = queue.waiting_counts(db)
    return {"waiting": counts["queued"] + counts["running"], "failed": counts["failed"]}


@router.get("/", response_class=HTMLResponse)
def home(request: Request, db: Db, user: CurrentUser) -> HTMLResponse:
    settings = request.app.state.settings
    health = {
        "last_scan": scan_text(db),
        "model": "reachable" if llm.is_reachable(settings) else "not reachable",
        "model_name": settings.model,
        **_queue_health(db),
    }
    today = date.today()
    items = reports.open_items(db, today)
    needing, needing_total = reports.attention(items)
    return _page(request, "home.html", me=user, health=health, version=__version__,
                 counts=reports.counts(db, today, items), needing=needing, needing_total=needing_total)  # fmt: skip
