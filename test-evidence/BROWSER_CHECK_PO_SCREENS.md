# Browser check: vendors, purchase orders, CSV import (PD-1.2)

A person follows this in a real browser. It uses a throwaway data folder and **made-up** vendors and PO numbers only.
**Do not write real names, prices or documents in this file**; the repository is public. Time: about 15 minutes, once in
Safari and once in Chrome.

## Set up (Terminal, in the project folder)

```sh
cd ~/Projects/po-dashboard
export PO_DASHBOARD_DATA="$HOME/Projects/po-dashboard-check-data2"
uv run po-dashboard user create-admin boss@example.com     # choose a password, twice; nothing shows as you type
uv run po-dashboard serve                                  # leave this window open
```

Open http://127.0.0.1:8000 and sign in. If you restart the server, run the `cd` and `export` lines again first.

## Steps

| # | Do this | Expected | Safari | Chrome |
|---|---|---|---|---|
| 1 | Click **Vendors**. | An empty list, with the "Add a vendor" form and **the cursor already blinking in Name**. | | |
| 2 | Add `Acme Fasteners` with domains `acme.com, mail.acme.com`. | You land on the vendor's page showing both domains. | | |
| 3 | Add `acme fasteners` (other case) as a vendor again. | "A vendor with that name already exists", and the boxes keep what you typed. | | |
| 4 | On the vendor page, add the domain `not a domain`. | Refused with a clear reason. Then add `acme.co.uk`: it appears. Click it to remove it. | | |
| 4b | On the vendor page, add `ACME.com` again (other case). Then change a detail and press **Save**. | "acme.com is already added to this vendor". After Save the page reloads with a green **Saved** note that fades after about 3 seconds. | | |
| 5 | Add a second vendor, `Northwind Metals`, with no domains. | It appears in the list. | | |
| 6 | Click **Purchase orders**, then **New purchase order**. | A form with PO number, vendor, currency, date, buyer and 10 line rows. **The cursor is already in PO number.** Buyer is your email. | | |
| 7 | Fill PO number `PO-1001`, vendor Acme, currency `usd`, and line 1: item `Bolt`, quantity `0`, unit `EA`, price `2.5`. Create. | Refused: "Line 1: Quantity must be more than zero", and **every box keeps what you typed**. | | |
| 8 | Fix the quantity to `10.5` and press Create. | You land on the PO page: PO 1001, vendor, currency **USD**, a line showing `10.5 EA`, `2.50 USD`, status **Sent**. | | |
| 9 | Press the browser's **Reload** on that page, a few times. | Nothing is created twice; the page just reloads. Then check **Purchase orders**: one PO-1001. | | |
| 10 | Try a quantity of `1.00001` on a new PO. | Refused: "at most 4 decimal places are kept; nothing is rounded". | | |
| 11 | Open **Import CSV**. Click **Download a template**. | A small `.csv` file downloads. **The cursor is on the file chooser.** | | |
| 12 | Choose `tests/data/purchase_orders_sample.csv` from the project folder and click **Import**. | Two POs (PO-2001, PO-2002) land in the list with "Imported 2 purchase orders". (This needs both vendors from steps 2 and 5, spelled the same.) | | |
| 13 | Import the **same file again**. | The page stays put and lists the problems in place: "PO number PO-2001 already exists. Nothing is overwritten." No page reload, nothing added. | | |
| 14 | Open the sample file in a text editor, change a quantity to `abc`, save a copy, and import the copy. | The problem is listed with its row number, e.g. "Row 3: Quantity: 'abc' is not a number", and nothing is imported. | | |
| 15 | Search the PO list for `2001`, then for `%`. | `2001` finds PO-2001. `%` finds nothing ("No purchase orders match"). | | |
| 16 | Add a vendor named `<b>Bold</b>`. Look at the vendor list and at the vendor page. | The name is shown literally as `<b>Bold</b>`, not in bold. | | |
| 17 | Sign in as a **buyer** (add one on **People**), and open **Vendors** and **Purchase orders**. | A buyer can use all of these pages, not only an admin. | | |
| 18 | Open PO-1001 (from step 8). Click **Record acknowledgement**. | A form listing each line, with **the cursor already in the first Confirmed quantity box**. | | |
| 19 | Enter confirmed quantity `6` (the PO ordered `10.5`), price `2.75`, promised date in the future. Record. | Back on the PO page: the line shows **Acknowledged**, the promised date, a note "The vendor's price 2.75 differs from the PO price 2.5", and "confirmed 6, not 10.5. Update the original purchase order". | | |
| 20 | Record a **second** acknowledgement with a promised date **later** than the first. | Both dates are shown, joined by an arrow, with a red **slipped** label on the later one. | | |
| 21 | Click **Record shipment**: carrier `UPS`, tracking `1Z999`, quantity `3`. Record. | Status becomes **Partially shipped**. The history lists the shipment with who entered it. | | |
| 22 | Record another shipment of `3`. | Status becomes **Shipped**. | | |
| 23 | Click **Record invoice**, number `INV-1`, quantity `6`. Notice the price box starts at the PO price. Record. | The line still shows **Shipped** with open quantity `4.5`, because it was confirmed short. The update-the-PO note stays until someone settles it. | | |
| 24 | Record an invoice `INV-2` for quantity `20`, price `3`. | Recorded, with notes "Invoiced ... more than the 10.5 ordered" and "above the PO price". The line becomes **Closed**. | | |
| 25 | In **History**, remove `INV-2`: first press Remove with the reason empty, then with a reason. | Without a reason the browser asks for one. With one, the entry goes, the notes clear and the line returns to **Shipped**. | | |
| 26 | Press **Reload** after recording or removing anything. | Nothing is recorded or removed a second time. | | |
| 27 | Make a mistake on purpose in a form (a negative quantity, a date typed as `soon`). | The message names the line and the field, and every box keeps what you typed. | | |
| 28 | Open **Purchase orders**. | A filter box above the list: Search, Status, Vendor, Sent from/to, Expected from/to, Late only. **The cursor is in Search.** | | |
| 29 | Click an **Expected from** box. | The browser shows a **calendar** to pick from. Pick a date range and press **Show**: only matching POs stay. **Clear** resets it. | | |
| 30 | On a PO, record an acknowledgement promising a date **in the past**. | The PO page shows the line as **N days late** in red; the list shows "1 late, up to N days". Tick **Late only**: the PO is found. | | |
| 31 | Record a shipment with carrier `UPS`, a tracking number like `1Z999AA10123456784` and an arrival date in the future. | The line's Expected date becomes "from the shipment" and the late flag goes. The History entry has **Track with the carrier**; it opens UPS's own page in a new tab. | | |
| 32 | On an open PO, open **Settle lines by hand**. | A collapsed box (it says it is the last resort). Opening it lists the open lines, each with "Leave as it is / Received / Closed short / Cancelled", and one Reason box. | | |
| 33 | Choose **Cancelled** for one line, leave the reason empty, press Settle. | The browser asks for a reason. With a reason, the line shows **Cancelled** and a note "Settled by hand". The line is not offered again. | | |
| 34 | On that line press **Reopen** with a reason. | The line returns to its earlier status. The page does not repeat the action on Reload. | | |
| 35 | Open **Timeline by line** and a line's heading. | A list in time order: Ordered, Vendor acknowledged, Shipped, Invoiced, Settled by hand. | | |
| 36 | Click **Home**. | Six tiles: open purchase orders, open lines, late lines, waiting for an acknowledgement, promised date slipped, original PO needs updating. The numbers match what you have entered. | | |
| 37 | Click the **Late lines** tile (make one late first: promise a date in the past). | You land on **Open items** showing only the late lines, each with a red "N days late". | | |
| 38 | On **Open items**, change **Show** and **Vendor**, press Show. **The cursor starts in Show.** | The list narrows; an empty view says "Nothing to show". | | |
| 39 | Press **Download CSV**. | A file `open-items-<date>.csv` downloads and opens in Excel or Numbers with the columns and accents correct. | | |
| 40 | Add a PO line whose item is `=1+1` and download again. | The cell shows `'=1+1` as plain text, never the number 2. | | |

## Things to look at while you do it

- Make the window **narrow** (phone width). The PO entry table and the PO page scroll sideways inside their box; the page itself must not.
- Tab through the **New purchase order** form: every box reachable, in a sensible order.
- Anything that looks wrong or clumsy: note it for the design pass (PD-1.8).

## Clean up

Press Ctrl-C in the server window, then:

```sh
rm -r "$HOME/Projects/po-dashboard-check-data2"
unset PO_DASHBOARD_DATA
```

## Result

Date: 2026-11-05  Who: owner  Safari: pass  Chrome: pass  Notes (no real names or figures): all steps done by the owner; two small fixes came out of it (domain already added, Saved note), committed in 0311dde.
