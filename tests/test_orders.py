"""Vendors, hand-entered POs and the CSV import (PD-1.2): what is accepted, and above all what is refused."""

from collections.abc import Iterator
from decimal import Decimal as D
from pathlib import Path

import pytest
from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session

from po_dashboard.config import Settings
from po_dashboard.db import ledger as L
from po_dashboard.db.migrate import upgrade
from po_dashboard.db.models import AuditLog
from po_dashboard.ledger import orders, vendors
from po_dashboard.ledger.errors import LedgerError
from po_dashboard.ledger.orders import ImportRejected, LineInput, import_csv, parse_line

SAMPLE = Path(__file__).parent / "data" / "purchase_orders_sample.csv"
HEADER = ",".join(orders.COLUMNS)


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
def acme(db: Session) -> L.Vendor:
    return vendors.create_vendor(db, name="Acme Fasteners", domains="acme.com")


def row(**over: str) -> str:
    base = {
        "po_number": "PO-1", "vendor": "Acme Fasteners", "currency": "USD", "line_no": "1", "item": "Bolt",
        "quantity": "10", "unit": "EA", "unit_price": "2.5", "date_sent": "2026-11-04", "buyer": "", "description": "",
        "requested_date": "",
    }  # fmt: skip
    base.update(over)
    return ",".join(base[c] for c in orders.COLUMNS)


def csv_of(*rows: str, header: str = HEADER) -> bytes:
    return ("\n".join([header, *rows]) + "\n").encode()


def rejected(db: Session, raw: bytes) -> ImportRejected:
    with pytest.raises(ImportRejected) as caught:
        import_csv(db, raw)
    assert db.scalar(select(func.count()).select_from(L.PurchaseOrder)) == 0, "a rejected file must write nothing"
    return caught.value


def said(problem: ImportRejected) -> str:
    return " | ".join(f"row {e.row}: {e.message}" for e in problem.errors)


# ---- vendors


def test_a_vendor_is_added_with_clean_domains(db: Session) -> None:
    vendor = vendors.create_vendor(
        db, name="  Acme  ", domains="@ACME.com, mail.acme.com;acme.com", contact_email="a@acme.com"
    )
    assert vendor.name == "Acme"
    assert sorted(db.scalars(select(L.VendorDomain.domain))) == ["acme.com", "mail.acme.com"]


@pytest.mark.parametrize("bad", ["acme", "ac me.com", "http://acme.com", "acme..com", "-acme.com", "a@b.com"])
def test_a_domain_must_look_like_one(db: Session, bad: str) -> None:
    with pytest.raises(LedgerError, match="not an email domain"):
        vendors.create_vendor(db, name="X", domains=bad)


def test_a_vendor_name_is_unique_ignoring_case(db: Session, acme: L.Vendor) -> None:
    with pytest.raises(LedgerError, match="already exists"):
        vendors.create_vendor(db, name="ACME FASTENERS")


def test_a_domain_belongs_to_one_vendor(db: Session, acme: L.Vendor) -> None:
    with pytest.raises(LedgerError, match="already belongs to Acme Fasteners"):
        vendors.create_vendor(db, name="Other", domains="acme.com")
    other = vendors.create_vendor(db, name="Other")
    with pytest.raises(LedgerError, match="already belongs"):
        vendors.add_domains(db, other, "ACME.com")


def test_domains_can_be_added_and_removed_and_only_from_their_own_vendor(db: Session, acme: L.Vendor) -> None:
    assert vendors.add_domains(db, acme, "acme.co.uk acme.com") == ["acme.co.uk"]  # the one already held is skipped
    other = vendors.create_vendor(db, name="Other")
    domain_id = db.scalar(select(L.VendorDomain.id).where(L.VendorDomain.domain == "acme.co.uk"))
    with pytest.raises(LedgerError, match="not found"):
        vendors.remove_domain(db, other, domain_id or 0)
    vendors.remove_domain(db, acme, domain_id or 0)
    assert db.scalar(select(L.VendorDomain.id).where(L.VendorDomain.domain == "acme.co.uk")) is None


