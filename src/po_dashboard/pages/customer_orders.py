"""Customer orders (Phase 8): POs that customers send to RaceSpec. Entered by hand, imported from CSV or Excel, and
later read from email through the same intake service (DEC-57). Everyone signed in can use them (DEC-56); every action
is audited. A revision waits for a person; a PO whose customer is unknown waits too."""

from __future__ import annotations

import csv
import io
import json
import secrets
import threading
import time
from collections.abc import Callable
from datetime import date
from decimal import Decimal
from typing import Annotated, Any

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy import select
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile

from po_dashboard.customers import config, intake, listing, po_import
from po_dashboard.customers.intake import LineData, PoData
from po_dashboard.db.customers import (
    Allocation,
    Customer,
    CustomerPO,
    CustomerPOIntake,
    CustomerPOLine,
    ReconciliationEvent,
    SalesInvoice,
    SalesInvoiceLine,
)
from po_dashboard.db.models import AppUser
from po_dashboard.db.types import Money, Quantity
from po_dashboard.ledger.errors import LedgerError, number, parse_seen, text, when
from po_dashboard.ledger.orders import COMMON_CURRENCIES, ImportRejected, RowError
from po_dashboard.ledger.reports import safe_cell
from po_dashboard.ledger.sheets import MAX_BYTES
from po_dashboard.pages import tables
from po_dashboard.pages.common import CurrentUser, Db, client_address, render
from po_dashboard.quickbooks.reconciliation import decisions, views
from po_dashboard.quickbooks.reconciliation import store as reconciliation
from po_dashboard.quickbooks.reconciliation.engine import CODES, STATUSES

router = APIRouter(prefix="/customer-orders")
Field = Annotated[str, Form()]

FORM_ROWS = 10
MAX_FORM_ROWS = 100
LINE_FIELDS = ("item", "description", "quantity", "unit", "unit_price", "line_amount")
UPLOAD_SLACK = 64 * 1024
HOLD_SECONDS = 15 * 60
STATES = {"open": "Open", "revision": "A revision is waiting", "closed": "Closed", "cancelled": "Cancelled"}
FILED_WORDS = {"revision": "waits for approval as a new revision", "copy": "is already here with the same terms"}

_lock = threading.Lock()
_held: dict[int, tuple[float, str, bytes, str]] = {}  # person -> (expires, token, file, file name)


def _hold(user: AppUser, raw: bytes, filename: str) -> str:
    token, now = secrets.token_urlsafe(16), time.monotonic()
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


def _count(value: str | None) -> int | None:
    return int(value) if value and value.isascii() and value.isdigit() and len(value) < 6 else None


# ---- the list


def _statuses() -> dict[str, str]:
    return {**views.STATUS_WORDS, "revision": "A revision is waiting"} | {}


def _filtered(request: Request, db: Db) -> tuple[list[views.Row], dict[str, Any], str]:
    q = request.query_params.get("q", "").strip()[:100].lower()
    chosen = _count(request.query_params.get("customer"))
    state = request.query_params.get("state", "")
    status = request.query_params.get("status", "")
    rows = []
    for row in views.order_rows(db, reconciliation.current(db)):
        if q and q not in row.row.number.lower() and q not in row.row.customer.name.lower():
            continue
        if chosen is not None and row.row.customer.id != chosen:
            continue
        if state == "open" and row.row.order.state != "active":
            continue
        if state == "revision" and row.row.pending is None:
            continue
        if state in ("closed", "cancelled") and row.row.order.state != state:
            continue
        if status == "revision" and row.row.pending is None:
            continue
        if status and status != "revision" and row.status != status:
            continue
        rows.append(row)
    picked = db.get(Customer, chosen) if chosen is not None else None
    return rows, {"q": q, "customer_id": chosen, "state": state, "status": status}, picked.name if picked else ""


SORT_KEYS: dict[str, Callable[[Any], Any]] = {
    "po": lambda r: r.row.number.lower(),
    "customer": lambda r: r.row.customer.name.lower(),
    "date": lambda r: r.row.order.po_date,
    "total": lambda r: r.row.total,
    "lines": lambda r: r.row.lines,
    "state": lambda r: r.row.order.state,
    "status": lambda r: r.label,
    "invoiced": lambda r: (r.invoiced is None, r.invoiced or Decimal(0)),
    "remaining": lambda r: (r.remaining is None, r.remaining or Decimal(0)),
    "upload": lambda r: (r.last_upload is None, r.last_upload),
}


