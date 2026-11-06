# PO Dashboard: Operations runbook

2026-11-04 · **Draft. Only the install and start commands in §2 exist and have been run so far.** Commands marked
*(PD-n.n)* are filled in and tested when that task is done (PLAN.md). Until then this page says what the runbook must
cover.

## 1. Where things live

| What | Where |
|---|---|
| Code | the cloned repository on the office Mac, anywhere under `~/Projects` (never Desktop, Documents or iCloud) |
| Database, stored originals, logs | the data folder, default `~/Library/Application Support/PO Dashboard/` (PD-6.5) |
| Backups | a folder the user chooses, outside iCloud; 35 days kept |
| Mailbox tokens or secrets | the office Mac's Keychain, one entry per mailbox, "PO Dashboard mailbox ..." |
| The key that seals TOTP secrets | the macOS Keychain, entry "PO Dashboard sealing key" (PD-1.9b) |
| Settings | the Settings screen (stored in the database); `.env` only for development, git-ignored |

## 2. Install and first run (PD-0.3, PD-7.1)

**Works today:**

1. Clone the repository under `~/Projects`. You need `uv` and Ollama (`brew install uv ollama`), and Tesseract for
   scanned PDFs (`brew install tesseract`).
2. `scripts/setup.sh`. It installs the dependencies and the pre-commit guard, states the model size (9 GB) and asks
   before downloading.
3. `uv run po-dashboard serve`, then open http://127.0.0.1:8000.

To check a change: `uv run ruff check . && uv run mypy && uv run pytest`.

**Still to come:** `uv run po-dashboard user create-admin` before first sign-in (PD-1.9b); `scripts/demo.sh` to load the
synthetic sample mailbox (PD-7.1); Mailboxes → Connect, signing in on the provider's own page (PD-3.7); install the
service, §3; let laptops in, §11.

## 3. Run as a service (PD-6.4)

A launchd user agent starts the app at login and restarts it if it stops. Commands for install, status, stop and
uninstall are `scripts/service.sh install|status|stop|uninstall`. Logs: the data folder's `logs/`. Logs hold ids and
counts, never document text.

**Keep the office Mac awake and logged in.** Set it not to sleep (System Settings → Energy) and leave its account logged
in; locking the screen is fine. If it sleeps or restarts, the first scan afterwards reads everything since the last
success, so no mail is missed. **After a power cut or restart, someone must log in at that Mac** (PD-6.4). Until then
laptops get a connection error, and nothing in the app can warn them, because the app is not running.

## 4. What to check each day

The dashboard health strip: **last scan** (should be within 10 minutes while the Mac is awake), **model reachable**,
**documents waiting for review**, **last backup**. A red banner or a `scan_stale` alert means a scan has not succeeded
for 30 minutes.

## 5. When something goes wrong

| Symptom | Likely cause | Do this |
|---|---|---|
| A mailbox shows "needs reconnect" | its access lapsed or was revoked, for example after a password change | Mailboxes → Reconnect, by someone with access to that mailbox. Permission changes can take up to 2 hours to apply |
| A laptop cannot reach the app | the office Mac is off, asleep or logged out; or that laptop is off the office network | Check the office Mac first. From outside the office, use the company's VPN |
| A browser warns about the certificate | this laptop has not trusted it yet, or it has expired | §11. Do not click through the warning: that is how a fake page would look |
| Banner: "can't read mailbox" after a change | the app's permission was changed on the mail system's side | Ask the company's admin to check that the app's access covers that mailbox |
| Banner: "model not reachable" | Ollama not running, or the model missing | Start Ollama; `ollama list` shows `qwen2.5:14b-instruct`; pull it if missing |
| Documents stuck "processing" | the app stopped mid-document | Restart the service; the document is retried; nothing is lost because the position moves only after storage |
| Every document is flagged or Unmatched | wrong vendor domains, or POs not entered | Check the vendor's domains; enter the missing POs |
| Slow extraction | machine short of memory | Close other apps; or choose a smaller model in Settings (lower accuracy) |
| Wrong data was accepted | a person accepted a misread document | Edit the facts on the PO (recorded in the audit trail); report the document so it joins the golden set |
| The Mac was off for days | | Start it; the catch-up scan reads everything since the last success |

## 6. Backup and restore (PD-6.5)

Nightly copy of the database and stored files to the chosen folder, 35 days kept. Restore: stop the service, run
`scripts/restore.sh <backup>`, start the service. The restore is tested in PD-6.5 on a copy, never on the live data.

## 7. Update (PD-6.5)

`git pull`, then restart the service. On start the app backs up the database, then applies migrations. If a migration
fails the app does not start and says why; restore the backup and report it. After an update, reload each open tab once
with Cmd-Shift-R (Safari: Cmd-Option-R) so the browser picks up the new stylesheet and script.

## 8. Moving to another machine

Install there (§2), then copy the data folder only if the old machine is retired: stop both services first, so two
copies never scan the same mailbox with different saved positions.

## 9. Leaving the mailbox

To stop the app reading a mailbox: Mailboxes → Disconnect, which deletes its token from the Keychain. To stop it
completely, stop the service as well and ask the company's admin to remove the app's access. Local data stays until the
owner deletes the data folder.

## 10. Sign-in and lost access (PD-1.9)

