"""The read-only interface the assistant's workflows call (ARCHITECTURE §17.3, §17.4; DEC-64).

One method per kind of question. Each returns rows copied from the records, so every figure and identifier in an answer
comes from here (DEC-69). There is no method that writes, and none takes SQL or free text to search with: a filter is
an identifier, a name, or one of a fixed set of words. A record is found only when its key is equal, compared the way
the app compares it (DEC-57): never a near match, never a guessed PO (DEC-09).

Two adapters fill it. `SyntheticBackend` answers from made-up fixtures in `tests/data/assistant/` (Stage 1, DEC-13).
The live adapter (Stage 2) will wrap the functions in §17.1 over a connection with `PRAGMA query_only = ON`.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Literal, Protocol, Self, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field, model_validator

from po_dashboard.customers.keys import name_key, po_key
from po_dashboard.quickbooks.reconciliation.engine import CODES, first_gap, merged
from po_dashboard.quickbooks.reconciliation.views import LEVELS, STATUS_WORDS

# The Open items page's choices (ledger/reports.SHOWS); a test keeps the two the same.
Show = Literal["all", "attention", "late", "no_ack", "slipped", "needs_update"]
ExceptionLevel = Literal["confirmed", "preliminary"]  # DEC-59

DEFAULT_FIXTURES = Path(__file__).resolve().parents[3] / "tests" / "data" / "assistant" / "records.json"


class _Row(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


LineStatusWord = Literal["Sent", "Acknowledged", "Partially shipped", "Shipped", "Closed", "Cancelled"]  # LineStatus


class LineRow(_Row):
    """One PO line as the PO page and Open items show it (ledger/status, ledger/expected, ledger/reports). The flags
    are the report's own facts; their words come from the same rules as `OpenItem.flags`, and a test keeps them so."""

    line_no: int = Field(ge=1)
    item: str
    description: str = ""
    unit: str
    ordered: Decimal
    open_qty: Decimal
    status: LineStatusWord
    promised_history: tuple[date, ...] = ()  # every promised date, in the order the vendor gave them
    eta: date | None = None  # the latest shipment estimate
    days_late: int = Field(default=0, ge=0)  # past the expected date while open, on the backend's as_of day
    no_ack: bool = False  # sent, and no acknowledgement for more than 2 business days (DEC-22)
    needs_update: bool = False  # the vendor confirmed a different quantity: the original PO needs updating (DEC-36)

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if self.done and (self.open_qty != 0 or self.days_late or self.no_ack or self.needs_update):
            raise ValueError("a closed or cancelled line has nothing open and no flag")
        if self.days_late and self.expected is None:
            raise ValueError("a line is late only against a date")
        return self

    @property
    def done(self) -> bool:
        return self.status in ("Closed", "Cancelled")

    @property
    def promised(self) -> date | None:
        """The vendor's latest promised date."""
        return self.promised_history[-1] if self.promised_history else None

    @property
    def expected(self) -> date | None:
        """The date to plan on: the shipment estimate if there is one, else the promised date (ledger/expected)."""
        return self.eta or self.promised

    @property
    def late(self) -> bool:
        return self.days_late > 0

    @property
    def slipped(self) -> bool:
        """A promised date moved later at least once (ledger/reports._slipped_lines)."""
        return any(b > a for a, b in zip(self.promised_history, self.promised_history[1:], strict=False))

    @property
    def flags(self) -> tuple[str, ...]:
        found = []
        if self.late:
            found.append(f"{self.days_late} days late" if self.days_late != 1 else "1 day late")
        if self.no_ack:
            found.append("no acknowledgement")
        if self.slipped:
            found.append("promised date slipped")
        if self.needs_update:
            found.append("update the original PO")
        return tuple(found)

    @property
    def next_step(self) -> str:
        if self.late:
            return "Ask the vendor for a new date"
        if self.no_ack:
            return "Ask the vendor to acknowledge the PO"
        if self.needs_update:
            return "Update the PO in QuickBooks to the confirmed quantity"
        if self.slipped:
            return "Check the new promised date"
        return ""


