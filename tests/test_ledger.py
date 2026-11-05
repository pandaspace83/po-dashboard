"""The ledger tables (PD-1.1b): exact numbers, and the rules the database itself enforces."""

from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import Engine, func, inspect, select
from sqlalchemy.exc import IntegrityError, StatementError
from sqlalchemy.orm import Session

from po_dashboard.config import Settings
from po_dashboard.db import ledger as L
from po_dashboard.db import models
from po_dashboard.db.migrate import upgrade
from po_dashboard.db.types import Money, Quantity

D = Decimal


@pytest.fixture
def engine(tmp_path: Path) -> Engine:
    eng = upgrade(Settings(data_dir=tmp_path / "data"))
    yield eng
    eng.dispose()


@pytest.fixture
def db(engine: Engine) -> Session:
    with Session(engine) as session:
        yield session


def vendor(db: Session, name: str = "Acme") -> L.Vendor:
    row = L.Vendor(name=name)
    db.add(row)
    db.flush()
    return row


def po(db: Session, number: str = "PO-1", currency: str = "USD", vendor_row: L.Vendor | None = None) -> L.PurchaseOrder:
    row = L.PurchaseOrder(po_number=number, vendor_id=(vendor_row or vendor(db, f"V-{number}")).id, currency=currency)
    db.add(row)
    db.flush()
    return row


def line(db: Session, order: L.PurchaseOrder, no: int = 1, qty: str = "10", price: str = "2.5") -> L.POLine:
    row = L.POLine(po_id=order.id, line_no=no, item="Bolt", ordered_qty=D(qty), unit="EA", unit_price=D(price))
    db.add(row)
    db.flush()
    return row


def refused(db: Session, thing: object, why: str) -> None:
    """The database must refuse this row, and for the stated reason (a named rule, or the column that clashed)."""
    db.add(thing)
    with pytest.raises(IntegrityError) as caught:
        db.flush()
    assert why in str(caught.value.orig), f"refused for another reason: {caught.value.orig}"
    db.rollback()


# ---- the tables exist


def test_the_ledger_tables_are_created(engine: Engine) -> None:
    expected = {
        "vendor", "vendor_domain", "purchase_order", "po_line", "acknowledgement", "ack_line",
        "promised_date_history", "shipment", "shipment_line", "invoice", "invoice_line",
    }  # fmt: skip
    assert expected <= set(inspect(engine).get_table_names())


def test_there_is_no_status_column_anywhere(engine: Engine) -> None:
    """Status is computed from the evidence (ARCHITECTURE §5), so it cannot be typed in or drift."""
    for table in ("purchase_order", "po_line"):
        assert "status" not in {c["name"] for c in inspect(engine).get_columns(table)}


# ---- exact numbers (DEC-24)


def test_quantities_and_prices_come_back_exactly(db: Session) -> None:
    row = line(db, po(db), qty="1234.5678", price="0.000001")
    db.commit()
    db.expire_all()
    stored = db.get(L.POLine, row.id)
    assert stored is not None
    assert stored.ordered_qty == D("1234.5678") and stored.unit_price == D("0.000001")
    assert isinstance(stored.ordered_qty, Decimal)


def test_tenths_add_up_exactly_in_sql(db: Session) -> None:
    """0.1 + 0.2 is 0.30000000000000004 in floats; here it is exactly 0.3, even when SQL does the adding."""
    order = po(db)
    target = line(db, order, qty="1")
    for qty in ("0.1", "0.2"):
        shipment = L.Shipment(po_id=order.id)
        db.add(shipment)
        db.flush()
        db.add(L.ShipmentLine(shipment_id=shipment.id, po_line_id=target.id, qty=D(qty)))
    db.flush()
    assert db.scalar(select(func.sum(L.ShipmentLine.qty))) == D("0.3")


@pytest.mark.parametrize("kind", [Quantity, Money])
def test_too_many_decimal_places_are_refused_not_rounded(kind: type) -> None:
    column = kind()
    places = kind.places  # type: ignore[attr-defined]
    assert column.process_bind_param(D("1").scaleb(-places), None) == 1  # the smallest step is kept
    with pytest.raises(ValueError, match="nothing is rounded"):
        column.process_bind_param(D("1").scaleb(-places - 1), None)