def test_vendor_input_is_checked(db: Session) -> None:
    with pytest.raises(LedgerError, match="Name is needed"):
        vendors.create_vendor(db, name="  ")
    with pytest.raises(LedgerError, match="Contact email"):
        vendors.create_vendor(db, name="X", contact_email="not an email")
    with pytest.raises(LedgerError, match="longer than"):
        vendors.create_vendor(db, name="x" * 201)
    with pytest.raises(LedgerError, match="control character"):
        vendors.create_vendor(db, name="Bad\x00Name")


def test_vendor_changes_are_audited_and_an_unchanged_save_is_not(db: Session, acme: L.Vendor) -> None:
    vendors.update_vendor(db, acme, name="Acme Fasteners", contact_name=None, contact_email=None, notes=None)
    before = db.scalar(select(func.count()).select_from(AuditLog).where(AuditLog.action == "vendor.update"))
    assert before == 0
    vendors.update_vendor(db, acme, name="Acme Fasteners", contact_name="Pat", contact_email=None, notes=None)
    entry = db.scalars(select(AuditLog).where(AuditLog.action == "vendor.update")).one()
    assert entry.object == f"vendor:{acme.id}" and "Pat" in (entry.after or "")


# ---- a PO by hand


def test_a_po_is_created_with_its_lines_in_order(db: Session, acme: L.Vendor) -> None:
    lines = [
        parse_line("Line 1", item="Bolt", quantity="10.5", unit="EA", unit_price="0.000001"),
        parse_line("Line 2", item="Nut", quantity="3", unit="EA", unit_price="1", requested_date="2026-12-01"),
    ]
    order = orders.create_po(
        db,
        po_number=" PO-9 ",
        vendor_id=acme.id,
        currency="usd",
        date_sent="2026-11-04",
        buyer="b@example.com",
        lines=lines,
    )
    db.commit()
    assert (order.po_number, order.currency, order.source) == ("PO-9", "USD", "manual")
    stored = db.scalars(select(L.POLine).where(L.POLine.po_id == order.id).order_by(L.POLine.line_no)).all()
    assert [(x.line_no, x.item, x.ordered_qty, x.unit_price) for x in stored] == [
        (1, "Bolt", D("10.5"), D("0.000001")),
        (2, "Nut", D("3"), D("1")),
    ]
    assert db.scalars(select(AuditLog).where(AuditLog.action == "po.create")).one().object == f"po:{order.id}"


def test_a_po_needs_a_vendor_a_currency_a_line_and_a_new_number(db: Session, acme: L.Vendor) -> None:
    line = [parse_line("Line 1", item="Bolt", quantity="1", unit="EA", unit_price="1")]
    base = {
        "po_number": "PO-1",
        "vendor_id": acme.id,
        "currency": "USD",
        "date_sent": None,
        "buyer": None,
        "lines": line,
    }
    with pytest.raises(LedgerError, match="Choose a vendor"):
        orders.create_po(db, **{**base, "vendor_id": None})
    with pytest.raises(LedgerError, match="Choose a vendor"):
        orders.create_po(db, **{**base, "vendor_id": 999})
    with pytest.raises(LedgerError, match="three-letter"):
        orders.create_po(db, **{**base, "currency": "US"})
    with pytest.raises(LedgerError, match="at least one line"):
        orders.create_po(db, **{**base, "lines": []})
    with pytest.raises(LedgerError, match="PO number is needed"):
        orders.create_po(db, **{**base, "po_number": " "})
    orders.create_po(db, **base)
    with pytest.raises(LedgerError, match="already exists"):
        orders.create_po(db, **{**base, "po_number": "po-1"})  # the same number in another case is the same PO


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("quantity", "0", "more than zero"),
        ("quantity", "-1", "more than zero"),
        ("quantity", "abc", "not a number"),
        ("quantity", "1,5", "not a number"),
        ("quantity", "1.00001", "nothing is rounded"),
        ("quantity", "NaN", "NaN"),
        ("quantity", "", "is needed"),
        ("unit_price", "-0.01", "cannot be negative"),
        ("unit_price", "1.0000001", "nothing is rounded"),
        ("unit_price", "Infinity", "NaN or infinity"),
        ("requested_date", "2026-13-01", "not a real date"),
        ("requested_date", "11.04.2026", "not a date"),
        ("requested_date", "13/01/2026", "not a real date"),
        ("requested_date", "20261104", "not a date"),
        ("requested_date", "2026-W45-1", "not a date"),
        ("requested_date", "1850-01-01", "looks wrong"),
        ("item", " ", "Item is needed"),
        ("unit", "x" * 17, "longer than"),
    ],
)
def test_a_bad_line_value_is_refused_naming_the_line_and_field(field: str, value: str, message: str) -> None:
    good = {"item": "Bolt", "quantity": "1", "unit": "EA", "unit_price": "1"}
    with pytest.raises(LedgerError, match=message) as caught:
        parse_line("Line 4", **{**good, field: value})
    assert str(caught.value).startswith("Line 4: ")


