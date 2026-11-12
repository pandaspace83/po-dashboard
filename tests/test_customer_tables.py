"""The customer-side tables (PD-8.1, ARCHITECTURE section 16.1): the database itself enforces their rules, they are new
tables only, and every purchasing record survives the migration unchanged."""

from __future__ import annotations

from collections.abc import Iterator
from decimal import Decimal
from pathlib import Path

import pytest
from alembic import command
from sqlalchemy import inspect, select, text
from sqlalchemy.exc import IntegrityError, StatementError
from sqlalchemy.orm import Session
from sqlalchemy.orm.exc import StaleDataError

from po_dashboard.config import Settings
from po_dashboard.customers import config
from po_dashboard.db import customers as c
from po_dashboard.db.engine import make_engine
from po_dashboard.db.ledger import Acknowledgement, POLine, PurchaseOrder, Vendor
from po_dashboard.db.migrate import alembic_config, upgrade
from test_gate import env  # noqa: F401

NEW_TABLES = {
    "customer", "customer_name", "customer_domain", "customer_po", "customer_po_line", "customer_po_intake",
    "sales_import", "sales_import_row", "sales_invoice", "sales_invoice_observation", "sales_invoice_line",
    "allocation", "reconciliation_event", "setting",
}  # fmt: skip
PURCHASING = ("vendor", "vendor_domain", "purchase_order", "po_line", "acknowledgement", "ack_line", "shipment",
              "shipment_line", "invoice", "invoice_line", "promised_date_history")  # fmt: skip


@pytest.fixture
def db(env) -> Iterator[Session]:  # noqa: ANN001, F811
    with Session(env[1]) as session:
        yield session


def customer(db: Session, name: str = "Globex Racing") -> c.Customer:
    row = c.Customer(name=name, name_key=" ".join(name.lower().split()))
    db.add(row)
    db.flush()
    return row


def po(db: Session, who: c.Customer, number: str = "CP-100", revision: int = 0, **more: object) -> c.CustomerPO:
    key = "".join(ch for ch in number.lower() if ch.isalnum())
    values: dict[str, object] = {"currency": "USD", **more}
    row = c.CustomerPO(customer_id=who.id, po_number=number, po_key=key, revision=revision, **values)
    db.add(row)
    db.flush()
    return row


def line(db: Session, order: c.CustomerPO, n: int = 1, **more: object) -> c.CustomerPOLine:
    values: dict[str, object] = dict(item="WIDGET-1", qty=Decimal("10"), unit_price=Decimal("100"))
    row = c.CustomerPOLine(customer_po_id=order.id, line_no=n, **{**values, **more})
    db.add(row)
    db.flush()
    return row


def invoice(db: Session, number: str = "INV-1", kind: str = "invoice", **more: object) -> c.SalesInvoice:
    values: dict[str, object] = {"currency": "USD", **more}
    row = c.SalesInvoice(kind=kind, invoice_number=number, invoice_key=number.lower(), **values)
    db.add(row)
    db.flush()
    return row


def refused(db: Session, build) -> None:  # noqa: ANN001
    """The change is refused by the database. A savepoint keeps the rest of the test's work."""
    with pytest.raises(IntegrityError), db.begin_nested():
        build()
        db.flush()


# ---- the migration


def test_the_customer_tables_exist(env) -> None:  # noqa: ANN001, F811
    assert NEW_TABLES <= set(inspect(env[1]).get_table_names())


