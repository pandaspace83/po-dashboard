"""T-7: the owner's ten reconciliation scenarios and their variants, end to end (PD-8.4, DEC-59, DEC-62).

Each scenario files customer POs through the intake service, uploads a made-up QuickBooks export through the sales
import, runs the reconciliation and checks the status, the exceptions with their level, and what was stored: the app's
allocations, a person's decisions, the history and the audit trail. Every file and name here is synthetic."""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal as D
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from po_dashboard.customers import intake, service
from po_dashboard.customers.intake import LineData, PoData
from po_dashboard.db import customers as c
from po_dashboard.db.models import AppUser, AuditLog
from po_dashboard.intake.store import FileStore
from po_dashboard.ledger.errors import LedgerError
from po_dashboard.quickbooks.reconciliation import decisions, store
from po_dashboard.quickbooks.reconciliation.engine import Order, Result
from po_dashboard.quickbooks.snapshots import imports
from test_customer_pages import file_order, made, order_form, rows
from test_gate import env, post  # noqa: F401
from test_orders_web import client  # noqa: F401
from test_sales_import import boss, db, report  # noqa: F401
from test_sales_pages import keep as upload_page

TODAY = date(2026, 11, 30)
WHOLE_PERIOD = (date(2026, 10, 1), date(2026, 11, 30))
TOTALS_HEAD = ",Type,Date,Num,P. O. #,Name,Memo,Amount"


@pytest.fixture
def files(tmp_path: Path) -> FileStore:
    return FileStore(tmp_path / "originals")


@pytest.fixture
def globex(db: Session) -> int:  # noqa: F811
    return service.create_customer(db, name="Globex Racing").id


def file(
    db: Session,
    number: str,
    *lines: tuple[str, str, str],
    customer: int,
    day: date = date(2026, 10, 1),
    currency: str = "USD",
) -> c.CustomerPO:
    data = PoData(
        number, currency, tuple(LineData(i, D(q), D(p)) for i, q, p in lines), customer_id=customer, po_date=day
    )
    result = intake.file_po(db, data)
    assert result.po is not None, result.kind
    return result.po


def bill(
    num: str,
    item: str,
    qty: str,
    price: str,
    *,
    po: str = "CP-1001",
    day: str = "11/03/2026",
    kind: str = "Invoice",
    who: str = "Globex Racing",
    amount: str | None = None,
) -> str:
    total = amount if amount is not None else str(D(qty or "0") * D(price or "0")) if qty else amount or ""
    return f",{kind},{day},{num},{po},{who},,{item},{qty},{price},{total}"


def totals(num: str, amount: str, *, po: str = "CP-1001", day: str = "11/03/2026", who: str = "Globex Racing") -> str:
    return f",Invoice,{day},{num},{po},{who},,{amount}"


def upload(
    db: Session, files: FileStore, *rows: str, head: str | None = None, period: tuple[date, date] = WHOLE_PERIOD
) -> imports.ImportReport:
    raw = report(*rows, head=head) if head else report(*rows)
    done = imports.apply_import(db, files, raw, "sales.csv", None, period_from=period[0], period_to=period[1])
    store.run(db, cause="upload", cause_ref=done.record.id, today=TODAY)
    return done


def look(db: Session, today: date = TODAY) -> Result:
    return store.current(db, today)


def of(result: Result, order: c.CustomerPO) -> Order:
    return result.orders[order.id]


def codes(order: Order) -> list[tuple[str, str]]:
    return [(p.code, p.level) for p in order.problems]


# ---- Scenario 1: fully invoiced


def test_scenario_1_a_po_billed_exactly_on_line_evidence_is_matched(db: Session, files: FileStore, globex: int) -> None:  # noqa: F811
    po = file(db, "CP-1001", ("Widget", "50", "100"), customer=globex)
    upload(db, files, bill("2001", "Widget", "50", "100"))
    order = of(look(db), po)
    assert order.status == "matched" and order.problems == [] and order.ordered == order.invoiced == D(5000)
    kept = db.scalars(select(c.Allocation)).one()
    assert (kept.basis, kept.state, kept.customer_po_id, kept.qty, kept.amount) == (
        "rule",
        "proposed",
        po.id,
        D(50),
        D(5000),
    )


