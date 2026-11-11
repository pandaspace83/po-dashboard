"""The dashboard counts, the open-items report and its CSV export (PD-1.6, DEC-42)."""

import csv
import io
from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from po_dashboard.ledger import reports
from test_entries_web import NONE, ack, po  # noqa: F401
from test_gate import ORIGIN, env, new_client, post  # noqa: F401
from test_orders_web import client, make_vendor, order_form, place  # noqa: F401

LATER = date(2026, 12, 1)  # the fixture PO was sent on 2026-11-04


def parsed(body: bytes) -> list[list[str]]:
    assert body.startswith(b"\xef\xbb\xbf")
    return list(csv.reader(io.StringIO(body[3:].decode("utf-8"))))


# ---- business days


@pytest.mark.parametrize(
    ("start", "today", "expected"),
    [(date(2026, 11, 2), date(2026, 11, 2), 0), (date(2026, 11, 2), date(2026, 11, 3), 1),
     (date(2026, 11, 6), date(2026, 11, 9), 1),  # Friday to Monday: one working day
     (date(2026, 11, 6), date(2026, 11, 7), 0), (date(2026, 11, 2), date(2026, 11, 9), 5)],
)  # fmt: skip
def test_business_days_skip_weekends(start: date, today: date, expected: int) -> None:
    assert reports.business_days_since(start, today) == expected


# ---- spreadsheet safety


@pytest.mark.parametrize("value", ["=1+1", "+cmd", "-5mm bolt", "@SUM(A1)", "\tx", "\rx", '=HYPERLINK("http://x")'])
def test_a_cell_that_would_run_as_a_formula_is_made_plain_text(value: str) -> None:
    assert reports.safe_cell(value) == "'" + value


@pytest.mark.parametrize("value", ["Bolt", "10", "", None, "M8-1.25 bolt", "a=b"])
def test_an_ordinary_cell_is_left_alone(value: str | None) -> None:
    assert reports.safe_cell(value) == ("" if value is None else value)


# ---- the numbers


def test_a_sent_po_with_no_acknowledgement_is_flagged_only_after_two_working_days(client: TestClient, env, po) -> None:  # noqa: ANN001
    with Session(env[1]) as db:
        assert reports.counts(db, date(2026, 11, 6)).no_ack == 0  # sent 11-04: only two working days on
        assert reports.counts(db, date(2026, 11, 9)).no_ack == 2  # both lines
        found = reports.counts(db, LATER)
    assert (found.open_pos, found.open_lines, found.late, found.slipped, found.needs_update) == (1, 2, 0, 0, 0)


def test_late_slipped_and_update_flags_are_counted_from_the_same_list(client: TestClient, env, po) -> None:  # noqa: ANN001
    post(client, f"/orders/{po[0]}/ack", ack(po, [("6", "", "2026-11-10"), NONE]))
    post(client, f"/orders/{po[0]}/ack", ack(po, [("", "", "2026-11-20"), NONE]))
    with Session(env[1]) as db:
        found = reports.counts(db, LATER)
        late = reports.open_items(db, LATER, "late")
        assert (found.late, found.slipped, found.needs_update, found.no_ack) == (1, 1, 1, 1)
        assert [i.line_no for i in late] == [1] and late[0].expectation.days_late == 11
        assert "promised date slipped" in late[0].flags and "update the original PO" in late[0].flags
        assert [i.line_no for i in reports.open_items(db, LATER, "no_ack")] == [2]
        assert len(reports.open_items(db, LATER, "nonsense")) == 2  # an unknown view shows everything, never nothing


def test_a_closed_or_cancelled_line_is_not_an_open_item(client: TestClient, env, po) -> None:  # noqa: ANN001
    done = {
        "line_id": [str(po[1]), str(po[2])],
        "line_action": ["cancelled", "received"],
        "line_version": ["1", "1"],
        "reason": "Done",
    }
    post(client, f"/orders/{po[0]}/close", done)
    with Session(env[1]) as db:
        assert reports.counts(db, LATER).open_lines == 0 and reports.counts(db, LATER).open_pos == 0


