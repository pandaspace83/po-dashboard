"""Purchase orders: the list, entering one by hand, importing a CSV file, and the detail page (REQ-PO-01, -02, -04)."""

from __future__ import annotations

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Form, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy import func, select
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile

from po_dashboard.db.ledger import POLine, PurchaseOrder, Vendor
from po_dashboard.ledger import events, listing
from po_dashboard.ledger import orders as rules
from po_dashboard.ledger.errors import LedgerError
from po_dashboard.ledger.expected import line_expectations
from po_dashboard.ledger.status import LineState, line_states, po_state
from po_dashboard.pages.common import CurrentUser, Db, client_address, render

router = APIRouter(prefix="/orders")
FORM_ROWS = 10
MAX_FORM_ROWS = 100  # six fields a row: the framework refuses a form of more than 1000 fields
UPLOAD_SLACK = 64 * 1024
LINE_FIELDS = ("item", "description", "quantity", "unit", "unit_price", "requested_date")


def _vendor_choices(db: Db) -> list[Vendor]:
    return list(db.scalars(select(Vendor).order_by(func.lower(Vendor.name))))


def _form_page(request: Request, db: Db, user: CurrentUser, header: dict[str, str], rows: list[dict[str, str]],
               errors: list[str] | None = None, status: int = 200) -> HTMLResponse:  # fmt: skip
    return render(request, "order_new.html", user, status, vendors=_vendor_choices(db), header=header, rows=rows,
                  errors=errors or [], currencies=rules.COMMON_CURRENCIES)  # fmt: skip


def _rows_of(columns: dict[str, list[str]], count: int) -> list[dict[str, str]]:
    return [{name: columns[name][i] if i < len(columns[name]) else "" for name in LINE_FIELDS} for i in range(count)]


def _blank_rows(count: int = FORM_ROWS) -> list[dict[str, str]]:
    return [{name: "" for name in LINE_FIELDS} for _ in range(count)]


@router.get("", response_class=HTMLResponse)
def order_list(request: Request, db: Db, user: CurrentUser,
               imported: int | None = Query(None, ge=0, le=100000)) -> HTMLResponse:  # fmt: skip
    found, problems = listing.parse_filters({key: value for key, value in request.query_params.items()})
    return render(request, "orders.html", user, orders=listing.list_orders(db, found, date.today()), f=found,
                  problems=problems, vendors=listing.vendor_choices(db), statuses=listing.STATUS_CHOICES,
                  raw=request.query_params, imported=imported)  # fmt: skip


@router.get("/new", response_class=HTMLResponse)
def new_order(request: Request, db: Db, user: CurrentUser) -> HTMLResponse:
    return _form_page(request, db, user, {"buyer": user.email}, _blank_rows())


@router.post("/create", response_class=HTMLResponse, response_model=None)
async def create(request: Request, db: Db, user: CurrentUser, po_number: Annotated[str, Form()],
                 vendor_id: Annotated[str, Form()] = "", currency: Annotated[str, Form()] = "",
                 date_sent: Annotated[str, Form()] = "", buyer: Annotated[str, Form()] = "") -> Response:  # fmt: skip
    form = await request.form()
    columns = {name: [str(v) for v in form.getlist(f"line_{name}")] for name in LINE_FIELDS}
    header = {
        "po_number": po_number,
        "vendor_id": vendor_id,
        "currency": currency,
        "date_sent": date_sent,
        "buyer": buyer,
    }
    count = len(columns["item"])
    if any(len(values) != count for values in columns.values()) or count > MAX_FORM_ROWS:
        shown = _blank_rows() if count > MAX_FORM_ROWS or count == 0 else _rows_of(columns, count)
        problem = ["The form was incomplete. Reload it and try again."]
        return _form_page(request, db, user, header, shown, problem, 400)
    rows = _rows_of(columns, count)

    errors: list[str] = []
    lines: list[rules.LineInput] = []
    for number, row in enumerate(rows, start=1):
        if not any(value.strip() for value in row.values()):
            continue
        try:
            lines.append(rules.parse_line(f"Line {number}", line_no=number, **row))
        except LedgerError as problem:
            errors.append(str(problem))
    chosen = int(vendor_id) if vendor_id.isascii() and vendor_id.isdigit() else None
    fields = {"po_number": po_number, "vendor_id": chosen, "currency": currency, "date_sent": date_sent, "buyer": buyer}
    try:
        rules.check_header(db, **fields)  # type: ignore[arg-type]
    except LedgerError as problem:
        errors.insert(0, str(problem))
    if not errors:
        try:
            order = rules.create_po(db, lines=lines, actor=user, client_address=client_address(request), **fields)  # type: ignore[arg-type]
            db.commit()
            return RedirectResponse(f"/orders/{order.id}", status_code=303)
        except LedgerError as problem:
            db.rollback()
            errors.append(str(problem))
    return _form_page(request, db, user, header, rows, errors, 400)


