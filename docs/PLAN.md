# PO Dashboard: Plan and To-Do

> **Status, 2026-11-04:** Plan approved. **Phase 0 is built** except CI (PD-0.5), which needs a GitHub remote. **Phase 1
> is in progress:** sign-in, the ledger tables and the status engine are built; the PO screens are next. The deployment
> changed the same day (DEC-28 to DEC-32), and this plan includes it.
> **Sources:** [REQUIREMENTS.md](REQUIREMENTS.md), [ARCHITECTURE.md](ARCHITECTURE.md), [DECISIONS.md](DECISIONS.md).

**How this file works.** Each task has an ID (`PD-3.2`) and a checkbox. Phases run in order and each ends with a demo
or a test run you can check. Nothing is built until you approve the plan; after that I tick tasks as they are done and
update [AUDIT.md](AUDIT.md) at the end of each phase. A phase that needs an answer from [DECISIONS.md](DECISIONS.md)
§2 says which.

**Documentation first (DEC-25).** Every phase starts with a `PD-n.0` task, before any code: bring the docs in line with
what really happened, ask the owner every OPEN question the phase needs, write down any design it needs, then get the
go-ahead. A task is ticked only after it has been run for real, not just written.

**Models.** Design and scoring work (the Phase 1 sign-in design, Phase 2 scoring design, Phase 6 security check, the final audit) suits Opus.
The rest is following this plan, which suits Sonnet. I will say before each switch.

## Phase 0: Skeleton

*Needs: nothing open. macOS only (DEC-18); the repository is public (DEC-19) and the licence is chosen (DEC-23). No GitHub remote exists yet; pushing is the owner's call.*

- [x] PD-0.1 Create the Git repository locally (not pushed); first commit contains these docs.
- [x] PD-0.2 `pyproject.toml` with uv, Ruff, pytest; the folder layout from ARCHITECTURE §14.
- [x] PD-0.3 `scripts/setup.sh` for macOS: Python, dependencies, checks for Ollama and Tesseract, states the 9 GB
  model size and asks before pulling it.
- [x] PD-0.4 A page that starts and shows "PO Dashboard" and the health strip.
- [ ] PD-0.5 CI on GitHub Actions: Ruff and the fast tests on every push (once the repo is on GitHub).
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
- [ ] PD-1.5.0 Documentation pass (DEC-25): DEC-39 and OPEN-16 written 2026-11-05; DEC-39 written; DEC-40 and OPEN-17 (sending mail, watching tracking) are open and do not block PD-1.5. Waiting for the owner's go-ahead.
- [ ] PD-1.5 PO list and PO detail. **Slice a built 2026-11-05** (list filters with calendars for sent and expected dates, expected date per line with the shipment estimate taking over from the promise, late flags with days late, carrier tracking link; 23 new tests). **Slice b built 2026-11-05:** the Cancelled status, one "Settle lines by hand" form (any lines, one reason; received, closed short, cancelled), reopen with a reason, and a timeline for each line; 12 new tests. **Still to do:** the "Waiting to confirm" batch screen and receipt tables **Dropped 2026-11-05:** the owner does not mark anything received (DEC-39, DEC-43), so the "Waiting to confirm" screen and the receipt tables are not built. Delivery will be inferred from carrier emails (DEC-43, proposed). Original scope: (REQ-DB-02, REQ-INV-04, DEC-39). (a) List filters: status, vendor, date sent, expected date range with calendars. (b) Receiving as a **batch check-off** (DEC-39): one "Waiting to confirm" screen of shipments and invoices, one click for all fine, exceptions only (short quantity, notes or issues); new `receipt` tables; only the lines on a document change. (c) Carrier tracking link per shipment. (d) Line timeline. (e) Late and back-ordered flags, a **Cancelled** status, and the manual settle for the update-the-PO flag. *Started in PD-1.2:* a simple list with search and a PO page with each line's computed status. *Moved to later phases (DEC-40):* the draft chaser and its "draft ready" alert (Phase 5); delivery read from carrier emails (Phases 3 and 4). Until then PD-1.5 only stores the tracking number and shows the link. *Moved to Phases 3 and 4:* arrival and the update-the-PO flag from emails.

**Step 5: dashboard, audit, design.**

