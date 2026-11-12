"""The reconciliation engine's rules, one by one, on plain records (PD-8.4, DEC-59, DEC-62). No database: each test
builds a book and reads the result. The owner's ten scenarios, end to end through the real services, are in
`test_reconciliation_scenarios.py`."""

from __future__ import annotations

import random
from datetime import date
from decimal import Decimal as D

from po_dashboard.quickbooks.reconciliation.engine import (
    CODES,
    STATUSES,
    Book,
    Decision,
    Invoice,
    InvoiceLine,
    Po,
    PoLine,
    Result,
    Rules,
    covers,
    first_gap,
    item_key,
    merged,
    reconcile,
    reference_keys,
)

TODAY = date(2026, 12, 31)
NOVEMBER = ((date(2026, 11, 1), date(2026, 12, 31)),)


def po(
    pid: int,
    number: str = "CP-1",
    lines: tuple[tuple[str, str, str], ...] = (("Bolt", "10", "100"),),
    *,
    customer: int = 1,
    state: str = "active",
    since: date = date(2026, 11, 1),
    currency: str = "USD",
    **more: object,
) -> Po:
    built = tuple(PoLine(pid * 100 + n, n, item, D(q), D(p)) for n, (item, q, p) in enumerate(lines, 1))
    return Po(pid, customer, number, more.pop("revision", 0), state, since, currency, built, **more)  # type: ignore[arg-type]


def inv(
    iid: int,
    number: str,
    lines: tuple[tuple[str | None, str | None, str | None, str, str], ...] = (),
    *,
    ref: str | None = "CP-1",
    customer: int | None = 1,
    kind: str = "invoice",
    total: str | None = None,
    currency: str = "USD",
    day: date = date(2026, 11, 5),
    review: str | None = None,
    qb: str | None = None,
) -> Invoice:
    built = tuple(
        InvoiceLine(iid * 100 + n, n, item, D(q) if q else None, D(p) if p else None, D(a), k)
        for n, (item, q, p, a, k) in enumerate(lines, 1)
    )
    amount = D(total) if total is not None else sum((ln.amount for ln in built), D(0))
    return Invoice(
        iid,
        kind,
        number,
        customer,
        "Globex",
        day,
        currency,
        amount,
        ref,
        qb,
        "line" if built else "invoice",
        built,
        review,
    )


def goods(item: str, qty: str, price: str, amount: str | None = None) -> tuple[str, str, str, str, str]:
    return (item, qty, price, amount or str(D(qty) * D(price)), "goods")


def charge(item: str, amount: str, kind: str) -> tuple[str, None, None, str, str]:
    return (item, None, None, amount, kind)


def run(
    pos: list[Po],
    invoices: list[Invoice],
    decisions: tuple[Decision, ...] = (),
    periods: tuple[tuple[date, date], ...] = NOVEMBER,
    today: date = TODAY,
    **rules: object,
) -> Result:
    return reconcile(Book(tuple(pos), tuple(invoices), Rules(today, **rules), decisions, periods))  # type: ignore[arg-type]


def codes(result: Result, pid: int) -> list[str]:
    return [p.code for p in result.orders[pid].problems]


# ---- exact matching (scenario 1) and the order of evidence


def test_an_invoice_that_bills_the_po_exactly_is_matched_on_line_evidence() -> None:
    r = run([po(1)], [inv(1, "2001", (goods("Bolt", "10", "100"),))])
    order = r.orders[1]
    assert order.status == "matched" and order.problems == [] and order.invoiced == order.ordered == D(1000)
    assert (
        order.remaining == 0
        and order.net == D(1000)
        and [(k.po_line_id, k.basis) for k in order.links] == [(101, "rule")]
    )


def test_items_are_compared_ignoring_only_case_and_spacing_never_by_likeness() -> None:
    assert item_key("  BOLTS:M8 ") == item_key("bolts:m8") and item_key("Bolt  M8") == "bolt m8"
    same = run([po(1, lines=(("Bolts:M8", "10", "100"),))], [inv(1, "2001", (goods(" bolts:m8", "10", "100"),))])
    assert same.orders[1].status == "matched"
    near = run([po(1, lines=(("Bolts:M8", "10", "100"),))], [inv(1, "2001", (goods("Bolt M8", "10", "100"),))])
    assert near.orders[1].status == "needs_review" and codes(near, 1) == ["unmatched_invoice"]
    assert near.orders[1].invoiced == 0 and near.orders[1].problems[0].amount == D(1000)


