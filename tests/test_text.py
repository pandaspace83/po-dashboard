"""The text step (PD-3.3, DEC-51): each kind of document becomes plain text, checked against the golden set."""

from __future__ import annotations

import difflib
import io
import shutil
import zipfile
from pathlib import Path

import pytest
from docx import Document
from openpyxl import Workbook
from PIL import Image
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas

from po_dashboard.intake import text as text_module
from po_dashboard.intake.text import (
    CUT_NOTE,
    MAX_PDF_PAGES,
    MAX_TEXT_CHARS,
    QUOTE_NOTE,
    NotReadable,
    ReaderUnavailable,
    extract_text,
    html_to_text,
    mark_quoted,
)

GOLDEN = Path(__file__).parent / "data" / "golden"
KIND = {".eml": "body", ".pdf": "pdf", ".png": "image", ".xlsx": "spreadsheet"}
MINIMUM = {".eml": 0.95, ".pdf": 0.99, ".xlsx": 0.99, ".png": 0.95}  # a scan is read by OCR, so it is close, not exact
needs_tesseract = pytest.mark.skipif(shutil.which("tesseract") is None, reason="Tesseract is not installed")


def lines(value: str) -> str:
    return "\n".join(" ".join(x.split()) for x in value.splitlines() if x.strip() and x.strip() != QUOTE_NOTE)


def golden_cases() -> list[Path]:
    return sorted(p for p in GOLDEN.iterdir() if p.is_dir())


@pytest.mark.parametrize("folder", golden_cases(), ids=lambda p: p.name)
def test_every_golden_document_gives_the_expected_text(folder: Path) -> None:
    document = next(folder.glob("document.*"))
    if document.suffix == ".png" and shutil.which("tesseract") is None:
        pytest.skip("Tesseract is not installed")
    got = extract_text(KIND[document.suffix], document.name, document.read_bytes()).text
    want = (folder / "text.txt").read_text(encoding="utf-8")
    ratio = difflib.SequenceMatcher(None, lines(got), lines(want), autojunk=False).ratio()
    assert ratio >= MINIMUM[document.suffix], f"{folder.name} read as {ratio:.3f} of the expected text"


# ---- email and HTML


def email_of(body: str, subtype: str = "plain", extra: str = "") -> bytes:
    return (
        f"From: A <a@vendor.test>\r\nTo: purchasing@buyer.test\r\n{extra}Subject: RE: PO 1\r\n"
        f"Content-Type: text/{subtype}; charset=utf-8\r\n\r\n{body}"
    ).encode()


def test_html_gives_its_words_without_links_scripts_or_styles() -> None:
    page = (
        "<html><head><title>x</title><style>p{color:red}</style></head><body><p>Order <b>4521</b></p>"
        '<script>alert("INJECTED")</script><a href="https://evil.test/track">Track it</a>'
        '<img src="https://evil.test/pixel.gif"><table><tr><td>1</td><td>BLT-M8</td></tr></table></body></html>'
    )
    got = html_to_text(page)
    assert "Order 4521" in got and "Track it" in got and "1\tBLT-M8" in got
    assert "INJECTED" not in got and "evil.test" not in got and "color:red" not in got


def test_an_html_only_message_is_read_from_its_html_part() -> None:
    got = extract_text("body", "m", email_of("<p>Shipped <b>40</b> boxes</p>", "html")).text
    assert "Subject: RE: PO 1" in got and "Shipped 40 boxes" in got and "<p>" not in got


def test_headers_are_decoded_and_a_cc_is_kept() -> None:
    raw = email_of("Hi", extra="Cc: boss@buyer.test\r\n").replace(
        b"Subject: RE: PO 1", b"Subject: =?utf-8?q?Caf=C3=A9_PO?="
    )
    got = extract_text("body", "m", raw).text
    assert "Subject: Café PO" in got and "Cc: boss@buyer.test" in got


@pytest.mark.parametrize(
    "marker",
    [
        "-----Original Message-----",
        "On Mon, Nov 2, 2026 at 9:00 AM Buyer <b@buyer.test> wrote:",
        "From: Buyer\nSent: Monday, November 2, 2026 9:00 AM",
        "> did it ship?",
    ],
)
def test_an_earlier_message_is_marked_and_nothing_is_removed(marker: str) -> None:
    got = mark_quoted(f"Shipped today.\n\nRegards\n{marker}\nOld words")
    assert got.index("Shipped today") < got.index(QUOTE_NOTE) < got.index("Old words") and "Old words" in got


