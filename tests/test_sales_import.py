"""The QuickBooks sales import (PD-8.3, DEC-58): reading a made-up export, previewing it, and keeping it without ever
overwriting what was accepted. Every file here is synthetic."""

from __future__ import annotations

import io
import json
from collections.abc import Iterator
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from openpyxl import Workbook
from sqlalchemy import select
from sqlalchemy.orm import Session

from po_dashboard.customers import service
from po_dashboard.db import customers as c
from po_dashboard.db.models import AppUser, AuditLog
from po_dashboard.intake.store import FileStore
from po_dashboard.ledger.errors import LedgerError
from po_dashboard.quickbooks.importers import mapping as mp
from po_dashboard.quickbooks.importers.sales import ImportProblem, read, read_money
from po_dashboard.quickbooks.snapshots import imports
from test_gate import env  # noqa: F401

HEAD = ",Type,Date,Num,P. O. #,Name,Memo,Item,Qty,Sales Price,Amount"
TITLE = ["RaceSpec Test Co (made up)", "Sales by Customer Detail", "November 2026", ""]
PERIOD = (date(2026, 11, 1), date(2026, 11, 30))


@pytest.fixture
def db(env) -> Iterator[Session]:  # noqa: ANN001, F811
    with Session(env[1]) as session:
        yield session


@pytest.fixture
def boss(db: Session) -> AppUser:
    return db.scalar(select(AppUser).where(AppUser.email == "boss@example.com"))  # type: ignore[return-value]


@pytest.fixture
def store(tmp_path: Path) -> FileStore:
    return FileStore(tmp_path / "originals")


def report(*rows: str, title: bool = True, head: str = HEAD) -> bytes:
    lines = [*(TITLE if title else []), head, *rows]
    return ("\n".join(lines) + "\n").encode()


def line(num: str, item: str, qty: str, price: str, amount: str, *, kind: str = "Invoice", day: str = "11/03/2026",
         po: str = "CP-1001", who: str = "Globex Racing", memo: str = "") -> str:  # fmt: skip
    return f",{kind},{day},{num},{po},{who},{memo},{item},{qty},{price},{amount}"


GOOD = report(
    "Globex Racing",
    line("2001", "Bolts:M8", "10", "2.50", "25.00"),
    line("2001", "Washers:M8", "100", "0.015", "1.50"),
    "Total Globex Racing,,,,,,,,,,26.50",
    "Initech Corp",
    line("2002", "Gear:G1", "4", "25", "100.00", who="Initech Corp", po="IN-7", day="11/05/2026"),
    "Total Initech Corp,,,,,,,,,,100.00",
)


def parsed(raw: bytes, db: Session, mapping: mp.Mapping | None = None):  # noqa: ANN201
    return imports.build_plan(db, raw, "sales.csv", mapping)


def pair(plan: imports.ImportPlan) -> dict[str, tuple[str, int, Decimal]]:
    return {i.group.primary.number: (i.outcome, len(i.group.primary.lines), i.group.primary.total) for i in plan.items}


# ---- the mapping


def test_the_columns_are_found_by_name_below_title_rows_and_a_leading_empty_column(db: Session) -> None:
    table = imports.read_file(GOOD, "sales.csv")
    assert table[2] == 5  # the column names are on row 5 (four title rows above)
    detected = mp.detect(table[1])
    assert detected.fields == {"type": "Type", "number": "Num", "date": "Date", "customer": "Name",
                               "po_reference": "P. O. #", "item": "Item", "description": "Memo", "qty": "Qty",
                               "unit_price": "Sales Price", "amount": "Amount"}  # fmt: skip
    assert detected.level == "line" and detected.preset == "sales_by_customer_detail" and detected.missing() == []


