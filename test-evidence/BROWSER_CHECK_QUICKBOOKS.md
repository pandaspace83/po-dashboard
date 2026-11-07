# Browser check: Load from QuickBooks and Excel uploads (PD-1.10a, DEC-46, DEC-47)

A person follows this in a real browser, once in Safari and once in Chrome. It uses a throwaway data folder and the
**made-up** report in `tests/data/`. **Do not write real names, prices or documents in this file**; the repository is
public. Time: about 15 minutes a browser.

Claude has already driven Chrome through the same steps with the made-up report (14 checks, all pass) and read the
owner's real report in a dry run (0 problems; counts only).

## Set up (Terminal, in the project folder)

```sh
cd ~/Projects/po-dashboard
export PO_DASHBOARD_DATA="$HOME/Projects/po-dashboard-check-data4"
uv run po-dashboard user create-admin boss@example.com     # choose a password, twice; nothing shows as you type
uv run po-dashboard serve                                  # leave this window open
```

Open http://127.0.0.1:8000 and sign in. If a page looks old, reload it once with Cmd-Shift-R (Safari: Cmd-Option-R).

## Steps

| # | Do this | Expected | Safari | Chrome |
|---|---|---|---|---|
| 1 | In Excel or Numbers make a sheet with the columns `vendor_name`, `contact_email` and two rows: `Acme Fasteners`, `sales@acme.example` and `Northwind Metals`, `orders@northwind.example`. Save it as **.xlsx**. On **Vendors → Import vendors** upload it. | "2 created". Both vendors are on the Vendors page with their domains. | | |
| 2 | Save the same sheet as **.xls** (old format) if your program offers it, and upload it. | Refused: "This is the old Excel format (.xls) ... Save it as .xlsx or CSV." Nothing changes. | | |
| 3 | **Purchase orders → Load from QuickBooks**. | A page explaining which QuickBooks report to export; the cursor is on the file chooser. | | |
| 4 | Choose `tests/data/quickbooks_open_pos_110526.csv` and press **Show what it would do**. | "Nothing has changed yet". **3** purchase orders with 6 lines to create; **1** waiting for a vendor (Globex Wire); 1 line partly received; 1 price rounded (10.00 ÷ 3 = 3.3333). The export date reads 11/05/2026, taken from the name. The cursor is on **Create 3 purchase orders**. | | |
| 5 | Check **Purchase orders** in another tab. | No new POs yet: the preview changed nothing. | | |
| 6 | Back on the preview, press **Create 3 purchase orders**. | The PO list: "Loaded 3 purchase orders from QuickBooks. 1 is waiting for its vendor ...". POs 5001, 5002 and 5004 are listed. | | |
| 7 | Press the browser's **Back**, then the Create button again (if it shows). | "That preview has expired or was already used." Still three POs, never six. | | |
| 8 | Open PO **5002**. | Tiles show **Date sent: not set** and **PO date (QuickBooks): 10/22/2026**. The line reads "Sheet:Alu 2mm · for Café Racing" (the accent is right), status **Partially shipped**; History has "Received per QuickBooks export of 11/05/2026". | | |
| 9 | Open PO **5001** and its **Timeline by line**, line 2. | "Unit price 3.3333 worked out from the QuickBooks total 10.00 ÷ 3, rounded (export of 11/05/2026)". Lines are Bolts, Nuts, Washers, in the order the file lists them; unit EA. | | |
| 10 | Go **Home**. | The loaded POs are **not** listed as waiting for an acknowledgement. | | |
| 11 | Load the same file again. | "Nothing new to create"; 3 already in the app, left as they are; Globex still waiting. No Create button. | | |
| 12 | Add the vendor `Globex Wire` (with any email), then load the file again. | 1 purchase order to create (5003). Create it: four POs now. | | |
| 13 | Upload a file that is not the report (for example the vendor .xlsx from step 1). | "This does not look like the QuickBooks Open POs report: it has no 'date', 'num', ... column." | | |
| 14 | *(Optional, your own data, stays on this Mac.)* Upload your real export from `real-docs/`. | The preview counts match what QuickBooks shows (38 POs, 114 lines in the 11/05 export; most wait until their vendors are imported). Press Cancel unless you want them in this throwaway folder. Do not write any names or figures here. | | |
| 15 | **Purchase orders → Import CSV or Excel** (the other button), and upload `tests/data/quickbooks_open_pos_110526.csv`. | No column list. The preview from step 4, with "This file is the QuickBooks Open POs report, so it is read here …". Press Cancel. *(Added 2026-11-07: the owner's step 4 sent the file to this button.)* | | |
| 16 | On **Load from QuickBooks**, upload the app's own template (**Import CSV or Excel → Download a template**). | "This is a purchase order file in the app's own import format …", with a **Go to Import CSV or Excel** button that opens that page. | | |

## Clean up

Press Ctrl-C in the server window, then:

```sh
rm -r "$HOME/Projects/po-dashboard-check-data4"
unset PO_DASHBOARD_DATA
```

## Result

Date:  Who:  Safari:  Chrome:  Notes (no real names or figures):
