# Browser check: the web standards on every screen (PD-1.8, DEC-45)

A person follows this in a real browser, once in Safari and once in Chrome. It uses a throwaway data folder and
**made-up** vendors and PO numbers only. **Do not write real names, prices or documents in this file**; the repository
is public. Time: about 25 minutes a browser (step 17 needs 31 minutes more, and is optional).

Claude has already driven Chrome through 51 automated checks of the same behaviour (all pass). This check is the
owner's own look, in both browsers, and is where the list of small tweaks for PD-1.8f comes from.

## Set up (Terminal, in the project folder)

```sh
cd ~/Projects/po-dashboard
export PO_DASHBOARD_DATA="$HOME/Projects/po-dashboard-check-data3"
uv run po-dashboard user create-admin boss@example.com     # choose a password, twice; nothing shows as you type
uv run po-dashboard serve                                  # leave this window open
```

Open http://127.0.0.1:8000 and sign in. If a page looks unstyled or old, reload it once with Cmd-Shift-R
(Safari: Cmd-Option-R).

## Steps

| # | Do this | Expected | Safari | Chrome |
|---|---|---|---|---|
| 1 | Sign in. | Home: health tiles, the six counts, and **Nothing open** (no POs yet). The top bar is one row; your account is the person icon at the right. | | |
| 2 | Click the person icon at the top right. Press Esc. Open it again and click anywhere else. | A small menu with **Change password** and **Sign out**. Esc closes it; so does a click elsewhere. | | |
| 3 | Click **Vendors**, then **Add a vendor**. | A page of its own, **the cursor in Name**. The Vendors page itself starts with a search box. | | |
| 4 | Add `Acme Fasteners` (domain `acme.com`) and `Northwind Metals` (domain `northwind.example`). Go back to **Vendors**. | Both are listed. Clicking between pages never flashes white: only the page body changes. | | |
| 5 | Type `north` in the Vendors search box. Do not press anything. | After a moment only Northwind is shown, and the address bar shows `?q=north`. Press the browser's **Back**: the full list again. | | |
| 6 | Click somewhere blank on the page, then press the `/` key. | The cursor jumps into the search box. | | |
| 7 | Click the **Vendor** column heading, then again. | A ▲ then ▼ next to it, and the order reverses. The rest of the page stays put. | | |
| 8 | Open Acme. Scroll down, change the Notes, press **Save**. | A green **Saved** note that fades after about 3 seconds. **The page does not jump to the top.** | | |
| 9 | On Acme, add the domain `northwind.example`. | A red message saying it already belongs to Northwind, scrolled into view. Then press **Reload**: nothing is sent again. | | |
| 10 | **Purchase orders → Import CSV**, choose `tests/data/purchase_orders_sample.csv`, **Import**. Then import the same file again. | First: "Imported 2 purchase orders" on the list. Second: the problems are listed on the import page, and the address stays `/orders/import`. | | |
| 11 | On **Purchase orders**, type `2001` in Search; then choose a Status; then a date in **Sent from** (a calendar opens). | The list follows each choice on its own; there is no Show button. Dates in the list read like **11/04/2026**. **Clear** resets everything. | | |
| 12 | In the **Vendor** filter box type `acm`, press the down arrow and Enter. | A short list appears under the box; Enter picks Acme and the list shows only Acme's POs. Empty the box: every vendor again. | | |
| 13 | **New purchase order**: type `PO-3001`, then type `Acme` in Vendor **without picking it** from the list, fill line 1 and press Create. | The Vendor box says **"Pick a vendor from the list."** Pick it; Create works and you land on the PO page. **Reload** there: still one PO-3001. | | |
| 14 | Press Create on a new PO with quantity `0` (a mistake on purpose). | The problem appears at the top and the cursor goes to it; every box keeps what you typed. Press the browser's **Back** and **Forward**: no "page not found". | | |
| 15 | On a PO page, scroll to **Settle lines by hand**, cancel one line with a reason, press Settle. Watch the button. | The button dims with a small turning mark while it works, then the line shows **Cancelled**. **The page stays where it was.** Times in History and the timeline read like **11/06/2026 2:35 PM**, with no "UTC". | | |
| 16 | Record an acknowledgement on a PO with a promised date **in the past**. Then go **Home** and leave it open. In a second tab, record a past promised date on another PO. | Home shows the first one under **Needs attention** with "Ask the vendor for a new date". Within about a minute, without touching it, Home's counts and list include the second one too. | | |
| 17 | *(Optional, 31 minutes.)* Leave Home open and touch nothing for 31 minutes. | The tab moves to the sign-in page on its own: the page updating itself does not keep you signed in. | | |
| 18 | **Open items**: change **Show**, then press **Download CSV**. | The list follows the choice; the CSV downloads as a file (it is not shown in the page), with dates like 11/06/2026. | | |
| 19 | **Audit** (admin): type `vendor` in Action. | The box suggests the actions; the list narrows as you type. Click **When** to see the oldest first. | | |
| 20 | Make the window **narrow** (phone width), or open the app on a phone on the office network. Visit Home, Purchase orders, a PO, Vendors. | No sideways scrolling of the page. The top bar scrolls sideways; tables scroll inside their box. Buttons are easy to tap. | | |
| 21 | Reload any page, then press **Tab** once. | A **Skip to the page** link appears at the top left; Enter moves past the top bar. Every control shows a blue outline when the Tab key reaches it. | | |
| 22 | Person icon → **Sign out**, then sign in again. | The sign-in page, then Home. No "security token" error. | | |

## Your list of small tweaks

Write one line per item, saying which screen: for example "PO page: Settle by hand is too prominent". No real names
or figures.

- (Idea, parked) An "Add and start another" button next to Add vendor; not needed now, since vendors come from the file
  upload. Recorded in PLAN.md.

## Clean up

Press Ctrl-C in the server window, then:

```sh
rm -r "$HOME/Projects/po-dashboard-check-data3"
unset PO_DASHBOARD_DATA
```

## Result

Date: 2026-11-06  Who: owner  Result: "we are good with all tests" (browsers not named)  Notes (no real names or figures): one parked idea, above.
