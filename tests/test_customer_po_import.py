"""Importing customer POs from CSV or Excel (PD-8.2, REQ-CO-03, DEC-57): a plan first, then all or nothing."""

from __future__ import annotations

import io
from collections.abc import Iterator
from decimal import Decimal

import pytest
from openpyxl import Workbook
from sqlalchemy import select
from sqlalchemy.orm import Session

from po_dashboard.customers import po_import, service
from po_dashboard.db import customers as c
from po_dashboard.db.models import AppUser, AuditLog
from po_dashboard.ledger.orders import ImportRejected
from test_gate import env  # noqa: F401

HEADER = ",".join(po_import.COLUMNS)


@pytest.fixture
def db(env) -> Iterator[Session]:  # noqa: ANN001, F811
    with Session(env[1]) as session:
        yield session


@pytest.fixture
def boss(db: Session) -> AppUser:
    return db.scalar(select(AppUser).where(AppUser.email == "boss@example.com"))  # type: ignore[return-value]


@pytest.fixture
def example(db: Session) -> c.Customer:
    return service.create_customer(db, name="Example Customer", qb_names="Example Customer Inc")


def csv(*rows: str, header: str = HEADER) -> bytes:
    return ("\n".join([header, *rows]) + "\n").encode()


ROW1 = "Example Customer,CP-1001,USD,1,Hex bolt M8,10,2.50,2026-11-04,26.50,EA,Zinc plated,25.00"
ROW2 = "Example Customer,CP-1001,USD,2,Washer M8,100,0.015,2026-11-04,26.50,EA,,1.50"


def rejected(db: Session, raw: bytes, name: str | None = None) -> ImportRejected:
    with pytest.raises(ImportRejected) as problem:
        po_import.apply(db, raw, name)
    return problem.value


def messages(problem: ImportRejected) -> list[tuple[int | None, str]]:
    return [(e.row, e.message) for e in problem.errors]


def test_the_template_is_a_file_the_import_reads(db: Session, example: c.Customer) -> None:
    plan = po_import.read(db, po_import.TEMPLATE.encode(), "template.csv")
    assert [(p.data.po_number, p.outcome, p.customer, len(p.data.lines)) for p in plan.pos] == [
        ("CP-1001", "created", "Example Customer", 2)
    ]
    assert plan.rows == 2 and plan.ignored_columns == []
    assert plan.pos[0].data.stated_total == Decimal("26.50") and plan.pos[0].warnings == []


def test_reading_a_file_changes_nothing(db: Session, example: c.Customer) -> None:
    db.commit()
    before = db.scalars(select(AuditLog.id)).all()
    po_import.read(db, csv(ROW1, ROW2))
    assert db.scalar(select(c.CustomerPO.id)) is None and db.scalars(select(AuditLog.id)).all() == before


def test_applying_files_each_po_and_audits_the_import(db: Session, example: c.Customer, boss: AppUser) -> None:
    plan = po_import.apply(
        db, csv(ROW1, ROW2), "orders.csv", source_ref="ab/cd", actor=boss, client_address="127.0.0.1"
    )
    db.commit()
    order = db.scalars(select(c.CustomerPO)).one()
    assert (order.state, order.source, order.source_ref, order.created_by) == ("active", "import", "ab/cd", boss.id)
    assert len(list(db.scalars(select(c.CustomerPOLine)))) == 2 and plan.count("created") == 1
    log = db.scalars(select(AuditLog).where(AuditLog.action == "customer_po.import")).one()
    assert log.user_id == boss.id and '"created": 1' in (log.after or "") and '"lines": 2' in (log.after or "")


