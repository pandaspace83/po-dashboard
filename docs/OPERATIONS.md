# PO Dashboard: Operations runbook

2026-11-04 · **Draft. Only the install and start commands in §2 exist and have been run so far.** Commands marked
*(PD-n.n)* are filled in and tested when that task is done (PLAN.md). Until then this page says what the runbook must
cover.

## 1. Where things live

| What | Where |
|---|---|
| Code | the cloned repository on the office Mac, anywhere under `~/Projects` (never Desktop, Documents or iCloud) |
| Database, stored originals, logs | the data folder, default `~/Library/Application Support/PO Dashboard/`; the log is `logs/app.log` (600, 1 MB, five kept): routes, status and timings with a request id, never a message, a query or a password. A user can quote the id shown in the `X-Request-ID` header |
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
synthetic sample mailbox (PD-7.1); install the service, §3; let laptops in, §11.

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
| A mailbox shows "needs reconnect" | its access lapsed or was revoked, for example after a password change | Mailboxes → Reconnect Gmail (on the office Mac, §16), by someone with access to that mailbox. Permission changes can take up to 2 hours to apply |
| A laptop cannot reach the app | the office Mac is off, asleep or logged out; or that laptop is off the office network | Check the office Mac first. From outside the office, use the company's VPN |
| A browser warns about the certificate | this laptop has not trusted it yet, or it has expired | §11. Do not click through the warning: that is how a fake page would look |
| Banner: "can't read mailbox" after a change | the app's permission was changed on the mail system's side | Ask the company's admin to check that the app's access covers that mailbox |
| Banner: "model not reachable" | Ollama not running, or the model missing | Start Ollama; `ollama list` shows `qwen2.5:14b-instruct`; pull it if missing |
| Documents stuck "processing" | the app stopped mid-document | Restart the service; the document is retried; nothing is lost because the position moves only after storage |
| Home says "Waiting to be read" and the number does not go down | the model is not reachable (Home says so beside it), or the model is slow: the first read after Ollama loads a 14b model can take minutes | Start Ollama and check `ollama list`. Nothing is lost and nothing is marked failed while the model is down; the documents are read when it is back |
| Home shows "Failed documents" above 0 | the model gave no usable answer for those documents, after 4 tries or at once | Open Failed documents (the tile). Retry puts a document back in the queue; Dismiss files it with a reason. If many fail together, check the model first |
| Every document is flagged or Unmatched | wrong vendor domains, or POs not entered | Check the vendor's domains; enter the missing POs |
| Slow extraction | machine short of memory | Close other apps; or choose a smaller model in Settings (lower accuracy) |
| Wrong data was accepted | a person accepted a misread document | Edit the facts on the PO (recorded in the audit trail); report the document so it joins the golden set |
| The Mac was off for days | | Start it; the catch-up scan reads everything since the last success |
| A document says "not read" or "failed" with a reason | it is over a limit, damaged, or hostile; it is kept as it came (DEC-51, DEC-53) | Open the original from the message. If a real vendor's file is refused, report it (name the file, not its contents) so the limit can be looked at |
| The log says "The macOS sandbox (sandbox-exec) is missing" | a macOS update removed it (RISK-14) | Documents are still read in a separate process with limits. Tell the developer: the sandbox needs replacing |

## 6. Backup and restore (PD-6.5)

**Built 2026-11-11 (the nightly schedule and the 35-day pruning are still PD-6.5):**

- **Back up now:** `uv run po-dashboard backup --to ~/Backups` (a folder on this Mac, outside iCloud, Desktop and
  Documents; the command refuses those, and refuses a folder inside the data folder). It makes a new folder with the
  database (a consistent copy made with SQLite's own backup call: it is safe while the app runs, and a plain file copy of
  a database in use is not), every stored original, and a manifest of hashes. It checks its own copy before it says done.
  It never overwrites a backup and never deletes one.
- **Restore:** stop the app, then `uv run po-dashboard restore ~/Backups/<folder> --into ~/NewDataFolder`. It verifies the
  backup first (hashes and integrity), and goes only into a folder that has no database, so it cannot damage the data you
  have. Start the app against it with `PO_DASHBOARD_DATA=~/NewDataFolder`. Move the old data folder aside yourself once
  you have checked the result; nothing deletes it for you.
- **Before every upgrade** the app makes a safety copy in the data folder's `backups/` folder when a migration is about to
  change a database that has data, and does not start if that copy fails. It also refuses, in plain words, a database that
  a newer version of the app has changed.
- **By hand until PD-6.5 schedules it:** run the backup command before every update and once a week. Until then, stored
  originals exist in only one place.
- **Still to come (PD-6.5):** the nightly run, 35 days kept (removing old backups needs the owner's yes, so the app will
  ask or list them, never silently delete), and a second copy on a different disk.

## 7. Update (PD-6.5)

`git pull`, then restart the service. **Run exactly one copy of the app**: the scanner, the SQLite file and the held upload previews all assume a single process, and a second copy would break them. On start the app backs up the database (§6), then applies migrations. If a migration
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

