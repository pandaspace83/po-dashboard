"""The SQLite database: one file in an owner-only data folder, WAL mode, foreign keys enforced (DEC-04, DEC-27)."""

from __future__ import annotations

import os
import stat
from pathlib import Path
from typing import Any

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.engine.interfaces import DBAPIConnection
from sqlalchemy.pool import ConnectionPoolEntry

from po_dashboard.config import Settings

DB_NAME = "po-dashboard.db"
SIDE_FILES = ("", "-wal", "-shm")


def database_path(settings: Settings) -> Path:
    return settings.data_dir / DB_NAME


def prepare_data_dir(data_dir: Path) -> None:
    """Create the data folder owner-only (700). Refuses to use an existing folder others can enter."""
    data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    mode = stat.S_IMODE(data_dir.stat().st_mode)
    if mode & 0o077:
        data_dir.chmod(0o700)


def secure_database_files(path: Path) -> None:
    """The database and its WAL side files are owner-only (600)."""
    for suffix in SIDE_FILES:
        side = Path(f"{path}{suffix}")
        if side.exists() and stat.S_IMODE(side.stat().st_mode) & 0o077:
            side.chmod(0o600)


def _on_connect(dbapi_connection: DBAPIConnection, _record: ConnectionPoolEntry) -> None:
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA busy_timeout=5000")
    cursor.execute("PRAGMA synchronous=NORMAL")
    cursor.close()


def make_engine(settings: Settings, **kwargs: Any) -> Engine:
    prepare_data_dir(settings.data_dir)
    path = database_path(settings)
    if not path.exists():
        # Create it with 600 from the first byte, so there is no moment when others can read it.
        os.close(os.open(path, os.O_WRONLY | os.O_CREAT, 0o600))
    engine = create_engine(f"sqlite:///{path}", **kwargs)
    event.listen(engine, "connect", _on_connect)
    return engine
