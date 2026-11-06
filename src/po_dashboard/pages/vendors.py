"""Vendors and the email domains they write from. Everyone signed in can see and edit them (DEC-29)."""

from __future__ import annotations

from collections.abc import Callable
from typing import Annotated, Any

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from sqlalchemy import func, select
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile

from po_dashboard.db.ledger import PurchaseOrder, Vendor, VendorDomain
from po_dashboard.ledger import orders as order_rules
from po_dashboard.ledger import vendor_import
from po_dashboard.ledger import vendors as rules
from po_dashboard.ledger.errors import LedgerError
from po_dashboard.pages import tables
from po_dashboard.pages.common import CurrentUser, Db, client_address, render

router = APIRouter(prefix="/vendors")
Field = Annotated[str, Form()]


def _list(request: Request, db: Db, user: CurrentUser, error: str | None = None, status: int = 200) -> HTMLResponse:
    """The vendor list: searched by name, domain or contact, sorted by a heading, 50 a page (DEC-45)."""
    domains: dict[int, list[str]] = {}
    for vendor_id, domain in db.execute(
        select(VendorDomain.vendor_id, VendorDomain.domain).order_by(VendorDomain.domain)
    ):
        domains.setdefault(vendor_id, []).append(domain)
    orders = rules.po_counts(db)
    q = request.query_params.get("q", "").strip()[:100]
    wanted = q.lower()
    rows = [
        v for v in db.scalars(select(Vendor).order_by(func.lower(Vendor.name)))
        if not wanted or wanted in v.name.lower() or wanted in (v.contact_name or "").lower()
        or wanted in (v.contact_email or "").lower() or any(wanted in d for d in domains.get(v.id, []))
    ]  # fmt: skip
    keys: dict[str, Callable[[Vendor], Any]] = {
        "name": lambda v: v.name.lower(),
        "domains": lambda v: domains[v.id][0] if domains.get(v.id) else None,
        "contact": lambda v: (v.contact_name or v.contact_email or "").lower() or None,
        "pos": lambda v: orders.get(v.id, 0),
    }
    ordered, sorting = tables.sort_rows(rows, request.query_params.get("sort"), keys, "name")
    shown = tables.page_of(ordered, request.query_params.get("page"))
    return render(request, "vendors.html", user, status, vendors=shown.rows, page=shown, sorting=sorting, q=q,
                  domains=domains, orders=orders, error=error)  # fmt: skip


def _new(request: Request, user: CurrentUser, error: str | None = None, form: dict[str, str] | None = None,
         status: int = 200) -> HTMLResponse:  # fmt: skip
    return render(request, "vendor_new.html", user, status, error=error, form=form or {})


def _detail(request: Request, db: Db, user: CurrentUser, vendor: Vendor, error: str | None = None,
            form: dict[str, str] | None = None, status: int = 200, saved: bool = False) -> HTMLResponse:  # fmt: skip
    domains = db.scalars(select(VendorDomain).where(VendorDomain.vendor_id == vendor.id).order_by(VendorDomain.domain))
    orders = db.scalars(
        select(PurchaseOrder).where(PurchaseOrder.vendor_id == vendor.id).order_by(PurchaseOrder.id.desc())
    )
    return render(
        request, "vendor_detail.html", user, status, vendor=vendor, domains=domains.all(), orders=orders.all(),
        error=error, form=form or {}, saved=saved,
    )  # fmt: skip


def _missing(request: Request, db: Db, user: CurrentUser) -> HTMLResponse:
    return _list(request, db, user, error="That vendor was not found.", status=404)


@router.get("", response_class=HTMLResponse)
def vendor_list(request: Request, db: Db, user: CurrentUser) -> HTMLResponse:
    return _list(request, db, user)


@router.get("/new", response_class=HTMLResponse)
def new_vendor(request: Request, user: CurrentUser) -> HTMLResponse:
    return _new(request, user)


@router.post("/create", response_class=HTMLResponse, response_model=None)
def create(
    request: Request, db: Db, user: CurrentUser, name: Field, contact_name: Field = "",
    contact_email: Field = "", domains: Field = "", notes: Field = "",
) -> HTMLResponse | RedirectResponse:  # fmt: skip
    entered = {"name": name, "contact_name": contact_name, "contact_email": contact_email, "domains": domains,
               "notes": notes}  # fmt: skip
    try:
        vendor = rules.create_vendor(db, name=name, contact_name=contact_name, contact_email=contact_email, notes=notes,
                                     domains=domains, actor=user, client_address=client_address(request))  # fmt: skip
        db.commit()
    except LedgerError as problem:
        db.rollback()
        return _new(request, user, error=str(problem), form=entered, status=400)
    return RedirectResponse(f"/vendors/{vendor.id}", status_code=303)


UPLOAD_SLACK = 64 * 1024


