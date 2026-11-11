"""Apply migrations. Run on every start (ARCHITECTURE §11).

Before changing a database that already has data, it makes a backup of it (ops/backup.py) in the data folder's
`backups/` folder, and refuses to start if that backup fails. It also refuses a database that a newer version of the
app has changed, instead of failing deep inside Alembic."""

from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import Engine

from po_dashboard.config import Settings
from po_dashboard.db.engine import database_path, make_engine, secure_database_files

MIGRATIONS = Path(__file__).parent / "migrations"


class MigrationRefused(RuntimeError):
    """The database cannot be used by this version of the app. The message says what to do."""


def alembic_config(engine: Engine) -> Config:
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS))
    config.attributes["engine"] = engine
    return config


def upgrade(settings: Settings) -> Engine:
    engine = make_engine(settings)
    config = alembic_config(engine)
    scripts = ScriptDirectory.from_config(config)
    with engine.connect() as conn:
        current = MigrationContext.configure(conn).get_current_revision()
    known = {script.revision for script in scripts.walk_revisions()}
    if current is not None and current not in known:
        engine.dispose()
        raise MigrationRefused(
            f"This database was changed by a newer version of the app (revision {current}). "
            "Update the app, or restore a backup made before that upgrade."
        )
    if current is not None and current not in scripts.get_heads():
        from po_dashboard.ops import backup  # a database with data is about to change: keep the old one first

        try:
            backup.create_backup(
                settings, settings.data_dir / "backups", label=f"before-{current}", inside_data_folder=True
            )
        except backup.BackupError as problem:
            engine.dispose()
            raise MigrationRefused(
                f"The backup before the upgrade failed, so nothing was changed: {problem}"
            ) from problem
    command.upgrade(config, "head")
    secure_database_files(database_path(settings))
    return engine
