"""Recording an acknowledgement, shipment or invoice against a purchase order, and taking an entry back (PD-1.4)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy import select
from starlette.datastructures import FormData

from po_dashboard.db.ledger import POLine, PurchaseOrder
from po_dashboard.ledger import events
from po_dashboard.ledger.errors import LedgerError
from po_dashboard.ledger.status import line_states
from po_dashboard.pages.common import CurrentUser, Db, client_address, render
from po_dashboard.pages.orders import missing_order, order_view

router = APIRouter(prefix="/orders/{po_id}")

# The per-line columns each entry form sends, one list per column, in the same order as `line_id`.
FIELDS = {
    "ack": ("qty", "price", "promised"),
    "shipment": ("qty", "eta"),
    "invoice": ("qty", "price"),
}
HEADER_FIELDS = (
    "carrier", "tracking_number", "shipped_at", "eta", "invoice_number", "invoice_date", "total", "reference",
    "without_details",
)  # fmt: skip
TEMPLATES = {"ack": "entry_ack.html", "shipment": "entry_shipment.html", "invoice": "entry_invoice.html"}


def _rows(form: FormData, kind: str) -> tuple[list[int], dict[int, dict[str, str]]] | None:
    """The line ids and the typed values by line, or None when the columns do not line up (a tampered form)."""
    raw_ids = [str(v) for v in form.getlist("line_id")]
    columns = {name: [str(v) for v in form.getlist(f"line_{name}")] for name in FIELDS[kind]}
    if any(len(values) != len(raw_ids) for values in columns.values()):
        return None
    if not all(value.isascii() and value.isdigit() for value in raw_ids):
        return None
    ids = [int(value) for value in raw_ids]
    return ids, {line_id: {name: columns[name][i] for name in FIELDS[kind]} for i, line_id in enumerate(ids)}


def _page(
    request: Request, db: Db, user: CurrentUser, order: PurchaseOrder, kind: str, header: dict[str, Any],
    rows: dict[int, dict[str, str]], errors: list[str] | None = None, status: int = 200,
) -> HTMLResponse:  # fmt: skip
    lines = db.scalars(select(POLine).where(POLine.po_id == order.id).order_by(POLine.line_no)).all()
    shown = _blank(kind, db, order)  # every line of this PO gets a row, whatever the posted form held
    for line_id, values in rows.items():
        if line_id in shown:
            shown[line_id] = values
    return render(request, TEMPLATES[kind], user, status, order=order, lines=lines, states=line_states(db, order.id),
                  header=header, rows=shown, errors=errors or [])  # fmt: skip


def _blank(kind: str, db: Db, order: PurchaseOrder) -> dict[int, dict[str, str]]:
    """Empty rows; an invoice's price starts as the PO price, which the person can change."""
    rows: dict[int, dict[str, str]] = {}
    for line in db.scalars(select(POLine).where(POLine.po_id == order.id)):
        rows[line.id] = {name: "" for name in FIELDS[kind]}
        if kind == "invoice":
            rows[line.id]["price"] = format(line.unit_price.normalize(), "f")
    return rows


def _open(db: Db, po_id: int) -> PurchaseOrder | None:
    return db.get(PurchaseOrder, po_id)


@router.get("/ack/new", response_class=HTMLResponse)
def ack_form(request: Request, db: Db, user: CurrentUser, po_id: int) -> HTMLResponse:
    order = _open(db, po_id)
    if order is None:
        return missing_order(request, user)
    return _page(request, db, user, order, "ack", {}, _blank("ack", db, order))


@router.get("/shipments/new", response_class=HTMLResponse)
def shipment_form(request: Request, db: Db, user: CurrentUser, po_id: int) -> HTMLResponse:
    order = _open(db, po_id)
    if order is None:
        return missing_order(request, user)
    return _page(request, db, user, order, "shipment", {}, _blank("shipment", db, order))