def test_a_free_line_and_a_decimal_quantity_are_fine() -> None:
    line = parse_line("Line 1", item="Sample", quantity="0.0001", unit="KG", unit_price="0")
    assert line == LineInput(item="Sample", quantity=D("0.0001"), unit="KG", unit_price=D("0"))


# ---- CSV import


def test_the_sample_file_imports_every_po_and_line(db: Session) -> None:
    for name in ("Acme Fasteners", "Northwind Metals"):
        vendors.create_vendor(db, name=name)
    result = import_csv(db, SAMPLE.read_bytes())
    db.commit()
    assert result.po_numbers == ["PO-2001", "PO-2002"]
    assert db.scalar(select(func.count()).select_from(L.POLine)) == 3
    second = db.scalars(select(L.PurchaseOrder).where(L.PurchaseOrder.po_number == "PO-2002")).one()
    assert (second.currency, second.source) == ("EUR", "csv")
    line = db.scalars(select(L.POLine).where(L.POLine.po_id == second.id)).one()
    assert (line.ordered_qty, line.unit_price, line.requested_date) == (D("12.5"), D("3.141593"), None)
    summary = db.scalars(select(AuditLog).where(AuditLog.action == "po.import")).one()
    assert '"purchase_orders": 2' in (summary.after or "")


def test_the_template_is_a_valid_file_once_its_vendor_exists(db: Session) -> None:
    vendors.create_vendor(db, name="Example Vendor")
    assert import_csv(db, orders.TEMPLATE.encode()).po_numbers == ["PO-1001"]


def test_a_byte_order_mark_and_windows_line_endings_are_fine(db: Session, acme: L.Vendor) -> None:
    raw = b"\xef\xbb\xbf" + csv_of(row()).replace(b"\n", b"\r\n")
    assert import_csv(db, raw).po_numbers == ["PO-1"]


def test_columns_may_be_in_any_order_and_any_case(db: Session, acme: L.Vendor) -> None:
    header = "Unit_Price, ITEM,quantity,unit,vendor,currency,line_no,PO_Number"
    assert import_csv(db, csv_of("2.5,Bolt,10,EA,Acme Fasteners,USD,1,PO-7", header=header)).po_numbers == ["PO-7"]


def test_a_quoted_cell_with_a_comma_and_a_line_break_is_kept(db: Session, acme: L.Vendor) -> None:
    raw = csv_of(row(description='"Zinc plated, M8\nbox of 100"'))
    import_csv(db, raw)
    assert db.scalars(select(L.POLine.description)).one() == "Zinc plated, M8\nbox of 100"


def test_an_unknown_vendor_is_refused_not_guessed_or_created(db: Session, acme: L.Vendor) -> None:
    problem = rejected(db, csv_of(row(vendor="Acme Fasteners Inc")))
    assert "not in the vendor list" in said(problem) and problem.errors[0].row == 2
    assert db.scalar(select(func.count()).select_from(L.Vendor)) == 1


