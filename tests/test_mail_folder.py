"""The folder provider (PD-3.1): the shared contract, plus what is particular to a folder of .eml files."""

from __future__ import annotations

import hashlib
import os
import stat
from pathlib import Path

from mail_contract import MailSourceContract, message
from po_dashboard.db.mail import Mailbox
from po_dashboard.mail import open_source
from po_dashboard.mail.folder import FolderSource
from po_dashboard.mail.source import FOLDERS, INBOX, MailSource


class TestFolderSource(MailSourceContract):
    def make(self, tmp_path: Path) -> MailSource:
        for folder in FOLDERS:
            (tmp_path / folder).mkdir()
        return FolderSource(tmp_path)

    def add(self, source: MailSource, folder: str, raw: bytes) -> None:
        assert isinstance(source, FolderSource)
        name = hashlib.sha256(raw).hexdigest()[:12]
        (source.root / folder / f"{name}.eml").write_bytes(raw)

    def break_it(self, source: MailSource) -> None:
        assert isinstance(source, FolderSource)
        for folder in FOLDERS:
            for path in (source.root / folder).iterdir():
                path.unlink()
            (source.root / folder).rmdir()

    def snapshot(self, source: MailSource) -> object:
        assert isinstance(source, FolderSource)
        return sorted(
            (str(p.relative_to(source.root)), p.stat().st_mtime_ns, p.read_bytes() if p.is_file() else b"")
            for p in source.root.rglob("*")
        )

    # --- particular to a folder
    def test_only_eml_files_are_read(self, tmp_path: Path) -> None:
        source = self.make(tmp_path)
        (tmp_path / INBOX / "a.eml").write_bytes(message(1))
        (tmp_path / INBOX / "B.EML").write_bytes(message(2))
        (tmp_path / INBOX / "notes.txt").write_bytes(message(3))
        (tmp_path / INBOX / ".hidden.eml").write_bytes(message(4))
        (tmp_path / INBOX / "nested").mkdir()
        (tmp_path / INBOX / "nested" / "c.eml").write_bytes(message(5))
        got = sorted(m.raw for m in source.fetch_new(INBOX, None).messages)
        assert got == sorted([message(1), message(2)])

    def test_a_symbolic_link_is_not_followed(self, tmp_path: Path) -> None:
        source = self.make(tmp_path)
        outside = tmp_path / "outside.eml"
        outside.write_bytes(message(9))
        (tmp_path / INBOX / "link.eml").symlink_to(outside)
        assert source.fetch_new(INBOX, None).messages == []

    def test_a_copy_under_another_name_is_not_read_twice(self, tmp_path: Path) -> None:
        source = self.make(tmp_path)
        (tmp_path / INBOX / "one.eml").write_bytes(message(1))
        (tmp_path / INBOX / "two.eml").write_bytes(message(1))
        assert len(source.fetch_new(INBOX, None).messages) == 1

    def test_a_changed_file_is_a_new_message(self, tmp_path: Path) -> None:
        source = self.make(tmp_path)
        path = tmp_path / INBOX / "one.eml"
        path.write_bytes(message(1))
        position = source.fetch_new(INBOX, None).position
        path.write_bytes(message(1, "REVISED"))
        assert len(source.fetch_new(INBOX, position).messages) == 1

    def test_a_folder_can_be_read_without_permission_to_write(self, tmp_path: Path) -> None:
        source = self.make(tmp_path)
        (tmp_path / INBOX / "a.eml").write_bytes(message(1))
        for folder in FOLDERS:
            os.chmod(tmp_path / folder, stat.S_IRUSR | stat.S_IXUSR)
        try:
            assert len(source.fetch_new(INBOX, None).messages) == 1
            assert source.health().ok
        finally:
            for folder in FOLDERS:
                os.chmod(tmp_path / folder, stat.S_IRWXU)

    def test_a_missing_folder_is_not_an_error(self, tmp_path: Path) -> None:
        source = FolderSource(tmp_path / "nowhere")
        assert source.fetch_new(INBOX, None).messages == []
        assert not source.health().ok

    def test_a_huge_message_is_skipped_with_a_reason_and_not_read_again(self, tmp_path: Path, monkeypatch) -> None:  # noqa: ANN001
        from po_dashboard.mail import folder

        source = self.make(tmp_path)
        monkeypatch.setattr(folder, "MAX_MESSAGE_BYTES", 100)
        (tmp_path / INBOX / "big.eml").write_bytes(b"x" * 500)
        first = source.fetch_new(INBOX, None)
        assert first.messages == [] and len(first.skipped) == 1 and "limit" in first.skipped[0].reason
        assert source.fetch_new(INBOX, first.position).skipped == []

    def test_the_golden_emails_are_read(self, tmp_path: Path) -> None:
        source = self.make(tmp_path)
        golden = Path(__file__).parent / "data" / "golden"
        emails = sorted(golden.glob("*/document.eml"))
        assert len(emails) > 30
        for path in emails:
            (tmp_path / INBOX / f"{path.parent.name}.eml").write_bytes(path.read_bytes())
        result = source.fetch_new(INBOX, None, limit=500)
        unique = {path.read_bytes() for path in emails}  # a duplicate document is one message
        assert len(result.messages) == len(unique)


def test_a_mailbox_row_opens_its_folder(tmp_path: Path) -> None:
    box = Mailbox(address="purchasing@example-buyer.test", provider="folder", location=str(tmp_path))
    assert isinstance(open_source(box), FolderSource)