class PoStatus(_Row):
    po_number: str
    vendor: str
    vendor_contact: str | None = None  # the contact address on the vendor record (DEC-40 (3))
    date_sent: date | None = None
    lines: tuple[LineRow, ...] = Field(min_length=1)

    @property
    def status(self) -> Literal["Open", "Closed"]:
        """A PO is open while any line is (ledger/status.po_states)."""
        return "Closed" if all(line.done for line in self.lines) else "Open"


class OpenItemRow(LineRow):
    """One open line on the Open items report."""

    po_number: str
    vendor: str


class CustomerOrderLine(_Row):
    line_no: int = Field(ge=1)
    item: str
    qty: Decimal
    unit_price: Decimal


class CustomerOrder(_Row):
    """A customer PO as the Customer orders list shows it (customers/listing, reconciliation/views)."""

    number: str
    customer: str
    customer_contact: str | None = None
    revision: int = Field(ge=1)
    pending_revision: bool = False  # a newer revision waits for a person
    po_date: date | None = None
    currency: str
    total: Decimal
    invoiced: Decimal | None = None  # None until it is reconciled
    remaining: Decimal | None = None
    status: str  # a key of reconciliation/views.STATUS_WORDS
    invoices: tuple[str, ...] = ()  # the sales invoice numbers matched to it
    lines: tuple[CustomerOrderLine, ...] = ()

    @model_validator(mode="after")
    def _known_status(self) -> Self:
        if self.status not in STATUS_WORDS:
            raise ValueError(f"unknown reconciliation status {self.status!r}")
        return self

    @property
    def status_words(self) -> str:
        return STATUS_WORDS[self.status]


class ExceptionRow(_Row):
    """One reconciliation exception as the exceptions queue shows it (reconciliation/views.queue)."""

    code: str  # a key of reconciliation/engine.CODES
    level: ExceptionLevel  # usually the code's own level, but the engine may raise or lower it
    message: str
    customer: str | None = None
    customer_po: str | None = None
    invoice: str | None = None
    amount: Decimal | None = None  # the difference, in the PO's or the invoice's currency
    day: date | None = None

    @model_validator(mode="after")
    def _known_code(self) -> Self:
        if self.code not in CODES:
            raise ValueError(f"unknown exception code {self.code!r}")
        return self

    @property
    def label(self) -> str:
        return CODES[self.code][0]

    @property
    def level_words(self) -> str:
        return LEVELS[self.level]


class Upload(_Row):
    file_name: str  # for display only, never a path
    uploaded: date
    period_from: date | None = None
    period_to: date | None = None

    @model_validator(mode="after")
    def _period(self) -> Self:
        if (self.period_from is None) != (self.period_to is None):
            raise ValueError("an upload's period has both ends or neither")
        if self.period_from and self.period_to and self.period_from > self.period_to:
            raise ValueError("a period ends on or after its start")
        return self


class Uploads(_Row):
    """The QuickBooks sales uploads, newest first, and the days they cover together."""

    uploads: tuple[Upload, ...] = ()

    @property
    def latest(self) -> Upload | None:
        return max(self.uploads, key=lambda u: u.uploaded, default=None)

    @property
    def periods(self) -> tuple[tuple[date, date], ...]:
        return tuple((u.period_from, u.period_to) for u in self.uploads if u.period_from and u.period_to)

    @property
    def covered(self) -> list[tuple[date, date]]:
        """The uploads' periods joined where they meet or overlap (reconciliation/engine.merged)."""
        return merged(self.periods)

    def first_gap(self, start: date, end: date) -> tuple[date, date] | None:
        """The first days from start to end that no upload covers (reconciliation/engine.first_gap)."""
        return first_gap(self.periods, start, end)


@runtime_checkable
class RecordsBackend(Protocol):
    """What the workflows may read. Every method reads; none writes."""

    def as_of(self) -> date:
        """The day lateness and the other results are worked out for."""
        ...

    def po_status(self, po_number: str) -> PoStatus | None:
        """One PO with its lines, or None when there is no PO with that number."""
        ...

    def open_items(self, show: Show = "all", vendor: str | None = None) -> list[OpenItemRow]:
        """The Open items report: every open line, oldest PO first, narrowed to one kind of trouble or one vendor."""
        ...

    def customer_orders(self, number: str | None = None, customer: str | None = None) -> list[CustomerOrder]:
        """Customer POs, narrowed by number or customer. One number can belong to two customers, so this is a list."""
        ...

    def exceptions(
        self, level: ExceptionLevel | None = None, customer: str | None = None, customer_po: str | None = None
    ) -> list[ExceptionRow]:
        """The reconciliation exceptions, narrowed by level, customer or customer PO."""
        ...

    def uploads(self) -> Uploads:
        """The QuickBooks sales uploads and the periods they cover."""
        ...


