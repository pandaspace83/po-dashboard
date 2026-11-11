"""The folder provider (tests, demo): reads `.eml` files from a directory (ARCHITECTURE §7). `<root>/Inbox` and
`<root>/Sent Items` stand for the two folders. The position is the set of file hashes already handed over, so a file
that is renamed or copied is not read twice, and a changed file is read as a new message.

It only ever opens files for reading. Symbolic links, hidden files and anything that is not `.eml` are ignored."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from po_dashboard.mail.source import FOLDERS, MAX_MESSAGE_BYTES, FetchResult, Health, RawMessage, Skipped


class FolderSource:
    def __init__(self, root: Path) -> None:
        self.root = root

    def folders(self) -> tuple[str, ...]:
        return FOLDERS

    def _files(self, folder: str) -> list[Path]:
        if folder not in FOLDERS:
            raise ValueError(f"unknown folder {folder!r}")
        directory = self.root / folder
        try:
            entries = sorted(directory.iterdir(), key=lambda p: p.name)
        except OSError:
            return []
        return [p for p in entries if p.suffix.lower() == ".eml" and not p.name.startswith(".")
                and p.is_file() and not p.is_symlink()]

    @staticmethod
    def _seen(position: str | None) -> set[str]:
        if not position:
            return set()
        try:
            value = json.loads(position)
        except ValueError:
            return set()
        if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
            return set()
        return set(value)

    def fetch_new(self, folder: str, position: str | None, limit: int = 50) -> FetchResult:
        seen = self._seen(position)
        messages: list[RawMessage] = []
        skipped: list[Skipped] = []
        more = False
        for path in self._files(folder):
            try:
                size = path.stat().st_size
                if size > MAX_MESSAGE_BYTES:
                    digest = f"too-large:{path.name}"
                    if digest not in seen:
                        skipped.append(Skipped(digest, folder, "larger than the 50 MB limit"))
                        seen.add(digest)
                    continue
                raw = path.read_bytes()
            except OSError:
                continue  # unreadable right now: left out of the position, so the next scan tries again
            digest = hashlib.sha256(raw).hexdigest()
            if digest in seen:
                continue
            if len(messages) >= limit:
                more = True
                break
            messages.append(RawMessage(digest, folder, raw))
            seen.add(digest)
        return FetchResult(messages, json.dumps(sorted(seen)), skipped, more)

    def health(self) -> Health:
        if not self.root.is_dir():
            return Health(False, "The mail folder does not exist.")
        missing = [f for f in FOLDERS if not (self.root / f).is_dir()]
        if missing:
            return Health(False, f"The folder has no {' or '.join(missing)} folder.")
        return Health(True, "Readable.")