def test_one_bad_row_rejects_the_whole_file_and_every_problem_is_listed(db: Session, acme: L.Vendor) -> None:
    raw = csv_of(
        row(po_number="PO-1", line_no="1"),
        row(po_number="PO-2", quantity="0"),
        row(po_number="PO-3", unit_price="x"),
        row(po_number="PO-4", requested_date="soon"),
    )
    problem = rejected(db, raw)
    assert [e.row for e in problem.errors] == [3, 4, 5]
    assert "more than zero" in problem.errors[0].message


def test_an_existing_po_number_is_never_overwritten(db: Session, acme: L.Vendor) -> None:
    import_csv(db, csv_of(row()))
    db.commit()
    with pytest.raises(ImportRejected) as caught:
        import_csv(db, csv_of(row(po_number="po-1", item="Changed")))
    assert "already exists" in said(caught.value)
    assert db.scalars(select(L.POLine.item)).one() == "Bolt"


def test_rows_of_one_po_must_agree_on_the_po_level_fields(db: Session, acme: L.Vendor) -> None:
    vendors.create_vendor(db, name="Other")
    for column, value in (("vendor", "Other"), ("currency", "EUR"), ("date_sent", "2026-11-05"), ("buyer", "x@y.com")):
        problem = rejected(db, csv_of(row(line_no="1"), row(line_no="2", **{column: value})))
        assert f"{column} differs from row 2" in said(problem)


def test_a_line_number_cannot_repeat_within_a_po_but_can_across_pos(db: Session, acme: L.Vendor) -> None:
    assert "used twice" in said(rejected(db, csv_of(row(line_no="1"), row(line_no="1"))))
    assert import_csv(db, csv_of(row(po_number="A"), row(po_number="B"))).po_numbers == ["A", "B"]


@pytest.mark.parametrize("value", ["0", "-1", "1.5", "abc", "", "٣", "100001"])
def test_a_line_number_must_be_a_whole_number(db: Session, acme: L.Vendor, value: str) -> None:
    assert "line_no must be a whole number" in said(rejected(db, csv_of(row(line_no=value))))


def test_header_problems_are_reported_once_each(db: Session) -> None:
    missing = rejected(db, b"po_number,vendor\nPO-1,Acme\n")
    assert "Missing column: currency" in said(missing)
    twice = rejected(db, csv_of(header=HEADER + ",item"))
    assert "appears twice: item" in said(twice)


@pytest.mark.parametrize(
    ("raw", "message"),
    [
        (b"", "is empty"),
        (b"\n\n", "is empty"),
        (HEADER.encode() + b"\n", "no rows"),
        (b"\xff\xfe\x00bad", "does not look like a CSV text file"),
        (b"x" * (orders.MAX_CSV_BYTES + 1), "larger than"),
    ],
)
def test_empty_binary_and_oversized_files_are_refused(db: Session, raw: bytes, message: str) -> None:
    assert message in said(rejected(db, raw))


def test_a_row_with_extra_cells_is_refused_and_a_short_row_reads_as_blanks(db: Session, acme: L.Vendor) -> None:
    assert "more cells" in said(rejected(db, csv_of(row() + ",extra,cells")))
    short = ",".join(row().split(",")[:8])  # the optional columns left off the end
    assert import_csv(db, csv_of(short)).po_numbers == ["PO-1"]


def test_blank_rows_are_skipped_and_the_row_numbers_still_count_them(db: Session, acme: L.Vendor) -> None:
    problem = rejected(db, csv_of(row(po_number="PO-1"), ",,,,,,,,,,,", row(po_number="PO-2", quantity="0")))
    assert [e.row for e in problem.errors] == [4]


def test_error_lists_are_capped(db: Session, acme: L.Vendor) -> None:
    problem = rejected(db, csv_of(*[row(po_number=f"PO-{n}", quantity="0") for n in range(80)]))
    assert len(problem.errors) == orders.MAX_ERRORS and problem.truncated