- [ ] PD-1.6 Dashboard counts, open-items report, CSV export (REQ-DB-01, -03). **Built and tested 2026-11-05** (DEC-42: home tiles that link to the report, an Open items page with a view and vendor filter, CSV download with formula-safe cells; 27 new tests). **Waiting for the owner's browser check:** steps 36 to 40 of [BROWSER_CHECK_PO_SCREENS.md](../test-evidence/BROWSER_CHECK_PO_SCREENS.md).
- [ ] PD-1.7 Audit trail on every change (NFR-03) and its screen (admins). Sign-in events are written from PD-1.9b. **Built and tested 2026-11-05:** an admin-only, read-only Audit page (filter by action, person, what it was about and dates; 50 a page; text never markup) and a guard test that every ledger change leaves a record; 7 new tests. **Waiting for the owner's browser check:** steps 41 to 44 of [BROWSER_CHECK_PO_SCREENS.md](../test-evidence/BROWSER_CHECK_PO_SCREENS.md).
- [x] PD-1.9e Name, look and feel (DEC-35): RaceSpec Internal Dashboard, light theme, inline icons, logo committed at the owner's say-so, the cursor ready in the first box of every form page. The sign-in and People pages are done.
- [ ] PD-1.10 *Proposed, waiting for OPEN-15:* reconcile with the ERP's export (DEC-41). A folder the app reads, a compare screen, and the update-the-PO flag clearing when the export matches. The export should carry, per PO line: **PO number; line number (or item number); vendor; item or part number; ordered quantity; received quantity; invoiced quantity; open quantity; line status (open, closed, cancelled); closed date; unit price; PO date; the promised or required date**. Nice to have: currency, invoice number, last receipt date, buyer. It must include **closed lines for the period**, not only open ones.
- [ ] PD-1.10a *Waiting for OPEN-19 (vendors) and the unit default:* the QuickBooks starting load and weekly check, as in DEC-41. Currency defaults to USD. POs whose vendor is not in the list wait; vendors are never created by this upload.
- [ ] PD-1.8 The visual design pass for the screens built after sign-in: tokens, status chips with text, phone width. The owner has more small tweaks to list.

**Demo:** enter a PO, add a partial ack, a partial shipment and a partial invoice by hand; the line statuses, the
dashboard and the open-items report follow.

## Phase 2: Synthetic data and the golden set

*Opus for PD-2.1 (scoring design). Needs: **OPEN-11 (tax), which the owner asked to be reminded of**; OPEN-09 only when real samples arrive.*

- [ ] PD-2.0 Documentation pass (DEC-25). **Ask the owner OPEN-11: do invoices carry tax the app must read, store or
  check?** The answer shapes PD-2.1, the golden invoices, and the invoice-total check in ARCHITECTURE §6.
- [ ] PD-2.1 Write what "correct" means field by field, and the scoring rules (exact, normalised date, quantity).
- [ ] PD-2.2 A generator that builds synthetic vendors, PO emails (body and PDF, sent from the mailbox, or sent to the vendor with the mailbox copied), acknowledgements (table, prose, German-style dates,
  short, no date, revised), shipment notices (partial, full, shipment-level date), invoices (partial, price
  difference, over-billed), scanned PDFs, spreadsheets, quoted threads, a duplicate, and documents with planted
  instructions. Each document gets an answer file.
- [ ] PD-2.3 The golden set committed under `tests/data/` (synthetic only) with a `GOLDEN_SET.md` that lists it.
- [ ] PD-2.4 `evals/` runner: scores a model against the golden set; writes a report. Copy in the 2026-11-04
  trial script as the starting point.
- [ ] PD-2.5 Score `qwen2.5:14b-instruct` (and the 7b and 3b for the record); set `golden_baseline.json`.

**Demo:** one command prints the score table against T-1 to T-5.

## Phase 3: Mail sources and intake

*Needs: OPEN-04 (Gmail method) and the dedicated Gmail test account from the owner (DEC-31). It holds only synthetic mail. Its address and password never go in the repo, a log or the chat: the owner enters them.*

