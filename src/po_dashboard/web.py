"""The web app: the gate in front, then pages (ARCHITECTURE §9, §10.1)."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import Engine

from po_dashboard import __version__
from po_dashboard.auth.gate import Gate
from po_dashboard.config import Settings
from po_dashboard.db.migrate import upgrade
from po_dashboard.pages import account, admin, orders, vendors
from po_dashboard.pages.formats import money, quantity

HERE = Path(__file__).parent
APP_NAME = "RaceSpec Internal Dashboard"


def create_app(settings: Settings | None = None, engine: Engine | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    engine = engine or upgrade(settings)
    app = FastAPI(
        title="PO Dashboard", version=__version__, docs_url=None, redoc_url=None, openapi_url=None
    )
    app.state.settings = settings
    app.state.engine = engine
    app.state.templates = Jinja2Templates(directory=HERE / "templates")
    app.state.templates.env.globals.update(
        app_name=APP_NAME, has_logo=(HERE / "static" / "brand" / "logo.png").is_file()
    )

    app.state.templates.env.filters.update(quantity=quantity, money=money)

    @app.get("/health")
    def health() -> JSONResponse:
        """Up or down only. Model and scan details are on the signed-in home page."""
        return JSONResponse({"status": "ok"})

    app.include_router(account.router)
    app.include_router(admin.router)
    app.include_router(vendors.router)
    app.include_router(orders.router)
    app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
    app.add_middleware(Gate, settings=settings, engine=engine)
    return app
