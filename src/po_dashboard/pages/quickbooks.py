"""Purchase orders → Load from QuickBooks (DEC-46): upload the "Open POs" report, see what it would do, then create.

Between the preview and the create, the file is held in this server's memory for the person who uploaded it, for 15
minutes, never on disk. Create reads it again against the vendor list as it is then, so a vendor added in between
counts. Reload never creates anything twice: create answers with a redirect, and a used file is forgotten.
"""

from __future__ import annotations

import secrets
import threading
import time
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile

from po_dashboard.db.models import AppUser
from po_dashboard.ledger import quickbooks as qb
from po_dashboard.ledger.errors import LedgerError, when
from po_dashboard.ledger.sheets import MAX_BYTES
from po_dashboard.pages.common import CurrentUser, Db, client_address, render

router = APIRouter(prefix="/orders/quickbooks")
HOLD_SECONDS = 15 * 60
UPLOAD_SLACK = 64 * 1024

_lock = threading.Lock()
_held: dict[int, tuple[float, str, bytes, str]] = {}  # person -> (expires, token, file, file name)


def _hold(user: AppUser, raw: bytes, filename: str) -> str:
    token = secrets.token_urlsafe(16)
    now = time.monotonic()
    with _lock:
        for key in [key for key, value in _held.items() if value[0] < now]:
            del _held[key]
        _held[user.id] = (now + HOLD_SECONDS, token, raw, filename)
    return token


def _take(user: AppUser, token: str) -> tuple[bytes, str] | None:
    with _lock:
        found = _held.get(user.id)
        if found is None or found[0] < time.monotonic() or not secrets.compare_digest(found[1], token):
            return None
        del _held[user.id]
    return found[2], found[3]


def _page(request: Request, user: AppUser, status: int = 200, **extra: object) -> HTMLResponse:
    return render(request, "quickbooks_load.html", user, status, shown=qb.MAX_SHOWN, **extra)


@router.get("", response_class=HTMLResponse)
def load_page(request: Request, user: CurrentUser) -> HTMLResponse:
    return _page(request, user)


@router.post("", response_class=HTMLResponse, response_model=None)
async def preview(request: Request, db: Db, user: CurrentUser) -> HTMLResponse:
    length = request.headers.get("content-length", "")
    if not length.isdigit():
        return _page(request, user, 411, error="The file could not be read: its size was not stated.")
    if int(length) > MAX_BYTES + UPLOAD_SLACK:
        return _page(request, user, 413, error=f"The file is larger than {MAX_BYTES // (1024 * 1024)} MB.")
    form = await request.form(max_files=1, max_fields=4)
    upload = form.get("file")
    if not isinstance(upload, UploadFile):
        return _page(request, user, 400, error="Choose the QuickBooks report first.")
    raw = await upload.read(MAX_BYTES + 1)
    filename = (upload.filename or "")[:200]
    try:
        typed = when(str(form.get("export_date") or ""), "Export date")
    except LedgerError as problem:
        return _page(request, user, 400, error=str(problem))
    return await show_plan(request, db, user, raw, filename, typed)


async def show_plan(request: Request, db: Session, user: AppUser, raw: bytes, filename: str,
                    typed: date | None = None, *, handed_over: bool = False) -> HTMLResponse:  # fmt: skip
    """The preview of what the file would do, holding the file for Create. Import CSV or Excel uses it too, with
    handed_over, when the file it was given is this report."""
    filename = filename[:200]
    try:
        plan = await run_in_threadpool(qb.plan_load, db, raw, filename, typed)
    except qb.QuickBooksRejected as problem:
        return _page(request, user, 400, error=str(problem), own_format=problem.own_format)
    token = _hold(user, raw, filename) if plan.ready else ""
    return _page(request, user, plan=plan, token=token, filename=filename, handed_over=handed_over)


@router.post("/create", response_class=HTMLResponse, response_model=None)
def create(request: Request, db: Db, user: CurrentUser, token: Annotated[str, Form()] = "",
           export_date: Annotated[str, Form()] = "") -> Response:  # fmt: skip
    held = _take(user, token)
    if held is None:
        return _page(request, user, 400, error="That preview has expired or was already used. Upload the file again.")
    raw, filename = held
    try:
        exported: date | None = when(export_date, "Export date")
        plan = qb.plan_load(db, raw, filename, exported)
        if not plan.ready:
            return _page(request, user, 400, plan=plan, token="", filename=filename,
                         error="Nothing was created: the file no longer gives anything to create.")  # fmt: skip
        result = qb.apply_load(db, plan, actor=user, client_address=client_address(request))
        db.commit()
    except (LedgerError, qb.QuickBooksRejected) as problem:
        db.rollback()
        return _page(request, user, 400, error=f"Nothing was created. {problem}")
    return RedirectResponse(f"/orders?imported={len(result.created)}&waiting={result.waiting}&source=quickbooks",
                            status_code=303)  # fmt: skip
