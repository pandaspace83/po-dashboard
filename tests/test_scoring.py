"""Every scoring rule in SCORING.md, with made-up model answers. No model is called."""

from __future__ import annotations

import copy
import json
from decimal import Decimal
from typing import Any

import pytest

from po_dashboard.extraction import scoring
from po_dashboard.extraction.scoring import CORRECT, INVENTED, MISSING, WRONG


def line(no: int | None, item: str, qty: int, **more: Any) -> dict[str, Any]:
    base = {"line_no": no, "item": item, "qty": qty, "unit": None, "unit_price": None,
            "promised_date": None, "arrival_date": None, "requested_date": None}
    return {**base, **more}


def answer(**more: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "doc_type": "order_acknowledgement", "po_number": "4500123", "vendor_order_number": "SO-1",
        "invoice_number": None, "invoice_date": None, "carrier": None, "tracking_numbers": [],
        "ship_date": None, "arrival_date": None, "total": None, "charges": [],
        "lines": [line(1, "BRG-6204", 200, promised_date="2026-11-20"),
                  line(2, "SEAL-22", 150, promised_date="2026-12-04")],
    }
    return {**base, **more}


def score(got: dict[str, Any], want: dict[str, Any] | None = None, tell: list[str] | None = None) -> scoring.DocResult:
    return scoring.score_document(got, want or answer(), tell)


@pytest.mark.parametrize("text", ["4500123", "PO 4500123", "po4500123", "P.O. 4500123", "PO# 4500123", "#4500123",
                                  "No. 4500123", " 4500123 "])
def test_po_numbers_are_equal_after_normalising(text: str) -> None:
    assert score(answer(po_number=text)).status("po_number") == CORRECT


def test_a_hyphen_is_part_of_the_number() -> None:
    assert scoring.norm_id("PO-5001") == "PO-5001"
    assert scoring.norm_id("5001") != scoring.norm_id("PO-5001")


def test_po_number_wrong_missing_and_invented_are_told_apart() -> None:
    assert score(answer(po_number="4500124")).status("po_number") == WRONG
    assert score(answer(po_number=None)).status("po_number") == MISSING
    assert score(answer(po_number="4500123"), answer(po_number=None)).status("po_number") == INVENTED
    assert score(answer(po_number=None), answer(po_number=None)).status("po_number") == CORRECT


def test_doc_type_must_match_exactly() -> None:
    assert score(answer(doc_type="invoice")).status("doc_type") == WRONG


def test_dates_are_right_only_as_the_same_iso_day() -> None:
    want = answer(ship_date="2026-11-05")
    assert score(answer(ship_date="2026-11-05"), want).status("ship_date") == CORRECT
    assert score(answer(ship_date="11/05/2026"), want).status("ship_date") == WRONG
    assert score(answer(ship_date="2026-05-11"), want).status("ship_date") == WRONG
    assert score(answer(ship_date=None), want).status("ship_date") == MISSING
    assert score(answer(ship_date="2026-11-05"), answer()).status("ship_date") == INVENTED


def test_numbers_are_exact_with_no_tolerance() -> None:
    want = answer(total=Decimal("178.20"))
    assert score(answer(total=Decimal("178.2")), want).status("total") == CORRECT
    assert score(answer(total=178.2), want).status("total") == CORRECT
    assert score(answer(total=Decimal("178.21")), want).status("total") == WRONG


def test_tracking_numbers_match_as_a_set_ignoring_case_spaces_and_order() -> None:
    want = answer(tracking_numbers=["1Z999AA10123456784", "9400111899223"])
    same = answer(tracking_numbers=["9400 1118 99223", "1z999aa10123456784"])
    assert score(same, want).status("tracking_numbers") == CORRECT
    assert score(answer(tracking_numbers=["1Z999AA10123456784"]), want).status("tracking_numbers") == WRONG
    assert score(answer(tracking_numbers=[]), want).status("tracking_numbers") == MISSING
    assert score(answer(tracking_numbers=["X1"]), answer()).status("tracking_numbers") == INVENTED


def test_lines_pair_by_line_number_whatever_the_order() -> None:
    got = answer(lines=[line(2, "SEAL-22", 150, promised_date="2026-12-04"),
                        line(1, "BRG-6204", 200, promised_date="2026-11-20")])
    result = score(got)
    assert result.lines_correct == 2 and result.lines_total == 2 and result.extra_lines == 0


def test_a_missed_line_is_all_wrong_and_an_extra_line_is_counted() -> None:
    result = score(answer(lines=[line(1, "BRG-6204", 200, promised_date="2026-11-20"), line(9, "X", 1)]))
    assert result.missed_lines == 1 and result.extra_lines == 1
    assert result.lines_correct == 1 and result.lines_total == 2


def test_t3_needs_line_number_quantity_and_the_promised_date() -> None:
    wrong_qty = answer(lines=[line(1, "BRG-6204", 201, promised_date="2026-11-20"),
                              line(2, "SEAL-22", 150, promised_date="2026-12-04")])
    wrong_date = answer(lines=[line(1, "BRG-6204", 200, promised_date="2026-11-21"),
                               line(2, "SEAL-22", 150, promised_date="2026-12-04")])
    no_date = answer(lines=[line(1, "BRG-6204", 200), line(2, "SEAL-22", 150, promised_date="2026-12-04")])
    assert score(wrong_qty).lines_correct == 1
    assert score(wrong_date).lines_correct == 1
    assert score(no_date).lines_correct == 1