@pytest.mark.parametrize("bad", [1.5, "1.5", True, None.__class__])
def test_floats_and_other_inexact_values_are_refused(bad: object) -> None:
    with pytest.raises(TypeError):
        Quantity().process_bind_param(bad, None)


@pytest.mark.parametrize("bad", [D("NaN"), D("Infinity"), D(2**64)])
def test_nan_infinity_and_huge_numbers_are_refused(bad: Decimal) -> None:
    with pytest.raises(ValueError):
        Money().process_bind_param(bad, None)


def test_a_float_in_a_row_stops_the_save(db: Session) -> None:
    order = po(db)
    db.add(L.POLine(po_id=order.id, line_no=1, item="x", ordered_qty=1.5, unit="EA", unit_price=D("1")))  # type: ignore[arg-type]
    with pytest.raises(StatementError):
        db.flush()
    db.rollback()


@pytest.mark.parametrize("kind", [Quantity, Money])
def test_queries_on_these_columns_can_be_cached(kind: type) -> None:
    """SQLAlchemy reads cache_ok from the class itself; a subclass without it silently slows every query."""
    assert kind.__dict__.get("cache_ok") is True


def test_none_stays_none() -> None:
    assert Quantity().process_bind_param(None, None) is None and Quantity().process_result_value(None, None) is None


def test_an_integer_is_accepted_and_read_back_as_decimal() -> None:
    assert Quantity().process_bind_param(3, None) == 30000
    assert Quantity().process_result_value(30000, None) == D("3")
    assert Money().process_bind_param(D("-1.25"), None) == -1250000


# ---- purchase orders and lines


def test_a_po_number_is_unique(db: Session) -> None:
    po(db, "PO-1")
    refused(db, L.PurchaseOrder(po_number="PO-1", vendor_id=vendor(db, "Other").id, currency="USD"), "po_number")


@pytest.mark.parametrize("currency", ["usd", "US", "USDD", "", "u$d"])
def test_a_currency_must_be_a_three_letter_upper_case_code(db: Session, currency: str) -> None:
    refused(db, L.PurchaseOrder(po_number="PO-X", vendor_id=vendor(db).id, currency=currency), "currency_iso_code")


def test_each_po_keeps_its_own_currency(db: Session) -> None:
    po(db, "PO-1", "USD")
    po(db, "PO-2", "EUR")
    assert sorted(db.scalars(select(L.PurchaseOrder.currency))) == ["EUR", "USD"]


def test_an_unknown_po_source_is_refused(db: Session) -> None:
    refused(
        db,
        L.PurchaseOrder(po_number="PO-9", vendor_id=vendor(db).id, currency="USD", source="telepathy"),
        "source_known",
    )


def test_a_line_number_is_unique_within_its_po_only(db: Session) -> None:
    first, second = po(db, "PO-1"), po(db, "PO-2")
    line(db, first, 1)
    line(db, second, 1)  # the same number on another PO is fine
    db.commit()
    refused(
        db,
        L.POLine(po_id=first.id, line_no=1, item="y", ordered_qty=D("1"), unit="EA", unit_price=D("1")),
        "po_id, po_line.line_no",
    )


def test_a_line_needs_a_positive_quantity_and_a_price_not_below_zero(db: Session) -> None:
    order = po(db)
    refused(
        db,
        L.POLine(po_id=order.id, line_no=1, item="x", ordered_qty=D("0"), unit="EA", unit_price=D("1")),
        "ordered_positive",
    )
    order = po(db, "PO-2")
    refused(
        db,
        L.POLine(po_id=order.id, line_no=1, item="x", ordered_qty=D("1"), unit="EA", unit_price=D("-0.01")),
        "price_not_negative",
    )
    order = po(db, "PO-3")
    line(db, order, price="0")  # a free line is allowed


def test_closing_a_line_by_hand_needs_a_reason_a_time_and_a_note(db: Session) -> None:
    target = line(db, po(db))
    now = datetime.now(UTC)
    target.closed_reason, target.closed_at, target.close_note = "short", now, "Vendor shipped 8 of 10"
    db.flush()  # complete: accepted
    target.close_note = None
    with pytest.raises(IntegrityError):
        db.flush()
    db.rollback()