def test_scenario_1_variant_an_invoice_list_with_totals_only_is_matched_by_amount(
    db: Session, files: FileStore, globex: int
) -> None:  # noqa: F811
    po = file(db, "CP-1001", ("Widget", "50", "100"), customer=globex)
    upload(db, files, totals("9001", "5000.00"), head=TOTALS_HEAD)
    order = of(look(db), po)
    assert order.status == "matched_by_amount" and order.problems == [] and order.invoiced == D(5000)


# ---- Scenario 2: potentially unbilled


def test_scenario_2_an_old_po_with_no_invoice_in_uploads_that_cover_its_time_is_possibly_unbilled(
    db: Session, files: FileStore, globex: int
) -> None:  # noqa: F811
    po = file(db, "CP-1001", ("Widget", "50", "100"), customer=globex, day=date(2026, 10, 1))
    upload(db, files, bill("2001", "Gear", "1", "10", po="CP-9999"))
    order = of(look(db), po)
    assert order.status == "no_invoice" and codes(order) == [("possibly_unbilled", "preliminary")]


def test_scenario_2_variant_uploads_that_miss_part_of_its_time_say_not_found_not_unbilled(
    db: Session, files: FileStore, globex: int
) -> None:  # noqa: F811
    po = file(db, "CP-1001", ("Widget", "50", "100"), customer=globex, day=date(2026, 10, 1))
    upload(db, files, bill("2001", "Gear", "1", "10", po="CP-9999"), period=(date(2026, 11, 1), date(2026, 11, 30)))
    order = of(look(db), po)
    assert codes(order) == [("not_found", "preliminary")] and "10/01/2026 to 10/31/2026" in order.problems[0].message


def test_scenario_2_variant_a_young_open_po_is_never_called_unbilled(
    db: Session, files: FileStore, globex: int
) -> None:  # noqa: F811
    po = file(db, "CP-1001", ("Widget", "50", "100"), customer=globex, day=date(2026, 11, 20))
    upload(db, files, bill("2001", "Gear", "1", "10", po="CP-9999"))
    order = of(look(db), po)
    assert order.status == "no_invoice" and order.problems == []
    later = of(look(db, date(2026, 12, 20)), po)  # 30 days on, the uploads no longer cover its time
    assert codes(later) == [("not_found", "preliminary")]


def test_scenario_2_variant_with_no_upload_at_all_the_po_is_not_covered(db: Session, globex: int) -> None:  # noqa: F811
    po = file(db, "CP-1001", ("Widget", "50", "100"), customer=globex)
    order = of(look(db), po)
    assert order.status == "not_covered" and codes(order) == [("not_found", "preliminary")]


# ---- Scenario 3: partially invoiced


def test_scenario_3_two_invoices_of_2000_and_1500_leave_1500_apparently_remaining(
    db: Session, files: FileStore, globex: int
) -> None:  # noqa: F811
    po = file(db, "CP-1001", ("Widget", "50", "100"), customer=globex)
    upload(db, files, bill("2001", "Widget", "20", "100"), bill("2002", "Widget", "15", "100", day="11/10/2026"))
    order = of(look(db), po)
    assert order.status == "partially_invoiced" and order.invoiced == D(3500) and order.remaining == D(1500)
    assert codes(order) == [("partially_invoiced", "preliminary")] and order.lines[0].remaining_qty == D(15)


def test_scenario_3_variant_by_invoice_totals_it_is_partial_by_amount(
    db: Session, files: FileStore, globex: int
) -> None:  # noqa: F811
    po = file(db, "CP-1001", ("Widget", "50", "100"), customer=globex)
    upload(db, files, totals("9001", "2000.00"), totals("9002", "1500.00", day="11/10/2026"), head=TOTALS_HEAD)
    order = of(look(db), po)
    assert order.status == "partially_invoiced" and order.remaining == D(1500)


# ---- Scenario 4: over-invoiced


def test_scenario_4_invoices_of_5800_against_5000_are_an_800_variance_for_review(
    db: Session, files: FileStore, globex: int
) -> None:  # noqa: F811
    po = file(db, "CP-1001", ("Widget", "50", "100"), customer=globex)
    upload(db, files, totals("9001", "5000.00"), totals("9002", "800.00", day="11/12/2026"), head=TOTALS_HEAD)
    order = of(look(db), po)
    assert order.status == "over_invoiced" and codes(order) == [("amount_mismatch", "preliminary")]
    assert order.problems[0].amount == D(800) and order.remaining == D(-800)


