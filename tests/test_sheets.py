"""One reader for CSV and Excel uploads (DEC-47), and the hostile files it must refuse without harm."""

import io
import time
import zipfile
from datetime import date, datetime

import openpyxl
import pytest
from fastapi.testclient import TestClient

from po_dashboard.ledger import sheets
from po_dashboard.ledger.sheets import SheetRejected, read_table
from test_gate import ORIGIN, env, token_in  # noqa: F401
from test_orders_web import client, make_vendor  # noqa: F401

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def workbook(*rows: list[object], extra_sheet: list[list[object]] | None = None) -> bytes:
    book = openpyxl.Workbook()
    sheet = book.active
    assert sheet is not None
    for row in rows:
        sheet.append(row)
    if extra_sheet:
        other = book.create_sheet("Second")
        for row in extra_sheet:
            other.append(row)
    out = io.BytesIO()
    book.save(out)
    return out.getvalue()


def replace_part(raw: bytes, name: str, content: bytes) -> bytes:
    """The same workbook with one file inside it swapped, as a hostile sender would do."""
    source = zipfile.ZipFile(io.BytesIO(raw))
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as target:
        for part in source.infolist():
            target.writestr(part.filename, content if part.filename == name else source.read(part.filename))
        if name not in source.namelist():
            target.writestr(name, content)
    return out.getvalue()


SHEET = "xl/worksheets/sheet1.xml"
NS = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'


# ---- ordinary files


def test_the_reader_uses_the_safe_xml_parser() -> None:
    from openpyxl import xml

    assert xml.DEFUSEDXML is True  # defusedxml is installed and openpyxl uses it; without it, entity bombs get through


def test_a_csv_reads_as_text_with_empty_cells_at_the_end_dropped() -> None:
    assert read_table(b"a,b,,\n1,,\n") == [["a", "b"], ["1"]]
    assert read_table("name\nSociété\n".encode("cp1252")) == [["name"], ["Société"]]


def test_an_excel_cell_reads_as_a_person_would_type_it() -> None:
    raw = workbook(
        ["qty", "price", "when", "at", "flag", "note", "blank"],
        [10, 2.5, date(2026, 11, 5), datetime(2026, 11, 5, 14, 30), True, "Société", None],
        [10.0, 0.1, datetime(2026, 11, 5), None, False, "  spaced  "],
    )
    assert read_table(raw, "pos.xlsx") == [
        ["qty", "price", "when", "at", "flag", "note", "blank"],
        ["10", "2.5", "2026-11-05", "2026-11-05 14:30", "TRUE", "Société"],
        ["10", "0.1", "2026-11-05", "", "FALSE", "  spaced  "],
    ]


def test_only_the_first_sheet_is_read() -> None:
    raw = workbook(["a"], ["1"], extra_sheet=[["b"], ["2"]])
    assert read_table(raw, "x.xlsx") == [["a"], ["1"]]


def test_a_formula_is_never_run() -> None:
    raw = workbook(["total"], ["=1+1"], ['=HYPERLINK("http://evil.example","x")'])
    assert read_table(raw, "x.xlsx") == [["total"], [], []]  # no saved value: empty, never the formula or its result


# ---- refused before anything is parsed