def test_po_references_are_read_as_keys_and_a_list_names_several_pos() -> None:
    assert reference_keys("CP-1; cp 2, PO 77,,cp-1") == [("cp1", "CP-1"), ("cp2", "cp 2"), ("77", "PO 77")]
    r = run(
        [po(1, "CP-1"), po(2, "CP-2", (("Gear", "4", "25"),))],
        [inv(1, "2001", (goods("Bolt", "10", "100"), goods("Gear", "4", "25")), ref="cp-1, CP 2")],
    )
    assert r.orders[1].status == r.orders[2].status == "matched"  # one invoice, two POs (multiple POs per invoice)


def test_a_reference_is_looked_up_only_within_the_invoices_customer() -> None:
    r = run([po(1, "CP-1", customer=2)], [inv(1, "2001", (goods("Bolt", "10", "100"),))])
    assert [p.code for p in r.unplaced] == ["ambiguous_match"] and "another customer's PO" in r.unplaced[0].message
    assert r.orders[1].links == [] and r.orders[1].status == "no_invoice"


def test_a_reference_the_app_does_not_have_is_unmatched_with_suggestions() -> None:
    r = run(
        [po(1, "CP-1"), po(2, "CP-2", (("Nut", "1", "1"),))],
        [inv(1, "2001", (goods("Bolt", "10", "100"),), ref="CP-9")],
    )
    (problem,) = r.unplaced
    assert problem.code == "unmatched_invoice" and "CP-9" in problem.message and problem.suggestions == (1,)
    assert not problem.blocking and r.orders[1].links == []


def test_an_invoice_with_no_reference_waits_and_is_never_matched_by_an_equal_amount() -> None:
    r = run([po(1)], [inv(1, "2001", total="1000.00", ref=None)])  # same customer, same amount, no reference
    (problem,) = r.unplaced
    assert problem.code == "unmatched_invoice" and "no PO reference" in problem.message and problem.suggestions == (1,)
    assert r.orders[1].links == [] and r.orders[1].status == "no_invoice"


def test_an_invoice_whose_customer_is_not_chosen_reaches_no_po() -> None:
    r = run([po(1)], [inv(1, "2001", (goods("Bolt", "10", "100"),), customer=None)])
    assert [p.code for p in r.unplaced] == ["unmatched_invoice"] and "does not know" in r.unplaced[0].message
    assert r.orders[1].links == []


def test_a_quickbooks_reference_on_both_sides_comes_before_the_po_reference() -> None:
    pos = [po(1, "CP-1"), po(2, "CP-2", qb_references=frozenset({"so-55"}))]
    r = run(pos, [inv(1, "2001", (goods("Bolt", "10", "100"),), ref="CP-1", qb="SO-55")])
    assert r.orders[1].links == [] and r.orders[2].invoice_ids == (1,)
    fallback = run(pos, [inv(1, "2001", (goods("Bolt", "10", "100"),), ref="CP-1", qb="SO-99")])
    assert fallback.orders[1].invoice_ids == (1,)  # a reference no PO carries: the PO reference decides


def test_an_item_on_two_candidate_lines_waits_for_a_person_and_nothing_is_counted() -> None:
    r = run([po(1, lines=(("Bolt", "5", "100"), ("Bolt", "5", "100")))], [inv(1, "2001", (goods("Bolt", "5", "100"),))])
    (problem,) = r.orders[1].problems
    assert problem.code == "ambiguous_match" and problem.blocking and problem.suggestions == (101, 102)
    assert r.orders[1].invoiced == 0 and r.orders[1].status == "needs_review"


# ---- quantities, prices, currency and tolerances (scenarios 3, 4, 5)


