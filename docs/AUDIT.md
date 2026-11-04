# PO Dashboard audit: requirements against what was built

**Date:** 2026-11-04 (updated at the documentation pass before Phase 1) · **Scope:** every requirement in [REQUIREMENTS.md](REQUIREMENTS.md) ·
**How to read it:** *Met* means built and checked by the test named in the last column; *Partly* says what is
missing; *Not met* says why; *Not built* means planned, with the phase. Updated at the end of every phase and
completed in PD-7.4.

Evidence IDs: **pytest** names a test file; **GS** is the golden set score (`GOLDEN_SET.md`); **E2E** is a browser
test; **HT** is a step in `test-evidence/HUMAN_TEST_CHECKLIST.md`; **SOAK** is the 24 hour run.

## 1. Summary

| | Count |
|---|---|
| Requirements (33 REQ and 14 NFR) | 47 |
| Met | 0 |
| Partly met | 5 (NFR-05, NFR-08, NFR-09, NFR-10, NFR-13) |
| Not met | 0 |
| Not built yet | 42 (including REQ-FU-04, which is Later) |
| Targets (T-1 to T-6) met | 0 of 6 |

Phase 0 (the app shell) is built, apart from CI. No requirement is fully met yet. The model trial of 2026-11-04
(BUILD_HISTORY) is the only evidence on extraction so far.

## 2. Functional requirements

| ID | Requirement (short) | Status | What was built | Evidence |
|---|---|---|---|---|
| REQ-IN-01 | Scheduled scan, catches up | Not built (Phase 3) | | |
| REQ-IN-02 | Mailbox read-only | Not built (Phase 3, 6) | | |
| REQ-IN-03 | Body and attachments: PDF, OCR, XLSX, CSV, DOCX | Not built (Phase 3) | | |
| REQ-IN-04 | Originals kept, facts link to source | Not built (Phase 3) | | |
| REQ-IN-05 | Skip repeats | Not built (Phase 3) | | |
| REQ-IN-06 | Interchangeable mail sources | Not built (Phase 3, 6) | | |
| REQ-IN-07 | Connect, reconnect, disconnect a mailbox; truthful wording | Not built (Phase 3, 6) | | |
| REQ-PO-01 | Create a PO by hand | Not built (Phase 1) | | |
| REQ-PO-02 | CSV import | Not built (Phase 1) | | |
| REQ-PO-03 | Read a PO from an emailed document | Not built (Phase 4) | | |
| REQ-PO-04 | Vendors and domains | Not built (Phase 1) | | |
| REQ-ACK-01 | Classify documents | Not built (Phase 4) | | |
| REQ-ACK-02 | Per-line confirmed qty, price, promised date | Not built (Phase 4) | | |
| REQ-ACK-03 | A person accepts before anything changes | Not built (Phase 4) | | |
| REQ-ACK-04 | Promised-date history, slips | Not built (Phase 4) | | |
| REQ-ACK-05 | Unmatched queue | Not built (Phase 4) | | |
| REQ-ACK-06 | Differences against the PO shown | Not built (Phase 4) | | |
| REQ-SHP-01 | Carrier, tracking, lines, ETA, partial | Not built (Phase 4) | | |
| REQ-SHP-02 | Shipment-level date applies to lines | Not built (Phase 4) | | |
| REQ-INV-01 | Invoice number, date, line qty and price | Not built (Phase 4) | | |
| REQ-INV-02 | Partial invoice leaves lines open | Not built (Phase 1) | | |
| REQ-INV-03 | PO closes when all lines close | Not built (Phase 1) | | |
| REQ-INV-04 | Received, short, cancelled by hand | Not built (Phase 1) | | |
| REQ-INV-05 | Over-billing flagged | Not built (Phase 4) | | |
| REQ-DB-01 | Dashboard counts | Not built (Phase 1, 5) | | |
| REQ-DB-02 | PO list and detail with line timeline | Not built (Phase 1) | | |
| REQ-DB-03 | Open-items report and CSV | Not built (Phase 1) | | |
| REQ-DB-04 | Review queue | Not built (Phase 4) | | |
| REQ-DB-05 | Company-wide visibility | Not built (Phase 1) | | |
| REQ-FU-01 | Rule-based in-app alerts | Not built (Phase 5) | | |
| REQ-FU-02 | Contacted, snooze, dismiss, reopen | Not built (Phase 5) | | |
| REQ-FU-03 | Drafted follow-up message | Not built (Phase 5) | | |
| REQ-FU-04 | Automatic vendor email | Later (DEC-10) | | |

## 3. Non-functional requirements

| ID | Requirement (short) | Status | What was built | Evidence |
|---|---|---|---|---|
| NFR-01 | Local-first, only the mailbox fetch leaves | Not built (all phases) | | |
| NFR-02 | Documents are data | Not built (Phase 4) | | |
| NFR-03 | Audit trail | Not built (Phase 1) | | |
| NFR-04 | Runs unattended, recovers | Not built (Phase 6) | | |
| NFR-05 | Health page, stale-scan alert | Partly (Phase 5) | Health strip with the model check. No scanner yet, so "last scan" is a placeholder, and no alert | pytest `test_web.py`, `test_llm.py` |
| NFR-06 | Backup and tested restore | Not built (Phase 6) | | |
| NFR-07 | Installs from Git on a clean machine | Not built (Phase 7) | | |
| NFR-08 | No secrets or real documents in Git | Partly (Phase 7) | `.gitignore` and the pre-commit guard. The repository scan comes in PD-7.3 | pytest `test_precommit_guard.py`, against real staged files |
| NFR-09 | 127.0.0.1 by default; HTTPS on the network | Partly (Phase 6) | A non-loopback address is refused at start. Network access with HTTPS is not built (PD-6.3a) | pytest `test_web.py` |
| NFR-10 | Model is a setting, one gateway | Partly (Phase 4) | The model name is a setting, and `models/llm.py` makes the only call so far. A test that enforces the single gateway is still to come | pytest `test_llm.py` |
| NFR-11 | One document in 2 minutes or less | Not built (Phase 4) | | |
| NFR-12 | Keyboard use, no accessibility violations | Not built (Phase 5) | | |
| NFR-13 | Sign-in, roles, lockout, CSRF, second factor | Partly (Phase 1) | Passwords, sessions, lockout, roles, audit, the sign-in and People pages, CSRF/Origin/Host checks and security headers are built. The second factor is paused (DEC-34); the by-hand browser check is PD-1.9d | pytest `test_auth.py`, `test_cli.py`, `test_gate.py` |
| NFR-14 | Reached from laptops on the office network | Not built (Phase 6) | | |

## 4. Targets

| ID | Target | Status | Evidence |
|---|---|---|---|
| T-1 | PO number correct on 100% | Not measured (Phase 2) | |
| T-2 | Document type correct on at least 98% | Not measured (Phase 2) | |
| T-3 | Line fields correct on at least 95% | Not measured (Phase 2) | The 4-document trial scored 34/36 fields (94%) on the 14b model |
| T-4 | Planted instructions followed: 0 | Not measured (Phase 2) | The trial: 0 on the 14b model, 1 of 1 on the others |
| T-5 | Wrong-PO matches the validator missed | Not measured (Phase 4) | |
| T-6 | 24 hour run, no loss or duplicate | Not measured (Phase 6) | |