def test_the_migration_adds_tables_only_and_every_purchasing_record_is_unchanged(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path / "data")
    engine = make_engine(settings)
    command.upgrade(alembic_config(engine), "0009")
    with Session(engine) as session:
        vendor = Vendor(name="Acme Fasteners")
        session.add(vendor)
        session.flush()
        order = PurchaseOrder(po_number="4521", vendor_id=vendor.id, currency="USD")
        session.add(order)
        session.flush()
        session.add_all([POLine(po_id=order.id, line_no=1, item="Bolt", ordered_qty=Decimal("10"), unit="EA",
                                unit_price=Decimal("2.5")), Acknowledgement(po_id=order.id)])  # fmt: skip
        session.commit()
    names = ", ".join(f"'{t}'" for t in PURCHASING)
    sql = f"select name, sql from sqlite_master where name in ({names})"  # noqa: S608 - fixed names, no input
    with engine.connect() as conn:
        before_schema = conn.exec_driver_sql(sql).all()
        before_rows = {t: conn.exec_driver_sql(f"select * from {t}").all() for t in PURCHASING}  # noqa: S608
        assert conn.exec_driver_sql("select 1 from sqlite_master where name = 'customer'").first() is None
    engine.dispose()
    engine = upgrade(settings)  # to 0010, with the safety backup the app always makes first
    with engine.connect() as conn:
        assert conn.exec_driver_sql(sql).all() == before_schema  # not one purchasing table was altered
        after_rows = {t: conn.exec_driver_sql(f"select * from {t}").all() for t in PURCHASING}  # noqa: S608
        assert after_rows == before_rows
        assert conn.exec_driver_sql("select count(*) from customer").scalar() == 0
    assert NEW_TABLES <= set(inspect(engine).get_table_names())
    engine.dispose()


def test_going_back_removes_only_the_new_tables(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path / "data")
    engine = upgrade(settings)
    with Session(engine) as session:
        session.add(Vendor(name="Kept"))
        session.commit()
    command.downgrade(alembic_config(engine), "0009")
    tables = set(inspect(engine).get_table_names())
    assert not NEW_TABLES & tables and {"vendor", "extraction", "scan_state"} <= tables
    with engine.connect() as conn:
        assert conn.exec_driver_sql("select name from vendor").scalar() == "Kept"
    engine.dispose()


def test_purchasing_code_does_not_use_the_customer_side() -> None:
    """The two workflows stay separate (DEC-56): nothing that handles vendor POs imports the customer modules."""
    root = Path(__file__).parent.parent / "src" / "po_dashboard"
    pages = [root / "pages" / name for name in ("orders.py", "vendors.py", "entries.py", "quickbooks.py")]
    purchasing = [*root.joinpath("ledger").glob("*.py"), root / "db" / "ledger.py", *pages]
    for path in purchasing:
        source = path.read_text()
        assert "customers" not in source.replace("customer, the customer", "").replace("customers (", ""), path.name


# ---- settings


def test_the_defaults_are_in_the_table_and_read_with_their_types(db: Session) -> None:
    stored = {row.key: row.value for row in db.scalars(select(c.Setting))}
    assert stored == config.DEFAULTS
    assert config.decimal(db, "amount_tolerance") == Decimal("0.01") and config.decimal(db, "quantity_tolerance") == 0
    assert config.whole(db, "unbilled_days") == 30 and config.flag(db, "customer_job_split")
    assert {"shipping", "freight", "tax"} <= config.names(db, "charge_items")
    assert config.text(db, "default_currency") == "USD"


def test_a_changed_setting_wins_and_a_missing_or_damaged_one_falls_back(db: Session) -> None:
    db.get(c.Setting, "unbilled_days").value = "45"  # type: ignore[union-attr]
    db.flush()
    assert config.whole(db, "unbilled_days") == 45
    db.get(c.Setting, "amount_tolerance").value = "lots"  # type: ignore[union-attr]
    db.get(c.Setting, "quantity_tolerance").value = "-3"  # type: ignore[union-attr]
    db.flush()
    assert config.decimal(db, "amount_tolerance") == Decimal("0.01") and config.decimal(db, "quantity_tolerance") == 0
    db.delete(db.get(c.Setting, "default_currency"))
    db.flush()
    assert config.text(db, "default_currency") == "USD"
    db.get(c.Setting, "unbilled_days").value = "2.5"  # type: ignore[union-attr]
    db.flush()
    assert config.whole(db, "unbilled_days") == 30  # not a whole number of days: the default


# ---- customers


