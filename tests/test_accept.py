# ruff: noqa: F811 (fixtures are imported by name, as in the other test files)
"""Accept (PD-4.5, DEC-54, DEC-08): a person accepts a read document and the entry is recorded through the ledger code,
linked to the document, under the person's name, in one transaction. A stub model gives each answer."""

from __future__ import annotations

import json
import re
from decimal import Decimal
from email.message import EmailMessage
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from po_dashboard.db.ledger import (
    AckLine,
    Acknowledgement,
    Invoice,
    InvoiceCharge,
    InvoiceLine,
    POLine,
    PurchaseOrder,
    Shipment,
    ShipmentLine,
    ShipmentTracking,
)
from po_dashboard.db.mail import Extraction, SourceDocument, SourceMessage
from po_dashboard.db.models import AuditLog
from po_dashboard.extraction import queue
from po_dashboard.extraction.review import shown
from po_dashboard.intake.reading import read_pending
from po_dashboard.intake.store import FileStore
from po_dashboard.mail.source import SENT
from test_extraction_queue import Stub, extractor, rows
from test_gate import env, post  # noqa: F401
from test_intake import db, put  # noqa: F401
from test_matching import answer, line, world  # noqa: F401
from test_orders_web import client  # noqa: F401
from test_review import good, read, store  # noqa: F401

D = Decimal
ALL_TICKS = {  # the test sender is not a known address of the vendor, and the stub answers are not in the text
    "tick_sender_not_vendor": "1",
    "tick_value_not_in_source": "1",
    "tick_planted_instruction": "1",
}


def form_for(row: Extraction, **over: str) -> dict[str, str]:
    """What the Review page posts when nothing is edited: every field and line input, the version and the ticks."""
    reply = json.loads(row.answer or "{}")
    data: dict[str, str] = {"version": str(row.version)}
    for name in ("po_number", "vendor_order_number", "invoice_number", "invoice_date", "carrier", "ship_date",
                 "arrival_date", "total"):  # fmt: skip
        data[f"f_{name}"] = shown(reply.get(name))
    data["f_tracking_numbers"] = shown(reply.get("tracking_numbers"))
    for i, one in enumerate(reply.get("lines") or []):
        for key in ("line_no", "item", "qty", "unit", "unit_price", "promised_date", "arrival_date", "requested_date"):
            data[f"line_{i}_{key}"] = shown(one.get(key))
    for i, charge in enumerate(reply.get("charges") or []):
        for key in ("kind", "label", "amount"):
            data[f"charge_{i}_{key}"] = shown(charge.get(key))
    return data | over


def accept(client: TestClient, row: Extraction, **over: str):  # noqa: ANN201
    return post(
        client,
        f"/review/{row.id}/accept",
        form_for(row, **over) | ALL_TICKS | {k: v for k, v in over.items() if k.startswith("tick_")},
    )


def ack_reply(**changes: Any) -> dict[str, Any]:
    one = line(1, "BLT-M8x40", 100, "14.90") | {"promised_date": "2026-12-01"}
    return good(lines=[one]) | changes


# ---- an acknowledgement


def test_accepting_records_the_entry_linked_to_the_document_under_the_persons_name(
    env, db, store, world, client: TestClient
) -> None:  # noqa: ANN001
    (row,) = read(env, db, store, ack_reply())
    done = accept(client, row)
    assert done.status_code == 303, re.sub(r"\s+", " ", re.sub("<[^>]+>", " ", done.text))[-400:]
    db.expire_all()
    (kept,) = rows(db)
    assert kept.review_state == "accepted" and kept.entry_kind == "ack" and kept.reviewed_by is not None
    ack = db.scalars(select(Acknowledgement)).one()
    document = db.scalars(select(SourceDocument)).one()
    assert (ack.id, ack.po_id, ack.source_document_id) == (kept.entry_id, world["first"].id, document.id)
    assert ack.accepted_by == kept.reviewed_by
    one = db.scalars(select(AckLine)).one()
    assert (one.confirmed_qty, one.confirmed_price, str(one.promised_date)) == (D("100"), D("14.90"), "2026-12-01")
    assert json.loads(kept.answer or "")["lines"][0]["unit_price"] == 14.9  # the model's answer is kept as read
    final = json.loads(kept.final or "")
    assert final["lines"][0]["unit_price"] == "14.9" and "sender_not_vendor" in final["ticks"]
    audited = db.scalars(select(AuditLog).where(AuditLog.action == "extraction.accept")).one()
    assert json.loads(audited.after or "")["entry"] == "ack"
    assert db.scalars(select(AuditLog).where(AuditLog.action == "ack.record")).one() is not None


