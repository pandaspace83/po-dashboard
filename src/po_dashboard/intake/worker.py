"""Reading one document in a separate, locked-down process (PD-3.6, DEC-53).

Hostile attachments are expected. The readers (pypdf, Pillow, openpyxl, python-docx, Tesseract) have limits of their
own, but each is a large parser of bytes from strangers, and a fault in one must cost one document, not the app. So
every document is read by a fresh Python process that:

- gets the document on its standard input and answers on its standard output (JSON); it touches no database and no
  file store;
- is stopped after DOCUMENT_SECONDS together with any Tesseract it started (its own process group), and stops itself
  past MEMORY_BYTES (macOS does not enforce memory limits on a process, so a watchdog thread checks);
- runs in the macOS sandbox: no network, no file writes, no program but this Python and Tesseract, and no reading of
  the stored originals or the Keychain files.

A crash, hang or blow-up ends as `failed` or `not_read` with fixed words. The child's error output is thrown away,
never logged: a traceback could quote the document (DEC-13).
"""

from __future__ import annotations

import json
import logging
import os
import re
import resource
import shutil
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Sequence
from pathlib import Path

from po_dashboard.intake.text import MAX_TEXT_CHARS, NotReadable, ReaderUnavailable, Text, extract_text

DOCUMENT_SECONDS = 600.0  # a 50-page scan takes about 3 minutes of OCR; 150 noisy page images about 9
MEMORY_BYTES = 1536 * 1024 * 1024  # pypdf's own limits held its worst bomb to about 330 MB
MEMORY_EXIT = 86
SANDBOX = "/usr/bin/sandbox-exec"
PATH = "/usr/bin:/bin:/opt/homebrew/bin:/usr/local/bin"  # where the child looks for Tesseract
KEYCHAINS = Path.home() / "Library" / "Keychains"
KINDS = frozenset({"body", "email", "pdf", "image", "spreadsheet", "word", "text"})
_EXTENSION = re.compile(r"[a-z0-9]{1,8}")
# The longest proper answer: every character escaped as a JSON surrogate pair (12 bytes). Anything longer is refused.
_ANSWER_LIMIT = 12 * MAX_TEXT_CHARS + 100_000

log = logging.getLogger(__name__)
_warned = threading.Event()


class ReaderFailed(Exception):
    """The reading process crashed or gave no proper answer. Fixed words only."""


# ---------------------------------------------------------------- the app's side


def _quoted(path: str) -> str:
    return '"' + path.replace("\\", "\\\\").replace('"', '\\"') + '"'


def profile(programs: Sequence[str], hidden: Sequence[Path], python_home: str | None = None) -> str:
    """The sandbox rules. Everything not named here is allowed, so Python and the readers work unchanged.

    `python_home` is the Python installation's own folder (`sys.base_prefix`): its programs may run too, because the
    python.org build of Python restarts itself through `Python.app` inside that folder, and the sandbox refused it
    (DEF-011, seen on GitHub's Macs). No program outside it, except those named, can run."""
    allowed = sorted({p for program in programs for p in (program, os.path.realpath(program))})
    may_run = " ".join(f"(literal {_quoted(p)})" for p in allowed)
    if python_home:
        may_run += f" (subpath {_quoted(os.path.realpath(python_home))})"
    rules = [
        "(version 1)",
        "(allow default)",
        "(deny network*)",
        "(deny file-write*)",
        '(allow file-write* (literal "/dev/null"))',
        "(deny process-exec*)",
        f"(allow process-exec* {may_run})",
    ]
    rules += [f"(deny file-read* (subpath {_quoted(os.path.realpath(folder))}))" for folder in hidden]
    return "\n".join(rules)


def command(kind: str, extension: str, memory: int, hidden: Sequence[Path]) -> list[str]:
    inner = [sys.executable, "-I", "-B", "-m", "po_dashboard.intake.worker", kind, extension, str(memory)]
    if not Path(SANDBOX).exists():
        if not _warned.is_set():
            _warned.set()
            log.warning("The macOS sandbox (sandbox-exec) is missing: documents are read in a separate process only.")
        return inner
    tesseract = shutil.which("tesseract", path=PATH)
    programs = [sys.executable, *([tesseract] if tesseract else [])]
    return [SANDBOX, "-p", profile(programs, [*hidden, KEYCHAINS], sys.base_prefix), *inner]


