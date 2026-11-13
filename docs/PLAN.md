# PO Dashboard: Plan and To-Do

> **Status, 2026-11-12:** Phases 0 to 3 are built. **Phase 4 is in progress:** its design is written (PD-4.0, DEC-54) and
> the queue, the extractor, the model gateway, the validator, matching, the Review screens and Accept are built (PD-4.0a
> to PD-4.5); PD-4.5a (the QuickBooks charge rows) is done; PD-4.6 comes next. **Phase 8 (customer orders and invoice reconciliation)**, which the owner asked for first, is built
> (PD-8.0 to PD-8.7); only PD-8.8, customer POs from email, waits for Phase 4. Left from Phase 3: the owner's check of the Mailboxes screen with the real
> Google account (TC-MAIL-28). **Order (owner, 2026-11-12), focus: Gmail testing, PO matching, drafting, chat.**
> (1) Phase 4 without PD-4.7. (2) Gmail end to end on synthetic mail. (3) Drafted follow-ups (PD-5.3 pulled forward,
> from the flags that already exist). (4) Chat (Phase 9, proposed). (5) Then Outlook (Phase 6), the rest of Phase 5 and
> PD-8.8. **Gate:** PD-4.7 (the accuracy baseline on the 14b model) must run before any real mail is read or Outlook is
> connected (DEC-13). The deferred list below waits until the front end, backend and AI layer are finished.
> **Sources:** [REQUIREMENTS.md](REQUIREMENTS.md), [ARCHITECTURE.md](ARCHITECTURE.md), [DECISIONS.md](DECISIONS.md).

**How this file works.** Each task has an ID (`PD-3.2`) and a checkbox. Phases run in order and each ends with a demo
or a test run you can check. Nothing is built until you approve the plan; after that I tick tasks as they are done and
update [AUDIT.md](AUDIT.md), the status table in the [README](../README.md) and the status line above at the end of each phase. A phase that needs an answer from [DECISIONS.md](DECISIONS.md)
§2 says which.

**Documentation first (DEC-25).** Every phase starts with a `PD-n.0` task, before any code: bring the docs in line with
what really happened, ask the owner every OPEN question the phase needs, write down any design it needs, then get the
go-ahead (including the README's status table, which is the public front page). A task is ticked only after it has been run for real, not just written.

**Models.** Design and scoring work (the Phase 1 sign-in design, Phase 2 scoring design, Phase 6 security check, the final audit) suits Opus.
The rest is following this plan, which suits Sonnet. I will say before each switch.

## Deferred until the front end, backend and AI layer are finished

*The owner, 2026-11-12: finish the build first, then come back to these. Each stays unticked where it sits in the plan;
this list gathers what the plan already marks as skipped or waiting for the owner.*

- PD-4.7: the golden set through the whole pipeline on the 14b model, with PD-2.5's first score. Until it runs, the
  model's accuracy is not measured; a person accepting every document (DEC-08) is the safeguard.
- TC-MAIL-28 (PD-3.7): the owner's check of the Mailboxes screen with the real Google account.
- PD-1.10a.4: the owner's check of the QuickBooks load ([BROWSER_CHECK_QUICKBOOKS.md](../test-evidence/BROWSER_CHECK_QUICKBOOKS.md)).
- PD-1.13: the owner's first test cycle with the workbook.
- PD-1.9d step 15: the 31-minute idle sign-out, by hand.
- PD-0.5: CI's first run, on the first push (the owner's call).

## Phase 0: Skeleton

*Needs: nothing open. macOS only (DEC-18); the repository is public (DEC-19) and the licence is chosen (DEC-23). No GitHub remote exists yet; pushing is the owner's call.*

- [x] PD-0.1 Create the Git repository locally (not pushed); first commit contains these docs.
- [x] PD-0.2 `pyproject.toml` with uv, Ruff, pytest; the folder layout from ARCHITECTURE §14.
- [x] PD-0.3 `scripts/setup.sh` for macOS: Python, dependencies, checks for Ollama and Tesseract, states the 9 GB
  model size and asks before pulling it.
- [x] PD-0.4 A page that starts and shows "PO Dashboard" and the health strip.
- [~] PD-0.5 CI on GitHub Actions: Ruff and the fast tests on every push (once the repo is on GitHub). **Written 2026-11-11** (`.github/workflows/ci.yml`: lock check, locked install, ruff, strict mypy, all tests, the real-documents guard over every tracked file, the test-record check; `.github/dependabot.yml`). It has not run, because nothing is pushed. Actions are pinned by major version; pin them to commit SHAs on the first push. On the first push also turn on branch protection with the CI check required.
- [x] PD-0.6 A pre-commit check that refuses `.eml`, PDFs, databases and `.env` outside `tests/data/` (RISK-07).

**Demo:** `scripts/setup.sh && uv run po-dashboard serve` opens an empty app at http://127.0.0.1:8000.

## Phase 1: Sign-in, ledger, status engine and dashboard (no mailbox, no AI)

*Needs: OPEN-08 answered (DEC-24). OPEN-10 is answered (DEC-28), so the allowed names and HTTPS are settings from the start. The 6-digit code is paused (DEC-34). DEC-26, DEC-27 and DEC-32 are for the owner to confirm. Built in the order below, agreed 2026-11-04, so task numbers are not in numeric order.*

- [x] PD-1.0 Documentation pass (DEC-25), and the Phase 0 bugs it found.
- [x] PD-1.9a The sign-in design, on Opus: ARCHITECTURE §10.1.

**Step 1: sign-in first, so every later page sits behind it.**

- [x] PD-1.1a Database plumbing: SQLAlchemy, Alembic, WAL, an owner-only data folder, and only the tables sign-in needs
  (`app_user`, `session`, `recovery_code`, `audit_log`).
- [x] PD-1.9b Sign-in core: argon2id, sessions, lockout, temporary passwords, the last-admin rule, audit, and the
  `po-dashboard user` commands (`create-admin`, `create`, `reset-password`, `unlock`, `list`). **The 6-digit code, recovery
  codes and the Keychain sealing key are paused (DEC-34)**; they are added when a second factor is settled.
- [x] PD-1.9c Sign-in web: the default-deny gate, the sign-in and change-password pages (the code and enrol pages are paused), CSRF, Host and
  Origin checks, security headers, and the admin Users screen. `/health` shrinks to up or down. The allowed names and HTTPS are settings from the start (DEC-28).