def test_a_price_within_the_tolerance_matches_and_beyond_it_is_a_confirmed_mismatch_with_its_variance() -> None:
    near = run([po(1)], [inv(1, "2001", (goods("Bolt", "10", "100.01"),))])
    assert near.orders[1].status == "matched" and codes(near, 1) == []  # 0.10 over in total, a cent a unit: accepted
    far = run([po(1)], [inv(1, "2001", (goods("Bolt", "10", "110"),))])
    (problem,) = far.orders[1].problems
    assert problem.code == "price_mismatch" and problem.level == "confirmed" and problem.amount == D(100)
    assert far.orders[1].status == "needs_review" and "110" in problem.message and "100" in problem.message
    loose = run([po(1)], [inv(1, "2001", (goods("Bolt", "10", "100.04"),))], amount_tolerance=D("0.05"))
    assert codes(loose, 1) == []


def test_more_quantity_than_ordered_is_over_invoiced_on_line_evidence_and_not_counted_twice() -> None:
    r = run([po(1, lines=(("Bolt", "50", "100"),))], [inv(1, "2001", (goods("Bolt", "58", "100"),))])
    (problem,) = r.orders[1].problems
    assert problem.code == "quantity_mismatch" and problem.level == "confirmed" and problem.amount == D(800)
    assert r.orders[1].status == "over_invoiced" and r.orders[1].remaining == D(-800)


def test_price_and_quantity_over_together_are_fully_explained_by_the_line_differences() -> None:
    r = run([po(1)], [inv(1, "2001", (goods("Bolt", "12", "110"),))])  # 1320 billed for 1000 ordered
    assert sorted(codes(r, 1)) == ["price_mismatch", "quantity_mismatch"]
    assert sum(p.amount for p in r.orders[1].problems if p.amount) == D(320)  # 12 x 10 + 2 x 100


def test_the_quantity_tolerance_is_a_setting() -> None:
    assert codes(run([po(1)], [inv(1, "2001", (goods("Bolt", "10.5", "100"),))], quantity_tolerance=D(1)), 1) == []


def test_a_partly_billed_po_is_partially_invoiced_with_its_apparent_remainder() -> None:
    r = run(
        [po(1, lines=(("Bolt", "50", "100"),))],
        [inv(1, "2001", (goods("Bolt", "20", "100"),)), inv(2, "2002", (goods("Bolt", "15", "100"),))],
    )
    order = r.orders[1]
    assert order.status == "partially_invoiced" and order.invoiced == D(3500) and order.remaining == D(1500)
    (problem,) = order.problems
    assert problem.code == "partially_invoiced" and problem.level == "preliminary" and problem.amount == D(1500)
    assert [f.billed_qty for f in order.lines] == [D(35)] and not problem.blocking


def test_an_invoice_in_another_currency_is_never_converted_or_counted() -> None:
    r = run([po(1)], [inv(1, "2001", (goods("Bolt", "10", "100"),), currency="EUR")])
    (problem,) = r.orders[1].problems
    assert problem.code == "currency_mismatch" and problem.blocking and r.orders[1].invoiced == 0
    assert r.orders[1].status == "needs_review"


# ---- invoices with only a total


def test_an_invoice_with_only_a_total_is_matched_by_amount_and_says_so() -> None:
    exact = run([po(1)], [inv(1, "9001", total="1000.00")])
    assert exact.orders[1].status == "matched_by_amount" and exact.orders[1].links[0].po_line_id is None
    over = run([po(1)], [inv(1, "9001", total="1000.00"), inv(2, "9002", total="800.00", day=date(2026, 11, 9))])
    (problem,) = over.orders[1].problems
    assert problem.code == "amount_mismatch" and problem.level == "preliminary" and problem.amount == D(800)
    assert over.orders[1].status == "over_invoiced" and "invoice totals only" in problem.message


def test_an_invoice_total_naming_two_pos_is_not_split_by_a_rule() -> None:
    r = run([po(1, "CP-1"), po(2, "CP-2")], [inv(1, "9001", total="1500.00", ref="CP-1; CP-2")])
    assert codes(r, 1) == codes(r, 2) == ["ambiguous_match"] and r.links == []
    split = run(
        [po(1, "CP-1"), po(2, "CP-2")],
        [inv(1, "9001", total="1500.00", ref="CP-1; CP-2")],
        decisions=(Decision(1, "confirmed", 1, 1, amount=D(1000)), Decision(2, "confirmed", 2, 1, amount=D(500))),
    )
    assert split.orders[1].status == "matched_by_amount" and split.orders[2].invoiced == D(500)


