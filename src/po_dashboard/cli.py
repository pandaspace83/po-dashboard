"""Command line: `po-dashboard serve`."""

from __future__ import annotations

import argparse

import uvicorn

from po_dashboard.config import Settings
from po_dashboard.web import create_app


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="po-dashboard")
    parser.add_argument("command", choices=["serve"])
    parser.parse_args(argv)
    settings = Settings.from_env()
    uvicorn.run(create_app(settings), host=settings.host, port=settings.port)