def _list(request: Request, db: Db, user: CurrentUser, error: str | None = None, status: int = 200) -> HTMLResponse:
    rows, f, customer_name = _filtered(request, db)
    ordered, sorting = tables.sort_rows(rows, request.query_params.get("sort"), SORT_KEYS, "-date")
    shown = tables.page_of(ordered, request.query_params.get("page"))
    waiting = db.scalar(select(CustomerPOIntake.id).where(CustomerPOIntake.state == "waiting").limit(1)) is not None
    flash = {k: _count(request.query_params.get(k)) for k in ("imported", "waiting", "skipped", "revisions")}
    return render(
        request,
        "customer_orders.html",
        user,
        status,
        rows=shown.rows,
        page=shown,
        sorting=sorting,
        f=f,
        customer_name=customer_name,
        states=STATES,
        statuses=_statuses(),
        error=error,
        waiting=waiting,
        flash=flash,
        exceptions=len(reconciliation.current(db).problems()),
    )


@router.get("", response_class=HTMLResponse)
def order_list(request: Request, db: Db, user: CurrentUser) -> HTMLResponse:
    return _list(request, db, user)


@router.get("/export.csv")
def order_csv(request: Request, db: Db, user: CurrentUser) -> Response:
    rows, _, _ = _filtered(request, db)
    ordered, _ = tables.sort_rows(rows, request.query_params.get("sort"), SORT_KEYS, "-date")
    out = io.StringIO(newline="")
    writer = csv.writer(out, lineterminator="\r\n")
    writer.writerow(views.CSV_COLUMNS)
    for line in views.csv_rows(ordered):
        writer.writerow(safe_cell(cell) for cell in line)
    return Response(
        b"\xef\xbb\xbf" + out.getvalue().encode("utf-8"),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="customer-orders.csv"'},
    )


# ---- the exception queue (DEC-59)


@router.get("/exceptions", response_class=HTMLResponse)
def exception_queue(request: Request, db: Db, user: CurrentUser) -> HTMLResponse:
    result = reconciliation.current(db)
    q = request.query_params
    chosen = _count(q.get("customer"))
    code, level, status = q.get("code", ""), q.get("level", ""), q.get("status", "")
    start, end = _day_param(q.get("from")), _day_param(q.get("to"))
    rows = []
    for row in views.queue(db, result):
        if chosen is not None and (row.customer is None or row.customer.id != chosen):
            continue
        if code in CODES and row.problem.code != code or level in views.LEVELS and row.problem.level != level:
            continue
        if status in STATUSES and row.status != status:
            continue
        if (
            (start or end)
            and row.day is None
            or start
            and row.day
            and row.day < start
            or end
            and row.day
            and row.day > end
        ):
            continue
        rows.append(row)
    rank = {"confirmed": 0, "preliminary": 1}
    rows.sort(key=lambda r: (rank[r.problem.level], r.customer.name.lower() if r.customer else "", r.number or ""))
    shown = tables.page_of(rows, q.get("page"))
    picked = db.get(Customer, chosen) if chosen is not None else None
    return render(
        request,
        "customer_exceptions.html",
        user,
        rows=shown.rows,
        page=shown,
        f={
            "customer_id": chosen,
            "code": code,
            "level": level,
            "status": status,
            "from": q.get("from", ""),
            "to": q.get("to", ""),
        },  # fmt: skip
        customer_name=picked.name if picked else "",
        codes=views.codes(),
        levels=views.LEVELS,
        statuses=STATUSES,
        total=len(result.problems()),
    )


def _day_param(value: str | None) -> date | None:
    try:
        return date.fromisoformat(value) if value else None
    except ValueError:
        return None


# ---- by hand


def _form_page(
    request: Request,
    db: Db,
    user: CurrentUser,
    header: dict[str, str],
    rows: list[dict[str, str]],
    errors: list[str] | None = None,
    status: int = 200,
) -> HTMLResponse:
    chosen = _count(header.get("customer_id"))
    picked = db.get(Customer, chosen) if chosen is not None else None
    return render(
        request,
        "customer_order_new.html",
        user,
        status,
        header=header,
        rows=rows,
        errors=errors or [],
        customer_name=picked.name if picked else "",
        currencies=COMMON_CURRENCIES,
    )


