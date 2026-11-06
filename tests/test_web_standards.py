"""The web standards every screen follows (DEC-45): HTMX set up safely, session rules kept, lists sorted and paged."""

import hashlib
import json
import re
from datetime import timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, update
from sqlalchemy.orm import Session
from starlette.requests import Request

from po_dashboard.auth.gate import HTMX_FILE
from po_dashboard.db.base import utcnow
from po_dashboard.db.models import SignInSession
from po_dashboard.pages import tables
from test_gate import ORIGIN, PW, admin_client, env, new_client, sign_in, token_in  # noqa: F401
from test_orders_web import client, csv_of, csv_row, make_vendor, upload  # noqa: F401

STATIC = Path(__file__).parents[1] / "src" / "po_dashboard" / "static"
# The SHA-256 of htmx.org 2.0.11's dist/htmx.min.js, from the npm package whose sha512 matched the registry's record
# (2026-11-06). A changed file fails here: replacing it is a deliberate step, never an accident.
HTMX_SHA256 = "d6fdc75f204e6bdefa99b69bf1e6d4ac69b8a364f77929f45c13476b4000f717"
HX = {"HX-Request": "true", "Accept": "text/html"}


def htmx_config(html: str) -> dict:  # type: ignore[type-arg]
    found = re.search(r"<meta name=\"htmx-config\" content='([^']+)'>", html)
    assert found, "the page has no htmx-config"
    return json.loads(found.group(1))


# ---- HTMX itself


def test_the_pinned_htmx_file_is_exactly_the_published_one() -> None:
    assert HTMX_FILE == "/static/htmx-2.0.11.min.js"
    assert hashlib.sha256((STATIC / "htmx-2.0.11.min.js").read_bytes()).hexdigest() == HTMX_SHA256
    assert sorted(p.name for p in STATIC.glob("htmx*")) == ["htmx-2.0.11.min.js"]


def test_signed_in_pages_load_htmx_from_this_site_with_the_safe_settings(admin_client: TestClient) -> None:
    html = admin_client.get("/").text
    assert f'<script src="{HTMX_FILE}" defer></script>' in html and "unpkg" not in html and "cdn" not in html
    config = htmx_config(html)
    assert config["allowEval"] is False and config["allowScriptTags"] is False
    assert config["includeIndicatorStyles"] is False and config["selfRequestsOnly"] is True
    assert config["historyCacheSize"] == 0  # no copies of pages (prices, temporary passwords) in browser storage
    assert {"code": "[45]..", "swap": True, "error": True} in config["responseHandling"]
    assert '<body hx-boost="true"' in html and 'id="announce"' in html and "hx-preserve" in html


def test_the_sign_in_page_loads_no_script(env) -> None:  # noqa: ANN001
    html = new_client(env[0]).get("/sign-in").text
    assert "<script" not in html and "htmx-config" not in html


def test_the_htmx_file_needs_a_sign_in_and_may_be_cached(env, admin_client: TestClient) -> None:  # noqa: ANN001
    assert new_client(env[0]).get(HTMX_FILE).status_code == 401
    served = admin_client.get(HTMX_FILE)
    assert served.status_code == 200 and "no-store" not in served.headers.get("cache-control", "")
    assert admin_client.get("/static/app.js").headers["cache-control"] == "no-store"


@pytest.mark.parametrize("path", ["/", "/orders", "/orders/new", "/orders/import", "/reports/open-items", "/vendors",
                                  "/vendors/import", "/admin/users", "/admin/audit"])  # fmt: skip
def test_no_page_has_inline_script_style_or_script_attributes(admin_client: TestClient, path: str) -> None:
    html = admin_client.get(path).text
    assert re.search(r"<script(?![^>]*\bsrc=\"/static/)", html) is None and "<style" not in html, path
    assert ' style="' not in html and "hx-on" not in html and "js:" not in html and " on" + "click=" not in html, path


def test_downloads_are_plain_links_not_page_swaps(admin_client: TestClient) -> None:
    downloads = (("/reports/open-items", "/reports/open-items.csv"), ("/orders/import", "/orders/import/template"),
                 ("/vendors/import", "/vendors/import/template"))  # fmt: skip
    for path, link in downloads:
        html = admin_client.get(path).text
        tag = re.search(rf'<a [^>]*href="{re.escape(link)}[^"]*"[^>]*>', html)
        assert tag and 'hx-boost="false"' in tag.group(0) and "download" in tag.group(0), path


# ---- the session rules still hold


def test_an_expired_session_sends_an_htmx_page_to_the_sign_in_page(env) -> None:  # noqa: ANN001
    refused = new_client(env[0]).get("/orders", headers=HX)
    assert refused.status_code == 401 and refused.headers["hx-redirect"] == "/sign-in"
    plain = new_client(env[0]).get("/orders")
    assert plain.status_code == 401 and "hx-redirect" not in plain.headers


def _age_session(engine, minutes: int) -> None:  # noqa: ANN001
    with Session(engine) as db:
        db.execute(update(SignInSession).values(last_seen_at=utcnow() - timedelta(minutes=minutes)))
        db.commit()


def _last_seen(engine):  # noqa: ANN001, ANN202
    with Session(engine) as db:
        return db.scalars(select(SignInSession.last_seen_at)).one()


