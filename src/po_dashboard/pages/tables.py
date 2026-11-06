"""Sorting and paging for every list (DEC-45 item 6): a click on a heading sorts, 50 rows a page, "1–50 of 312".

Lists are sorted and cut in Python because several columns (status, expected date, days late) are worked out, not
stored. The choices live in the address, so a view can be bookmarked and Back returns to it.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

from starlette.requests import Request

PAGE_SIZE = 50


@dataclass(frozen=True)
class Sorting:
    key: str
    descending: bool

    @property
    def value(self) -> str:
        """As it is written in the address: "vendor", or "-vendor" for the other way."""
        return ("-" if self.descending else "") + self.key

    def next_for(self, key: str) -> str:
        """The value a heading's link sets: the same column flips direction, another starts ascending."""
        if key == self.key:
            return key if self.descending else "-" + key
        return key

    def aria(self, key: str) -> str:
        if key != self.key:
            return "none"
        return "descending" if self.descending else "ascending"


@dataclass(frozen=True)
class Page[T]:
    rows: list[T]
    number: int
    pages: int
    total: int
    size: int = PAGE_SIZE

    @property
    def first(self) -> int:
        return (self.number - 1) * self.size + 1 if self.total else 0

    @property
    def last(self) -> int:
        return min(self.number * self.size, self.total)


def sort_rows[T](
    rows: Sequence[T], raw: str | None, keys: Mapping[str, Callable[[T], Any]], default: str
) -> tuple[list[T], Sorting]:
    """Sort by the column named in `raw` ("vendor", or "-vendor" for the other way). An unknown name uses `default`.
    Empty values always go last, whichever way the column is sorted."""
    wanted = (raw or "").strip()
    descending = wanted.startswith("-")
    key = wanted.lstrip("-")
    if key not in keys:
        descending = default.startswith("-")
        key = default.lstrip("-")
    value = keys[key]
    present = [row for row in rows if value(row) is not None]
    empty = [row for row in rows if value(row) is None]
    present.sort(key=value, reverse=descending)
    return present + empty, Sorting(key, descending)


def page_of[T](rows: Sequence[T], raw: str | None, size: int = PAGE_SIZE) -> Page[T]:
    """One page of the rows. A page number that is not a number, or is past the end, shows the nearest real page."""
    total = len(rows)
    pages = max(1, -(-total // size))
    text = (raw or "").strip()
    number = int(text) if text.isascii() and text.isdigit() and len(text) < 7 else 1
    number = min(max(number, 1), pages)
    start = (number - 1) * size
    return Page(list(rows[start : start + size]), number, pages, total, size)


def with_query(request: Request, **changes: Any) -> str:
    """This page's address with some query values changed. None or "" removes a value. A new sort starts at page 1."""
    params = dict(request.query_params)
    if "sort" in changes:
        params.pop("page", None)
    for name, value in changes.items():
        if value is None or value == "":
            params.pop(name, None)
        else:
            params[name] = str(value)
    query = urlencode([(name, value) for name, value in params.items() if value != ""])
    return request.url.path + ("?" + query if query else "")