@pytest.mark.parametrize(
    ("raw", "name", "message"),
    [
        (b"x" * (2 * 1024 * 1024 + 1), "big.csv", "larger than 2 MB"),
        (b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\0" * 64, "old.xls", "old Excel format"),
        (b"a,b\n", "book.xlsm", "macros"),
        (b"PK\x03\x04 not really a zip", "x.xlsx", "could not be opened"),
        (b"\x89PNG\r\n\x1a\n\0\0", "picture.csv", "does not look like a CSV"),
    ],
)  # fmt: skip
def test_files_that_are_not_readable_spreadsheets_are_refused(raw: bytes, name: str, message: str) -> None:
    with pytest.raises(SheetRejected, match=message):
        read_table(raw, name)


def test_a_zip_that_is_not_a_workbook_is_refused() -> None:
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as target:
        target.writestr("readme.txt", "hello")
    with pytest.raises(SheetRejected, match="not an Excel workbook"):
        read_table(out.getvalue(), "x.xlsx")


def test_a_workbook_with_macros_is_refused_whatever_its_name() -> None:
    raw = replace_part(workbook(["a"]), "xl/vbaProject.bin", b"\0" * 100)
    with pytest.raises(SheetRejected, match="macros"):
        read_table(raw, "innocent.xlsx")


# ---- hostile workbooks


def test_a_zip_bomb_is_refused_from_its_declared_size() -> None:
    raw = replace_part(workbook(["a"]), "xl/media/padding.bin", b"\0" * (21 * 1024 * 1024))
    assert len(raw) < 200 * 1024  # small on the way in
    with pytest.raises(SheetRejected, match="more than 20 MB once unzipped"):
        read_table(raw, "x.xlsx")


def test_an_entity_bomb_is_refused_quickly() -> None:
    laughs = (
        b'<?xml version="1.0"?><!DOCTYPE lolz [<!ENTITY lol "lol">'
        + b"".join(f'<!ENTITY lol{n} "{("&lol" + str(n - 1) + ";") * 10 if n > 1 else "&lol;" * 10}">'.encode()
                   for n in range(1, 10))
        + f'<worksheet {NS}><sheetData><row r="1"><c r="A1" t="inlineStr"><is><t>&lol9;</t></is></c></row>'
          "</sheetData></worksheet>".encode()
    )  # fmt: skip
    raw = replace_part(workbook(["a"]), SHEET, laughs)
    start = time.perf_counter()
    with pytest.raises(SheetRejected, match="could not be read"):
        read_table(raw, "x.xlsx")
    assert time.perf_counter() - start < 2


def test_a_workbook_cannot_read_files_from_the_mac(tmp_path) -> None:  # noqa: ANN001
    secret = tmp_path / "secret.txt"
    secret.write_text("TOP-SECRET-VALUE")
    xxe = (
        f'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY s SYSTEM "file://{secret}">]>'
        f'<worksheet {NS}><sheetData><row r="1"><c r="A1" t="inlineStr"><is><t>&s;</t></is></c></row>'
        "</sheetData></worksheet>"
    ).encode()
    raw = replace_part(workbook(["a"]), SHEET, xxe)
    with pytest.raises(SheetRejected) as refused:
        read_table(raw, "x.xlsx")
    assert "TOP-SECRET" not in str(refused.value)


def test_a_sheet_that_claims_to_be_huge_is_cut_off_quickly() -> None:
    huge = (
        f'<?xml version="1.0"?><worksheet {NS}><dimension ref="A1:XFD1048576"/><sheetData>'
        '<row r="1"><c r="A1" t="inlineStr"><is><t>a</t></is></c></row>'
        '<row r="1048576"><c r="XFD1048576" t="inlineStr"><is><t>z</t></is></c></row>'
        "</sheetData></worksheet>"
    ).encode()
    raw = replace_part(workbook(["a"]), SHEET, huge)
    start = time.perf_counter()
    with pytest.raises(SheetRejected, match="More than 5000 rows"):
        read_table(raw, "x.xlsx")
    assert time.perf_counter() - start < 5


def test_too_many_columns_is_refused() -> None:
    with pytest.raises(SheetRejected, match="more than 60 columns"):
        read_table(workbook([f"c{n}" for n in range(61)]), "x.xlsx")


def test_a_damaged_workbook_says_so_without_details() -> None:
    raw = replace_part(workbook(["a"]), SHEET, b"<worksheet><sheetData><row")
    with pytest.raises(SheetRejected, match="could not be read") as refused:
        read_table(raw, "x.xlsx")
    assert "Traceback" not in str(refused.value) and "xml" not in str(refused.value).lower()


def test_the_limits_are_the_ones_the_decision_names() -> None:
    assert (sheets.MAX_BYTES, sheets.MAX_ROWS, sheets.MAX_UNZIPPED, sheets.MAX_COLUMNS) == (
        2 * 1024 * 1024, 5000, 20 * 1024 * 1024, 60
    )  # fmt: skip


# ---- the uploads take Excel files


def _upload(client: TestClient, path: str, raw: bytes, name: str):  # noqa: ANN202
    headers = {**ORIGIN, "X-CSRF-Token": token_in(client.get(path).text)}
    return client.post(path, files={"file": (name, raw, XLSX)}, headers=headers)


def test_vendors_can_be_uploaded_as_an_excel_file(client: TestClient) -> None:
    raw = workbook(["vendor_name", "contact_email"], ["Acme Fasteners", "pat@acme.example"])
    response = _upload(client, "/vendors/import", raw, "vendors.xlsx")
    assert response.status_code == 200 and "1 created" in response.text
    assert "acme.example" in client.get("/vendors").text


def test_purchase_orders_can_be_uploaded_as_an_excel_file_with_real_date_cells(client: TestClient) -> None:
    make_vendor(client)
    header = ["po_number", "vendor", "currency", "line_no", "item", "quantity", "unit", "unit_price", "date_sent"]
    raw = workbook(header, ["PO-900", "Acme Fasteners", "USD", 1, "Bolt", 10, "EA", 2.5, date(2026, 11, 4)])
    response = _upload(client, "/orders/import", raw, "pos.xlsx")
    assert response.status_code == 303, response.text
    page = client.get(response.headers["location"]).text
    assert "PO-900" in page


def test_the_upload_pages_offer_excel_files(client: TestClient) -> None:
    for path in ("/orders/import", "/vendors/import"):
        assert ".xlsx" in client.get(path).text, path


def _with_shared_strings(sst: bytes) -> bytes:
    """A workbook whose cell A1 really uses the shared-strings part, as Excel itself writes them."""
    base = workbook(["a"])
    types = zipfile.ZipFile(io.BytesIO(base)).read("[Content_Types].xml").replace(
        b"</Types>",
        b'<Override PartName="/xl/sharedStrings.xml" '
        b'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sharedStrings+xml"/></Types>',
    )
    cell = f'<?xml version="1.0"?><worksheet {NS}><sheetData><row r="1"><c r="A1" t="s"><v>0</v></c></row>'
    raw = replace_part(base, "[Content_Types].xml", types)
    raw = replace_part(raw, SHEET, (cell + "</sheetData></worksheet>").encode())
    return replace_part(raw, "xl/sharedStrings.xml", sst)


def test_shared_strings_are_read_and_an_entity_trick_in_them_is_refused(tmp_path) -> None:  # noqa: ANN001
    plain = f'<?xml version="1.0"?><sst {NS} count="1" uniqueCount="1"><si><t>hello</t></si></sst>'.encode()
    assert read_table(_with_shared_strings(plain), "x.xlsx") == [["hello"]]
    secret = tmp_path / "secret.txt"
    secret.write_text("TOP-SECRET-VALUE")
    hostile = (
        f'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY s SYSTEM "file://{secret}">]>'
        f'<sst {NS} count="1" uniqueCount="1"><si><t>&s;</t></si></sst>'
    ).encode()
    with pytest.raises(SheetRejected) as refused:
        read_table(_with_shared_strings(hostile), "x.xlsx")
    assert "TOP-SECRET" not in str(refused.value)


@pytest.mark.parametrize(
    "part", ["xl/workbook.xml", "[Content_Types].xml", "xl/styles.xml", "xl/_rels/workbook.xml.rels"]
)
def test_an_entity_trick_in_any_part_that_is_read_is_refused(part: str, tmp_path) -> None:  # noqa: ANN001
    secret = tmp_path / "secret.txt"
    secret.write_text("TOP-SECRET-VALUE")
    base = workbook(["a"])
    original = zipfile.ZipFile(io.BytesIO(base)).read(part)
    body = original.split(b"?>", 1)[1] if original.startswith(b"<?xml") else original
    hostile = f'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY s SYSTEM "file://{secret}">]>'.encode() + body
    with pytest.raises(SheetRejected) as refused:
        read_table(replace_part(base, part, hostile), "x.xlsx")
    assert "TOP-SECRET" not in str(refused.value)


def test_without_the_safe_parser_excel_files_are_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    from openpyxl import xml

    monkeypatch.setattr(xml, "DEFUSEDXML", False)
    with pytest.raises(SheetRejected, match="defusedxml is missing"):
        read_table(workbook(["a"]), "x.xlsx")
    assert read_table(b"a\n1\n", "x.csv") == [["a"], ["1"]]  # CSV still works