def test_the_order_of_columns_does_not_matter_and_names_are_compared_loosely(db: Session) -> None:
    raw = b"Amount,Memo,P.O. #,Name,Num,Date,Type\n25.00,x,CP-1,Globex Racing,2001,11/03/2026,Invoice\n"
    plan = parsed(raw, db)
    assert plan.parsed.mapping.level == "invoice" and pair(plan) == {"2001": ("new", 0, Decimal("25.00"))}
    assert plan.items[0].group.primary.po_reference == "CP-1"


def test_a_file_that_is_not_a_sales_export_is_refused_in_plain_words(db: Session) -> None:
    for raw in (b"a,b,c\n1,2,3\n", b"customer,po_number\nA,B\n", b""):
        with pytest.raises(ImportProblem, match="does not look like a sales export"):
            parsed(raw, db)
    with pytest.raises(ImportProblem, match="could not be opened"):
        imports.read_file(b"PK\x03\x04 broken", "x.xlsx")
    with pytest.raises(ImportProblem, match="macros"):
        imports.read_file(b"x", "sales.xlsm")


def test_a_required_column_that_is_not_set_is_named(db: Session) -> None:
    table, headers, _ = imports.read_file(GOOD, "sales.csv")
    without = mp.Mapping({k: v for k, v in mp.detect(headers).fields.items() if k != "amount"})
    with pytest.raises(ImportProblem, match="Amount is not set|for Amount"):
        read(table, without, default_currency="USD", charge_names=frozenset())
    ghost = mp.Mapping({**mp.detect(headers).fields, "item": "No Such Column"})
    with pytest.raises(ImportProblem, match="which the file lacks"):
        read(table, ghost, default_currency="USD", charge_names=frozenset())


def test_a_mapping_chosen_in_the_preview_is_checked_against_the_file(db: Session) -> None:
    headers = ["Type", "Date", "Num", "Name", "Amount"]
    chosen, problems = mp.from_form({"number": "Num", "date": "Date", "amount": "Amount", "customer": "Name"}, headers)
    assert problems == [] and chosen.fields["customer"] == "Name"
    _, bad = mp.from_form({"number": "Nope", "date": "Date", "amount": "Date"}, headers)
    assert any("no column called Nope" in p for p in bad) and any("cannot be both" in p for p in bad)


def test_a_saved_mapping_is_a_setting_that_suits_only_a_file_that_has_its_columns(db: Session) -> None:
    detected = mp.detect(["Type", "Date", "Num", "Name", "Amount"])
    mp.save_profile(db, "Invoice List", detected)
    assert mp.profiles(db) == {"invoice list": detected}
    assert mp.usable(detected, ["Type", "Date", "Num", "Name", "Amount", "Memo"]) and not mp.usable(detected, ["Date"])
    with pytest.raises(ValueError, match="letters, digits"):
        mp.save_profile(db, "bad/name", detected)


# ---- reading the rows


def test_headings_totals_and_the_title_are_recognised_and_every_row_is_accounted_for(db: Session) -> None:
    plan = parsed(GOOD, db)
    outcomes = {r.row_number: r.outcome for r in plan.parsed.rows}
    assert outcomes[1] == outcomes[2] == "heading" and outcomes[6] == "heading"  # title rows and a customer heading
    assert plan.parsed.count("line") == 3 and plan.parsed.count("total") == 2 and plan.parsed.count("unusable") == 0
    assert pair(plan) == {"2001": ("new", 2, Decimal("26.500")), "2002": ("new", 1, Decimal("100.00"))}
    assert plan.parsed.period == (date(2026, 11, 3), date(2026, 11, 5))


def test_a_customer_comes_from_the_heading_row_when_there_is_no_name_column(db: Session) -> None:
    head = ",Type,Date,Num,P. O. #,Memo,Item,Qty,Sales Price,Amount"
    raw = report("Globex Racing", ",Invoice,11/03/2026,2001,CP-1,,Bolt,10,2.50,25.00", "Total Globex Racing,,,,,,,,,25",
                 head=head)  # fmt: skip
    plan = parsed(raw, db)
    assert plan.items[0].group.primary.customer_name == "Globex Racing" and "customer" not in plan.parsed.mapping.fields