def test_scenario_4_variant_on_line_evidence_the_extra_quantity_is_a_confirmed_discrepancy(
    db: Session, files: FileStore, globex: int
) -> None:  # noqa: F811
    po = file(db, "CP-1001", ("Widget", "50", "100"), customer=globex)
    upload(db, files, bill("2001", "Widget", "50", "100"), bill("2002", "Widget", "8", "100", day="11/12/2026"))
    order = of(look(db), po)
    assert order.status == "over_invoiced" and codes(order) == [("quantity_mismatch", "confirmed")]
    assert order.problems[0].amount == D(800)


# ---- Scenario 5: price mismatch


def test_scenario_5_ten_units_at_110_against_100_is_a_confirmed_100_variance(
    db: Session, files: FileStore, globex: int
) -> None:  # noqa: F811
    po = file(db, "CP-1001", ("Widget", "10", "100"), customer=globex)
    upload(db, files, bill("2001", "Widget", "10", "110"))
    order = of(look(db), po)
    assert order.status == "needs_review" and codes(order) == [("price_mismatch", "confirmed")]
    assert order.problems[0].amount == D(100) and order.problems[0].po_line_id == order.lines[0].line.id


# ---- Scenario 6: duplicates


def test_scenario_6_an_invoice_repeated_in_one_file_in_a_later_file_or_as_the_same_file_is_counted_once(
    db: Session, files: FileStore, globex: int
) -> None:  # noqa: F811
    po = file(db, "CP-1001", ("Widget", "50", "100"), customer=globex)
    rows = (bill("2001", "Widget", "50", "100"),)
    upload(db, files, *rows, *rows)  # the same invoice twice in one file: duplicate rows, not a duplicate invoice
    assert of(look(db), po).status == "matched" and of(look(db), po).invoiced == D(5000)
    upload(db, files, *rows, bill("2009", "Gear", "1", "10", po="CP-7"))  # a later export showing it again
    with pytest.raises(LedgerError, match="already imported"):
        upload(db, files, *rows, bill("2009", "Gear", "1", "10", po="CP-7"))  # the very same file
    order = of(look(db), po)
    assert order.status == "matched" and order.invoiced == D(5000) and order.problems == []
    assert len(list(db.scalars(select(c.Allocation)))) == 1


def test_scenario_6_two_different_invoices_with_one_number_are_a_confirmed_potential_duplicate(
    db: Session, files: FileStore, globex: int
) -> None:  # noqa: F811
    po = file(db, "CP-1001", ("Widget", "50", "100"), customer=globex)
    upload(
        db,
        files,
        bill("2001", "Widget", "50", "100"),
        bill("2001", "Widget", "50", "100", who="Globex Racing", day="11/09/2026"),
    )
    order = of(look(db), po)
    assert ("potential_duplicate", "confirmed") in codes(order) and order.status == "needs_review"
    assert order.invoiced == D(5000)  # the first is counted, never both


# ---- Scenario 7: several invoices per PO


def test_scenario_7_instalments_across_uploads_add_up_once_each(db: Session, files: FileStore, globex: int) -> None:  # noqa: F811
    po = file(db, "CP-1001", ("Widget", "50", "100"), ("Gear", "10", "50"), customer=globex)
    upload(
        db,
        files,
        bill("2001", "Widget", "20", "100"),
        bill("2002", "Gear", "10", "50", day="11/04/2026"),
        period=(date(2026, 10, 1), date(2026, 11, 10)),
    )
    first = of(look(db), po)
    assert first.status == "partially_invoiced" and first.invoiced == D(2500)
    upload(
        db,
        files,
        bill("2002", "Gear", "10", "50", day="11/04/2026"),
        bill("2003", "Widget", "30", "100", day="11/20/2026"),
        period=(date(2026, 11, 1), date(2026, 11, 30)),
    )
    order = of(look(db), po)
    assert order.status == "matched" and order.invoiced == order.ordered == D(5500) and order.problems == []
    assert len(order.invoice_ids) == 3 and len([k for k in order.links]) == 3  # 2002 was in both uploads: once