def test_a_background_update_does_not_count_as_activity(env, admin_client: TestClient) -> None:  # noqa: ANN001
    engine = env[1]
    _age_session(engine, 10)
    before = _last_seen(engine)
    assert admin_client.get("/", headers={**HX, "X-Background": "1"}).status_code == 200
    assert _last_seen(engine) == before  # not moved on by the page updating itself
    assert admin_client.get("/", headers=HX).status_code == 200
    assert _last_seen(engine) > before  # a real click does count


def test_a_page_left_open_is_still_signed_out_after_30_idle_minutes(env, admin_client: TestClient) -> None:  # noqa: ANN001
    _age_session(env[1], 31)
    refused = admin_client.get("/", headers={**HX, "X-Background": "1"})
    assert refused.status_code == 401 and refused.headers["hx-redirect"] == "/sign-in"


# ---- history: Back and Reload never land on an address that only answers a form


def test_a_refused_form_sent_by_htmx_is_kept_out_of_the_history(client: TestClient) -> None:
    page = client.get("/vendors")
    data = {"csrf_token": token_in(page.text), "name": "Acme"}
    saved = client.post("/vendors/create", data=data, headers={**ORIGIN, **HX})
    assert saved.status_code == 303 and "hx-push-url" not in saved.headers  # the page it lands on is pushed
    refused = client.post("/vendors/create", data=data, headers={**ORIGIN, **HX})  # the same name again
    assert refused.status_code == 400 and refused.headers["hx-push-url"] == "false"
    plain = client.post("/vendors/create", data=data, headers=ORIGIN)
    assert plain.status_code == 400 and "hx-push-url" not in plain.headers


def test_an_upload_can_carry_its_token_in_the_header_the_script_adds(client: TestClient) -> None:
    make_vendor(client)
    sent = upload(client, csv_of(csv_row("PO-77")))
    assert sent.status_code == 303 and sent.headers["location"].startswith("/orders?imported=1")


# ---- lists: sorted by a click on a heading, 50 a page


def _many_orders(client: TestClient, count: int) -> None:
    make_vendor(client)
    make_vendor(client, "Beta Supply", "beta.example")
    rows = [csv_row(f"PO-{n:03d}", "Acme Fasteners" if n % 2 else "Beta Supply") for n in range(1, count + 1)]
    assert upload(client, csv_of(*rows)).status_code == 303


def test_the_po_list_shows_50_a_page_and_says_how_many(client: TestClient) -> None:
    _many_orders(client, 55)
    first = client.get("/orders?sort=po").text
    assert first.count('<a href="/orders/') == 50 and "1–50 of 55 purchase orders" in first
    assert "PO-001" in first and "PO-050" in first and "PO-051" not in first and "Page 1 of 2" in first
    second = client.get("/orders?sort=po&page=2").text
    assert second.count('<a href="/orders/') == 5 and "51–55 of 55" in second and "PO-055" in second
    assert "51–55 of 55" in client.get("/orders?sort=po&page=99").text  # past the end: the last page
    assert "1–50 of 55" in client.get("/orders?sort=po&page=abc").text


def test_a_heading_sorts_and_a_second_click_reverses(client: TestClient) -> None:
    _many_orders(client, 3)
    up = client.get("/orders?sort=po").text
    down = client.get("/orders?sort=-po").text
    assert up.index("PO-001") < up.index("PO-003") and down.index("PO-003") < down.index("PO-001")
    assert 'aria-sort="ascending"' in up and 'href="/orders?sort=-po"' in up
    assert 'aria-sort="descending"' in down and 'href="/orders?sort=po"' in down
    by_vendor = client.get("/orders?sort=vendor").text
    assert by_vendor.index("Acme Fasteners</td>") < by_vendor.index("Beta Supply</td>")
    assert client.get("/orders?sort=nonsense").status_code == 200


def test_filters_update_the_list_in_place_and_keep_the_sort(client: TestClient) -> None:
    _many_orders(client, 3)
    html = client.get("/orders?sort=-po&q=PO-00").text
    form = re.search(r'<form method="get" action="/orders" id="filters"[^>]*>', html)
    assert form and 'hx-target="#results"' in form.group(0) and 'hx-push-url="true"' in form.group(0)
    assert '<input type="hidden" name="sort" value="-po" form="filters">' in html
    assert 'href="/orders?sort=po&amp;q=PO-00"' in html  # a heading keeps the filters


def _request(query: str) -> Request:
    return Request({"type": "http", "path": "/orders", "query_string": query.encode(), "headers": []})


def test_with_query_changes_one_value_and_a_new_sort_starts_at_page_one() -> None:
    assert tables.with_query(_request("q=x&page=3"), page=4) == "/orders?q=x&page=4"
    assert tables.with_query(_request("q=x&page=3"), sort="-po") == "/orders?q=x&sort=-po"
    assert tables.with_query(_request("q=x"), q="") == "/orders"
    assert tables.with_query(_request("q=a%26b"), page=2) == "/orders?q=a%26b&page=2"


def test_empty_values_sort_last_whichever_way() -> None:
    rows = [{"d": 2}, {"d": None}, {"d": 1}]
    up, _ = tables.sort_rows(rows, "d", {"d": lambda r: r["d"]}, "d")
    down, sorting = tables.sort_rows(rows, "-d", {"d": lambda r: r["d"]}, "d")
    assert [r["d"] for r in up] == [1, 2, None] and [r["d"] for r in down] == [2, 1, None]
    assert sorting.value == "-d" and sorting.aria("d") == "descending" and sorting.aria("x") == "none"
