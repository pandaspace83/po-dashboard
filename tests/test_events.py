"""Recording acknowledgements, shipments and invoices by hand (PD-1.4), and the status that follows."""

from collections.abc import Iterator
from datetime import date
from decimal import Decimal as D
from pathlib import Path

import pytest
from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session

from po_dashboard.config import Settings
from po_dashboard.db import ledger as L
from po_dashboard.db.migrate import upgrade
from po_dashboard.db.models import AppUser, AuditLog
from po_dashboard.ledger import events as E
from po_dashboard.ledger import orders, vendors
from po_dashboard.ledger.errors import LedgerError
from po_dashboard.ledger.status import LineStatus, line_states, po_states


@pytest.fixture
def engine(tmp_path: Path) -> Iterator[Engine]:
    eng = upgrade(Settings(data_dir=tmp_path / "data"))
    yield eng
    eng.dispose()


@pytest.fixture
def db(engine: Engine) -> Iterator[Session]:
    with Session(engine) as session:
        yield session


@pytest.fixture
def order(db: Session) -> L.PurchaseOrder:
    vendor = vendors.create_vendor(db, name="Acme")
    lines = [
        orders.parse_line("Line 1", item="Bolt", quantity="100", unit="EA", unit_price="2.50", line_no=1),
        orders.parse_line("Line 2", item="Nut", quantity="50", unit="EA", unit_price="1", line_no=2),
    ]
    return orders.create_po(
        db, po_number="PO-1", vendor_id=vendor.id, currency="USD", date_sent=None, buyer=None, lines=lines
    )


def ids(db: Session, order: L.PurchaseOrder) -> tuple[int, int]:
    first, second = db.scalars(select(L.POLine.id).where(L.POLine.po_id == order.id).order_by(L.POLine.line_no))
    return first, second


def only_line(db: Session, order: L.PurchaseOrder) -> int:
    return db.scalars(select(L.POLine.id).where(L.POLine.po_id == order.id)).one()


def status(db: Session, line_id: int) -> LineStatus:
    return line_states(db)[line_id].status


def person(db: Session) -> AppUser:
    user = AppUser(email="buyer@example.com", password_hash="x", role="buyer")
    db.add(user)
    db.flush()
    return user


# ---- reading what was typed


@pytest.mark.parametrize("empty", [("", "", ""), (" ", "  ", "")])
def test_an_empty_ack_row_is_skipped(empty: tuple[str, str, str]) -> None:
    assert E.parse_ack_line("Line 1", 1, *empty) is None


def test_an_ack_row_may_give_any_part_of_the_answer() -> None:
    assert E.parse_ack_line("Line 1", 1, "60", "", "") == E.AckInput(1, D("60"))
    assert E.parse_ack_line("Line 1", 1, "", "2.75", "") == E.AckInput(1, None, D("2.75"))
    assert E.parse_ack_line("Line 1", 1, "", "", "2026-12-01") == E.AckInput(1, None, None, date(2026, 12, 1))
    assert E.parse_ack_line("Line 1", 1, "0", "", "").confirmed_qty == 0  # type: ignore[union-attr]  # cannot supply


@pytest.mark.parametrize(
    ("qty", "price", "promised", "message"),
    [
        ("-1", "", "", "Confirmed quantity cannot be negative"),
        ("abc", "", "", "not a number"),
        ("1.00001", "", "", "nothing is rounded"),
        ("", "-2", "", "Confirmed price cannot be negative"),
        ("", "", "soon", "not a date"),
    ],
)
def test_a_bad_ack_value_names_the_line_and_field(qty: str, price: str, promised: str, message: str) -> None:
    with pytest.raises(LedgerError, match=message) as caught:
        E.parse_ack_line("Line 3", 1, qty, price, promised)
    assert str(caught.value).startswith("Line 3: ")


def test_shipping_and_invoice_rows() -> None:
    assert E.parse_ship_line("Line 1", 1, "", "") is None
    assert E.parse_ship_line("Line 1", 1, "40", "2026-12-10") == E.ShipLineInput(1, D("40"), date(2026, 12, 10))
    with pytest.raises(LedgerError, match="Quantity is needed"):
        E.parse_ship_line("Line 1", 1, "", "2026-12-10")  # an arrival date with no quantity
    with pytest.raises(LedgerError, match="more than zero"):
        E.parse_ship_line("Line 1", 1, "0", "")
    assert E.parse_invoice_line("Line 1", 1, "", "2.50") is None  # a pre-filled price alone is not a row
    assert E.parse_invoice_line("Line 1", 1, "40", "2.5") == E.InvoiceLineInput(1, D("40"), D("2.5"))
    with pytest.raises(LedgerError, match="Price is needed"):
        E.parse_invoice_line("Line 1", 1, "40", "")  # the price is never guessed from the PO
    with pytest.raises(LedgerError, match="Price cannot be negative"):
        E.parse_invoice_line("Line 1", 1, "40", "-1")


