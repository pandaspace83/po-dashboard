"""The status engine (PD-1.3): every row of ARCHITECTURE §5 and the owner's rulings in DEC-36."""

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal as D
from itertools import product
from pathlib import Path

import pytest
from sqlalchemy import Engine, event, select
from sqlalchemy.orm import Session

from po_dashboard.config import Settings
from po_dashboard.db import ledger as L
from po_dashboard.db.migrate import upgrade
from po_dashboard.ledger.status import LineFacts, LineStatus, POStatus, line_state, line_states, po_state, po_states

SENT, ACKED, PART, SHIPPED, CLOSED = (
    LineStatus.SENT,
    LineStatus.ACKNOWLEDGED,
    LineStatus.PARTIALLY_SHIPPED,
    LineStatus.SHIPPED,
    LineStatus.CLOSED,
)


def state(ordered="100", confirmed=None, acknowledged=None, shipped="0", invoiced="0", closed=None):  # noqa: ANN001, ANN201
    """Build a line. A stated quantity implies an acknowledgement, unless the test says otherwise."""
    return line_state(
        LineFacts(
            ordered=D(ordered),
            confirmed=None if confirmed is None else D(confirmed),
            acknowledged=confirmed is not None if acknowledged is None else acknowledged,
            shipped=D(shipped),
            invoiced=D(invoiced),
            closed_reason=closed,
        )
    )


# ---- each row of the table


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        pytest.param({}, SENT, id="sent: nothing has happened"),
        pytest.param({"acknowledged": True}, ACKED, id="acknowledged: an ack with no quantity"),
        pytest.param({"confirmed": "100"}, ACKED, id="acknowledged: confirmed in full, nothing shipped"),
        pytest.param({"confirmed": "100", "shipped": "40"}, PART, id="partially shipped: some of the target"),
        pytest.param({"shipped": "40"}, PART, id="partially shipped: no ack, target is the ordered quantity"),
        pytest.param({"confirmed": "100", "shipped": "100"}, SHIPPED, id="shipped: all of the target"),
        pytest.param({"confirmed": "100", "shipped": "120"}, SHIPPED, id="shipped: more than the target"),
        pytest.param({"invoiced": "100"}, CLOSED, id="closed: invoiced reaches ordered"),
        pytest.param({"invoiced": "120"}, CLOSED, id="closed: invoiced passes ordered"),
        pytest.param({"closed": "received"}, CLOSED, id="closed by hand: received"),
        pytest.param({"closed": "short"}, CLOSED, id="closed by hand: short"),
        pytest.param({"closed": "cancelled"}, LineStatus.CANCELLED, id="cancelled by hand (DEC-39)"),
    ],
)
def test_each_row_of_the_table(line: dict[str, str], expected: LineStatus) -> None:
    assert state(**line).status is expected


def test_closed_wins_over_every_other_row() -> None:
    assert state(confirmed="100", shipped="100", invoiced="100").status is CLOSED
    assert state(confirmed="100", shipped="40", closed="short").status is CLOSED


# ---- the owner's rulings (DEC-36)


def test_a_shipping_notice_moves_a_line_that_was_never_acknowledged() -> None:
    assert state(shipped="100").status is SHIPPED


def test_a_full_invoice_closes_a_line_with_no_shipping_notice() -> None:
    """The owner: the invoice arrives once the PO is completely received (DEC-14)."""
    assert state(invoiced="100").status is CLOSED


def test_a_partial_invoice_with_no_shipping_notice_counts_as_partly_shipped() -> None:
    line = state(invoiced="50")
    assert line.status is PART and line.open_qty == D("50")


def test_the_larger_of_shipped_and_invoiced_is_what_moved() -> None:
    assert state(shipped="30", invoiced="50").status is PART
    assert state(shipped="100", invoiced="50").status is SHIPPED