def test_a_second_accept_of_the_same_document_is_refused(env, db, store, world, client: TestClient) -> None:  # noqa: ANN001
    (row,) = read(env, db, store, ack_reply())
    stale = form_for(row)
    assert accept(client, row).status_code == 303
    again = post(client, f"/review/{row.id}/accept", stale | ALL_TICKS)
    assert again.status_code == 400 and "not waiting" in again.text
    assert len(db.scalars(select(Acknowledgement)).all()) == 1


def test_an_old_page_is_refused_by_its_version(env, db, store, world, client: TestClient) -> None:  # noqa: ANN001
    (row,) = read(env, db, store, ack_reply())
    refused = accept(client, row, version=str(row.version + 3))
    assert refused.status_code == 400 and "changed by someone else" in refused.text.lower()
    assert db.scalars(select(Acknowledgement)).first() is None and rows(db)[0].review_state == "waiting"


def test_what_the_person_typed_is_recorded_not_what_the_model_read(env, db, store, world, client: TestClient) -> None:  # noqa: ANN001
    (row,) = read(env, db, store, ack_reply())
    assert accept(client, row, line_0_unit_price="15.25", line_0_qty="90").status_code == 303
    db.expire_all()
    one = db.scalars(select(AckLine)).one()
    assert (one.confirmed_qty, one.confirmed_price) == (D("90"), D("15.25"))
    audited = db.scalars(select(AuditLog).where(AuditLog.action == "extraction.accept")).one()
    changed = json.loads(audited.after or "")["changed"]
    assert changed == ["line_0_qty", "line_0_unit_price"]  # names only, never the values
    assert json.loads(rows(db)[0].answer or "")["lines"][0]["qty"] == 100
    page = client.get(f"/review/message/{db.scalars(select(SourceMessage)).one().id}").text
    assert 'name="line_0_unit_price" value="15.25"' in page and 'name="line_0_qty" value="90"' in page


def test_the_tick_for_an_odd_sender_is_needed_and_a_refusal_keeps_nothing(
    env, db, store, world, client: TestClient
) -> None:  # noqa: ANN001
    (row,) = read(env, db, store, ack_reply())
    refused = post(client, f"/review/{row.id}/accept", form_for(row))  # no tick
    assert refused.status_code == 400 and "Tick each box" in refused.text and "sender not vendor" in refused.text
    assert db.scalars(select(Acknowledgement)).first() is None and rows(db)[0].review_state == "waiting"
    assert accept(client, row).status_code == 303


def test_a_refusal_midway_leaves_neither_the_entry_nor_the_new_state(env, db, store, world, client: TestClient) -> None:  # noqa: ANN001
    reply = ack_reply(lines=[line(1, "BLT-M8x40", 100, "14.90"), line(2, "SEAL-22", 10, "1.10")])
    (row,) = read(env, db, store, reply)
    refused = accept(client, row, line_1_qty="ten")  # the second line is wrong, after the first was parsed
    assert refused.status_code == 400 and "Line 2" in refused.text and "not a number" in refused.text
    db.expire_all()
    assert db.scalars(select(Acknowledgement)).first() is None and db.scalars(select(AckLine)).first() is None
    assert rows(db)[0].review_state == "waiting"
    assert db.scalars(select(AuditLog).where(AuditLog.action == "ack.record")).first() is None


def test_an_acknowledgement_with_no_lines_is_acknowledged_with_no_details_for_every_line(
    env, db, store, world, client: TestClient
) -> None:  # noqa: ANN001
    (row,) = read(env, db, store, good(lines=[]))
    assert accept(client, row).status_code == 303
    db.expire_all()
    assert len(db.scalars(select(AckLine)).all()) == 2  # both lines of PO 4569586
    assert all(a.confirmed_qty is None and a.promised_date is None for a in db.scalars(select(AckLine)))


# ---- lines: assigned or left out


def test_a_line_matching_nothing_blocks_accept_until_it_is_assigned_or_left_out(
    env, db, store, world, client: TestClient
) -> None:  # noqa: ANN001
    reply = ack_reply(lines=[line(None, "BLT-M8x40", 100, "14.90"), line(None, "MYSTERY", 5, "1.00")])
    (row,) = read(env, db, store, reply)
    blocked = accept(client, row)
    assert blocked.status_code == 400 and "Line 2 matches no line on PO 4569586" in blocked.text
    assert db.scalars(select(Acknowledgement)).first() is None
    assigned = accept(client, row, line_1_line_no="2")  # typing the PO line number assigns it
    assert assigned.status_code == 303
    db.expire_all()
    assert len(db.scalars(select(AckLine)).all()) == 2