def test_one_invoice_naming_two_pos_is_split_between_them_by_item(db: Session, files: FileStore, globex: int) -> None:  # noqa: F811
    one = file(db, "CP-1001", ("Widget", "50", "100"), customer=globex)
    two = file(db, "CP-1002", ("Gear", "10", "50"), customer=globex)
    upload(
        db,
        files,
        bill("2001", "Widget", "50", "100", po="CP-1001; CP-1002"),
        bill("2001", "Gear", "10", "50", po="CP-1001; CP-1002"),
    )
    result = look(db)
    assert of(result, one).status == of(result, two).status == "matched" and of(result, two).invoiced == D(500)


# ---- Scenario 8: missing or ambiguous PO reference


def test_scenario_8_an_invoice_with_no_po_reference_waits_and_a_persons_link_places_it(
    db: Session, files: FileStore, globex: int, boss: AppUser
) -> None:  # noqa: F811
    po = file(db, "CP-1001", ("Widget", "50", "100"), customer=globex, day=date(2026, 11, 1))
    upload(db, files, bill("2001", "Widget", "50", "100", po=""))
    result = look(db)
    (problem,) = result.unplaced
    assert problem.code == "unmatched_invoice" and problem.suggestions == (po.id,) and of(result, po).links == []
    invoice = db.scalars(select(c.SalesInvoice)).one()
    with pytest.raises(LedgerError, match="Reason is needed"):
        decisions.link(db, invoice, po, actor=boss)  # the invoice names no PO: this is an override
    decisions.link(db, invoice, po, reason="Customer confirmed by phone", actor=boss)
    order = of(look(db), po)
    assert order.status == "matched" and look(db).unplaced == []
    upload(db, files, bill("2050", "Gear", "1", "10", po="CP-7"))  # a later upload re-runs everything
    assert of(look(db), po).status == "matched"  # the person's decision is never undone
    log = db.scalars(select(AuditLog).where(AuditLog.action == "reconciliation.link")).one()
    assert log.user_id == boss.id and "Customer confirmed by phone" in (log.after or "")


def test_scenario_8_variant_a_reference_to_another_customers_po_is_ambiguous(
    db: Session, files: FileStore, globex: int
) -> None:  # noqa: F811
    other = service.create_customer(db, name="Initech Corp").id
    po = file(db, "CP-1001", ("Widget", "50", "100"), customer=other)
    upload(db, files, bill("2001", "Widget", "50", "100"))  # Globex's invoice names Initech's PO number
    result = look(db)
    assert [p.code for p in result.unplaced] == ["ambiguous_match"] and of(result, po).links == []


def test_scenario_8_variant_an_exported_name_the_app_does_not_know_matches_nothing_until_a_person_chooses(
    db: Session, files: FileStore, globex: int, boss: AppUser
) -> None:  # noqa: F811
    po = file(db, "CP-1001", ("Widget", "50", "100"), customer=globex)
    upload(db, files, bill("2001", "Widget", "50", "100", who="Globex Racing Team"))  # similar, not the same name
    assert of(look(db), po).links == [] and [p.code for p in look(db).unplaced] == ["unmatched_invoice"]
    imports.map_name(db, "Globex Racing Team", customer_id=globex, actor=boss)
    store.run(db, cause="person", today=TODAY)
    assert of(look(db), po).status == "matched"


# ---- Scenario 9: a revised customer PO


def test_scenario_9_a_revision_keeps_the_original_terms_until_approved_and_then_reconciles_the_new_ones(
    db: Session, files: FileStore, globex: int, boss: AppUser
) -> None:  # noqa: F811
    po = file(db, "CP-1001", ("Widget", "50", "100"), customer=globex)
    upload(db, files, bill("2001", "Widget", "50", "100"))
    revised = file(db, "CP-1001", ("Widget", "60", "100"), customer=globex)  # the customer sends more quantity
    store.run(db, cause="po_change", cause_ref=revised.id, today=TODAY)
    assert revised.state == "pending"
    waiting = of(look(db), po)
    assert waiting.status == "needs_review" and codes(waiting) == [("revised_po", "preliminary")]
    assert waiting.ordered == D(5000)  # the approved terms stand
    intake.approve_revision(db, revised, actor=boss)
    store.run(db, cause="revision", cause_ref=revised.id, today=TODAY)
    now = of(look(db), revised)
    assert now.status == "partially_invoiced" and now.ordered == D(6000) and now.remaining == D(1000)
    assert po.state == "superseded" and po.id not in look(db).orders  # the original is kept, not reconciled
    history = [
        (e.customer_po_id, e.status_before, e.status_after, e.cause)
        for e in db.scalars(select(c.ReconciliationEvent).order_by(c.ReconciliationEvent.id))
    ]
    assert history[-1] == (revised.id, "needs_review", "partially_invoiced", "revision")