# ---- an acknowledgement


def test_an_ack_moves_the_line_and_is_accepted_by_the_person(db: Session, order: L.PurchaseOrder) -> None:
    first, second = ids(db, order)
    who = person(db)
    ack = E.record_ack(db, order, [E.AckInput(first, D("100"), None, date(2026, 12, 1))], actor=who)
    assert ack.accepted_by == who.id and ack.accepted_at is not None
    assert status(db, first) is LineStatus.ACKNOWLEDGED and status(db, second) is LineStatus.SENT


def test_an_ack_with_no_details_acknowledges_every_listed_line(db: Session, order: L.PurchaseOrder) -> None:
    first, second = ids(db, order)
    E.record_ack(db, order, [], without_details=True)
    assert status(db, first) is LineStatus.ACKNOWLEDGED and status(db, second) is LineStatus.ACKNOWLEDGED
    assert line_states(db)[first].facts.confirmed is None  # acknowledged, but no quantity stated


def test_an_ack_needs_at_least_one_line_and_only_this_pos_lines(db: Session, order: L.PurchaseOrder) -> None:
    first, _ = ids(db, order)
    with pytest.raises(LedgerError, match="at least one line"):
        E.record_ack(db, order, [])
    other = orders.create_po(
        db, po_number="PO-2", vendor_id=order.vendor_id, currency="USD", date_sent=None, buyer=None,
        lines=[orders.parse_line("Line 1", item="X", quantity="1", unit="EA", unit_price="1")],
    )  # fmt: skip
    foreign = only_line(db, other)
    with pytest.raises(LedgerError, match="does not belong"):
        E.record_ack(db, order, [E.AckInput(foreign, D("1"))])
    with pytest.raises(LedgerError, match="appears twice"):
        E.record_ack(db, order, [E.AckInput(first, D("1")), E.AckInput(first, D("2"))])
    assert db.scalar(select(func.count()).select_from(L.Acknowledgement)) == 0  # nothing half-written


def test_a_short_confirmation_flags_the_purchaser_and_a_later_full_one_clears_it(
    db: Session, order: L.PurchaseOrder
) -> None:
    first, _ = ids(db, order)
    E.record_ack(db, order, [E.AckInput(first, D("60"))])
    assert line_states(db)[first].po_needs_update
    E.record_ack(db, order, [E.AckInput(first, D("100"))])
    assert not line_states(db)[first].po_needs_update


def test_promised_dates_are_kept_and_a_later_date_is_a_slip(db: Session, order: L.PurchaseOrder) -> None:
    first, second = ids(db, order)
    for promised in ("2026-12-01", "2026-12-15", "2026-12-10"):
        E.record_ack(db, order, [E.AckInput(first, None, None, date.fromisoformat(promised))])
    steps = E.promised_history(db, order)[first]
    assert [(s.promised.isoformat(), s.slipped) for s in steps] == [
        ("2026-12-01", False), ("2026-12-15", True), ("2026-12-10", False),
    ]  # fmt: skip
    assert second not in E.promised_history(db, order)


def test_a_different_price_is_flagged_next_to_the_line(db: Session, order: L.PurchaseOrder) -> None:
    first, second = ids(db, order)
    E.record_ack(db, order, [E.AckInput(first, None, D("2.75")), E.AckInput(second, None, D("1"))])
    notes = E.line_warnings(db, order)
    assert notes[first] == ["The vendor's price 2.75 differs from the PO price 2.5."]
    assert second not in notes  # the same price is not a difference


# ---- a shipment


def test_a_partial_then_full_shipment_moves_the_line(db: Session, order: L.PurchaseOrder) -> None:
    first, _ = ids(db, order)
    E.record_ack(db, order, [E.AckInput(first, D("100"))])
    E.record_shipment(
        db, order, [E.ShipLineInput(first, D("40"))], carrier="UPS", tracking_number="1Z999", eta="2026-12-10"
    )
    assert status(db, first) is LineStatus.PARTIALLY_SHIPPED
    E.record_shipment(db, order, [E.ShipLineInput(first, D("60"), date(2026, 12, 20))])
    assert status(db, first) is LineStatus.SHIPPED


