"""Sales invoices (Phase 8, DEC-58): upload a QuickBooks Desktop sales export, check how its columns were read, see
what it would do, then keep it. Everyone signed in can use it (DEC-56); every upload is audited and kept with its file,
its rows and the dates it covers.

Between the preview and the confirmation the file is held in this server's memory for the person who uploaded it, for
15 minutes, never on disk (as the QuickBooks purchasing load does). A used or expired preview is forgotten."""

from __future__ import annotations

import json
import secrets
import threading
import time
from collections import Counter
from typing import Annotated

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy import func, select
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile

from po_dashboard.customers import config
from po_dashboard.db.customers import (
    Customer,
    SalesImport,
    SalesImportRow,
    SalesInvoice,
    SalesInvoiceLine,
    SalesInvoiceObservation,
)
from po_dashboard.db.models import AppUser
from po_dashboard.intake.store import FileStore
from po_dashboard.ledger.errors import LedgerError, when
from po_dashboard.ledger.sheets import MAX_BYTES
from po_dashboard.pages import tables
from po_dashboard.pages.common import CurrentUser, Db, client_address, render
from po_dashboard.quickbooks.importers import mapping as mp
from po_dashboard.quickbooks.importers.sales import ImportProblem
from po_dashboard.quickbooks.reconciliation import store as reconciliation
from po_dashboard.quickbooks.snapshots import imports

router = APIRouter(prefix="/sales-invoices")
Field = Annotated[str, Form()]

HOLD_SECONDS = 15 * 60
UPLOAD_SLACK = 64 * 1024
SHOWN = 50  # invoices listed in a preview (the counts above cover all of them)
STATES = {"accepted": "Accepted", "needs_review": "Needs review"}
KINDS = {"invoice": "Invoice", "credit_memo": "Credit memo"}

_lock = threading.Lock()
_held: dict[int, tuple[float, str, bytes, str]] = {}  # person -> (expires, token, file, file name)


def _hold(user: AppUser, raw: bytes, filename: str, token: str | None = None) -> str:
    token, now = token or secrets.token_urlsafe(16), time.monotonic()
    with _lock:
        for key in [key for key, value in _held.items() if value[0] < now]:
            del _held[key]
        _held[user.id] = (now + HOLD_SECONDS, token, raw, filename)
    return token


def _peek(user: AppUser, token: str, *, take: bool = False) -> tuple[bytes, str] | None:
    with _lock:
        found = _held.get(user.id)
        if found is None or found[0] < time.monotonic() or not secrets.compare_digest(found[1], token):
            return None
        if take:
            del _held[user.id]
    return found[2], found[3]


def _store(request: Request) -> FileStore:
    return FileStore(request.app.state.settings.data_dir / "originals")


def _count(value: str | None) -> int | None:
    return int(value) if value and value.isascii() and value.isdigit() and len(value) < 6 else None


# ---- the list and the upload history


@router.get("", response_class=HTMLResponse)
def invoice_list(request: Request, db: Db, user: CurrentUser) -> HTMLResponse:
    q = request.query_params.get("q", "").strip()[:100].lower()
    kind, state = request.query_params.get("kind", ""), request.query_params.get("state", "")
    unmapped_only = request.query_params.get("unmapped") == "1"
    customers = {c.id: c.name for c in db.scalars(select(Customer))}
    rows = []
    for inv in db.scalars(select(SalesInvoice).order_by(SalesInvoice.invoice_date.desc(), SalesInvoice.id.desc())):
        shown = customers.get(inv.customer_id) if inv.customer_id else None
        if (
            q
            and q not in inv.invoice_number.lower()
            and q not in (shown or inv.customer_name_exported or "").lower()
            and q not in (inv.po_reference or "").lower()
        ):
            continue
        if kind in KINDS and inv.kind != kind or state in STATES and inv.state != state:
            continue
        if unmapped_only and inv.customer_id is not None:
            continue
        rows.append((inv, shown))
    page = tables.page_of(rows, request.query_params.get("page"))
    history = list(db.scalars(select(SalesImport).order_by(SalesImport.id.desc()).limit(10)))
    who = {u.id: u.email for u in db.scalars(select(AppUser))}
    unmapped = db.scalar(select(func.count()).select_from(SalesInvoice).where(SalesInvoice.customer_id.is_(None)))
    flash = _count(request.query_params.get("imported"))
    return render(
        request,
        "sales_invoices.html",
        user,
        rows=page.rows,
        page=page,
        history=history,
        who=who,
        flash=flash,
        f={"q": q, "kind": kind, "state": state, "unmapped": unmapped_only},
        kinds=KINDS,
        states=STATES,
        unmapped=unmapped or 0,
        counts={h.id: json.loads(h.counts or "{}") for h in history},
    )


# ---- upload, check the mapping, confirm


