"""Failed documents (PD-4.0a, DEC-54): the documents the model could not read, why, and Retry or Dismiss. Any signed-in
person may use it (DEC-29); every action is audited."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from po_dashboard.db.models import AppUser
from po_dashboard.extraction import queue
from po_dashboard.ledger.errors import LedgerError, parse_seen
from po_dashboard.pages import tables
from po_dashboard.pages.common import CurrentUser, Db, render

router = APIRouter(prefix="/documents")


def _show(request: Request, db: Db, user: AppUser, error: str | None = None, status: int = 200) -> HTMLResponse:
    page = tables.page_of(queue.failed_list(db), request.query_params.get("page"))
    return render(request, "failed_documents.html", user, status, page=page, error=error)


@router.get("/failed", response_class=HTMLResponse)
def failed(request: Request, db: Db, user: CurrentUser) -> HTMLResponse:
    return _show(request, db, user)


@router.post("/failed/{extraction_id}/retry", response_class=HTMLResponse, response_model=None)
def retry(
    request: Request, db: Db, user: CurrentUser, extraction_id: int, version: Annotated[str, Form()] = ""
) -> HTMLResponse | RedirectResponse:
    try:
        queue.retry(db, extraction_id, parse_seen(version), user)
        db.commit()
    except LedgerError as problem:
        db.rollback()
        return _show(request, db, user, str(problem), 400)
    return RedirectResponse("/documents/failed", status_code=303)


@router.post("/failed/{extraction_id}/dismiss", response_class=HTMLResponse, response_model=None)
def dismiss(
    request: Request, db: Db, user: CurrentUser, extraction_id: int,
    version: Annotated[str, Form()] = "", reason: Annotated[str, Form()] = "",
) -> HTMLResponse | RedirectResponse:  # fmt: skip
    try:
        queue.dismiss(db, extraction_id, parse_seen(version), reason, user)
        db.commit()
    except LedgerError as problem:
        db.rollback()
        return _show(request, db, user, str(problem), 400)
    return RedirectResponse("/documents/failed", status_code=303)
