"""Pre-commit guard (RISK-07, DEC-13): refuse mail, documents, data and keys outside tests/data/."""

import sys
from pathlib import PurePosixPath

BLOCKED_SUFFIXES = {
    # Mail
    ".eml", ".msg", ".mbox",
    # The document types the app reads (REQ-IN-03)
    ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".xlsm", ".csv", ".tif", ".tiff",
    # Databases
    ".db", ".db-wal", ".db-shm", ".sqlite", ".sqlite3", ".sqlite-wal", ".sqlite-shm",
    # Keys
    ".pem", ".key", ".p12", ".pfx",
}  # fmt: skip


def blocked(path: str) -> bool:
    p = PurePosixPath(path)
    if "real-docs" in p.parts:
        return True
    if p.parts[:2] == ("tests", "data"):
        return False
    is_env = p.name == ".env" or (p.name.startswith(".env.") and p.name != ".env.example")
    return p.suffix.lower() in BLOCKED_SUFFIXES or is_env


def main(files: list[str]) -> int:
    bad = [f for f in files if blocked(f)]
    for f in bad:
        print(f"refused: {f} (only synthetic files under tests/data/ may be committed)")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
