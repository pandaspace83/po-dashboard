"""The audit trail screen, for admins only (NFR-03, PD-1.7). It only reads: nothing here can change or erase a row."""

from __future__ import annotations

from datetime import UTC, datetime, time, timedelta

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import func, select

from po_dashboard.db.models import AppUser, AuditLog
from po_dashboard.ledger.errors import LedgerError, when
from po_dashboard.pages.common import Admin, Db, render, require_admin

router = APIRouter(prefix="/admin/audit", dependencies=[Depends(require_admin)])
PAGE_SIZE = 50
SHOWN_CHARS = 400  # a long before/after is cut for the table; the row itself is kept whole


def _cut(value: str | None) -> str:
    if not value:
        return ""
    return value if len(value) <= SHOWN_CHARS else value[:SHOWN_CHARS] + " ..."


@router.get("", response_class=HTMLResponse)
def audit_trail(request: Request, db: Db, admin: Admin, action: str = Query("", max_length=64),
                person: str = Query("", max_length=10), q: str = Query("", max_length=100),
                date_from: str = Query("", max_length=10), date_to: str = Query("", max_length=10),
                page: int = Query(1, ge=1, le=100000)) -> HTMLResponse:  # fmt: skip
    problems: list[str] = []

    def day(value: str, label: str) -> datetime | None:
        try:
            found = when(value, label)
        except LedgerError as problem:
            problems.append(str(problem))
            return None
        return None if found is None else datetime.combine(found, time.min, tzinfo=UTC)

    start, end = day(date_from, "From"), day(date_to, "To")
    who = int(person) if person.isascii() and person.isdigit() and len(person) < 10 else None
    query = select(AuditLog, AppUser.email).join(AppUser, AppUser.id == AuditLog.user_id, isouter=True)
    if action.strip():
        query = query.where(AuditLog.action == action.strip())
    if who is not None:
        query = query.where(AuditLog.user_id == who)
    if q.strip():
        query = query.where(AuditLog.object.icontains(q.strip(), autoescape=True))
    if start:
        query = query.where(AuditLog.created_at >= start)
    if end:
        query = query.where(AuditLog.created_at < end + timedelta(days=1))
    total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    rows = db.execute(
        query.order_by(AuditLog.created_at.desc(), AuditLog.id.desc()).limit(PAGE_SIZE).offset((page - 1) * PAGE_SIZE)
    ).all()
    entries = [(row, email, _cut(row.before), _cut(row.after)) for row, email in rows]
    actions = list(db.scalars(select(AuditLog.action).distinct().order_by(AuditLog.action)))
    people = list(db.execute(select(AppUser.id, AppUser.email).order_by(AppUser.email)))
    pages = max((total + PAGE_SIZE - 1) // PAGE_SIZE, 1)
    keep = {"action": action, "person": person, "q": q, "date_from": date_from, "date_to": date_to}
    return render(request, "audit.html", admin, entries=entries, total=total, page=page, pages=pages, actions=actions,
                  people=people, f=keep, who=who, problems=problems)  # fmt: skip
