"""Backup and restore (engineering review, 2026-11-10; NFR-06): the stored originals cannot be recreated, so a backup
is tested by restoring it and comparing, not by trusting that it was written."""

from __future__ import annotations

import hashlib
import sqlite3
import stat
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

import pytest
from alembic import command
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from po_dashboard import cli
from po_dashboard.config import Settings
from po_dashboard.db import migrate
from po_dashboard.db.engine import make_engine
from po_dashboard.db.mail import Mailbox, SourceMessage
from po_dashboard.db.migrate import MigrationRefused, alembic_config, upgrade
from po_dashboard.intake.store import FileStore
from po_dashboard.ops import backup
from po_dashboard.ops.backup import BackupError, create_backup, restore_backup, verify_backup

NOW = datetime(2026, 11, 11, 9, 0, tzinfo=UTC)


def populate(settings: Settings, originals: int = 3) -> list[bytes]:
    """A data folder with a mailbox, messages in the database and the same messages in the file store."""
    engine = upgrade(settings)
    store = FileStore(settings.data_dir / "originals")
    sent: list[bytes] = []
    with Session(engine) as db:
        box = Mailbox(address="p@example.test", provider="folder", location="/x")
        db.add(box)
        db.flush()
        for n in range(originals):
            raw = f"Subject: Made-up message {n}\n\nBody {n} PO 4500{n}\n".encode()
            digest, path = store.put(raw)
            db.add(SourceMessage(mailbox_id=box.id, folder="Inbox", provider_id=str(n), received_at=NOW,
                                 raw_path=path, content_hash=digest, size=len(raw)))
            sent.append(raw)
        db.commit()
    engine.dispose()
    return sent


@pytest.fixture
def live(tmp_path: Path) -> Settings:
    return Settings(data_dir=tmp_path / "data")


def rows(data_dir: Path) -> list[tuple[int, str]]:
    with closing(sqlite3.connect(data_dir / "po-dashboard.db")) as conn:
        return conn.execute("select id, content_hash from source_message order by id").fetchall()


def test_a_backup_restores_to_exactly_what_was_backed_up(live: Settings, tmp_path: Path) -> None:
    sent = populate(live)
    report = create_backup(live, tmp_path / "Backups", now=NOW)
    assert report.files == 3 and report.revision == "0010"
    assert verify_backup(report.folder).files == 3
    restored = tmp_path / "restored"
    restore_backup(report.folder, restored)
    assert rows(restored) == rows(live.data_dir) and len(rows(restored)) == 3
    for raw in sent:
        digest = hashlib.sha256(raw).hexdigest()
        assert (restored / "originals" / digest[:2] / digest).read_bytes() == raw
    with closing(sqlite3.connect(restored / "po-dashboard.db")) as conn:
        assert conn.execute("pragma integrity_check").fetchone() == ("ok",)
    upgrade(Settings(data_dir=restored)).dispose()  # the restored folder opens and needs no change


def test_the_snapshot_holds_committed_rows_still_in_the_write_ahead_log(live: Settings, tmp_path: Path) -> None:
    populate(live, originals=1)
    engine = make_engine(live)
    with engine.begin() as conn:
        conn.execute(text("update mailbox set address = 'changed@example.test'"))
    wal = live.data_dir / "po-dashboard.db-wal"
    assert wal.exists() and wal.stat().st_size > 0  # the new value may be only in the log, not the main file
    report = create_backup(live, tmp_path / "Backups", now=NOW)
    engine.dispose()
    with closing(sqlite3.connect(report.folder / "po-dashboard.db")) as conn:
        assert conn.execute("select address from mailbox").fetchone() == ("changed@example.test",)


def test_a_backup_is_private(live: Settings, tmp_path: Path) -> None:
    populate(live)
    report = create_backup(live, tmp_path / "Backups", now=NOW)
    for path in [report.folder, *report.folder.rglob("*")]:
        assert stat.S_IMODE(path.stat().st_mode) & 0o077 == 0, path


def test_nothing_is_overwritten_or_pruned(live: Settings, tmp_path: Path) -> None:
    populate(live)
    first = create_backup(live, tmp_path / "Backups", now=NOW)
    with pytest.raises(BackupError, match="never overwritten"):
        create_backup(live, tmp_path / "Backups", now=NOW)
    later = create_backup(live, tmp_path / "Backups", now=datetime(2026, 12, 20, tzinfo=UTC))  # 39 days on
    assert first.folder.is_dir() and later.folder.is_dir()


@pytest.mark.parametrize("where", ["Desktop/b", "Documents/b", "Library/Mobile Documents/b", "Library/CloudStorage/b"])
def test_a_synced_folder_is_refused(live: Settings, tmp_path: Path, monkeypatch, where: str) -> None:  # noqa: ANN001
    populate(live)
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    with pytest.raises(BackupError, match="syncs"):
        create_backup(live, tmp_path / "home" / where, now=NOW)
    assert not (tmp_path / "home").exists()


def test_a_backup_inside_the_data_folder_is_refused(live: Settings) -> None:
    populate(live)
    with pytest.raises(BackupError, match="lost with the folder"):
        create_backup(live, live.data_dir / "backups", now=NOW)


