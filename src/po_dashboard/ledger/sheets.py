"""One reader for every upload (DEC-47): a CSV file (UTF-8 or Windows-1252) or an Excel workbook (.xlsx).

Whatever the file, the caller gets rows of plain text, header row first, so the importers do not care which it was.
An Excel file is read with care, because it is a zip of XML written by someone else:

- only the first sheet, and only each cell's saved value: a formula is never run (one with no saved value is empty);
- the XML is parsed by `defusedxml` (openpyxl uses it when it is installed; a test checks that it is), which refuses
  the entity tricks that make a small file expand into gigabytes;
- the file is refused before parsing when it is over 2 MB, more than 20 MB once unzipped, holds macros, or is the old
  .xls format; reading stops at 5,000 rows and 60 columns, whatever the file says its size is.
"""

from __future__ import annotations

import csv
import io
import warnings
import zipfile
from datetime import date, datetime, time
from decimal import Decimal

MAX_BYTES = 2 * 1024 * 1024
MAX_ROWS = 5000  # data rows; the header comes on top
MAX_UNZIPPED = 20 * 1024 * 1024
MAX_COLUMNS = 60
MAX_PARTS = 1000  # files inside the .xlsx zip
ZIP_MAGIC = b"PK\x03\x04"
OLE_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"  # the old .xls format, and any password-protected workbook


class SheetRejected(ValueError):
    """The file cannot be read at all. The message says why, for the person who uploaded it."""


def decode_csv(raw: bytes) -> str | None:
    """Text from a file saved as UTF-8 or as Windows-1252, which is how QuickBooks and older Excel save CSV."""
    if b"\x00" in raw:
        return None  # a picture, a spreadsheet or another binary file
    for encoding in ("utf-8-sig", "cp1252"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return None


def read_table(raw: bytes, filename: str | None = None) -> list[list[str]]:
    """Every row of the file as text, header first, with empty cells at the end of a row dropped.
    Raises SheetRejected when the file cannot be read."""
    if len(raw) > MAX_BYTES:
        raise SheetRejected(f"The file is larger than {MAX_BYTES // (1024 * 1024)} MB.")
    name = (filename or "").lower()
    if name.endswith((".xlsm", ".xltm", ".xlsb")):
        raise SheetRejected("Workbooks with macros (.xlsm, .xlsb) are not read. Save it as .xlsx or CSV.")
    if raw.startswith(OLE_MAGIC) or name.endswith(".xls"):
        raise SheetRejected(
            "This is the old Excel format (.xls) or a password-protected workbook. Save it as .xlsx or CSV."
        )
    rows = _xlsx_rows(raw) if raw.startswith(ZIP_MAGIC) else _csv_rows(raw)
    if len(rows) > MAX_ROWS + 1:
        raise SheetRejected(f"More than {MAX_ROWS} rows. Split the file.")
    return rows


def _trim(cells: list[str]) -> list[str]:
    while cells and not cells[-1].strip():
        cells.pop()
    return cells


def _csv_rows(raw: bytes) -> list[list[str]]:
    content = decode_csv(raw)
    if content is None:
        raise SheetRejected("The file does not look like a CSV text file or an Excel workbook, so it was not read.")
    rows: list[list[str]] = []
    try:
        for cells in csv.reader(io.StringIO(content)):
            rows.append(_trim(cells))
            if len(rows) > MAX_ROWS + 1:
                break
    except csv.Error as problem:
        raise SheetRejected(f"The file could not be read as CSV: {problem}") from None
    return rows


def _xlsx_rows(raw: bytes) -> list[list[str]]:
    try:
        archive = zipfile.ZipFile(io.BytesIO(raw))
        parts = archive.infolist()
    except (zipfile.BadZipFile, ValueError):
        raise SheetRejected("The file looks like an Excel workbook but could not be opened.") from None
    names = {part.filename.lower() for part in parts}
    if len(parts) > MAX_PARTS or sum(part.file_size for part in parts) > MAX_UNZIPPED:
        raise SheetRejected(f"The workbook is more than {MAX_UNZIPPED // (1024 * 1024)} MB once unzipped.")
    if any(name.endswith("vbaproject.bin") for name in names):
        raise SheetRejected("The workbook holds macros, so it was not read. Save it as .xlsx without macros, or CSV.")
    if "xl/workbook.xml" not in names:
        raise SheetRejected("The file is a zip but not an Excel workbook.")

    import openpyxl  # imported here: only an Excel upload needs it
    from openpyxl import xml as openpyxl_xml

    if not openpyxl_xml.DEFUSEDXML:  # without defusedxml openpyxl falls back to a parser that entity tricks get through
        raise SheetRejected("Excel files cannot be read safely on this computer (defusedxml is missing). Use CSV.")

    rows: list[list[str]] = []
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")  # openpyxl warns about styles and extensions it does not read
            book = openpyxl.load_workbook(io.BytesIO(raw), read_only=True, data_only=True, keep_links=False)
            try:
                if not book.worksheets:
                    raise SheetRejected("The workbook has no sheets.")
                sheet = book.worksheets[0]
                # The caps are given to the reader, so a sheet that claims to be huge is never padded out.
                for values in sheet.iter_rows(max_row=MAX_ROWS + 2, max_col=MAX_COLUMNS + 1, values_only=True):
                    cells = _trim([_text(value) for value in values])
                    if len(cells) > MAX_COLUMNS:
                        raise SheetRejected(f"The sheet has more than {MAX_COLUMNS} columns.")
                    rows.append(cells)
            finally:
                book.close()
    except SheetRejected:
        raise
    except Exception:  # a damaged or hostile workbook can fail in many ways; the details are never shown
        raise SheetRejected(
            "The workbook could not be read. It may be damaged; save it again as .xlsx or CSV."
        ) from None
    return rows


def _text(value: object) -> str:
    """A cell's saved value as the text a person would type: 10 not 10.0, dates as 2026-11-05."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if value.is_integer():
            return str(int(value))
        return format(Decimal(repr(value)).normalize(), "f")
    if isinstance(value, datetime):
        return value.date().isoformat() if value.time() == time(0) else value.isoformat(sep=" ", timespec="minutes")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, time):
        return value.isoformat(timespec="minutes")
    return str(value)
