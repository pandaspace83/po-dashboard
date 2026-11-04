"""Apply migrations. Run on every start. Backing up first is added in PD-6.5 (ARCHITECTURE §11)."""

from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import Engine

from po_dashboard.config import Settings
from po_dashboard.db.engine import database_path, make_engine, secure_database_files

MIGRATIONS = Path(__file__).parent / "migrations"


def alembic_config(engine: Engine) -> Config:
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS))
    config.attributes["engine"] = engine
    return config


def upgrade(settings: Settings) -> Engine:
    engine = make_engine(settings)
    command.upgrade(alembic_config(engine), "head")
    secure_database_files(database_path(settings))
    return engine
