"""The reconciliation engine (DEC-59, DEC-62): customer POs against QuickBooks sales invoices, in plain code.

No model, no database, no clock: `reconcile(book)` takes plain records and the day to judge by, and gives each PO's
status, its figures and its exceptions. The same book always gives the same result, whatever order its records come
in. The loader in `store.py` builds the book from the tables; nothing here knows a QuickBooks column name (DEC-60).

The order of evidence (DEC-59): the customer, exactly; a QuickBooks reference where both sides carry one; the invoice's
PO reference, exactly, within that customer; then lines by exactly equal item; then quantities, prices and currency.
A person's decision is used before any of these and is never undone by a re-run. Nothing is matched by a similar name
or an equal amount, and anything with two candidates, or none, waits for a person.

An exception is a `Problem` (the word "exception" means something else in Python). Each has a level: **confirmed**
when it rests on line evidence on both sides of an exact or person-made match, else **preliminary**; and it may be
**blocking**, which means the PO's figures cannot be trusted until a person looks (the status is then needs review)."""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal

from po_dashboard.customers.keys import po_key

ZERO = Decimal(0)
CONFIRMED, PRELIMINARY = "confirmed", "preliminary"

# code: (what the exception queue calls it, its usual level, whether it blocks the PO's status)
CODES: dict[str, tuple[str, str, bool]] = {
    "not_found": ("Not found in the QuickBooks exports", PRELIMINARY, False),
    "possibly_unbilled": ("Possibly unbilled", PRELIMINARY, False),
    "partially_invoiced": ("Partially invoiced", PRELIMINARY, False),
    "amount_mismatch": ("Amount mismatch", PRELIMINARY, False),
    "quantity_mismatch": ("Quantity mismatch", CONFIRMED, False),
    "price_mismatch": ("Unit price mismatch", CONFIRMED, True),
    "currency_mismatch": ("Currency mismatch", CONFIRMED, True),
    "potential_duplicate": ("Potential duplicate", CONFIRMED, True),
    "unmatched_invoice": ("Unmatched invoice", PRELIMINARY, True),
    "ambiguous_match": ("Ambiguous match", PRELIMINARY, True),
    "revised_po": ("Revised PO waiting for review", PRELIMINARY, True),
    "credit_memo": ("Credit memo waiting for a person", PRELIMINARY, True),
    "charge_unclassified": ("Charge not classified", PRELIMINARY, True),
    "charges_unassigned": ("Charges not split between POs", PRELIMINARY, False),
    "invoice_changed": ("Invoice changed since it was accepted", PRELIMINARY, True),
    "invoice_incomplete": ("Invoice not read completely", PRELIMINARY, True),
    "stale_decision": ("A person's decision no longer fits", PRELIMINARY, True),
}

# the status of a customer PO, the first that applies (DEC-59)
STATUSES: dict[str, str] = {
    "cancelled": "Cancelled",
    "closed": "Closed",
    "needs_review": "Needs review",
    "over_invoiced": "Over-invoiced",
    "matched": "Matched",
    "matched_by_amount": "Matched by amount only",
    "partially_invoiced": "Partially invoiced",
    "no_invoice": "No invoice in the uploaded files",
    "not_covered": "Not covered by any upload",
}

MISSING_BILLING = frozenset({"not_found", "possibly_unbilled", "partially_invoiced"})  # not said of a closed PO


# ---- what goes in


@dataclass(frozen=True)
class PoLine:
    id: int
    line_no: int
    item: str
    qty: Decimal
    unit_price: Decimal

    @property
    def amount(self) -> Decimal:
        return self.qty * self.unit_price


