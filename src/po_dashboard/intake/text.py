"""Turn a stored document into plain text for the model (PD-3.3, DEC-51, ARCHITECTURE §6 step 3).

Every reader works on bytes in memory and returns text. Nothing is fetched, nothing is run: HTML links are dropped and
images are never loaded, a spreadsheet gives its stored values (formulas are never calculated, macros never run), and
the one program called, Tesseract, gets the image on its standard input and has no network use. Hostile files are
expected (PD-3.6): zip-based files are size-checked before they are opened, Pillow opens five formats only, pypdf never
runs another program, and a file over a limit is refused with a fixed reason. Reasons never contain document text
(DEC-13). The app calls these readers through `worker.py`, which runs them in a separate, sandboxed process (DEC-53).
"""

from __future__ import annotations

import csv
import email
import email.header
import io
import re
import shutil
import subprocess
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from email.message import Message
from email.policy import Compat32
from html.parser import HTMLParser
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from PIL.Image import Image as PILImage
    from pypdf import PdfReader

MAX_PDF_PAGES = 50  # DEC-51
MAX_TEXT_CHARS = 200_000  # more than any purchasing document needs; the rest is cut and said so
MAX_SHEETS, MAX_ROWS, MAX_COLUMNS = 20, 5000, 60
MAX_ZIP_ENTRIES, MAX_ZIP_BYTES, MAX_ZIP_RATIO = 2000, 150 * 1024 * 1024, 200
MAX_IMAGE_PIXELS = 40_000_000
MAX_FRAMES = 10  # pages of a multi-page TIFF
MAX_HEADER_INPUT = 10_000  # characters of one header decoded; the rest is never parsed (PD-3.6)
# Pillow knows dozens of formats, and EPS among them would run Ghostscript if it were ever installed. Only these are
# opened (PD-3.6). Page images inside a PDF come out of pypdf, which has already decoded JPEG 2000 itself.
IMAGE_FORMATS = ("PNG", "JPEG", "GIF", "TIFF", "BMP")
PDF_IMAGE_FORMATS = (*IMAGE_FORMATS, "JPEG2000")
OCR_SECONDS = 120
THIN_PDF = 25  # fewer characters than this per page and the PDF is a scan
QUOTE_NOTE = "[EARLIER MESSAGE: everything below was quoted from before and is not the current message]"
CUT_NOTE = f"[text cut after {MAX_TEXT_CHARS} characters]"

_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_BLANKS = re.compile(r"\n{3,}")
_QUOTE_STARTS = (
    re.compile(r"^-{2,}\s*(original|forwarded) message\s*-{2,}\s*$", re.I),
    re.compile(r"^_{20,}\s*$"),
    re.compile(r"^on .{5,200} wrote:\s*$", re.I),
    re.compile(r"^>"),
)
_SENT = re.compile(r"^sent:", re.I)
_FROM = re.compile(r"^from:", re.I)


class NotReadable(Exception):
    """The file is stored but will not be read, for a reason written for the person (fixed words, no file text)."""


class ReaderUnavailable(Exception):
    """A program the reader needs is missing (Tesseract). The document waits; it is not marked failed."""


@dataclass(frozen=True)
class Text:
    text: str
    pages: int | None = None


# ---------------------------------------------------------------- helpers


def _tidy(raw: str) -> str:
    """Control characters out, trailing blanks off each line, runs of blank lines squeezed, cut to size."""
    text = _CONTROL.sub(" ", raw.replace("\r\n", "\n").replace("\r", "\n"))
    text = _BLANKS.sub("\n\n", "\n".join(line.rstrip() for line in text.split("\n"))).strip()
    return (text[:MAX_TEXT_CHARS] + "\n" + CUT_NOTE if len(text) > MAX_TEXT_CHARS else text) + "\n"


