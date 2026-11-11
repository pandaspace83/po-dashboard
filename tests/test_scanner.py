"""The scanner (PD-3.4): no loss, no duplicate, one scan at a time, a cap per tick, short write locks."""

from __future__ import annotations

import asyncio
import logging
import subprocess
import sys
import textwrap
import threading
import time
from datetime import timedelta
from email.message import EmailMessage
from pathlib import Path

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from po_dashboard.db.base import utcnow
from po_dashboard.db.mail import Mailbox, ScanState, SourceDocument, SourceMessage
from po_dashboard.intake.store import FileStore
from po_dashboard.scheduler import scanner as scanner_module
from po_dashboard.scheduler.loop import run_forever
from po_dashboard.scheduler.scanner import Scanner, last_scan
from test_gate import env, new_client, sign_in  # noqa: F401


def eml(n: int, body: str = "Shipped today") -> bytes:
    msg = EmailMessage()
    msg["From"], msg["To"] = "V <v@vendor.test>", "p@example-buyer.test"
    msg["Subject"], msg["Message-ID"] = f"Shipment {n}", f"<m{n}@vendor.test>"
    msg["Date"] = "Mon, 02 Nov 2026 09:30:00 +0000"
    msg.set_content(f"{body} {n}")
    return msg.as_bytes()


@pytest.fixture
def mail_dir(tmp_path: Path) -> Path:
    (tmp_path / "mail" / "Inbox").mkdir(parents=True)
    (tmp_path / "mail" / "Sent Items").mkdir()
    return tmp_path / "mail"


def drop(mail_dir: Path, count: int, folder: str = "Inbox", start: int = 1) -> None:
    for n in range(start, start + count):
        (mail_dir / folder / f"{n:03}.eml").write_bytes(eml(n + (1000 if folder == "Sent Items" else 0)))


@pytest.fixture
def scan(env, mail_dir: Path, tmp_path: Path):  # noqa: ANN001, ANN201, F811
    engine = env[1]
    with Session(engine) as db:
        db.add(Mailbox(address="purchasing@example-buyer.test", provider="folder", location=str(mail_dir)))
        db.commit()
    return Scanner(engine, FileStore(tmp_path / "originals"))


def count(engine, table) -> int:  # noqa: ANN001
    with Session(engine) as db:
        return db.scalar(select(func.count()).select_from(table)) or 0


def test_a_scan_stores_and_reads_new_mail_and_the_next_scan_finds_nothing(scan: Scanner, mail_dir: Path) -> None:
    drop(mail_dir, 3)
    first = scan.scan()
    assert first.ran and first.stored == 3 and first.read == 3 and not first.errors and not first.more
    assert count(scan.engine, SourceMessage) == 3
    with Session(scan.engine) as db:
        assert {d.state for d in db.scalars(select(SourceDocument))} == {"read"}
        assert last_scan(db) is not None
    again = scan.scan()
    assert again.stored == 0 and again.duplicates == 0 and count(scan.engine, SourceMessage) == 3
    drop(mail_dir, 1, start=4)
    assert scan.scan().stored == 1 and count(scan.engine, SourceMessage) == 4


def test_both_folders_are_scanned_with_their_own_position(scan: Scanner, mail_dir: Path) -> None:
    drop(mail_dir, 2)
    drop(mail_dir, 2, folder="Sent Items")
    assert scan.scan().stored == 4
    with Session(scan.engine) as db:
        assert {s.folder for s in db.scalars(select(ScanState))} == {"Inbox", "Sent Items"}


# ---- crashes