Designed in ARCHITECTURE §10.1. **Working now:** `create-admin`, `create`, `reset-password`, `unlock`, `list`. The rest (the second factor, the Users screen) is not built. Run these at the office Mac, as the account that runs the app. Each is recorded
in the audit trail.

| Problem | Do this |
|---|---|
| New install, no admin yet | `uv run po-dashboard user create-admin` |
| Forgot a password | `uv run po-dashboard user reset-password <email>` prints a temporary password (the Users screen comes in PD-1.9c) |
| Add a person | `uv run po-dashboard user create <email> --role buyer` prints a temporary password, shown once |
| *(when the second factor returns)* Lost the phone | Sign in with a recovery code, then re-enrol. With no codes left: an admin resets it, or `uv run po-dashboard user reset-2fa` |
| "Too many wrong attempts" | Wait 15 minutes, or `uv run po-dashboard user unlock` |
| *(when the second factor returns)* "Sign-in is unavailable: cannot read the key" | Unlock the login Keychain at the Mac and answer any Keychain prompt. The scanner keeps running meanwhile |
| *(when the second factor returns)* Database restored on another Mac | Passwords still work. Run `reset-2fa` for each person, because the sealing key stayed in the old Mac's Keychain |

## 11. Letting a laptop in (PD-6.3a)

Designed in ARCHITECTURE §11; not built yet. Each laptop does this once. Nothing is installed apart from trusting the
certificate, and the sign-in itself does not change.

1. Get the office Mac's certificate file, and trust it in that laptop's system (the steps differ for Mac and Windows, OPEN-14).
2. Open the app's address, `https://<office Mac's name>.local:<port>`, and bookmark it.
3. Sign in. An admin hands you a temporary password the first time.

The steps and the real address go here once they have been tested on the real office network.

## 12. Importing purchase orders from a spreadsheet (PD-1.2)

**Purchase orders**, then **Import CSV or Excel**, then **Download a template** shows the columns. An Excel workbook
(.xlsx) works as well as CSV: the first sheet is read, and only the values saved in it (DEC-47). One row per PO line; rows sharing a
PO number make one PO. Required: `po_number, vendor, currency, line_no, item, quantity, unit, unit_price`. Optional:
`date_sent, buyer, description, requested_date`. Dates are `12/31/2026` or `2026-12-31`; numbers use digits and a dot (no thousands
separator); the file can be **UTF-8 or Windows-1252** (what QuickBooks and older Excel write); columns are found by name in any order, and columns the import does not use are skipped and counted. The vendor must already be on the **Vendors** page, spelled the same
way. The file goes in whole or not at all, and a PO number that exists is never overwritten. If the page lists problems,
fix those rows and import again: nothing from the failed attempt was kept.

## 13. Importing vendors (PD-1.11, DEC-44)

**Vendors → Import vendors** takes a CSV with the columns `vendor_name` and `contact_email` (both required), `contact_name`, `email_domains`, `notes`; **Download a template** gives an example. Upload it as often as you like: a new vendor is created, an existing one gets any new email domain added, and nothing already entered is overwritten. A row with no email, or with an email already used by another vendor, is skipped. A row with a problem is listed and skipped; the rest go in. A domain that already belongs to another vendor is never moved.

## 14. Using the screens (DEC-45)

- **Home** lists what needs doing, most urgent first, each with its next step; "All clear" means nothing needs you. Home
  and Open items update themselves every minute; that does not keep you signed in, so after 30 idle minutes the tab goes
  to the sign-in page.
- **Lists:** click a column heading to sort (again to reverse); 50 rows a page. Filters apply as you choose them. The
  address keeps the view, so a bookmark or Back returns to it.
- **Keys:** `/` puts the cursor in the page's search box. In a vendor box, type, then the arrow keys and Enter; Esc
  closes the list. Esc also closes the account menu (your email, top right: Change password, Sign out).
- **Dates** are shown as QuickBooks shows them, 11/06/2026, and times in the office's time zone.

## 15. Going live: loading the open POs from QuickBooks (PD-1.10a, DEC-46)

Once, at go-live; after that, POs come from the emails.

1. **Vendors first.** Import the vendor file (§13) so every vendor on the open POs is in the app, with its email.
2. **Export the report.** In QuickBooks run **Open Purchase Orders** (the detail report), all vendors and all dates, and
   export it as CSV or Excel. Keep QuickBooks' name, such as `Open POs 110526 Project.CSV`: the six digits are the export
   date (MMDDYY). Do not convert it.
3. **Preview.** **Purchase orders → Load from QuickBooks**, choose the file, **Show what it would do**. Nothing changes
   yet. The page lists the POs to create, any **waiting for a vendor** (add those vendors, then load the same file again),
   any already in the app (left alone), partly received lines and any price it had to round.
4. **Create.** Check the export date and press **Create N purchase orders**. All are created, or none.

What it does: currency USD, unit EA; the QuickBooks date is kept as the **PO date (QuickBooks)** and the date sent is left
empty, so the app does not flag a missing acknowledgement it never had the chance to see; a customer named on a line is
shown as "· for <customer>"; a line partly received gets "received N per QuickBooks export of <date>". What it never
does: create vendors, change a PO already in the app, read `Other 1`, `Print` or the delivery dates (those come from
emails), or close anything. Closing from the weekly export waits for OPEN-20.

