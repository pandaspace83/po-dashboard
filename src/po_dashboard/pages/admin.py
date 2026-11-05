"""Admin: people. Every route here needs the admin role, read from the database on each request."""

from __future__ import annotations

import threading
import time
from typing import Annotated

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select

from po_dashboard.auth import passwords, service
from po_dashboard.db.models import AppUser
from po_dashboard.pages.common import Admin, Db, context, require_admin

router = APIRouter(prefix="/admin", dependencies=[Depends(require_admin)])

SHOWN_FOR_SECONDS = 60
_lock = threading.Lock()
_shown: dict[int, tuple[float, tuple[str, str]]] = {}


def _keep_once(admin: AppUser, email: str, temporary: str) -> RedirectResponse:
    """Hold a temporary password in memory only (never on disk, in a cookie or in the URL) for the next page view.

    The form post answers with a redirect, so Reload repeats a harmless page view, not the action (ARCHITECTURE §10.1).
    """
    with _lock:
        _shown[admin.id] = (time.monotonic() + SHOWN_FOR_SECONDS, (email, temporary))
    return RedirectResponse("/admin/users", status_code=303)


def _take_once(admin: AppUser) -> tuple[str, str] | None:
    with _lock:
        expires, value = _shown.pop(admin.id, (0.0, ("", "")))
    return value if time.monotonic() < expires else None


def _render(request: Request, db: Db, admin: AppUser, error: str | None = None, shown: tuple[str, str] | None = None,
            status: int = 200) -> HTMLResponse:  # fmt: skip
    people = db.scalars(select(AppUser).order_by(AppUser.email)).all()
    ctx = context(request, me=admin, people=people, error=error, shown=shown, roles=service.ROLES)
    templates: Jinja2Templates = request.app.state.templates
    return templates.TemplateResponse(request, "users.html", ctx, status_code=status)


def _target(db: Db, user_id: int) -> AppUser | None:
    return db.get(AppUser, user_id)


@router.get("/users", response_class=HTMLResponse)
def users(request: Request, db: Db, admin: Admin) -> HTMLResponse:
    return _render(request, db, admin, shown=_take_once(admin))


@router.post("/users/create", response_class=HTMLResponse, response_model=None)
def create(
    request: Request, db: Db, admin: Admin, email: Annotated[str, Form()], role: Annotated[str, Form()]
) -> HTMLResponse | RedirectResponse:
    try:
        user, temporary = service.create_user(db, email, role, actor=admin)
    except (service.AuthError, passwords.PasswordRejected) as problem:
        return _render(request, db, admin, error=str(problem), status=400)
    return _keep_once(admin, user.email, temporary or "")


@router.post("/users/{user_id}/{action}", response_class=HTMLResponse, response_model=None)
def act(
    request: Request, db: Db, admin: Admin, user_id: int, action: str,
    role: Annotated[str | None, Form()] = None,
) -> HTMLResponse | RedirectResponse:  # fmt: skip
    target = _target(db, user_id)
    if target is None or action not in {"reset", "unlock", "disable", "enable", "role"}:
        return _render(request, db, admin, error="That person or action was not found.", status=404)
    try:
        if action == "reset":
            return _keep_once(admin, target.email, service.reset_password(db, target, actor=admin))
        if action == "unlock":
            service.unlock(db, target, actor=admin)
        elif action == "disable":
            service.disable(db, target, actor=admin)
        elif action == "enable":
            service.enable(db, target, actor=admin)
        elif action == "role":
            service.set_role(db, target, role or "", actor=admin)
    except service.AuthError as problem:
        return _render(request, db, admin, error=str(problem), status=400)
    return RedirectResponse("/admin/users", status_code=303)