class Fixtures(_Row):
    """The synthetic adapter's records: made-up data only (DEC-13). Checked as they load, so a fixture cannot say
    something the live functions never would."""

    about: str = ""  # what the file is, for a person reading it
    as_of: date
    pos: tuple[PoStatus, ...] = ()
    customer_orders: tuple[CustomerOrder, ...] = ()
    exceptions: tuple[ExceptionRow, ...] = ()
    uploads: tuple[Upload, ...] = ()

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        numbers = [po.po_number.lower() for po in self.pos]
        if len(set(numbers)) != len(numbers):
            raise ValueError("PO numbers are unique, ignoring case")
        orders = [(name_key(o.customer), po_key(o.number)) for o in self.customer_orders]
        if len(set(orders)) != len(orders):
            raise ValueError("a customer PO number is unique within its customer")
        for po in self.pos:
            for line in po.lines:
                late = 0 if line.done or line.expected is None else max((self.as_of - line.expected).days, 0)
                if line.days_late != late:
                    raise ValueError(f"{po.po_number} line {line.line_no}: days late is {late} on {self.as_of}")
        return self


def _same_name(a: str | None, b: str) -> bool:
    return a is not None and name_key(a) == name_key(b)


class SyntheticBackend:
    """Answers from made-up fixtures, the way the live functions would: the same filters, the same order, the same
    comparisons. It holds a copy and has nothing that changes it."""

    def __init__(self, fixtures: Fixtures) -> None:
        self._data = fixtures

    @classmethod
    def from_file(cls, path: Path = DEFAULT_FIXTURES) -> SyntheticBackend:
        return cls(Fixtures.model_validate_json(path.read_text(encoding="utf-8")))

    def as_of(self) -> date:
        return self._data.as_of

    def po_status(self, po_number: str) -> PoStatus | None:
        wanted = po_number.strip().lower()
        return next((po for po in self._data.pos if po.po_number.lower() == wanted), None)

    def open_items(self, show: Show = "all", vendor: str | None = None) -> list[OpenItemRow]:
        rows: list[OpenItemRow] = []
        for po in self._data.pos:
            if vendor is not None and not _same_name(po.vendor, vendor):
                continue
            for line in po.lines:
                if line.done or not _shows(line, show):
                    continue
                rows.append(OpenItemRow(po_number=po.po_number, vendor=po.vendor, **line.model_dump()))
        return rows

    def customer_orders(self, number: str | None = None, customer: str | None = None) -> list[CustomerOrder]:
        return [
            o
            for o in self._data.customer_orders
            if (number is None or po_key(o.number) == po_key(number))
            and (customer is None or _same_name(o.customer, customer))
        ]

    def exceptions(
        self, level: ExceptionLevel | None = None, customer: str | None = None, customer_po: str | None = None
    ) -> list[ExceptionRow]:
        return [
            e
            for e in self._data.exceptions
            if (level is None or e.level == level)
            and (customer is None or _same_name(e.customer, customer))
            and (customer_po is None or (e.customer_po is not None and po_key(e.customer_po) == po_key(customer_po)))
        ]

    def uploads(self) -> Uploads:
        return Uploads(uploads=tuple(sorted(self._data.uploads, key=lambda u: u.uploaded, reverse=True)))


def _shows(line: LineRow, show: Show) -> bool:
    """The Open items filter, as ledger/reports.open_items applies it."""
    match show:
        case "all":
            return True
        case "attention":
            return bool(line.next_step)
        case "late":
            return line.late
        case "no_ack":
            return line.no_ack
        case "slipped":
            return line.slipped
        case "needs_update":
            return line.needs_update
