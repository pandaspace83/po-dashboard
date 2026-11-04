# PO Dashboard: Plan and To-Do

> **Status:** Plan approved 2026-11-04. **Phase 0 built locally** (0.1 first commit and 0.5 CI still open).
> **Sources:** [REQUIREMENTS.md](REQUIREMENTS.md), [ARCHITECTURE.md](ARCHITECTURE.md), [DECISIONS.md](DECISIONS.md).

**How this file works.** Each task has an ID (`PD-3.2`) and a checkbox. Phases run in order and each ends with a demo
or a test run you can check. Nothing is built until you approve the plan; after that I tick tasks as they are done and
update [AUDIT.md](AUDIT.md) at the end of each phase. A phase that needs an answer from [DECISIONS.md](DECISIONS.md)
§2 says which.

**Models.** Design and scoring work (Phase 2 scoring design, Phase 6 security check, the final audit) suits Opus.
The rest is following this plan, which suits Sonnet. I will say before each switch.

## Phase 0: Skeleton

*Needs: nothing open. macOS only (DEC-18); the repository is public (DEC-19), so the licence (OPEN-03) is needed before the first push.*

- [ ] PD-0.1 Create the Git repository locally (not pushed); first commit contains these docs.
- [x] PD-0.2 `pyproject.toml` with uv, Ruff, pytest; the folder layout from ARCHITECTURE §14.
- [x] PD-0.3 `scripts/setup.sh` for macOS: Python, dependencies, checks for Ollama and Tesseract, states the 9 GB
  model size and asks before pulling it.
- [x] PD-0.4 A page that starts and shows "PO Dashboard" and the health strip.
- [ ] PD-0.5 CI on GitHub Actions: Ruff and the fast tests on every push (once the repo is on GitHub).
- [x] PD-0.6 A pre-commit check that refuses `.eml`, PDFs, databases and `.env` outside `tests/data/` (RISK-07).

**Demo:** `scripts/setup.sh && uv run po-dashboard serve` opens an empty app at http://127.0.0.1:8000.

## Phase 1: Ledger, status engine and dashboard (no mailbox, no AI)

*Needs: OPEN-08 answered (DEC-24: several currencies, decimal quantities), OPEN-10 (how others reach the app; does not block the login).*

- [ ] PD-1.1 Tables and migrations for ARCHITECTURE §4.
- [ ] PD-1.2 Vendors screen; PO entry by hand; CSV import (REQ-PO-01, -02, -04).
- [ ] PD-1.3 The status engine (§5) with unit tests for every row of the table, including short confirmation and
  partial invoice.
- [ ] PD-1.4 Manual acknowledgement, shipment and invoice entry, so the whole life cycle works before any AI.
- [ ] PD-1.5 PO list, PO detail with the line timeline, close-by-hand actions with reasons (REQ-INV-04).
- [ ] PD-1.6 Dashboard counts, open-items report, CSV export (REQ-DB-01, -03).
- [ ] PD-1.7 Audit trail on every change (NFR-03) and its screen (admins).
- [ ] PD-1.9 Sign-in (NFR-13, DEC-20): users and roles, argon2id, sessions with idle timeout, lockout, CSRF, TOTP, a first-admin setup step, an admin screen to add and disable people. Built before the other screens so every page is behind it. Security tests: every page and API refuses an unauthenticated request; a buyer cannot reach admin screens.
- [ ] PD-1.8 The visual design pass: tokens, light and dark, status chips with text, phone width.

**Demo:** enter a PO, add a partial ack, a partial shipment and a partial invoice by hand; the line statuses, the
dashboard and the open-items report follow.

## Phase 2: Synthetic data and the golden set

*Opus for PD-2.1 (scoring design). Needs: OPEN-09 only when real samples arrive.*

- [ ] PD-2.1 Write what "correct" means field by field, and the scoring rules (exact, normalised date, quantity).
- [ ] PD-2.2 A generator that builds synthetic vendors, PO emails (body and PDF, sent to the vendor with the mailbox copied), acknowledgements (table, prose, German-style dates,
  short, no date, revised), shipment notices (partial, full, shipment-level date), invoices (partial, price
  difference, over-billed), scanned PDFs, spreadsheets, quoted threads, a duplicate, and documents with planted
  instructions. Each document gets an answer file.