def test_an_open_line_cannot_carry_half_a_close(db: Session) -> None:
    order = po(db)
    refused(db, L.POLine(po_id=order.id, line_no=1, item="x", ordered_qty=D("1"), unit="EA", unit_price=D("1"),
                         close_note="stray note"), "closed_by_hand_is_complete")  # fmt: skip


def test_an_unknown_close_reason_is_refused(db: Session) -> None:
    order = po(db)
    row = L.POLine(po_id=order.id, line_no=1, item="x", ordered_qty=D("1"), unit="EA", unit_price=D("1"))
    row.closed_reason, row.closed_at, row.close_note = "lost", datetime.now(UTC), "n"
    refused(db, row, "close_reason_known")


def test_dates_come_back_as_dates(db: Session) -> None:
    order = po(db)
    order.date_sent = date(2026, 11, 4)
    target = line(db, order)
    target.requested_date = date(2026, 12, 1)
    db.commit()
    db.expire_all()
    assert db.get(L.PurchaseOrder, order.id).date_sent == date(2026, 11, 4)  # type: ignore[union-attr]
    assert db.get(L.POLine, target.id).requested_date == date(2026, 12, 1)  # type: ignore[union-attr]


# ---- vendors


def test_a_vendor_name_and_a_domain_are_unique_and_a_domain_is_lower_case(db: Session) -> None:
    acme = vendor(db, "Acme")
    db.add(L.VendorDomain(vendor_id=acme.id, domain="acme.com"))
    db.commit()  # a refusal rolls back, so what must survive it is saved first
    refused(db, L.Vendor(name="Acme"), "vendor.name")
    other = vendor(db, "Other")
    refused(db, L.VendorDomain(vendor_id=other.id, domain="acme.com"), "vendor_domain.domain")  # one domain, one vendor
    other = vendor(db, "Third")
    refused(db, L.VendorDomain(vendor_id=other.id, domain="Mixed.COM"), "lower_case")


# ---- acknowledgements, shipments, invoices


def test_an_ack_keeps_what_the_vendor_confirmed_and_may_leave_parts_out(db: Session) -> None:
    order = po(db)
    target = line(db, order, qty="100")
    ack = L.Acknowledgement(po_id=order.id)
    db.add(ack)
    db.flush()
    db.add(L.AckLine(ack_id=ack.id, po_line_id=target.id, confirmed_qty=D("60")))  # short, no price, no date
    db.flush()
    row = db.scalars(select(L.AckLine)).one()
    assert row.confirmed_qty == D("60") and row.promised_date is None and row.confirmed_price is None


def test_a_line_is_confirmed_once_per_ack(db: Session) -> None:
    order = po(db)
    target = line(db, order)
    ack = L.Acknowledgement(po_id=order.id)
    db.add(ack)
    db.flush()
    db.add(L.AckLine(ack_id=ack.id, po_line_id=target.id))
    db.commit()
    refused(db, L.AckLine(ack_id=ack.id, po_line_id=target.id), "ack_line.ack_id, ack_line.po_line_id")


def test_promised_dates_are_kept_as_history(db: Session) -> None:
    target = line(db, po(db))
    for when in (date(2026, 12, 1), date(2026, 12, 15)):
        db.add(L.PromisedDateHistory(po_line_id=target.id, promised_date=when))
    db.flush()
    dates = db.scalars(select(L.PromisedDateHistory.promised_date).order_by(L.PromisedDateHistory.id)).all()
    assert dates == [date(2026, 12, 1), date(2026, 12, 15)]  # the later date is the slip


def test_shipment_and_invoice_quantities_must_be_positive(db: Session) -> None:
    order = po(db)
    target = line(db, order)
    shipment, invoice = L.Shipment(po_id=order.id), L.Invoice(po_id=order.id, invoice_number="INV-1")
    db.add_all([shipment, invoice])
    db.commit()
    refused(db, L.ShipmentLine(shipment_id=shipment.id, po_line_id=target.id, qty=D("0")), "qty_positive")
    refused(db, L.InvoiceLine(invoice_id=invoice.id, po_line_id=target.id, qty=D("0"), price=D("1")), "qty_positive")
    refused(
        db, L.InvoiceLine(invoice_id=invoice.id, po_line_id=target.id, qty=D("1"), price=D("-1")), "price_not_negative"
    )