def test_a_row_with_no_customer_at_all_is_not_read_and_says_why(db: Session) -> None:
    head = ",Type,Date,Num,Memo,Item,Qty,Sales Price,Amount"
    plan = parsed(report(",Invoice,11/03/2026,2001,,Bolt,10,2.50,25.00", head=head), db)
    assert plan.items == [] and "Customer is missing" in plan.parsed.rows[-1].reason  # type: ignore[operator]


def test_amounts_are_read_as_quickbooks_writes_them_and_exactly(db: Session) -> None:
    assert read_money("$1,234.50", "A") == Decimal("1234.50") and read_money("(50.00)", "A") == Decimal("-50.00")
    assert read_money(" -7 ", "A") == Decimal("-7") and read_money("−3.5", "A") == Decimal("-3.5")
    for bad in ("", "abc", "1.0000001", "1e400", "NaN", "1.2.3"):
        with pytest.raises(LedgerError):
            read_money(bad, "Amount")


def test_a_row_that_cannot_be_read_is_listed_and_the_invoice_it_touches_is_marked_for_review(db: Session) -> None:
    raw = report(line("2001", "Bolt", "10", "2.50", "25.00"), line("2001", "Nut", "abc", "1", "5.00"),
                 line("2003", "Gear", "1", "1", "1", day="not a date"))  # fmt: skip
    plan = parsed(raw, db)
    rows = {r.row_number: (r.outcome, r.reason) for r in plan.parsed.rows}
    assert rows[6][0] == "line" and rows[7][0] == "unusable" and "Quantity is not a number" in (rows[7][1] or "")
    assert rows[8][0] == "unusable" and "Invoice date" in (rows[8][1] or "")
    (only,) = plan.items
    assert only.group.primary.number == "2001" and "a line could not be read" in only.group.primary.review[0]


def test_credit_memos_are_told_from_invoices_and_payments_are_not_sales_documents(db: Session) -> None:
    raw = report(
        line("2001", "Bolt", "10", "2.50", "25.00"),
        line("C-9", "Bolt", "-2", "2.50", "-5.00", kind="Credit Memo"),
        line("P-1", "", "", "", "30.00", kind="Payment"),
        line("2001", "Nut", "1", "1", "1.00", kind="Invoice"),
    )
    plan = parsed(raw, db)
    kinds = {i.group.primary.number: i.group.primary.kind for i in plan.items}
    assert kinds == {"2001": "invoice", "C-9": "credit_memo"}
    assert plan.items[1].group.primary.total == Decimal("-5.00")  # kept as exported, never flipped
    assert "not a sales document this import reads: Payment" in plan.parsed.unreadable_reasons()


def test_charges_discounts_and_tax_are_lines_of_their_own_kind_and_never_goods(db: Session) -> None:
    raw = report(line("2001", "Bolt", "10", "2.50", "25.00"), line("2001", "Shipping", "", "", "5.00"),
                 line("2001", "Sales Tax", "", "", "2.00"), line("2001", "Discount", "", "", "-1.00"),
                 line("2001", "Fuel Surcharge", "", "", "0.50"), line("2001", "Gizmo", "1", "3", "3.00"))  # fmt: skip
    (only,) = parsed(raw, db).items
    assert [ln.kind for ln in only.group.primary.lines] == ["goods", "freight", "tax", "discount", "freight", "goods"]
    assert only.group.primary.total == Decimal("34.50")


def test_a_line_with_an_amount_and_no_item_or_quantity_waits_for_a_person(db: Session) -> None:
    (only,) = parsed(report(line("2001", "Bolt", "10", "2.50", "25.00"), line("2001", "", "", "", "9.00")), db).items
    assert only.group.primary.lines[1].kind == "other" and "no item or quantity" in only.group.primary.review[0]


def test_the_charge_names_are_a_setting(db: Session) -> None:
    db.get(c.Setting, "charge_items").value = "bolts:m8"  # type: ignore[union-attr]
    db.flush()
    (only,) = parsed(report(line("2001", "Bolts:M8", "10", "2.50", "25.00")), db).items
    assert only.group.primary.lines[0].kind == "other"