def test_a_line_confirmed_at_zero_stays_open_and_tells_the_purchaser() -> None:
    line = state(confirmed="0")
    assert line.status is ACKED and line.open_qty == D("100") and line.po_needs_update


def test_a_line_confirmed_short_never_closes_by_itself() -> None:
    line = state(confirmed="60", shipped="60", invoiced="60")
    assert line.status is SHIPPED and line.open_qty == D("40") and line.po_needs_update


def test_closing_a_short_line_by_hand_settles_it() -> None:
    line = state(confirmed="60", shipped="60", invoiced="60", closed="short")
    assert line.status is CLOSED and line.open_qty == 0 and not line.po_needs_update


def test_a_cancelled_zero_confirmation_settles_it() -> None:
    line = state(confirmed="0", closed="cancelled")
    assert line.status is LineStatus.CANCELLED and not line.po_needs_update


def test_the_purchaser_is_only_told_when_the_quantity_differs() -> None:
    assert not state().po_needs_update
    assert not state(acknowledged=True).po_needs_update  # the vendor stated no quantity: nothing differs
    assert not state(confirmed="100").po_needs_update
    assert state(confirmed="60").po_needs_update


def test_a_vendor_confirming_more_than_ordered_does_not_raise_the_bar() -> None:
    line = state(confirmed="120", shipped="100")
    assert line.status is SHIPPED and line.po_needs_update
    assert state(confirmed="120", invoiced="100").status is CLOSED


# ---- exact numbers


def test_the_smallest_step_short_is_still_open() -> None:
    line = state(ordered="10", invoiced="9.9999")
    assert line.status is PART and line.open_qty == D("0.0001")
    assert state(ordered="10", shipped="9.9999").status is PART


def test_open_quantity_is_never_negative() -> None:
    assert state(invoiced="120").open_qty == 0


def test_rules_hold_across_every_combination() -> None:
    """Whatever the mix of facts, the status and the open quantity must agree with each other."""
    quantities = ("0", "60", "100", "120")
    combinations = product((None, *quantities), (True, False), quantities, quantities, (None, "short"))
    for confirmed, acknowledged, shipped, invoiced, closed in combinations:
        if confirmed is not None and not acknowledged:
            continue  # a stated quantity comes from an acknowledgement
        line = state("100", confirmed, acknowledged, shipped, invoiced, closed)
        moved = max(D(shipped), D(invoiced))
        assert (line.status is CLOSED) == (line.open_qty == 0)
        if line.status is SENT:
            assert not acknowledged and moved == 0
        if line.status is ACKED:
            assert acknowledged and moved == 0
        if line.status in (PART, SHIPPED):
            assert moved > 0
        if line.po_needs_update:
            assert line.status is not CLOSED and D(confirmed) != D("100")


# ---- a purchase order


def test_a_po_with_no_lines_is_open_not_closed() -> None:
    assert po_state([]).status is POStatus.OPEN


def test_a_po_closes_only_when_every_line_has() -> None:
    assert po_state([state(invoiced="100"), state(closed="cancelled")]).status is POStatus.CLOSED
    still_open = po_state([state(invoiced="100"), state()])
    assert still_open.status is POStatus.OPEN and still_open.open_lines == 1


def test_a_po_shows_partially_invoiced_only_while_it_is_open() -> None:
    assert po_state([state(invoiced="100"), state()]).partially_invoiced
    assert po_state([state(invoiced="50")]).partially_invoiced
    assert not po_state([state(shipped="100")]).partially_invoiced
    assert not po_state([state(invoiced="100")]).partially_invoiced


# ---- from the database


@pytest.fixture
def engine(tmp_path: Path) -> Iterator[Engine]:
    eng = upgrade(Settings(data_dir=tmp_path / "data"))
    yield eng
    eng.dispose()


@pytest.fixture
def db(engine: Engine) -> Iterator[Session]:
    with Session(engine) as session:
        yield session