def test_last_vendor_activity_is_the_latest_entry(client: TestClient, env, po) -> None:  # noqa: ANN001
    with Session(env[1]) as db:
        assert all(i.last_activity is None for i in reports.open_items(db, LATER))
    post(client, f"/orders/{po[0]}/ack", ack(po, [("10", "", ""), NONE]))
    with Session(env[1]) as db:
        first, second = reports.open_items(db, LATER)
        assert first.last_activity is not None and second.last_activity is None


# ---- the CSV


def test_the_csv_has_the_columns_and_neutralises_formulas(client: TestClient, env) -> None:  # noqa: ANN001
    vendor = make_vendor(client)
    rows = [("=HYPERLINK(\"http://evil.example\")", "@x", "1", "EA", "1", ""), ("-5mm bolt", "", "2", "EA", "1", ""),
            ("",) * 6]  # fmt: skip
    place(client, order_form(vendor, rows))
    with Session(env[1]) as db:
        body = reports.to_csv(reports.open_items(db, LATER))
    table = parsed(body)
    assert tuple(table[0]) == reports.CSV_COLUMNS and len(table) == 3
    assert table[1][3] == '\'=HYPERLINK("http://evil.example")' and table[1][4] == "'@x"
    assert table[2][3] == "'-5mm bolt"
    assert all(not cell.startswith(("=", "+", "@")) for row in table[1:] for cell in row)


# ---- the pages


def test_the_dashboard_shows_the_counts_and_links_to_the_report(client: TestClient, po) -> None:  # noqa: ANN001
    page = client.get("/").text
    assert "Open purchase orders" in page and 'href="/reports/open-items?show=late"' in page
    assert "Nothing open" not in page


def test_an_empty_dashboard_says_so(client: TestClient) -> None:
    assert "Nothing open" in client.get("/").text


def test_the_report_page_filters_and_downloads(client: TestClient, po) -> None:  # noqa: ANN001
    page = client.get("/reports/open-items").text
    assert page.count(" autofocus") == 1 and "Bolt" in page and "Nut" in page
    assert "All clear" in client.get("/reports/open-items?show=late").text
    assert "All clear" in client.get("/reports/open-items?vendor=99999").text
    assert client.get("/reports/open-items?show=%3Cscript%3E&vendor=%27").status_code == 200
    download = client.get("/reports/open-items.csv")
    assert download.status_code == 200 and download.headers["content-type"].startswith("text/csv")
    assert 'attachment; filename="open-items-' in download.headers["content-disposition"]
    assert len(parsed(download.content)) == 3


def test_the_report_needs_a_sign_in(env) -> None:  # noqa: ANN001
    anonymous = new_client(env[0])
    for path in ("/reports/open-items", "/reports/open-items.csv"):
        assert anonymous.get(path, headers=ORIGIN, follow_redirects=False).status_code in (303, 401, 403)


def test_the_to_do_list_puts_the_most_urgent_first_and_says_what_to_do(client: TestClient, env, po) -> None:  # noqa: ANN001
    post(client, f"/orders/{po[0]}/ack", ack(po, [("6", "", "2026-11-10"), NONE]))
    with Session(env[1]) as db:
        items = reports.open_items(db, LATER)
        needing, total = reports.attention(items)
        assert total == len(reports.open_items(db, LATER, "attention")) == 2
        assert [i.line_no for i in needing] == [1, 2]  # line 1 is late; line 2 only waits for an acknowledgement
        assert needing[0].next_step == "Ask the vendor for a new date"
        assert needing[1].next_step == "Ask the vendor to acknowledge the PO"


def test_home_shows_the_to_do_list_or_all_clear(client: TestClient, po) -> None:  # noqa: ANN001
    page = client.get("/").text
    assert "Needs attention" in page and 'id="live"' in page and 'hx-trigger="every 60s"' in page
    assert '"X-Background": "1"' in page and 'hx-disinherit="*"' in page