def _stop(child: subprocess.Popen[bytes]) -> None:
    try:
        os.killpg(child.pid, signal.SIGKILL)  # the child and any Tesseract it started
    except (ProcessLookupError, PermissionError):
        child.kill()
    child.communicate()


def read_isolated(
    kind: str, filename: str, data: bytes, *, hidden: Sequence[Path] = (),
    seconds: float = DOCUMENT_SECONDS, memory: int = MEMORY_BYTES,
) -> Text:  # fmt: skip
    """The text of one document, read in a locked-down process. Raises what `extract_text` raises (NotReadable,
    ReaderUnavailable) and ReaderFailed when the process itself fails. `hidden` folders cannot be read by it."""
    if kind not in KINDS:
        raise NotReadable("a kind of file the app does not read")
    extension = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    extension = extension if _EXTENSION.fullmatch(extension) else "-"
    env = {"PATH": PATH, "LANG": "en_US.UTF-8", "OMP_THREAD_LIMIT": "1"}
    with subprocess.Popen(  # noqa: S603 - a fixed command; the document goes in on stdin, never as an argument
        command(kind, extension, memory, hidden), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL, env=env, cwd="/", start_new_session=True,
    ) as child:  # fmt: skip
        try:
            out, _ = child.communicate(data, timeout=seconds)
        except subprocess.TimeoutExpired:
            _stop(child)
            raise NotReadable("reading the file took too long") from None
    if child.returncode == MEMORY_EXIT:
        raise NotReadable("reading the file needed too much memory")
    if child.returncode != 0 or len(out) > _ANSWER_LIMIT:
        raise ReaderFailed("the reader stopped unexpectedly")
    return _answer(out)


def _answer(out: bytes) -> Text:
    try:
        answer = json.loads(out)
        state = answer["state"]
    except (ValueError, TypeError, KeyError):
        raise ReaderFailed("the reader gave an answer the app does not understand") from None
    if state == "read" and isinstance(answer.get("text"), str):
        pages = answer.get("pages")
        return Text(answer["text"], pages if isinstance(pages, int) and not isinstance(pages, bool) else None)
    if state == "waiting":
        raise ReaderUnavailable("a program the reader needs is missing")
    if state in ("not_read", "failed") and isinstance(answer.get("reason"), str):
        reason = answer["reason"][:200]
        raise NotReadable(reason) if state == "not_read" else ReaderFailed(reason)
    raise ReaderFailed("the reader gave an answer the app does not understand")


# ---------------------------------------------------------------- the reading process


def _watch_memory(limit: int) -> None:
    unit = 1 if sys.platform == "darwin" else 1024  # ru_maxrss is bytes on macOS, kilobytes on Linux

    def check() -> None:
        if resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * unit > limit:
            os._exit(MEMORY_EXIT)

    def watch() -> None:
        while True:
            check()
            time.sleep(0.05)

    check()  # once now, then every 50 ms
    threading.Thread(target=watch, daemon=True).start()


def main(argv: Sequence[str]) -> int:
    kind, extension, memory = argv[0], argv[1], int(argv[2])
    _watch_memory(memory)
    data = sys.stdin.buffer.read()
    name = "document" if extension == "-" else f"document.{extension}"
    answer: dict[str, object]
    try:
        found = extract_text(kind, name, data)
        answer = {"state": "read", "text": found.text, "pages": found.pages}
    except ReaderUnavailable:
        answer = {"state": "waiting"}
    except NotReadable as why:
        answer = {"state": "not_read", "reason": str(why)}
    except Exception as problem:  # a hostile or broken file: the kind of error only, never its contents
        answer = {"state": "failed", "reason": f"could not be opened ({type(problem).__name__})"}
    sys.stdout.buffer.write(json.dumps(answer).encode("ascii"))
    sys.stdout.buffer.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