def test_a_failure_part_way_through_a_batch_stores_nothing_of_it_and_the_retry_loses_and_repeats_nothing(
    scan: Scanner, mail_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    drop(mail_dir, 5)
    real = scanner_module.store_message
    calls = {"n": 0}

    def flaky(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202
        calls["n"] += 1
        if calls["n"] == 3:
            raise OSError("disk went away")
        return real(*args, **kwargs)

    monkeypatch.setattr(scanner_module, "store_message", flaky)
    report = scan.scan()
    assert report.errors and count(scan.engine, SourceMessage) == 0  # the batch is whole or nothing
    with Session(scan.engine) as db:
        state = db.scalars(select(ScanState).where(ScanState.folder == "Inbox")).one()
        assert state.position is None and state.failures == 1 and "OSError" in (state.last_error or "")
    monkeypatch.setattr(scanner_module, "store_message", real)
    assert scan.scan().stored == 5 and count(scan.engine, SourceMessage) == 5
    assert scan.scan().stored == 0


def test_a_process_killed_mid_scan_loses_nothing_and_repeats_nothing(tmp_path: Path, mail_dir: Path) -> None:
    drop(mail_dir, 6)
    data = tmp_path / "killed"
    script = textwrap.dedent(f"""
        import os, sys
        from pathlib import Path
        from sqlalchemy.orm import Session
        from po_dashboard.config import Settings
        from po_dashboard.db.migrate import upgrade
        from po_dashboard.db.mail import Mailbox
        from po_dashboard.intake.store import FileStore
        from po_dashboard.scheduler import scanner as s
        settings = Settings(data_dir=Path({str(data)!r}))
        engine = upgrade(settings)
        with Session(engine) as db:
            if not db.query(Mailbox).count():
                db.add(Mailbox(address="p@example-buyer.test", provider="folder", location={str(mail_dir)!r}))
                db.commit()
        if sys.argv[1] == "kill":
            real, n = s.store_message, [0]
            def die(*a, **k):
                n[0] += 1
                if n[0] == 4:
                    os._exit(9)          # no cleanup, no commit: as if the power went
                return real(*a, **k)
            s.store_message = die
        r = s.Scanner(engine, FileStore(Path({str(data)!r}) / "originals"), batch=3).scan()
        print(r.stored)
    """)
    run = lambda mode: subprocess.run(  # noqa: E731
        [sys.executable, "-I", "-c", script, mode], capture_output=True, text=True, timeout=120, check=False,
        env={"PATH": "/usr/bin:/bin", "PYTHONPATH": ":".join(sys.path)},
    )  # fmt: skip
    assert run("kill").returncode == 9
    done = run("again")
    assert done.returncode == 0, done.stderr[-500:]
    from po_dashboard.config import Settings
    from po_dashboard.db.migrate import upgrade

    engine = upgrade(Settings(data_dir=data))
    try:
        with Session(engine) as db:
            assert db.scalar(select(func.count()).select_from(SourceMessage)) == 6  # all of it, once
            assert db.scalar(select(func.count(func.distinct(SourceMessage.content_hash)))) == 6
    finally:
        engine.dispose()


# ---- one at a time, a cap, errors


def test_a_tick_during_a_running_scan_is_skipped_not_queued(
    scan: Scanner, mail_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    drop(mail_dir, 2)
    inside, release = threading.Event(), threading.Event()
    real = scanner_module.store_message

    def slow(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202
        inside.set()
        release.wait(10)
        return real(*args, **kwargs)

    monkeypatch.setattr(scanner_module, "store_message", slow)
    first: list = []
    worker = threading.Thread(target=lambda: first.append(scan.scan()))
    worker.start()
    assert inside.wait(10)
    assert scan.scan().ran is False  # skipped at once
    release.set()
    worker.join(30)
    assert first[0].ran and first[0].stored == 2 and count(scan.engine, SourceMessage) == 2


def test_a_burst_is_spread_over_several_ticks(scan: Scanner, mail_dir: Path) -> None:
    drop(mail_dir, 7)
    scan.max_messages, scan.batch = 3, 2
    ticks, seen = 0, 0
    while True:
        report = scan.scan()
        ticks += 1
        seen += report.stored
        assert report.stored <= 3
        if not report.more:
            break
        assert ticks < 10
    assert ticks >= 3 and seen == 7 and count(scan.engine, SourceMessage) == 7


def test_documents_read_per_tick_are_capped_too(scan: Scanner, mail_dir: Path) -> None:
    drop(mail_dir, 4)
    scan.max_reads = 3
    first = scan.scan()
    assert first.read == 3 and first.more
    assert scan.scan().read == 1


def test_a_mailbox_that_cannot_be_read_is_recorded_and_the_others_carry_on(
    scan: Scanner, mail_dir: Path, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    other = tmp_path / "other"
    (other / "Inbox").mkdir(parents=True)
    (other / "Inbox" / "1.eml").write_bytes(eml(99, "TOPSECRET-BODY"))
    with Session(scan.engine) as db:
        db.add(Mailbox(address="second@example-buyer.test", provider="folder", location=str(other)))
        db.commit()
    real = scan.open_mailbox

    def open_one(mailbox: Mailbox):  # noqa: ANN202
        if mailbox.address.startswith("purchasing"):
            raise ConnectionError("TOPSECRET-BODY in the message")
        return real(mailbox)

    scan.open_mailbox = open_one
    report = scan.scan()
    assert report.stored == 1 and report.errors == ["could not read the mailbox (ConnectionError)"]
    with Session(scan.engine) as db:
        failed = db.scalars(select(ScanState).where(ScanState.last_error.is_not(None))).one()
        assert failed.failures == 1 and "TOPSECRET" not in (failed.last_error or "")
    assert "TOPSECRET" not in caplog.text and "TOPSECRET" not in str(report)


def test_one_folder_failing_does_not_stop_the_other(scan: Scanner, mail_dir: Path) -> None:
    drop(mail_dir, 2, folder="Sent Items")
    real = scan.open_mailbox

    class Flaky:
        def __init__(self, inner) -> None:  # noqa: ANN001
            self.inner = inner

        def folders(self):  # noqa: ANN201
            return self.inner.folders()

        def health(self):  # noqa: ANN201
            return self.inner.health()

        def fetch_new(self, folder, position, limit=50):  # noqa: ANN001, ANN201
            if folder == "Inbox":
                raise TimeoutError("slow")
            return self.inner.fetch_new(folder, position, limit)

    scan.open_mailbox = lambda mailbox: Flaky(real(mailbox))
    report = scan.scan()
    assert report.stored == 2 and len(report.errors) == 1


def test_a_success_after_failures_clears_the_error(scan: Scanner, mail_dir: Path) -> None:
    drop(mail_dir, 1)
    real = scan.open_mailbox
    scan.open_mailbox = lambda m: (_ for _ in ()).throw(ConnectionError("x"))
    scan.scan()
    scan.open_mailbox = real
    scan.scan()
    with Session(scan.engine) as db:
        states = list(db.scalars(select(ScanState).where(ScanState.folder == "Inbox")))
        assert all(s.failures == 0 and s.last_error is None for s in states)


# ---- the database lock


def test_a_scan_waits_for_the_web_apps_write_lock_instead_of_failing(scan: Scanner, mail_dir: Path) -> None:
    drop(mail_dir, 2)
    holding, done = threading.Event(), threading.Event()

    def web_write() -> None:
        with scan.engine.connect() as conn:
            conn.exec_driver_sql("BEGIN IMMEDIATE")
            holding.set()
            time.sleep(0.8)
            conn.commit()
        done.set()

    worker = threading.Thread(target=web_write)
    worker.start()
    assert holding.wait(10)
    report = scan.scan()
    worker.join(10)
    assert done.is_set() and report.stored == 2 and not report.errors


def test_a_scan_does_not_hold_the_write_lock_while_it_reads_a_document(
    scan: Scanner, mail_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    drop(mail_dir, 1)
    from po_dashboard.intake import reading

    real = reading.read_stored
    checked: list[bool] = []

    def slow(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202
        with scan.engine.begin() as conn:  # a web request writing while the reader works: it must not be blocked
            conn.execute(text("PRAGMA busy_timeout=100"))
            conn.execute(text("UPDATE mailbox SET state = state"))
        checked.append(True)
        return real(*args, **kwargs)

    monkeypatch.setattr(reading, "read_stored", slow)
    assert scan.scan().read == 1 and checked


# ---- the clock and the health strip


def test_the_loop_scans_again_and_again_and_survives_a_crashing_scan() -> None:
    calls: list[int] = []

    class Fake:
        def scan(self):  # noqa: ANN201
            calls.append(1)
            if len(calls) == 2:
                raise RuntimeError("boom")
            return scanner_module.ScanReport()

    async def go() -> None:
        task = asyncio.create_task(run_forever(Fake(), interval=0.01))  # type: ignore[arg-type]
        await asyncio.sleep(0.3)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(go())
    assert len(calls) >= 3


def test_the_home_page_says_when_the_last_scan_was(scan: Scanner, mail_dir: Path, env) -> None:  # noqa: ANN001, F811
    client = new_client(env[0])
    sign_in(client, "buyer@example.com")
    assert "no scan yet" in client.get("/").text
    drop(mail_dir, 1)
    scan.scan()
    assert "just now" in client.get("/").text
    with Session(scan.engine) as db:
        for state in db.scalars(select(ScanState)):
            state.last_success = utcnow() - timedelta(minutes=12)
        db.commit()
    assert "12 min ago" in client.get("/").text