def _import_page(request: Request, user: CurrentUser, result: vendor_import.VendorImportResult | None = None,
                 problem: str | None = None, status: int = 200) -> HTMLResponse:  # fmt: skip
    return render(request, "vendor_import.html", user, status, columns=vendor_import.COLUMNS,
                  required=vendor_import.REQUIRED, result=result, problem=problem,
                  shown=vendor_import.MAX_SHOWN)  # fmt: skip


@router.get("/import", response_class=HTMLResponse)
def import_page(request: Request, user: CurrentUser) -> HTMLResponse:
    return _import_page(request, user)


@router.get("/import/template")
def import_template(user: CurrentUser) -> Response:
    return Response(vendor_import.TEMPLATE, media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": 'attachment; filename="vendors-template.csv"'})  # fmt: skip


@router.post("/import", response_class=HTMLResponse, response_model=None)
async def import_file(request: Request, db: Db, user: CurrentUser) -> Response:
    length = request.headers.get("content-length", "")
    if not length.isdigit():
        return _import_page(request, user, problem="The file could not be read: its size was not stated.", status=411)
    if int(length) > order_rules.MAX_CSV_BYTES + UPLOAD_SLACK:
        return _import_page(request, user, problem="The file is larger than 2 MB.", status=413)
    form = await request.form(max_files=1, max_fields=4)
    upload = form.get("file")
    if not isinstance(upload, UploadFile):
        return _import_page(request, user, problem="Choose a CSV file first.", status=400)
    raw = await upload.read(order_rules.MAX_CSV_BYTES + 1)

    def work() -> vendor_import.VendorImportResult:
        try:
            result = vendor_import.import_vendors(db, raw, filename=upload.filename, actor=user,
                                                  client_address=client_address(request))  # fmt: skip
            db.commit()
            return result
        except Exception:
            db.rollback()
            raise

    try:
        result = await run_in_threadpool(work)
    except vendor_import.VendorFileRejected as problem:
        return _import_page(request, user, problem=str(problem), status=400)
    return _import_page(request, user, result=result)


@router.get("/search")
def search(db: Db, user: CurrentUser, q: str = "") -> JSONResponse:
    """The pickers on other pages call this while a person types (read-only; any signed-in person)."""
    return JSONResponse([{"id": v.id, "name": v.name} for v in rules.search_vendors(db, q)])


@router.get("/{vendor_id}", response_class=HTMLResponse)
def vendor_page(request: Request, db: Db, user: CurrentUser, vendor_id: int, saved: str = "") -> HTMLResponse:
    vendor = db.get(Vendor, vendor_id)
    if vendor is None:
        return _missing(request, db, user)
    return _detail(request, db, user, vendor, saved=saved == "1")  # a fixed word, never text taken from the address


@router.post("/{vendor_id}/update", response_class=HTMLResponse, response_model=None)
def update(request: Request, db: Db, user: CurrentUser, vendor_id: int, name: Field, contact_name: Field = "",
           contact_email: Field = "", notes: Field = "") -> HTMLResponse | RedirectResponse:  # fmt: skip
    vendor = db.get(Vendor, vendor_id)
    if vendor is None:
        return _missing(request, db, user)
    try:
        rules.update_vendor(db, vendor, name=name, contact_name=contact_name, contact_email=contact_email, notes=notes,
                            actor=user, client_address=client_address(request))  # fmt: skip
        db.commit()
    except LedgerError as problem:
        db.rollback()
        entered = {"name": name, "contact_name": contact_name, "contact_email": contact_email, "notes": notes}
        return _detail(request, db, user, vendor, error=str(problem), form=entered, status=400)
    return RedirectResponse(f"/vendors/{vendor_id}?saved=1", status_code=303)


@router.post("/{vendor_id}/domains/add", response_class=HTMLResponse, response_model=None)
def add_domain(
    request: Request, db: Db, user: CurrentUser, vendor_id: int, domains: Field
) -> HTMLResponse | RedirectResponse:
    vendor = db.get(Vendor, vendor_id)
    if vendor is None:
        return _missing(request, db, user)
    try:
        rules.add_domains(db, vendor, domains, actor=user, client_address=client_address(request))
        db.commit()
    except LedgerError as problem:
        db.rollback()
        return _detail(request, db, user, vendor, error=str(problem), form={"domains": domains}, status=400)
    return RedirectResponse(f"/vendors/{vendor_id}?saved=1", status_code=303)


@router.post("/{vendor_id}/domains/{domain_id}/remove", response_class=HTMLResponse, response_model=None)
def remove_domain(
    request: Request, db: Db, user: CurrentUser, vendor_id: int, domain_id: int
) -> HTMLResponse | RedirectResponse:
    vendor = db.get(Vendor, vendor_id)
    if vendor is None:
        return _missing(request, db, user)
    try:
        rules.remove_domain(db, vendor, domain_id, actor=user, client_address=client_address(request))
        db.commit()
    except LedgerError as problem:
        db.rollback()
        return _detail(request, db, user, vendor, error=str(problem), status=400)
    return RedirectResponse(f"/vendors/{vendor_id}?saved=1", status_code=303)