- [x] PD-1.9d The security tests listed in ARCHITECTURE §10.1 (124 tests pass; the items about the paused second
  factor wait for it), and a run-through by hand in Safari and Chrome, [BROWSER_CHECK_SIGN_IN.md](../test-evidence/BROWSER_CHECK_SIGN_IN.md),
  done 2026-11-04. **Step 15 (the 31 minute idle timeout) was skipped by the owner's choice**; the automated tests cover it,
  and it is to be run by hand at a later stage. Small look-and-feel and behaviour tweaks the owner noticed are to be
  listed and fixed in PD-1.8.

**Step 2: tables.**

- [x] PD-1.1b The ledger tables and migrations for ARCHITECTURE §4, with exact decimals and a currency on each PO (DEC-24). Built: vendors and their domains, POs and lines, acknowledgements, promised-date history, shipments and invoices (migration 0002, 43 tests). The mail, document, extraction and alert tables come with their own phases.

**Step 3: status engine.**

- [x] PD-1.3 The status engine (§5, DEC-36) with unit tests for every row of the table, including short confirmation and
  partial invoice. Built: `ledger/status.py`, 43 tests; each rule was broken on purpose to confirm a test catches it.

**Step 4: PO screens.**

- [x] PD-1.2 Vendors screen; PO entry by hand; CSV import (REQ-PO-01, -02, -04). **Built and tested** (DEC-37; vendors, the entry form, the all-or-nothing CSV import, the PO list and a first PO page that already shows status from the engine; 97 new tests). **Browser check done by the owner in Safari and Chrome, 2026-11-05** ([BROWSER_CHECK_PO_SCREENS.md](../test-evidence/BROWSER_CHECK_PO_SCREENS.md)).
- [x] PD-1.4 Manual acknowledgement, shipment and invoice entry, so the whole life cycle works before any AI. **Built and tested** (DEC-38: three entry forms, history with removal by reason, promised-date slips, price and over-billing flags; 55 new tests, and a real server was driven from Sent to Shipped over HTTP). **Browser check (steps 18 to 27) done by the owner in Safari and Chrome, 2026-11-05.**
- [x] PD-1.5.0 Documentation pass (DEC-25): DEC-39 and OPEN-16 written 2026-11-05; DEC-40 and OPEN-17 answered the same day.
- [x] PD-1.5 PO list and PO detail. *Browser-checked by the owner (steps 28 to 35, 2026-11-05; the timeline fix re-checked 2026-11-06).* **Slice a built 2026-11-05** (list filters with calendars for sent and expected dates, expected date per line with the shipment estimate taking over from the promise, late flags with days late, carrier tracking link; 23 new tests). **Slice b built 2026-11-05:** the Cancelled status, one "Settle lines by hand" form (any lines, one reason; received, closed short, cancelled), reopen with a reason, and a timeline for each line; 12 new tests. **Still to do:** the "Waiting to confirm" batch screen and receipt tables **Dropped 2026-11-05:** the owner does not mark anything received (DEC-39, DEC-43), so the "Waiting to confirm" screen and the receipt tables are not built. Delivery will be inferred from carrier emails (DEC-43, proposed). Original scope: (REQ-DB-02, REQ-INV-04, DEC-39). (a) List filters: status, vendor, date sent, expected date range with calendars. (b) Receiving as a **batch check-off** (DEC-39): one "Waiting to confirm" screen of shipments and invoices, one click for all fine, exceptions only (short quantity, notes or issues); new `receipt` tables; only the lines on a document change. (c) Carrier tracking link per shipment. (d) Line timeline. (e) Late and back-ordered flags, a **Cancelled** status, and the manual settle for the update-the-PO flag. *Started in PD-1.2:* a simple list with search and a PO page with each line's computed status. *Moved to later phases (DEC-40):* the draft chaser and its "draft ready" alert (Phase 5); delivery read from carrier emails (Phases 3 and 4). Until then PD-1.5 only stores the tracking number and shows the link. *Moved to Phases 3 and 4:* arrival and the update-the-PO flag from emails.

**Step 5: dashboard, audit, design.**

- [x] PD-1.6 Dashboard counts, open-items report, CSV export (REQ-DB-01, -03). **Built and tested 2026-11-05** (DEC-42: home tiles that link to the report, an Open items page with a view and vendor filter, CSV download with formula-safe cells; 27 new tests). **Browser-checked by the owner:** steps 36 to 40 of [BROWSER_CHECK_PO_SCREENS.md](../test-evidence/BROWSER_CHECK_PO_SCREENS.md).
- [x] PD-1.7 Audit trail on every change (NFR-03) and its screen (admins). Sign-in events are written from PD-1.9b. **Built and tested 2026-11-05:** an admin-only, read-only Audit page (filter by action, person, what it was about and dates; 50 a page; text never markup) and a guard test that every ledger change leaves a record; 7 new tests. **Browser-checked by the owner:** steps 41 to 44 of [BROWSER_CHECK_PO_SCREENS.md](../test-evidence/BROWSER_CHECK_PO_SCREENS.md).
- [x] PD-1.9e Name, look and feel (DEC-35): RaceSpec Internal Dashboard, light theme, inline icons, logo committed at the owner's say-so, the cursor ready in the first box of every form page. The sign-in and People pages are done.
- [ ] PD-1.10 *Proposed, waiting for OPEN-15:* reconcile with the ERP's export (DEC-41). A folder the app reads, a compare screen, and the update-the-PO flag clearing when the export matches. The export should carry, per PO line: **PO number; line number (or item number); vendor; item or part number; ordered quantity; received quantity; invoiced quantity; open quantity; line status (open, closed, cancelled); closed date; unit price; PO date; the promised or required date**. Nice to have: currency, invoice number, last receipt date, buyer. It must include **closed lines for the period**, not only open ones.
- [x] PD-1.11 Vendor import in a fixed format (DEC-44). **Built and tested 2026-11-05** (an Import vendors button on the Vendors page; creates new, adds to existing, lists bad rows; 23 new tests). **Browser-checked by the owner 2026-11-06** ("we are good with all tests"): steps 45 to 53 of [BROWSER_CHECK_PO_SCREENS.md](../test-evidence/BROWSER_CHECK_PO_SCREENS.md).
- [ ] PD-1.10a The QuickBooks starting load (DEC-46) and Excel uploads (DEC-47). *Designed 2026-11-06 on Opus with the owner's answers; Sonnet for the build, Opus for the hostile-file review.* **Go-ahead from the owner 2026-11-06.**
  - [x] PD-1.10a.1 (2026-11-06) Spreadsheet reading: one reader for CSV and .xlsx used by all three uploads, with the limits in DEC-47 and hostile-file tests (zip bomb, entity bomb, macros, formulas without saved values). Adds `openpyxl` and `defusedxml`.
  - [x] PD-1.10a.2 (2026-11-06) Migration 0003: `purchase_order.po_date`, `po_line.customer`. The PO page shows "PO date (QuickBooks)" and "· for <customer>"; Open items and the CSV show the customer.
  - [x] PD-1.10a.3 (2026-11-06) The QuickBooks reader (headings, totals, export date from the name), the preview (to create, waiting for a vendor, already in the app, rounded prices, partly received) held in memory for 15 minutes, and the all-or-nothing create with "received per QuickBooks" entries and audit.
  - [ ] PD-1.10a.4 Tests on a made-up report with the same shape as the owner's (headings, totals, spread lines, partly received, a price that cannot be exact, a missing vendor); Claude drives it in Chrome; browser-check steps for the owner. **Done 2026-11-06 except the owner's check:** 50 new tests (28 on the reader, many hostile files); 14 Chrome checks of the load and 51 of the earlier screens pass; the owner's real report read in a dry run with 0 problems (38 POs, 114 lines; counts only). **Waiting for:** [BROWSER_CHECK_QUICKBOOKS.md](../test-evidence/BROWSER_CHECK_QUICKBOOKS.md).
  - [x] PD-1.10a.5 (2026-11-07) Found in the owner's check, step 4: the report sent to **Import CSV or Excel** (the button beside **Load from QuickBooks**) was refused with a list of missing columns. That page now recognises the report and shows the QuickBooks preview; the app's own file on the QuickBooks page is refused with a link to Import. 3 tests; 7 Chrome checks; checklist steps 15 and 16 added.
  - Not in PD-1.10a: closing lines missing from the report (OPEN-20) and the weekly comparison (PD-1.10).
