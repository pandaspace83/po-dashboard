"""What every mail source must do (PD-3.1, REQ-IN-06). A provider's test file subclasses MailSourceContract and
says how to make a source, add a message to it and break it; the same checks then run against every provider."""

from __future__ import annotations

from pathlib import Path

import pytest

from po_dashboard.mail.source import FOLDERS, INBOX, SENT, MailSource

# Names that would let a source change a mailbox. None may exist on any provider (DEC-07).
WRITE_WORDS = ("send", "move", "delete", "trash", "flag", "mark", "modify", "store", "write", "append", "copy",
               "expunge", "archive", "label", "rename", "remove", "update", "create", "save", "put", "post")


def message(n: int, subject: str = "Order acknowledgement") -> bytes:
    return (
        f"From: Vendor <v{n}@vendor.test>\nTo: purchasing@example-buyer.test\nSubject: {subject} {n}\n"
        f"Date: Mon, 02 Nov 2026 09:30:00 +0000\nMessage-ID: <{n}@vendor.test>\n\nBody {n} PO 45000{n}\n"
    ).encode()


class MailSourceContract:
    # --- supplied by the provider's test file
    def make(self, tmp_path: Path) -> MailSource:
        raise NotImplementedError

    def add(self, source: MailSource, folder: str, raw: bytes) -> None:
        raise NotImplementedError

    def break_it(self, source: MailSource) -> None:
        raise NotImplementedError

    def snapshot(self, source: MailSource) -> object:
        """Anything that changes if the source was written to."""
        raise NotImplementedError

    # --- the contract
    def test_it_scans_the_inbox_and_sent_items(self, tmp_path: Path) -> None:
        assert self.make(tmp_path).folders() == FOLDERS == (INBOX, SENT)

    def test_an_empty_folder_gives_nothing_and_a_position(self, tmp_path: Path) -> None:
        result = self.make(tmp_path).fetch_new(INBOX, None)
        assert result.messages == [] and isinstance(result.position, str) and not result.more

    def test_each_message_comes_once(self, tmp_path: Path) -> None:
        source = self.make(tmp_path)
        self.add(source, INBOX, message(1))
        first = source.fetch_new(INBOX, None)
        assert [m.raw for m in first.messages] == [message(1)]
        second = source.fetch_new(INBOX, first.position)
        assert second.messages == []

    def test_a_message_that_arrives_later_is_returned_once(self, tmp_path: Path) -> None:
        source = self.make(tmp_path)
        self.add(source, INBOX, message(1))
        position = source.fetch_new(INBOX, None).position
        self.add(source, INBOX, message(2))
        later = source.fetch_new(INBOX, position)
        assert [m.raw for m in later.messages] == [message(2)]
        assert source.fetch_new(INBOX, later.position).messages == []

    def test_the_bytes_are_handed_over_unchanged(self, tmp_path: Path) -> None:
        source = self.make(tmp_path)
        odd = message(3) + b"\r\n\xff\xfe not utf-8 \x00 bytes\r\n"
        self.add(source, INBOX, odd)
        assert source.fetch_new(INBOX, None).messages[0].raw == odd

    def test_a_batch_limit_loses_nothing_and_repeats_nothing(self, tmp_path: Path) -> None:
        source = self.make(tmp_path)
        for n in range(1, 6):
            self.add(source, INBOX, message(n))
        got: list[bytes] = []
        position: str | None = None
        for _ in range(10):
            result = source.fetch_new(INBOX, position, limit=2)
            assert len(result.messages) <= 2
            got += [m.raw for m in result.messages]
            position = result.position
            if not result.more:
                break
        assert sorted(got) == sorted(message(n) for n in range(1, 6))

    def test_the_two_folders_are_separate(self, tmp_path: Path) -> None:
        source = self.make(tmp_path)
        self.add(source, INBOX, message(1))
        self.add(source, SENT, message(2))
        assert [m.raw for m in source.fetch_new(INBOX, None).messages] == [message(1)]
        sent = source.fetch_new(SENT, None)
        assert [m.raw for m in sent.messages] == [message(2)] and sent.messages[0].folder == SENT

    def test_a_position_it_does_not_understand_means_start_again(self, tmp_path: Path) -> None:
        source = self.make(tmp_path)
        self.add(source, INBOX, message(1))
        for junk in ("", "not a position", "[1, 2]", "{}", "\x00"):
            assert [m.raw for m in source.fetch_new(INBOX, junk).messages] == [message(1)]

    def test_an_unknown_folder_is_refused(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError):
            self.make(tmp_path).fetch_new("Trash", None)

    def test_health_is_ok_and_then_says_what_is_wrong_without_raising(self, tmp_path: Path) -> None:
        source = self.make(tmp_path)
        assert source.health().ok
        self.break_it(source)
        health = source.health()
        assert not health.ok and health.detail

    def test_reading_changes_nothing(self, tmp_path: Path) -> None:
        source = self.make(tmp_path)
        self.add(source, INBOX, message(1))
        self.add(source, SENT, message(2))
        before = self.snapshot(source)
        for folder in FOLDERS:
            source.fetch_new(folder, None)
        source.health()
        assert self.snapshot(source) == before

    def test_it_has_no_way_to_change_a_mailbox(self, tmp_path: Path) -> None:
        source = self.make(tmp_path)
        public = [n for n in dir(source) if not n.startswith("_")]
        assert not any(any(word in name.lower() for word in WRITE_WORDS) for name in public), public