def test_scenario_9_variant_a_persons_line_decision_follows_the_revision_or_is_shown_as_no_longer_fitting(
    db: Session, files: FileStore, globex: int, boss: AppUser
) -> None:  # noqa: F811
    po = file(db, "CP-1001", ("Widget", "25", "100"), ("Widget", "25", "100"), ("Gear", "1", "5"), customer=globex)
    upload(db, files, bill("2001", "Widget", "25", "100"))
    assert codes(of(look(db), po))[0] == ("ambiguous_match", "preliminary")
    invoice = db.scalars(select(c.SalesInvoice)).one()
    inv_line = db.scalars(select(c.SalesInvoiceLine)).one()
    po_lines = db.scalars(select(c.CustomerPOLine).where(c.CustomerPOLine.customer_po_id == po.id))
    second = sorted(po_lines, key=lambda x: x.line_no)[1]
    decisions.link(db, invoice, po, invoice_line_id=inv_line.id, po_line_id=second.id, actor=boss)  # it names this PO
    assert [f.billed_qty for f in of(look(db), po).lines] == [D(0), D(25), D(0)]
    same_line = file(
        db, "CP-1001", ("Widget", "25", "100"), ("Widget", "25", "100"), ("Gear", "2", "5"), customer=globex
    )
    intake.approve_revision(db, same_line, actor=boss)
    store.run(db, cause="revision", today=TODAY)
    assert [f.billed_qty for f in of(look(db), same_line).lines] == [D(0), D(25), D(0)]  # carried over by line and item
    moved = file(db, "CP-1001", ("Widget", "25", "100"), ("Bolt", "25", "100"), ("Gear", "2", "5"), customer=globex)
    intake.approve_revision(db, moved, actor=boss)
    store.run(db, cause="revision", today=TODAY)
    assert ("stale_decision", "preliminary") in codes(of(look(db), moved))


# ---- Scenario 10: credit memos, discounts, freight and tax


def test_scenario_10_charges_are_apart_from_goods_and_the_net_is_given_when_every_part_is_known(
    db: Session, files: FileStore, globex: int
) -> None:  # noqa: F811
    po = file(db, "CP-1001", ("Widget", "50", "100"), customer=globex)
    upload(
        db,
        files,
        bill("2001", "Widget", "50", "100"),
        bill("2001", "Shipping", "", "", amount="75.00"),
        bill("2001", "Sales Tax", "", "", amount="412.50"),
        bill("2001", "Discount", "", "", amount="-250.00"),
    )
    order = of(look(db), po)
    assert order.status == "matched" and order.invoiced == D(5000) and order.problems == []
    assert order.charges == {"freight": D(75), "tax": D("412.5"), "discount": D(-250)} and order.net == D("5237.5")
    assert order.lines[0].billed_qty == D(50)


def test_scenario_10_a_credit_memo_never_changes_billed_quantities_and_waits_until_a_person_links_it(
    db: Session, files: FileStore, globex: int, boss: AppUser
) -> None:  # noqa: F811
    po = file(db, "CP-1001", ("Widget", "50", "100"), customer=globex)
    upload(
        db,
        files,
        bill("2001", "Widget", "50", "100"),
        bill("C-1", "Widget", "-5", "100", kind="Credit Memo", day="11/09/2026"),
    )
    waiting = of(look(db), po)
    assert (
        codes(waiting) == [("credit_memo", "preliminary")]
        and waiting.net is None
        and waiting.lines[0].billed_qty == D(50)
    )
    memo = db.scalars(select(c.SalesInvoice).where(c.SalesInvoice.kind == "credit_memo")).one()
    decisions.link(db, memo, po, actor=boss)
    order = of(look(db), po)
    assert (
        order.status == "matched"
        and order.credits == D(-500)
        and order.net == D(4500)
        and order.lines[0].billed_qty == D(50)
    )