def test_a_shipment_covers_only_the_lines_it_lists_and_keeps_its_dates(db: Session, order: L.PurchaseOrder) -> None:
    first, second = ids(db, order)
    ship = E.record_shipment(db, order, [E.ShipLineInput(first, D("100"), date(2026, 12, 20))],
                             shipped_at="2026-12-01", eta="2026-12-10")  # fmt: skip
    assert (ship.shipped_at, ship.eta) == (date(2026, 12, 1), date(2026, 12, 10))
    assert db.scalars(select(L.ShipmentLine.eta)).one() == date(2026, 12, 20)  # the line's own date
    assert status(db, second) is LineStatus.SENT


def test_shipment_header_and_lines_are_checked(db: Session, order: L.PurchaseOrder) -> None:
    first, _ = ids(db, order)
    line = [E.ShipLineInput(first, D("1"))]
    with pytest.raises(LedgerError, match="Expected arrival"):
        E.record_shipment(db, order, line, eta="tomorrow")
    with pytest.raises(LedgerError, match="longer than 100"):
        E.record_shipment(db, order, line, carrier="x" * 101)
    with pytest.raises(LedgerError, match="at least one line"):
        E.record_shipment(db, order, [])
    assert db.scalar(select(func.count()).select_from(L.Shipment)) == 0


# ---- an invoice


def test_a_partial_invoice_closes_only_what_it_covers(db: Session, order: L.PurchaseOrder) -> None:
    first, second = ids(db, order)
    E.record_invoice(db, order, [E.InvoiceLineInput(first, D("100"), D("2.5"))], invoice_number="INV-1", total="250")
    assert status(db, first) is LineStatus.CLOSED and status(db, second) is LineStatus.SENT
    assert po_states(db)[order.id].partially_invoiced


def test_an_invoice_number_is_unique_per_po_in_any_case(db: Session, order: L.PurchaseOrder) -> None:
    first, _ = ids(db, order)
    line = [E.InvoiceLineInput(first, D("10"), D("2.5"))]
    E.record_invoice(db, order, line, invoice_number="INV-1")
    with pytest.raises(LedgerError, match="already recorded"):
        E.record_invoice(db, order, line, invoice_number="inv-1")


def test_an_invoice_needs_a_number_a_line_and_a_valid_total(db: Session, order: L.PurchaseOrder) -> None:
    first, _ = ids(db, order)
    line = [E.InvoiceLineInput(first, D("10"), D("2.5"))]
    with pytest.raises(LedgerError, match="Invoice number is needed"):
        E.record_invoice(db, order, line, invoice_number=" ")
    with pytest.raises(LedgerError, match="at least one line"):
        E.record_invoice(db, order, [], invoice_number="INV-1")
    with pytest.raises(LedgerError, match="Total"):
        E.record_invoice(db, order, line, invoice_number="INV-1", total="lots")
    with pytest.raises(LedgerError, match="Invoice date"):
        E.record_invoice(db, order, line, invoice_number="INV-1", invoice_date="12/31/2026")
    assert db.scalar(select(func.count()).select_from(L.Invoice)) == 0


def test_over_billing_is_flagged_but_still_recorded(db: Session, order: L.PurchaseOrder) -> None:
    """The vendor may really have billed more; the person decides, and the page says so (REQ-INV-05)."""
    first, _ = ids(db, order)
    E.record_invoice(db, order, [E.InvoiceLineInput(first, D("120"), D("3"))], invoice_number="INV-1")
    notes = E.line_warnings(db, order)[first]
    assert "Invoiced 120, more than the 100 ordered." in notes
    assert "Invoiced at 3, above the PO price 2.5." in notes
    assert status(db, first) is LineStatus.CLOSED


def test_shipping_more_than_ordered_is_flagged(db: Session, order: L.PurchaseOrder) -> None:
    first, _ = ids(db, order)
    E.record_shipment(db, order, [E.ShipLineInput(first, D("101"))])
    assert E.line_warnings(db, order)[first] == ["Shipped 101, more than the 100 ordered."]


# ---- history and taking an entry back