def test_there_is_nothing_to_back_up_before_the_first_start(live: Settings, tmp_path: Path) -> None:
    with pytest.raises(BackupError, match="no database"):
        create_backup(live, tmp_path / "Backups", now=NOW)


def test_a_damaged_backup_is_refused_before_it_is_restored(live: Settings, tmp_path: Path) -> None:
    populate(live)
    report = create_backup(live, tmp_path / "Backups", now=NOW)
    original = next((report.folder / "originals").rglob("*"))
    while original.is_dir():
        original = next(original.iterdir())
    original.write_bytes(b"tampered")
    with pytest.raises(BackupError, match="damaged"):
        restore_backup(report.folder, tmp_path / "restored")
    assert not (tmp_path / "restored").exists()


def test_a_changed_database_or_a_missing_manifest_is_refused(live: Settings, tmp_path: Path) -> None:
    populate(live)
    report = create_backup(live, tmp_path / "Backups", now=NOW)
    (report.folder / "po-dashboard.db").write_bytes(b"not a database")
    with pytest.raises(BackupError, match="does not match"):
        verify_backup(report.folder)
    (report.folder / "manifest.json").unlink()
    with pytest.raises(BackupError, match="not a backup"):
        verify_backup(report.folder)


def test_a_restore_never_goes_over_a_data_folder_that_has_a_database(live: Settings, tmp_path: Path) -> None:
    populate(live)
    report = create_backup(live, tmp_path / "Backups", now=NOW)
    before = rows(live.data_dir)
    with pytest.raises(BackupError, match="new, empty folder"):
        restore_backup(report.folder, live.data_dir)
    assert rows(live.data_dir) == before


def test_an_upgrade_of_a_database_with_data_makes_a_backup_first(live: Settings) -> None:
    populate(live)
    engine = make_engine(live)
    command.downgrade(alembic_config(engine), "0004")  # as if the app were updated from an older version
    engine.dispose()
    upgrade(live).dispose()
    made = sorted((live.data_dir / "backups").glob("po-dashboard-before-0004-*"))
    assert len(made) == 1 and verify_backup(made[0]).revision == "0004"


def test_a_first_start_and_a_start_with_nothing_to_upgrade_make_no_backup(live: Settings) -> None:
    upgrade(live).dispose()
    upgrade(live).dispose()
    assert not (live.data_dir / "backups").exists()


def test_when_the_backup_fails_nothing_is_changed_and_the_app_does_not_start(
    live: Settings, monkeypatch,  # noqa: ANN001
) -> None:
    populate(live)
    engine = make_engine(live)
    command.downgrade(alembic_config(engine), "0004")
    engine.dispose()

    def refuse(*a: object, **k: object) -> None:
        raise BackupError("the disk is full")

    monkeypatch.setattr(backup, "create_backup", refuse)
    with pytest.raises(MigrationRefused, match="nothing was changed"):
        upgrade(live)
    assert backup.revision_of(live.data_dir / "po-dashboard.db") == "0004"


def test_a_database_from_a_newer_app_is_refused_in_plain_words(live: Settings) -> None:
    populate(live)
    with closing(sqlite3.connect(live.data_dir / "po-dashboard.db")) as conn:
        conn.execute("update alembic_version set version_num = '9999'")
        conn.commit()
    with pytest.raises(MigrationRefused, match="newer version"):
        upgrade(live)
    assert backup.revision_of(live.data_dir / "po-dashboard.db") == "9999"
    assert migrate.MigrationRefused is MigrationRefused


def run(monkeypatch, capsys, data: Path, *argv: str) -> tuple[int, str, str]:  # noqa: ANN001
    monkeypatch.setenv("PO_DASHBOARD_DATA", str(data))
    with pytest.raises(SystemExit) as stop:
        cli.main(list(argv))
    captured = capsys.readouterr()
    return int(stop.value.code or 0), captured.out, captured.err


def test_the_commands_back_up_and_restore_and_print_no_message_text(
    live: Settings, tmp_path: Path, monkeypatch, capsys,  # noqa: ANN001
) -> None:
    populate(live)
    code, out, _ = run(monkeypatch, capsys, live.data_dir, "backup", "--to", str(tmp_path / "Backups"))
    assert code == 0 and "3 stored originals" in out and "Made-up message" not in out
    folder = next((tmp_path / "Backups").iterdir())
    code, out, _ = run(monkeypatch, capsys, live.data_dir, "restore", str(folder), "--into", str(tmp_path / "again"))
    assert code == 0 and rows(tmp_path / "again") == rows(live.data_dir) and "Made-up message" not in out


def test_the_commands_say_why_in_words_when_they_refuse(
    live: Settings, tmp_path: Path, monkeypatch, capsys,  # noqa: ANN001
) -> None:
    populate(live)
    code, _, err = run(monkeypatch, capsys, live.data_dir, "backup", "--to", str(live.data_dir / "inside"))
    assert code == 1 and "lost with the folder" in err
    missing = str(tmp_path / "nope")
    code, _, err = run(monkeypatch, capsys, live.data_dir, "restore", missing, "--into", str(tmp_path / "x"))
    assert code == 1 and "not a backup" in err


def test_the_select_import_is_used(live: Settings) -> None:
    populate(live, originals=1)
    with Session(make_engine(live)) as db:
        assert len(db.scalars(select(SourceMessage)).all()) == 1
