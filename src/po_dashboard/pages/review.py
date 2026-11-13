"""The Review screens (PD-4.4, ARCHITECTURE section 6.4, DEC-54): the documents the model read, waiting for a person.
Anyone signed in may use them (DEC-29); every action is audited. The original is served as a download only."""

from __future__ import annotations

from collections.abc import Callable
from typing import Annotated

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from po_dashboard.db.mail import Extraction, SourceDocument, SourceMessage
from po_dashboard.db.models import AppUser
from po_dashboard.extraction import accept as accepting
from po_dashboard.extraction import review
from po_dashboard.intake.store import FileStore
from po_dashboard.ledger.errors import LedgerError, parse_seen
from po_dashboard.pages import tables
from po_dashboard.pages.common import CurrentUser, Db, render

router = APIRouter(prefix="/review")


def _store(request: Request) -> FileStore:
    return FileStore(request.app.state.settings.data_dir / "originals")


@router.get("", response_class=HTMLResponse)
def index(request: Request, db: Db, user: CurrentUser) -> HTMLResponse:
    show = request.query_params.get("show", "waiting")
    show = show if show in review.SHOWS else "waiting"
    page = tables.page_of(review.listing(db, show), request.query_params.get("page"))
    return render(
        request, "review.html", user, page=page, show=show, titles=review.TITLES, counts=review.counts(db),
    )


def _message_page(
    request: Request, db: Db, user: AppUser, message_id: int, error: str | None = None, status: int = 200
) -> HTMLResponse:
    found = review.message_view(db, _store(request), message_id)
    if found is None:
        raise HTTPException(404)
    message, documents = found
    return render(request, "review_message.html", user, status, message=message, documents=documents, error=error)


@router.get("/message/{message_id}", response_class=HTMLResponse)
def message_page(request: Request, db: Db, user: CurrentUser, message_id: int) -> HTMLResponse:
    return _message_page(request, db, user, message_id)


def _then(
    db: Db, request: Request, user: AppUser, extraction_id: int, act: Callable[[], object],
    where: Callable[[int], str],
) -> Response:
    """Run one action; on a refusal show the message's page again with the reason, otherwise go on to `where`."""
    row = db.get(Extraction, extraction_id)
    document = db.get(SourceDocument, row.document_id) if row else None
    if document is None:
        raise HTTPException(404)
    try:
        act()
        db.commit()
    except LedgerError as problem:
        db.rollback()
        return _message_page(request, db, user, document.message_id, str(problem), 400)
    return RedirectResponse(where(document.message_id), status_code=303)


@router.post("/{extraction_id}/accept", response_class=HTMLResponse, response_model=None)
async def accept(request: Request, db: Db, user: CurrentUser, extraction_id: int) -> Response:
    """Accept with the values in the form (DEC-08). The form's field names are in the Review page; the token and the
    extraction's version come with it, so a second Accept of the same document is refused."""
    form = {key: value for key, value in (await request.form()).items() if isinstance(value, str)}
    client = request.client.host if request.client else None

    def act() -> None:
        accepting.accept(db, extraction_id, parse_seen(form.get("version")), form, user, client)

    return _then(db, request, user, extraction_id, act, lambda message_id: f"/review/message/{message_id}")


@router.post("/{extraction_id}/reject", response_class=HTMLResponse, response_model=None)
def reject(
    request: Request, db: Db, user: CurrentUser, extraction_id: int,
    version: Annotated[str, Form()] = "", reason: Annotated[str, Form()] = "",
) -> Response:  # fmt: skip
    def act() -> None:
        review.reject(db, extraction_id, parse_seen(version), reason, user)

    return _then(db, request, user, extraction_id, act, lambda _message: "/review")


@router.post("/{extraction_id}/file", response_class=HTMLResponse, response_model=None)
def file_it(
    request: Request, db: Db, user: CurrentUser, extraction_id: int, version: Annotated[str, Form()] = ""
) -> Response:
    def act() -> None:
        review.file_as_no_facts(db, extraction_id, parse_seen(version), user)

    return _then(db, request, user, extraction_id, act, lambda _message: "/review?show=no_facts")


@router.post("/{extraction_id}/attach", response_class=HTMLResponse, response_model=None)
def attach(
    request: Request, db: Db, user: CurrentUser, extraction_id: int, version: Annotated[str, Form()] = "",
    po_id: Annotated[str, Form()] = "", po_number: Annotated[str, Form()] = "",
) -> Response:  # fmt: skip
    def act() -> None:
        found = review.find_po(db, po_id, po_number)
        review.attach(db, _store(request), extraction_id, parse_seen(version), found, user)

    return _then(db, request, user, extraction_id, act, lambda message_id: f"/review/message/{message_id}")


@router.get("/original/{document_id}")
def original(request: Request, db: Db, user: CurrentUser, document_id: int) -> Response:
    document = db.get(SourceDocument, document_id)
    message = db.get(SourceMessage, document.message_id) if document else None
    if document is None or message is None:
        raise HTTPException(404)
    view = request.query_params.get("view") == "1"
    try:
        data, media_type, name, inline = review.original(document, message, _store(request), view)
    except (OSError, ValueError) as problem:
        raise HTTPException(404) from problem
    headers = {
        "Content-Disposition": f'{"inline" if inline else "attachment"}; filename="{name}"',
        "X-Content-Type-Options": "nosniff",
        "Cache-Control": "no-store",
    }
    if inline:
        headers["Content-Security-Policy"] = "sandbox"
    return Response(data, media_type=media_type, headers=headers)