- [x] PD-1.12 Vendor search box (2026-11-06): the vendor drop-down on the New PO form, the PO list filter and the Open items filter is a type-to-search box (`GET /vendors/search`, matches name or email domain), so hundreds of vendors stay quick and the page is not reloaded. Vendor upload now requires an email. Steps 50 to 53 of the browser check.
- [x] PD-1.8 The design pass for every screen, to the web standards in DEC-45. *Opus for 1.8a (the standards); Sonnet for the rest.*
  - [x] PD-1.8a The owner approved DEC-45 on 2026-11-06, unchanged. Date format decided 2026-11-06: MM/DD/YYYY, as in QuickBooks.
  - [x] PD-1.8b HTMX in place (2026-11-06): the pinned file and its checksum test, the CSP-safe settings, the CSRF header, every page boosted, the existing scripts re-attached after a swap, busy buttons, expired session goes to sign-in, background updates do not count as activity.
  - [x] PD-1.8c Tables (2026-11-06): sorting, 50 a page with counts, the heading row kept on screen. PO list, Open items, Vendors, People, Audit.
  - [x] PD-1.8d In place (2026-11-06): fading Saved notes on every form, errors next to their box, filters as you choose, the `/` key, messages read out by screen readers.
  - [x] PD-1.8e (2026-11-06) Home as the exceptions list with "All clear", and the 60-second live counts on Home and Open items.
  - [x] PD-1.8f Phone width and accessibility sweep, one date and time format, the large-data speed test, and the owner's own list of tweaks. Browser check in Safari and Chrome. **Built 2026-11-06 except the owner's tweak list:** Claude ran 51 checks in real Chrome (desktop and phone width, all pass, no script errors or policy violations) and the speed test (slowest page 137 ms on 300 vendors, 1,000 POs, 5,000 lines). **Browser-checked by the owner 2026-11-06** ([BROWSER_CHECK_DESIGN.md](../test-evidence/BROWSER_CHECK_DESIGN.md)): "we are good with all tests". One idea came out of it and is parked below.

