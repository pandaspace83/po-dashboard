"""Alembic environment. The engine is handed in by po_dashboard.db.migrate, never read from a config file."""

from alembic import context

from po_dashboard.db import ledger, models  # noqa: F401  (registers the tables)
from po_dashboard.db.base import Base

engine = context.config.attributes["engine"]
with engine.connect() as connection:
    context.configure(connection=connection, target_metadata=Base.metadata, render_as_batch=True)
    with context.begin_transaction():
        context.run_migrations()