def new_po(db: Session, number: str = "PO-1", lines: tuple[str, ...] = ("100",)) -> L.PurchaseOrder:
    vendor = L.Vendor(name=f"Vendor of {number}")
    db.add(vendor)
    db.flush()
    order = L.PurchaseOrder(po_number=number, vendor_id=vendor.id, currency="USD")
    db.add(order)
    db.flush()
    for no, ordered in enumerate(lines, start=1):
        db.add(L.POLine(po_id=order.id, line_no=no, item="Bolt", ordered_qty=D(ordered), unit="EA", unit_price=D(1)))
    db.flush()
    return order


def line_ids(db: Session, order: L.PurchaseOrder) -> list[int]:
    return list(db.scalars(select(L.POLine.id).where(L.POLine.po_id == order.id).order_by(L.POLine.line_no)))


def ack(db: Session, order: L.PurchaseOrder, line_id: int, qty: str | None, at: datetime | None = None) -> None:
    row = L.Acknowledgement(po_id=order.id)
    if at is not None:
        row.accepted_at = at
    db.add(row)
    db.flush()
    db.add(L.AckLine(ack_id=row.id, po_line_id=line_id, confirmed_qty=None if qty is None else D(qty)))
    db.flush()


def ship(db: Session, order: L.PurchaseOrder, line_id: int, qty: str) -> None:
    row = L.Shipment(po_id=order.id)
    db.add(row)
    db.flush()
    db.add(L.ShipmentLine(shipment_id=row.id, po_line_id=line_id, qty=D(qty)))
    db.flush()


def invoice(db: Session, order: L.PurchaseOrder, number: str, *lines: tuple[int, str]) -> None:
    row = L.Invoice(po_id=order.id, invoice_number=number)
    db.add(row)
    db.flush()
    for line_id, qty in lines:
        db.add(L.InvoiceLine(invoice_id=row.id, po_line_id=line_id, qty=D(qty), price=D(1)))
    db.flush()


def test_a_new_line_is_sent(db: Session) -> None:
    order = new_po(db)
    only = line_ids(db, order)[0]
    assert line_states(db)[only].status is SENT


def test_the_latest_accepted_ack_is_the_one_that_counts(db: Session) -> None:
    order = new_po(db)
    only = line_ids(db, order)[0]
    ack(db, order, only, "60")
    ack(db, order, only, "100")
    line = line_states(db)[only]
    assert line.facts.confirmed == D("100") and not line.po_needs_update  # the shortfall clears itself


def test_latest_means_accepted_last_not_inserted_last(db: Session) -> None:
    order = new_po(db)
    only = line_ids(db, order)[0]
    now = datetime.now(UTC)
    ack(db, order, only, "60", at=now)
    ack(db, order, only, "80", at=now - timedelta(days=1))
    assert line_states(db)[only].facts.confirmed == D("60")


def test_a_later_ack_confirming_zero_replaces_an_earlier_quantity(db: Session) -> None:
    order = new_po(db)
    only = line_ids(db, order)[0]
    ack(db, order, only, "60")
    ack(db, order, only, "0")
    assert line_states(db)[only].facts.confirmed == 0


def test_a_later_ack_with_no_quantity_keeps_the_earlier_one(db: Session) -> None:
    order = new_po(db)
    only = line_ids(db, order)[0]
    ack(db, order, only, "60")
    ack(db, order, only, None)  # for example, it only moves the date
    assert line_states(db)[only].facts.confirmed == D("60")


def test_an_ack_with_no_quantity_still_acknowledges(db: Session) -> None:
    order = new_po(db)
    only = line_ids(db, order)[0]
    ack(db, order, only, None)
    assert line_states(db)[only].status is ACKED


def test_shipments_add_up_exactly(db: Session) -> None:
    order = new_po(db, lines=("0.3",))
    only = line_ids(db, order)[0]
    ship(db, order, only, "0.1")
    assert line_states(db)[only].status is PART
    ship(db, order, only, "0.2")
    line = line_states(db)[only]
    assert line.status is SHIPPED and line.facts.shipped == D("0.3")