# ---- credit memos and charges (scenario 10)


def test_a_credit_memo_waits_for_a_person_and_never_changes_what_was_billed() -> None:
    bill = inv(1, "2001", (goods("Bolt", "10", "100"),))
    memo = inv(2, "C-1", (goods("Bolt", "-2", "100"),), kind="credit_memo")
    waiting = run([po(1)], [bill, memo])
    assert codes(waiting, 1) == ["credit_memo"] and waiting.orders[1].net is None
    assert waiting.orders[1].status == "needs_review" and waiting.orders[1].lines[0].billed_qty == D(10)
    linked = run([po(1)], [bill, memo], decisions=(Decision(1, "confirmed", 1, 2),))
    order = linked.orders[1]
    assert order.status == "matched" and order.credits == D(-200) and order.net == D(800)
    assert order.lines[0].billed_qty == D(10) and order.invoiced == D(1000)


def test_freight_tax_and_discounts_are_apart_from_goods_and_the_net_includes_them() -> None:
    r = run(
        [po(1)],
        [
            inv(
                1,
                "2001",
                (
                    goods("Bolt", "10", "100"),
                    charge("Shipping", "50", "freight"),
                    charge("Sales Tax", "80", "tax"),
                    charge("Discount", "-30", "discount"),
                ),
            )
        ],
    )
    order = r.orders[1]
    assert order.status == "matched" and order.invoiced == D(1000) and order.problems == []
    assert order.charges == {"freight": D(50), "tax": D(80), "discount": D(-30)} and order.net == D(1100)


def test_a_charge_that_is_on_the_po_is_matched_to_its_line_like_anything_else() -> None:
    r = run(
        [po(1, lines=(("Bolt", "10", "100"), ("Shipping", "1", "50")))],
        [inv(1, "2001", (goods("Bolt", "10", "100"), ("Shipping", "1", "50", "50", "freight")))],
    )
    assert r.orders[1].status == "matched" and r.orders[1].charges == {}


def test_a_charge_nobody_classified_stops_the_net_and_waits() -> None:
    r = run([po(1)], [inv(1, "2001", (goods("Bolt", "10", "100"), charge("Misc", "9", "other")))])
    assert codes(r, 1) == ["charge_unclassified"] and r.orders[1].net is None and r.orders[1].status == "needs_review"


def test_charges_on_an_invoice_for_several_pos_are_not_split() -> None:
    r = run(
        [po(1, "CP-1"), po(2, "CP-2", (("Gear", "4", "25"),))],
        [
            inv(
                1,
                "2001",
                (goods("Bolt", "10", "100"), goods("Gear", "4", "25"), charge("Freight", "40", "freight")),
                ref="CP-1; CP-2",
            )
        ],
    )
    assert [p.code for p in r.unplaced] == ["charges_unassigned"] and r.unplaced[0].amount == D(40)
    assert r.orders[1].net is None and r.orders[2].net is None and r.orders[1].status == "matched"


def test_a_billed_line_with_no_quantity_cannot_be_compared() -> None:
    r = run([po(1)], [inv(1, "2001", (("Bolt", None, None, "1000", "goods"),))])
    assert codes(r, 1) == ["invoice_incomplete"] and r.orders[1].lines[0].qty_known is False


# ---- duplicates (scenario 6) and invoices waiting for a person


def test_two_different_invoices_with_one_number_are_a_confirmed_potential_duplicate() -> None:
    r = run([po(1)], [inv(1, "2001", (goods("Bolt", "10", "100"),), review="duplicate_number")])
    (problem,) = r.orders[1].problems
    assert problem.code == "potential_duplicate" and problem.level == "confirmed" and problem.blocking
    assert r.orders[1].invoiced == D(1000)  # counted once, never twice


def test_an_invoice_changed_or_read_incompletely_makes_the_po_wait() -> None:
    for review, code in (("changed", "invoice_changed"), ("incomplete", "invoice_incomplete")):
        r = run([po(1)], [inv(1, "2001", (goods("Bolt", "10", "100"),), review=review)])
        assert codes(r, 1) == [code] and r.orders[1].status == "needs_review"
    lost = run([po(1)], [inv(1, "2001", (goods("Bolt", "10", "100"),), review="changed", ref=None)])
    assert sorted(p.code for p in lost.unplaced) == ["invoice_changed", "unmatched_invoice"]


