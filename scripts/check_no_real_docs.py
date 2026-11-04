"""Pre-commit guard (RISK-07, DEC-13): refuse mail, documents, databases and .env outside tests/data/."""

import sys
from pathlib import PurePosixPath

BLOCKED_SUFFIXES = {".eml", ".msg", ".pdf", ".docx", ".xlsx", ".db", ".sqlite", ".sqlite3", ".mbox"}


def blocked(path: str) -> bool:
    p = PurePosixPath(path)
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