@dataclass(frozen=True)
class Po:
    """The revision in force of one customer PO (only that revision is reconciled, DEC-59)."""

    id: int
    customer_id: int
    number: str
    revision: int
    state: str  # active, closed or cancelled
    since: date  # the original PO's date, or the day it was filed when it has none
    currency: str
    lines: tuple[PoLine, ...]
    pending_revision: int | None = None  # a newer revision waiting for a person
    revised_on: date | None = None  # when the revision in force was approved, for a revision after the first
    qb_references: frozenset[str] = frozenset()  # lower case; empty until a source fills it (DEC-62)
    earlier: tuple[int, ...] = ()  # the ids of its other revisions, for its history

    @property
    def key(self) -> str:
        return po_key(self.number)

    @property
    def ordered(self) -> Decimal:
        return sum((line.amount for line in self.lines), ZERO)


@dataclass(frozen=True)
class InvoiceLine:
    id: int
    position: int
    item: str | None
    qty: Decimal | None
    unit_price: Decimal | None
    amount: Decimal
    kind: str  # goods, freight, tax, discount, other


@dataclass(frozen=True)
class Invoice:
    id: int
    kind: str  # invoice or credit_memo
    number: str
    customer_id: int | None  # empty until a person says which customer the exported name is
    customer_name: str | None
    date: date | None
    currency: str
    total: Decimal
    po_reference: str | None  # as exported, possibly a list
    qb_reference: str | None
    level: str  # line or invoice
    lines: tuple[InvoiceLine, ...] = ()
    review: str | None = None  # why it waits for a person: duplicate_number, changed or incomplete


@dataclass(frozen=True)
class Decision:
    """A person's decision: this invoice (or line) belongs to this PO (or line), or it does not."""

    id: int
    state: str  # confirmed or rejected
    po_id: int  # the revision in force (the loader carries a decision on an earlier revision over)
    invoice_id: int
    po_line_id: int | None = None
    invoice_line_id: int | None = None
    amount: Decimal | None = None  # how much of an invoice with only a total, or of a credit memo
    stale: str | None = None  # why it no longer fits the revision in force


@dataclass(frozen=True)
class Rules:
    today: date
    amount_tolerance: Decimal = Decimal("0.01")
    quantity_tolerance: Decimal = ZERO
    unbilled_days: int = 30


@dataclass(frozen=True)
class Book:
    pos: tuple[Po, ...]
    invoices: tuple[Invoice, ...]
    rules: Rules
    decisions: tuple[Decision, ...] = ()
    periods: tuple[tuple[date, date], ...] = ()  # the dates each upload covers


# ---- what comes out


@dataclass(frozen=True)
class Problem:
    code: str
    level: str
    blocking: bool
    message: str
    po_id: int | None = None
    invoice_id: int | None = None
    po_line_id: int | None = None
    invoice_line_id: int | None = None
    amount: Decimal | None = None  # the difference, in the PO's (or the invoice's) currency
    suggestions: tuple[int, ...] = ()  # PO ids, or PO line ids for a line, a person might choose

    @property
    def label(self) -> str:
        return CODES[self.code][0]


@dataclass(frozen=True)
class Link:
    """An allocation: invoice evidence counted against PO evidence."""

    po_id: int
    invoice_id: int
    amount: Decimal
    po_line_id: int | None = None
    invoice_line_id: int | None = None
    qty: Decimal | None = None
    unit_price: Decimal | None = None
    basis: str = "rule"  # rule, or person
    credit: bool = False


@dataclass
class LineFigures:
    line: PoLine
    billed_qty: Decimal = ZERO
    billed_amount: Decimal = ZERO
    qty_known: bool = True

    @property
    def remaining_qty(self) -> Decimal:
        return self.line.qty - self.billed_qty


@dataclass
class Order:
    po: Po
    status: str = ""
    ordered: Decimal = ZERO
    invoiced: Decimal = ZERO  # goods and anything else matched to the PO's own lines, or an invoice's total
    credits: Decimal = ZERO  # credit memos a person linked (negative, as exported)
    charges: dict[str, Decimal] = field(default_factory=dict)  # freight, tax, discount, other: outside its lines
    net: Decimal | None = None  # only when every part is classified
    lines: list[LineFigures] = field(default_factory=list)
    links: list[Link] = field(default_factory=list)
    problems: list[Problem] = field(default_factory=list)
    net_unclear: bool = False

    @property
    def remaining(self) -> Decimal:
        return self.ordered - self.invoiced

    @property
    def invoice_ids(self) -> tuple[int, ...]:
        return tuple(sorted({link.invoice_id for link in self.links}))

    @property
    def blocked(self) -> bool:
        return any(problem.blocking for problem in self.problems)