def test_two_invoices_with_the_same_date_and_lines_are_both_counted_and_flagged() -> None:
    twice = [inv(1, "2001", (goods("Bolt", "5", "100"),)), inv(2, "2002", (goods("Bolt", "5", "100"),))]
    r = run([po(1)], twice)
    assert r.orders[1].invoiced == D(1000) and codes(r, 1) == ["potential_duplicate"]
    assert r.orders[1].problems[0].level == "preliminary" and r.orders[1].status == "matched"
    later = [twice[0], inv(2, "2002", (goods("Bolt", "5", "100"),), day=date(2026, 11, 20))]
    assert codes(run([po(1)], later), 1) == []  # two instalments on two dates are normal (scenario 7)


# ---- no invoice: coverage and the 30-day rule (scenario 2)


def test_a_young_po_without_an_invoice_is_not_called_anything() -> None:
    r = run([po(1, since=date(2026, 12, 15))], [])
    assert r.orders[1].status == "no_invoice" and r.orders[1].problems == []


def test_an_old_po_with_no_invoice_is_possibly_unbilled_only_when_the_uploads_cover_its_time() -> None:
    covered = run([po(1, since=date(2026, 11, 1))], [])
    (problem,) = covered.orders[1].problems
    assert problem.code == "possibly_unbilled" and problem.level == "preliminary" and problem.amount == D(1000)
    gap = run([po(1, since=date(2026, 11, 1))], [], periods=((date(2026, 11, 1), date(2026, 11, 10)),))
    (missing,) = gap.orders[1].problems
    assert missing.code == "not_found" and "11/11/2026 to 12/01/2026" in missing.message
    assert gap.orders[1].status == "no_invoice"


def test_a_po_no_upload_touches_is_not_covered() -> None:
    r = run([po(1, since=date(2026, 11, 1))], [], periods=((date(2026, 9, 1), date(2026, 9, 30)),))
    assert r.orders[1].status == "not_covered" and codes(r, 1) == ["not_found"]
    assert run([po(1, since=date(2026, 12, 20))], [], periods=()).orders[1].status == "not_covered"


def test_the_unbilled_days_are_a_setting() -> None:
    assert codes(run([po(1, since=date(2026, 12, 20))], [], unbilled_days=10), 1) == ["possibly_unbilled"]


def test_the_periods_of_several_uploads_are_joined() -> None:
    periods = (
        (date(2026, 11, 11), date(2026, 11, 30)),
        (date(2026, 11, 1), date(2026, 11, 10)),
        (date(2026, 12, 5), date(2026, 12, 9)),
    )
    assert merged(periods) == [(date(2026, 11, 1), date(2026, 11, 30)), (date(2026, 12, 5), date(2026, 12, 9))]
    assert covers(periods, date(2026, 11, 2), date(2026, 11, 29)) and not covers(
        periods, date(2026, 11, 2), date(2026, 12, 6)
    )
    assert first_gap(periods, date(2026, 11, 2), date(2026, 12, 6)) == (date(2026, 12, 1), date(2026, 12, 4))
    assert first_gap(periods, date(2026, 11, 2), date(2026, 11, 20)) is None


# ---- states, revisions and a person's decisions (scenarios 8, 9)


def test_a_closed_po_is_closed_and_is_not_called_unbilled_but_a_wrong_price_still_shows() -> None:
    assert run([po(1, state="closed")], []).orders[1].problems == []
    r = run([po(1, state="cancelled")], [inv(1, "2001", (goods("Bolt", "5", "110"),))])
    assert r.orders[1].status == "cancelled" and codes(r, 1) == ["price_mismatch"]


def test_a_pending_revision_makes_the_po_wait_and_the_terms_in_force_are_used() -> None:
    r = run([po(1, pending_revision=1)], [inv(1, "2001", (goods("Bolt", "10", "100"),))])
    assert codes(r, 1) == ["revised_po"] and r.orders[1].status == "needs_review" and r.orders[1].invoiced == D(1000)


