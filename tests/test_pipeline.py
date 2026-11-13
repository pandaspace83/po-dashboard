"""The pipeline end to end with a stub model (PD-4.6, ARCHITECTURE §6 and §13): mail dropped in a folder mailbox is
scanned, stored, read by the text step (in the sandbox), queued, read by the stub model, matched and flagged; a person
accepts each document on the Review screen; and the PO's lines move from Sent to Closed with nothing typed by hand.

Everything is the app's own code except the model: the scanner, the folder mail source, the text step, the extractor,
matching, the validator, the pages and the ledger. All mail here is made up (DEC-13)."""

from __future__ import annotations

import io
import json
from collections.abc import Iterator
from datetime import UTC, datetime
from decimal import Decimal
from email.message import EmailMessage
from pathlib import Path
from typing import Any

import pytest
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas
from sqlalchemy import select
from sqlalchemy.orm import Session

from po_dashboard.config import Settings
from po_dashboard.db.ledger import POLine, PurchaseOrder, Vendor, VendorDomain
from po_dashboard.db.mail import Extraction, Mailbox, SourceDocument, SourceMessage
from po_dashboard.extraction import schema
from po_dashboard.extraction.runner import Extractor
from po_dashboard.intake.store import FileStore
from po_dashboard.ledger.status import LineStatus, POStatus, line_states, po_states
from po_dashboard.models import llm
from po_dashboard.scheduler.scanner import Scanner
from test_accept import form_for
from test_gate import env, new_client, post, sign_in  # noqa: F401
from test_matching import answer, line

T0 = datetime(2026, 11, 12, 12, 0, tzinfo=UTC)
D = Decimal
BUYER_BOX, DANA = "purchasing@example-buyer.test", "Dana Whitfield <dana@acme-fasteners.test>"


# ---- the mail, as it would arrive over a few weeks


def email(sender: str, to: str, subject: str, body: str, n: int, attach: bytes | None = None) -> bytes:
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = sender, to, subject
    msg["Message-ID"], msg["Date"] = f"<flow-{n}@example.test>", f"Mon, {n + 9:02} Nov 2026 09:30:00 +0000"
    msg.set_content(body)
    if attach is not None:
        msg.add_attachment(attach, maintype="application", subtype="pdf", filename="shipment-notice.pdf")
    return msg.as_bytes()


def pdf(lines: list[str]) -> bytes:
    out = io.BytesIO()
    page = canvas.Canvas(out, pagesize=letter)
    for i, text in enumerate(lines):
        page.drawString(72, 720 - 18 * i, text)
    page.save()
    return out.getvalue()


PO_MAIL = email(BUYER_BOX, DANA, "Purchase order 4580000", (
    "PURCHASE ORDER 4580000\n"
    "Line 1: BLT-M8x40, 100 EA at 14.90, wanted 2026-12-15\n"
    "Line 2: SEAL-22, 50 EA at 1.10, wanted 2026-12-15"
), 1)  # fmt: skip
ACK_MAIL = email(DANA, BUYER_BOX, "Order acknowledgement SO-7781", (
    "ORDER ACKNOWLEDGEMENT for your PO 4580000, our order SO-7781.\n"
    "Line 1: BLT-M8x40, 100 EA at 14.90, promised 12/10/2026.\n"
    "Line 2: SEAL-22, 50 EA at 1.10, promised 12/10/2026."
), 2)  # fmt: skip
SHIP_MAIL = email(DANA, BUYER_BOX, "Documents", "Please see attached.", 3, attach=pdf([
    "SHIPMENT NOTICE", "Your PO 4580000, our order SO-7781", "Shipped 12/08/2026 by UPS, tracking 1Z999AA10123456784",
    "Line 1: BLT-M8x40, 100 EA", "Line 2: SEAL-22, 20 EA",
]))  # fmt: skip
INVOICE_MAIL = email(DANA, BUYER_BOX, "Invoice INV-881", (
    "INVOICE INV-881 dated 12/09/2026 for your PO 4580000.\n"
    "Line 1: BLT-M8x40, 100 EA at 14.90 = 1,490.00\n"
    "Line 2: SEAL-22, 50 EA at 1.10 = 55.00\n"
    "Freight 25.00\n"
    "Total 1,570.00"
), 4)  # fmt: skip


