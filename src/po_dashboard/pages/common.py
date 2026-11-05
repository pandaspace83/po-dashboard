"""What every page handler needs: a database session, the signed-in person, and template context."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Annotated, Any

from fastapi import Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from po_dashboard.auth.gate import SESSION_COOKIE
from po_dashboard.config import Settings
from po_dashboard.db.models import AppUser


def get_db(request: Request) -> Iterator[Session]:
    with Session(request.app.state.engine) as db:
        yield db


Db = Annotated[Session, Depends(get_db)]


def current_user(request: Request, db: Db) -> AppUser:
    """The signed-in person, read from the database on every request so a demotion applies at once."""
    user_id = request.scope.get("state", {}).get("user_id")
    user = db.get(AppUser, user_id) if user_id else None
    if user is None or user.disabled_at is not None:
        raise HTTPException(401)
    return user


CurrentUser = Annotated[AppUser, Depends(current_user)]


def require_admin(user: CurrentUser) -> AppUser:
    if user.role != "admin":
        raise HTTPException(403, "Admins only.")
    return user


Admin = Annotated[AppUser, Depends(require_admin)]


def context(request: Request, **extra: Any) -> dict[str, Any]:
    state = request.scope.get("state", {})
    return {"csrf_token": state.get("csrf_token"), "user": getattr(request.state, "user", None), **extra}


def render(request: Request, name: str, user: AppUser, status: int = 200, **extra: Any) -> HTMLResponse:
    """Render a signed-in page: the person is always passed as `me`, which the top bar needs."""
    templates: Jinja2Templates = request.app.state.templates
    return templates.TemplateResponse(request, name, context(request, me=user, **extra), status_code=status)


def client_address(request: Request) -> str | None:
    return request.client.host if request.client else None


def set_session_cookie(response: Response, settings: Settings, token: str) -> None:
    response.set_cookie(SESSION_COOKIE, token, httponly=True, samesite="strict", secure=settings.https, path="/")
