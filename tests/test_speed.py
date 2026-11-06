"""Every main page answers within 0.3 seconds on a large made-up data set (DEC-45 item 14).

300 vendors, 1,000 POs and 5,000 lines, with acknowledgements on 600 POs (some late, some slipped). Each page is
asked for twice first, so the measurement is of the page itself, not of start-up; the slowest of three tries counts.
"""

import time
from datetime import date, timedelta
from decimal import Decimal as D
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from po_dashboard.auth import service
from po_dashboard.config import Settings
from po_dashboard.db import ledger as L
from po_dashboard.db.migrate import upgrade
from po_dashboard.ledger import events as E
from po_dashboard.web import create_app
from test_gate import PW, new_client, sign_in

LIMIT_SECONDS = 0.3


@pytest.fixture(scope="module")
def big(tmp_path_factory: pytest.TempPathFactory):  # noqa: ANN201
    settings = Settings(data_dir=Path(tmp_path_factory.mktemp("speed")) / "data")
    engine = upgrade(settings)
    today = date.today()
    with Session(engine) as db:
        service.create_user(db, "boss@example.com", "admin", password=PW)
        vendors = [L.Vendor(name=f"Vendor {n:03d}") for n in range(300)]
        db.add_all(vendors)
        db.flush()
        db.add_all(L.VendorDomain(vendor_id=v.id, domain=f"v{n:03d}.example") for n, v in enumerate(vendors))
        orders = [
            L.PurchaseOrder(po_number=f"PO-{n:05d}", vendor_id=vendors[n % 300].id, currency="USD",
                            date_sent=today - timedelta(days=n % 60))
            for n in range(1000)
        ]  # fmt: skip
        db.add_all(orders)
        db.flush()
        db.add_all(
            L.POLine(po_id=o.id, line_no=k, item=f"Part {o.id}-{k}", ordered_qty=D("10"), unit="EA",
                     unit_price=D("2.5"))
            for o in orders
            for k in range(1, 6)
        )
        db.commit()
        for o in orders[:600]:
            ids = list(db.scalars(select(L.POLine.id).where(L.POLine.po_id == o.id)))
            promised = today + timedelta(days=(o.id % 20) - 10)  # half in the past: late
            E.record_ack(db, o, [E.AckInput(i, promised_date=promised) for i in ids])
            if o.id % 25 == 0:
                E.record_ack(db, o, [E.AckInput(ids[0], promised_date=promised + timedelta(days=7))])
        db.commit()
        some_po = orders[10].id
    app = create_app(settings, engine)
    client = new_client(app)
    assert sign_in(client, "boss@example.com").status_code == 303
    yield client, some_po
    engine.dispose()


PAGES = [
    "/", "/orders", "/orders?sort=expected&page=10", "/orders?late=1&sort=-late", "/orders?q=PO-009",
    "/reports/open-items", "/reports/open-items?show=late&sort=vendor", "/vendors", "/vendors?q=Vendor 2&sort=-pos",
    "/admin/audit", "/vendors/search?q=Vendor 1", "PO",
]  # fmt: skip


@pytest.mark.parametrize("path", PAGES)
def test_each_page_answers_within_the_limit(big, path: str) -> None:  # noqa: ANN001
    client, some_po = big
    url = f"/orders/{some_po}" if path == "PO" else path
    for _ in range(2):
        assert client.get(url).status_code == 200
    slowest = 0.0
    for _ in range(3):
        start = time.perf_counter()
        response = client.get(url)
        slowest = max(slowest, time.perf_counter() - start)
        assert response.status_code == 200
    assert slowest < LIMIT_SECONDS, f"{url} took {slowest:.3f} s"