def dated(no: int, item: str, qty: int, price: str | None, field: str, when: str) -> dict[str, Any]:
    return line(no, item, qty, price) | {field: when}


# What a perfect reader gives for each document, found by words only that document has
READINGS: dict[str, dict[str, Any]] = {
    "PURCHASE ORDER 4580000": answer(doc_type="purchase_order", po_number="4580000", lines=[
        dated(1, "BLT-M8x40", 100, "14.90", "requested_date", "2026-12-15"),
        dated(2, "SEAL-22", 50, "1.10", "requested_date", "2026-12-15"),
    ]),
    "ORDER ACKNOWLEDGEMENT": answer(po_number="4580000", vendor_order_number="SO-7781", lines=[
        dated(1, "BLT-M8x40", 100, "14.90", "promised_date", "2026-12-10"),
        dated(2, "SEAL-22", 50, "1.10", "promised_date", "2026-12-10"),
    ]),
    "SHIPMENT NOTICE": answer(doc_type="shipment_notice", po_number="4580000", vendor_order_number="SO-7781",
                              carrier="UPS", tracking_numbers=["1Z999AA10123456784"], ship_date="2026-12-08",
                              lines=[line(1, "BLT-M8x40", 100), line(2, "SEAL-22", 20)]),
    "INVOICE INV-881": answer(doc_type="invoice", po_number="4580000", invoice_number="INV-881",
                              invoice_date="2026-12-09", total=D("1570.00"),
                              lines=[line(1, "BLT-M8x40", 100, "14.90"), line(2, "SEAL-22", 50, "1.10")],
                              charges=[{"kind": "freight", "label": "Freight", "amount": D("25.00")}]),
}  # fmt: skip


class Reader:
    """The stub model: answers each document as a perfect reader would, and "other" for a covering note."""

    def __init__(self) -> None:
        self.calls = 0

    def __call__(self, settings: Settings, text: str, *, model: str | None = None, **_: Any) -> llm.Extraction:
        self.calls += 1
        said = next((reading for words, reading in READINGS.items() if words in text), answer(doc_type="other"))
        raw = json.dumps(said, default=float)
        return llm.Extraction(answer=schema.parse_answer(raw), seconds=1.0, raw=raw)


# ---- the app around the mailbox


class Office:
    """A folder mailbox, the app's scanner and extractor, and a buyer signed in to the pages."""

    def __init__(self, env: Any, mail_dir: Path) -> None:  # noqa: ANN401
        self.engine, self.settings, self.mail_dir = env[1], env[2], mail_dir
        store = FileStore(self.settings.data_dir / "originals")  # the app's own store, so the pages read the text
        self.scanner = Scanner(self.engine, store, model="stub:1b", now=lambda: T0)
        self.model = Reader()
        self.extractor = Extractor(self.engine, store, self.settings, extract=self.model, now=lambda: T0)
        self.client = new_client(env[0])
        assert sign_in(self.client, "buyer@example.com").status_code == 303
        self.dropped = 0

    def arrives(self, raw: bytes, folder: str = "Inbox") -> None:
        self.dropped += 1
        (self.mail_dir / folder / f"{self.dropped:03}.eml").write_bytes(raw)

    def scan_and_read(self) -> list[Extraction]:
        """One scan, then the extractor until it has nothing due. Returns the extractions it made."""
        report = self.scanner.scan()
        assert report.ran and not report.errors
        made = []
        while (outcome := self.extractor.run_once()).kind != "idle":
            assert outcome.kind == "done"
            made.append(outcome.extraction_id)
        with Session(self.engine) as db:
            return [db.get(Extraction, i) for i in made]  # type: ignore[misc]

    def accept(self, row: Extraction, **changes: str) -> None:
        done = post(self.client, f"/review/{row.id}/accept", form_for(row, **changes))  # no ticks: none is needed
        assert done.status_code == 303, done.text[-400:]

    def statuses(self, number: str = "4580000") -> tuple[POStatus, list[LineStatus]]:
        with Session(self.engine) as db:
            po = db.scalars(select(PurchaseOrder).where(PurchaseOrder.po_number == number)).one()
            states = line_states(db, po.id)
            by_no = {n: i for i, n in db.execute(select(POLine.id, POLine.line_no).where(POLine.po_id == po.id))}
            return po_states(db)[po.id].status, [states[by_no[n]].status for n in sorted(by_no)]


