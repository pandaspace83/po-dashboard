"""The open-items report and its CSV export (REQ-DB-03, PD-1.6)."""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Query, Request
from fastapi.responses import HTMLResponse, Response

from po_dashboard.ledger import reports, vendors
from po_dashboard.pages.common import CurrentUser, Db, render

router = APIRouter(prefix="/reports")


def _choice(show: str) -> str:
    return show if show in reports.SHOWS else "all"


def _vendor(value: str) -> int | None:
    return int(value) if value.isascii() and value.isdigit() and len(value) < 10 else None


@router.get("/open-items", response_class=HTMLResponse)
def open_items(request: Request, db: Db, user: CurrentUser, show: str = Query("all", max_length=20),
               vendor: str = Query("", max_length=10)) -> HTMLResponse:  # fmt: skip
    today = date.today()
    chosen, vendor_id = _choice(show), _vendor(vendor)
    return render(request, "open_items.html", user, items=reports.open_items(db, today, chosen, vendor_id),
                  show=chosen, shows=reports.SHOWS, vendor_id=vendor_id,
                  vendor_name=vendors.vendor_name_of(db, vendor_id),
                  today=today)  # fmt: skip


@router.get("/open-items.csv")
def open_items_csv(db: Db, user: CurrentUser, show: str = Query("all", max_length=20),
                   vendor: str = Query("", max_length=10)) -> Response:  # fmt: skip
    today = date.today()
    body = reports.to_csv(reports.open_items(db, today, _choice(show), _vendor(vendor)))
    name = f"open-items-{today.isoformat()}.csv"
    return Response(body, media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})  # fmt: skip
