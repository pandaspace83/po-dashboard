"""Drive real Chrome through every browser test case in test-evidence/test_record.toml, the way a tester would, and
save a screenshot of each result in test-evidence/screenshots/. The results go to test-evidence/chrome_run.json, which
scripts/test_record.py reads.

    uv run --with playwright==1.64.1 python scripts/browser_evidence.py            # every browser case, in order
    uv run --with playwright==1.64.1 python scripts/browser_evidence.py --headed   # watch it

It starts the app on a free port with a new, empty data folder in the system's temporary folder, and deletes that
folder at the end: the real data folder is never opened. Made-up data only. It uses the Google Chrome installed on this
Mac (no browser download). Cases run in the order of the test record, because later cases use what earlier ones made,
exactly as a person following the workbook would. A failed check is recorded with a screenshot of the moment
(<ID>-fail.png) and the run goes on.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import tomllib
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx
import uvicorn
from playwright.sync_api import Browser, BrowserContext, Page, sync_playwright
from sqlalchemy.orm import Session

from po_dashboard.auth import service
from po_dashboard.config import Settings
from po_dashboard.db.mail import Extraction, Mailbox, SourceDocument, SourceMessage
from po_dashboard.db.migrate import upgrade
from po_dashboard.ledger.orders import TEMPLATE as PO_TEMPLATE
from po_dashboard.web import create_app

ROOT = Path(__file__).resolve().parent.parent
EVIDENCE = ROOT / "test-evidence"
SHOTS = EVIDENCE / "screenshots"
DATA = ROOT / "tests" / "data"
ADMIN, BUYER = "boss@example.com", "buyer@example.com"
# Made up, for a throwaway database that is deleted at the end of the run.
PASSWORD = "correct horse battery staple"  # noqa: S105
BUYER_PASSWORD = "maple river lantern seven"  # noqa: S105
NOT_RIGHT = "The email or password is not right."
TODAY = date.today()


class Failed(AssertionError):
    pass


def check(ok: object, what: str) -> None:
    if not ok:
        raise Failed(what)


CASES: dict[str, Callable[[Run], None]] = {}


def case(cid: str) -> Callable[[Callable[[Run], None]], Callable[[Run], None]]:
    def register(fn: Callable[[Run], None]) -> Callable[[Run], None]:
        CASES[cid] = fn
        return fn

    return register


class Run:
    """The browser state shared by the cases: the admin's and the buyer's windows, and what earlier cases made."""

    def __init__(self, browser: Browser, base: str, work: Path) -> None:
        self.browser, self.base, self.work = browser, base, work
        self.admin_ctx = self.context()
        self.a = self.admin_ctx.new_page()  # the admin's window
        self.buyer_ctx = self.context()
        self.b = self.buyer_ctx.new_page()  # the buyer's window
        self.last: Page = self.a
        self.current = ""
        self.shots: list[str] = []
        self.problems: list[str] = []
        self.engine: Any = None  # the app's database, for cases that need a made-up row the app cannot yet make
        self.saved: dict[str, str] = {}  # urls of things made by earlier cases
        self.temporary = ""

    def context(self, **options: object) -> BrowserContext:
        ctx = self.browser.new_context(viewport={"width": 1280, "height": 800}, accept_downloads=True, **options)
        ctx.on("page", self.watch)
        return ctx

    def watch(self, page: Page) -> None:
        page.on("pageerror", lambda e: self.problems.append(f"{self.current}: script error: {e}"))

    # ---- helpers

    def go(self, page: Page, path: str) -> Page:
        page.goto(self.base + path)
        self.settle(page, 300)
        self.last = page
        return page

    def settle(self, page: Page, ms: int = 700) -> None:
        page.wait_for_timeout(ms)
        self.last = page

    def main(self, page: Page) -> str:
        return page.inner_text("main")

    def focus_id(self, page: Page) -> str:
        return str(page.evaluate("document.activeElement && document.activeElement.id"))

    def sign_in(self, page: Page, email: str, password: str) -> None:
        self.go(page, "/sign-in")
        page.fill("#email", email)
        page.fill("#password", password)
        page.click("main button[type=submit]")
        self.settle(page, 900)

    def sign_out(self, page: Page) -> None:
        if page.locator("details.account summary").count():
            page.click("details.account summary")
            page.click("details.account button:has-text('Sign out')")
            page.wait_for_url(re.compile(r".*/sign-in"))
            self.settle(page, 300)

    def shot(self, page: Page | None = None, *, full: bool = False) -> None:
        page = page or self.last
        name = f"{self.current}.png" if not self.shots else f"{self.current}-{len(self.shots) + 1}.png"
        page.screenshot(path=str(SHOTS / name), caret="initial", full_page=full)
        self.shots.append(f"screenshots/{name}")

    def upload(self, page: Page, path: Path, button: str = "form button.primary") -> None:
        page.set_input_files("#file", str(path))
        page.click(button)
        self.settle(page, 1300)

    def open_po(self, page: Page, number: str) -> None:
        self.go(page, f"/orders?q={number}")
        page.click(f"#results a:text-is('{number}')")
        page.wait_for_url(re.compile(r".*/orders/\d+$"))
        self.settle(page, 500)

    def pick_vendor(self, page: Page, box: str, typed: str) -> None:
        page.click(f"#{box}_text")
        page.fill(f"#{box}_text", typed)
        self.settle(page, 800)
        page.click(f"#{box}_list li[role=option] >> nth=0")
        self.settle(page, 300)

    def new_po(self, page: Page, number: str, vendor: str, item: str, qty: str, unit: str, price: str,
               sent: date | None = None) -> None:  # fmt: skip
        self.go(page, "/orders/new")
        page.fill("#po_number", number)
        if sent:
            page.fill("#date_sent", sent.isoformat())
        self.pick_vendor(page, "vendor_id", vendor)
        page.fill("#currency", "usd")
        page.fill("input[name=line_item] >> nth=0", item)
        page.fill("input[name=line_quantity] >> nth=0", qty)
        page.fill("input[name=line_unit] >> nth=0", unit)
        page.fill("input[name=line_unit_price] >> nth=0", price)
        page.click("form button.primary[type=submit]")
        self.settle(page, 1200)

    def ack(self, page: Page, promised: date, qty: str = "", price: str = "") -> None:
        page.click("a:has-text('Record acknowledgement')")
        page.wait_for_url(re.compile(r".*/ack/new$"))
        self.settle(page, 400)
        if qty:
            page.fill("input[name=line_qty] >> nth=0", qty)
        if price:
            page.fill("input[name=line_price] >> nth=0", price)
        page.fill("input[name=line_promised] >> nth=0", promised.isoformat())
        page.click("form button.primary[type=submit]")
        page.wait_for_url(re.compile(r".*/orders/\d+$"))
        self.settle(page, 600)

    def open_timeline(self, page: Page) -> None:
        page.evaluate(
            "document.querySelectorAll('details.timeline, details.timeline details').forEach(d => d.open = true)"
        )
        self.settle(page, 200)


# ---------------------------------------------------------------------------------------------------------------------
# Sign-in and accounts


@case("TC-SIGN-01")
def sign_in_page_only(r: Run) -> None:
    r.go(r.a, "/")
    check(r.a.url.endswith("/sign-in"), f"the address is {r.a.url}, not /sign-in")
    check(r.a.locator("details.account").count() == 0, "account details are shown to a signed-out visitor")
    try:  # autofocus is applied once the page has loaded: wait for it instead of reading it at a fixed moment (DEF-009)
        r.a.wait_for_function("document.activeElement && document.activeElement.id === 'email'", timeout=5000)
    except Exception:  # noqa: BLE001, S110 - the check below says it plainly
        pass
    check(r.focus_id(r.a) == "email", "the cursor is not in Email")
    r.shot(r.a)


@case("TC-SIGN-02")
def same_refusal(r: Run) -> None:
    r.sign_in(r.a, ADMIN, "not the password at all")
    first = r.a.inner_text("main [role=alert]")
    r.sign_in(r.a, "nobody@example.com", "not the password at all")
    second = r.a.inner_text("main [role=alert]")
    check(NOT_RIGHT in first and first == second, f"messages differ: {first!r} / {second!r}")
    check(r.a.url.endswith("/sign-in"), "left the sign-in page")
    r.shot(r.a)


@case("TC-SIGN-03")
def home_and_menu(r: Run) -> None:
    r.sign_in(r.a, ADMIN, PASSWORD)
    check(r.a.url == r.base + "/", f"landed on {r.a.url}")
    r.a.click("details.account summary")
    menu = r.a.inner_text("details.account")
    check("Change password" in menu and "Sign out" in menu and ADMIN in menu, f"menu reads {menu!r}")
    r.shot(r.a)
    r.a.keyboard.press("Escape")
    check(not r.a.is_visible("details.account .menu"), "Esc did not close the menu")
    r.a.click("details.account summary")
    r.a.click("h1")
    check(not r.a.is_visible("details.account .menu"), "a click elsewhere did not close the menu")


@case("TC-SIGN-04")
def add_person(r: Run) -> None:
    r.go(r.a, "/")
    r.a.click("nav a[href='/admin/users']")
    r.a.wait_for_url(re.compile(r".*/admin/users$"))
    r.settle(r.a, 400)
    r.a.fill("#email", BUYER)
    r.a.select_option("#role", "buyer")
    r.a.click("form[action='/admin/users/create'] button.primary")
    r.settle(r.a, 1000)
    secret = r.a.locator("code.secret")
    check(secret.count() == 1, "no temporary password shown")
    r.temporary = secret.inner_text().strip()
    check(len(r.temporary) == 20, f"the temporary password has {len(r.temporary)} characters")
    r.shot(r.a)
    r.a.click("nav a[href='/admin/users']")
    r.settle(r.a, 700)
    check(r.a.locator("code.secret").count() == 0, "the temporary password is shown again")


@case("TC-SIGN-05")
def temporary_then_change(r: Run) -> None:
    r.sign_in(r.b, BUYER, r.temporary)
    check(r.b.url.endswith("/change-password"), f"a temporary password landed on {r.b.url}")
    r.go(r.b, "/")
    check(r.b.url.endswith("/change-password"), "Home did not send the person back to Change password")
    r.b.fill("#current", r.temporary)
    r.b.fill("#new", "abc")
    r.b.fill("#again", "abc")
    r.b.click("main button.primary")
    r.settle(r.b, 900)
    refusal = r.b.inner_text("main [role=alert]") if r.b.locator("main [role=alert]").count() else ""
    refusal = refusal or r.b.evaluate("document.getElementById('new').validationMessage")  # the browser's own check
    check(refusal and r.b.url.endswith("/change-password"), "abc was not refused with a reason")
    r.shot(r.b)
    r.b.fill("#current", r.temporary)
    r.b.fill("#new", BUYER_PASSWORD)
    r.b.fill("#again", BUYER_PASSWORD)
    r.b.click("main button.primary")
    r.b.wait_for_url(r.base + "/")
    r.settle(r.b, 500)
    check(r.b.locator("nav a[href='/admin/users']").count() == 0, "a buyer has a People link")


@case("TC-SIGN-06")
def buyer_refused_admin(r: Run) -> None:
    r.go(r.b, "/admin/audit")
    audit = r.main(r.b)
    r.go(r.b, "/admin/users")
    people = r.main(r.b)
    check("Admins only" in people and "Admins only" in audit, "an admin screen opened for a buyer")
    check("Go to Home" in people and '{"detail"' not in r.b.content(), "the refusal is raw code, not a page")
    check(r.b.locator("nav a[href='/admin/audit']").count() == 0, "a buyer has an Audit link")
    r.shot(r.b)


@case("TC-SIGN-07")
def disabled_person(r: Run) -> None:
    r.sign_out(r.b)
    r.go(r.a, "/admin/users")
    r.a.click(f"tr:has-text('{BUYER}') button:has-text('Disable')")
    r.settle(r.a, 900)
    r.sign_in(r.b, BUYER, BUYER_PASSWORD)
    check(NOT_RIGHT in r.main(r.b) and r.b.url.endswith("/sign-in"), "a disabled buyer was let in")
    r.shot(r.b)
    r.go(r.a, "/admin/users")
    r.a.click(f"tr:has-text('{BUYER}') button:has-text('Enable')")
    r.settle(r.a, 900)
    r.sign_in(r.b, BUYER, BUYER_PASSWORD)
    check(r.b.url == r.base + "/", "an enabled buyer could not sign in")
    r.sign_out(r.b)


@case("TC-SIGN-08")
def lockout(r: Run) -> None:
    for _ in range(6):
        r.sign_in(r.b, BUYER, "a wrong password again")
    check("Too many wrong attempts" in r.main(r.b), "no lock after wrong passwords")
    r.sign_in(r.b, BUYER, BUYER_PASSWORD)
    check(
        "Too many wrong attempts" in r.main(r.b) and r.b.url.endswith("/sign-in"),
        "the right password got in while locked",
    )
    r.shot(r.b)
    r.go(r.a, "/admin/users")
    r.a.click(f"tr:has-text('{BUYER}') button:has-text('Unlock')")
    r.settle(r.a, 900)
    r.sign_in(r.b, BUYER, BUYER_PASSWORD)
    check(r.b.url == r.base + "/", "the buyer could not sign in after Unlock")


@case("TC-SIGN-11")
def sign_out_and_in(r: Run) -> None:
    r.go(r.a, "/")
    r.sign_out(r.a)
    check(r.a.url.endswith("/sign-in"), "Sign out did not reach the sign-in page")
    r.sign_in(r.a, ADMIN, PASSWORD)
    check(r.a.url == r.base + "/" and "security token" not in r.main(r.a).lower(), "signing in again failed")
    r.shot(r.a)


# ---------------------------------------------------------------------------------------------------------------------
# Vendor import


@case("TC-VIMP-01")
def vendor_file(r: Run) -> None:
    r.go(r.a, "/vendors")
    r.a.click("a:has-text('Import vendors')")
    r.a.wait_for_url(re.compile(r".*/vendors/import$"))
    r.settle(r.a, 500)
    check(r.focus_id(r.a) == "file", "the cursor is not on the file chooser")
    r.upload(r.a, DATA / "vendors_sample.xlsx")
    result = r.a.inner_text("#import-result")
    check("3 created" in result and r.a.url.endswith("/vendors/import"), f"the import says {result!r}")
    r.shot(r.a)
    r.go(r.a, "/vendors")
    listing = r.main(r.a)
    for name in ("Acme Fasteners", "Northwind Metals", "Initech Tools", "acme.example", "northwind.example"):
        check(name in listing, f"{name} is not on the Vendors page")


@case("TC-VIMP-02")
def vendor_file_again(r: Run) -> None:
    r.go(r.a, "/vendors/import")
    r.upload(r.a, DATA / "vendors_sample.xlsx")
    result = r.a.inner_text("#import-result")
    check("0 created" in result and "3 already up to date" in result, f"the second import says {result!r}")
    r.shot(r.a)


@case("TC-VIMP-03")
def vendor_rows_refused(r: Run) -> None:
    r.go(r.a, "/vendors/import")
    r.upload(r.a, DATA / "vendors_problems.csv")
    result = r.a.inner_text("#import-result")
    for words in ("1 created", "Contact email is required", "already the contact email of", "already belongs to"):
        check(words in result, f"{words!r} missing from {result!r}")
    r.shot(r.a)


@case("TC-VIMP-05")
def old_excel(r: Run) -> None:
    old = r.work / "vendors.xls"
    old.write_bytes(bytes.fromhex("d0cf11e0a1b11ae1") + b"\0" * 504)  # the start of an old-format Excel file
    r.go(r.a, "/vendors/import")
    r.upload(r.a, old)
    result = r.a.inner_text("#import-result")
    check("old Excel format (.xls)" in result and "Save it as .xlsx or CSV" in result, f"the page says {result!r}")
    r.shot(r.a)


# ---------------------------------------------------------------------------------------------------------------------
# Vendors


@case("TC-VEN-01")
def add_vendor(r: Run) -> None:
    r.go(r.a, "/vendors")
    r.a.click("a:has-text('Add a vendor')")
    r.a.wait_for_url(re.compile(r".*/vendors/new$"))
    r.settle(r.a, 400)
    check(r.focus_id(r.a) == "name", "the cursor is not in Name")
    r.a.fill("#name", "Contoso Bearings")
    r.a.fill("#domains", "contoso.example, mail.contoso.example")
    r.a.click("form button.primary")
    r.a.wait_for_url(re.compile(r".*/vendors/\d+"))
    r.settle(r.a, 500)
    page = r.main(r.a)
    check("contoso.example" in page and "mail.contoso.example" in page, "the domains are not on the vendor page")
    r.saved["contoso"] = r.a.url.split("?")[0]
    r.shot(r.a)


@case("TC-VEN-02")
def vendor_name_taken(r: Run) -> None:
    r.go(r.a, "/vendors/new")
    r.a.fill("#name", "contoso bearings")
    r.a.fill("#contact_name", "Kept Example")
    r.a.click("form button.primary")
    r.settle(r.a, 900)
    check("A vendor with that name already exists" in r.main(r.a), "the duplicate name was not refused")
    check(r.a.input_value("#name") == "contoso bearings" and r.a.input_value("#contact_name") == "Kept Example",
          "the boxes lost what was typed")  # fmt: skip
    r.shot(r.a)


def add_domain(r: Run, domain: str) -> None:
    r.a.fill("#domains", domain)
    r.a.click("form[action$='/domains/add'] button.primary")
    r.settle(r.a, 900)


@case("TC-VEN-03")
def domain_of_another(r: Run) -> None:
    r.go(r.a, r.saved["contoso"].replace(r.base, ""))
    add_domain(r, "northwind.example")
    alert = r.a.inner_text("main [role=alert]") if r.a.locator("main [role=alert]").count() else ""
    check("already belongs to Northwind Metals" in alert, f"the page says {alert!r}")
    r.shot(r.a)


@case("TC-VEN-04")
def domains_checked(r: Run) -> None:
    r.go(r.a, r.saved["contoso"].replace(r.base, ""))
    add_domain(r, "not a domain")
    check(r.a.locator("main [role=alert]").count() == 1, "'not a domain' was not refused")
    add_domain(r, "contoso.co.uk")
    check("contoso.co.uk" in r.main(r.a), "contoso.co.uk was not added")
    r.a.click("button[aria-label='Remove contoso.co.uk']")
    r.settle(r.a, 900)
    check("contoso.co.uk" not in r.main(r.a), "contoso.co.uk was not removed")
    add_domain(r, "CONTOSO.example")
    check(
        "contoso.example is already added to this vendor" in r.main(r.a), "the same domain in capitals was not caught"
    )
    r.shot(r.a)


@case("TC-VEN-05")
def vendor_saved(r: Run) -> None:
    r.go(r.a, r.saved["contoso"].replace(r.base, ""))
    r.a.fill("#notes", "Made-up note: ships on Mondays")
    r.a.click("form[action$='/update'] button.primary")
    r.settle(r.a, 800)
    check(r.a.locator(".notice.fades").count() == 1, "no Saved note")
    r.shot(r.a)
    r.a.wait_for_timeout(4000)
    check(r.a.locator(".notice.fades").count() == 0, "the Saved note did not fade")


@case("TC-VEN-06")
def vendor_without_domain(r: Run) -> None:
    r.go(r.a, "/vendors/new")
    r.a.fill("#name", "Hooli Plastics")
    r.a.click("form button.primary")
    r.a.wait_for_url(re.compile(r".*/vendors/\d+"))
    r.go(r.a, "/vendors?q=Hooli")
    check("no domain: replies cannot be matched" in r.main(r.a), "the vendor is not marked as having no domain")
    r.shot(r.a)


@case("TC-VEN-07")
def vendor_search(r: Run) -> None:
    r.go(r.a, "/vendors")
    everyone = r.a.locator("#results tbody tr").count()
    r.a.fill("#q", "north")
    r.settle(r.a, 1300)
    rows = r.a.locator("#results tbody tr").count()
    check(rows == 1 and "Northwind Metals" in r.a.inner_text("#results") and "q=north" in r.a.url,
          f"{rows} rows at {r.a.url}")  # fmt: skip
    r.shot(r.a)
    r.a.go_back()
    r.settle(r.a, 1300)
    check(r.a.locator("#results tbody tr").count() == everyone, "Back did not show the full list")
    r.a.click("h1")
    r.a.keyboard.press("/")
    check(r.focus_id(r.a) == "q", "/ did not put the cursor in search")
    r.a.click("th a.sort:has-text('Vendor')")
    r.settle(r.a, 900)
    first = r.a.inner_text("#results tbody tr:first-child td:first-child")
    r.a.click("th a.sort:has-text('Vendor')")
    r.settle(r.a, 900)
    again = r.a.inner_text("#results tbody tr:first-child td:first-child")
    check(first != again, f"the order did not reverse ({first} / {again})")


@case("TC-VEN-08")
def markup_as_text(r: Run) -> None:
    r.go(r.a, "/vendors/new")
    r.a.fill("#name", "<b>Bold</b>")
    r.a.click("form button.primary")
    r.a.wait_for_url(re.compile(r".*/vendors/\d+"))
    r.settle(r.a, 500)
    check("<b>Bold</b>" in r.a.inner_text("h1") and r.a.locator("h1 b").count() == 0, "the markup was run")
    r.go(r.a, "/vendors?q=Bold")
    check(
        "<b>Bold</b>" in r.a.inner_text("#results") and r.a.locator("#results b").count() == 0,
        "the list ran the markup",
    )
    r.shot(r.a)


# ---------------------------------------------------------------------------------------------------------------------
# Purchase orders


@case("TC-PO-01")
def po_by_hand(r: Run) -> None:
    r.go(r.a, "/orders")
    r.a.click("a:has-text('New purchase order')")
    r.a.wait_for_url(re.compile(r".*/orders/new$"))
    r.settle(r.a, 400)
    check(r.focus_id(r.a) == "po_number", "the cursor is not in PO number")
    check(r.a.input_value("#buyer") == ADMIN, "Buyer is not the signed-in person")
    r.new_po(r.a, "PO-1001", "Acme", "Bolt", "10.5", "EA", "2.5", sent=TODAY - timedelta(days=5))
    check(re.search(r"/orders/\d+$", r.a.url), f"no PO page: {r.a.url}")
    page = r.main(r.a)
    for words in ("PO PO-1001", "USD", "10.5", "2.50", "Sent"):
        check(words in page, f"{words!r} is not on the PO page")
    r.saved["po1001"] = r.a.url
    r.shot(r.a)


@case("TC-PO-02")
def po_reload(r: Run) -> None:
    r.go(r.a, r.saved["po1001"].replace(r.base, ""))
    for _ in range(3):
        r.a.reload()
        r.settle(r.a, 400)
    r.go(r.a, "/orders?q=PO-1001")
    check(r.a.locator("#results tbody tr").count() == 1, "Reload made a second PO-1001")
    r.shot(r.a)


@case("TC-PO-03")
def po_refused(r: Run) -> None:
    r.new_po(r.a, "PO-1002", "Acme", "Nut", "0", "EA", "1")
    page = r.main(r.a)
    check("Line 1: Quantity must be more than zero" in page, "the zero quantity was not refused by line and field")
    check(r.a.evaluate("document.activeElement.getAttribute('role')") == "alert", "the cursor is not on the problem")
    check(r.a.input_value("#po_number") == "PO-1002" and r.a.input_value("input[name=line_item] >> nth=0") == "Nut",
          "the boxes lost what was typed")  # fmt: skip
    r.shot(r.a)
    r.a.go_back()
    r.settle(r.a, 800)
    r.a.go_forward()
    r.settle(r.a, 800)
    check("not found" not in r.main(r.a).lower(), "Back and Forward gave 'not found'")


@case("TC-PO-04")
def po_decimals(r: Run) -> None:
    r.new_po(r.a, "PO-1002", "Acme", "Nut", "1.00001", "EA", "1")
    check("4 decimal places" in r.main(r.a) and r.a.url.endswith("/orders/new"), "1.00001 was not refused")
    r.shot(r.a)


@case("TC-PO-05")
def vendor_box(r: Run) -> None:
    r.go(r.a, "/orders/new")
    r.a.click("#vendor_id_text")
    r.a.keyboard.type("acm")
    r.settle(r.a, 900)
    check(r.a.locator("#vendor_id_list li[role=option]").count() >= 1, "no list under the vendor box")
    r.shot(r.a)
    r.a.keyboard.press("ArrowDown")
    r.a.keyboard.press("Enter")
    r.settle(r.a, 300)
    check(r.a.input_value("#vendor_id_text") == "Acme Fasteners", "Enter did not pick Acme Fasteners")
    r.a.fill("#vendor_id_text", "Acme")
    r.a.fill("#po_number", "PO-1009")
    r.a.fill("input[name=line_item] >> nth=0", "Bolt")
    r.a.fill("input[name=line_quantity] >> nth=0", "1")
    r.a.fill("input[name=line_unit] >> nth=0", "EA")
    r.a.fill("input[name=line_unit_price] >> nth=0", "1")
    r.a.click("form button.primary[type=submit]")
    r.settle(r.a, 600)
    message = r.a.evaluate("document.getElementById('vendor_id_text').validationMessage")
    check("Pick a vendor from the list" in message and r.a.url.endswith("/orders/new"), f"the box says {message!r}")


@case("TC-PO-07")
def po_filters(r: Run) -> None:
    r.go(r.a, "/orders")
    check(r.focus_id(r.a) == "q", "the cursor is not in Search")
    r.a.fill("#q", "1001")
    r.settle(r.a, 1300)
    check(r.a.locator("#results tbody tr").count() == 1 and "q=1001" in r.a.url, "Search did not find PO-1001")
    check(re.search(r"\d\d/\d\d/\d{4}", r.a.inner_text("#results")), "dates do not read MM/DD/YYYY")
    r.a.select_option("#status", index=1)
    r.settle(r.a, 1200)
    check("status=" in r.a.url, "Status did not apply by itself")
    r.a.fill("#sent_from", (TODAY - timedelta(days=30)).isoformat())
    r.a.locator("#sent_from").dispatch_event("change")
    r.settle(r.a, 1200)
    check("sent_from=" in r.a.url, "Sent from did not apply by itself")
    r.shot(r.a)
    r.a.click("#filters a:has-text('Clear')")
    r.settle(r.a, 1000)
    check("status=" not in r.a.url and "q=" not in r.a.url, f"Clear left {r.a.url}")
    r.a.fill("#q", "%")
    r.settle(r.a, 1300)
    check("No purchase orders match" in r.main(r.a), "% matched something")


@case("TC-PO-09")
def buyer_uses_pages(r: Run) -> None:
    for path, heading in (
        ("/vendors", "Vendors"),
        ("/orders", "Purchase orders"),
        ("/orders/new", "New purchase order"),
    ):
        r.go(r.b, path)
        check(heading in r.b.inner_text("h1"), f"{path} did not open for a buyer")
    r.shot(r.b)


# ---------------------------------------------------------------------------------------------------------------------
# Purchase order import


@case("TC-PIMP-01")
def po_file(r: Run) -> None:
    r.go(r.a, "/orders")
    r.a.click("a:has-text('Import CSV or Excel')")
    r.a.wait_for_url(re.compile(r".*/orders/import$"))
    r.settle(r.a, 400)
    check(r.focus_id(r.a) == "file", "the cursor is not on the file chooser")
    with r.a.expect_download() as got:
        r.a.click("a:has-text('Download a template')")
    check(got.value.suggested_filename.endswith(".csv"), "the template is not a .csv file")
    r.upload(r.a, DATA / "purchase_orders_sample.csv")
    check("Imported 2 purchase orders" in r.main(r.a) and "PO-2001" in r.main(r.a), "the sample did not import")
    r.shot(r.a)


@case("TC-PIMP-02")
def po_file_again(r: Run) -> None:
    r.go(r.a, "/orders/import")
    r.upload(r.a, DATA / "purchase_orders_sample.csv")
    check(
        "PO number PO-2001 already exists. Nothing is overwritten." in r.main(r.a), "the second import was not refused"
    )
    check(r.a.url.endswith("/orders/import"), "the page moved away from Import")
    r.shot(r.a)


@case("TC-PIMP-03")
def po_bad_row(r: Run) -> None:
    r.go(r.a, "/orders/import")
    r.upload(r.a, DATA / "purchase_orders_bad_row.csv")
    result = r.a.inner_text("#import-result")
    check("Row 3" in result and "abc" in result, f"the bad row is not named: {result!r}")
    r.shot(r.a)
    r.go(r.a, "/orders?q=PO-2101")
    check("No purchase orders match" in r.main(r.a), "part of the bad file was imported")


@case("TC-PIMP-06")
def quickbooks_on_import(r: Run) -> None:
    r.go(r.a, "/orders")
    r.a.click("a:has-text('Import CSV or Excel')")
    r.a.wait_for_url(re.compile(r".*/orders/import$"))
    r.settle(r.a, 400)
    r.upload(r.a, DATA / "quickbooks_open_pos_110526.csv")
    page = r.main(r.a)
    check("Missing column" not in page and "This file is the QuickBooks Open POs report" in page, "no hand-over")
    check("Nothing has changed yet" in page and "Create 3" in r.a.evaluate("document.activeElement.textContent"),
          "the preview is not ready to create")  # fmt: skip
    r.shot(r.a)
    r.a.click("a:has-text('Cancel')")
    r.settle(r.a, 600)


# ---------------------------------------------------------------------------------------------------------------------
# QuickBooks starting load


@case("TC-QB-01")
def quickbooks_preview(r: Run) -> None:
    r.go(r.a, "/orders")
    r.a.click("a:has-text('Load from QuickBooks')")
    r.a.wait_for_url(re.compile(r".*/orders/quickbooks$"))
    r.settle(r.a, 500)
    check(r.focus_id(r.a) == "file", "the cursor is not on the file chooser")
    r.upload(r.a, DATA / "quickbooks_open_pos_110526.csv", "button:has-text('Show what it would do')")
    page = r.main(r.a)
    for words in ("Nothing has changed yet", "3 purchase orders with 6 lines", "Globex Wire", "partly received",
                  "10.00 ÷ 3 = 3.3333", "exported 11/05/2026"):  # fmt: skip
        check(words in page, f"{words!r} is not in the preview")
    check(r.a.input_value("#export_date") == "2026-11-05", "the export date was not read from the name")
    check("Create 3" in r.a.evaluate("document.activeElement.textContent"), "the cursor is not on Create")
    r.shot(r.a)
    other = r.admin_ctx.new_page()
    r.go(other, "/orders?q=500")
    check("No purchase orders match" in r.main(other), "the preview created something")
    other.close()
    r.last = r.a


@case("TC-QB-02")
def quickbooks_create(r: Run) -> None:
    r.a.click("button:has-text('Create 3 purchase orders')")
    r.a.wait_for_url(re.compile(r".*source=quickbooks.*"))
    r.settle(r.a, 600)
    page = r.main(r.a)
    check(
        "Loaded 3 purchase orders from QuickBooks" in page and "1 is waiting for its vendor" in page, "no Loaded note"
    )
    for number in ("5001", "5002", "5004"):
        check(number in r.a.inner_text("#results"), f"{number} is not listed")
    r.shot(r.a)


@case("TC-QB-03")
def quickbooks_twice(r: Run) -> None:
    r.a.go_back()
    r.settle(r.a, 1200)
    if r.a.locator("button:has-text('Create 3 purchase orders')").count():
        r.a.click("button:has-text('Create 3 purchase orders')")
        r.settle(r.a, 1000)
        check("expired or was already used" in r.main(r.a), "the old preview was accepted again")
    r.shot(r.a)
    r.go(r.a, "/orders?q=500")
    check(r.a.locator("#results tbody tr").count() == 3, "the file was loaded twice")


@case("TC-QB-04")
def quickbooks_po_page(r: Run) -> None:
    r.open_po(r.a, "5002")
    page = r.main(r.a)
    for words in ("not set", "PO date (QuickBooks)", "10/22/2026", "Sheet:Alu 2mm", "for Café Racing",
                  "Partially shipped", "Received per QuickBooks export of 11/05/2026"):  # fmt: skip
        check(words in page, f"{words!r} is not on PO 5002")
    r.shot(r.a, full=True)


@case("TC-QB-05")
def quickbooks_rounded(r: Run) -> None:
    r.open_po(r.a, "5001")
    r.open_timeline(r.a)
    page = r.main(r.a)
    check("Unit price 3.3333 worked out from the QuickBooks total 10.00 ÷ 3, rounded (export of 11/05/2026)" in page,
          "the rounding is not explained in the timeline")  # fmt: skip
    order = [page.find(item) for item in ("Bolts", "Nuts", "Washers")]
    check(-1 not in order and order == sorted(order), "the lines are not in file order")
    r.a.locator("text=worked out from the QuickBooks total").first.scroll_into_view_if_needed()
    r.shot(r.a)


@case("TC-QB-06")
def quickbooks_no_ack_flag(r: Run) -> None:
    r.go(r.a, "/")
    todo = r.a.inner_text("#live")
    check(not re.search(r"\b500[124]\b", todo), "a loaded PO is flagged for an acknowledgement")
    r.shot(r.a)


@case("TC-QB-07")
def quickbooks_again(r: Run) -> None:
    r.go(r.a, "/orders/quickbooks")
    r.upload(r.a, DATA / "quickbooks_open_pos_110526.csv", "button:has-text('Show what it would do')")
    page = r.main(r.a)
    check("Nothing new to create" in page and "3</strong> already" in r.a.inner_html("main") and "Globex Wire" in page,
          "the second load offers something")  # fmt: skip
    check(r.a.locator("form[action='/orders/quickbooks/create']").count() == 0, "a Create button is offered")
    r.shot(r.a)
    r.go(r.a, "/vendors/new")
    r.a.fill("#name", "Globex Wire")
    r.a.fill("#contact_email", "sales@globex.example")
    r.a.click("form button.primary")
    r.a.wait_for_url(re.compile(r".*/vendors/\d+"))
    r.go(r.a, "/orders/quickbooks")
    r.upload(r.a, DATA / "quickbooks_open_pos_110526.csv", "button:has-text('Show what it would do')")
    check("<strong>1</strong> purchase order " in r.a.inner_html("main"), "Globex's PO is not offered")
    r.a.click("button:has-text('Create 1 purchase order')")
    r.a.wait_for_url(re.compile(r".*source=quickbooks.*"))
    r.go(r.a, "/orders?q=500")
    check(r.a.locator("#results tbody tr").count() == 4, "there are not four QuickBooks POs")
    r.shot(r.a)


@case("TC-QB-08")
def quickbooks_wrong_files(r: Run) -> None:
    r.go(r.a, "/orders/quickbooks")
    r.upload(r.a, DATA / "vendors_sample.xlsx", "button:has-text('Show what it would do')")
    refused = "This does not look like the QuickBooks Open POs report: it has no 'date', 'num'"
    r.a.wait_for_selector(f"text={refused}", timeout=10000)  # the answer arrives after the click (DEF-013)
    check(refused in r.main(r.a), "the vendor file was not refused by name")
    r.shot(r.a)
    template = r.work / "purchase-orders-template.csv"
    template.write_text(PO_TEMPLATE)
    r.upload(r.a, template, "button:has-text('Show what it would do')")
    r.a.wait_for_selector("text=app's own import format", timeout=10000)
    check("app's own import format" in r.main(r.a), "the app's own file was not recognised")
    r.shot(r.a)
    r.a.click("a:has-text('Go to Import CSV or Excel')")
    r.a.wait_for_url(re.compile(r".*/orders/import$"))


# ---------------------------------------------------------------------------------------------------------------------
# Entries by hand


@case("TC-EVT-01")
def short_ack(r: Run) -> None:
    r.go(r.a, r.saved["po1001"].replace(r.base, ""))
    r.a.click("a:has-text('Record acknowledgement')")
    r.a.wait_for_url(re.compile(r".*/ack/new$"))
    r.settle(r.a, 400)
    check(
        r.a.evaluate("document.activeElement.name") == "line_qty", "the cursor is not in the first Confirmed quantity"
    )
    r.a.go_back()
    r.settle(r.a, 600)
    r.ack(r.a, TODAY + timedelta(days=14), qty="6", price="2.75")
    page = r.main(r.a)
    for words in ("Acknowledged", "The vendor's price 2.75 differs from the PO price 2.5", "confirmed 6, not 10.5",
                  "Update the original purchase order"):  # fmt: skip
        check(words in page, f"{words!r} is not on the PO page")
    r.shot(r.a)


@case("TC-EVT-02")
def slipped(r: Run) -> None:
    r.go(r.a, r.saved["po1001"].replace(r.base, ""))
    r.ack(r.a, TODAY + timedelta(days=28))
    check("slipped" in r.main(r.a).lower() and "→" in r.main(r.a), "the later date is not shown as a slip")
    r.shot(r.a)


def ship(r: Run, qty: str) -> None:
    r.a.click("a:has-text('Record shipment')")
    r.a.wait_for_url(re.compile(r".*/shipments/new$"))
    r.settle(r.a, 400)
    r.a.fill("#carrier", "UPS")
    r.a.fill("#tracking_number", "1Z999AA10123456784")
    r.a.fill("input[name=line_qty] >> nth=0", qty)
    r.a.click("form button.primary[type=submit]")
    r.a.wait_for_url(re.compile(r".*/orders/\d+$"))
    r.settle(r.a, 600)


@case("TC-EVT-03")
def shipments(r: Run) -> None:
    r.go(r.a, r.saved["po1001"].replace(r.base, ""))
    ship(r, "3")
    check("Partially shipped" in r.main(r.a), "the first shipment did not make it Partially shipped")
    ship(r, "3")
    page = r.main(r.a)
    check(
        "Shipped" in page and "Partially shipped" not in r.a.inner_text("table >> nth=0"),
        "not Shipped after the second",
    )
    link = r.a.locator("a:has-text('Track with the carrier')").first
    check(link.count() and "ups.com" in (link.get_attribute("href") or "") and link.get_attribute("target") == "_blank",
          "no carrier link that opens in a new tab")  # fmt: skip
    check(ADMIN in page, "the history does not say who entered the shipment")
    r.shot(r.a, full=True)


def invoice(r: Run, number: str, qty: str, price: str = "") -> str:
    r.a.click("a:has-text('Record invoice')")
    r.a.wait_for_url(re.compile(r".*/invoices/new$"))
    r.settle(r.a, 400)
    start = r.a.input_value("input[name=line_price] >> nth=0")
    r.a.fill("#invoice_number", number)
    r.a.fill("input[name=line_qty] >> nth=0", qty)
    if price:
        r.a.fill("input[name=line_price] >> nth=0", price)
    r.a.click("form button.primary[type=submit]")
    r.a.wait_for_url(re.compile(r".*/orders/\d+$"))
    r.settle(r.a, 600)
    return start


@case("TC-EVT-04")
def first_invoice(r: Run) -> None:
    r.go(r.a, r.saved["po1001"].replace(r.base, ""))
    start = invoice(r, "INV-1", "6")
    check(start in ("2.5", "2.50"), f"the price box started at {start!r}, not the PO price")
    lines = r.a.inner_text("table >> nth=0")
    check("Shipped" in lines and "4.5" in lines, "the short-confirmed line did not stay open with 4.5")
    check("Update the original purchase order" in r.main(r.a), "the update-the-PO note went")
    r.shot(r.a)


@case("TC-EVT-05")
def over_billing(r: Run) -> None:
    r.go(r.a, r.saved["po1001"].replace(r.base, ""))
    invoice(r, "INV-2", "20", "3")
    page = r.main(r.a)
    check("more than the 10.5 ordered" in page and "above the PO price" in page, "over-billing was not flagged")
    check("Closed" in r.a.inner_text("table >> nth=0"), "the line is not Closed")
    r.shot(r.a, full=True)


@case("TC-EVT-06")
def remove_entry(r: Run) -> None:
    r.go(r.a, r.saved["po1001"].replace(r.base, ""))
    form = r.a.locator("form[action*='/entries/invoice/']:has(input[name=reason])").last
    form.locator("button:has-text('Remove')").click()
    r.settle(r.a, 400)
    asked = form.locator("input[name=reason]").evaluate("box => box.validationMessage")
    check(asked, "Remove went ahead with no reason")
    form.locator("input[name=reason]").fill("Made-up reason: entered twice")
    form.locator("button:has-text('Remove')").click()
    r.settle(r.a, 1000)
    page = r.main(r.a)
    check("INV-2" not in r.a.inner_text("table >> nth=0") and "Shipped" in r.a.inner_text("table >> nth=0"),
          "the line did not return to Shipped")  # fmt: skip
    check("more than the 10.5 ordered" not in page, "INV-2's notes stayed")
    r.shot(r.a)


@case("TC-EVT-08")
def late_line(r: Run) -> None:
    r.open_po(r.a, "PO-2001")
    r.ack(r.a, TODAY - timedelta(days=5))
    check("days late" in r.main(r.a), "the PO page does not show the line as late")
    r.shot(r.a)
    r.go(r.a, "/orders")
    check(re.search(r"1 late, up to \d+ days", r.main(r.a)), "the list does not say 1 late")
    r.a.check("input[name=late]")
    r.settle(r.a, 1200)
    check("PO-2001" in r.a.inner_text("#results") and "late=1" in r.a.url, "Late only does not find PO-2001")


@case("TC-EVT-09")
def settle_and_reopen(r: Run) -> None:
    r.open_po(r.a, "PO-2002")
    before = r.a.inner_text("table >> nth=0")
    r.a.evaluate("document.querySelector('details.settle').open = true")
    r.a.select_option("select[name=line_action] >> nth=0", "cancelled")
    r.a.click("details.settle button.primary")
    r.settle(r.a, 400)
    check(
        r.a.evaluate("document.getElementById('settle_reason').validationMessage"), "Settle went ahead with no reason"
    )
    r.a.fill("#settle_reason", "Made-up reason: no longer needed")
    r.a.click("details.settle button.primary")
    r.settle(r.a, 1200)
    check(
        "Cancelled" in r.a.inner_text("table >> nth=0") and "Settled by hand" in r.main(r.a),
        "the line is not Cancelled",
    )
    r.shot(r.a)
    form = r.a.locator("form[action*='/reopen']").first
    form.locator("input[name=reason]").fill("Made-up reason: needed after all")
    form.locator("button:has-text('Reopen')").click()
    r.settle(r.a, 1200)
    check("Cancelled" not in r.a.inner_text("table >> nth=0") and "Sent" in before, "Reopen did not restore the line")
    r.shot(r.a)


@case("TC-EVT-10")
def timeline(r: Run) -> None:
    r.go(r.a, r.saved["po1001"].replace(r.base, ""))
    r.open_timeline(r.a)
    text = r.a.inner_text("details.timeline")
    for words in ("Ordered", "Vendor acknowledged", "Shipped", "Invoiced", "Entry taken back"):
        check(words in text, f"{words!r} is not in the timeline")
    check(re.search(r"\d\d/\d\d/\d{4} \d{1,2}:\d\d [AP]M", text), "times do not read like 11/06/2026 2:35 PM")
    r.a.locator("details.timeline").scroll_into_view_if_needed()
    r.shot(r.a)


# ---------------------------------------------------------------------------------------------------------------------
# Home and Open items


@case("TC-HOME-01")
def home_counts(r: Run) -> None:
    r.go(r.a, "/")
    tiles = r.a.locator("#live a.tile")  # the six purchasing counts; the health strip above them is separate (DEF-012)
    check(tiles.count() == 6, f"{tiles.count()} tiles, not six")
    check("Ask the vendor for a new date" in r.a.inner_text("#live"), "Needs attention has no next step")
    r.shot(r.a, full=True)


@case("TC-HOME-02")
def home_updates_itself(r: Run) -> None:
    r.go(r.a, "/")
    r.open_po(r.b, "5004")
    r.ack(r.b, TODAY - timedelta(days=3))
    r.a.bring_to_front()
    for _ in range(16):
        r.a.wait_for_timeout(5000)
        if re.search(r"\b5004\b", r.a.inner_text("#live")):
            break
    check(re.search(r"\b5004\b", r.a.inner_text("#live")), "Home did not update itself within 80 seconds")
    r.last = r.a
    r.shot(r.a, full=True)


@case("TC-HOME-03")
def open_items(r: Run) -> None:
    r.go(r.a, "/")
    r.a.click("a.tile:has-text('Late')")
    r.a.wait_for_url(re.compile(r".*/reports/open-items.*"))
    r.settle(r.a, 600)
    check("days late" in r.a.inner_text("#results tbody tr:first-child"), "the first open item is not a late one")
    r.shot(r.a)
    r.pick_vendor(r.a, "vendor", "Initech")
    r.settle(r.a, 1200)
    check("All clear" in r.main(r.a), "an empty view does not say All clear")


@case("TC-HOME-04")
def csv_download(r: Run) -> None:
    r.new_po(r.a, "PO-1003", "Acme", "=1+1", "2", "EA", "1")
    r.go(r.a, "/reports/open-items")
    with r.a.expect_download() as got:
        r.a.click("a:has-text('Download CSV')")
    download = got.value
    check(download.suggested_filename.startswith("open-items-"), "the file name is not open-items-<date>.csv")
    saved = r.work / download.suggested_filename
    download.save_as(saved)
    rows = list(csv.reader(saved.read_text(encoding="utf-8-sig").splitlines()))
    cells = [cell for row in rows for cell in row]
    check("'=1+1" in cells and "=1+1" not in cells, "the formula cell is not plain text")
    check(any(re.fullmatch(r"\d\d/\d\d/\d{4}", cell) for cell in cells), "the CSV dates do not read MM/DD/YYYY")
    check(any("Café Racing" in cell for cell in cells), "the accent in Café Racing is not right")
    check(r.a.url.split("?")[0].endswith("/reports/open-items"), "the download replaced the page")
    r.shot(r.a)


# ---------------------------------------------------------------------------------------------------------------------
# Audit


@case("TC-AUD-01")
def audit(r: Run) -> None:
    r.go(r.a, "/")
    r.a.click("nav a[href='/admin/audit']")
    r.a.wait_for_url(re.compile(r".*/admin/audit$"))
    r.settle(r.a, 500)
    check(r.focus_id(r.a) == "action", "the cursor is not in Action")
    r.a.fill("#action", "line.close")
    r.settle(r.a, 1300)
    check(
        "action=line.close" in r.a.url and "line.close" in r.a.inner_text("#results"), "Action did not narrow the list"
    )
    r.shot(r.a)
    r.a.click("#filters a:has-text('Clear')")
    r.settle(r.a, 1000)
    check("action=" not in r.a.url, "Clear did not reset")
    r.go(r.a, "/admin/audit?date_from=soon")
    check("not a date" in r.main(r.a).lower() and r.a.locator("#results tbody tr").count() > 0,
          "a wrong date was not reported, or the list went")  # fmt: skip


# ---------------------------------------------------------------------------------------------------------------------
# Page behaviour


@case("TC-WEB-01")
def no_white_flash(r: Run) -> None:
    r.go(r.a, "/")
    r.a.evaluate("window.__same = 'yes'")
    for href, title in (("/orders", "Purchase orders"), ("/vendors", "Vendors"), ("/reports/open-items", "Open items")):
        r.a.click(f"nav a[href='{href}']")
        r.a.wait_for_url(re.compile(f".*{href}$"))
        r.settle(r.a, 500)
        check(r.a.title().startswith(title), f"the tab title did not follow to {title}")
    check(r.a.evaluate("window.__same") == "yes", "a page change reloaded the whole page")
    r.a.go_back()
    r.settle(r.a, 900)
    r.a.go_forward()
    r.settle(r.a, 900)
    check("not found" not in r.main(r.a).lower() and r.a.url.endswith("/reports/open-items"), "Back or Forward failed")
    r.shot(r.a)


@case("TC-WEB-02")
def phone_width(r: Run) -> None:
    ctx = r.browser.new_context(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True)
    phone = ctx.new_page()
    r.sign_in(phone, ADMIN, PASSWORD)
    po = r.saved["po1001"].replace(r.base, "")
    wide = {}
    for path in ("/", "/orders", po, "/vendors", "/reports/open-items", "/admin/users", "/admin/audit", "/orders/new"):
        r.go(phone, path)
        wide[path] = phone.evaluate("document.documentElement.scrollWidth - window.innerWidth")
    check(all(px <= 1 for px in wide.values()), f"pages scroll sideways: {wide}")
    r.go(phone, "/orders")
    r.shot(phone)
    ctx.close()
    r.last = r.a


@case("TC-WEB-03")
def keyboard(r: Run) -> None:
    r.go(r.a, "/")
    r.a.reload()
    r.settle(r.a, 500)
    r.a.keyboard.press("Tab")
    r.settle(r.a, 200)
    check(r.a.evaluate("document.activeElement.classList.contains('skip')") and r.a.is_visible("a.skip"),
          "Tab does not show the Skip to the page link")  # fmt: skip
    r.shot(r.a)
    r.a.keyboard.press("Enter")
    r.settle(r.a, 300)
    check(r.a.evaluate("!!document.activeElement.closest('main') || document.activeElement.id === 'main'"),
          "Enter on the skip link did not move into the page")  # fmt: skip


@case("TC-MAIL-27")
def mailbox_round_trip(r: Run) -> None:
    r.go(r.a, "/mailboxes")
    if "/sign-in" in r.a.url:
        r.sign_in(r.a, ADMIN, PASSWORD)
        r.go(r.a, "/mailboxes")
    check(r.a.locator("nav a[href='/mailboxes']").count() == 1, "an admin has no Mailboxes link")
    check("never sees your password" in r.main(r.a), "the page is not as written")
    r.shot(r.a)
    r.a.fill("#days", "2")
    r.a.click("form[action='/mailboxes/connect'] button.primary")
    r.a.wait_for_url(re.compile(r".*/mailboxes/continue$"))
    link = r.a.locator("a[href^='https://accounts.google.com/']")
    check(link.count() == 1 and link.get_attribute("hx-boost") == "false", "no plain link to Google")
    state = parse_qs(urlparse(link.get_attribute("href") or "").query)["state"][0]
    r.shot(r.a)
    # Google sends the browser back from another site. localhost is a different site from 127.0.0.1, so a Strict
    # cookie is not sent on that first hop, which is why the callback is public and hands over to /mailboxes/finish.
    port = urlparse(r.base).port
    r.a.goto(f"http://localhost:{port}/health")
    r.a.evaluate("u => { window.location.href = u }", f"{r.base}/mailboxes/callback?state={state}&code=PRETEND-CODE")
    r.a.wait_for_url(re.compile(r".*/mailboxes$"), timeout=15000)
    r.settle(r.a, 300)
    text = r.main(r.a)
    check("Connected pretend.mailbox@example.test" in text and "connected" in text, f"not connected: {text[:200]}")
    said = re.search(r"Mail from (\d\d/\d\d/\d{4}) on will be read", text)
    listed = r.a.locator("table tbody tr td").nth(4).inner_text().strip()
    shown = said.group(1) if said else None
    check(shown == listed, f"the message says {shown}, the table {listed}")  # DEF-010
    r.shot(r.a)
    r.a.click("form[action$='/disconnect'] button")
    r.a.wait_for_selector("text=Mail already stored stays")
    check("Connect Gmail" in r.main(r.a), "no way to connect again after disconnecting")
    r.shot(r.a)


def seed_failed_document(r: Run, subject: str) -> None:
    """A made-up failed document in the evidence database: the model is never run here."""
    with Session(r.engine) as db:
        box = db.query(Mailbox).first() or Mailbox(address="evidence@example.test", provider="folder", location="/none")
        db.add(box)
        db.flush()
        n = db.query(SourceMessage).count() + 1
        message = SourceMessage(
            mailbox_id=box.id, folder="Inbox", provider_id=f"evidence-{n}", sender="vendor@acme.example",
            recipients="purchasing@example.test", subject=subject, received_at=datetime.now(UTC),
            message_id_header=f"<evidence-{n}@acme.example>", raw_path="00/" + "0" * 64,
            content_hash=f"{n:064}", size=10,
        )  # fmt: skip
        db.add(message)
        db.flush()
        document = SourceDocument(
            message_id=message.id, filename="Message body", kind="body", stored_path="00/" + "0" * 64,
            content_hash=f"{n:064}", size=10, state="read", text_path="00/" + "0" * 64,
        )  # fmt: skip
        db.add(document)
        db.flush()
        db.add(Extraction(
            document_id=document.id, model="stub:1b", prompt_version=1, schema_version=1, state="failed", attempts=4,
            last_error="the model server gave no answer, even after 4 tries", finished_at=datetime.now(UTC),
        ))  # fmt: skip
        db.commit()


@case("TC-QUE-07")
def failed_documents(r: Run) -> None:
    seed_failed_document(r, "Acknowledgement for PO 4521")
    r.go(r.a, "/")
    if "/sign-in" in r.a.url:
        r.sign_in(r.a, ADMIN, PASSWORD)
        r.go(r.a, "/")
    tile = r.a.locator("a.tile[href='/documents/failed']")
    check(tile.count() == 1 and "1" in tile.inner_text(), "Home does not show one failed document")
    tile.click()
    r.a.wait_for_url(re.compile(r".*/documents/failed$"))
    listed = r.main(r.a)
    check("Acknowledgement for PO 4521" in listed and "even after 4 tries" in listed, "the failure is not listed")
    r.shot(r.a)
    r.a.click("form[action$='/retry'] button")
    r.a.wait_for_selector("text=No failed documents.")
    r.go(r.a, "/")
    shown = r.a.locator("a.tile[href='/documents/failed']").inner_text().split()[-1]
    check(shown == "0", f"the count is {shown}, not 0")
    seed_failed_document(r, "A newsletter")
    r.go(r.a, "/documents/failed")
    r.a.fill("input[name='reason']", "Not an order")
    r.a.click("form[action$='/dismiss'] button")
    r.a.wait_for_selector("text=No failed documents.")
    r.shot(r.a)


@case("TC-CO-08")
def customer_orders(r: Run) -> None:
    r.go(r.a, "/customers/new")
    if "/sign-in" in r.a.url:
        r.sign_in(r.a, ADMIN, PASSWORD)
        r.go(r.a, "/customers/new")
    check(r.a.locator("nav a[href='/customer-orders']").count() == 1, "no Customer orders link in the top bar")
    r.a.fill("#name", "Globex Racing")
    r.a.fill("#domains", "globex.example")
    r.a.fill("#qb_names", "Globex Racing Inc")
    r.a.click("form[action='/customers/create'] button.primary")
    r.a.wait_for_url(re.compile(r".*/customers/\d+$"))
    check("Globex Racing Inc" in r.main(r.a) and "globex.example" in r.main(r.a), "the customer page lacks its names")
    r.shot(r.a)
    # an order by hand, choosing the customer in the search box
    r.go(r.a, "/customer-orders/new")
    r.a.fill("#po_number", "CP-100")
    r.a.fill("#customer_id_text", "Glob")
    r.a.wait_for_selector("#customer_id_list li[role='option']")
    r.a.click("#customer_id_list li[role='option']")
    check(r.a.input_value("#customer_id") != "", "picking the customer did not set it")
    for row, (item, qty, price) in enumerate((("WIDGET-1", "10", "100"), ("WIDGET-2", "5", "20")), start=1):
        r.a.fill(f"input[aria-label='Item, line {row}']", item)
        r.a.fill(f"input[aria-label='Quantity, line {row}']", qty)
        r.a.fill(f"input[aria-label='Unit price, line {row}']", price)
    r.shot(r.a)
    r.a.click("form[action='/customer-orders/create'] button.primary")
    r.a.wait_for_url(re.compile(r".*/customer-orders/\d+$"))
    check("Revision 0: active" in r.main(r.a) and "1100.00 USD" in r.main(r.a), "the order page is wrong")
    r.shot(r.a)
    # the same number at another price is a revision that waits for a person
    r.go(r.a, "/customer-orders/new")
    r.a.fill("#po_number", "cp 100")
    r.a.fill("#customer_id_text", "Glob")
    r.a.wait_for_selector("#customer_id_list li[role='option']")
    r.a.click("#customer_id_list li[role='option']")
    r.a.fill("input[aria-label='Item, line 1']", "WIDGET-1")
    r.a.fill("input[aria-label='Quantity, line 1']", "10")
    r.a.fill("input[aria-label='Unit price, line 1']", "110")
    r.a.fill("input[aria-label='Item, line 2']", "WIDGET-2")
    r.a.fill("input[aria-label='Quantity, line 2']", "5")
    r.a.fill("input[aria-label='Unit price, line 2']", "20")
    r.a.click("form[action='/customer-orders/create'] button.primary")
    r.a.wait_for_selector("text=Revision 1 is waiting for approval")
    check("Line 1 unit price: 100 to 110" in r.main(r.a), "the page does not show what changed")
    r.shot(r.a)
    r.a.click("form[action$='/approve'] button")
    r.a.wait_for_selector("text=Revision 1: active")
    r.shot(r.a)
    # a file: one known customer, one unknown (it waits)
    known = r.work / "customer-orders-known.csv"
    known.write_text(
        "customer,po_number,currency,line_no,item,quantity,unit_price\nGlobex Racing,CP-2001,USD,1,Gear,4,25\n"
        "Initech Corp,CP-3001,USD,1,Cog,2,10\n"
    )
    r.go(r.a, "/customer-orders/import")
    r.upload(r.a, known, "button:has-text('Show what it would do')")
    r.a.wait_for_selector("text=What the file would do")
    check("will be filed" in r.main(r.a) and "not found: Initech Corp" in r.main(r.a), "the preview is wrong")
    r.shot(r.a)
    r.a.click("button:has-text('File these')")
    r.a.wait_for_url(re.compile(r".*/customer-orders\?imported=1.*"))
    check("waiting for a customer" in r.main(r.a), "the list does not say an order is waiting")
    r.a.click("a:has-text('Waiting for a customer')")
    r.a.wait_for_url(re.compile(r".*/customer-orders/waiting$"))
    r.a.fill("input[name='new_customer']", "Initech Corp")
    r.shot(r.a)
    r.a.click("button:has-text('File it')")
    r.a.wait_for_url(re.compile(r".*/customer-orders/\d+.*"))
    check("CP-3001" in r.main(r.a) and "Initech Corp" in r.main(r.a), "the waiting order was not filed")
    r.go(r.a, "/customer-orders")
    listed = r.main(r.a)
    check("CP-2001" in listed and "CP-3001" in listed and "CP-100" in listed, "the list lacks an order")
    check("cp 100" not in listed, "a revision changed how the order's number is shown")  # DEF-015
    check(r.a.locator("nav").first.bounding_box()["height"] < 80, "the top bar wraps onto two rows")  # DEF-014
    r.shot(r.a)


@case("TC-WEB-06")
def readable_errors(r: Run) -> None:
    r.go(r.a, "/no-such-page")
    check("Page not found" in r.a.inner_text("h1") and "Go to Home" in r.main(r.a), "no readable Page not found page")
    check(r.a.locator("nav a[href='/orders']").count() == 1, "the page has no top bar")
    r.shot(r.a)


@case("TC-WEB-04")
def dark_mode(r: Run) -> None:
    r.a.emulate_media(color_scheme="dark")
    r.go(r.a, "/orders")
    colour = r.a.evaluate("getComputedStyle(document.body).backgroundColor")
    values = [int(v) for v in re.findall(r"\d+", colour)[:3]]
    check(len(values) == 3 and min(values) > 200, f"the background is {colour} in dark mode")
    r.shot(r.a)
    r.a.emulate_media(color_scheme="light")


# ---------------------------------------------------------------------------------------------------------------------


class PretendKeychain:
    """The Keychain, in memory, with the Google client already saved (the run never touches the real one)."""

    def __init__(self) -> None:
        self.items = {
            "gmail-client-id": "1-pretend.apps.googleusercontent.com",
            "gmail-client-secret": "pretend-secret-value",
        }

    def get(self, name: str) -> str | None:
        return self.items.get(name)

    def put(self, name: str, value: str) -> None:
        self.items[name] = value

    def forget(self, name: str) -> None:
        self.items.pop(name, None)


def pretend_google(request: httpx.Request) -> httpx.Response:
    """Just enough of Google for the Mailboxes round trip: the token endpoint and the Gmail profile."""
    if request.url.host == "oauth2.googleapis.com":
        return httpx.Response(200, json={"access_token": "pretend-access", "refresh_token": "pretend-refresh",
                                         "expires_in": 3600})  # fmt: skip
    if request.url.path.endswith("/profile"):
        return httpx.Response(200, json={"emailAddress": "pretend.mailbox@example.test", "historyId": "1"})
    return httpx.Response(404)


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def commit() -> str:
    head = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True, text=True).stdout
    dirty = subprocess.run(["git", "status", "--porcelain", "--untracked-files=no"], cwd=ROOT, capture_output=True,
                           text=True).stdout.strip()  # fmt: skip
    return head.strip() + (" with uncommitted changes" if dirty else "")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--headed", action="store_true", help="show the browser window")
    args = parser.parse_args()
    record = tomllib.loads((EVIDENCE / "test_record.toml").read_text(encoding="utf-8"))
    browser_cases = [c["id"] for c in record["case"] if c["kind"] == "browser" and not c.get("retired")]
    missing = [cid for cid in browser_cases if cid not in CASES]
    extra = [cid for cid in CASES if cid not in browser_cases]
    if missing or extra:
        print("The script and the test record disagree. Missing here:", missing, "Not browser cases:", extra)
        return 2

    SHOTS.mkdir(exist_ok=True)
    for old in SHOTS.glob("TC-*.png"):
        old.unlink()
    work = Path(tempfile.mkdtemp(prefix="po-dashboard-evidence-"))
    settings = Settings(data_dir=work / "data")
    engine = upgrade(settings)
    with Session(engine) as db:
        service.create_user(db, ADMIN, "admin", password=PASSWORD)
        db.commit()
    port = free_port()
    app = create_app(settings, engine)
    app.state.secrets = PretendKeychain()  # never the real Keychain, never the real Google
    app.state.google_http = httpx.Client(transport=httpx.MockTransport(pretend_google))
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    threading.Thread(target=server.run, daemon=True).start()
    while not server.started:
        time.sleep(0.05)

    results: dict[str, dict[str, object]] = {}
    started = datetime.now()
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(channel="chrome", headless=not args.headed)
            run = Run(browser, f"http://127.0.0.1:{port}", work)
            run.engine = engine
            for cid in browser_cases:
                run.current, run.shots = cid, []
                began = time.monotonic()
                try:
                    CASES[cid](run)
                    if not run.shots:
                        run.shot()
                    outcome, detail = "Pass", ""
                except Exception as problem:  # a failed check or a step that could not be done: record it, go on
                    outcome, detail = "Fail", str(problem).splitlines()[0][:300]
                    try:
                        run.last.screenshot(path=str(SHOTS / f"{cid}-fail.png"), caret="initial")
                        run.shots.append(f"screenshots/{cid}-fail.png")
                    except Exception as gone:  # the page itself may be gone
                        print(f"      no failure screenshot for {cid}: {gone}", flush=True)
                results[cid] = {"result": outcome, "detail": detail, "screenshots": run.shots,
                                "seconds": round(time.monotonic() - began, 1)}  # fmt: skip
                print(f"{outcome}  {cid}" + (f"  [{detail}]" if detail else ""), flush=True)
            version = browser.version
            browser.close()
    finally:
        server.should_exit = True
        time.sleep(0.3)
        engine.dispose()
        shutil.rmtree(work, ignore_errors=True)

    passed = sum(1 for r in results.values() if r["result"] == "Pass")
    out = {
        "date": started.strftime("%m/%d/%Y"),
        "started": started.isoformat(timespec="seconds"),
        "browser": f"Google Chrome {version}",
        "by": "Claude, scripts/browser_evidence.py",
        "commit": commit(),
        "passed": passed,
        "total": len(results),
        "problems": run.problems,
        "cases": results,
    }
    (EVIDENCE / "chrome_run.json").write_text(json.dumps(out, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"\n{passed} of {len(results)} passed; script errors in the pages: {run.problems or 'none'}")
    return 0 if passed == len(results) and not run.problems else 1


if __name__ == "__main__":
    sys.exit(main())