- [ ] PD-1.13 The test record and user sign-off (owner, 2026-11-07: "a full test cases document ... for production
  sign off"). *Opus for what the sign-off must cover; Sonnet to keep it up.* **Built 2026-11-07:**
  [test_record.toml](../test-evidence/test_record.toml) holds every test case (83: steps, expected result, the
  automated tests that cover it, the owner's last result), the defect log, and 25 sign-off checks (15 ready now, 10
  waiting for later phases). `scripts/test_record.py` builds [TEST_CASES.md](../test-evidence/TEST_CASES.md),
  [DEFECTS.md](../test-evidence/DEFECTS.md) and [SIGN_OFF.md](../test-evidence/SIGN_OFF.md) from it, and with
  `--workbook` an Excel workbook for a test cycle (Read me, Test cases, Evidence, Defects & retests, Sign-off), which
  stays on this Mac. `scripts/browser_evidence.py` drives Chrome through every browser case from an empty throwaway
  database and saves a screenshot of each result. Its first runs found DEF-002 (a buyer opening an admin address saw
  raw code), fixed the same day. A test keeps the record true. From now on a failure is logged the moment it is found
  (DEC-25 still applies before each phase). **Waiting for:** the owner's first cycle with the workbook. PD-7.2's
  checklist becomes the Sign-off tab.
- [x] PD-1.13a (2026-11-07) Refusals, missing pages and unreadable forms are pages a person can read (DEF-002), not raw
  JSON; a script still gets JSON. 3 tests; TC-WEB-06.

**Parked (owner, 2026-11-06: helpful, not needed now):**

- An **Add and start another** button beside Add vendor (and Create and start another on New purchase order), returning
  to an empty form with a fading "Saved" note. Put aside because vendors will come from the file upload (DEC-44).

**Demo:** enter a PO, add a partial ack, a partial shipment and a partial invoice by hand; the line statuses, the
dashboard and the open-items report follow.

## Phase 2: Synthetic data and the golden set

*Opus for PD-2.1 (the scoring design, written 2026-11-10); Sonnet for PD-2.2 to PD-2.5. Nothing more is needed from
the owner except the go-ahead.*

- [x] PD-2.0 Documentation pass (DEC-25), 2026-11-10. The owner's answers: tax is rare, freight varies, and tariffs
  sometimes come as their own line items (OPEN-11 closed; DEC-48, REQ-INV-06); vendors send every kind of document
  (typed in the email, PDFs, scans, spreadsheets) and write only US English (DEC-49); made-up documents only for now
  (OPEN-09 moved to Phase 6). OPEN-21 (charge lines on POs) was answered the same day: charges are information only. ARCHITECTURE §4, §6 and §13
  brought in line; the test record replaces the Phase 7 checklist.
- [x] PD-2.1 What "correct" means field by field, and the scoring rules: [SCORING.md](SCORING.md). Written and
  approved by the owner 2026-11-10.
- [x] PD-2.2 The seeded generator (`evals/golden/generate.py`): about 100 documents as SCORING.md §5 lists them, each in
  its real form, its text, and its answer file. Two new dev dependencies to write PDFs and scan images: `reportlab` and
  `pillow` (a few MB together). Done 2026-11-10: 100 documents (30 acknowledgements, 22 shipment notices, 25
  invoices, 8 purchase orders, 15 others; 36 emails, 30 PDFs, 12 spreadsheets, 11 scans, 11 automatic notices). The
  same seed gives the same bytes.
- [x] PD-2.3 The golden set committed under `tests/data/golden/` (made up only) with `GOLDEN_SET.md` listing it, and a
  test that the generator rebuilds it exactly from the seed (TC-AI-01). Written to the folder; **not committed
  until the owner asks.**
- [x] PD-2.4 The scoring run `evals/score.py`, starting from the 2026-11-04 trial script. Pulled forward from PD-4.1 so
  the run and the app share one prompt: `extraction/schema.py` (schema v1), `extraction/prompts.py` (prompt v1, the
  document fenced as data, DEC-09), and the model call in `models/llm.py` (local Ollama only). Unit tests for every
  scoring rule with made-up model answers, so they need no model (TC-AI-02, TC-AI-03). Done 2026-11-10. A reply stops
  at 2,048 tokens, so a runaway answer cannot hold the run for minutes.
- [~] PD-2.5 Score `qwen2.5:14b-instruct`: **skipped by the owner, 2026-11-10** ("not worried about knowing exactly how
  fast or slow the model is"). The 14b model wrote about 3 tokens a second on this Mac while it was swapping, which
  made a full two-pass run about 9 hours. What is known: the pipeline works (`evals/score.py --only` on 3 documents,
  7b), and the 14b read ack-01 and ack-06 correctly. No accuracy baseline is set. The first full score and
  `evals/golden_baseline.json` now come with PD-4.7, on the whole pipeline, when Phase 4 is built.

**Demo:** one command prints the score table against T-1 to T-5 (T-5 waits for the validator, PD-4.2).

## Phase 3: Mail sources and intake

*Sonnet for building; Opus for the hostile-attachment review (PD-3.6) before anything real is read. Needs from the owner:
the go-ahead on DEC-51, and, at PD-3.5, a free Google Cloud project and the dedicated Gmail test account (DEC-31),
which holds only synthetic mail. Its address and password never go in the repo, a log or the chat: the owner enters
them. The Review screen and the Unmatched queue are Phase 4 (PD-4.3, PD-4.4): they show extractions, and none exist until
the model is wired in.*

- [x] PD-3.0 Documentation pass (DEC-25), 2026-11-10. OPEN-04 answered (DEC-50: Gmail API, `gmail.readonly`, Google's
  sign-in page); the Mailboxes screen stays in Phase 3; ARCHITECTURE §6 corrected (a charge is never a flag, OPEN-21);
  DEC-51 proposed: how each kind of document is read, the limits, and "only mail after connecting" on first connection.
- [x] PD-3.1 `MailSource` interface, the `mailbox` table (one row in v1, more in v2, DEC-33; Inbox and Sent Items) and the folder provider; contract tests every provider must pass. Done 2026-11-10 (TC-MAIL-01 to 04): migration 0004; a read-only guard that reads the whole app's source (checked by three deliberate breaks); the contract in `tests/mail_contract.py` that Gmail must also pass.
- [x] PD-3.2 Intake: store originals, hashes, dedupe, message and document tables (REQ-IN-04, -05). Done 2026-11-10 (TC-MAIL-05 to 07): `intake/store.py` and `intake/messages.py`, migration 0005. `store_message` does not commit and is not yet called by anything: the scanner loop (PD-3.4) calls it.
- [x] PD-3.3 Text extraction (DEC-51): HTML body, text PDF, OCR, XLSX/CSV, DOCX, quoted-thread marking (REQ-IN-03). Done 2026-11-11 (TC-MAIL-08 to 10): `intake/text.py` (the readers) and `intake/reading.py` (stored, then read, not_read or failed, or waiting if Tesseract is missing). Against the golden set: emails 99.9%, typed PDFs and spreadsheets 100%, scans 98.7% (OCR) of the expected text. New dependencies `pypdf`, `python-docx` and Pillow (now a runtime dependency); Tesseract was already installed. `read_pending` is not yet called by anything: the scanner (PD-3.4) calls it. CI installs Tesseract with Homebrew.
- [x] PD-3.4 The scanner loop with a saved position, one scan at a time, and the crash test. Done 2026-11-11 (TC-MAIL-11 to 14): `scheduler/scanner.py` (fetch, store, read; one transaction per batch holding the messages and the position that covers them), `scheduler/loop.py` (an asyncio task, one scan every 5 minutes, started only by `po-dashboard serve`), the `scan_state` table (migration 0008), and the "Last scan" tile on Home. A scan that overlaps another is skipped; at most 200 messages and 25 documents are handled per scan (a bigger burst carries on 5 seconds later); a real process killed mid-scan loses and repeats nothing. **`BEGIN IMMEDIATE`:** used by the scanner's own write transactions only, and OCR and network work happen outside any transaction; applying it to every request would make page views take the write lock, so the web app is unchanged (`test_speed.py` unaffected). **Moved to Phase 4:** the "model not reachable" waiting state and the cap on extraction work, because nothing calls the model yet; they go in with PD-4.1.
- [x] PD-3.5 The Gmail provider (read-only, DEC-50) and a test that proves it cannot write (REQ-IN-02). **Done 2026-11-11 (TC-MAIL-15 to 18 against a pretend Gmail; TC-MAIL-19 passed on the real test account: both emails read, Gmail untouched). Reconnecting with more `--days` now widens the window and resets the saved scan position.** `mail/gmail.py` (the provider: history-id position, fixed host, GET only, retries with backoff and jitter honouring `Retry-After` for 429, 5xx, connect errors and Google's two 403 rate-limit answers, never for 401 or other 403), `mail/gmail_auth.py` (PKCE, checked `state`, a loopback listener on 127.0.0.1, the Keychain through `keyring`, token refresh), `mail/gmail_connect.py`, and the terminal commands `po-dashboard mailbox gmail-client | connect-gmail | disconnect-gmail` (OPERATIONS §16). A lapsed sign-in marks the mailbox `needs_reconnect` and the scanner leaves it alone. The connect flow runs from Terminal for now; PD-3.7 puts the same functions behind the Mailboxes screen (the callback is then the app's own `/mailboxes/callback`). New dependency: `keyring`. Tests never touch the real Keychain (a fixture refuses it).
- [x] PD-3.6 Hostile-attachment tests: oversize, wrong type, a PDF bomb, a spreadsheet with formulas. **Done 2026-11-11 on Opus (TC-MAIL-20 to 24, DEC-53 decided).** Every reader was probed with made-up hostile files first (BUILD_HISTORY). Fixed: each document is now read by its own process in the macOS sandbox with a 10-minute and 1.5 GB limit (`intake/worker.py`); intake parses headers from raw bytes with the plain parser (the structured one needed over 2 GB for a 0.3 MB Subject); a message with more than 2,000 MIME parts is stored whole and not read; Pillow opens five image formats only and pypdf never runs `jbig2dec`; the Gmail sign-in listener drops a silent connection after 10 s. About 0.1 s more per document.
- [~] PD-3.7 The Mailboxes screen: connect, reconnect, disconnect the one mailbox, show its state and last check, with
  the wording in ARCHITECTURE §7 (REQ-IN-07). **Built 2026-11-11 (TC-MAIL-25 to 27 pass: the tests with a pretend Google, and real Chrome through the whole round trip); the owner's real-Google check TC-MAIL-28 is not run yet, so this is not ticked.** `pages/mailboxes.py` and three templates; admin only, with a Mailboxes link. Connect keeps the one-time `state` and PKCE verifier in memory (10 minutes, once) and shows one plain *Continue to Google* link (a form post or boosted link cannot leave for Google under the page's security policy). Google's return is the app's own `/mailboxes/callback`, a **public route** because the session cookie is `SameSite=Strict` and is not sent on that return; it only accepts a `state` the app issued, then hands over to `/mailboxes/finish`, which needs the admin who started it. One mailbox at a time (DEC-33). Works from a browser on the office Mac only (Google allows an `http` loopback return only); from a laptop or with HTTPS on, the page says what to do. Connect and disconnect are audited. `finish_connect` is now shared with the Terminal command. Not done here: a Home banner when a mailbox needs reconnecting (PD-6.x health banner).

**Demo:** drop the golden `.eml` files in a folder, run the scanner, see every message and attachment stored once.
Then the same through the Gmail test account.

## Phase 4: Extraction, validation, review queue

*Design: ARCHITECTURE §6.1 to §6.4 and DEC-54 (written on Opus in PD-4.0). Sonnet for building, Opus for PD-4.6's
security tests and the PD-4.7 scoring. Tests use a stub model; only PD-4.7 needs the 14b model (about 9 GB of memory,
already downloaded).*

- [x] PD-4.0 Documentation pass (DEC-25), 2026-11-12. The owner's answers: a document from someone other than the PO's
  vendor goes to Review with a tick-to-accept check; documents with no order facts are filed, not queued; a resent or
  revised emailed PO shows its differences and changes nothing; the QuickBooks load skips charge rows (DEC-55, closes
  the check OPEN-21 left). Written: DEC-54 (the pipeline), ARCHITECTURE §4 (the `extraction`, `shipment_tracking` and
  `invoice_charge` tables) and §6.1 to §6.4 (queue, matching, flags, review), the screens in §9. Brought in line:
  AUDIT.md (not updated since Phase 0: 12 requirements met, 22 partly), the test record's swapped PO references,
  DEC-53's table row. DEC-54 confirmed by the owner, 2026-11-12.
- [x] PD-4.0a The tables and the queue (§6.1). **Done 2026-11-12 (TC-QUE-01 to 07: 48 tests, and the Failed documents page checked in real Chrome).** Migration 0009: `extraction` (queue and review columns, `version`), `shipment_tracking`, `invoice_charge`, and a real foreign key from acknowledgements, shipments and invoices to `source_document` (the rebuild SQLite needs for that dropped migration 0006's case-insensitive invoice-number index, which the migration now puts back). `extraction/queue.py` (queue each read document once, claim by one guarded `UPDATE`, recover `running` rows at start, the Failed list with Retry and Dismiss), `extraction/runner.py` (the extractor: no transaction during a model call; model down costs nothing; retries after 1, 5 and 30 minutes then failed; a bad answer fails at once; fixed words only), `extraction/window.py` (about 15,000 characters, quoted earlier messages first, `read_in_part`), the extractor's own loop in the app beside the scanner's, the scanner queueing in the transaction that records the text, and `/documents/failed` with a Home tile. `models/llm.py` now tells `BadAnswer` (not tried again) from a server error, and keeps Ollama's prompt count. The extractor must be given its model call, so no test can reach a real model. Nothing reads a document with the real model yet: PD-4.1 to PD-4.5 build what is done with an answer.
- [x] PD-4.1 The model gateway finished: Ollama's prompt count kept, errors sorted into "try again" and "will fail the
  same way", the health strip's "model not reachable", waiting and failed counts. A test that only `models/llm.py`
  calls the model server (NFR-10). **Done 2026-11-12 (TC-QUE-08).** Most of it was built with PD-4.0a (the prompt count,
  `BadAnswer` against a server error, `ModelUnavailable`, the Home tiles); this task added the source-reading test that
  no module but `models/llm.py` (and the address setting in `config.py`) names the model server, and a test of the
  Home "not reachable" / "reachable" word.
- [x] PD-4.2 The validator (§6.3): every flag, each with a test, named as in the golden set; `value_not_in_source` with
  numbers, US dates and codes (OCR look-alikes equal); `planted_instruction` caught on all 8 golden documents that
  have one and on none of the other 92. **Done 2026-11-12 (TC-VAL-01 to 05: 62 tests).** `extraction/validate.py` (the
  19 flags, pure: the answer, the text and a `Context` in, `Flag`s out, stored as `{flag, level, line, charge, field}`,
  never document text), `extraction/in_source.py` (numbers, US dates and codes in the text; OCR look-alikes equal only
  when the caller says the text came from OCR), `extraction/matching.py` (the §6.2 line rule only, which the flags
  need; PD-4.3 adds the PO and the vendor). All 100 golden answers give exactly their `expected_flags`; altering the
  golden values, 934 of 947 are caught (the rest are numbers or dates found elsewhere in the same text). The golden
  `po_context` now carries each line's unit and last accepted promised date (needed for `unit_differs` and
  `date_changed`); no document or answer changed. **For PD-4.3:** it decides `ocr` (the text step does not record
  it yet) and each line's `open` for the kind of document, and passes `sender_is_vendor`, `po_exists`, the earlier
  extractions and the recorded invoice and tracking numbers.
- [x] PD-4.3 Matching (§6.2): PO number exactly, else the document's other facts (vendor order, tracking and invoice numbers, equal items and quantities: DEC-71), the sender only as supporting evidence, lines by stated number or one equal item; an
  emailed PO's vendor from its recipients; the Unmatched list with the sender's vendor's open POs; the No order facts
  list (including Sent Items that are not POs). **Done 2026-11-12 (TC-MATCH-01 to 06: 29 tests).** DEC-71 confirmed
  by the owner. `extraction/matcher.py` (`decide`, the ledger's inputs to the validator, the two lists, the candidates for
  Unmatched), called by the extractor when it stores an answer, so a read document now ends `waiting`, `unmatched` or
  `no_facts` with its PO, vendor and flags. The vendor order number is read from accepted extractions' saved values (the
  owner's choice over a new column). **Left for later:** scanned PDFs are not told apart from typed ones, so the OCR
  look-alike rule applies to images only (the text step would have to record it); the `read_in_part` flag is still only
  set from the window, not from Ollama's prompt count.
- [x] PD-4.4 The Review screen (§6.4): a message's documents, text beside fields, every field editable, lines beside
  their PO lines with differences and flags, the three ticks, Accept, Reject with a reason, File, Attach to a PO; the
  original served as a download (a PDF or picture may open in the browser's own viewer under a sandbox policy). Home's
  counts gain "waiting for review" and "unmatched" (REQ-DB-01). **Done 2026-11-12 (TC-REV-01 to 06: 12 tests, and the screens checked in real Chrome, 65 of 65).**
  `extraction/review.py` (the lists, the message view, Reject, File, Attach, the original), `pages/review.py` (`/review`,
  `/review/message/{id}`, `/review/{id}/reject|file|attach`, `/review/original/{document_id}`), the templates
  `review.html` and `review_message.html`, a Review link in the top bar, and Home tiles "Waiting for review" and
  "Unmatched". **Accept is not here:** its button is disabled and its form only reloads the page (no token in the address),
  because recording the entry is PD-4.5; the fields are named so PD-4.5 can read them. **Decision for PD-4.5:** the edited
  fields are not saved by anything in PD-4.4; Accept will take them from the form.
- [x] PD-4.5 Accept and reject in one transaction through `ledger/events.py` (acknowledgement, shipment with every
  tracking number, invoice with its charges, a new emailed PO), the document linked, the person named, the model's
  answer and the accepted values both kept; a second Accept refused by `version`; a revised emailed PO shows its
  differences only. The PO's timeline shows each line's source document (REQ-DB-02).
  **Done 2026-11-12 (TC-ACC-01 to 06: 17 tests; the screen is checked in real Chrome by TC-ACC-01).** `extraction/accept.py`
  (`accept`: the ticks the flags need, each line matched again from the form so a typed PO line number assigns it, a
  "Leave out" box per line, then the entry through `ledger/events.py`, whose `record_ack`, `record_shipment` and
  `record_invoice` gained `source_document_id`, every tracking number and the invoice's charges; the extraction keeps
  `answer` as read and `final` as accepted, and the audit names the changed fields, never their values). `POST
  /review/{id}/accept` takes the form as it stands; the page's Accept button is live. An emailed PO whose number is new
  is created with `source = document`, USD unless changed; one that exists lists its differences (quantity, unit, price,
  date, lines added or missing) and cannot be accepted. The PO's timeline links each accepted line to its message.
  **Choices:** a discount charge is typed with a minus sign; an acknowledgement that lists no lines is accepted as
  "acknowledged, no details"; Reject was already built in PD-4.4. **Also:** the `.tuples()` deprecation warnings in the
  extraction code are gone.
- [x] PD-4.5a The QuickBooks load skips charge rows, Shipping and Freight by default and more in a setting, and the
  preview lists them (DEC-55). **Done 2026-11-12 (TC-QB-10, TC-QB-11).** `plan_load` skips a row whose item (the whole
  name, or the part before a colon, ignoring case) is in the `po_charge_items` setting (default `shipping,freight`;
  `Freight Brackets` is goods), lists it in the preview as "skipped: a charge, not goods", and counts it in the load's
  audit. A file of only charges has nothing to create and no error. There is still no screen to edit the setting; it is
  a row in the `setting` table.
- [ ] PD-4.6 Pipeline tests with a stub model; security tests with a stub that obeys planted instructions, proving the
  validator, the ticks and the person contain it (Opus).
- [~] PD-4.7 The golden set through the whole pipeline: each PDF, scan and spreadsheet inside an email from its vendor's
  domain (a "please see attached" body, which must land in No order facts); a database built from each case's
  `po_context`; T-1 to T-5 and seconds per document (NFR-11) on the 14b model (Opus). **Skipped by the owner,
  2026-11-12,** until the front end, backend and AI layer are finished (the deferred list at the top).

**Demo:** the golden mailbox flows to the review queue; accepting documents moves the lines through their statuses.

## Phase 5: Alerts and follow-up

*Thresholds: the defaults in DEC-22, which the owner will finalise later.*

- [ ] PD-5.0 Documentation pass (DEC-25).
- [ ] PD-5.1 The rules engine (§8), one test per rule and per clear condition.
- [ ] PD-5.2 Follow-ups screen: Contacted with a note, Snooze, Dismiss, reopen on change (REQ-FU-02).
- [ ] PD-5.3 Drafted follow-up message (REQ-FU-03), reviewed for tone and accuracy with the owner.
- [ ] PD-5.4 Dashboard strip of today's alerts; `scan_stale` and the health banner (NFR-05).
- [ ] PD-5.5 Browser tests with an accessibility check on the main screens (NFR-12).

**Demo:** a late line produces an alert; marking it contacted quiets it; a new acknowledgement with a later date
reopens it as a slip.

## Phase 6: The company's mail system, the office Mac, running for real

*Needs: OPEN-13(a) and OPEN-14, then the mailbox access from the company's admin; the mailbox is read whole, Inbox and Sent Items (DEC-30). Opus for the access check. This phase comes before the demo on real laptops.*

- [ ] PD-6.0 Documentation pass (DEC-25). Verify against the provider's current docs: how a shared mailbox is granted, and whether its sign-in accepts the office Mac's name as a return address. Neither is confirmed.
- [ ] PD-6.1 The company's mail provider (Microsoft Graph expected): access to the one shared mailbox (DEC-33), the token in the Keychain, folder delta, attachments.
  Verify against Microsoft's current docs how attachments are fetched with delta (ARCHITECTURE §3).
- [ ] PD-6.2 A check that the app can read each connected mailbox and is **refused** on any other (RISK-04), and has no
  write permission.
- [ ] PD-6.3 Re-run the golden set shape on real mail only after the owner confirms each real document (DEC-13).
- [ ] PD-6.3a Network access (DEC-28, NFR-14), **only once a second factor exists (DEC-34)**: HTTPS with a certificate for the office Mac's name, the app refuses to listen on the network without it, the firewall check, the one-time certificate step on a laptop, and a real sign-in from a second laptop on the real office network (RISK-12). **From the engineering review:** `ssl_certfile` and `ssl_keyfile` passed to Uvicorn from Settings, and Settings refuses a non-loopback host unless `https` and both files are set (the certificate key must be mode 600), so HTTPS cannot be "on" in name only; a per-IP limit on sign-in failures beside the per-account lockout (so one laptop cannot lock a colleague out, or guess across accounts); the `__Host-` prefix on the session cookie; Uvicorn's proxy headers stay off because there is no proxy, so a spoofed `X-Forwarded-For` is never trusted; a real-laptop check that the `.local` name resolves; a startup warning if FileVault is off (`fdesetup status`).
- [ ] PD-6.0a (DEC-72, OPEN-32) The production laptop: a start-up host check (memory, disk, model, Tesseract) shown on the health strip; the macOS-only parts (document sandbox, Homebrew paths, launchd, FileVault check, `setup.sh`) behind small interfaces; a move onto a different machine rehearsed from a backup. Written for another operating system only if OPEN-32 says so. A laptop sleeps, so sleep and lid settings and catching up after waking are checked in PD-6.4.
- [ ] PD-6.4 launchd service, restart on failure, start at login (DEC-12); stay-awake settings; what really happens after a power cut, and the Keychain prompt (RISK-10, RISK-11).
- [~] PD-6.5 Backups with a tested restore, 35-day pruning; migrations preceded by a backup (§11). **Pulled forward 2026-11-11, because stored originals exist from PD-3.2:** `po-dashboard backup` and `restore`, a safety copy before an upgrade, a refusal of a newer database, 20 tests that restore a backup and compare it (TC-OPS-01). Still to do here: the nightly schedule, a second copy on another disk, 35-day pruning only with the owner's yes, the runbook tested by following it.
- [ ] PD-6.6 24-hour soak test with a restart in the middle (T-6).
- [ ] PD-6.7 `OPERATIONS.md` filled with the real commands and tested by following it.
- [ ] PD-6.8 Provider sign-in as settled by OPEN-13(a) and DEC-32.

**Demo:** the office Mac starts the app at login and scans the real mailbox every 5 minutes. Someone on another laptop
signs in over the office network. It survives a restart, and the health page says so.

## Phase 7: Second machine, audit, release

- [ ] PD-7.0 Documentation pass (DEC-25).
- [ ] PD-7.1 Rehearse the install on a clean machine (or a fresh user account) from Git, following README only
  (NFR-07). Fix every gap.
- [ ] PD-7.2 The human test: the Sign-off tab of the test record (PD-1.13) with every row ready, followed by a person.
- [ ] PD-7.3 `scripts/release_gate.sh`: suites, golden set against the baseline, a repository scan for secrets and
  real documents.
- [ ] PD-7.4 Complete [AUDIT.md](AUDIT.md): every requirement Met, Partly or Not met, with its evidence.
- [ ] PD-7.5 Update BUILD_HISTORY and tag v1.0.

**Demo:** the friend installs from Git, loads the demo mailbox, then connects their own.

## After v1 (not planned yet)

More than one mailbox, with keyword rules for mailboxes that are not purchasing-only (DEC-33), automatic vendor email written by the AI layer, a person confirming only when there is no concrete reason to send (DEC-70, replacing DEC-10's "later"), Windows or Linux service (DEC-12),
a malware scan, auto-accept for very high-confidence documents.

## Phase 8: Customer orders and invoice reconciliation

*The owner's request of 2026-11-12, built before Phase 4 resumes (DEC-56). A separate workflow: customer POs against
QuickBooks sales invoices. Design: ARCHITECTURE §16, DEC-56 to DEC-59, written on Opus. Sonnet for building, Opus for the
PD-8.4 rules review. Synthetic data only until the owner shows a real export (OPEN-22).*

- [x] PD-8.0 Documentation pass (DEC-25), 2026-11-12. The owner's four answers (the export is not known yet; customer POs
  come to the purchasing mailbox; everyone signed in sees it; "possibly unbilled" after 30 days); REQ-CO-01 to 16 and
  T-7; DEC-56 to DEC-59; OPEN-22 to OPEN-25 (the export, tolerances, Customer:Job names, where customers come from, each
  with a working assumption); ARCHITECTURE §16 and RISK-16, RISK-17. The owner gave the go-ahead on this plan, 2026-11-12.
- [x] PD-8.1 The tables (§16.1) in migration 0010, new tables only, and `db/customers.py`; the `setting` table and its
  defaults. **Done 2026-11-12 (TC-CO-01 to 03: 27 tests).** 14 new tables; a database with purchasing records upgrades with every purchasing table definition and row identical, and downgrades cleanly; `customers/config.py` reads the settings (one cent, exact quantities, 30 days, charge names, USD, Customer:Job) with a fallback to the default for a missing or damaged value. A unique "ignoring case" rule is carried by a stored key column, so the database itself refuses the second spelling. No purchasing module imports the customer side (a test).
- [x] PD-8.2 Customers (list, add, edit, QuickBooks names, domains) and customer POs: the intake service (§16.2), the form,
  the CSV and Excel import with its template and preview, revisions with Approve and Reject, and the waiting list for
  unknown customers. **Done 2026-11-12 (TC-CO-04 to 08: 74 tests, and the whole flow checked in real Chrome, 62 of 62).** `customers/` (`keys.py`, `service.py`, `intake.py`, `po_import.py`, `listing.py`) and `pages/customers.py`, `pages/customer_orders.py` with eight templates. Every customer PO goes through one `file_po`: created active (the person filing it approves it), skipped as a copy, the next revision pending, or waiting for a customer; nothing is guessed (an exact name or domain only). The import shows a preview and files all or nothing, holding the file in memory for 15 minutes. A Customer orders link for everyone signed in (DEC-56). The search box (`app.js`, `_picker.html`) now serves customers as well as vendors. Not here: reconciliation status on the list (PD-8.4), which for now shows Open, Closed or Cancelled and any revision waiting.
- [x] PD-8.3 The sales invoice import (§16.3, code in `quickbooks/importers/` and `quickbooks/snapshots/`, DEC-60): mapping profiles with the two presets, the preview with missing fields named, the period, observations (new, same, changed, conflicting), credit memos and charges, the upload history with the file kept, and "map to a customer or create one" for an unknown name. **Done 2026-11-12 (TC-CO-09 to 12: 54 tests, and the upload, mapping, keeping, invoice page, repeat and customer linking checked in real Chrome, 63 of 63).** Reader, snapshots and the `/sales-invoices` screens; synthetic samples in `tests/data/`. Behaviour choices that left room are DEC-61 (Assumed). Still synthetic only: the real export is OPEN-22. Not here: the matching and statuses (PD-8.4); resolving a changed invoice (accepting the new figures) comes with the review actions in PD-8.5.
- [x] PD-8.4 The matching engine and status (§16.4, §16.5, code in `quickbooks/reconciliation/`): order of evidence, tolerances, allocations by rule and by a
  person, the confirmed and preliminary levels, coverage and the 30-day rule, history. The owner's ten scenarios and their
  variants are each a test (T-7). Opus reviews the rules before they are ticked. **Done 2026-11-12 on Opus (TC-CO-13 to 15:
  71 tests; T-7 met for the ten scenarios and their variants).** The engine, the loader and run, a person's decisions; the
  pages re-run it at every change. The rules where DEC-59 left room are DEC-62 (Assumed). No screens yet: PD-8.5 shows
  the statuses, the exception queue and the decision buttons.
- [x] PD-8.5 The screens (§16.6, DEC-63): order list with status, figures, filters and CSV, order page with every action,
  exception queue, the invoice page with its review and placing forms; Chrome cases for each. **Done 2026-11-12 (TC-CO-16:
  automated; TC-CO-17: real Chrome).** `quickbooks/reconciliation/views.py` (reads only), the list, CSV, queue and order page in
  `pages/customer_orders.py`, the invoice page, `resolve` and `place` in `pages/sales_invoices.py`, `intake.set_state`
  (close, cancel, reopen) and `imports.resolve_invoice`. An upload now also keeps each line's item as written, so using its
  figures does not lower-case them.
- [x] PD-8.6 Security and regression: malformed and hostile uploads, the route sweep, an audit row for every action, the
  purchasing tests unchanged and green, the migration on a copy of a populated database. **Done 2026-11-12 (TC-CO-18: 16
  tests; the whole suite and ruff and mypy green).** Nine hostile files are refused by both upload pages; every changing route in
  the three sections is swept for session, token, Origin and no server error, and is either audited or only a preview (a new
  route fails the test until it is listed); each action driven for real leaves its audit row naming the person; a populated
  database upgrades again, goes back and comes forward with purchasing rows identical. No defect found.
- [x] PD-8.7 End to end on synthetic records (customers, POs, two uploads, every scenario), then AUDIT.md, the README,
  BUILD_HISTORY and the completion report. **Done 2026-11-12 (TC-CO-19; the whole suite, ruff and mypy green).** One test
  drives only the screens: a customer, nine POs by file, two uploads on different days, every T-7 scenario, then a person
  settles what waited. It found DEF-017 (the CSV showed 25.000000), fixed. AUDIT.md: REQ-CO-01 to 14 Met, T-7 Met, both on
  synthetic records.
- [ ] PD-8.8 (after Phase 4) Customer POs from email (§16.7): the `customer_purchase_order` type, a new prompt version,
  golden-set cases and a re-score, the customer found by sender domain, Accept through the intake service.

**Demo:** upload a synthetic QuickBooks export; the order list shows matched, partial, over-invoiced, not found and needs
review, each linked to its evidence; a person settles an ambiguous match and the audit trail shows it.

## Phase 9: Assistant: questions about the records (proposed)

*The owner's brief of 2026-11-12. Design: ARCHITECTURE §17; DEC-64 to DEC-69 (all Proposed); OPEN-26 to OPEN-31.
**Nothing is built until the owner gives the go-ahead and places this phase in the order (OPEN-26).** Opus for PD-9.0 and
the evaluation design in PD-9.6; Sonnet for PD-9.1 to PD-9.5. Stage 1 is standalone: no page, no route, no live
database, and a stub model in every test.*

- [~] PD-9.0 Documentation pass (DEC-25). **Started 2026-11-12:** the impact assessment and the design (ARCHITECTURE
  §17), DEC-64 to DEC-69 proposed, OPEN-26 to OPEN-31 asked, RISK-18 to RISK-21. **Waiting for:** the owner's answers
  and go-ahead; then the REQ-AS requirements, the README status table and the TC-AS cases in the test record.

**Stage 1: the foundation, standalone.**

- [ ] PD-9.1 Contracts and the read interface (§17.3): `assistant/contracts.py`, and `assistant/backend.py` with the
  synthetic adapter and its made-up fixtures in `tests/data/assistant/` (DEC-13). Adds `langgraph` if OPEN-27 says so.
- [ ] PD-9.2 The gateway, done with PD-4.1: `models/llm.chat_json` for any schema, the one-call-at-a-time lock with
  questions first (DEC-68), seconds and token counts kept; the test that only `models/llm.py` reaches the model server
  covers the assistant.
- [ ] PD-9.3 Classification (DEC-66): the rules, the model step, the identifier check, and
  `assistant/prompts/intent_classification.md` (versioned).
- [ ] PD-9.4 Rewriting (DEC-67): the reference check, substitution, the model step and its checks, and
  `assistant/prompts/query_rewriting.md`.
- [ ] PD-9.5 The graph (DEC-64, DEC-65): reference check, rewrite, classify, route, read (synthetic adapter), evidence
  check, then a template answer with sources or "insufficient information"; the tracing guard;
  `po-dashboard assistant ask "<question>" --synthetic`.
- [ ] PD-9.6 Tests and the evaluation set. With a stub model, each case in the brief: a direct question, a summary, a
  draft, an ambiguous question, a compound question, unrelated history, identifiers kept exactly, no needless rewrite,
  missing and conflicting evidence, instructions planted in a question and in a record, a buyer asking for admin-only
  data, no write reaching the database, no mail code, no outside connection. About 80 synthetic questions with their
  expected result in `evals/assistant/`, and a scoring script. One timed run on the 14b model, only with the owner's yes
  (OPEN-28).

**Stage 2: the workflows. Designed in §17.6 to §17.8; built only after the owner has reviewed Stage 1.**

- [ ] PD-9.7 Design review with the owner: ASK, SUMMARIZE and DRAFT on the live read functions; OPEN-29 and OPEN-31;
  the retrieval and model trial (§17.7), with each download's size stated first. The tasks after PD-9.7 are written
  then.

**Demo (Stage 1):** in Terminal, `po-dashboard assistant ask` classifies and routes the brief's examples against synthetic
records, answers with sources or "insufficient information", refuses "delete all outstanding invoices", and asks "which
PO?" when the question cannot tell.
