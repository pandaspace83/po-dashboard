"""Logging (engineering review, 2026-11-10; DEC-13). One local file, `logs/app.log` in the data folder, owner-only and
rotated. Records hold ids, counts, routes and timings: never a subject, a document, a file name, a query string, a
password or a token. Nothing leaves this Mac (DEC-01)."""

from __future__ import annotations

import logging
import os
import re
import secrets
from logging.handlers import RotatingFileHandler
from pathlib import Path

LOGGER = "po_dashboard"
LOG_NAME = "app.log"
FORMAT = "%(asctime)s %(levelname)s %(name)s %(message)s"
_NUMBER = re.compile(r"/\d+(?=/|$)")


def configure(data_dir: Path, level: int = logging.INFO) -> Path:
    """Send the app's own log to data_dir/logs/app.log (600, 1 MB, five files kept). Safe to call twice."""
    folder = data_dir / "logs"
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    folder.chmod(0o700)
    path = folder / LOG_NAME
    if not path.exists():
        os.close(os.open(path, os.O_WRONLY | os.O_CREAT, 0o600))
    logger = logging.getLogger(LOGGER)
    logger.setLevel(level)
    if not any(isinstance(h, RotatingFileHandler) and h.baseFilename == str(path) for h in logger.handlers):
        handler = RotatingFileHandler(path, maxBytes=1_000_000, backupCount=5, encoding="utf-8")
        handler.setFormatter(logging.Formatter(FORMAT))
        logger.addHandler(handler)
    logger.propagate = False
    return path


def request_id() -> str:
    return secrets.token_hex(6)


def route_of(path: str) -> str:
    """A path with its numbers masked and no query string: /orders/17/close becomes /orders/{n}/close."""
    return _NUMBER.sub("/{n}", path)[:120]