def test_a_difference_against_a_later_revision_says_the_invoice_came_first() -> None:
    revised = po(1, lines=(("Bolt", "10", "90"),), revision=1, revised_on=date(2026, 11, 20))
    r = run([revised], [inv(1, "2001", (goods("Bolt", "10", "100"),), day=date(2026, 11, 5))])
    assert "dated before revision 1 was approved" in r.orders[1].problems[0].message


def test_a_persons_rejection_keeps_the_rule_from_matching_again() -> None:
    r = run([po(1)], [inv(1, "2001", (goods("Bolt", "10", "100"),))], decisions=(Decision(1, "rejected", 1, 1),))
    assert r.orders[1].links == [] and [p.code for p in r.unplaced] == ["unmatched_invoice"]
    assert "a person said it does not belong" in r.unplaced[0].message


def test_a_persons_link_places_an_invoice_with_no_reference_and_its_lines_then_match_by_item() -> None:
    r = run(
        [po(1)], [inv(1, "2001", (goods("Bolt", "10", "100"),), ref=None)], decisions=(Decision(1, "confirmed", 1, 1),)
    )
    assert r.unplaced == [] and r.orders[1].status == "matched" and r.orders[1].links[0].basis == "rule"


def test_a_persons_line_choice_settles_an_ambiguous_item() -> None:
    pos = [po(1, lines=(("Bolt", "5", "100"), ("Bolt", "5", "100")))]
    r = run(pos, [inv(1, "2001", (goods("Bolt", "5", "100"),))], decisions=(Decision(1, "confirmed", 1, 1, 102, 101),))
    order = r.orders[1]
    assert order.problems[0].code == "partially_invoiced" and [f.billed_qty for f in order.lines] == [D(0), D(5)]
    assert order.links[0].basis == "person"


def test_a_decision_that_no_longer_fits_the_revision_is_shown_not_applied() -> None:
    stale = Decision(1, "confirmed", 1, 1, None, 101, stale="no longer fits")
    r = run([po(1)], [inv(1, "2001", (goods("Bolt", "10", "100"),))], decisions=(stale,))
    assert "stale_decision" in codes(r, 1) and r.orders[1].status == "needs_review"


# ---- the same data gives the same result


def test_the_result_does_not_depend_on_the_order_of_the_records() -> None:
    pos = [po(1, "CP-1"), po(2, "CP-2", (("Gear", "4", "25"),)), po(3, "CP-3", since=date(2026, 11, 2))]
    invoices = [
        inv(1, "2001", (goods("Bolt", "6", "100"),)),
        inv(2, "2002", (goods("Gear", "4", "26"),), ref="CP-2"),
        inv(3, "2003", (goods("Bolt", "4", "100"),), day=date(2026, 11, 9)),
        inv(4, "9", total="5", ref=None),
    ]

    def shape(result: Result) -> object:
        return (
            {k: (o.status, o.invoiced, [(p.code, p.message) for p in o.problems]) for k, o in result.orders.items()},
            sorted((k.po_id, k.invoice_id, k.invoice_line_id) for k in result.links),
            [p.message for p in result.unplaced],
        )

    first = shape(run(pos, invoices))
    for seed in range(5):
        random.Random(seed).shuffle(pos)  # noqa: S311
        random.Random(seed + 9).shuffle(invoices)  # noqa: S311
        assert shape(run(pos, invoices)) == first


def test_every_code_and_status_has_words_for_the_screens() -> None:
    assert all(label and level in ("confirmed", "preliminary") for label, level, _ in CODES.values())
    assert set(STATUSES) == {
        "cancelled",
        "closed",
        "needs_review",
        "over_invoiced",
        "matched",
        "matched_by_amount",
        "partially_invoiced",
        "no_invoice",
        "not_covered",
    }


def test_part_of_a_total_a_person_left_unplaced_is_said() -> None:
    r = run(
        [po(1, "CP-1"), po(2, "CP-2")],
        [inv(1, "9001", total="1500.00", ref="CP-1; CP-2")],
        decisions=(Decision(1, "confirmed", 1, 1, amount=D(1000)),),
    )
    (left,) = r.unplaced
    assert left.code == "unmatched_invoice" and left.amount == D(500) and "1,000.00 USD" in left.message