def test_a_customer_name_is_unique_whatever_its_case_and_spacing(db: Session) -> None:
    customer(db, "Globex Racing")
    refused(db, lambda: customer(db, "GLOBEX   racing"))
    other = customer(db, "Initech")
    db.add(c.CustomerName(customer_id=other.id, name="Globex Racing LLC", name_key="globex racing llc"))
    db.flush()
    refused(db, lambda: db.add(c.CustomerName(customer_id=other.id, name="x", name_key="globex racing llc")))


def test_a_domain_is_lower_case_and_belongs_to_one_customer(db: Session) -> None:
    first, second = customer(db, "One"), customer(db, "Two")
    db.add(c.CustomerDomain(customer_id=first.id, domain="one.example"))
    db.flush()
    refused(db, lambda: db.add(c.CustomerDomain(customer_id=second.id, domain="one.example")))
    refused(db, lambda: db.add(c.CustomerDomain(customer_id=second.id, domain="Two.Example")))


def test_a_customer_with_orders_cannot_be_deleted_but_an_empty_one_takes_its_names_with_it(db: Session) -> None:
    busy, idle = customer(db, "Busy"), customer(db, "Idle")
    po(db, busy)
    db.add_all([c.CustomerName(customer_id=idle.id, name="Idle Co", name_key="idle co"),
                c.CustomerDomain(customer_id=idle.id, domain="idle.example")])  # fmt: skip
    db.commit()
    with pytest.raises(IntegrityError):
        db.delete(db.get(c.Customer, busy.id))
        db.flush()
    db.rollback()
    db.delete(db.get(c.Customer, idle.id))
    db.commit()
    assert db.scalar(select(c.CustomerName.id)) is None and db.scalar(select(c.CustomerDomain.id)) is None


def test_a_stale_save_of_a_customer_is_refused(env, db: Session) -> None:  # noqa: ANN001, F811
    who = customer(db)
    db.commit()
    with Session(env[1]) as other:
        mine = other.get(c.Customer, who.id)
        db.get(c.Customer, who.id).contact_name = "First"  # type: ignore[union-attr]
        db.commit()
        mine.contact_name = "Second"  # type: ignore[union-attr]
        with pytest.raises(StaleDataError):
            other.commit()


# ---- customer POs


def test_a_po_number_is_unique_for_its_customer_and_revision_not_across_customers(db: Session) -> None:
    one, two = customer(db, "One"), customer(db, "Two")
    po(db, one, "CP-100")
    po(db, two, "CP-100")  # the same number for another customer is a different order
    po(db, one, "CP-100", revision=1)  # a revision of the same order
    refused(db, lambda: po(db, one, "cp 100"))  # the same order spelt differently, same revision
    refused(db, lambda: po(db, one, "CP-100", revision=1))


def test_a_customer_po_has_known_states_sources_currency_and_revision(db: Session) -> None:
    who = customer(db)
    refused(db, lambda: po(db, who, "A", state="maybe"))
    refused(db, lambda: po(db, who, "B", source="telepathy"))
    refused(db, lambda: po(db, who, "C", revision=-1))
    refused(db, lambda: po(db, who, "D", currency="usd"))
    for state in c.PO_STATES:
        po(db, who, f"S-{state}", state=state)


def test_a_po_line_needs_a_positive_quantity_and_a_price_and_a_line_number_used_once(db: Session) -> None:
    order = po(db, customer(db))
    line(db, order, 1)
    refused(db, lambda: line(db, order, 1))
    refused(db, lambda: line(db, order, 2, qty=Decimal("0")))
    refused(db, lambda: line(db, order, 3, unit_price=Decimal("-1")))
    line(db, order, 4, unit_price=Decimal("0"))  # a free line is allowed


def test_the_lines_of_a_po_go_with_it_but_what_was_invoiced_stays(db: Session) -> None:
    order, bill = po(db, customer(db)), None
    one = line(db, order)
    bill = invoice(db)
    db.add(c.Allocation(customer_po_id=order.id, customer_po_line_id=one.id, sales_invoice_id=bill.id,
                        qty=Decimal("10"), amount=Decimal("1000")))  # fmt: skip
    db.add(c.ReconciliationEvent(customer_po_id=order.id, status_after="matched", cause="upload"))
    db.commit()
    db.delete(db.get(c.CustomerPO, order.id))
    db.commit()
    assert db.scalar(select(c.CustomerPOLine.id)) is None and db.scalar(select(c.Allocation.id)) is None
    assert db.scalar(select(c.ReconciliationEvent.id)) is None
    assert db.get(c.SalesInvoice, bill.id) is not None  # the invoice is a record of its own


