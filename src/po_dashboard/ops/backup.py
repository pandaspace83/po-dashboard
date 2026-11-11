"""Backup and restore (engineering review, 2026-11-10; NFR-06; the rest of PD-6.5 follows).

A backup is one folder: a consistent copy of the database, made with SQLite's own online-backup call (never a file
copy of a database in use, which can be torn by the write-ahead log), every stored original, and a manifest of hashes.
Stored originals cannot be recreated once the mailbox has moved on, so they are the part that matters most.

Rules this module keeps:
- It never overwrites or deletes anything. A backup folder that exists is refused, and a restore goes only into a
  data folder that has no database yet. Old backups are never pruned here: removing files needs the owner's yes.
- It refuses a destination in iCloud or a synced folder (CLAUDE.md), or inside the data folder it is protecting.
- It holds no message text in what it prints or logs: counts, hashes and paths only."""

from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from po_dashboard import __version__
from po_dashboard.config import Settings
from po_dashboard.db.engine import database_path
from po_dashboard.intake.store import HASH

MANIFEST = "manifest.json"
DB_NAME = "po-dashboard.db"
ORIGINALS = "originals"
SYNCED = ("Desktop", "Documents", "Library/Mobile Documents", "Library/CloudStorage")


class BackupError(RuntimeError):
    """A backup or restore was refused or failed. The message says what to do and holds no message text."""


@dataclass(frozen=True)
class Report:
    folder: Path
    files: int
    database_bytes: int
    revision: str | None


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def refuse_unsafe_destination(destination: Path, protected: Path | None = None) -> None:
    resolved = destination.expanduser().resolve()
    home = Path.home().resolve()
    for name in SYNCED:
        folder = home / name
        if resolved == folder or folder in resolved.parents:
            raise BackupError(
                f"{resolved} is in a folder that syncs to iCloud or the cloud. Pick a folder on this Mac, "
                "such as ~/Backups."
            )
    if protected is not None:
        inside = protected.resolve()
        if resolved == inside or inside in resolved.parents:
            raise BackupError("A backup inside the data folder is not a backup: it is lost with the folder.")


def revision_of(database: Path) -> str | None:
    try:
        with closing(sqlite3.connect(f"file:{database}?mode=ro", uri=True)) as conn:
            row = conn.execute("select version_num from alembic_version").fetchone()
    except sqlite3.Error:
        return None
    return str(row[0]) if row else None


def private_folder(path: Path, top: Path) -> None:
    """Make `path` and every folder between it and `top` owner-only (mkdir's mode reaches only the last one)."""
    path.mkdir(parents=True, exist_ok=True)
    for folder in [path, *path.parents]:
        folder.chmod(0o700)
        if folder == top:
            break


def create_backup(
    settings: Settings, destination_root: Path, *, now: datetime | None = None, label: str = "backup",
    inside_data_folder: bool = False,
) -> Report:
    """Write a new backup folder under `destination_root` and check it. Returns what it holds.

    `inside_data_folder` is only for the safety copy made just before an upgrade: it guards against a failed
    migration, not against losing the disk, so it may live beside the data it protects."""
    refuse_unsafe_destination(destination_root, None if inside_data_folder else settings.data_dir)
    source = database_path(settings)
    if not source.is_file():
        raise BackupError("There is no database to back up yet.")
    stamp = (now or datetime.now(UTC)).strftime("%Y%m%d-%H%M%S")
    folder = destination_root.expanduser() / f"po-dashboard-{label}-{stamp}"
    if folder.exists():
        raise BackupError(f"{folder.name} already exists. A backup is never overwritten.")
    private_folder(folder, folder)
    try:
        copied = folder / DB_NAME
        with closing(sqlite3.connect(f"file:{source}?mode=ro", uri=True)) as live, closing(
            sqlite3.connect(copied)
        ) as target:
            live.backup(target)  # a consistent snapshot, even while the app is writing
        with closing(sqlite3.connect(copied)) as check:
            result = check.execute("pragma integrity_check").fetchone()
        if not result or result[0] != "ok":
            raise BackupError("The copy of the database failed its integrity check. Nothing was kept.")
        copied.chmod(0o600)
        files = 0
        originals = settings.data_dir / ORIGINALS
        if originals.is_dir():
            for path in sorted(originals.rglob("*")):
                if not path.is_file() or path.name.startswith("."):
                    continue
                if not HASH.fullmatch(path.name) or path.parent.name != path.name[:2]:
                    continue  # not one of ours
                target_file = folder / ORIGINALS / path.parent.name / path.name
                private_folder(target_file.parent, folder)
                shutil.copyfile(path, target_file)
                target_file.chmod(0o600)
                if sha256_of(target_file) != path.name:
                    raise BackupError("A stored original did not copy exactly. Nothing was kept.")
                files += 1
        revision = revision_of(copied)
        size = copied.stat().st_size
        manifest = {"made_at": (now or datetime.now(UTC)).isoformat(), "app_version": __version__,
                    "database_sha256": sha256_of(copied), "database_bytes": size, "revision": revision,
                    "originals": files}
        (folder / MANIFEST).write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        (folder / MANIFEST).chmod(0o600)
    except BaseException:
        shutil.rmtree(folder, ignore_errors=True)  # only the folder this call just made, never an older backup
        raise
    return Report(folder, files, size, revision)


def verify_backup(folder: Path) -> Report:
    """Check a backup folder against its own manifest: the database hash and integrity, and every original's hash."""
    try:
        manifest = json.loads((folder / MANIFEST).read_text(encoding="utf-8"))
    except (OSError, ValueError) as problem:
        raise BackupError("That folder is not a backup: its manifest is missing or unreadable.") from problem
    database = folder / DB_NAME
    if not database.is_file() or sha256_of(database) != manifest.get("database_sha256"):
        raise BackupError("The backup's database does not match its manifest. Do not restore it.")
    with closing(sqlite3.connect(f"file:{database}?mode=ro", uri=True)) as conn:
        result = conn.execute("pragma integrity_check").fetchone()
    if not result or result[0] != "ok":
        raise BackupError("The backup's database failed its integrity check. Do not restore it.")
    files = 0
    for path in sorted((folder / ORIGINALS).rglob("*")) if (folder / ORIGINALS).is_dir() else []:
        if path.is_file():
            if sha256_of(path) != path.name:
                raise BackupError("A stored original in the backup is damaged. Do not restore it.")
            files += 1
    if files != manifest.get("originals"):
        raise BackupError("The backup has a different number of stored originals than its manifest says.")
    return Report(folder, files, int(manifest["database_bytes"]), manifest.get("revision"))


def restore_backup(folder: Path, data_dir: Path) -> Report:
    """Restore into a data folder that has no database. The backup is verified first; nothing existing is touched."""
    report = verify_backup(folder)
    target_db = data_dir / DB_NAME
    if target_db.exists() or (data_dir / ORIGINALS).exists():
        raise BackupError(
            "That data folder already has a database or stored originals. Restore into a new, empty folder, "
            "then move the old one aside yourself once you have checked the result."
        )
    private_folder(data_dir, data_dir)
    shutil.copyfile(folder / DB_NAME, target_db)
    target_db.chmod(0o600)
    for path in sorted((folder / ORIGINALS).rglob("*")) if (folder / ORIGINALS).is_dir() else []:
        if path.is_file():
            out = data_dir / ORIGINALS / path.parent.name / path.name
            private_folder(out.parent, data_dir)
            shutil.copyfile(path, out)
            out.chmod(0o600)
    return report