def test_the_currency_is_a_column_or_the_default_and_a_bad_one_is_not_read(db: Session) -> None:
    head = HEAD + ",Currency"
    raw = report(line("2001", "Bolt", "1", "1", "1") + ",EUR", line("2002", "Bolt", "1", "1", "1") + ",", head=head)
    plan = parsed(raw, db)
    assert [i.group.primary.currency for i in plan.items] == ["EUR", "USD"]
    bad = parsed(report(line("2003", "Bolt", "1", "1", "1") + ",EURO", head=head), db)
    assert bad.items == [] and "three-letter code" in (bad.parsed.rows[-1].reason or "")


def test_several_po_references_on_one_invoice_are_kept_as_exported_and_never_corrected(db: Session) -> None:
    raw = report(line("2001", "Bolt", "1", "1", "1", po="CP-1"), line("2001", "Nut", "1", "1", "1", po="CP-2"),
                 line("2001", "Gear", "1", "1", "1", po=""))  # fmt: skip
    (only,) = parsed(raw, db).items
    assert only.group.primary.po_reference == "CP-1; CP-2"


def test_invoice_numbers_group_whatever_their_case_and_spacing(db: Session) -> None:
    raw = report(line("inv-1", "Bolt", "1", "1", "1"), line(" INV-1 ", "Nut", "1", "1", "1"))
    (only,) = parsed(raw, db).items
    assert len(only.group.primary.lines) == 2 and only.group.primary.key == "inv-1"


def test_a_whole_invoice_listed_twice_is_counted_once_and_noted(db: Session) -> None:
    rows = [line("2001", "Bolt", "10", "2.50", "25.00"), line("2001", "Nut", "1", "1", "1.00")]
    plan = parsed(report(*rows, *rows), db)
    (only,) = plan.items
    assert len(only.group.primary.lines) == 2 and only.group.primary.total == Decimal("26.00")
    assert "the whole invoice appears twice in the file" in only.group.primary.notes and not only.group.primary.review
    assert plan.parsed.count("unusable") == 2 and "counted once" in plan.parsed.rows[-1].reason  # type: ignore[operator]


def test_an_invoice_level_row_repeated_exactly_is_counted_once_and_a_different_amount_is_a_conflict(
    db: Session,
) -> None:
    head = ",Type,Date,Num,P. O. #,Name,Memo,Amount"
    same = report(
        ",Invoice,11/03/2026,9001,CP-1,Globex Racing,,100.00",
        ",Invoice,11/03/2026,9001,CP-1,Globex Racing,,100.00",
        head=head,
    )
    (only,) = parsed(same, db).items
    assert (
        only.group.primary.total == Decimal("100.00")
        and "a row appears more than once in the file" in only.group.primary.notes
    )
    clash = report(
        ",Invoice,11/03/2026,9001,CP-1,Globex Racing,,100.00",
        ",Invoice,11/03/2026,9001,CP-1,Globex Racing,,250.00",
        head=head,
    )
    (two,) = parsed(clash, db).items
    assert (
        two.outcome == "conflicting" and len(two.group.others) == 1 and two.group.others[0].total == Decimal("250.00")
    )
    assert "share this number" in two.group.primary.review[0]


def test_two_line_level_invoices_with_one_number_for_different_customers_conflict(db: Session) -> None:
    raw = report(line("2001", "Bolt", "1", "1", "1"), line("2001", "Gear", "1", "1", "1", who="Initech Corp"))
    (only,) = parsed(raw, db).items
    assert only.outcome == "conflicting" and only.group.others[0].customer_name == "Initech Corp"


def test_quantities_prices_and_totals_of_a_line_level_invoice_are_exact(db: Session) -> None:
    (only,) = parsed(report(line("2001", "Bolt", "2.5", "0.000001", "0.000003")), db).items
    ln = only.group.primary.lines[0]
    assert (ln.qty, ln.unit_price, ln.amount) == (Decimal("2.5"), Decimal("0.000001"), Decimal("0.000003"))


