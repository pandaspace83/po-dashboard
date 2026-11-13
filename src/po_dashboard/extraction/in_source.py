"""Is a value the model gave really in the document's text? (ARCHITECTURE section 6.3, `value_not_in_source`.)

The model can invent a value that looks real; this check finds a number, date or code that the text does not hold, so
a person looks before accepting it. It answers only "is it there", never "is it right".

- **Numbers** are compared as decimals: commas, currency signs, a minus sign and trailing zeros are ignored, so 14.9
  is found in "$14.90" and 1385.42 in "1,385.42". A number glued to letters ("M8x40") is part of a code, not a number.
- **Dates** in the usual US ways of writing them (DEC-49): 11/20/2026 and 11/20/26 (month first), 2026-11-20, Nov 20,
  2026, November 20th, 2026, and 20 November 2026. A date written with no year matches its month and day.
- **Codes** (PO, order, invoice and tracking numbers) are compared as letters and digits only, ignoring case: "P.O.
  4582964" holds 4582964.
- **Scanned text** (OCR): O and 0, l and 1, S and 5, B and 8 count as equal."""

from __future__ import annotations

import re
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

from po_dashboard.extraction.scoring import norm_id

_NUMBER = re.compile(r"(?<![A-Za-z0-9])(\d{1,3}(?:,\d{3})+|\d+)(\.\d+)?")
_MONTH = (
    r"(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|sep(?:t(?:ember)?)?"
    r"|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
)
_MONTHS = {name: n for n, name in enumerate(
    ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"), 1
)}  # fmt: skip
_NUMERIC_DATE = re.compile(r"(?<!\d)(\d{1,2})[/.-](\d{1,2})[/.-](\d{4}|\d{2})(?!\d)")
_ISO_DATE = re.compile(r"(?<!\d)(\d{4})-(\d{1,2})-(\d{1,2})(?!\d)")
_MONTH_FIRST = re.compile(rf"\b{_MONTH}\.?\s+(\d{{1,2}})(?:st|nd|rd|th)?\b(?:,?\s*(\d{{4}})\b)?", re.IGNORECASE)
_DAY_FIRST = re.compile(rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s+(?:of\s+)?{_MONTH}\b\.?,?\s+(\d{{4}})\b", re.IGNORECASE)
_RUN = re.compile(r"[A-Za-z0-9]+")

# OCR look-alikes. Each run of letters and digits that mixes the two is read the likelier way: as a number when its
# digits outnumber its other letters ("2O26", "l50", "45B2964"), else as a word ("N0vember", "A1icia"). An ordinal
# ("18th") is left alone, and lower-case s and b are not look-alikes, so "1st" stays "1st".
_LOOK_ALIKE_LETTERS = frozenset("OolISB")
_TO_DIGITS = str.maketrans({"O": "0", "o": "0", "l": "1", "I": "1", "S": "5", "B": "8"})
_TO_LETTERS = str.maketrans({"0": "o", "1": "l", "5": "s", "8": "b"})
_CODE_OCR = str.maketrans({"O": "0", "L": "1", "I": "1", "S": "5", "B": "8"})
_ORDINAL = re.compile(r"\d+(?:st|nd|rd|th)", re.IGNORECASE)


def ocr_view(text: str) -> str:
    """The text with OCR's look-alike letters and digits read the likelier way for each run (see above)."""

    def fix(run: re.Match[str]) -> str:
        word = run.group(0)
        digits = sum(c.isdigit() for c in word)
        if not digits or _ORDINAL.fullmatch(word):
            return word
        letters = sum(c.isalpha() and c not in _LOOK_ALIKE_LETTERS for c in word)
        return word.translate(_TO_DIGITS if digits > letters else _TO_LETTERS)

    return _RUN.sub(fix, text)


def _squash(value: str, ocr: bool) -> str:
    squashed = re.sub(r"[^A-Z0-9]", "", value.upper())
    return squashed.translate(_CODE_OCR) if ocr else squashed


def _day(year: int, month: int, day: int) -> date | None:
    try:
        return date(year, month, day)
    except ValueError:
        return None


class SourceText:
    """The numbers, dates and codes a document's text holds, read once."""

    def __init__(self, text: str, *, ocr: bool = False) -> None:
        self.ocr = ocr
        views = [text, ocr_view(text)] if ocr else [text]
        self.numbers: set[Decimal] = set()
        self.dates: set[date] = set()
        self.month_days: set[tuple[int, int]] = set()  # dates written with no year
        for view in views:
            self._read(view)
        self.squashed = _squash(text, ocr)

    def _read(self, view: str) -> None:
        # Dates first, and each whole date is blanked before numbers are read, so the 11 and 20 of 11/20/2026 do not
        # count as numbers: a quantity of 11 the text does not hold is still caught.
        spans: list[tuple[int, int]] = []
        for match in _NUMERIC_DATE.finditer(view):
            m, d, y = match.groups()
            self._add(_day(int(y) + 2000 if len(y) == 2 else int(y), int(m), int(d)), match, spans)
        for match in _ISO_DATE.finditer(view):
            y, m, d = match.groups()
            self._add(_day(int(y), int(m), int(d)), match, spans)
        for match in _MONTH_FIRST.finditer(view):
            name, d, y = match.groups()
            month = _MONTHS[name[:3].lower()]
            if y:
                self._add(_day(int(y), month, int(d)), match, spans)
            elif _day(2000, month, int(d)):  # no year: kept as month and day (2000 allows Feb 29); not blanked
                self.month_days.add((month, int(d)))
        for match in _DAY_FIRST.finditer(view):
            d, name, y = match.groups()
            self._add(_day(int(y), _MONTHS[name[:3].lower()], int(d)), match, spans)
        blanked = list(view)
        for start, end in spans:
            blanked[start:end] = " " * (end - start)
        for whole, fraction in _NUMBER.findall("".join(blanked)):
            self.numbers.add(Decimal(whole.replace(",", "") + fraction))

    def _add(self, found: date | None, match: re.Match[str], spans: list[tuple[int, int]]) -> None:
        if found is not None:
            self.dates.add(found)
            spans.append(match.span())

    def has_number(self, value: Any) -> bool:
        if value is None or isinstance(value, bool):
            return False
        try:
            wanted = abs(Decimal(str(value)))
        except InvalidOperation:
            return False
        return wanted.is_finite() and wanted in self.numbers

    def has_date(self, value: Any) -> bool:
        try:
            wanted = date.fromisoformat(str(value))
        except ValueError:
            return False
        return wanted in self.dates or (wanted.month, wanted.day) in self.month_days

    def has_code(self, value: Any) -> bool:
        normal = norm_id(value)
        wanted = _squash(normal, self.ocr) if normal else ""
        return bool(wanted) and wanted in self.squashed