@router.get("/import", response_class=HTMLResponse)
def import_page(request: Request, db: Db, user: CurrentUser) -> HTMLResponse:
    return render(request, "order_import.html", user, columns=rules.COLUMNS, required=rules.REQUIRED, errors=None)


@router.get("/import/template")
def import_template(user: CurrentUser) -> Response:
    return Response(rules.TEMPLATE, media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": 'attachment; filename="purchase-orders-template.csv"'})  # fmt: skip


@router.post("/import", response_class=HTMLResponse, response_model=None)
async def import_file(request: Request, db: Db, user: CurrentUser) -> Response:
    def page(problem: rules.ImportRejected | None, message: str | None, status: int) -> HTMLResponse:
        errors = problem.errors if problem else [rules.RowError(None, message or "")]
        return render(request, "order_import.html", user, status, columns=rules.COLUMNS, required=rules.REQUIRED,
                      errors=errors, truncated=bool(problem and problem.truncated))  # fmt: skip

    length = request.headers.get("content-length", "")
    if not length.isdigit():
        return page(None, "The file could not be read: its size was not stated.", 411)
    if int(length) > rules.MAX_CSV_BYTES + UPLOAD_SLACK:
        return page(None, f"The file is larger than {rules.MAX_CSV_BYTES // (1024 * 1024)} MB.", 413)
    form = await request.form(max_files=1, max_fields=4)
    upload = form.get("file")
    if not isinstance(upload, UploadFile):
        return page(None, "Choose a CSV file first.", 400)
    raw = await upload.read(rules.MAX_CSV_BYTES + 1)

    def work() -> int:
        try:
            result = rules.import_csv(db, raw, actor=user, client_address=client_address(request))
            db.commit()
            return len(result.po_numbers)
        except Exception:
            db.rollback()
            raise

    try:
        count = await run_in_threadpool(work)
    except rules.ImportRejected as problem:
        return page(problem, None, 400)
    return RedirectResponse(f"/orders?imported={count}", status_code=303)


def missing_order(request: Request, user: CurrentUser) -> HTMLResponse:
    return render(request, "orders.html", user, 404, orders=[], f=listing.Filters(), problems=[], vendors=[],
                  statuses=listing.STATUS_CHOICES, raw={}, imported=None,
                  error="That purchase order was not found.")  # fmt: skip


def order_view(request: Request, db: Db, user: CurrentUser, order: PurchaseOrder, error: str | None = None,
               status: int = 200) -> HTMLResponse:  # fmt: skip
    vendor = db.get(Vendor, order.vendor_id)
    lines = db.scalars(select(POLine).where(POLine.po_id == order.id).order_by(POLine.line_no)).all()
    states: dict[int, LineState] = line_states(db, order.id)
    summary = po_state(states[line.id] for line in lines)
    expected = line_expectations(db, date.today(), order.id, states)
    return render(
        request, "order_detail.html", user, status, order=order, vendor=vendor, lines=lines, states=states,
        summary=summary, expected=expected, notes=events.line_warnings(db, order),
        promised=events.promised_history(db, order), history=events.entries(db, order), error=error,
    )  # fmt: skip


@router.get("/{po_id}", response_class=HTMLResponse)
def order_page(request: Request, db: Db, user: CurrentUser, po_id: int) -> HTMLResponse:
    order = db.get(PurchaseOrder, po_id)
    return missing_order(request, user) if order is None else order_view(request, db, user, order)
