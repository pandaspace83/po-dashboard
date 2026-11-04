"""The web app. Phase 0: one page with the health strip."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from po_dashboard import __version__
from po_dashboard.config import Settings
from po_dashboard.models import llm

HERE = Path(__file__).parent


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    app = FastAPI(title="PO Dashboard", version=__version__, docs_url=None, redoc_url=None)
    templates = Jinja2Templates(directory=HERE / "templates")

    def health() -> dict[str, str]:
        return {
            "app": "ok",
            "last_scan": "no scan yet",
            "model": "reachable" if llm.is_reachable(settings) else "not reachable",
            "model_name": settings.model,
        }

    @app.get("/", response_class=HTMLResponse)
    def home(request: Request) -> HTMLResponse:
        return templates.TemplateResponse(
            request, "home.html", {"health": health(), "version": __version__}
        )

    @app.get("/health")
    def health_check() -> JSONResponse:
        return JSONResponse(health())

    return app