- [ ] PD-2.3 The golden set committed under `tests/data/` (synthetic only) with a `GOLDEN_SET.md` that lists it.
- [ ] PD-2.4 `evals/` runner: scores a model against the golden set; writes a report. Copy in the 2026-11-04
  trial script as the starting point.
- [ ] PD-2.5 Score `qwen2.5:14b-instruct` (and the 7b and 3b for the record); set `golden_baseline.json`.

**Demo:** one command prints the score table against T-1 to T-5.

## Phase 3: Mail sources and intake

*Needs: OPEN-04 (Gmail method) and a dedicated Gmail test account.*

- [ ] PD-3.1 `MailSource` interface and the folder provider; contract tests every provider must pass.
- [ ] PD-3.2 Intake: store originals, hashes, dedupe, message and document tables (REQ-IN-04, -05).
- [ ] PD-3.3 Text extraction: HTML body, text PDF, OCR, XLSX/CSV, DOCX, quoted-thread marking (REQ-IN-03).
- [ ] PD-3.4 The scanner loop with a saved position, one scan at a time, and the crash test (kill mid-scan, no loss,
  no duplicate).
- [ ] PD-3.5 The Gmail provider (read-only) and a test that proves it cannot write (REQ-IN-02).
- [ ] PD-3.6 Hostile-attachment tests: oversize, wrong type, a PDF bomb, a spreadsheet with formulas.

**Demo:** drop the golden `.eml` files in a folder, run the scanner, see every message and attachment stored once.
Then the same through the Gmail test account.

## Phase 4: Extraction, validation, review queue

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

*Needs: OPEN-07 (thresholds).*

- [ ] PD-5.1 The rules engine (§8), one test per rule and per clear condition.
- [ ] PD-5.2 Follow-ups screen: Contacted with a note, Snooze, Dismiss, reopen on change (REQ-FU-02).
- [ ] PD-5.3 Drafted follow-up message (REQ-FU-03), reviewed for tone and accuracy with the owner.
- [ ] PD-5.4 Dashboard strip of today's alerts; `scan_stale` and the health banner (NFR-05).
- [ ] PD-5.5 Browser tests with an accessibility check on the main screens (NFR-12).

**Demo:** a late line produces an alert; marking it contacted quiets it; a new acknowledgement with a later date
reopens it as a slip.

## Phase 6: Microsoft 365 and running for real

*Needs: the mailbox access from the owner's admin, OPEN-05 (which folder). Opus for the access check.*

- [ ] PD-6.1 Graph provider: app-only sign-in, secret or certificate in the Keychain, folder delta, attachments.
  Verify against Microsoft's current docs how attachments are fetched with delta (ARCHITECTURE §3).
- [ ] PD-6.2 A check that the app can read the one mailbox and is **refused** on any other (RISK-04), and has no
  write permission.
- [ ] PD-6.3 Re-run the golden set shape on real mail only after the owner confirms each real document (DEC-13).
- [ ] PD-6.3a If the owner chose network access (OPEN-10): HTTPS with a local certificate, listen on the network, firewall check.
- [ ] PD-6.4 launchd service, restart on failure, start at login (DEC-12).
- [ ] PD-6.5 Backups with a tested restore, 35-day pruning; migrations preceded by a backup (§11).
- [ ] PD-6.6 24-hour soak test with a restart in the middle (T-6).
- [ ] PD-6.7 `OPERATIONS.md` filled with the real commands and tested by following it.

**Demo:** the app starts at login, scans the real mailbox every 5 minutes, survives a restart and a sleep, and the
health page says so.

## Phase 7: Second machine, audit, release

- [ ] PD-7.1 Rehearse the install on a clean machine (or a fresh user account) from Git, following README only
  (NFR-07). Fix every gap.
- [ ] PD-7.2 `test-evidence/HUMAN_TEST_CHECKLIST.md` for a person to follow, and run it.
- [ ] PD-7.3 `scripts/release_gate.sh`: suites, golden set against the baseline, a repository scan for secrets and
  real documents.
- [ ] PD-7.4 Complete [AUDIT.md](AUDIT.md): every requirement Met, Partly or Not met, with its evidence.
- [ ] PD-7.5 Update BUILD_HISTORY and tag v1.0.

**Demo:** the friend installs from Git, loads the demo mailbox, then connects their own.

## After v1 (not planned yet)

Automatic vendor email (DEC-10), more than one person per install (OPEN-06), Windows or Linux service (DEC-12),
a malware scan, auto-accept for very high-confidence documents.