def test_scenario_10_variant_a_line_nobody_can_classify_makes_the_net_unknown_and_waits(
    db: Session, files: FileStore, globex: int
) -> None:  # noqa: F811
    po = file(db, "CP-1001", ("Widget", "50", "100"), customer=globex)
    upload(db, files, bill("2001", "Widget", "50", "100"), bill("2001", "", "", "", amount="20.00"))
    order = of(look(db), po)
    assert order.net is None and order.status == "needs_review"
    assert codes(order) == [("charge_unclassified", "preliminary"), ("invoice_incomplete", "preliminary")]
    assert order.charges == {"other": D(20)} and order.invoiced == D(5000)


# ---- currency, and what is kept


def test_an_invoice_in_another_currency_is_a_confirmed_mismatch_and_is_not_counted(
    db: Session, files: FileStore, globex: int
) -> None:  # noqa: F811
    po = file(db, "CP-1001", ("Widget", "50", "100"), customer=globex, currency="EUR")
    upload(db, files, bill("2001", "Widget", "50", "100"))  # the export's default currency is USD
    order = of(look(db), po)
    assert (
        codes(order) == [("currency_mismatch", "confirmed")] and order.invoiced == 0 and order.status == "needs_review"
    )


def test_a_run_replaces_the_apps_own_allocations_and_never_a_persons(
    db: Session, files: FileStore, globex: int, boss: AppUser
) -> None:  # noqa: F811
    po = file(db, "CP-1001", ("Widget", "50", "100"), customer=globex)
    other = file(db, "CP-1002", ("Widget", "50", "100"), customer=globex)
    upload(db, files, bill("2001", "Widget", "50", "100"))
    invoice = db.scalars(select(c.SalesInvoice)).one()
    decisions.reject(db, invoice, other, reason="Not this one", actor=boss)
    for _ in range(3):
        store.run(db, cause="po_change", today=TODAY)
    rows = sorted((a.basis, a.state, a.customer_po_id) for a in db.scalars(select(c.Allocation)))
    assert rows == [("person", "rejected", other.id), ("rule", "proposed", po.id)]


def test_history_is_written_only_when_a_status_or_its_exceptions_change(
    db: Session, files: FileStore, globex: int
) -> None:  # noqa: F811
    po = file(db, "CP-1001", ("Widget", "50", "100"), customer=globex)
    store.run(db, cause="po_change", cause_ref=po.id, today=TODAY)
    store.run(db, cause="po_change", today=TODAY)
    upload(db, files, bill("2001", "Widget", "20", "100"))
    upload(db, files, bill("2002", "Widget", "30", "100", day="11/10/2026"))
    events = list(db.scalars(select(c.ReconciliationEvent).order_by(c.ReconciliationEvent.id)))
    assert [(e.status_before, e.status_after, e.cause) for e in events] == [
        (None, "not_covered", "po_change"),
        ("not_covered", "partially_invoiced", "upload"),
        ("partially_invoiced", "matched", "upload"),
    ]
    assert json.loads(events[1].exceptions or "[]")[0][0] == "partially_invoiced"


# ---- a person's decisions: checks and the audit trail


def test_a_decision_is_refused_when_it_cannot_be_right(
    db: Session, files: FileStore, globex: int, boss: AppUser
) -> None:  # noqa: F811
    other = service.create_customer(db, name="Initech Corp").id
    po = file(db, "CP-1001", ("Widget", "50", "100"), customer=globex)
    theirs = file(db, "IN-1", ("Widget", "50", "100"), customer=other)
    euro = file(db, "CP-2001", ("Widget", "50", "100"), customer=globex, currency="EUR")
    upload(db, files, bill("2001", "Widget", "50", "100"), bill("2002", "Widget", "1", "1", who="Unknown Co", po=""))
    invoice, stranger = (
        db.scalars(select(c.SalesInvoice).where(c.SalesInvoice.invoice_number == n)).one() for n in ("2001", "2002")
    )
    for target, words in ((theirs, "different customer"), (euro, "never converted")):
        with pytest.raises(LedgerError, match=words):
            decisions.link(db, invoice, target, reason="x", actor=boss)
    with pytest.raises(LedgerError, match="customer first"):
        decisions.link(db, stranger, po, reason="x", actor=boss)
    with pytest.raises(LedgerError, match="Reason is needed"):
        decisions.reject(db, invoice, po, reason=" ", actor=boss)
    with pytest.raises(LedgerError, match="An amount is given only"):
        decisions.link(db, invoice, po, amount=D(10), actor=boss)
    line = db.scalars(select(c.SalesInvoiceLine).where(c.SalesInvoiceLine.invoice_id == invoice.id)).one()
    with pytest.raises(LedgerError, match="both lines"):
        decisions.link(db, invoice, po, invoice_line_id=line.id, actor=boss)
    rule = db.scalars(select(c.Allocation).where(c.Allocation.basis == "rule")).one()
    with pytest.raises(LedgerError, match="follow the rules"):
        decisions.withdraw(db, rule, actor=boss)
    decisions.link(db, invoice, po, actor=boss)  # it names this PO: no reason needed
    with pytest.raises(LedgerError, match="already decided"):
        decisions.reject(db, invoice, po, reason="changed my mind", actor=boss)