@router.get("/invoices/new", response_class=HTMLResponse)
def invoice_form(request: Request, db: Db, user: CurrentUser, po_id: int) -> HTMLResponse:
    order = _open(db, po_id)
    if order is None:
        return missing_order(request, user)
    return _page(request, db, user, order, "invoice", {}, _blank("invoice", db, order))


async def _submit(request: Request, db: Db, user: CurrentUser, po_id: int, kind: str) -> Response:
    order = _open(db, po_id)
    if order is None:
        return missing_order(request, user)
    form = await request.form(max_fields=1000)
    header = {name: str(form.get(name, "")) for name in HEADER_FIELDS}
    parsed = _rows(form, kind)
    if parsed is None:
        return _page(request, db, user, order, kind, header, _blank(kind, db, order),
                     ["The form was incomplete. Reload it and try again."], 400)  # fmt: skip
    ids, rows = parsed
    errors: list[str] = []
    numbers = {line.id: line.line_no for line in db.scalars(select(POLine).where(POLine.po_id == po_id))}
    made: list[Any] = []
    for line_id in ids:
        label = f"Line {numbers.get(line_id, '?')}"
        values = rows[line_id]
        try:
            if kind == "ack":
                row: Any = events.parse_ack_line(label, line_id, values["qty"], values["price"], values["promised"])
            elif kind == "shipment":
                row = events.parse_ship_line(label, line_id, values["qty"], values["eta"])
            else:
                row = events.parse_invoice_line(label, line_id, values["qty"], values["price"])
        except LedgerError as problem:
            errors.append(str(problem))
            continue
        if row is not None:
            made.append(row)
    if not errors:
        try:
            actor, where = user, client_address(request)
            if kind == "ack":
                events.record_ack(db, order, made, without_details=bool(header["without_details"]),
                                  source_ref=header["reference"], actor=actor, client_address=where)  # fmt: skip
            elif kind == "shipment":
                events.record_shipment(
                    db, order, made, carrier=header["carrier"], tracking_number=header["tracking_number"],
                    shipped_at=header["shipped_at"], eta=header["eta"], actor=actor, client_address=where,
                )  # fmt: skip
            else:
                events.record_invoice(db, order, made, invoice_number=header["invoice_number"],
                                      invoice_date=header["invoice_date"], total=header["total"], actor=actor,
                                      client_address=where)  # fmt: skip
            db.commit()
            return RedirectResponse(f"/orders/{po_id}", status_code=303)
        except LedgerError as problem:
            db.rollback()
            errors.append(str(problem))
    return _page(request, db, user, order, kind, header, rows, errors, 400)


@router.post("/ack", response_class=HTMLResponse, response_model=None)
async def record_ack(request: Request, db: Db, user: CurrentUser, po_id: int) -> Response:
    return await _submit(request, db, user, po_id, "ack")


@router.post("/shipments", response_class=HTMLResponse, response_model=None)
async def record_shipment(request: Request, db: Db, user: CurrentUser, po_id: int) -> Response:
    return await _submit(request, db, user, po_id, "shipment")


@router.post("/invoices", response_class=HTMLResponse, response_model=None)
async def record_invoice(request: Request, db: Db, user: CurrentUser, po_id: int) -> Response:
    return await _submit(request, db, user, po_id, "invoice")


@router.post("/entries/{kind}/{entry_id}/remove", response_class=HTMLResponse, response_model=None)
async def remove_entry(request: Request, db: Db, user: CurrentUser, po_id: int, kind: str, entry_id: int) -> Response:
    order = _open(db, po_id)
    if order is None:
        return missing_order(request, user)
    form = await request.form(max_fields=20)
    try:
        events.remove_entry(db, order, kind, entry_id, str(form.get("reason", "")), actor=user,
                            client_address=client_address(request))  # fmt: skip
        db.commit()
    except LedgerError as problem:
        db.rollback()
        return order_view(request, db, user, order, error=str(problem), status=400)
    return RedirectResponse(f"/orders/{po_id}", status_code=303)