def test_numbers_are_exact_and_more_places_than_kept_are_refused_not_rounded(db: Session) -> None:
    order = po(db, customer(db), stated_total=Decimal("1234.567891"))
    row = line(db, order, qty=Decimal("2.5"), unit_price=Decimal("0.000001"), stated_amount=Decimal("0.000003"))
    db.commit()
    db.expire_all()
    got = db.get(c.CustomerPOLine, row.id)
    assert (got.qty, got.unit_price, got.stated_amount) == (Decimal("2.5"), Decimal("0.000001"), Decimal("0.000003"))  # type: ignore[union-attr]
    assert db.get(c.CustomerPO, order.id).stated_total == Decimal("1234.567891")  # type: ignore[union-attr]
    with pytest.raises(StatementError, match="nothing is rounded"), db.begin_nested():
        line(db, order, 2, unit_price=Decimal("0.0000001"))
    with pytest.raises(StatementError, match="float is not exact"), db.begin_nested():
        line(db, po(db, customer(db, "Other"), "X"), qty=2.5)  # type: ignore[arg-type]


def test_intake_rows_have_known_states_and_survive_the_person_who_made_them(db: Session) -> None:
    db.add(c.CustomerPOIntake(source="document", payload="{}", reason="no customer found"))
    db.flush()
    refused(db, lambda: db.add(c.CustomerPOIntake(source="document", payload="{}", reason="x", state="lost")))
    refused(db, lambda: db.add(c.CustomerPOIntake(source="fax", payload="{}", reason="x")))


# ---- sales invoices and uploads


def test_an_invoice_number_is_unique_per_kind_so_a_credit_memo_may_share_it(db: Session) -> None:
    invoice(db, "1001")
    invoice(db, "1001", kind="credit_memo")
    refused(db, lambda: invoice(db, "1001"))


def test_an_invoice_has_known_kind_level_state_and_currency(db: Session) -> None:
    refused(db, lambda: invoice(db, "A", kind="refund"))
    refused(db, lambda: invoice(db, "B", level="paragraph"))
    refused(db, lambda: invoice(db, "C", state="lost"))
    refused(db, lambda: invoice(db, "D", currency="US"))
    ok = invoice(db, "E", level="line", state="needs_review", total=Decimal("-250.5"))  # a credit memo may be negative
    assert ok.id


def test_invoice_lines_have_known_kinds_and_may_be_negative_or_have_no_item(db: Session) -> None:
    bill = invoice(db)
    db.add_all([c.SalesInvoiceLine(invoice_id=bill.id, position=1, item="WIDGET-1", qty=Decimal("10"),
                                   unit_price=Decimal("100"), amount=Decimal("1000")),
                c.SalesInvoiceLine(invoice_id=bill.id, position=2, amount=Decimal("-50"), kind="discount"),
                ])  # fmt: skip
    db.flush()
    refused(db, lambda: db.add(c.SalesInvoiceLine(invoice_id=bill.id, position=2, amount=Decimal("1"))))
    refused(db, lambda: db.add(c.SalesInvoiceLine(invoice_id=bill.id, position=3, amount=Decimal("1"), kind="gift")))


def test_a_customer_with_invoices_cannot_be_deleted(db: Session) -> None:
    who = customer(db)
    invoice(db, "INV-9", customer_id=who.id)
    db.commit()
    with pytest.raises(IntegrityError):
        db.delete(db.get(c.Customer, who.id))
        db.flush()
    db.rollback()


def upload(db: Session, name: str = "sales.csv") -> c.SalesImport:
    row = c.SalesImport(file_name=name, file_hash="a" * 64, stored_path="aa/" + "a" * 64, mapping="{}")
    db.add(row)
    db.flush()
    return row