def _blank_rows(count: int = FORM_ROWS) -> list[dict[str, str]]:
    return [{name: "" for name in LINE_FIELDS} for _ in range(count)]


@router.get("/new", response_class=HTMLResponse)
def new_order(request: Request, db: Db, user: CurrentUser) -> HTMLResponse:
    header = {"currency": config.text(db, "default_currency")}
    return _form_page(request, db, user, header, _blank_rows())


@router.post("/create", response_class=HTMLResponse, response_model=None)
async def create(
    request: Request,
    db: Db,
    user: CurrentUser,
    po_number: Field,
    customer_id: Field = "",
    currency: Field = "",
    po_date: Field = "",
    stated_total: Field = "",
) -> Response:
    form = await request.form()
    columns = {name: [str(v) for v in form.getlist(f"line_{name}")] for name in LINE_FIELDS}
    header = {
        "po_number": po_number,
        "customer_id": customer_id,
        "currency": currency,
        "po_date": po_date,
        "stated_total": stated_total,
    }
    count = len(columns["item"])
    if any(len(values) != count for values in columns.values()) or count > MAX_FORM_ROWS or count == 0:
        return _form_page(
            request, db, user, header, _blank_rows(), ["The form was incomplete. Reload it and try again."], 400
        )
    rows = [{name: columns[name][i] for name in LINE_FIELDS} for i in range(count)]
    errors: list[str] = []
    lines: list[LineData] = []
    for position, row in enumerate(rows, start=1):
        if not any(value.strip() for value in row.values()):
            continue
        try:
            lines.append(
                LineData(
                    item=text(row["item"], "Item", longest=100, required=True) or "",
                    description=text(row["description"], "Description", longest=500, multiline=True),
                    qty=number(row["quantity"], "Quantity", Quantity, positive=True),
                    unit=text(row["unit"], "Unit", longest=16),
                    unit_price=number(row["unit_price"], "Unit price", Money, positive=False),
                    stated_amount=number(row["line_amount"], "Amount", Money, positive=False)
                    if row["line_amount"].strip()
                    else None,
                )
            )
        except LedgerError as problem:
            errors.append(f"Line {position}: {problem}")
    chosen = _count(customer_id)
    try:
        if chosen is None:
            raise LedgerError("Choose a customer. Add the customer first if it is not in the list.")
        data = PoData(
            po_number=po_number,
            currency=currency,
            lines=tuple(lines),
            customer_id=chosen,
            po_date=when(po_date, "PO date"),
            stated_total=number(stated_total, "Total", Money, positive=False) if stated_total.strip() else None,
            source="manual",
        )
    except LedgerError as problem:
        errors.insert(0, str(problem))
    if not errors:
        try:
            result = intake.file_po(db, data, actor=user, client_address=client_address(request))
            reconciliation.run(db, cause="po_change", cause_ref=result.po.id if result.po else None)
            db.commit()
            where = result.po.id if result.po else 0
            tail = "" if result.kind == "created" else f"?filed={'copy' if result.kind == 'skipped' else 'revision'}"
            return RedirectResponse(f"/customer-orders/{where}{tail}", status_code=303)
        except LedgerError as problem:
            db.rollback()
            errors.append(str(problem))
    return _form_page(request, db, user, header, rows, errors, 400)


# ---- from a file


def _import_page(request: Request, user: CurrentUser, status: int = 200, **extra: object) -> HTMLResponse:
    context: dict[str, object] = {"errors": None, "plan": None, "token": "", "truncated": False} | extra
    return render(request, "customer_order_import.html", user, status, columns=po_import.COLUMNS,
                  required=po_import.REQUIRED, **context)  # fmt: skip


@router.get("/import", response_class=HTMLResponse)
def import_page(request: Request, user: CurrentUser) -> HTMLResponse:
    return _import_page(request, user)


@router.get("/import/template")
def import_template(user: CurrentUser) -> Response:
    return Response(
        po_import.TEMPLATE,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="customer-orders-template.csv"'},
    )