def _upload_page(request: Request, user: CurrentUser, status: int = 200, **extra: object) -> HTMLResponse:
    context: dict[str, object] = {"error": None, "plan": None, "token": ""} | extra
    return render(request, "sales_upload.html", user, status, **context)


@router.get("/upload", response_class=HTMLResponse)
def upload_page(request: Request, user: CurrentUser) -> HTMLResponse:
    return _upload_page(request, user)


def _preview(
    request: Request,
    db: Db,
    user: CurrentUser,
    raw: bytes,
    filename: str,
    token: str,
    posted: dict[str, str] | None = None,
    status: int = 200,
    error: str | None = None,
) -> HTMLResponse:
    """The preview of what `raw` would do. With `posted`, the mapping the person chose; else the one the headers suggest
    (or a saved one that suits)."""
    problems: list[str] = []
    chosen: mp.Mapping | None = None
    try:
        _, headers, _row = imports.read_file(raw, filename)
        if posted is not None:
            chosen, problems = mp.from_form(
                {k.removeprefix("map_"): v for k, v in posted.items() if k.startswith("map_")}, headers
            )
        else:
            chosen = next((m for m in mp.profiles(db).values() if mp.usable(m, headers)), None)
        if problems:
            return _upload_page(
                request,
                user,
                400,
                error=" ".join(problems),
                headers=headers,
                token=token,
                filename=filename,
                fields=mp.FIELDS,
                labels=mp.LABELS,
                current=posted or {},
            )
        plan = imports.build_plan(db, raw, filename, chosen)
    except ImportProblem as problem:
        return _upload_page(request, user, 400, error=str(problem))
    start, end = plan.parsed.period
    period = {
        "from": (posted or {}).get("period_from") or (start.isoformat() if start else ""),
        "to": (posted or {}).get("period_to") or (end.isoformat() if end else ""),
    }
    return _upload_page(
        request,
        user,
        status,
        plan=plan,
        token=token,
        filename=filename,
        headers=plan.headers,
        fields=mp.FIELDS,
        error=error,
        labels=mp.LABELS,
        required=mp.REQUIRED,
        current=plan.parsed.mapping.fields,
        period=period,
        shown=SHOWN,
        reasons=plan.parsed.unreadable_reasons().most_common(8),
        profiles=sorted(mp.profiles(db)),
        remember=(posted or {}).get("remember", ""),
        customers=bool(plan.unknown_names),
    )


@router.post("/upload", response_class=HTMLResponse, response_model=None)
async def upload(request: Request, db: Db, user: CurrentUser) -> HTMLResponse:
    length = request.headers.get("content-length", "")
    if not length.isdigit():
        return _upload_page(request, user, 411, error="The file could not be read: its size was not stated.")
    if int(length) > MAX_BYTES + UPLOAD_SLACK:
        return _upload_page(request, user, 413, error=f"The file is larger than {MAX_BYTES // (1024 * 1024)} MB.")
    form = await request.form(max_files=1, max_fields=4)
    sent = form.get("file")
    if not isinstance(sent, UploadFile):
        return _upload_page(request, user, 400, error="Choose the QuickBooks sales export first.")
    raw = await sent.read(MAX_BYTES + 1)
    filename = (sent.filename or "")[:200]
    token = _hold(user, raw, filename)
    return await run_in_threadpool(_preview, request, db, user, raw, filename, token)


@router.post("/preview", response_class=HTMLResponse, response_model=None)
async def reshow(request: Request, db: Db, user: CurrentUser) -> HTMLResponse:
    """The same file again with the mapping or the dates the person chose."""
    form = {k: str(v) for k, v in (await request.form(max_fields=60)).items()}
    held = _peek(user, form.get("token", ""))
    if held is None:
        return _upload_page(
            request, user, 400, error="That preview has expired or was already used. Upload the file again."
        )
    _hold(user, held[0], held[1], form["token"])  # another 15 minutes
    return await run_in_threadpool(_preview, request, db, user, held[0], held[1], form["token"], form)


@router.post("/confirm", response_class=HTMLResponse, response_model=None)
async def confirm(request: Request, db: Db, user: CurrentUser) -> Response:
    form = {k: str(v) for k, v in (await request.form(max_fields=60)).items()}
    held = _peek(user, form.get("token", ""), take=True)
    if held is None:
        return _upload_page(
            request, user, 400, error="That preview has expired or was already used. Upload the file again."
        )
    raw, filename = held
    try:
        _, headers, _row = imports.read_file(raw, filename)
        chosen, problems = mp.from_form(
            {k.removeprefix("map_"): v for k, v in form.items() if k.startswith("map_")}, headers
        )
        if problems:
            raise LedgerError(" ".join(problems))
        report = await run_in_threadpool(
            lambda: imports.apply_import(
                db,
                _store(request),
                raw,
                filename,
                chosen,
                period_from=when(form.get("period_from"), "From"),
                period_to=when(form.get("period_to"), "To"),
                actor=user,
                client_address=client_address(request),
                save_mapping_as=form.get("remember", "").strip() or None,
            )
        )
        reconciliation.run(db, cause="upload", cause_ref=report.record.id)
        db.commit()
    except ImportProblem as problem:
        db.rollback()
        return _upload_page(request, user, 400, error=str(problem))
    except (LedgerError, ValueError) as problem:
        db.rollback()
        _hold(user, raw, filename, form.get("token"))  # so the person can fix the dates and try again
        return _preview(request, db, user, raw, filename, form.get("token", ""), form, 400, str(problem))
    return RedirectResponse(f"/sales-invoices?imported={report.counts['new']}", status_code=303)