def test_a_from_line_in_a_document_is_not_taken_for_a_quote() -> None:
    body = "ORDER ACKNOWLEDGEMENT\nFrom: Umbrella Seals\nDate: 11/07/2026\nPO 4538802"
    assert QUOTE_NOTE not in mark_quoted(body)


def test_a_bad_character_set_does_not_stop_the_message() -> None:
    raw = email_of("Caf\xe9").replace(b"charset=utf-8", b"charset=no-such-set")
    assert "Caf" in extract_text("body", "m", raw).text


# ---- PDF and scans


def pdf_of(pages: list[str], **options: str) -> bytes:
    out = io.BytesIO()
    page = canvas.Canvas(out, pagesize=letter, **options)
    for text in pages:
        page.drawString(72, 700, text)
        page.showPage()
    page.save()
    return out.getvalue()


def test_a_text_pdf_gives_its_text_and_page_count() -> None:
    got = extract_text("pdf", "a.pdf", pdf_of(["Acknowledgement of PO 4521 shipping today from the warehouse"] * 2))
    assert got.pages == 2 and "PO 4521" in got.text


def test_a_pdf_over_the_page_limit_is_not_read() -> None:
    with pytest.raises(NotReadable, match="more than 50 pages"):
        extract_text("pdf", "a.pdf", pdf_of(["A page with enough words to count as text"] * (MAX_PDF_PAGES + 1)))


def test_a_password_protected_pdf_is_not_read() -> None:
    with pytest.raises(NotReadable, match="password"):
        extract_text("pdf", "a.pdf", pdf_of(["Secret order text for the vendor"], encrypt="hunter2"))


def picture_of(words: str) -> bytes:
    from PIL import ImageDraw, ImageFont

    image = Image.new("RGB", (900, 200), "white")
    ImageDraw.Draw(image).text((20, 60), words, fill="black", font=ImageFont.load_default(size=40))
    out = io.BytesIO()
    image.save(out, format="PNG")
    return out.getvalue()


@needs_tesseract
def test_a_scanned_pdf_is_read_by_ocr_from_its_page_images() -> None:
    out = io.BytesIO()
    page = canvas.Canvas(out, pagesize=letter)
    page.drawImage(
        __import__("reportlab.lib.utils", fromlist=["ImageReader"]).ImageReader(
            io.BytesIO(picture_of("PO 4521 SHIPPED"))
        ),
        50,
        500,
        500,
        111,
    )
    page.save()
    got = extract_text("pdf", "scan.pdf", out.getvalue()).text
    assert "4521" in got and "SHIPPED" in got


@needs_tesseract
def test_an_image_is_read_by_ocr() -> None:
    assert "4521" in extract_text("image", "a.png", picture_of("PO 4521")).text