@router.post("/import", response_class=HTMLResponse, response_model=None)
async def import_preview(request: Request, db: Db, user: CurrentUser) -> HTMLResponse:
    length = request.headers.get("content-length", "")
    if not length.isdigit():
        return _import_page(request, user, 411, errors=_message("The file could not be read: its size was not stated."))
    if int(length) > MAX_BYTES + UPLOAD_SLACK:
        return _import_page(
            request, user, 413, errors=_message(f"The file is larger than {MAX_BYTES // (1024 * 1024)} MB.")
        )
    form = await request.form(max_files=1, max_fields=4)
    upload = form.get("file")
    if not isinstance(upload, UploadFile):
        return _import_page(request, user, 400, errors=_message("Choose a CSV or Excel file first."))
    raw = await upload.read(MAX_BYTES + 1)
    filename = (upload.filename or "")[:200]
    try:
        plan = await run_in_threadpool(po_import.read, db, raw, filename)
    except ImportRejected as rejected:
        return _import_page(request, user, 400, errors=rejected.errors, truncated=rejected.truncated)
    return _import_page(request, user, plan=plan, token=_hold(user, raw, filename), filename=filename)


def _message(words: str) -> list[RowError]:
    return [RowError(None, words)]


@router.post("/import/confirm", response_class=HTMLResponse, response_model=None)
def import_confirm(request: Request, db: Db, user: CurrentUser, token: Field = "") -> Response:
    held = _take(user, token)
    if held is None:
        return _import_page(
            request, user, 400, errors=_message("That preview has expired or was already used. Upload the file again.")
        )
    raw, filename = held
    try:
        plan = po_import.apply(db, raw, filename, actor=user, client_address=client_address(request))
        reconciliation.run(db, cause="po_change")
        db.commit()
    except ImportRejected as rejected:
        db.rollback()
        return _import_page(request, user, 400, errors=rejected.errors, truncated=rejected.truncated)
    except LedgerError as problem:
        db.rollback()
        return _import_page(request, user, 400, errors=_message(f"Nothing was filed. {problem}"))
    return RedirectResponse(
        f"/customer-orders?imported={plan.count('created')}&revisions={plan.count('revision')}"
        f"&skipped={plan.count('skipped')}&waiting={plan.count('waiting')}",
        status_code=303,
    )


# ---- waiting for a customer


def _waiting_page(
    request: Request, db: Db, user: CurrentUser, error: str | None = None, status: int = 200
) -> HTMLResponse:
    rows = []
    for waiting in db.scalars(
        select(CustomerPOIntake).where(CustomerPOIntake.state == "waiting").order_by(CustomerPOIntake.id)
    ):
        payload = json.loads(waiting.payload)
        rows.append(
            {
                "waiting": waiting,
                "po_number": payload["po_number"],
                "customer": payload.get("customer_name"),
                "domain": payload.get("sender_domain"),
                "lines": len(payload["lines"]),
            }
        )
    return render(request, "customer_orders_waiting.html", user, status, rows=rows, error=error)


@router.get("/waiting", response_class=HTMLResponse)
def waiting_list(request: Request, db: Db, user: CurrentUser) -> HTMLResponse:
    return _waiting_page(request, db, user)


@router.post("/waiting/{waiting_id}/resolve", response_class=HTMLResponse, response_model=None)
async def waiting_resolve(request: Request, db: Db, user: CurrentUser, waiting_id: int) -> Response:
    form = await request.form()
    waiting = db.get(CustomerPOIntake, waiting_id)
    if waiting is None:
        return _waiting_page(request, db, user, "That waiting purchase order was not found.", 404)
    picked = _count(str(form.get(f"customer_{waiting_id}", "")))
    try:
        result = intake.resolve_waiting(
            db,
            waiting,
            customer_id=picked,
            new_customer_name=str(form.get("new_customer", "")),
            actor=user,
            client_address=client_address(request),
        )
        reconciliation.run(db, cause="po_change")
        db.commit()
    except LedgerError as problem:
        db.rollback()
        return _waiting_page(request, db, user, str(problem), 400)
    if result.po is None:
        return RedirectResponse("/customer-orders/waiting", status_code=303)
    tail = "" if result.kind == "created" else f"?filed={'copy' if result.kind == 'skipped' else 'revision'}"
    return RedirectResponse(f"/customer-orders/{result.po.id}{tail}", status_code=303)