def test_a_formula_in_a_cell_is_text_and_nothing_is_evaluated(db: Session) -> None:
    (only,) = parsed(report(line("2001", '=HYPERLINK("http://x.test")', "1", "1", "1", memo="=1+1")), db).items
    assert (
        only.group.primary.lines[0].item == '=HYPERLINK("http://x.test")'
        and only.group.primary.lines[0].description == "=1+1"
    )


def test_an_excel_file_reads_like_a_csv_file(db: Session) -> None:
    book = Workbook()
    sheet = book.active
    for row in GOOD.decode().splitlines():
        sheet.append(row.split(","))
    out = io.BytesIO()
    book.save(out)
    plan = imports.build_plan(db, out.getvalue(), "sales.xlsx")
    assert pair(plan) == pair(parsed(GOOD, db))


# ---- the plan against what the app holds


def test_the_plan_changes_nothing_and_says_which_invoices_are_new(db: Session, store: FileStore) -> None:
    db.commit()
    plan = parsed(GOOD, db)
    assert [i.outcome for i in plan.items] == ["new", "new"] and plan.earlier is None
    assert db.scalar(select(c.SalesImport.id)) is None and db.scalar(select(c.SalesInvoice.id)) is None
    assert not any(store.root.glob("*/*")) if store.root.exists() else True


def test_unknown_customer_names_are_counted_and_known_ones_are_found_exactly(db: Session) -> None:
    service.create_customer(db, name="Globex Racing", qb_names="Globex Racing:Season")
    plan = parsed(GOOD, db)
    assert plan.unknown_names == {"Initech Corp": 1}
    assert [i.customer.name if i.customer else None for i in plan.items] == ["Globex Racing", None]
    job = parsed(report(line("2005", "Bolt", "1", "1", "1", who="Globex Racing:Season 2027")), db)
    assert job.items[0].customer is not None and not job.unknown_names  # the Customer:Job rule


# ---- keeping it


def keep(
    db: Session, store: FileStore, raw: bytes = GOOD, name: str = "sales.csv", **more: object
) -> imports.ImportReport:
    values: dict[str, object] = {"period_from": PERIOD[0], "period_to": PERIOD[1]}
    return imports.apply_import(db, store, raw, name, None, **{**values, **more})  # type: ignore[arg-type]


def test_an_upload_keeps_the_file_every_row_the_period_and_a_first_sighting_of_each_invoice(
    db: Session, store: FileStore, boss: AppUser
) -> None:
    done = keep(db, store, actor=boss, client_address="127.0.0.1", save_mapping_as="detail")
    db.commit()
    batch = db.scalars(select(c.SalesImport)).one()
    assert (batch.file_name, batch.period_from, batch.period_to, batch.created_by) == (
        "sales.csv",
        PERIOD[0],
        PERIOD[1],
        boss.id,
    )
    assert store.read(batch.stored_path) == GOOD and json.loads(batch.mapping)["amount"] == "Amount"
    kept = [ln for ln in GOOD.decode().splitlines() if ln.strip()]
    assert len(list(db.scalars(select(c.SalesImportRow)))) == len(kept)  # every row that has anything in it
    invoices = {i.invoice_number: i for i in db.scalars(select(c.SalesInvoice))}
    assert (
        set(invoices) == {"2001", "2002"}
        and invoices["2001"].total == Decimal("26.5")
        and invoices["2001"].state == "accepted"
    )
    assert [o.outcome for o in db.scalars(select(c.SalesInvoiceObservation))] == ["first", "first"]
    assert len(list(db.scalars(select(c.SalesInvoiceLine)))) == 3 and done.counts["new"] == 2
    log = db.scalars(select(AuditLog).where(AuditLog.action == "sales_import.apply")).one()
    assert log.user_id == boss.id and '"new": 2' in (log.after or "") and "2026-11-30" in (log.after or "")
    assert "detail" in mp.profiles(db)