def test_a_spreadsheet_formula_is_stored_as_text_and_never_run(db: Session, acme: L.Vendor) -> None:
    import_csv(db, csv_of(row(item='"=HYPERLINK(""http://evil.example"")"')))
    assert db.scalars(select(L.POLine.item)).one().startswith("=HYPERLINK(")


def test_the_numbers_in_a_file_are_exact(db: Session, acme: L.Vendor) -> None:
    import_csv(db, csv_of(row(quantity="0.1", unit_price="0.000001")))
    line = db.scalars(select(L.POLine)).one()
    assert (line.ordered_qty, line.unit_price) == (D("0.1"), D("0.000001"))


def test_a_file_that_fails_midway_leaves_nothing_behind(
    db: Session, acme: L.Vendor, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If saving the third PO were to fail, the caller's rollback must remove the first two as well."""
    real, calls = orders.create_po, {"n": 0}

    def flaky(*args: object, **kwargs: object) -> L.PurchaseOrder:
        calls["n"] += 1
        if calls["n"] == 3:
            raise RuntimeError("disk full")
        return real(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(orders, "create_po", flaky)
    with pytest.raises(RuntimeError):
        import_csv(db, csv_of(*[row(po_number=f"PO-{n}") for n in range(3)]))
    db.rollback()
    assert db.scalar(select(func.count()).select_from(L.PurchaseOrder)) == 0


def test_a_file_saved_by_quickbooks_or_old_excel_in_windows_1252_is_read_as_it_is(db: Session, acme: L.Vendor) -> None:
    text = csv_of(row(description="Pr\u00e9cis\u00ae seal")).decode("utf-8")
    raw = text.encode("cp1252")  # in this encoding the accent and the symbol are single bytes that UTF-8 refuses
    assert import_csv(db, raw).po_numbers == ["PO-1"]
    kept = db.scalars(select(L.POLine.description)).one()
    assert kept == "Pr\u00e9cis\u00ae seal"  # the accents and the symbol survive


def test_a_utf8_file_with_a_byte_order_mark_still_reads(db: Session, acme: L.Vendor) -> None:
    assert import_csv(db, b"\xef\xbb\xbf" + csv_of(row())).po_numbers == ["PO-1"]


def test_a_file_that_is_neither_utf8_nor_windows_1252_is_refused_not_guessed(db: Session) -> None:
    assert "does not look like a CSV text file" in said(rejected(db, HEADER.encode() + b"\n\x81\x8d\x8f\x90\x9d\n"))


def test_a_report_with_other_column_names_is_refused_naming_what_is_missing(db: Session) -> None:
    quickbooks = b'"Date","Num","Name","Source Name","Qty"\n"11/05/2026","8088","Someone","Vendor",3\n'
    message = said(rejected(db, quickbooks))
    assert "Missing column: po_number, vendor, currency" in message and "The file has: date, num" in message


def test_columns_the_import_does_not_use_are_skipped_and_counted(db: Session, acme: L.Vendor) -> None:
    result = import_csv(db, csv_of(row() + ",x", header=HEADER + ",qty"))
    assert result.po_numbers == ["PO-1"] and result.ignored_columns == ["qty"]


def test_a_date_can_be_typed_the_quickbooks_way_or_the_iso_way() -> None:
    from datetime import date

    from po_dashboard.ledger.errors import when

    assert when("04/11/2026", "Date") == date(2026, 4, 11)  # month/day/year, as QuickBooks writes it
    assert when("4/1/2026", "Date") == date(2026, 4, 1)
    assert when("2026-04-11", "Date") == date(2026, 4, 11)


def test_dates_and_times_are_shown_the_quickbooks_way() -> None:
    from datetime import UTC, date, datetime

    from po_dashboard.display import day, moment

    assert day(date(2026, 11, 6)) == "11/06/2026" and day(None) == "" and moment(None) == ""
    shown = moment(datetime(2026, 11, 6, 19, 5, tzinfo=UTC))
    local = datetime(2026, 11, 6, 19, 5, tzinfo=UTC).astimezone()
    assert shown.endswith(("AM", "PM")) and f":{local.minute:02d} " in shown and day(local) in shown