# ---- one invoice, one upload


def _names(request: Request, db: Db, user: CurrentUser, error: str | None = None, status: int = 200) -> HTMLResponse:
    return render(request, "sales_names.html", user, status, names=imports.unmapped_names(db), error=error)


@router.get("/names", response_class=HTMLResponse)
def names_page(request: Request, db: Db, user: CurrentUser) -> HTMLResponse:
    return _names(request, db, user)


@router.post("/names/map", response_class=HTMLResponse, response_model=None)
async def map_name(request: Request, db: Db, user: CurrentUser) -> Response:
    form = {k: str(v) for k, v in (await request.form(max_fields=20)).items()}
    name = form.get("name", "")
    try:
        imports.map_name(
            db,
            name,
            customer_id=_count(form.get("customer_id")),
            new_customer_name=form.get("new_customer", ""),
            actor=user,
            client_address=client_address(request),
        )
        reconciliation.run(db, cause="person")
        db.commit()
    except LedgerError as problem:
        db.rollback()
        return _names(request, db, user, str(problem), 400)
    return RedirectResponse("/sales-invoices/names", status_code=303)


@router.get("/imports/{import_id}", response_class=HTMLResponse)
def import_page(request: Request, db: Db, user: CurrentUser, import_id: int) -> HTMLResponse:
    batch = db.get(SalesImport, import_id)
    if batch is None:
        return render(request, "error.html", user, 404, title="Page not found", message="That upload was not found.")
    rows = list(
        db.scalars(
            select(SalesImportRow).where(SalesImportRow.import_id == import_id).order_by(SalesImportRow.row_number)
        )
    )
    outcome = request.query_params.get("outcome", "")
    shown = [r for r in rows if not outcome or r.outcome == outcome]
    page = tables.page_of([(r, json.loads(r.cells)) for r in shown], request.query_params.get("page"))
    maker = db.get(AppUser, batch.created_by) if batch.created_by else None
    return render(
        request,
        "sales_import_detail.html",
        user,
        batch=batch,
        page=page,
        counts=json.loads(batch.counts or "{}"),
        mapping=json.loads(batch.mapping),
        maker=maker.email if maker else "",
        labels=mp.LABELS,
        outcome=outcome,
    )


@router.get("/imports/{import_id}/file")
def import_file(request: Request, db: Db, user: CurrentUser, import_id: int) -> Response:
    """The original file as uploaded, as a download (never shown inside the app's own page)."""
    batch = db.get(SalesImport, import_id)
    if batch is None:
        return Response("That upload was not found.", status_code=404, media_type="text/plain")
    name = "".join(ch for ch in batch.file_name if ch.isalnum() or ch in "._- ")[:100] or "sales-export"
    return Response(
        _store(request).read(batch.stored_path),
        media_type="application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{name}"', "X-Content-Type-Options": "nosniff"},
    )


@router.get("/{invoice_id}", response_class=HTMLResponse)
def invoice_page(request: Request, db: Db, user: CurrentUser, invoice_id: int) -> HTMLResponse:
    invoice = db.get(SalesInvoice, invoice_id)
    if invoice is None:
        return render(request, "error.html", user, 404, title="Page not found", message="That invoice was not found.")
    customer = db.get(Customer, invoice.customer_id) if invoice.customer_id else None
    lines = list(
        db.scalars(
            select(SalesInvoiceLine)
            .where(SalesInvoiceLine.invoice_id == invoice.id)
            .order_by(SalesInvoiceLine.position)
        )
    )
    seen = []
    for obs in db.scalars(
        select(SalesInvoiceObservation)
        .where(SalesInvoiceObservation.invoice_id == invoice.id)
        .order_by(SalesInvoiceObservation.id)
    ):
        batch = db.get(SalesImport, obs.import_id)
        content = json.loads(obs.content)
        seen.append(
            {
                "obs": obs,
                "batch": batch,
                "review": content.get("review", []),
                "notes": content.get("notes", []),
                "total": content["record"]["total"],
            }
        )
    return render(
        request,
        "sales_invoice_detail.html",
        user,
        invoice=invoice,
        customer=customer,
        lines=lines,
        seen=seen,
        counts=Counter(s["obs"].outcome for s in seen),
        default_currency=config.text(db, "default_currency"),
    )


__all__ = ["router"]