def test_the_same_file_again_is_refused_and_adds_nothing(db: Session, store: FileStore) -> None:
    keep(db, store)
    db.commit()
    with pytest.raises(LedgerError, match="already imported on"):
        keep(db, store)
    assert (
        len(list(db.scalars(select(c.SalesImport)))) == 1
        and len(list(db.scalars(select(c.SalesInvoiceObservation)))) == 2
    )


def test_the_period_is_needed_and_must_run_forwards_and_a_file_with_no_invoice_is_refused(
    db: Session, store: FileStore
) -> None:
    with pytest.raises(LedgerError, match="State the dates"):
        keep(db, store, period_from=None)
    with pytest.raises(LedgerError, match="starts after it ends"):
        keep(db, store, period_from=PERIOD[1], period_to=PERIOD[0])
    with pytest.raises(LedgerError, match="Nothing in the file can be imported"):
        keep(db, store, raw=report(line("P-1", "", "", "", "9", kind="Payment")))
    assert db.scalar(select(c.SalesImport.id)) is None and not store.root.exists()  # not even the file was kept


def test_a_later_file_with_the_same_invoice_adds_a_sighting_and_changes_nothing(db: Session, store: FileStore) -> None:
    keep(db, store)
    db.commit()
    later = report(line("2001", "Bolts:M8", "10", "2.50", "25.00"), line("2001", "Washers:M8", "100", "0.015", "1.50"),
                   line("2003", "Gear", "1", "9", "9.00"))  # fmt: skip
    done = keep(db, store, raw=later, name="later.csv")
    db.commit()
    assert done.counts["same"] == 1 and done.counts["new"] == 1
    outcomes = [
        (o.invoice_id, o.outcome)
        for o in db.scalars(select(c.SalesInvoiceObservation).order_by(c.SalesInvoiceObservation.id))
    ]
    assert (
        outcomes.count((1, "same")) == 1
        and db.scalars(select(c.SalesInvoice).where(c.SalesInvoice.invoice_key == "2001")).one().state == "accepted"
    )


def test_an_invoice_that_changed_keeps_its_accepted_content_and_waits_for_a_person(
    db: Session, store: FileStore
) -> None:
    keep(db, store)
    db.commit()
    changed = report(
        line("2001", "Bolts:M8", "10", "3.00", "30.00"), line("2001", "Washers:M8", "100", "0.015", "1.50")
    )
    done = keep(db, store, raw=changed, name="edited.csv")
    db.commit()
    assert done.counts["changed"] == 1
    invoice = db.scalars(select(c.SalesInvoice).where(c.SalesInvoice.invoice_key == "2001")).one()
    assert invoice.state == "needs_review" and invoice.total == Decimal("26.5")  # the accepted figures stand
    line_one = db.scalars(
        select(c.SalesInvoiceLine).where(c.SalesInvoiceLine.invoice_id == invoice.id, c.SalesInvoiceLine.position == 1)
    ).one()
    assert line_one.unit_price == Decimal("2.5")
    seen = db.scalars(select(c.SalesInvoiceObservation).where(c.SalesInvoiceObservation.outcome == "changed")).one()
    assert json.loads(seen.content)["record"]["total"] == "31.5"  # what the new file said is kept beside it


def test_two_invoices_with_one_number_are_kept_for_review_with_both_sightings(db: Session, store: FileStore) -> None:
    raw = report(line("2001", "Bolt", "1", "1", "1"), line("2001", "Gear", "1", "1", "1", who="Initech Corp"))
    done = keep(db, store, raw=raw)
    db.commit()
    assert done.counts["conflicting"] == 1
    invoice = db.scalars(select(c.SalesInvoice)).one()
    assert invoice.state == "needs_review" and invoice.customer_name_exported == "Globex Racing"
    assert sorted(o.outcome for o in db.scalars(select(c.SalesInvoiceObservation))) == ["conflicting", "first"]