def test_a_partial_invoice_closes_only_what_it_covers(db: Session) -> None:
    """REQ-INV-02: the other lines, and the unmet part of a line, stay open."""
    order = new_po(db, lines=("100", "100", "100"))
    full, part, untouched = line_ids(db, order)
    invoice(db, order, "INV-1", (full, "100"), (part, "40"))
    states = line_states(db)
    assert states[full].status is CLOSED and states[full].open_qty == 0
    assert states[part].status is PART and states[part].open_qty == D("60")
    assert states[untouched].status is SENT and states[untouched].open_qty == D("100")
    summary = po_states(db)[order.id]
    assert summary.status is POStatus.OPEN and summary.partially_invoiced and summary.open_lines == 2


def test_two_invoices_together_close_a_line_and_its_po(db: Session) -> None:
    order = new_po(db)
    only = line_ids(db, order)[0]
    invoice(db, order, "INV-1", (only, "40"))
    assert po_states(db)[order.id].status is POStatus.OPEN
    invoice(db, order, "INV-2", (only, "60"))
    assert line_states(db)[only].status is CLOSED
    assert po_states(db)[order.id].status is POStatus.CLOSED


def test_a_whole_life_from_sent_to_closed(db: Session) -> None:
    order = new_po(db)
    only = line_ids(db, order)[0]
    seen = [line_states(db)[only].status]
    ack(db, order, only, "100")
    seen.append(line_states(db)[only].status)
    ship(db, order, only, "40")
    seen.append(line_states(db)[only].status)
    ship(db, order, only, "60")
    seen.append(line_states(db)[only].status)
    invoice(db, order, "INV-1", (only, "100"))
    seen.append(line_states(db)[only].status)
    assert seen == [SENT, ACKED, PART, SHIPPED, CLOSED]


def test_closing_by_hand_in_the_database_closes_the_line_and_po(db: Session) -> None:
    order = new_po(db)
    only = line_ids(db, order)[0]
    ack(db, order, only, "60")
    row = db.get(L.POLine, only)
    assert row is not None
    row.closed_reason, row.closed_at, row.close_note = "short", datetime.now(UTC), "Vendor can only supply 60"
    db.flush()
    assert line_states(db)[only].status is CLOSED
    assert po_states(db)[order.id].status is POStatus.CLOSED


def test_one_pos_facts_never_touch_another(db: Session) -> None:
    first, second = new_po(db, "PO-1"), new_po(db, "PO-2")
    one, other = line_ids(db, first)[0], line_ids(db, second)[0]
    ship(db, first, one, "100")
    ack(db, first, one, "100")
    invoice(db, second, "INV-1", (other, "40"))
    assert set(line_states(db, first.id)) == {one}
    assert set(line_states(db, second.id)) == {other}
    assert line_states(db, first.id)[one].status is SHIPPED
    assert line_states(db, second.id)[other].facts.invoiced == D("40")
    assert line_states(db, second.id)[other].facts.shipped == 0


def test_every_po_is_listed_even_with_no_lines(db: Session) -> None:
    order = new_po(db, lines=())
    summary = po_states(db)[order.id]
    assert summary.status is POStatus.OPEN and summary.open_lines == 0


def test_the_number_of_queries_does_not_grow_with_the_lines(db: Session, engine: Engine) -> None:
    order = new_po(db, lines=("10",) * 25)
    for line_id in line_ids(db, order):
        ack(db, order, line_id, "10")
        ship(db, order, line_id, "5")
    db.commit()
    count = {"queries": 0}

    def counted(*_: object) -> None:
        count["queries"] += 1

    event.listen(engine, "before_cursor_execute", counted)
    try:
        assert len(line_states(db)) == 25
    finally:
        event.remove(engine, "before_cursor_execute", counted)
    assert count["queries"] <= 4
