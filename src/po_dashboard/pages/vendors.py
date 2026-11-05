"""Vendors and the email domains they write from. Everyone signed in can see and edit them (DEC-29)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import func, select

from po_dashboard.db.ledger import PurchaseOrder, Vendor, VendorDomain
from po_dashboard.ledger import vendors as rules
from po_dashboard.ledger.errors import LedgerError
from po_dashboard.pages.common import CurrentUser, Db, client_address, render

router = APIRouter(prefix="/vendors")
Field = Annotated[str, Form()]


def _list(request: Request, db: Db, user: CurrentUser, error: str | None = None, form: dict[str, str] | None = None,
          status: int = 200) -> HTMLResponse:  # fmt: skip
    rows = db.scalars(select(Vendor).order_by(func.lower(Vendor.name))).all()
    domains: dict[int, list[str]] = {}
    for vendor_id, domain in db.execute(
        select(VendorDomain.vendor_id, VendorDomain.domain).order_by(VendorDomain.domain)
    ):
        domains.setdefault(vendor_id, []).append(domain)
    return render(request, "vendors.html", user, status, vendors=rows, domains=domains, orders=rules.po_counts(db),
                  error=error, form=form or {})  # fmt: skip


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
        return _list(request, db, user, error=str(problem), form=entered, status=400)
    return RedirectResponse(f"/vendors/{vendor.id}", status_code=303)


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