def test_withdrawing_a_decision_lets_the_rules_apply_again_and_is_audited(
    db: Session, files: FileStore, globex: int, boss: AppUser
) -> None:  # noqa: F811
    po = file(db, "CP-1001", ("Widget", "50", "100"), customer=globex)
    upload(db, files, bill("2001", "Widget", "50", "100"))
    invoice = db.scalars(select(c.SalesInvoice)).one()
    row = decisions.reject(db, invoice, po, reason="Wrong job", actor=boss)
    assert of(look(db), po).links == []
    decisions.withdraw(db, row, reason="It was right after all", actor=boss)
    assert of(look(db), po).status == "matched"
    actions = [
        (a.action, a.user_id) for a in db.scalars(select(AuditLog).where(AuditLog.action.like("reconciliation.%")))
    ]
    assert actions == [("reconciliation.reject", boss.id), ("reconciliation.withdraw", boss.id)]
    person = select(c.ReconciliationEvent).where(c.ReconciliationEvent.cause == "person")
    assert [(e.status_before, e.status_after) for e in db.scalars(person)] == [
        ("matched", "no_invoice"),
        ("no_invoice", "matched"),
    ]


# ---- the pages re-run it


def test_filing_a_po_keeping_an_upload_and_approving_a_revision_in_the_app_each_re_run_it(
    client: TestClient, env
) -> None:  # noqa: ANN001, F811
    cid = made(env)
    assert file_order(client, **order_form(cid, "CP-1001")).status_code == 303
    assert [(e.status_after, e.cause) for e in rows(env, c.ReconciliationEvent)] == [("not_covered", "po_change")]
    upload_page(client, report(bill("2001", "WIDGET-1", "10", "100"), bill("2001", "WIDGET-2", "5", "20")), "sales.csv")
    assert [(e.status_after, e.cause) for e in rows(env, c.ReconciliationEvent)][-1] == ("matched", "upload")
    assert len([a for a in rows(env, c.Allocation) if a.basis == "rule"]) == 2
    assert file_order(client, **order_form(cid, "CP-1001", price="90")).status_code == 303  # a revision, pending
    assert rows(env, c.ReconciliationEvent)[-1].status_after == "needs_review"
    pending = next(o for o in rows(env, c.CustomerPO) if o.state == "pending")
    assert post(client, f"/customer-orders/{pending.id}/approve", {"version": str(pending.version)}).status_code == 303
    last = rows(env, c.ReconciliationEvent)[-1]
    assert (last.customer_po_id, last.cause, last.status_after) == (
        pending.id,
        "revision",
        "needs_review",
    )  # price now differs
    assert "price_mismatch" in (last.exceptions or "")


def test_a_re_run_on_later_days_adds_no_history_while_nothing_changes(
    db: Session, files: FileStore, globex: int
) -> None:  # noqa: F811
    file(db, "CP-1001", ("Widget", "50", "100"), customer=globex, day=date(2026, 9, 1))
    upload(db, files, bill("2001", "Gear", "1", "10", po="CP-7"), period=(date(2026, 9, 1), date(2026, 11, 30)))
    before = len(list(db.scalars(select(c.ReconciliationEvent))))
    for day in (date(2026, 12, 1), date(2026, 12, 15), date(2027, 1, 2)):
        store.run(db, cause="po_change", today=day)
    assert len(list(db.scalars(select(c.ReconciliationEvent)))) == before
