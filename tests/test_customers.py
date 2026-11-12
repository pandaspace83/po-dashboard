"""Customers and customer PO intake (PD-8.2, DEC-57): exact matching only, nothing replaced without a person, nothing
guessed, and every change audited."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from po_dashboard.customers import config, intake, service
from po_dashboard.customers.intake import LineData, PoData
from po_dashboard.customers.keys import name_key, po_key
from po_dashboard.db import customers as c
from po_dashboard.db.models import AppUser, AuditLog
from po_dashboard.ledger.errors import LedgerError
from test_gate import env  # noqa: F401


@pytest.fixture
def db(env) -> Iterator[Session]:  # noqa: ANN001, F811
    with Session(env[1]) as session:
        yield session


@pytest.fixture
def boss(db: Session) -> AppUser:
    return db.scalar(select(AppUser).where(AppUser.email == "boss@example.com"))  # type: ignore[return-value]


def globex(db: Session, boss: AppUser | None = None, **more: object) -> c.Customer:
    values: dict[str, object] = {
        "name": "Globex Racing",
        "qb_names": "Globex Racing Inc\nGlobex",
        "domains": "globex.test",
    }
    return service.create_customer(db, actor=boss, **{**values, **more})  # type: ignore[arg-type]


def po(number: str = "CP-100", *, price: str = "100", qty: str = "10", **more: object) -> PoData:
    values: dict[str, object] = {
        "po_number": number, "currency": "USD", "source": "manual",
        "lines": (LineData(item="WIDGET-1", qty=Decimal(qty), unit_price=Decimal(price), unit="EA"),
                  LineData(item="WIDGET-2", qty=Decimal("5"), unit_price=Decimal("20"), unit="EA")),
    }  # fmt: skip
    return PoData(**{**values, **more})  # type: ignore[arg-type]


def actions(db: Session) -> list[str]:
    return [a.action for a in db.scalars(select(AuditLog).order_by(AuditLog.id)) if a.action.startswith("customer")]


# ---- keys


def test_names_and_po_numbers_compare_only_by_their_keys() -> None:
    assert name_key("  Globex   RACING ") == "globex racing" and name_key("Globex") != name_key("Globex Racing")
    assert po_key("PO-1001") == po_key("po 1001") == po_key("1001") == "1001"
    assert po_key("CP-100") == "cp100" and po_key("PO") == "po" and po_key("POLE-5") != po_key("5")  # rule: leading PO


# ---- customers


def test_a_customer_is_created_with_its_names_and_domains_and_audited(db: Session, boss: AppUser) -> None:
    customer = globex(db, boss, contact_email="buyer@globex.test", notes="Net 30")
    db.commit()
    assert customer.active and customer.name_key == "globex racing"
    assert {n.name for n in db.scalars(select(c.CustomerName))} == {"Globex Racing Inc", "Globex"}
    assert [d.domain for d in db.scalars(select(c.CustomerDomain))] == ["globex.test"]
    assert actions(db) == ["customer.create"]
    log = db.scalars(select(AuditLog).where(AuditLog.action == "customer.create")).one()
    assert log.user_id == boss.id and "Globex Racing Inc" in (log.after or "")


def test_a_name_is_taken_once_whatever_its_case_and_a_name_never_means_two_customers(db: Session) -> None:
    globex(db)
    with pytest.raises(LedgerError, match="already exists"):
        service.create_customer(db, name="GLOBEX   racing")
    with pytest.raises(LedgerError, match="already used for Globex Racing"):
        service.create_customer(db, name="Initech", qb_names="Globex")  # another customer's QuickBooks name
    with pytest.raises(LedgerError, match="already used for Globex Racing"):
        service.create_customer(db, name="globex")  # equals a QuickBooks name of another customer
    with pytest.raises(LedgerError, match="already belongs to Globex Racing"):
        service.create_customer(db, name="Initech", domains="globex.test")
    other = service.create_customer(db, name="Initech", qb_names="Initech")  # its own name repeated is not a clash
    assert not db.scalars(select(c.CustomerName).where(c.CustomerName.customer_id == other.id)).all()


def test_customer_details_are_checked(db: Session) -> None:
    with pytest.raises(LedgerError, match="Name is needed"):
        service.create_customer(db, name="  ")
    with pytest.raises(LedgerError, match="email"):
        service.create_customer(db, name="X", contact_email="not an email")
    with pytest.raises(LedgerError, match="not an email domain"):
        service.create_customer(db, name="Y", domains="no spaces.com, @bad")


def test_a_customer_is_updated_with_before_and_after_and_a_stale_page_is_refused(db: Session, boss: AppUser) -> None:
    customer = globex(db, boss)
    db.commit()
    version = customer.version
    service.update_customer(db, customer, name="Globex Racing LLC", contact_name="Pat", contact_email=None, notes=None,
                            actor=boss, seen=version, check=True)  # fmt: skip
    db.commit()
    assert (
        customer.name == "Globex Racing LLC"
        and customer.name_key == "globex racing llc"
        and customer.version == version + 1
    )
    log = db.scalars(select(AuditLog).where(AuditLog.action == "customer.update")).one()
    assert "Globex Racing LLC" in (log.after or "") and "Globex Racing" in (log.before or "")
    with pytest.raises(LedgerError, match="changed by someone else"):
        service.update_customer(db, customer, name="Again", contact_name=None, contact_email=None, notes=None,
                                seen=version, check=True)  # fmt: skip
    service.update_customer(db, customer, name="Globex Racing LLC", contact_name="Pat", contact_email=None, notes=None)
    assert actions(db).count("customer.update") == 1  # nothing changed: nothing recorded


def test_an_inactive_customer_takes_no_new_po_but_keeps_its_records(db: Session, boss: AppUser) -> None:
    customer = globex(db, boss)
    intake.file_po(db, po(customer_id=customer.id), actor=boss)
    service.set_active(db, customer, False, actor=boss)
    db.commit()
    with pytest.raises(LedgerError, match="not active"):
        intake.file_po(db, po("CP-200", customer_id=customer.id), actor=boss)
    assert db.scalar(select(c.CustomerPO.id)) is not None
    service.set_active(db, customer, True, actor=boss)
    assert intake.file_po(db, po("CP-200", customer_id=customer.id), actor=boss).kind == "created"


def test_names_and_domains_are_added_and_removed_and_audited(db: Session, boss: AppUser) -> None:
    customer = globex(db, boss)
    other = service.create_customer(db, name="Initech", domains="initech.test")
    assert service.add_names(db, customer, "Globex East; Globex West", boss) == ["Globex East", "Globex West"]
    with pytest.raises(LedgerError, match="already used"):
        service.add_names(db, other, "globex east")
    assert service.add_domains(db, customer, "globex.co.uk, globex.test") == ["globex.co.uk"]  # one was held already
    with pytest.raises(LedgerError, match="already belongs to Initech"):
        service.add_domains(db, customer, "initech.test")
    with pytest.raises(LedgerError, match="already added"):
        service.add_domains(db, customer, "globex.test")
    name = db.scalars(select(c.CustomerName).where(c.CustomerName.name == "Globex East")).one()
    service.remove_name(db, customer, name.id, boss)
    with pytest.raises(LedgerError, match="not found"):
        service.remove_name(db, other, name.id)
    domain = db.scalars(select(c.CustomerDomain).where(c.CustomerDomain.domain == "globex.co.uk")).one()
    service.remove_domain(db, customer, domain.id, boss)
    assert {"customer.name_add", "customer.name_remove", "customer.domain_add", "customer.domain_remove"} <= set(
        actions(db)
    )


def test_a_customer_is_found_exactly_and_never_by_resemblance(db: Session) -> None:
    customer = globex(db)
    for name in ("Globex Racing", "  globex   RACING", "Globex Racing Inc", "GLOBEX"):
        assert service.find_by_name(db, name) == customer, name
    for name in ("Globex Racing Incorporated", "Globex Rac", "Globex Racing Team", "Glob", ""):
        assert service.find_by_name(db, name) is None, name
    assert service.find_by_domain(db, "@Globex.test") == customer and service.find_by_domain(db, "other.test") is None


def test_a_customer_job_counts_for_its_customer_only_while_that_setting_is_on(db: Session) -> None:
    customer = globex(db)
    assert service.find_for_export(db, "Globex Racing:Season 2027") == customer  # on by default (OPEN-24)
    assert service.find_by_name(db, "Globex Racing:Season 2027") is None  # not for other uses
    db.get(c.Setting, "customer_job_split").value = "0"  # type: ignore[union-attr]
    db.flush()
    assert service.find_for_export(db, "Globex Racing:Season 2027") is None
    assert service.find_for_export(db, "Nobody:Job") is None


def test_the_customer_search_and_counts(db: Session, boss: AppUser) -> None:
    customer = globex(db)
    service.set_active(db, service.create_customer(db, name="Globex Old"), False)
    assert [x.name for x in service.search(db, "glob")] == ["Globex Racing"] and service.search(db, " ") == []
    assert service.search(db, "100%") == []  # a wildcard is just text
    intake.file_po(db, po(customer_id=customer.id), actor=boss)
    intake.file_po(db, po(price="105", customer_id=customer.id), actor=boss)  # a revision: the same order
    assert service.counts(db) == {customer.id: (1, 0)}


# ---- filing a PO


def test_a_new_po_is_filed_active_and_the_person_filing_it_approves_it(db: Session, boss: AppUser) -> None:
    customer = globex(db, boss)
    result = intake.file_po(db, po(customer_id=customer.id, po_date=date(2026, 11, 10), stated_total=Decimal("1100")),
                            actor=boss, client_address="127.0.0.1")  # fmt: skip
    db.commit()
    order = result.po
    assert result.kind == "created" and order is not None
    assert (order.state, order.revision, order.po_key, order.approved_by, order.created_by) == (
        "active",
        0,
        "cp100",
        boss.id,
        boss.id,
    )
    assert (
        order.approved_at is not None and order.po_date == date(2026, 11, 10) and order.stated_total == Decimal("1100")
    )
    lines = list(db.scalars(select(c.CustomerPOLine).order_by(c.CustomerPOLine.line_no)))
    assert [(x.line_no, x.item, x.qty, x.unit_price) for x in lines] == [
        (1, "WIDGET-1", Decimal("10"), Decimal("100")),
        (2, "WIDGET-2", Decimal("5"), Decimal("20")),
    ]
    log = db.scalars(select(AuditLog).where(AuditLog.action == "customer_po.create")).one()
    assert log.client_address == "127.0.0.1" and '"lines": 2' in (log.after or "")


def test_the_same_number_for_another_customer_is_another_order(db: Session, boss: AppUser) -> None:
    one, two = globex(db), service.create_customer(db, name="Initech")
    assert intake.file_po(db, po(customer_id=one.id), actor=boss).kind == "created"
    assert intake.file_po(db, po(customer_id=two.id), actor=boss).kind == "created"
    assert len(list(db.scalars(select(c.CustomerPO)))) == 2


def test_the_same_order_with_the_same_terms_is_a_copy_and_changes_nothing(db: Session, boss: AppUser) -> None:
    customer = globex(db)
    first = intake.file_po(db, po(customer_id=customer.id), actor=boss).po
    before = actions(db)
    for number in ("cp-100", "CP 100", "cp100"):  # spelt differently
        again = intake.file_po(db, po(number, customer_id=customer.id), actor=boss)
        assert again.kind == "skipped" and again.po == first
    assert len(list(db.scalars(select(c.CustomerPO)))) == 1 and actions(db) == before


def test_the_same_number_with_other_terms_is_a_pending_revision_that_replaces_nothing(
    db: Session, boss: AppUser
) -> None:
    customer = globex(db)
    original = intake.file_po(db, po(customer_id=customer.id), actor=boss).po
    revised = intake.file_po(db, po(price="110", customer_id=customer.id), actor=boss)
    assert revised.kind == "revision" and revised.po is not None
    assert (revised.po.revision, revised.po.state, revised.po.approved_by) == (1, "pending", None)
    assert original.state == "active" and original.revision == 0  # type: ignore[union-attr]
    again = intake.file_po(db, po(price="110", customer_id=customer.id), actor=boss)  # the revision arriving twice
    assert again.kind == "skipped" and again.po == revised.po
    third = intake.file_po(db, po(price="120", customer_id=customer.id), actor=boss)
    assert third.po.revision == 2 and third.po.state == "pending"  # type: ignore[union-attr]
    assert original.state == "active"  # type: ignore[union-attr]


def test_a_change_to_the_date_currency_total_unit_or_a_line_is_a_revision(db: Session, boss: AppUser) -> None:
    customer = globex(db)
    intake.file_po(db, po(customer_id=customer.id, po_date=date(2026, 11, 1)), actor=boss)
    one_line = LineData(item="WIDGET-1", qty=Decimal(10), unit_price=Decimal(100))
    changes = [
        po(customer_id=customer.id, po_date=date(2026, 11, 2)), po(customer_id=customer.id, currency="EUR"),
        po(customer_id=customer.id, po_date=date(2026, 11, 1), stated_total=Decimal("9")),
        po(customer_id=customer.id, po_date=date(2026, 11, 1), qty="11"),
        po(customer_id=customer.id, po_date=date(2026, 11, 1), lines=(one_line,)),
    ]  # fmt: skip
    for number, change in enumerate(changes, start=1):
        assert intake.file_po(db, change, actor=boss).po.revision == number, number  # type: ignore[union-attr]


def test_approving_a_revision_makes_it_the_order_and_keeps_the_old_one(db: Session, boss: AppUser) -> None:
    customer = globex(db)
    original = intake.file_po(db, po(customer_id=customer.id), actor=boss).po
    revised = intake.file_po(db, po(price="110", customer_id=customer.id), actor=boss).po
    db.commit()
    version = revised.version  # type: ignore[union-attr]
    intake.approve_revision(db, revised, actor=boss, seen=version, check=True)  # type: ignore[arg-type]
    db.commit()
    assert (original.state, revised.state, revised.approved_by) == ("superseded", "active", boss.id)  # type: ignore[union-attr]
    log = db.scalars(select(AuditLog).where(AuditLog.action == "customer_po.approve")).one()
    assert '"revision": 0' in (log.before or "") and '"revision": 1' in (log.after or "")
    with pytest.raises(LedgerError, match="not waiting for approval"):
        intake.approve_revision(db, revised)  # type: ignore[arg-type]


def test_rejecting_a_revision_needs_a_reason_and_leaves_the_order_alone(db: Session, boss: AppUser) -> None:
    customer = globex(db)
    original = intake.file_po(db, po(customer_id=customer.id), actor=boss).po
    revised = intake.file_po(db, po(price="110", customer_id=customer.id), actor=boss).po
    with pytest.raises(LedgerError, match="Reason is needed"):
        intake.reject_revision(db, revised, " ", actor=boss)  # type: ignore[arg-type]
    intake.reject_revision(db, revised, "Customer confirmed the old price", actor=boss)  # type: ignore[arg-type]
    assert (revised.state, original.state) == ("rejected", "active")  # type: ignore[union-attr]
    assert intake.file_po(db, po(price="110", customer_id=customer.id), actor=boss).kind == "skipped"  # not asked again
    log = db.scalars(select(AuditLog).where(AuditLog.action == "customer_po.reject")).one()
    assert "Customer confirmed the old price" in (log.after or "")


def test_a_revision_made_from_an_old_page_is_refused(db: Session, boss: AppUser) -> None:
    customer = globex(db)
    intake.file_po(db, po(customer_id=customer.id), actor=boss)
    revised = intake.file_po(db, po(price="110", customer_id=customer.id), actor=boss).po
    with pytest.raises(LedgerError, match="changed by someone else"):
        intake.approve_revision(db, revised, actor=boss, seen=revised.version + 5, check=True)  # type: ignore[union-attr]


# ---- checking the data


def refused(db: Session, data: PoData, message: str) -> None:
    with pytest.raises(LedgerError, match=message):
        intake.file_po(db, data)


def test_the_data_is_checked_with_the_purchasing_rules(db: Session) -> None:
    customer = globex(db)
    base = {"customer_id": customer.id}
    refused(db, po(" ", **base), "PO number is needed")
    refused(db, po("---", **base), "at least one letter or digit")
    refused(db, po(currency="usd1", **base), "Currency")
    refused(db, po(source="telepathy", **base), "Source")
    refused(db, po(lines=(), **base), "at least one line")
    refused(db, po(qty="0", **base), "Quantity must be more than zero")
    refused(db, po(price="-1", **base), "cannot be below zero")
    refused(db, po(qty="1.00001", **base), "nothing is rounded")
    refused(db, po(price="0.0000001", **base), "nothing is rounded")
    refused(db, po(stated_total=Decimal("0.0000001"), **base), "Total")
    two = LineData(item="A", qty=Decimal(1), unit_price=Decimal(1), line_no=1)
    refused(db, po(lines=(two, two), **base), "used once")
    refused(db, po(lines=(LineData(item="", qty=Decimal(1), unit_price=Decimal(1)),), **base), "Item is needed")
    refused(db, po(lines=(LineData(item="x" * 101, qty=Decimal(1), unit_price=Decimal(1)),), **base), "longer than 100")
    assert db.scalar(select(c.CustomerPO.id)) is None  # nothing was filed by any of them


def test_a_free_line_and_a_line_with_no_unit_are_allowed_and_lines_are_numbered_when_left_out(db: Session) -> None:
    customer = globex(db)
    free = (
        LineData(item="SAMPLE", qty=Decimal(1), unit_price=Decimal(0)),
        LineData(item="X", qty=Decimal(2), unit_price=Decimal(3)),
    )
    order = intake.file_po(db, po(lines=free, customer_id=customer.id)).po
    assert [
        x.line_no for x in db.scalars(select(c.CustomerPOLine).where(c.CustomerPOLine.customer_po_id == order.id))
    ] == [1, 2]  # type: ignore[union-attr]


def test_a_stated_line_amount_must_equal_quantity_times_price_to_the_cent(db: Session) -> None:
    customer = globex(db)

    def with_amount(amount: str) -> PoData:
        line = LineData(item="W", qty=Decimal("10"), unit_price=Decimal("2.5"), stated_amount=Decimal(amount))
        return po(lines=(line,), customer_id=customer.id)

    refused(db, with_amount("25.02"), "not quantity times price \\(25.0\\)")
    refused(db, with_amount("24.9"), "Nothing is guessed")
    assert intake.file_po(db, with_amount("25.01")).kind == "created"  # within the one-cent tolerance


def test_a_stated_total_that_is_not_the_sum_of_the_lines_is_kept_as_written_and_flagged(db: Session) -> None:
    customer = globex(db)
    result = intake.file_po(db, po(customer_id=customer.id, stated_total=Decimal("1200")))  # the lines come to 1100
    assert result.kind == "created" and result.po.stated_total == Decimal("1200")  # type: ignore[union-attr]
    assert result.warnings == ["The total on the PO (1200) is not the sum of its lines (1100)."]
    assert intake.file_po(db, po("CP-2", customer_id=customer.id, stated_total=Decimal("1100.00"))).warnings == []


def test_checking_changes_nothing_in_the_database(db: Session) -> None:
    customer = globex(db)
    db.commit()
    before = actions(db)
    found = intake.classify(db, intake.check(db, po(customer_id=customer.id)))
    assert found.kind == "created" and found.customer == customer
    assert db.scalar(select(c.CustomerPO.id)) is None and actions(db) == before


# ---- finding the customer


def test_the_customer_is_found_by_choice_then_exact_name_then_domain(db: Session, boss: AppUser) -> None:
    customer = globex(db)
    other = service.create_customer(db, name="Initech", domains="initech.test")
    assert (
        intake.resolve_customer(db, po(customer_id=other.id, customer_name="Globex", sender_domain="globex.test"))
        == other
    )
    assert intake.resolve_customer(db, po(customer_name="Globex Racing Inc", sender_domain="initech.test")) == customer
    assert intake.resolve_customer(db, po(customer_name="Unknown Co", sender_domain="initech.test")) == other
    assert intake.resolve_customer(db, po(customer_name="Globex Rac")) is None  # close is not a match
    assert intake.resolve_customer(db, po(sender_domain="not a domain")) is None
    with pytest.raises(LedgerError, match="not found"):
        intake.resolve_customer(db, po(customer_id=9999))


# ---- waiting for a customer


def test_a_po_for_an_unknown_customer_waits_as_it_came_and_files_nothing(db: Session, boss: AppUser) -> None:
    globex(db)
    data = po(
        customer_name="Brand New Customer",
        po_date=date(2026, 11, 3),
        stated_total=Decimal("1100"),
        source="document",
        source_ref="msg 7",
    )
    result = intake.file_po(db, data, actor=boss)
    db.commit()
    waiting = result.intake
    assert result.kind == "waiting" and waiting is not None and result.po is None
    assert waiting.state == "waiting" and "Brand New Customer" in waiting.reason and waiting.source == "document"
    assert db.scalar(select(c.CustomerPO.id)) is None
    assert intake.from_json(waiting.payload) == intake.check(db, data)  # exactly as it came, numbers exact
    assert actions(db)[-1] == "customer_po.waiting"


def test_a_waiting_po_is_filed_when_a_person_picks_a_customer(db: Session, boss: AppUser) -> None:
    customer = globex(db)
    waiting = intake.file_po(db, po(customer_name="Brand New Customer"), actor=boss).intake
    result = intake.resolve_waiting(db, waiting, customer_id=customer.id, actor=boss)  # type: ignore[arg-type]
    db.commit()
    assert result.kind == "created" and result.po.customer_id == customer.id  # type: ignore[union-attr]
    assert (waiting.state, waiting.resolved_by, waiting.customer_po_id) == ("done", boss.id, result.po.id)  # type: ignore[union-attr]
    with pytest.raises(LedgerError, match="not waiting"):
        intake.resolve_waiting(db, waiting, customer_id=customer.id)  # type: ignore[arg-type]


def test_a_waiting_po_can_make_its_customer_and_is_still_checked(db: Session, boss: AppUser) -> None:
    waiting = intake.file_po(db, po(customer_name="Brand New Customer"), actor=boss).intake
    with pytest.raises(LedgerError, match="Choose a customer"):
        intake.resolve_waiting(db, waiting)  # type: ignore[arg-type]
    result = intake.resolve_waiting(db, waiting, new_customer_name="Brand New Customer", actor=boss)  # type: ignore[arg-type]
    assert result.kind == "created" and service.find_by_name(db, "Brand New Customer") is not None
    assert "customer.create" in actions(db) and "customer_po.waiting_resolve" in actions(db)


def test_a_waiting_po_that_is_a_copy_resolves_to_the_existing_order(db: Session, boss: AppUser) -> None:
    customer = globex(db)
    existing = intake.file_po(db, po(customer_id=customer.id), actor=boss).po
    waiting = intake.file_po(db, po(customer_name="Nobody"), actor=boss).intake
    result = intake.resolve_waiting(db, waiting, customer_id=customer.id, actor=boss)  # type: ignore[arg-type]
    assert result.kind == "skipped" and waiting.customer_po_id == existing.id  # type: ignore[union-attr]


def test_a_waiting_po_can_be_rejected_with_a_reason(db: Session, boss: AppUser) -> None:
    waiting = intake.file_po(db, po(customer_name="Spam Co"), actor=boss).intake
    with pytest.raises(LedgerError, match="Reason is needed"):
        intake.reject_waiting(db, waiting, "")  # type: ignore[arg-type]
    intake.reject_waiting(db, waiting, "Not one of our customers", actor=boss)  # type: ignore[arg-type]
    assert waiting.state == "rejected" and db.scalar(select(c.CustomerPO.id)) is None
    with pytest.raises(LedgerError, match="not waiting"):
        intake.reject_waiting(db, waiting, "again")  # type: ignore[arg-type]


def test_the_defaults_are_used_for_the_tolerance(db: Session) -> None:
    customer = globex(db)
    db.get(c.Setting, "amount_tolerance").value = "0"  # type: ignore[union-attr]
    db.flush()
    line = LineData(item="W", qty=Decimal("10"), unit_price=Decimal("2.5"), stated_amount=Decimal("25.01"))
    refused(db, po(lines=(line,), customer_id=customer.id), "not quantity times price")
    assert config.decimal(db, "amount_tolerance") == 0
