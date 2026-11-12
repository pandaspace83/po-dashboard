"""Customers: the people RaceSpec invoices, the names QuickBooks uses for them and the domains they write from
(REQ-CO-01, DEC-56). Everyone signed in can see and edit them (DEC-29); every change is audited."""

from __future__ import annotations

from collections.abc import Callable
from typing import Annotated, Any

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy import func, select

from po_dashboard.customers import service
from po_dashboard.db.customers import Customer, CustomerDomain, CustomerName, CustomerPO
from po_dashboard.ledger.errors import LedgerError, parse_seen
from po_dashboard.pages import tables
from po_dashboard.pages.common import CurrentUser, Db, client_address, render

router = APIRouter(prefix="/customers")
Field = Annotated[str, Form()]


def _list(request: Request, db: Db, user: CurrentUser, error: str | None = None, status: int = 200) -> HTMLResponse:
    domains: dict[int, list[str]] = {}
    for customer_id, domain in db.execute(
        select(CustomerDomain.customer_id, CustomerDomain.domain).order_by(CustomerDomain.domain)
    ):
        domains.setdefault(customer_id, []).append(domain)
    names: dict[int, list[str]] = {}
    for customer_id, name in db.execute(
        select(CustomerName.customer_id, CustomerName.name).order_by(CustomerName.name)
    ):
        names.setdefault(customer_id, []).append(name)
    counts = service.counts(db)
    q = request.query_params.get("q", "").strip()[:100]
    wanted = q.lower()
    rows = [
        c for c in db.scalars(select(Customer).order_by(func.lower(Customer.name)))
        if not wanted or wanted in c.name.lower() or wanted in (c.contact_name or "").lower()
        or any(wanted in d for d in domains.get(c.id, [])) or any(wanted in n.lower() for n in names.get(c.id, []))
    ]  # fmt: skip
    keys: dict[str, Callable[[Customer], Any]] = {
        "name": lambda c: c.name.lower(),
        "contact": lambda c: (c.contact_name or c.contact_email or "").lower() or None,
        "orders": lambda c: counts.get(c.id, (0, 0))[0],
        "invoices": lambda c: counts.get(c.id, (0, 0))[1],
    }
    ordered, sorting = tables.sort_rows(rows, request.query_params.get("sort"), keys, "name")
    shown = tables.page_of(ordered, request.query_params.get("page"))
    return render(request, "customers.html", user, status, customers=shown.rows, page=shown, sorting=sorting, q=q,
                  domains=domains, names=names, counts=counts, error=error)  # fmt: skip


def _new(request: Request, user: CurrentUser, error: str | None = None, form: dict[str, str] | None = None,
         status: int = 200) -> HTMLResponse:  # fmt: skip
    return render(request, "customer_new.html", user, status, error=error, form=form or {})


def _detail(request: Request, db: Db, user: CurrentUser, customer: Customer, error: str | None = None,
            form: dict[str, str] | None = None, status: int = 200, saved: bool = False) -> HTMLResponse:  # fmt: skip
    names = db.scalars(select(CustomerName).where(CustomerName.customer_id == customer.id).order_by(CustomerName.name))
    domains = db.scalars(
        select(CustomerDomain).where(CustomerDomain.customer_id == customer.id).order_by(CustomerDomain.domain)
    )
    orders = db.scalars(
        select(CustomerPO)
        .where(CustomerPO.customer_id == customer.id, CustomerPO.state.in_(("active", "closed", "cancelled")))
        .order_by(CustomerPO.id.desc())
    )  # fmt: skip
    return render(request, "customer_detail.html", user, status, customer=customer, names=names.all(),
                  domains=domains.all(), orders=orders.all(), error=error, form=form or {}, saved=saved)  # fmt: skip


def _missing(request: Request, db: Db, user: CurrentUser) -> HTMLResponse:
    return _list(request, db, user, error="That customer was not found.", status=404)


@router.get("", response_class=HTMLResponse)
def customer_list(request: Request, db: Db, user: CurrentUser) -> HTMLResponse:
    return _list(request, db, user)


@router.get("/new", response_class=HTMLResponse)
def new_customer(request: Request, user: CurrentUser) -> HTMLResponse:
    return _new(request, user)


@router.get("/search")
def search(db: Db, user: CurrentUser, q: str = "") -> JSONResponse:
    """The customer pickers call this while a person types (read-only; any signed-in person)."""
    return JSONResponse([{"id": c.id, "name": c.name} for c in service.search(db, q)])


