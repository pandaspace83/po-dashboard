"""Refusals and missing pages as pages a person can read (DEF-002), not raw JSON. A script that does not ask for HTML
still gets the short JSON answer, as before."""

from __future__ import annotations

from collections.abc import Mapping

from fastapi import Request
from fastapi.exception_handlers import http_exception_handler, request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import Response
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from starlette.exceptions import HTTPException

from po_dashboard.db.models import AppUser
from po_dashboard.pages.common import context

MESSAGES = {
    403: ("Admins only", "This page is for admins. Ask an admin if you need something from it."),
    404: (
        "Page not found",
        "There is nothing at this address. It may have been typed wrong, or what it showed is gone.",
    ),
    405: ("That cannot be done here", "This address does not take that kind of request. Use the page's own buttons."),
    422: ("The form could not be read", "Something the form needs was missing or unreadable. Go back and try again."),
}
OTHER = ("That did not work", "Go back and try again. If it happens again, tell an admin what you were doing.")


def wants_page(request: Request) -> bool:
    return "text/html" in request.headers.get("accept", "") or request.headers.get("hx-request") == "true"


def _person(request: Request) -> AppUser | None:
    """The signed-in person for the top bar; none while a temporary password is still to be changed."""
    state = request.scope.get("state", {})
    if not state.get("user_id") or state.get("stage") == "pending":
        return None
    with Session(request.app.state.engine) as db:
        return db.get(AppUser, state["user_id"])


def _page(request: Request, status: int, headers: Mapping[str, str] | None = None) -> Response:
    title, message = MESSAGES.get(status, OTHER)
    templates: Jinja2Templates = request.app.state.templates
    page = context(request, me=_person(request), title=title, message=message, status=status)
    return templates.TemplateResponse(request, "error.html", page, status_code=status, headers=headers)


async def http_error(request: Request, problem: Exception) -> Response:
    if not isinstance(problem, HTTPException):
        raise problem
    if problem.status_code == 401 or not wants_page(request):  # the gate deals with 401s; scripts get JSON
        return await http_exception_handler(request, problem)
    return _page(request, problem.status_code, problem.headers)


async def validation_error(request: Request, problem: Exception) -> Response:
    if not isinstance(problem, RequestValidationError):
        raise problem
    if not wants_page(request):
        return await request_validation_exception_handler(request, problem)
    return _page(request, 422)