def test_a_line_can_be_left_out(env, db, store, world, client: TestClient) -> None:  # noqa: ANN001
    reply = ack_reply(lines=[line(None, "BLT-M8x40", 100, "14.90"), line(None, "MYSTERY", 5, "1.00")])
    (row,) = read(env, db, store, reply)
    assert accept(client, row, line_1_skip="1").status_code == 303
    db.expire_all()
    assert len(db.scalars(select(AckLine)).all()) == 1
    assert json.loads(rows(db)[0].final or "")["lines"][1]["left_out"] is True


def test_every_line_left_out_records_nothing(env, db, store, world, client: TestClient) -> None:  # noqa: ANN001
    (row,) = read(env, db, store, ack_reply())
    refused = accept(client, row, line_0_skip="1")
    assert refused.status_code == 400 and "at least one line" in refused.text
    assert db.scalars(select(Acknowledgement)).first() is None


# ---- a shipment and an invoice


def test_a_shipment_notice_keeps_every_tracking_number(env, db, store, world, client: TestClient) -> None:  # noqa: ANN001
    one = line(1, "BLT-M8x40", 60) | {"arrival_date": "2026-12-03"}
    reply = answer(doc_type="shipment_notice", po_number="4569586", carrier="UPS", ship_date="2026-11-30",
                   tracking_numbers=["1Z999AA10123456784", "1Z999AA10123456785"], lines=[one])  # fmt: skip
    (row,) = read(
        env, db, store, reply, body="PO 4569586 shipped UPS 1Z999AA10123456784 1Z999AA10123456785 BLT-M8x40 60"
    )
    assert accept(client, row).status_code == 303
    db.expire_all()
    ship = db.scalars(select(Shipment)).one()
    assert (ship.carrier, ship.tracking_number, str(ship.shipped_at)) == ("UPS", "1Z999AA10123456784", "2026-11-30")
    assert ship.source_document_id == db.scalars(select(SourceDocument)).one().id
    assert sorted(t.tracking_number for t in db.scalars(select(ShipmentTracking))) == [
        "1Z999AA10123456784", "1Z999AA10123456785"]  # fmt: skip
    only = db.scalars(select(ShipmentLine)).one()
    assert (only.qty, str(only.eta)) == (D("60"), "2026-12-03")


def invoice_reply(**changes: Any) -> dict[str, Any]:
    charges = [{"kind": "freight", "label": "Freight", "amount": D("12.50")},
               {"kind": "other", "label": "Early pay discount", "amount": D("-3.00")}]  # fmt: skip
    return answer(doc_type="invoice", po_number="4569586", invoice_number="INV-77", invoice_date="2026-12-05",
                  total=D("1499.50"), lines=[line(1, "BLT-M8x40", 100, "14.90")], charges=charges) | changes  # fmt: skip # noqa: E501


def test_an_invoice_is_recorded_with_its_charges_including_a_discount(
    env, db, store, world, client: TestClient
) -> None:  # noqa: ANN001
    body = "Invoice INV-77 PO 4569586 BLT-M8x40 100 14.90 Freight 12.50 Early pay discount -3.00 total 1499.50"
    (row,) = read(env, db, store, invoice_reply(), body=body)
    assert accept(client, row).status_code == 303
    db.expire_all()
    bill = db.scalars(select(Invoice)).one()
    assert (bill.invoice_number, bill.total, bill.source_document_id) == (
        "INV-77",
        D("1499.50"),
        db.scalars(select(SourceDocument)).one().id,
    )
    assert db.scalars(select(InvoiceLine)).one().price == D("14.90")
    charges = {c.label: (c.kind, c.amount) for c in db.scalars(select(InvoiceCharge))}
    assert charges == {"Freight": ("freight", D("12.50")), "Early pay discount": ("other", D("-3.00"))}


def test_an_invoice_with_no_number_needs_one_typed_and_a_bad_charge_records_nothing(
    env, db, store, world, client: TestClient
) -> None:  # noqa: ANN001
    (row,) = read(env, db, store, invoice_reply(invoice_number=None))
    refused = accept(client, row)
    assert refused.status_code == 400 and "Invoice number is needed" in refused.text
    bad = accept(client, row, f_invoice_number="INV-9", charge_0_kind="bribe")
    assert bad.status_code == 400 and "Charge 1" in bad.text
    db.expire_all()
    assert db.scalars(select(Invoice)).first() is None and db.scalars(select(InvoiceCharge)).first() is None
    assert accept(client, row, f_invoice_number="INV-9").status_code == 303
    assert db.scalars(select(Invoice)).one().invoice_number == "INV-9"