@router.post("/waiting/{waiting_id}/reject", response_class=HTMLResponse, response_model=None)
def waiting_reject(
    request: Request, db: Db, user: CurrentUser, waiting_id: int, reason: Field = ""
) -> HTMLResponse | RedirectResponse:
    waiting = db.get(CustomerPOIntake, waiting_id)
    if waiting is None:
        return _waiting_page(request, db, user, "That waiting purchase order was not found.", 404)
    try:
        intake.reject_waiting(db, waiting, reason, actor=user, client_address=client_address(request))
        db.commit()
    except LedgerError as problem:
        db.rollback()
        return _waiting_page(request, db, user, str(problem), 400)
    return RedirectResponse("/customer-orders/waiting", status_code=303)


# ---- one order


def _detail(
    request: Request,
    db: Db,
    user: CurrentUser,
    order: CustomerPO,
    error: str | None = None,
    status: int = 200,
    filed: str = "",
) -> HTMLResponse:
    customer = db.get(Customer, order.customer_id)
    lines = listing.lines_of(db, order)
    total = sum((listing.line_total(line) for line in lines), Decimal(0))
    revisions = listing.revisions_of(db, order)
    counting = next((r for r in reversed(revisions) if r.state in listing.LIVE), None)
    diffs = {r.id: listing.differences(db, counting, r) for r in revisions if r.state == "pending" and counting}
    names = {
        u.id: u.email
        for u in db.scalars(select(AppUser))
        if u.id in {r.approved_by for r in revisions} | {r.created_by for r in revisions}
    }
    result = reconciliation.current(db)
    found = result.orders.get(order.id)
    family = [r.id for r in revisions]
    invoices = {i.id: i for i in db.scalars(select(SalesInvoice).where(SalesInvoice.customer_id == order.customer_id))}
    invoice_lines = {
        ln.id: ln
        for ln in db.scalars(select(SalesInvoiceLine).where(SalesInvoiceLine.invoice_id.in_(list(invoices) or [0])))
    }
    decided = list(
        db.scalars(
            select(Allocation)
            .where(Allocation.basis == "person", Allocation.customer_po_id.in_(family))
            .order_by(Allocation.id)
        )
    )
    events = list(
        db.scalars(
            select(ReconciliationEvent)
            .where(ReconciliationEvent.customer_po_id.in_(family))
            .order_by(ReconciliationEvent.id.desc())
            .limit(50)
        )
    )
    uploads = views.last_uploads(db)
    return render(
        request,
        "customer_order_detail.html",
        user,
        status,
        found=found,
        figures={f.line.id: f for f in found.lines} if found else {},
        invoices=invoices,
        invoice_lines=invoice_lines,
        invoice_choices=sorted(invoices.values(), key=lambda i: (i.invoice_date is None, i.invoice_date), reverse=True)[
            :300
        ],
        decided=decided,
        events=[(e, json.loads(e.exceptions or "[]")) for e in events],
        status_words=views.STATUS_WORDS,
        uploads=uploads,
        in_force=found is not None,
        levels=views.LEVELS,
        who_decided={u.id: u.email for u in db.scalars(select(AppUser))},
        order=order,
        customer=customer,
        lines=lines,
        total=total,
        revisions=revisions,
        number=revisions[0].po_number,
        diffs=diffs,
        who=names,
        error=error,
        filed=FILED_WORDS.get(filed, ""),
        mismatch=order.stated_total is not None and order.stated_total != total,
    )


@router.get("/{order_id}", response_class=HTMLResponse)
def order_page(request: Request, db: Db, user: CurrentUser, order_id: int, filed: str = "") -> HTMLResponse:
    order = db.get(CustomerPO, order_id)
    if order is None:
        return _list(request, db, user, error="That customer purchase order was not found.", status=404)
    return _detail(request, db, user, order, filed=filed)


def _revision_act(
    request: Request, db: Db, user: CurrentUser, order_id: int, work: Callable[[CustomerPO], object],
    *, run: bool = True, cause: str = "revision",
) -> HTMLResponse | RedirectResponse:  # fmt: skip
    order = db.get(CustomerPO, order_id)
    if order is None:
        return _list(request, db, user, error="That customer purchase order was not found.", status=404)
    try:
        work(order)
        if run:
            reconciliation.run(db, cause=cause, cause_ref=order.id)
        db.commit()
    except LedgerError as problem:
        db.rollback()
        db.refresh(order)
        return _detail(request, db, user, order, error=str(problem), status=400)
    return RedirectResponse(f"/customer-orders/{order_id}", status_code=303)