def test_the_same_file_again_adds_nothing_and_a_changed_price_is_a_pending_revision(
    db: Session, example: c.Customer
) -> None:
    po_import.apply(db, csv(ROW1, ROW2))
    again = po_import.apply(db, csv(ROW1, ROW2))
    assert [p.outcome for p in again.pos] == ["skipped"] and len(list(db.scalars(select(c.CustomerPO)))) == 1
    changed = po_import.apply(
        db, csv(ROW1.replace("2.50", "2.75").replace("25.00", "27.50"), ROW2.replace("26.50", "26.50"))
    )
    assert [p.outcome for p in changed.pos] == ["revision"]
    assert [(o.revision, o.state) for o in db.scalars(select(c.CustomerPO).order_by(c.CustomerPO.revision))] == [
        (0, "active"),
        (1, "pending"),
    ]


def test_a_customer_is_found_by_an_exact_quickbooks_name_but_not_by_a_similar_one(
    db: Session, example: c.Customer
) -> None:
    plan = po_import.read(
        db,
        csv(
            ROW1.replace("Example Customer,", "EXAMPLE customer INC,", 1).replace(
                "Example Customer", "EXAMPLE customer INC"
            )
        ),
    )
    assert (plan.pos[0].outcome, plan.pos[0].customer) == ("created", "Example Customer")
    similar = po_import.read(db, csv(ROW1.replace("Example Customer,", "Example Customers,", 1)))
    assert similar.pos[0].outcome == "waiting" and similar.pos[0].customer is None


def test_an_unknown_customer_waits_and_the_rest_of_the_file_is_filed(db: Session, example: c.Customer) -> None:
    other = "Unknown Co,CP-77,USD,1,Gear,1,10,,,,,"
    plan = po_import.apply(db, csv(ROW1, ROW2, other))
    assert sorted(p.outcome for p in plan.pos) == ["created", "waiting"]
    waiting = db.scalars(select(c.CustomerPOIntake)).one()
    assert waiting.state == "waiting" and waiting.source == "import" and "Unknown Co" in waiting.reason
    assert len(list(db.scalars(select(c.CustomerPO)))) == 1


def test_rows_of_one_po_are_grouped_even_when_the_number_is_spelt_differently(db: Session, example: c.Customer) -> None:
    second = ROW2.replace("CP-1001", "cp 1001").replace("Example Customer", "example customer")
    plan = po_import.read(db, csv(ROW1, second))
    assert len(plan.pos) == 1 and len(plan.pos[0].data.lines) == 2


def test_an_excel_file_is_read_like_a_csv_file(db: Session, example: c.Customer) -> None:
    book = Workbook()
    sheet = book.active
    sheet.append(list(po_import.COLUMNS))
    for row in (ROW1, ROW2):
        sheet.append(row.split(","))
    out = io.BytesIO()
    book.save(out)
    plan = po_import.read(db, out.getvalue(), "orders.xlsx")
    assert [(p.outcome, len(p.data.lines)) for p in plan.pos] == [("created", 2)]


def test_a_formula_in_a_cell_stays_text_and_nothing_is_evaluated(db: Session, example: c.Customer) -> None:
    risky = ROW1.replace("Hex bolt M8", '=HYPERLINK("http://attacker.test")').replace("Zinc plated", "=1+1")
    po_import.apply(db, csv(risky, ROW2))
    line = db.scalars(select(c.CustomerPOLine).where(c.CustomerPOLine.line_no == 1)).one()
    assert line.item == '=HYPERLINK("http://attacker.test")' and line.description == "=1+1"


def test_columns_the_import_does_not_use_are_reported_and_skipped(db: Session, example: c.Customer) -> None:
    raw = csv(ROW1 + ",secret note", ROW2 + ",", header=HEADER + ",Internal Notes")
    assert po_import.read(db, raw).ignored_columns == ["internal notes"]


def test_a_file_with_the_wrong_columns_is_refused_naming_them(db: Session, example: c.Customer) -> None:
    problem = rejected(db, csv("a,b", header="customer,po_number"))
    assert (
        problem.errors[0].row == 1
        and "Missing column: currency, line_no, item, quantity, unit_price" in problem.errors[0].message
    )
    twice = rejected(db, csv(ROW1, header=HEADER + ",item"))
    assert "A column appears twice: item" in twice.errors[0].message
    assert (
        "empty" in rejected(db, b"\n\n").errors[0].message
        and "no rows" in rejected(db, (HEADER + "\n").encode()).errors[0].message
    )