def test_the_history_lists_every_entry_with_who_and_what(db: Session, order: L.PurchaseOrder) -> None:
    first, _ = ids(db, order)
    who = person(db)
    E.record_ack(db, order, [E.AckInput(first, D("60"), D("2.5"), date(2026, 12, 1))], actor=who)
    E.record_shipment(
        db, order, [E.ShipLineInput(first, D("60"))], carrier="UPS", tracking_number="1Z9", eta="2026-12-05", actor=who
    )
    E.record_invoice(
        db, order, [E.InvoiceLineInput(first, D("60"), D("2.5"))], invoice_number="INV-7", total="150", actor=who
    )
    shown = E.entries(db, order)
    assert [view.kind for view in shown] == ["ack", "shipment", "invoice"]
    assert {view.who for view in shown} == {"buyer@example.com"}
    assert shown[0].details == ["Line 1: confirmed 60, price 2.5, promised 2026-12-01"]
    assert shown[1].title == "Shipment UPS 1Z9" and "Expected 2026-12-05" in shown[1].details
    assert shown[2].title == "Invoice INV-7" and "Total 150 USD" in shown[2].details


def test_removing_an_entry_needs_a_reason_and_undoes_its_effect(db: Session, order: L.PurchaseOrder) -> None:
    first, _ = ids(db, order)
    ack = E.record_ack(db, order, [E.AckInput(first, D("60"), None, date(2026, 12, 1))])
    ship = E.record_shipment(db, order, [E.ShipLineInput(first, D("60"))])
    bill = E.record_invoice(db, order, [E.InvoiceLineInput(first, D("100"), D("2.5"))], invoice_number="INV-1")
    assert status(db, first) is LineStatus.CLOSED
    with pytest.raises(LedgerError, match="Reason is needed"):
        E.remove_entry(db, order, "invoice", bill.id, " ")
    E.remove_entry(db, order, "invoice", bill.id, "Typed against the wrong PO")
    assert status(db, first) is LineStatus.SHIPPED  # confirmed 60 and 60 shipped: shipped, with 40 still open
    E.remove_entry(db, order, "shipment", ship.id, "Duplicate notice")
    assert status(db, first) is LineStatus.ACKNOWLEDGED
    E.remove_entry(db, order, "ack", ack.id, "Wrong line")
    assert status(db, first) is LineStatus.SENT
    assert E.promised_history(db, order) == {}  # the removed ack's date went with it
    assert db.scalar(select(func.count()).select_from(L.AckLine)) == 0


def test_removal_is_audited_with_what_the_entry_said_and_why(db: Session, order: L.PurchaseOrder) -> None:
    first, _ = ids(db, order)
    bill = E.record_invoice(db, order, [E.InvoiceLineInput(first, D("10"), D("2.5"))], invoice_number="INV-9")
    E.remove_entry(db, order, "invoice", bill.id, "Wrong PO")
    entry = db.scalars(select(AuditLog).where(AuditLog.action == "invoice.remove")).one()
    assert "INV-9" in (entry.before or "") and "Wrong PO" in (entry.after or "")


def test_an_entry_of_another_po_or_kind_cannot_be_removed_through_this_one(db: Session, order: L.PurchaseOrder) -> None:
    first, _ = ids(db, order)
    other = orders.create_po(
        db, po_number="PO-2", vendor_id=order.vendor_id, currency="USD", date_sent=None, buyer=None,
        lines=[orders.parse_line("Line 1", item="X", quantity="1", unit="EA", unit_price="1")],
    )  # fmt: skip
    theirs = E.record_shipment(db, other, [E.ShipLineInput(only_line(db, other), D("1"))])
    mine = E.record_shipment(db, order, [E.ShipLineInput(first, D("1"))])
    with pytest.raises(LedgerError, match="not found"):
        E.remove_entry(db, order, "shipment", theirs.id, "x")  # someone else's PO
    with pytest.raises(LedgerError, match="not found"):
        E.remove_entry(db, order, "invoice", mine.id, "x")  # right number, wrong kind
    with pytest.raises(LedgerError, match="not found"):
        E.remove_entry(db, order, "nonsense", mine.id, "x")
    assert db.scalar(select(func.count()).select_from(L.Shipment)) == 2


def test_every_recording_is_audited(db: Session, order: L.PurchaseOrder) -> None:
    first, _ = ids(db, order)
    who = person(db)
    E.record_ack(db, order, [E.AckInput(first, D("1"))], actor=who)
    E.record_shipment(db, order, [E.ShipLineInput(first, D("1"))], actor=who)
    E.record_invoice(db, order, [E.InvoiceLineInput(first, D("1"), D("1"))], invoice_number="A", actor=who)
    actions = set(db.scalars(select(AuditLog.action).where(AuditLog.user_id == who.id)))
    assert {"ack.record", "shipment.record", "invoice.record"} <= actions