def _decode(data: bytes) -> str:
    for encoding in ("utf-8-sig", "cp1252"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


class _RawHeaders(Compat32):
    """The plain email parser, giving each header as it came. Plain `compat32` turns a header with raw 8-bit bytes
    into a Header object that has already replaced "ü" with "?"; this keeps the bytes for `unescape` to decode."""

    def header_fetch_parse(self, name: str, value: str) -> str:
        return value


RAW_HEADERS = _RawHeaders()


def unescape(value: str) -> str:
    """Text the email parser kept as escaped raw bytes, as real text: UTF-8 if it is, else Windows-1252. Never returns
    a lone surrogate, which the database would refuse (and a refused batch would be tried again on every scan)."""
    try:
        data = value.encode("utf-8", "surrogateescape")
    except UnicodeEncodeError:
        return value.encode("utf-8", "replace").decode("utf-8")
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("cp1252", "replace")


def decode_words(value: str) -> str:
    """=?utf-8?q?...?= encoded words decoded. If the whole header will not decode (raw accented text next to an
    encoded word, say), each word is decoded on its own; a word that will not decode is kept as it came."""
    if "=?" not in value:
        return value
    try:
        return str(email.header.make_header(email.header.decode_header(value)))
    except Exception:  # a hostile or sloppy header: word by word
        return _ENCODED_WORD.sub(_one_word, _BETWEEN_WORDS.sub(r"\1", value))


_ENCODED_WORD = re.compile(r"=\?[^?\s]+\?[bBqQ]\?[^?\s]*\?=")
_BETWEEN_WORDS = re.compile(r"(\?=)\s+(?==\?)")  # the space between two encoded words is not text (RFC 2047)


def _one_word(match: re.Match[str]) -> str:
    try:
        ((data, charset),) = email.header.decode_header(match.group(0))
        return data.decode(charset or "ascii", "replace") if isinstance(data, bytes) else str(data)
    except Exception:
        return match.group(0)


def header_value(msg: Message, name: str) -> str | None:
    """One header as text, from the raw header and never the structured parser: `policy.default` needs memory that
    grows with the square of the number of encoded words, and 20,000 of them in a 0.3 MB Subject took over 2 GB
    (PD-3.6). Only the first MAX_HEADER_INPUT characters are decoded."""
    wanted = name.lower()
    for key, value in msg.raw_items():
        if key.lower() == wanted:
            return decode_words(unescape(str(value)[:MAX_HEADER_INPUT]))
    return None


def _check_zip(data: bytes) -> None:
    """Refuse a zip-based file (xlsx, docx) that would expand into something huge, before anything opens it."""
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            entries = archive.infolist()
    except zipfile.BadZipFile:
        raise NotReadable("the file is damaged or is not what its name says") from None
    if len(entries) > MAX_ZIP_ENTRIES:
        raise NotReadable("the file holds too many parts to be a normal document")
    total = sum(entry.file_size for entry in entries)
    if total > MAX_ZIP_BYTES or total > MAX_ZIP_RATIO * max(len(data), 1):
        raise NotReadable("the file would expand to an unsafe size")


# ---------------------------------------------------------------- email and HTML


class _HTMLText(HTMLParser):
    """HTML to text. Links are dropped (only their words stay), nothing is fetched, scripts and styles are skipped."""

    SKIP = {"script", "style", "head", "title", "template", "noscript"}
    BLOCKS = {
        "p",
        "div",
        "br",
        "li",
        "tr",
        "table",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "ul",
        "ol",
        "blockquote",
        "pre",
    }

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.hidden = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in self.SKIP:
            self.hidden += 1
        elif tag in self.BLOCKS:
            self.parts.append("\n")
        elif tag in ("td", "th"):
            self.parts.append("\t")

    def handle_endtag(self, tag: str) -> None:
        if tag in self.SKIP:
            self.hidden = max(0, self.hidden - 1)
        elif tag in self.BLOCKS:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self.hidden:
            self.parts.append(data)


def html_to_text(markup: str) -> str:
    parser = _HTMLText()
    parser.feed(markup)
    parser.close()
    lines = [
        "\t".join(" ".join(cell.split()) for cell in line.split("\t")).strip("\t")
        for line in "".join(parser.parts).split("\n")
    ]
    return "\n".join(lines)


def mark_quoted(body: str) -> str:
    """Put one note before the first sign of an earlier message (Original Message line, 'On ... wrote:', an Outlook
    From/Sent block, or a > line). Nothing is removed: the note tells the model which part is the current message."""
    lines = body.split("\n")
    for n, line in enumerate(lines):
        stripped = line.strip()
        outlook = bool(_FROM.match(stripped)) and any(_SENT.match(x.strip()) for x in lines[n + 1 : n + 4]) and n > 0
        if outlook or any(pattern.match(stripped) for pattern in _QUOTE_STARTS):
            return "\n".join([*lines[:n], QUOTE_NOTE, *lines[n:]])
    return body


def _body_part(msg: Message) -> str:
    html = ""
    for part in msg.walk():
        if part.is_multipart() or part.get_content_disposition() == "attachment" or part.get_filename():
            continue
        kind = part.get_content_type()
        if kind not in ("text/plain", "text/html"):
            continue
        payload = part.get_payload(decode=True)
        if not isinstance(payload, bytes):
            continue
        charset = part.get_content_charset() or "utf-8"
        try:
            text = payload.decode(charset, errors="replace")
        except LookupError:
            text = payload.decode("utf-8", errors="replace")
        if kind == "text/plain":
            return text
        html = html or text
    return html_to_text(html) if html else ""


def message_text(raw: bytes) -> Text:
    """A message as the model sees it: the From, To, Subject, Date and Cc lines, then the body (the plain part, or the
    HTML part turned into text), with any quoted earlier message marked. Attachments are separate documents."""
    msg = email.message_from_bytes(raw, policy=RAW_HEADERS)
    heads = []
    for name in ("From", "To", "Subject", "Date", "Cc"):
        value = header_value(msg, name)
        if value:
            heads.append(f"{name}: {' '.join(value.split())}")
    return Text(_tidy("\n".join(heads) + "\n\n" + mark_quoted(_body_part(msg).replace("\r\n", "\n"))))


# ---------------------------------------------------------------- images and scans


def _ocr(png: bytes) -> str:
    program = shutil.which("tesseract")
    if program is None:
        raise ReaderUnavailable("Tesseract is not installed")
    try:
        done = subprocess.run(  # noqa: S603 - a list, no shell; the image goes in on stdin, never as a file name
            [program, "stdin", "stdout", "-l", "eng", "--psm", "4", "-c", "preserve_interword_spaces=1"],
            input=png, capture_output=True, timeout=OCR_SECONDS, check=False,
            env={"PATH": "/usr/bin:/bin:/opt/homebrew/bin:/usr/local/bin", "OMP_THREAD_LIMIT": "1"},
        )  # fmt: skip
    except subprocess.TimeoutExpired:
        raise NotReadable("reading the scan took too long") from None
    if done.returncode != 0:
        raise NotReadable("the scan could not be read")
    return done.stdout.decode("utf-8", errors="replace")


def _png_of(image: PILImage) -> bytes:
    from PIL import Image  # imported here: only scans need it

    if image.mode not in ("L", "RGB"):
        flat = Image.new("RGB", image.size, "white")
        flat.paste(image.convert("RGBA"), mask=image.convert("RGBA").getchannel("A"))
        image = flat
    out = io.BytesIO()
    image.save(out, format="PNG")
    return out.getvalue()


def image_text(data: bytes) -> Text:
    from PIL import Image, UnidentifiedImageError

    # Pillow's own bomb limit is left as it is (a process-wide setting); the size is checked here before decoding.
    try:
        with Image.open(io.BytesIO(data), formats=IMAGE_FORMATS) as image:
            frames = min(getattr(image, "n_frames", 1), MAX_FRAMES)
            pages = []
            for n in range(frames):
                image.seek(n)
                if image.width * image.height > MAX_IMAGE_PIXELS:
                    raise NotReadable("the image is too large to read")
                pages.append(_ocr(_png_of(image.copy())))
    except (Image.DecompressionBombError, Image.DecompressionBombWarning):
        raise NotReadable("the image is too large to read") from None
    except UnidentifiedImageError:
        raise NotReadable("the file is damaged or is not what its name says") from None
    return Text(_tidy("\n\n".join(pages)), pages=frames)


# ---------------------------------------------------------------- PDF


def pdf_text(data: bytes) -> Text:
    from pypdf import PdfReader, apply_configuration
    from pypdf.errors import DependencyError, LimitReachedError, PdfReadError

    # pypdf would run the jbig2dec program on a JBIG2 scan if it were ever installed: never (PD-3.6). Its own limits
    # (75 MB per stream, a budget for parsing) stop compression bombs; they are reported as such.
    with apply_configuration(jbig2dec_binary=None):
        try:
            return _pdf_text(PdfReader(io.BytesIO(data), strict=False))
        except LimitReachedError:
            raise NotReadable("the file would expand to an unsafe size") from None
        except DependencyError:
            raise NotReadable("the scan uses a picture format the app does not read") from None
        except PdfReadError:  # not a PDF at all (an .exe named invoice.pdf), or a broken one
            raise NotReadable("the file is damaged or is not what its name says") from None


def _pdf_text(reader: PdfReader) -> Text:
    if reader.is_encrypted and not reader.decrypt(""):
        raise NotReadable("the PDF is password protected")
    count = len(reader.pages)
    if count > MAX_PDF_PAGES:
        raise NotReadable(f"more than {MAX_PDF_PAGES} pages")
    texts = [(page.extract_text() or "").strip() for page in reader.pages]
    if sum(len(t) for t in texts) < THIN_PDF * max(count, 1):  # no text layer: a scan, so read the page images
        texts = []
        for page in reader.pages:
            texts.append("\n".join(_ocr(_png_of(_open(img.data))) for img in list(page.images)[:3]))
    return Text(_tidy("\n\n".join(texts)), pages=count)


def _open(data: bytes) -> PILImage:
    from PIL import Image

    image = Image.open(io.BytesIO(data), formats=PDF_IMAGE_FORMATS)
    if image.width * image.height > MAX_IMAGE_PIXELS:
        raise NotReadable("the image is too large to read")
    return image


# ---------------------------------------------------------------- spreadsheets and Word


def _cell(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, datetime):
        return value.date().isoformat() if value.time() == datetime.min.time() else value.isoformat(sep=" ")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, float | Decimal):
        shown = format(Decimal(str(value)).normalize(), "f")
        return shown
    return " ".join(str(value).split())