def test_every_problem_is_listed_by_row_and_nothing_is_filed(db: Session, example: c.Customer) -> None:
    good = "Example Customer,CP-1,USD,1,Gear,1,10,,,,,"

    def row(number: str, *, who: str = "Example Customer", currency: str = "USD", line: str = "1", qty: str = "1",
            price: str = "10", date: str = "", amount: str = "") -> str:  # fmt: skip
        return f"{who},{number},{currency},{line},Gear,{qty},{price},{date},,,,{amount}"

    bad = [
        row("CP-2", who=""),
        row(""),
        row("CP-3", currency="usd1"),
        row("CP-4", line="one"),
        row("CP-5", qty="0"),
        row("CP-6", price="-5"),
        row("CP-7", date="not a date"),
        row("CP-8", amount="99"),
        row("CP-9", qty="1.00001"),
    ]
    found = messages(rejected(db, csv(good, *bad)))
    assert [row for row, _ in found] == list(range(3, 12))  # one problem per bad row, in order, none for row 2
    assert "Customer is needed" in found[0][1] and "PO number is needed" in found[1][1] and "Currency" in found[2][1]
    assert "line_no must be a whole number" in found[3][1] and "more than zero" in found[4][1]
    assert db.scalar(select(c.CustomerPO.id)) is None and db.scalar(select(c.CustomerPOIntake.id)) is None  # none of it
    assert db.scalar(select(AuditLog.id).where(AuditLog.action == "customer_po.import")) is None


def test_one_bad_po_stops_the_whole_file(db: Session, example: c.Customer) -> None:
    rejected(db, csv(ROW1, ROW2, "Example Customer,CP-2,USD,1,Gear,0,10,,,,,"))
    assert db.scalar(select(c.CustomerPO.id)) is None


def test_the_rows_of_one_po_must_agree_and_line_numbers_are_used_once(db: Session, example: c.Customer) -> None:
    found = messages(rejected(db, csv(ROW1, ROW2.replace("USD", "EUR"), ROW2.replace("Washer", "Nut"))))
    assert any("currency differs from row 2" in m for _, m in found)
    assert any("used once" in m for _, m in found)


def test_a_line_amount_that_is_not_quantity_times_price_is_refused(db: Session, example: c.Customer) -> None:
    found = messages(rejected(db, csv(ROW1.replace("25.00", "26.00"), ROW2)))
    assert found[0][0] == 2 and "not quantity times price" in found[0][1]


def test_a_total_that_is_not_the_sum_of_the_lines_is_a_warning_not_a_refusal(db: Session, example: c.Customer) -> None:
    plan = po_import.read(db, csv(ROW1.replace("26.50", "30"), ROW2.replace("26.50", "30")))
    assert plan.pos[0].warnings == ["The total on the PO (30) is not the sum of its lines (26.500)."]


def test_an_inactive_customer_is_a_problem_with_its_row(db: Session, example: c.Customer) -> None:
    service.set_active(db, example, False)
    found = messages(rejected(db, csv(ROW1, ROW2)))
    assert found == [(2, "Example Customer is not active. Make it active first, or choose another customer.")]


def test_a_file_that_is_not_a_spreadsheet_is_refused_with_one_plain_message(db: Session) -> None:
    for raw, name in (
        (b"\x00\x01\x02\xff binary", "x.csv"),
        (b"PK\x03\x04 not really a zip", "x.xlsx"),
        (b"x", "macro.xlsm"),
    ):
        problem = rejected(db, raw, name)
        assert len(problem.errors) == 1 and problem.errors[0].row is None


def test_at_most_forty_problems_are_listed_and_the_rest_are_counted(db: Session, example: c.Customer) -> None:
    rows = [f"Example Customer,CP-{n},USD,1,Gear,0,10,,,,," for n in range(60)]
    problem = rejected(db, csv(*rows))
    assert len(problem.errors) == 40 and problem.truncated