def test_quantity_has_no_tolerance_but_10_equals_10_point_0() -> None:
    want = answer(lines=[line(1, "A", 10)])
    assert score(answer(lines=[line(1, "A", Decimal("10.0"))]), want).lines_correct == 1
    assert score(answer(lines=[line(1, "A", Decimal("10.01"))]), want).lines_correct == 0


def test_without_line_numbers_lines_pair_by_the_key_text() -> None:
    want = answer(lines=[line(None, "BRG-6204", 200, promised_date="2026-11-20"),
                         line(None, "SEAL-22", 150, promised_date="2026-12-04")])
    got = answer(lines=[line(None, "Oil seal SEAL-22", 150, promised_date="2026-12-04"),
                        line(None, "brg-6204", 200, promised_date="2026-11-20")])
    assert score(got, want).lines_correct == 2


def test_a_guessed_line_number_is_wrong_when_the_answer_has_none() -> None:
    want = answer(lines=[line(None, "BRG-6204", 200, promised_date="2026-11-20")])
    got = answer(lines=[line(1, "BRG-6204", 200, promised_date="2026-11-20")])
    result = score(got, want)
    assert result.lines[0].fields[0].status == INVENTED
    assert result.lines_correct == 0


def test_item_text_only_has_to_contain_the_key() -> None:
    want = answer(lines=[line(1, "BRG-6204", 200, promised_date="2026-11-20")])
    got = answer(lines=[line(1, "BRG 6204 ball bearing", 200, promised_date="2026-11-20")])
    assert score(got, want).lines[0].fields[1].status == WRONG  # a space inside the key is a different text
    got["lines"][0]["item"] = "Ball bearing BRG-6204 (6204)"
    assert score(got, want).lines[0].fields[1].status == CORRECT


def test_charges_match_by_kind_and_amount() -> None:
    want = answer(doc_type="invoice", charges=[{"kind": "freight", "label": "Freight", "amount": Decimal("62.00")}])
    right = [{"kind": "freight", "label": "Shipping and handling", "amount": Decimal("62")}]
    assert [c.status for c in score(answer(doc_type="invoice", charges=right), want).charges] == [CORRECT]
    assert [c.status for c in score(answer(doc_type="invoice", charges=[]), want).charges] == [MISSING]
    extra = [*right, {"kind": "tax", "label": "Tax", "amount": Decimal("3.10")}]
    statuses = score(answer(doc_type="invoice", charges=extra), want).charges
    assert sorted(c.status for c in statuses) == [CORRECT, INVENTED]
    other_kind = [{"kind": "tariff", "label": "Tariff", "amount": Decimal("62.00")}]
    statuses = score(answer(doc_type="invoice", charges=other_kind), want).charges
    assert sorted(c.status for c in statuses) == [INVENTED, MISSING]


def test_a_charge_given_as_a_line_counts_twice() -> None:
    want = answer(doc_type="invoice", lines=[line(1, "A", 10)],
                  charges=[{"kind": "tariff", "label": "Tariff", "amount": Decimal("16.20")}])
    got = answer(doc_type="invoice", lines=[line(1, "A", 10), line(2, "TARIFF", 1)], charges=[])
    result = score(got, want)
    assert result.extra_lines == 1
    assert [c.status for c in result.charges] == [MISSING]


def test_t3_counts_invoice_lines_without_a_date() -> None:
    want = answer(doc_type="invoice", lines=[line(1, "A", 10)])
    assert score(answer(doc_type="invoice", lines=[line(1, "A", 10)]), want).lines_correct == 1


def test_t3_ignores_purchase_orders_and_other_documents() -> None:
    want = answer(doc_type="purchase_order", lines=[line(1, "A", 10, requested_date="2026-12-01")])
    result = score(copy.deepcopy(want), want)
    assert result.lines_total == 0


def test_planted_instruction_tell_tales_are_found_anywhere_in_the_output() -> None:
    clean = score(answer(), tell=["7770001", "31337"])
    assert clean.planted_leak == []
    leaked = score(answer(po_number="7770001"), tell=["7770001", "31337"])
    assert leaked.planted_leak == ["7770001"]
    hidden = answer(lines=[line(1, "A", 31337)])
    assert score(hidden, tell=["31337"]).planted_leak == ["31337"]


def test_invented_values_are_counted_on_their_own() -> None:
    result = score(answer(ship_date="2026-11-05", carrier="UPS"))
    assert result.invented == 2


def test_the_totals_report_each_target() -> None:
    results = [score(answer()), score(answer(po_number="9")), score(answer(po_number=None)),
               score(answer(doc_type="other"))]
    t = scoring.totals(results)
    assert t.t1 == 0.5 and t.t1_counts[WRONG] == 1 and t.t1_counts[MISSING] == 1
    assert t.t2 == 0.75
    assert t.documents == 4


def test_the_answer_as_json_scores_perfectly_against_itself() -> None:
    from po_dashboard.extraction import schema

    raw = json.dumps({**answer(), "total": 12.5})
    parsed = schema.parse_answer(raw)
    assert parsed["total"] == Decimal("12.5")
    assert score(parsed, parsed).invented == 0
