"""The file store (PD-3.2): every original message and attachment, kept unchanged under its SHA-256, in an owner-only
folder inside the data folder (ARCHITECTURE §11). A file's name is its hash, so the same bytes are stored once and
nothing a message says (a file name, say) ever reaches a path."""

from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path

HASH = re.compile(r"[0-9a-f]{64}")


class FileStore:
    def __init__(self, root: Path) -> None:
        self.root = root

    @staticmethod
    def digest(data: bytes) -> str:
        return hashlib.sha256(data).hexdigest()

    @staticmethod
    def relative(digest: str) -> str:
        if not HASH.fullmatch(digest):
            raise ValueError("not a SHA-256 digest")
        return f"{digest[:2]}/{digest}"

    def put(self, data: bytes) -> tuple[str, str]:
        """Keep `data`. Returns (digest, path inside the store). Storing the same bytes again changes nothing."""
        digest = self.digest(data)
        relative = self.relative(digest)
        target = self.root / relative
        if target.is_file():
            return digest, relative
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.root.chmod(0o700)
        target.parent.chmod(0o700)
        temporary = target.with_name(f".{digest}.{os.getpid()}.tmp")
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, target)  # all or nothing: a crash leaves no half-written original
        finally:
            temporary.unlink(missing_ok=True)
        return digest, relative

    def read(self, relative: str) -> bytes:
        """The bytes at a stored path. Refuses anything that is not a path this store made."""
        directory, _, name = relative.partition("/")
        if not HASH.fullmatch(name) or directory != name[:2]:
            raise ValueError("not a path in the file store")
        return (self.root / relative).read_bytes()