- [ ] PD-3.0 Documentation pass (DEC-25).
- [ ] PD-3.1 `MailSource` interface, the `mailbox` table (one row in v1, more in v2, DEC-33; Inbox and Sent Items) and the folder provider; contract tests every provider must pass.
- [ ] PD-3.2 Intake: store originals, hashes, dedupe, message and document tables (REQ-IN-04, -05).
- [ ] PD-3.3 Text extraction: HTML body, text PDF, OCR, XLSX/CSV, DOCX, quoted-thread marking (REQ-IN-03).
- [ ] PD-3.4 The scanner loop with a saved position, one scan at a time, and the crash test (kill mid-scan, no loss,
  no duplicate).
- [ ] PD-3.5 The Gmail provider (read-only) and a test that proves it cannot write (REQ-IN-02).
- [ ] PD-3.6 Hostile-attachment tests: oversize, wrong type, a PDF bomb, a spreadsheet with formulas.
- [ ] PD-3.7 The Mailboxes screen: connect, reconnect, disconnect the one mailbox, with the wording in ARCHITECTURE §7 (REQ-IN-07).

**Demo:** drop the golden `.eml` files in a folder, run the scanner, see every message and attachment stored once.
Then the same through the Gmail test account.

## Phase 4: Extraction, validation, review queue

- [ ] PD-4.0 Documentation pass (DEC-25).
- [ ] PD-4.1 Model gateway `models/llm.py` and the versioned prompt and schema (ARCHITECTURE §6).
- [ ] PD-4.2 The validator (§6.5), each check with a test; the "value must appear in the source" check.
- [ ] PD-4.3 Matching a message to a vendor and PO; the Unmatched queue (REQ-ACK-05). For an emailed PO the vendor comes from the recipient address and a new PO is created on accept (REQ-PO-03, DEC-17).
- [ ] PD-4.4 The Review screen: source beside fields, differences against the PO, accept, edit, reject
  (REQ-ACK-03, -06, REQ-DB-04).
- [ ] PD-4.5 Accept writes ledger rows, history rows and audit in one transaction; promised-date history and slips
  (REQ-ACK-04).
- [ ] PD-4.6 Pipeline tests with a stub model; security tests with a stub that obeys planted instructions, proving
  the validator and queue contain it.
- [ ] PD-4.7 Re-run the golden set through the whole pipeline; record the score.

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
- [ ] PD-6.3a Network access (DEC-28, NFR-14), **only once a second factor exists (DEC-34)**: HTTPS with a certificate for the office Mac's name, the app refuses to listen on the network without it, the firewall check, the one-time certificate step on a laptop, and a real sign-in from a second laptop on the real office network (RISK-12).
- [ ] PD-6.4 launchd service, restart on failure, start at login (DEC-12); stay-awake settings; what really happens after a power cut, and the Keychain prompt (RISK-10, RISK-11).
- [ ] PD-6.5 Backups with a tested restore, 35-day pruning; migrations preceded by a backup (§11).
- [ ] PD-6.6 24-hour soak test with a restart in the middle (T-6).
- [ ] PD-6.7 `OPERATIONS.md` filled with the real commands and tested by following it.
- [ ] PD-6.8 Provider sign-in as settled by OPEN-13(a) and DEC-32.

**Demo:** the office Mac starts the app at login and scans the real mailbox every 5 minutes. Someone on another laptop
signs in over the office network. It survives a restart, and the health page says so.

## Phase 7: Second machine, audit, release

- [ ] PD-7.0 Documentation pass (DEC-25).
- [ ] PD-7.1 Rehearse the install on a clean machine (or a fresh user account) from Git, following README only
  (NFR-07). Fix every gap.
- [ ] PD-7.2 `test-evidence/HUMAN_TEST_CHECKLIST.md` for a person to follow, and run it.
- [ ] PD-7.3 `scripts/release_gate.sh`: suites, golden set against the baseline, a repository scan for secrets and
  real documents.
- [ ] PD-7.4 Complete [AUDIT.md](AUDIT.md): every requirement Met, Partly or Not met, with its evidence.
- [ ] PD-7.5 Update BUILD_HISTORY and tag v1.0.

**Demo:** the friend installs from Git, loads the demo mailbox, then connects their own.

## After v1 (not planned yet)

More than one mailbox, with keyword rules for mailboxes that are not purchasing-only (DEC-33), automatic vendor email (DEC-10), Windows or Linux service (DEC-12),
a malware scan, auto-accept for very high-confidence documents.