def test_a_missing_tesseract_leaves_the_document_waiting(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(text_module.shutil, "which", lambda _name: None)
    with pytest.raises(ReaderUnavailable):
        extract_text("image", "a.png", picture_of("PO 4521"))


def test_a_file_that_is_not_an_image_is_refused() -> None:
    with pytest.raises(NotReadable, match="damaged"):
        extract_text("image", "a.png", b"not an image at all")


def test_an_image_with_too_many_pixels_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(text_module, "MAX_IMAGE_PIXELS", 1000)
    with pytest.raises(NotReadable, match="too large"):
        extract_text("image", "a.png", picture_of("PO 4521"))


# ---- spreadsheets, CSV and Word


def book_of(build) -> bytes:  # noqa: ANN001
    book = Workbook()
    build(book)
    out = io.BytesIO()
    book.save(out)
    return out.getvalue()


def test_a_spreadsheet_gives_its_values_row_by_row() -> None:
    def build(book: Workbook) -> None:
        sheet = book.active
        sheet.title = "Order"
        sheet.append(["Line", "Part", "Qty", "Price"])
        sheet.append([1, "BLT-M8x40", 200, 14.9])
        sheet.append([2, "SEAL-22", 150, 1.10])

    got = extract_text("spreadsheet", "a.xlsx", book_of(build)).text
    assert "Sheet: Order" in got and "1\tBLT-M8x40\t200\t14.9" in got and "2\tSEAL-22\t150\t1.1" in got


def test_a_formula_is_never_calculated_and_a_hidden_sheet_is_skipped() -> None:
    def build(book: Workbook) -> None:
        book.active.append(["Total", "=1+1"])
        hidden = book.create_sheet("Secret")
        hidden.append(["HIDDEN VALUE"])
        hidden.sheet_state = "hidden"

    got = extract_text("spreadsheet", "a.xlsx", book_of(build)).text
    assert "HIDDEN VALUE" not in got and "=1+1" not in got  # saved without a calculated value: nothing to show


def test_csv_and_tsv_are_read_with_their_own_separator() -> None:
    assert "A\tB" in extract_text("spreadsheet", "a.csv", b"A,B\r\n1,2\r\n").text
    assert "A\tB" in extract_text("spreadsheet", "a.tsv", b"A\tB\n1\t2\n").text
    assert "Café" in extract_text("spreadsheet", "a.csv", "Café,1".encode("cp1252")).text


def test_a_word_file_gives_paragraphs_and_tables_in_order() -> None:
    document = Document()
    document.add_paragraph("Order confirmation for PO 4521")
    table = document.add_table(rows=2, cols=2)
    for n, value in enumerate(["Part", "Qty", "BLT-M8", "200"]):
        table.cell(n // 2, n % 2).text = value
    document.add_paragraph("Thank you")
    out = io.BytesIO()
    document.save(out)
    got = extract_text("word", "a.docx", out.getvalue()).text
    assert got.index("Order confirmation") < got.index("Part\tQty") < got.index("BLT-M8\t200") < got.index("Thank you")


def zip_with(name: str, size: int) -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(name, b"\0" * size)
    return out.getvalue()


@pytest.mark.parametrize("kind", ["spreadsheet", "word"])
def test_a_zip_bomb_is_refused_before_it_is_opened(kind: str) -> None:
    with pytest.raises(NotReadable, match="unsafe size"):
        extract_text(kind, "a.xlsx", zip_with("xl/big.xml", 10_000_000))


def test_a_damaged_office_file_is_refused() -> None:
    with pytest.raises(NotReadable, match="damaged"):
        extract_text("word", "a.docx", b"PK not really")


# ---- plain text, limits and unknown kinds


def test_plain_text_is_cleaned_and_a_huge_text_is_cut_and_says_so() -> None:
    assert extract_text("text", "a.txt", b"Hello\x00 world\r\n\r\n\r\n\r\nBye").text == "Hello  world\n\nBye\n"
    got = extract_text("text", "a.txt", ("x" * (MAX_TEXT_CHARS + 50)).encode()).text
    assert got.rstrip().endswith(CUT_NOTE)


def test_a_kind_the_app_does_not_read_is_refused() -> None:
    with pytest.raises(NotReadable):
        extract_text("other", "a.zip", b"PK")


def test_a_word_file_with_an_entity_trick_does_not_leak_a_local_file(tmp_path: Path) -> None:
    secret = tmp_path / "secret.txt"
    secret.write_text("TOP-SECRET-VALUE")
    document = Document()
    document.add_paragraph("Order &s; confirmed")
    out = io.BytesIO()
    document.save(out)
    hostile = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(out.getvalue())) as source, zipfile.ZipFile(hostile, "w") as target:
        for item in source.infolist():
            data = source.read(item.filename)
            if item.filename == "word/document.xml":
                head, body = data.split(b"?>", 1)
                data = head + f'?><!DOCTYPE x [<!ENTITY s SYSTEM "file://{secret}">]>'.encode() + body
            target.writestr(item, data)
    try:
        got = extract_text("word", "a.docx", hostile.getvalue()).text
    except NotReadable:
        return  # refusing it is fine
    assert "TOP-SECRET" not in got


def test_a_spreadsheet_with_an_entity_trick_is_refused_by_the_text_step_too(tmp_path: Path) -> None:
    secret = tmp_path / "secret.txt"
    secret.write_text("TOP-SECRET-VALUE")
    base = book_of(lambda book: book.active.append(["a"]))
    hostile = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(base)) as source, zipfile.ZipFile(hostile, "w") as target:
        for item in source.infolist():
            data = source.read(item.filename)
            if item.filename == "xl/workbook.xml":
                body = data.split(b"?>", 1)[1] if data.startswith(b"<?xml") else data
                data = f'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY s SYSTEM "file://{secret}">]>'.encode() + body
            target.writestr(item, data)
    with pytest.raises(Exception) as refused:  # noqa: PT011 - whichever error the safe parser raises
        extract_text("spreadsheet", "a.xlsx", hostile.getvalue())
    assert "TOP-SECRET" not in str(refused.value)
