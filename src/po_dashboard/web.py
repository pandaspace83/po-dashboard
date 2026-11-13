"""The web app: the gate in front, then pages (ARCHITECTURE §9, §10.1)."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import Engine, text
from starlette.exceptions import HTTPException

from po_dashboard import __version__
from po_dashboard.auth.gate import Gate
from po_dashboard.config import Settings
from po_dashboard.db.migrate import upgrade
from po_dashboard.display import day, moment
from po_dashboard.extraction import review as review_rules
from po_dashboard.extraction.runner import Extractor
from po_dashboard.intake.store import FileStore
from po_dashboard.mail.gmail_auth import Keychain
from po_dashboard.models import llm
from po_dashboard.pages import (
    account,
    admin,
    audit,
    customer_orders,
    customers,
    documents,
    entries,
    errors,
    mailboxes,
    orders,
    quickbooks,
    reports,
    review,
    sales_invoices,
    vendors,
)
from po_dashboard.pages.formats import money, quantity
from po_dashboard.pages.tables import with_query
from po_dashboard.scheduler.loop import run_extractor, run_forever
from po_dashboard.scheduler.scanner import Scanner

HERE = Path(__file__).parent
APP_NAME = "RaceSpec Internal Dashboard"


def create_app(
    settings: Settings | None = None, engine: Engine | None = None, *, start_scanner: bool = False
) -> FastAPI:
    """`start_scanner` is true only for `po-dashboard serve`: tests and tools build the app without a clock."""
    settings = settings or Settings.from_env()
    engine = engine or upgrade(settings)

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        tasks: list[asyncio.Task[None]] = []
        if start_scanner:  # the two clocks: mail comes in, and the model reads (DEC-54)
            store = FileStore(settings.data_dir / "originals")
            tasks.append(asyncio.create_task(run_forever(Scanner(engine, store, model=settings.model))))
            tasks.append(asyncio.create_task(run_extractor(Extractor(engine, store, settings, extract=llm.extract))))
        try:
            yield
        finally:
            for task in tasks:
                task.cancel()

    app = FastAPI(
        title="PO Dashboard", version=__version__, docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan
    )
    app.state.settings = settings
    app.state.engine = engine
    app.state.secrets = Keychain()  # the macOS Keychain; tests put an in-memory one here
    app.state.google_http = None  # the HTTP client for Google's calls; tests put a pretend Google here
    app.state.templates = Jinja2Templates(directory=HERE / "templates")
    app.state.templates.env.globals.update(
        app_name=APP_NAME, has_logo=(HERE / "static" / "brand" / "logo.png").is_file(), with_query=with_query
    )

    app.state.templates.env.filters.update(
        quantity=quantity, money=money, day=day, moment=moment, flag_count=review_rules.flag_count
    )

    @app.get("/health")
    def health() -> JSONResponse:
        """Up or down only (down if the database cannot be read). Model and scan details need a sign-in."""
        try:
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
        except Exception:
            return JSONResponse({"status": "down"}, status_code=503)
        return JSONResponse({"status": "ok"})

    app.include_router(account.router)
    app.include_router(admin.router)
    app.include_router(documents.router)
    app.include_router(review.router)
    app.include_router(customers.router)
    app.include_router(customer_orders.router)
    app.include_router(sales_invoices.router)
    app.include_router(mailboxes.router)
    app.include_router(mailboxes.public)
    app.include_router(audit.router)
    app.include_router(vendors.router)
    app.include_router(quickbooks.router)  # before orders: /orders/quickbooks is not /orders/{id}
    app.include_router(orders.router)
    app.include_router(entries.router)
    app.include_router(reports.router)
    app.add_exception_handler(HTTPException, errors.http_error)
    app.add_exception_handler(RequestValidationError, errors.validation_error)
    app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
    app.add_middleware(Gate, settings=settings, engine=engine)
    return app
