import stat
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import Engine, inspect, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from po_dashboard.config import Settings
from po_dashboard.db import models
from po_dashboard.db.base import Base
from po_dashboard.db.engine import database_path
from po_dashboard.db.migrate import upgrade


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(data_dir=tmp_path / "data")


@pytest.fixture
def engine(settings: Settings) -> Engine:
    eng = upgrade(settings)
    yield eng
    eng.dispose()


def mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def add_user(session: Session, email: str = "a@example.com", role: str = "buyer") -> models.AppUser:
    user = models.AppUser(email=email, password_hash="x", role=role)
    session.add(user)
    session.flush()
    return user


def test_creates_the_sign_in_tables(engine: Engine) -> None:
    tables = set(inspect(engine).get_table_names())
    assert {"app_user", "session", "recovery_code", "audit_log"} <= tables


# SQLAlchemy cannot reflect the case-blind invoice index (an expression index, migration 0006) on SQLite and says so;
# tests/test_concurrency.py proves the database enforces it.
@pytest.mark.filterwarnings("ignore:Skipped unsupported reflection of expression-based index")
def test_migration_matches_the_models(engine: Engine) -> None:
    with engine.connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn), Base.metadata)
    assert diff == []


def test_upgrade_twice_is_harmless(settings: Settings, engine: Engine) -> None:
    upgrade(settings).dispose()


def test_wal_and_foreign_keys_are_on(engine: Engine) -> None:
    with engine.connect() as conn:
        assert conn.execute(text("PRAGMA journal_mode")).scalar() == "wal"
        assert conn.execute(text("PRAGMA foreign_keys")).scalar() == 1


def test_data_folder_and_database_are_owner_only(settings: Settings, engine: Engine) -> None:
    with Session(engine) as s:
        add_user(s)
        s.commit()
    assert mode(settings.data_dir) == 0o700
    for suffix in ("", "-wal", "-shm"):
        side = Path(f"{database_path(settings)}{suffix}")
        if side.exists():
            assert mode(side) == 0o600, side.name


def test_an_open_data_folder_is_tightened(tmp_path: Path) -> None:
    folder = tmp_path / "data"
    folder.mkdir(mode=0o755)
    folder.chmod(0o755)
    upgrade(Settings(data_dir=folder)).dispose()
    assert mode(folder) == 0o700


def test_email_is_unique(engine: Engine) -> None:
    with Session(engine) as s:
        add_user(s)
        s.commit()
        with pytest.raises(IntegrityError):
            add_user(s)


def test_an_unknown_role_is_refused(engine: Engine) -> None:
    with Session(engine) as s, pytest.raises(IntegrityError):
        add_user(s, role="superuser")


def test_a_session_needs_a_real_user(engine: Engine) -> None:
    with Session(engine) as s, pytest.raises(IntegrityError):
        s.add(
            models.SignInSession(
                user_id=999, token_hash="h", stage="full", csrf_token="c",
                expires_at=datetime.now(UTC) + timedelta(hours=1),
            )
        )  # fmt: skip
        s.flush()


def test_deleting_a_user_keeps_the_audit_entry(engine: Engine) -> None:
    with Session(engine) as s:
        user = add_user(s)
        s.add(models.AuditLog(user_id=user.id, action="sign_in"))
        s.flush()
        s.delete(user)
        s.commit()
        entry = s.query(models.AuditLog).one()
        assert entry.user_id is None and entry.action == "sign_in"


def test_timestamps_come_back_in_utc_and_naive_ones_are_refused(engine: Engine) -> None:
    when = datetime(2026, 11, 4, 12, 0, tzinfo=UTC)
    with Session(engine) as s:
        user = add_user(s)
        user.locked_until = when
        s.commit()
        assert s.get(models.AppUser, user.id).locked_until == when  # type: ignore[union-attr]
        user.locked_until = datetime(2026, 11, 4, 12, 0)
        with pytest.raises(Exception, match="time zone"):
            s.commit()