def test_a_credit_memo_and_an_invoice_may_share_a_number(db: Session, store: FileStore) -> None:
    keep(
        db,
        store,
        raw=report(line("77", "Bolt", "1", "1", "1"), line("77", "Bolt", "-1", "1", "-1", kind="Credit Memo")),
    )
    db.commit()
    assert sorted(i.kind for i in db.scalars(select(c.SalesInvoice))) == ["credit_memo", "invoice"]


def test_an_invoice_the_export_marks_for_review_is_kept_for_review(db: Session, store: FileStore) -> None:
    keep(db, store, raw=report(line("2001", "Bolt", "10", "2.50", "25.00"), line("2001", "Nut", "abc", "1", "5.00")))
    db.commit()
    assert db.scalars(select(c.SalesInvoice)).one().state == "needs_review"


def test_a_failure_part_way_keeps_nothing_once_rolled_back(
    db: Session, store: FileStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = imports._create
    calls = []

    def flaky(*args: object, **kwargs: object) -> c.SalesInvoice:
        calls.append(1)
        if len(calls) == 2:
            raise RuntimeError("the disk filled")
        return real(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(imports, "_create", flaky)
    db.commit()
    with pytest.raises(RuntimeError):
        keep(db, store)
    db.rollback()
    assert all(
        db.scalar(select(m.id).limit(1)) is None
        for m in (c.SalesImport, c.SalesImportRow, c.SalesInvoice, c.SalesInvoiceLine)
    )


def test_the_content_compared_is_the_same_from_the_file_and_from_the_database(db: Session, store: FileStore) -> None:
    keep(db, store, raw=report(line("2001", "Bolt", "10.50", "2.500", "26.250"), line("2001", "Shipping", "", "", "5")))
    db.commit()
    again = report(line("2001", "BOLT", "10.5", "2.5", "26.25"), line("2001", "Shipping", "", "", "5.00"))
    assert keep(db, store, raw=again, name="again.csv").counts["same"] == 1  # trailing zeros and case are not a change


# ---- names the app does not know


def test_a_name_the_app_does_not_know_is_mapped_to_a_customer_and_found_by_itself_next_time(
    db: Session, store: FileStore, boss: AppUser
) -> None:
    keep(db, store)
    globex = service.create_customer(db, name="Globex Racing")
    db.commit()
    assert imports.unmapped_names(db) == [("Globex Racing", 1), ("Initech Corp", 1)]
    assert (
        imports.map_name(db, "Globex Racing", customer_id=globex.id, actor=boss) == 1
    )  # its own name: no alias needed
    assert imports.map_name(db, "Initech Corp", new_customer_name="Initech Incorporated", actor=boss) == 1
    db.commit()
    assert imports.unmapped_names(db) == [] and {
        i.customer_id is not None for i in db.scalars(select(c.SalesInvoice))
    } == {True}
    assert service.find_by_name(db, "Initech Corp") is not None  # remembered as a QuickBooks name
    later = parsed(report(line("3001", "Gear", "1", "1", "1", who="Initech Corp")), db)
    assert later.items[0].customer is not None and not later.unknown_names
    assert {"customer.create", "customer.name_add", "sales_invoice.map_customer"} <= {
        a.action for a in db.scalars(select(AuditLog))
    }


def test_mapping_a_name_needs_a_customer_and_cannot_take_a_name_another_customer_has(
    db: Session, store: FileStore
) -> None:
    keep(db, store)
    other = service.create_customer(db, name="Other", qb_names="Initech Corp")
    with pytest.raises(LedgerError, match="Choose a customer"):
        imports.map_name(db, "Initech Corp")
    third = service.create_customer(db, name="Third")
    # "Initech Corp" is already Other's name: mapping it to Third would make one name mean two customers
    with pytest.raises(LedgerError, match="already used for Other"):
        imports.map_name(db, "Initech Corp", customer_id=third.id)
    assert other.id != third.id