@router.post("/{order_id}/approve", response_class=HTMLResponse, response_model=None)
def approve(
    request: Request, db: Db, user: CurrentUser, order_id: int, version: Field = ""
) -> HTMLResponse | RedirectResponse:
    return _revision_act(
        request,
        db,
        user,
        order_id,
        lambda o: intake.approve_revision(
            db, o, actor=user, client_address=client_address(request), seen=parse_seen(version), check=True
        ),
    )


@router.post("/{order_id}/reject", response_class=HTMLResponse, response_model=None)
def reject(
    request: Request, db: Db, user: CurrentUser, order_id: int, reason: Field = "", version: Field = ""
) -> HTMLResponse | RedirectResponse:
    return _revision_act(
        request,
        db,
        user,
        order_id,
        lambda o: intake.reject_revision(
            db, o, reason, actor=user, client_address=client_address(request), seen=parse_seen(version), check=True
        ),
    )


# ---- close, cancel or reopen; and a person's decisions about invoices


@router.post("/{order_id}/state", response_class=HTMLResponse, response_model=None)
def change_state(
    request: Request, db: Db, user: CurrentUser, order_id: int, to: Field = "", reason: Field = "", version: Field = ""
) -> HTMLResponse | RedirectResponse:
    return _revision_act(
        request, db, user, order_id,
        lambda o: intake.set_state(
            db, o, to, reason, actor=user, client_address=client_address(request), seen=parse_seen(version), check=True
        ),
        cause="po_change",
    )  # fmt: skip


def _invoice(db: Db, value: str) -> SalesInvoice:
    found = db.get(SalesInvoice, _count(value) or 0)
    if found is None:
        raise LedgerError("Choose an invoice from the list.")
    return found


def _amount(value: str) -> Decimal | None:
    return number(value, "Amount", Money, positive=False) if value.strip() else None


@router.post("/{order_id}/link", response_class=HTMLResponse, response_model=None)
def link_invoice(
    request: Request, db: Db, user: CurrentUser, order_id: int, invoice_id: Field = "", invoice_line: Field = "",
    po_line: Field = "", amount: Field = "", reason: Field = "",
) -> HTMLResponse | RedirectResponse:  # fmt: skip
    def work(order: CustomerPO) -> None:
        invoice = _invoice(db, invoice_id)
        line_id = po_line_id = None
        if invoice_line.strip() or po_line.strip():
            at, no = _count(invoice_line), _count(po_line)
            line = db.scalar(select(SalesInvoiceLine).where(
                SalesInvoiceLine.invoice_id == invoice.id, SalesInvoiceLine.position == at))  # fmt: skip
            target = db.scalar(select(CustomerPOLine).where(
                CustomerPOLine.customer_po_id == order.id, CustomerPOLine.line_no == no))  # fmt: skip
            if line is None or target is None:
                raise LedgerError("Give a line number that is on the invoice and a line number that is on the PO.")
            line_id, po_line_id = line.id, target.id
        decisions.link(
            db, invoice, order, invoice_line_id=line_id, po_line_id=po_line_id, amount=_amount(amount),
            reason=reason, actor=user, client_address=client_address(request),
        )  # fmt: skip

    return _revision_act(request, db, user, order_id, work, run=False)


@router.post("/{order_id}/reject-invoice", response_class=HTMLResponse, response_model=None)
def reject_invoice(
    request: Request, db: Db, user: CurrentUser, order_id: int, invoice_id: Field = "", reason: Field = ""
) -> HTMLResponse | RedirectResponse:
    return _revision_act(
        request, db, user, order_id,
        lambda o: decisions.reject(
            db, _invoice(db, invoice_id), o, reason=reason, actor=user, client_address=client_address(request)
        ),
        run=False,
    )  # fmt: skip


@router.post("/{order_id}/withdraw", response_class=HTMLResponse, response_model=None)
def withdraw_decision(
    request: Request, db: Db, user: CurrentUser, order_id: int, decision_id: Field = "", reason: Field = ""
) -> HTMLResponse | RedirectResponse:
    def work(order: CustomerPO) -> None:
        row = db.get(Allocation, _count(decision_id) or 0)
        if row is None or row.customer_po_id not in {r.id for r in listing.revisions_of(db, order)}:
            raise LedgerError("That decision was not found on this purchase order.")
        decisions.withdraw(db, row, reason=reason, actor=user, client_address=client_address(request))

    return _revision_act(request, db, user, order_id, work, run=False)