def test_an_invoice_number_already_recorded_is_refused_and_the_document_stays_waiting(
    env, db, store, world, client: TestClient
) -> None:  # noqa: ANN001
    (row,) = read(env, db, store, invoice_reply())
    db.add(Invoice(po_id=world["first"].id, invoice_number="inv-77"))
    db.commit()
    refused = accept(client, row)
    assert refused.status_code == 400 and "already recorded" in refused.text
    db.expire_all()
    assert rows(db)[0].review_state == "waiting" and len(db.scalars(select(Invoice)).all()) == 1


# ---- an emailed PO


def read_sent(env, db: Session, store: FileStore, reply: dict[str, Any]) -> list[Extraction]:  # noqa: ANN001
    msg = EmailMessage()
    msg["From"], msg["To"] = "purchasing@example-buyer.test", "Dana <dana@acme.test>"
    msg["Subject"], msg["Message-ID"] = "Purchase order", "<po1@example-buyer.test>"
    msg["Date"] = "Mon, 09 Nov 2026 09:30:00 +0000"
    msg.set_content("PO 4580000. Line 1 BLT-M8x40 qty 5 EA at 14.90, wanted 2026-12-15.")
    put(db, store, msg.as_bytes(), folder=SENT)
    read_pending(db, store, limit=500)
    queue.queue_missing(db, "stub:1b")
    db.commit()
    run = extractor(env, store, Stub(*[json.loads(json.dumps(reply, default=float))] * 5))
    while run.run_once().kind == "done":
        pass
    return rows(db)


def po_reply(number: str = "4580000", qty: Any = 5) -> dict[str, Any]:
    one = line(1, "BLT-M8x40", qty, "14.90") | {"requested_date": "2026-12-15"}
    return answer(doc_type="purchase_order", po_number=number, lines=[one])


def test_a_new_emailed_po_is_created_for_the_recipients_vendor(env, db, store, world, client: TestClient) -> None:  # noqa: ANN001
    (row,) = read_sent(env, db, store, po_reply())
    page = client.get(f"/review/message/{db.scalars(select(SourceMessage)).one().id}").text
    assert 'name="f_currency" value="USD"' in page
    assert accept(client, row, f_currency="EUR").status_code == 303
    db.expire_all()
    order = db.scalars(select(PurchaseOrder).where(PurchaseOrder.po_number == "4580000")).one()
    assert (order.source, order.currency, order.vendor_id) == ("document", "EUR", world["acme"].id)
    made = db.scalars(select(POLine).where(POLine.po_id == order.id)).one()
    assert (made.ordered_qty, made.unit_price, str(made.requested_date)) == (D("5"), D("14.90"), "2026-12-15")
    kept = rows(db)[0]
    assert (kept.review_state, kept.entry_kind, kept.entry_id, kept.po_id) == ("accepted", "po", order.id, order.id)


def test_a_revised_emailed_po_shows_its_differences_only_and_changes_nothing(
    env, db, store, world, client: TestClient
) -> None:  # noqa: ANN001
    (row,) = read_sent(env, db, store, po_reply(number="4569586", qty=120))
    page = client.get(f"/review/message/{db.scalars(select(SourceMessage)).one().id}").text
    assert "what this document says differently" in page
    assert "Line 1: quantity is 100 on the PO, 120 in the document." in page
    assert "PO line 2 is not in the document." in page
    assert (
        "price is" not in page.lower().split("what this document says differently")[1].split("</ul>")[0]
    )  # same price
    refused = accept(client, row)
    assert refused.status_code == 400 and "already exists" in refused.text
    db.expire_all()
    assert (
        db.scalars(select(POLine).where(POLine.po_id == world["first"].id, POLine.line_no == 1)).one().ordered_qty
        == 100
    )


# ---- the timeline names the source document


def test_the_pos_timeline_links_each_accepted_line_to_its_source_document(
    env, db, store, world, client: TestClient
) -> None:  # noqa: ANN001
    (row,) = read(env, db, store, ack_reply())
    assert accept(client, row).status_code == 303
    message = db.scalars(select(SourceMessage)).one()
    page = client.get(f"/orders/{world['first'].id}").text
    assert f'href="/review/message/{message.id}">source document</a>' in page