def test_every_row_of_an_upload_is_kept_once_with_a_known_outcome_and_goes_with_its_upload(db: Session) -> None:
    first = upload(db)
    db.add(c.SalesImportRow(import_id=first.id, row_number=1, cells="[]", outcome="line"))
    db.flush()
    refused(db, lambda: db.add(c.SalesImportRow(import_id=first.id, row_number=1, cells="[]", outcome="line")))
    refused(db, lambda: db.add(c.SalesImportRow(import_id=first.id, row_number=2, cells="[]", outcome="shrug")))
    bill = invoice(db)
    db.add(c.SalesInvoiceObservation(invoice_id=bill.id, import_id=first.id, fingerprint="f" * 64, content="{}",
                                     outcome="first"))  # fmt: skip
    refused(db, lambda: db.add(c.SalesInvoiceObservation(invoice_id=bill.id, import_id=first.id, fingerprint="f",
                                                         content="{}", outcome="maybe")))  # fmt: skip
    db.commit()
    db.delete(db.get(c.SalesImport, first.id))
    db.commit()
    assert db.scalar(select(c.SalesImportRow.id)) is None and db.scalar(select(c.SalesInvoiceObservation.id)) is None
    assert db.get(c.SalesInvoice, bill.id) is not None


def test_two_uploads_may_show_the_same_invoice_and_one_upload_the_same_number_twice(db: Session) -> None:
    bill, first, second = invoice(db), upload(db, "a.csv"), upload(db, "b.csv")
    for batch in (first, second, second):  # the last: two different invoices with one number in one file
        db.add(c.SalesInvoiceObservation(invoice_id=bill.id, import_id=batch.id, fingerprint="f" * 64, content="{}",
                                         outcome="same"))  # fmt: skip
    db.flush()
    assert len(list(db.scalars(select(c.SalesInvoiceObservation)))) == 3


# ---- allocations


def test_one_po_may_have_many_invoices_and_one_invoice_many_pos(db: Session) -> None:
    who = customer(db)
    one, two = po(db, who, "CP-1"), po(db, who, "CP-2")
    first, second = invoice(db, "INV-1"), invoice(db, "INV-2")
    for order, bill in ((one, first), (one, second), (two, second)):
        db.add(c.Allocation(customer_po_id=order.id, sales_invoice_id=bill.id, amount=Decimal("100")))
    db.commit()
    assert len(list(db.scalars(select(c.Allocation).where(c.Allocation.customer_po_id == one.id)))) == 2
    assert len(list(db.scalars(select(c.Allocation).where(c.Allocation.sales_invoice_id == second.id)))) == 2


def test_an_allocation_has_known_basis_and_state_and_lines_are_optional(db: Session) -> None:
    order, bill = po(db, customer(db)), invoice(db)
    db.add(c.Allocation(customer_po_id=order.id, sales_invoice_id=bill.id))  # an invoice-level match
    db.flush()
    refused(db, lambda: db.add(c.Allocation(customer_po_id=order.id, sales_invoice_id=bill.id, basis="hunch")))
    refused(db, lambda: db.add(c.Allocation(customer_po_id=order.id, sales_invoice_id=bill.id, state="done")))


def test_an_allocation_goes_when_its_invoice_goes_and_the_order_stays(db: Session) -> None:
    order, bill = po(db, customer(db)), invoice(db)
    db.add(c.Allocation(customer_po_id=order.id, sales_invoice_id=bill.id))
    db.commit()
    db.delete(db.get(c.SalesInvoice, bill.id))
    db.commit()
    assert db.scalar(select(c.Allocation.id)) is None and db.get(c.CustomerPO, order.id) is not None


def test_foreign_keys_are_enforced_on_the_customer_side(db: Session) -> None:
    refused(db, lambda: db.add(c.CustomerPO(customer_id=999, po_number="X", po_key="x", currency="USD")))
    refused(db, lambda: db.add(c.Allocation(customer_po_id=999, sales_invoice_id=999)))
    assert db.execute(text("pragma foreign_keys")).scalar() == 1