@dataclass
class Result:
    orders: dict[int, Order]
    unplaced: list[Problem]  # about invoices that reach no PO
    links: list[Link]

    def problems(self) -> list[Problem]:
        return [p for order in self.orders.values() for p in order.problems] + self.unplaced


# ---- small helpers


def item_key(item: str | None) -> str:
    """Items are compared exactly, ignoring only case and spacing: `Bolts:M8` is `bolts:m8`, never `Bolt M8`."""
    return " ".join((item or "").split()).lower()


def reference_keys(reference: str | None) -> list[tuple[str, str]]:
    """The POs an invoice names: a comma- or semicolon-separated list, each as a PO key, in order, once each."""
    seen: dict[str, str] = {}
    for part in re.split(r"[;,]", reference or ""):
        shown = part.strip()
        key = po_key(shown) if shown else ""
        if key and key not in seen:
            seen[key] = shown
    return list(seen.items())


def merged(periods: tuple[tuple[date, date], ...]) -> list[tuple[date, date]]:
    out: list[tuple[date, date]] = []
    for start, end in sorted(p for p in periods if p[0] <= p[1]):
        if out and start <= out[-1][1] + timedelta(days=1):
            out[-1] = (out[-1][0], max(out[-1][1], end))
        else:
            out.append((start, end))
    return out


def covers(periods: tuple[tuple[date, date], ...], start: date, end: date) -> bool:
    """Whether the uploads together cover every day from start to end."""
    return any(a <= start and end <= b for a, b in merged(periods))


def touches(periods: tuple[tuple[date, date], ...], start: date, end: date) -> bool:
    return any(a <= end and start <= b for a, b in merged(periods))


def first_gap(periods: tuple[tuple[date, date], ...], start: date, end: date) -> tuple[date, date] | None:
    day = start
    for a, b in merged(periods):
        if b < day:
            continue
        if a > day:
            return day, min(end, a - timedelta(days=1))
        day = b + timedelta(days=1)
        if day > end:
            return None
    return (day, end) if day <= end else None


def money(value: Decimal | None, currency: str = "") -> str:
    if value is None:
        return "unknown"
    shown = f"{value:,.2f}" if value == value.quantize(Decimal("0.01")) else f"{value.normalize():,f}"
    return f"{shown} {currency}".strip()


def number(value: Decimal | None) -> str:
    return "unknown" if value is None else f"{value.normalize():f}"


def _day(value: date) -> str:
    return value.strftime("%m/%d/%Y")


def _name(invoice: Invoice) -> str:
    return f"{'Credit memo' if invoice.kind == 'credit_memo' else 'Invoice'} {invoice.number}"


# ---- the run


