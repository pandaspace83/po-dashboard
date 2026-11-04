"""Admin: people. Every route here needs the admin role, read from the database on each request."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select

from po_dashboard.auth import passwords, service
from po_dashboard.db.models import AppUser
from po_dashboard.pages.common import Admin, Db, context, require_admin

router = APIRouter(prefix="/admin", dependencies=[Depends(require_admin)])


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
    return _render(request, db, admin)


@router.post("/users/create", response_class=HTMLResponse)
def create(
    request: Request, db: Db, admin: Admin, email: Annotated[str, Form()], role: Annotated[str, Form()]
) -> HTMLResponse:
    try:
        user, temporary = service.create_user(db, email, role, actor=admin)
    except (service.AuthError, passwords.PasswordRejected) as problem:
        return _render(request, db, admin, error=str(problem), status=400)
    # Rendered straight into this response and never kept, so the temporary password is shown exactly once.
    return _render(request, db, admin, shown=(user.email, temporary or ""))


@router.post("/users/{user_id}/{action}", response_class=HTMLResponse)
def act(
    request: Request, db: Db, admin: Admin, user_id: int, action: str,
    role: Annotated[str | None, Form()] = None,
) -> HTMLResponse:  # fmt: skip
    target = _target(db, user_id)
    if target is None or action not in {"reset", "unlock", "disable", "enable", "role"}:
        return _render(request, db, admin, error="That person or action was not found.", status=404)
    try:
        if action == "reset":
            return _render(request, db, admin, shown=(target.email, service.reset_password(db, target, actor=admin)))
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
    return _render(request, db, admin)