@pytest.fixture
def office(env, tmp_path: Path) -> Iterator[Office]:  # noqa: ANN001
    mail_dir = tmp_path / "mail"
    (mail_dir / "Inbox").mkdir(parents=True)
    (mail_dir / "Sent Items").mkdir()
    with Session(env[1]) as db:
        db.add(Mailbox(address=BUYER_BOX, provider="folder", location=str(mail_dir)))
        vendor = Vendor(name="Acme Fasteners")
        db.add(vendor)
        db.flush()
        db.add(VendorDomain(vendor_id=vendor.id, domain="acme-fasteners.test"))
        db.commit()
    yield Office(env, mail_dir)


def flags(row: Extraction) -> set[str]:
    return {f["flag"] for f in json.loads(row.flags or "[]")}


def run_the_flow(office: Office) -> None:
    """The PO goes out, then the vendor's acknowledgement, shipment and invoice come back, each accepted on Review."""
    office.arrives(PO_MAIL, "Sent Items")
    (po,) = office.scan_and_read()
    assert (po.review_state, flags(po)) == ("waiting", set())  # a clean document asks nothing of the person
    office.accept(po, f_currency="USD")
    assert office.statuses() == (POStatus.OPEN, [LineStatus.SENT, LineStatus.SENT])

    office.arrives(ACK_MAIL)
    (ack,) = office.scan_and_read()
    assert (ack.review_state, flags(ack)) == ("waiting", set())
    office.accept(ack)
    assert office.statuses() == (POStatus.OPEN, [LineStatus.ACKNOWLEDGED, LineStatus.ACKNOWLEDGED])

    office.arrives(SHIP_MAIL)
    body, notice = office.scan_and_read()  # the covering note and the attached PDF, one message
    assert body.review_state == "no_facts" and notice.review_state == "waiting"
    assert flags(notice) == {"qty_below_order"}  # 20 of 50 shipped: a note, no tick
    office.accept(notice)
    assert office.statuses() == (POStatus.OPEN, [LineStatus.SHIPPED, LineStatus.PARTIALLY_SHIPPED])

    office.arrives(INVOICE_MAIL)
    (bill,) = office.scan_and_read()
    assert (bill.review_state, flags(bill)) == ("waiting", {"charge_present"})
    office.accept(bill)
    assert office.statuses() == (POStatus.CLOSED, [LineStatus.CLOSED, LineStatus.CLOSED])


def test_the_mailbox_flows_to_review_and_accepting_moves_the_lines_to_closed(office: Office) -> None:
    run_the_flow(office)
    with Session(office.engine) as db:
        po = db.scalars(select(PurchaseOrder).where(PurchaseOrder.po_number == "4580000")).one()
        assert po.source == "document" and po.vendor_id is not None
        messages = {m.subject: m.id for m in db.scalars(select(SourceMessage))}
    # each entry on the PO's page links back to the message it came from (REQ-DB-02)
    page = office.client.get(f"/orders/{po.id}").text
    for subject in ("Order acknowledgement SO-7781", "Documents", "Invoice INV-881"):
        assert f'href="/review/message/{messages[subject]}">source document</a>' in page
    # the covering note and its attachment are reviewed together, on one page
    shipment_page = office.client.get(f"/review/message/{messages['Documents']}").text
    assert "No order facts" in shipment_page and "shipment-notice.pdf" in shipment_page
    assert office.model.calls == 5  # one reading per document: the PO, the ack, the note, the PDF, the invoice


def test_scanning_again_and_restarting_read_nothing_twice(office: Office, env) -> None:  # noqa: ANN001
    run_the_flow(office)
    with Session(office.engine) as db:
        before = (len(db.scalars(select(SourceDocument)).all()), len(db.scalars(select(Extraction)).all()))
    again = office.scanner.scan()
    assert again.stored == 0 and again.read == 0
    restarted = Extractor(office.engine, office.scanner.store, office.settings, extract=office.model, now=lambda: T0)
    assert restarted.recover() == 0 and restarted.run_once().kind == "idle"
    with Session(office.engine) as db:
        after = (len(db.scalars(select(SourceDocument)).all()), len(db.scalars(select(Extraction)).all()))
    assert after == before and office.model.calls == 5
    assert office.statuses() == (POStatus.CLOSED, [LineStatus.CLOSED, LineStatus.CLOSED])