@router.post("/create", response_class=HTMLResponse, response_model=None)
def create(
    request: Request, db: Db, user: CurrentUser, name: Field, contact_name: Field = "", contact_email: Field = "",
    qb_names: Field = "", domains: Field = "", notes: Field = "",
) -> HTMLResponse | RedirectResponse:  # fmt: skip
    entered = {"name": name, "contact_name": contact_name, "contact_email": contact_email, "qb_names": qb_names,
               "domains": domains, "notes": notes}  # fmt: skip
    try:
        customer = service.create_customer(
            db, name=name, contact_name=contact_name, contact_email=contact_email, notes=notes, qb_names=qb_names,
            domains=domains, actor=user, client_address=client_address(request),
        )  # fmt: skip
        db.commit()
    except LedgerError as problem:
        db.rollback()
        return _new(request, user, error=str(problem), form=entered, status=400)
    return RedirectResponse(f"/customers/{customer.id}", status_code=303)


@router.get("/{customer_id}", response_class=HTMLResponse)
def customer_page(request: Request, db: Db, user: CurrentUser, customer_id: int, saved: str = "") -> HTMLResponse:
    customer = db.get(Customer, customer_id)
    if customer is None:
        return _missing(request, db, user)
    return _detail(request, db, user, customer, saved=saved == "1")  # a fixed word, never text from the address


def _act(request: Request, db: Db, user: CurrentUser, customer_id: int, work: Callable[[Customer], object],
         form: dict[str, str] | None = None) -> HTMLResponse | RedirectResponse:  # fmt: skip
    customer = db.get(Customer, customer_id)
    if customer is None:
        return _missing(request, db, user)
    try:
        work(customer)
        db.commit()
    except LedgerError as problem:
        db.rollback()
        db.refresh(customer)
        return _detail(request, db, user, customer, error=str(problem), form=form, status=400)
    return RedirectResponse(f"/customers/{customer_id}?saved=1", status_code=303)


@router.post("/{customer_id}/update", response_class=HTMLResponse, response_model=None)
def update(
    request: Request, db: Db, user: CurrentUser, customer_id: int, name: Field, contact_name: Field = "",
    contact_email: Field = "", notes: Field = "", version: Field = "",
) -> HTMLResponse | RedirectResponse:  # fmt: skip
    entered = {"name": name, "contact_name": contact_name, "contact_email": contact_email, "notes": notes}
    return _act(request, db, user, customer_id, lambda c: service.update_customer(
        db, c, name=name, contact_name=contact_name, contact_email=contact_email, notes=notes, actor=user,
        client_address=client_address(request), seen=parse_seen(version), check=True), entered)  # fmt: skip


@router.post("/{customer_id}/active", response_class=HTMLResponse, response_model=None)
def set_active(request: Request, db: Db, user: CurrentUser, customer_id: int, active: Field = "",
               version: Field = "") -> HTMLResponse | RedirectResponse:  # fmt: skip
    return _act(request, db, user, customer_id, lambda c: service.set_active(
        db, c, active == "1", actor=user, client_address=client_address(request), seen=parse_seen(version),
        check=True))  # fmt: skip


@router.post("/{customer_id}/names/add", response_class=HTMLResponse, response_model=None)
def add_names(request: Request, db: Db, user: CurrentUser, customer_id: int,
              names: Field) -> HTMLResponse | RedirectResponse:  # fmt: skip
    return _act(
        request,
        db,
        user,
        customer_id,
        lambda c: service.add_names(db, c, names, user, client_address(request)),
        {"names": names},
    )


@router.post("/{customer_id}/names/{name_id}/remove", response_class=HTMLResponse, response_model=None)
def remove_name(request: Request, db: Db, user: CurrentUser, customer_id: int,
                name_id: int) -> HTMLResponse | RedirectResponse:  # fmt: skip
    return _act(
        request, db, user, customer_id, lambda c: service.remove_name(db, c, name_id, user, client_address(request))
    )


@router.post("/{customer_id}/domains/add", response_class=HTMLResponse, response_model=None)
def add_domains(request: Request, db: Db, user: CurrentUser, customer_id: int,
                domains: Field) -> HTMLResponse | RedirectResponse:  # fmt: skip
    return _act(
        request,
        db,
        user,
        customer_id,
        lambda c: service.add_domains(db, c, domains, user, client_address(request)),
        {"domains": domains},
    )


@router.post("/{customer_id}/domains/{domain_id}/remove", response_class=HTMLResponse, response_model=None)
def remove_domain(request: Request, db: Db, user: CurrentUser, customer_id: int,
                  domain_id: int) -> HTMLResponse | RedirectResponse:  # fmt: skip
    return _act(
        request, db, user, customer_id, lambda c: service.remove_domain(db, c, domain_id, user, client_address(request))
    )