def test_an_invoice_number_is_unique_per_po_not_across_pos(db: Session) -> None:
    first, second = po(db, "PO-1"), po(db, "PO-2")
    db.add_all([L.Invoice(po_id=first.id, invoice_number="INV-1"), L.Invoice(po_id=second.id, invoice_number="INV-1")])
    db.commit()  # the same number on a different PO is fine
    refused(db, L.Invoice(po_id=first.id, invoice_number="INV-1"), "invoice.po_id, invoice.invoice_number")


def test_money_totals_and_shipment_dates_round_trip(db: Session) -> None:
    order = po(db)
    db.add(L.Invoice(po_id=order.id, invoice_number="A", total=D("1234.560001"), invoice_date=date(2026, 11, 1)))
    db.add(L.Shipment(po_id=order.id, eta=date(2026, 11, 20), carrier="UPS", tracking_number="1Z999"))
    db.commit()
    assert db.scalars(select(L.Invoice.total)).one() == D("1234.560001")
    assert db.scalars(select(L.Shipment.eta)).one() == date(2026, 11, 20)


# ---- deletes: what goes with its parent, and what refuses to lose it


def test_deleting_a_po_takes_its_lines_but_refuses_while_a_vendor_said_something_about_it(db: Session) -> None:
    bare = po(db, "PO-1")
    line(db, bare)
    db.delete(bare)
    db.flush()
    assert db.scalar(select(func.count()).select_from(L.POLine)) == 0  # the lines went with it

    busy = po(db, "PO-2")
    db.add(L.Acknowledgement(po_id=busy.id))
    db.flush()
    db.delete(busy)
    with pytest.raises(IntegrityError):
        db.flush()
    db.rollback()


def test_a_vendor_with_purchase_orders_cannot_be_deleted(db: Session) -> None:
    acme = vendor(db)
    po(db, vendor_row=acme)
    db.commit()
    db.delete(acme)
    with pytest.raises(IntegrityError):
        db.flush()
    db.rollback()


def test_a_vendor_without_purchase_orders_takes_its_domains_with_it(db: Session) -> None:
    acme = vendor(db)
    db.add(L.VendorDomain(vendor_id=acme.id, domain="acme.com"))
    db.flush()
    db.delete(acme)
    db.flush()
    assert db.scalar(select(func.count()).select_from(L.VendorDomain)) == 0


def test_a_line_that_something_refers_to_cannot_be_deleted(db: Session) -> None:
    order = po(db)
    target = line(db, order)
    invoice = L.Invoice(po_id=order.id, invoice_number="INV-1")
    db.add(invoice)
    db.flush()
    db.add(L.InvoiceLine(invoice_id=invoice.id, po_line_id=target.id, qty=D("1"), price=D("1")))
    db.commit()
    db.delete(target)
    with pytest.raises(IntegrityError):
        db.flush()
    db.rollback()


def test_deleting_an_ack_takes_its_lines(db: Session) -> None:
    order = po(db)
    target = line(db, order)
    ack = L.Acknowledgement(po_id=order.id)
    db.add(ack)
    db.flush()
    db.add(L.AckLine(ack_id=ack.id, po_line_id=target.id))
    db.flush()
    db.delete(ack)
    db.flush()
    assert db.scalar(select(func.count()).select_from(L.AckLine)) == 0


def test_removing_a_person_keeps_the_records_they_made(db: Session) -> None:
    user = models.AppUser(email="a@example.com", password_hash="x", role="buyer")
    db.add(user)
    db.flush()
    order = L.PurchaseOrder(po_number="PO-1", vendor_id=vendor(db).id, currency="USD", created_by=user.id)
    db.add(order)
    db.flush()
    db.delete(user)
    db.flush()
    db.refresh(order)
    assert order.created_by is None and order.po_number == "PO-1"