The report uploaded on **Import CSV or Excel** by mistake is recognised and shows this same preview; nothing is created
until Create is pressed. The app's own import file uploaded on **Load from QuickBooks** is refused with a link to
**Import CSV or Excel**.

What it does: currency USD, unit EA; the QuickBooks date is kept as the **PO date (QuickBooks)** and the date sent is left
empty, so the app does not flag a missing acknowledgement it never had the chance to see; a customer named on a line is
shown as "· for <customer>"; a line partly received gets "received N per QuickBooks export of <date>". What it never
does: create vendors, change a PO already in the app, read `Other 1`, `Print` or the delivery dates (those come from
emails), or close anything. Closing from the weekly export waits for OPEN-20.


## 15a. Customer orders (PD-8.2)

**Customer orders** in the top bar is for the purchase orders that customers send to RaceSpec; it is separate from
Purchase orders (what RaceSpec sends to vendors). Everyone signed in can use it.

1. **Add the customer first** (Customers). Write the names QuickBooks uses for it, spelt the same, and the email domains
   its orders come from. A customer is found only by an exact name or domain, never by a similar one.
2. **File an order** with New customer order, or Import CSV or Excel (download the template). A file is previewed first:
   it shows what each order would do and files nothing until you confirm.
3. **Same order again:** the same number with the same terms changes nothing. The same number with different terms
   becomes a revision that waits: open the order, read the changes listed, then Approve or Reject (a reason is needed).
4. **Waiting for a customer:** an order whose customer was not found waits on that page. Choose the customer, or write the
   name of a new one, and it is filed as it came. Reject one that is not yours, with a reason.

Nothing here reads email yet: customer orders come in by hand or by file until the email reader is built (PD-8.8).

**Sales invoices (PD-8.3).** From Customer orders, Sales invoices, upload a QuickBooks Desktop sales export (CSV or Excel).
Until a real export has been seen the app is tested on made-up files only (OPEN-22), so check the preview each time.

1. **Upload, then read the preview.** It says which row holds the column names, whether matching will be by line or by
   invoice total, how many invoices are new, already known, changed or sharing a number, and which rows were not read and
   why. Nothing is kept yet.
2. **Check the columns** under "Which column holds what"; change any that are wrong and press Show again.
3. **Say what dates the export covers** (suggested from the invoice dates). The app never calls an order unbilled without them.
4. **Keep this upload.** The file, every row and the dates are stored. An invoice already accepted is never overwritten: one
   that changed shows "needs review" with what the later upload said. The same file again is refused.
5. **Customers the export names that the app does not know** are kept without a customer; choose each one under
   "choose the customer". The name is then remembered for the next export.

## 16. Connecting the Gmail test mailbox (PD-3.5, DEC-50)

Use a **dedicated test Gmail account** that holds only made-up mail (DEC-13). The app reads it with the `gmail.readonly`
permission only, so Google itself refuses a send, move or delete. Nothing secret goes in a file or in chat.

**Once, in the Google Cloud console (free):**
1. Create a project (any name). Under *APIs and services* turn on the **Gmail API**.
2. *OAuth consent screen*: user type **External**, publishing status **Testing**. Under *Test users* add the test Gmail
   address. Under *Data access* add the one scope `.../auth/gmail.readonly`.
3. *Credentials*, *Create credentials*, *OAuth client ID*, application type **Desktop app**. Keep the client id (it ends
   `.apps.googleusercontent.com`) and the client secret to hand; do not paste them anywhere but the prompt below.

**Then, in Terminal on this Mac:**
1. `uv run po-dashboard mailbox gmail-client` asks for the client id and secret at hidden prompts and saves them in the
   Keychain.
2. `uv run po-dashboard mailbox connect-gmail` opens Google's sign-in page. Sign in as the test account and allow
   read-only access. Add `--days 7` to also read the last seven days of mail (the default 0 reads only mail that arrives
   after connecting, so an old inbox does not flood the Unmatched queue).
3. The scanner (running inside `serve`) picks the mailbox up on its next 5-minute tick.

**Or on the Mailboxes screen (PD-3.7, admins only).** With the client saved (step 1 above), open
`http://127.0.0.1:8000/mailboxes` **in a browser on this Mac**, press *Connect Gmail* (or *Reconnect Gmail* if it says
needs reconnect), then *Continue to Google* and sign in. Google sends the browser back to the Mac you are on, so this
cannot be done from a laptop (the page says so), and while HTTPS is on it is done in Terminal instead. The page shows
each mailbox's state, its last check and why a scan failed, and a *Disconnect* button. Version 1 connects one mailbox: to
use a different account, disconnect the first.

**Weekly, while the Google project is in Testing mode:** Google ends the sign-in after about 7 days. The scan then stops
for that mailbox, Home's last-scan tile ages, and the mailbox is marked *needs reconnect*. Run `connect-gmail` again; no
mail is lost, because the saved position is kept. `uv run po-dashboard mailbox disconnect-gmail ADDRESS` forgets the saved
sign-in (the mail already stored stays).