def spreadsheet_text(data: bytes) -> Text:
    from openpyxl import load_workbook

    _check_zip(data)
    book = load_workbook(io.BytesIO(data), read_only=True, data_only=True)  # stored values; nothing is calculated
    out: list[str] = []
    try:
        for sheet in book.worksheets[:MAX_SHEETS]:
            if sheet.sheet_state != "visible":
                continue  # what a person opening the file would not see
            out.append(f"Sheet: {sheet.title}")
            for row in sheet.iter_rows(max_row=MAX_ROWS, max_col=MAX_COLUMNS, values_only=True):
                cells = [_cell(v) for v in row]
                while cells and not cells[-1]:
                    cells.pop()
                out.append("\t".join(cells))
            out.append("")
    finally:
        book.close()
    return Text(_tidy("\n".join(out)))


def csv_text(data: bytes, delimiter: str) -> Text:
    csv.field_size_limit(1_000_000)
    rows = []
    for n, row in enumerate(csv.reader(io.StringIO(_decode(data)), delimiter=delimiter)):
        if n >= MAX_ROWS:
            break
        rows.append("\t".join(" ".join(cell.split()) for cell in row[:MAX_COLUMNS]).rstrip("\t"))
    return Text(_tidy("\n".join(rows)))


def word_text(data: bytes) -> Text:
    from docx import Document
    from docx.table import Table

    _check_zip(data)
    document = Document(io.BytesIO(data))
    out: list[str] = []
    for block in document.iter_inner_content():
        if isinstance(block, Table):
            for row in block.rows:
                seen: list[object] = []
                cells = []
                for cell in row.cells:
                    if any(cell._tc is s for s in seen):  # a merged cell is one cell, said once
                        continue
                    seen.append(cell._tc)
                    cells.append(" ".join(cell.text.split()))
                out.append("\t".join(cells))
        else:
            out.append(block.text)
    return Text(_tidy("\n".join(out)))


# ---------------------------------------------------------------- the one entry point


def extract_text(kind: str, filename: str, data: bytes) -> Text:
    """The text of one stored document. `kind` is what intake decided (body, pdf, image, spreadsheet, word, text,
    email). Raises NotReadable (stored, never read, with a reason) or ReaderUnavailable (wait and try again)."""
    readers: dict[str, Callable[[], Text]] = {
        "body": lambda: message_text(data),
        "email": lambda: message_text(data),
        "pdf": lambda: pdf_text(data),
        "image": lambda: image_text(data),
        "word": lambda: word_text(data),
        "text": lambda: Text(_tidy(_decode(data))),
    }
    extension = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if kind == "spreadsheet":
        if extension in ("csv", "tsv"):
            return csv_text(data, "\t" if extension == "tsv" else ",")
        return spreadsheet_text(data)
    if kind not in readers:
        raise NotReadable("a kind of file the app does not read")
    return readers[kind]()