class _Run:
    def __init__(self, book: Book) -> None:
        self.book, self.rules = book, book.rules
        self.orders = {po.id: Order(po) for po in sorted(book.pos, key=lambda p: p.id)}
        self.family = {(po.customer_id, po.key): po for po in book.pos}
        self.holders: dict[str, set[int]] = defaultdict(set)
        for po in book.pos:
            self.holders[po.key].add(po.customer_id)
        self.decided: dict[int, list[Decision]] = defaultdict(list)
        for decision in sorted(book.decisions, key=lambda d: d.id):
            self.decided[decision.invoice_id].append(decision)
        self.invoices = {invoice.id: invoice for invoice in book.invoices}
        self.unplaced: list[Problem] = []
        self.links: list[Link] = []

    def problem(
        self, code: str, message: str, *, po: Po | None = None, invoice: Invoice | None = None,
        level: str | None = None, blocking: bool | None = None, **more: object,
    ) -> None:  # fmt: skip
        _, usual_level, usual_blocking = CODES[code]
        found = Problem(
            code, level or usual_level, usual_blocking if blocking is None else blocking, message,
            po_id=po.id if po else None, invoice_id=invoice.id if invoice else None, **more,  # type: ignore[arg-type]
        )  # fmt: skip
        (self.orders[po.id].problems if po else self.unplaced).append(found)

    def link(self, po: Po, invoice: Invoice, amount: Decimal, **more: object) -> None:
        made = Link(po.id, invoice.id, amount, **more)  # type: ignore[arg-type]
        self.links.append(made)
        self.orders[po.id].links.append(made)

    # -- one invoice

    def invoice(self, invoice: Invoice) -> None:
        reached = self._place(invoice)
        if invoice.review:
            self._flag(invoice, reached)

    def _place(self, invoice: Invoice) -> list[Po]:
        if invoice.customer_id is None:
            self.problem(
                "unmatched_invoice",
                f"{_name(invoice)} names a customer the app does not know ({invoice.customer_name or 'no name'}). "
                "Choose the customer under Sales invoices.",
                invoice=invoice, blocking=False,
            )  # fmt: skip
            return []
        mine = self.decided.get(invoice.id, [])
        for decision in mine:
            if decision.stale and decision.po_id in self.orders:
                self.problem("stale_decision", decision.stale, po=self.orders[decision.po_id].po, invoice=invoice)
        live = [d for d in mine if not d.stale and d.po_id in self.orders]
        refused = {d.po_id for d in live if d.state == "rejected"}
        whole = [d for d in live if d.state == "confirmed" and d.invoice_line_id is None]
        by_line: dict[int, Decision] = {
            d.invoice_line_id: d for d in live if d.state == "confirmed" and d.invoice_line_id is not None
        }
        candidates = [self.orders[d.po_id].po for d in whole] if whole else self._by_reference(invoice, refused)
        for decision in by_line.values():
            po = self.orders[decision.po_id].po
            if po not in candidates:
                candidates.append(po)
        same: list[Po] = []
        for po in candidates:
            if po.currency == invoice.currency:
                same.append(po)
            else:
                self.problem(
                    "currency_mismatch",
                    f"{_name(invoice)} is in {invoice.currency} and PO {po.number} is in {po.currency}. Currencies "
                    "are never converted, so it is not counted.",
                    po=po, invoice=invoice,
                )  # fmt: skip
        if not same:
            return []
        if invoice.kind == "credit_memo":
            self._credit(invoice, same, whole)
        elif invoice.level == "invoice" or not invoice.lines:
            self._whole(invoice, same, whole)
        else:
            self._lines(invoice, same, by_line)
        return same

    def _by_reference(self, invoice: Invoice, refused: set[int]) -> list[Po]:
        customer = invoice.customer_id
        if invoice.qb_reference:
            wanted = invoice.qb_reference.strip().lower()
            hits = [p for p in self.book.pos if p.customer_id == customer and wanted in p.qb_references]
            hits = sorted((p for p in hits if p.id not in refused), key=lambda p: p.id)
            if hits:
                return hits
        keys = reference_keys(invoice.po_reference)
        if not keys:
            self.problem(
                "unmatched_invoice",
                f"{_name(invoice)} carries no PO reference, so it is not matched to any PO. A person chooses the PO.",
                invoice=invoice, blocking=False, suggestions=self._suggest(invoice),
            )  # fmt: skip
            return []
        found: list[Po] = []
        for key, shown in keys:
            po = self.family.get((customer, key))  # type: ignore[arg-type]
            if po is not None and po.id in refused:
                self.problem(
                    "unmatched_invoice",
                    f"{_name(invoice)} names PO {shown}, but a person said it does not belong to that PO.",
                    invoice=invoice, blocking=False, suggestions=self._suggest(invoice),
                )  # fmt: skip
            elif po is not None:
                found.append(po)
            elif self.holders.get(key):
                self.problem(
                    "ambiguous_match",
                    f"{_name(invoice)} names PO {shown}, which is another customer's PO number, not this customer's. "
                    "A person decides.",
                    invoice=invoice, blocking=False,
                )  # fmt: skip
            else:
                self.problem(
                    "unmatched_invoice",
                    f"{_name(invoice)} names PO {shown}, which the app does not have for this customer.",
                    invoice=invoice, blocking=False, suggestions=self._suggest(invoice),
                )  # fmt: skip
        return found

    def _suggest(self, invoice: Invoice) -> tuple[int, ...]:
        """POs a person might choose: never used to match, only offered (DEC-59)."""
        mine = [p for p in self.book.pos if p.customer_id == invoice.customer_id and p.state == "active"
                and p.currency == invoice.currency]  # fmt: skip
        items = {item_key(line.item) for line in invoice.lines if line.kind == "goods" and line.item}
        if items:
            hits = [p for p in mine if items & {item_key(line.item) for line in p.lines}]
        else:
            hits = [p for p in mine if abs(p.ordered - invoice.total) <= self.rules.amount_tolerance]
        return tuple(sorted(p.id for p in hits))[:5]

    def _flag(self, invoice: Invoice, reached: list[Po]) -> None:
        code, text = {
            "duplicate_number": (
                "potential_duplicate",
                f"Two different invoices carry the number {invoice.number}. Only the first one seen is counted; "
                "a person decides which is right.",
            ),
            "changed": (
                "invoice_changed",
                f"{_name(invoice)} was different in a later upload. The figures used are the ones first accepted, "
                "until a person decides.",
            ),
        }.get(invoice.review or "", (
            "invoice_incomplete",
            f"Part of {_name(invoice)} could not be read from the export, so its figures may be incomplete.",
        ))  # fmt: skip
        if not reached:
            self.problem(code, text, invoice=invoice, blocking=False)
        for po in reached:
            self.problem(code, text, po=po, invoice=invoice)

    def _remainder(self, invoice: Invoice, placed: Decimal) -> None:
        """Part of an invoice a person split between POs that no PO received."""
        left = invoice.total - placed
        if placed and abs(left) > self.rules.amount_tolerance:
            self.problem(
                "unmatched_invoice",
                f"Only {money(placed, invoice.currency)} of {_name(invoice)}'s "
                f"{money(invoice.total, invoice.currency)} is linked to a PO; {money(left, invoice.currency)} is not.",
                invoice=invoice, blocking=False, amount=left,
            )  # fmt: skip

    def _credit(self, invoice: Invoice, pos: list[Po], whole: list[Decision]) -> None:
        """A credit memo is never counted by a rule: a person links it, and it never changes what was billed."""
        if not whole:
            for po in pos:
                self.problem(
                    "credit_memo",
                    f"{_name(invoice)} ({money(invoice.total, invoice.currency)}) names this PO. A credit memo never "
                    "changes what was billed by itself: a person links it to the PO, or says it is not for it.",
                    po=po, invoice=invoice, amount=invoice.total,
                )  # fmt: skip
            return
        placed = ZERO
        for decision in whole:
            po = self.orders[decision.po_id].po
            if po not in pos:
                continue
            amount = decision.amount if decision.amount is not None else invoice.total if len(whole) == 1 else None
            if amount is None:
                self.problem(
                    "ambiguous_match",
                    f"{_name(invoice)} was linked to several POs without saying how much belongs to each.",
                    po=po, invoice=invoice,
                )  # fmt: skip
            else:
                self.link(po, invoice, amount, basis="person", credit=True)
                placed += amount
        self._remainder(invoice, placed)

    def _whole(self, invoice: Invoice, pos: list[Po], whole: list[Decision]) -> None:
        """An invoice with only a total: counted against one PO by amount, never split by a rule."""
        if whole:
            placed = ZERO
            for decision in whole:
                po = self.orders[decision.po_id].po
                if po not in pos:
                    continue
                amount = decision.amount if decision.amount is not None else invoice.total if len(whole) == 1 else None
                if amount is None:
                    self.problem(
                        "ambiguous_match",
                        f"{_name(invoice)} was linked to several POs without saying how much belongs to each.",
                        po=po, invoice=invoice,
                    )  # fmt: skip
                else:
                    self.link(po, invoice, amount, basis="person")
                    placed += amount
            self._remainder(invoice, placed)
            return
        if len(pos) == 1:
            self.link(pos[0], invoice, invoice.total)
            return
        numbers = ", ".join(p.number for p in pos)
        for po in pos:
            self.problem(
                "ambiguous_match",
                f"{_name(invoice)} names POs {numbers} and has only a total "
                f"({money(invoice.total, invoice.currency)}), "
                "so it cannot be split between them. A person says how much belongs to each.",
                po=po, invoice=invoice, suggestions=tuple(p.id for p in pos),
            )  # fmt: skip

    def _lines(self, invoice: Invoice, pos: list[Po], by_line: dict[int, Decision]) -> None:
        charges: list[InvoiceLine] = []
        for line in sorted(invoice.lines, key=lambda ln: ln.position):
            decision = by_line.get(line.id)
            if decision is not None:
                po = self.orders[decision.po_id].po
                target = next((pl for pl in po.lines if pl.id == decision.po_line_id), None)
                if target is not None and po in pos:
                    self._count(invoice, line, po, target, "person")
                    continue
            key = item_key(line.item)
            hits = [(po, pl) for po in pos for pl in po.lines if key and item_key(pl.item) == key]
            if len(hits) == 1:
                self._count(invoice, line, hits[0][0], hits[0][1], "rule")
            elif hits:
                where = "; ".join(f"PO {po.number} line {pl.line_no}" for po, pl in hits)
                for po in dict.fromkeys(po for po, _ in hits):
                    self.problem(
                        "ambiguous_match",
                        f"Line {line.position} of {_name(invoice)} bills {line.item}, which is on {where}. "
                        "A person chooses the line.",
                        po=po, invoice=invoice, invoice_line_id=line.id, suggestions=tuple(pl.id for _, pl in hits),
                    )  # fmt: skip
            elif line.kind == "goods":
                for po in pos:
                    self.problem(
                        "unmatched_invoice",
                        f"Line {line.position} of {_name(invoice)} bills {line.item or 'an item with no name'} "
                        f"({money(line.amount, invoice.currency)}), which is not on PO {po.number}.",
                        po=po, invoice=invoice, invoice_line_id=line.id, amount=line.amount,
                    )  # fmt: skip
            else:
                charges.append(line)
        if not charges:
            return
        if len(pos) == 1:
            order = self.orders[pos[0].id]
            for line in charges:
                order.charges[line.kind] = order.charges.get(line.kind, ZERO) + line.amount
                if line.kind == "other":
                    self.problem(
                        "charge_unclassified",
                        f"Line {line.position} of {_name(invoice)} ({line.item or 'no item'}, "
                        f"{money(line.amount, invoice.currency)}) is neither goods nor a known charge, so the net "
                        "amount cannot be worked out.",
                        po=pos[0], invoice=invoice, invoice_line_id=line.id, amount=line.amount,
                    )  # fmt: skip
            return
        total = sum((line.amount for line in charges), ZERO)
        for po in pos:
            self.orders[po.id].net_unclear = True
        self.problem(
            "charges_unassigned",
            f"{_name(invoice)} has {money(total, invoice.currency)} of charges ("
            f"{', '.join(sorted({line.kind for line in charges}))}) and names POs {', '.join(p.number for p in pos)}; "
            "they are not split between them.",
            invoice=invoice, amount=total,
        )  # fmt: skip

    def _count(self, invoice: Invoice, line: InvoiceLine, po: Po, target: PoLine, basis: str) -> None:
        self.link(
            po, invoice, line.amount, po_line_id=target.id, invoice_line_id=line.id, qty=line.qty,
            unit_price=line.unit_price, basis=basis,
        )  # fmt: skip
        if line.qty is None:
            self.problem(
                "invoice_incomplete",
                f"Line {line.position} of {_name(invoice)} bills {line.item} with no quantity, so the quantity billed "
                "cannot be compared.",
                po=po, invoice=invoice, invoice_line_id=line.id,
            )  # fmt: skip

    # -- one PO

    def settle(self, order: Order) -> None:
        po, rules = order.po, self.rules
        tolerance, qty_tolerance = rules.amount_tolerance, rules.quantity_tolerance
        goods = [link for link in order.links if not link.credit]
        order.ordered = po.ordered
        order.invoiced = sum((link.amount for link in goods), ZERO)
        order.credits = sum((link.amount for link in order.links if link.credit), ZERO)
        figures = {line.id: LineFigures(line) for line in sorted(po.lines, key=lambda ln: (ln.line_no, ln.id))}
        order.lines = list(figures.values())
        explained = ZERO
        for link in goods:
            if link.po_line_id is None:
                continue
            fig = figures[link.po_line_id]
            fig.billed_amount += link.amount
            if link.qty is None:
                fig.qty_known = False
            else:
                fig.billed_qty += link.qty
            if link.unit_price is None:
                continue
            difference = link.unit_price - fig.line.unit_price
            variance = difference * link.qty if link.qty is not None else None
            if variance is not None and variance > 0:
                explained += variance  # within the tolerance or not, it is accounted for in the total
            if abs(difference) > tolerance:
                invoice = self.invoices[link.invoice_id]
                self.problem(
                    "price_mismatch",
                    f"{_name(invoice)} bills {fig.line.item} at {money(link.unit_price)}; the PO's price is "
                    f"{money(fig.line.unit_price)}"
                    + (_difference(variance, link.qty, po.currency) if variance is not None else "")
                    + "."
                    + _revision_note(invoice, po),
                    po=po, invoice=invoice, po_line_id=fig.line.id, invoice_line_id=link.invoice_line_id,
                    amount=variance,
                )  # fmt: skip
        line_level = all(link.po_line_id is not None for link in goods)
        for fig in order.lines:
            if fig.qty_known and fig.billed_qty > fig.line.qty:
                explained += (fig.billed_qty - fig.line.qty) * fig.line.unit_price  # accounted for, as above
            if fig.qty_known and fig.billed_qty > fig.line.qty + qty_tolerance:
                extra = fig.billed_qty - fig.line.qty
                variance = extra * fig.line.unit_price
                self.problem(
                    "quantity_mismatch",
                    f"Line {fig.line.line_no} ({fig.line.item}): {number(fig.billed_qty)} billed against "
                    f"{number(fig.line.qty)} ordered, {number(extra)} more ({money(variance, po.currency)}).",
                    po=po, po_line_id=fig.line.id, amount=variance,
                )  # fmt: skip
        excess = order.invoiced - order.ordered
        if goods and excess - explained > tolerance:
            self.problem(
                "amount_mismatch",
                f"{money(order.invoiced, po.currency)} invoiced against {money(order.ordered, po.currency)} ordered: "
                f"{money(excess - explained, po.currency)} more"
                + (" than the lines account for." if explained else ".")
                + ("" if line_level else " This rests on invoice totals only."),
                po=po, level=CONFIRMED if line_level else PRELIMINARY, amount=excess - explained,
            )  # fmt: skip
        partial = bool(goods) and (
            any(f.billed_qty < f.line.qty - qty_tolerance for f in order.lines if f.qty_known)
            if line_level
            else order.remaining > tolerance
        )
        if partial:
            self.problem(
                "partially_invoiced",
                f"{money(order.invoiced, po.currency)} of {money(order.ordered, po.currency)} invoiced so far; "
                f"{money(order.remaining, po.currency)} apparently remains.",
                po=po, amount=order.remaining,
            )  # fmt: skip
        named = any(p.invoice_id is not None for p in order.problems)  # an invoice reaches it but is not counted
        if not goods and not named and po.state == "active":
            self._unbilled(order)
        if po.pending_revision is not None:
            self.problem(
                "revised_po",
                f"Revision {po.pending_revision} of this PO is waiting for approval. Revision {po.revision}'s terms "
                "are used until a person decides.",
                po=po,
            )  # fmt: skip
        self._same_content(order)
        if po.state in ("closed", "cancelled"):
            order.problems = [p for p in order.problems if p.code not in MISSING_BILLING]
        waiting_credit = any(p.code == "credit_memo" for p in order.problems)
        if order.net_unclear or "other" in order.charges or waiting_credit:
            order.net = None
        else:
            order.net = order.invoiced + sum(order.charges.values(), ZERO) + order.credits
        order.status = self._status(order, goods, partial, line_level)

    def _status(self, order: Order, goods: list[Link], partial: bool, line_level: bool) -> str:
        po = order.po
        if po.state in ("closed", "cancelled"):
            return po.state
        if order.blocked:
            return "needs_review"
        if any(p.code in ("quantity_mismatch", "amount_mismatch") for p in order.problems):
            return "over_invoiced"
        if goods:
            if partial:
                return "partially_invoiced"
            return "matched" if line_level else "matched_by_amount"
        return "no_invoice" if touches(self.book.periods, po.since, self.rules.today) else "not_covered"

    def _unbilled(self, order: Order) -> None:
        """No invoice for this PO. Said only of a PO old enough, and "possibly unbilled" only when the uploads
        cover the days in which it should have been invoiced (DEC-56, REQ-CO-12)."""
        po, rules = order.po, self.rules
        age = (rules.today - po.since).days
        if age < rules.unbilled_days:
            return
        end = po.since + timedelta(days=rules.unbilled_days)
        if covers(self.book.periods, po.since, end):
            self.problem(
                "possibly_unbilled",
f"No invoice for this PO in the uploaded files, which cover {_day(po.since)} to {_day(end)}, "
                f"{self.rules.unbilled_days} days from its date.",
                po=po, amount=po.ordered,
            )  # fmt: skip
            return
        gap = first_gap(self.book.periods, po.since, end)
        where = f"{_day(gap[0])} to {_day(gap[1])}" if gap else "part of that time"
        self.problem(
            "not_found",
            f"No invoice for this PO in the uploaded files. They do not cover {where}, so it may have been invoiced "
            "in a period not uploaded yet.",
            po=po,
        )  # fmt: skip

    def _same_content(self, order: Order) -> None:
        """Two invoices with different numbers, the same date and the same lines for one PO: both are counted (they
        are two invoices), and a person is told in case one was entered twice."""
        groups: dict[tuple[object, ...], list[int]] = defaultdict(list)
        mine: dict[int, list[Link]] = defaultdict(list)
        for link in order.links:
            if not link.credit:
                mine[link.invoice_id].append(link)
        for invoice_id, links in sorted(mine.items()):
            invoice = self.invoices[invoice_id]
            shape = tuple(sorted((k.po_line_id or 0, str(k.qty), str(k.unit_price), str(k.amount)) for k in links))
            groups[(invoice.date, shape)].append(invoice_id)
        for ids in groups.values():
            if len(ids) > 1:
                numbers = ", ".join(self.invoices[i].number for i in ids)
                self.problem(
                    "potential_duplicate",
                    f"Invoices {numbers} have the same date and bill the same for this PO. Both are counted; if one "
                    "was entered twice, it is corrected in QuickBooks.",
                    po=order.po, level=PRELIMINARY, blocking=False,
                )  # fmt: skip


def _difference(variance: Decimal, qty: Decimal | None, currency: str) -> str:
    return f", a difference of {money(variance, currency)} on {number(qty)}"


def _revision_note(invoice: Invoice, po: Po) -> str:
    """Revision history as evidence: a difference may come from terms that changed after the invoice was issued."""
    if po.revision and po.revised_on and invoice.date and invoice.date < po.revised_on:
        return f" The invoice is dated before revision {po.revision} was approved."
    return ""


def reconcile(book: Book) -> Result:
    run = _Run(book)
    for invoice in sorted(book.invoices, key=lambda i: i.id):
        run.invoice(invoice)
    for order in run.orders.values():
        run.settle(order)
    return Result(run.orders, run.unplaced, run.links)
