# PO Dashboard audit: requirements against what was built

**Date:** 2026-11-04, updated 2026-11-12 (the documentation pass before Phase 4) · **Scope:** every requirement in [REQUIREMENTS.md](REQUIREMENTS.md) ·
**How to read it:** *Met* means built and checked by the test named in the last column; *Partly* says what is
missing; *Not met* says why; *Not built* means planned, with the phase. Updated at the end of every phase and
completed in PD-7.4.

Evidence IDs: **TC-** is a case in the test record ([TEST_CASES.md](../test-evidence/TEST_CASES.md)), each naming its tests; **pytest** names a test file; **GS** is the golden set score (`GOLDEN_SET.md`); **E2E** is a browser
test; **HT** is a step in `test-evidence/HUMAN_TEST_CHECKLIST.md`; **SOAK** is the 24 hour run.

## 1. Summary

| | Count |
|---|---|
| Requirements (49 REQ and 14 NFR) | 63 |
| Met | 12 |
| Partly met | 22 |
| Not met | 0 |
| Not built or not measured yet | 29 (including REQ-FU-04 and REQ-CO-16, which are Later) |
| Targets (T-1 to T-7) met | 0 of 7 |

Updated at the documentation pass before Phase 4 (PD-4.0, 2026-11-12): Phases 0 to 3 are built (PD-3.7 waits for
the owner's real-Google check). It had not been updated since Phase 0, so this brings it in line. 12 requirements are
met; most of the rest are partly met, waiting for documents to be read (Phase 4), alerts (Phase 5) or the office Mac
(Phase 6). Evidence is the test record's case ids ([TEST_CASES.md](../test-evidence/TEST_CASES.md)). No target is
measured yet: the owner skipped the full Phase 2 score, and the first one is PD-4.7.

## 2. Functional requirements

| ID | Requirement (short) | Status | What was built | Evidence |
|---|---|---|---|---|
| REQ-IN-01 | Scheduled scan, catches up | Met | The scanner reads each connected mailbox every 5 minutes from its saved position, so it catches up after a stop; one scan at a time | TC-MAIL-11 to 14; TC-MAIL-19 (owner, real Gmail) |
| REQ-IN-02 | Mailbox read-only | Partly (Phase 6) | Gmail with the `gmail.readonly` scope and GET requests only; the folder reader; a test reads the whole app for any write call. The company's mail system comes in Phase 6 | TC-MAIL-01, TC-MAIL-16; TC-MAIL-19 (owner: Gmail unchanged) |
| REQ-IN-03 | Body and attachments: PDF, OCR, XLSX, CSV, DOCX | Met | Bodies (HTML too), text PDFs, OCR of scans and images, spreadsheets, CSV, Word, quoted earlier messages marked; each document read in a sandboxed process (DEC-53) | TC-MAIL-08 to 10, TC-MAIL-20 to 24 |
| REQ-IN-04 | Originals kept, facts link to source | Partly (Phase 4) | Every original message and attachment is kept unchanged under its hash. Linking each accepted fact to its document comes with Accept | TC-MAIL-05, TC-MAIL-07 |
| REQ-IN-05 | Skip repeats | Met | The same bytes are one message or one attachment, through any mailbox; a crash repeats nothing | TC-MAIL-05, TC-MAIL-11, TC-MAIL-12 |
| REQ-IN-06 | Interchangeable mail sources | Partly (Phase 6) | One `MailSource` contract that the folder and Gmail providers both pass. The company's provider comes in Phase 6 | TC-MAIL-02, TC-MAIL-15 |
| REQ-IN-07 | Connect, reconnect, disconnect a mailbox; truthful wording | Partly (PD-3.7, Phase 6) | The Mailboxes screen connects, reconnects and disconnects the Gmail mailbox, with true wording. Waiting: the owner's real-Google check; the company's provider's wording in Phase 6 | TC-MAIL-25 to 27; TC-MAIL-28 (owner, not yet run) |
| REQ-PO-01 | Create a PO by hand | Met | The New PO form: vendor, number, date sent, currency, lines with exact decimals | TC-PO-01 to 05 |
| REQ-PO-02 | CSV import | Met | CSV and Excel import, whole file or nothing, problems listed by row; and the QuickBooks starting load | TC-PIMP-01 to 06, TC-PO-06, TC-QB-01 to 08 |
| REQ-PO-03 | Read a PO from an emailed document | Not built (Phase 4) | | |
| REQ-PO-04 | Vendors and domains | Partly (Phase 4) | Vendors with contacts and email domains (one vendor per domain), by hand or by file. Matching a message by sender domain and PO number is Phase 4 | TC-VEN-01 to 08, TC-VIMP-01 to 05 |
| REQ-ACK-01 | Classify documents | Not built (Phase 4) | | |
| REQ-ACK-02 | Per-line confirmed qty, price, promised date | Not built (Phase 4) | Typed by hand already (confirmed quantity, price, promised date per line); read from documents in Phase 4 | TC-EVT-01 |
| REQ-ACK-03 | A person accepts before anything changes | Not built (Phase 4) | | |
| REQ-ACK-04 | Promised-date history, slips | Partly (Phase 4) | Promised-date history per line and a slip flag, for acknowledgements typed by hand. From documents in Phase 4 | TC-EVT-02, TC-EVT-10 |
| REQ-ACK-05 | Unmatched queue | Not built (Phase 4) | Groundwork: a domain belongs to one vendor, and a vendor with no domain is marked | TC-VEN-03, TC-VEN-06 |
| REQ-ACK-06 | Differences against the PO shown | Partly (Phase 4) | Price and quantity differences are shown beside each line for entries typed by hand; on the Review screen in Phase 4 | TC-EVT-01, TC-EVT-05 |
| REQ-SHP-01 | Carrier, tracking, lines, ETA, partial | Partly (Phase 4) | Shipments typed by hand: carrier, tracking, lines, quantities, partial. From documents in Phase 4 | TC-EVT-03 |
| REQ-SHP-02 | Shipment-level date applies to lines | Partly (Phase 4) | A shipment's arrival date applies to every line without its own, for shipments typed by hand | TC-EVT-03, TC-EVT-08 |
| REQ-INV-01 | Invoice number, date, line qty and price | Partly (Phase 4) | Invoices typed by hand: number, date, per-line quantity and price. From documents in Phase 4 | TC-EVT-04 |
| REQ-INV-02 | Partial invoice leaves lines open | Met | A line closes when invoiced reaches ordered; a partial invoice, or a short confirmation, leaves the rest open | TC-EVT-04, TC-EVT-11 |
| REQ-INV-03 | PO closes when all lines close | Met | A PO is closed when all its lines are | TC-EVT-11 |
| REQ-INV-04 | Received, short, cancelled by hand | Met | Received, short and cancelled by hand, each with a reason, and reopen | TC-EVT-09 |
| REQ-INV-05 | Over-billing flagged | Met | Billing more than ordered, or above the PO price, is flagged and still recorded. A document's invoice goes through the same ledger in Phase 4 | TC-EVT-05 |
| REQ-DB-01 | Dashboard counts | Partly (Phase 4) | Home: open POs, open lines, late, no acknowledgement, slipped, needing the PO updated, and a Needs attention list. Documents waiting for review and unmatched come with Phase 4 | TC-HOME-01, TC-HOME-02 |
| REQ-DB-02 | PO list and detail with line timeline | Partly (Phase 4) | PO list with filters, sorting and paging; PO detail with each line's timeline. Each line's source document comes with Phase 4 | TC-PO-07, TC-PO-08, TC-EVT-10 |
| REQ-DB-03 | Open-items report and CSV | Met | Open items with filters and a CSV whose cells cannot run as formulas | TC-HOME-03, TC-HOME-04 |
| REQ-DB-04 | Review queue | Not built (Phase 4) | | |
| REQ-DB-05 | Company-wide visibility | Met | A buyer sees and uses every PO and vendor; only People, Audit and Mailboxes are admin-only | TC-PO-09, TC-SIGN-06 |
| REQ-FU-01 | Rule-based in-app alerts | Not built (Phase 5) | | |
| REQ-FU-02 | Contacted, snooze, dismiss, reopen | Not built (Phase 5) | | |
| REQ-FU-03 | Drafted follow-up message | Not built (Phase 5) | | |
| REQ-FU-04 | Automatic vendor email | Later (DEC-10) | | |
| REQ-CO-01 | Customers and their QuickBooks names | Not built (Phase 8) | | |
| REQ-CO-02 | Customer PO by hand | Not built (Phase 8) | | |
| REQ-CO-03 | Customer PO import | Not built (Phase 8) | | |
| REQ-CO-04 | Revisions kept, approved by a person | Not built (Phase 8) | | |
| REQ-CO-05 | One intake service; unknown customer waits | Not built (Phase 8) | | |
| REQ-CO-06 | QuickBooks sales import with mapping, preview, history | Not built (Phase 8) | | |
| REQ-CO-07 | Repeated uploads safe | Not built (Phase 8) | | |
| REQ-CO-08 | Credit memos and charges apart | Not built (Phase 8) | | |
| REQ-CO-09 | Deterministic matching, ambiguity to a person | Not built (Phase 8) | | |
| REQ-CO-10 | Reconciliation status from evidence | Not built (Phase 8) | | |
| REQ-CO-11 | Exception queue, confirmed or preliminary | Not built (Phase 8) | | |
| REQ-CO-12 | Possibly unbilled after 30 days, when covered | Not built (Phase 8) | | |
| REQ-CO-13 | Overrides with a reason, audited | Not built (Phase 8) | | |
| REQ-CO-14 | Customer orders section and CSV | Not built (Phase 8) | | |
| REQ-CO-15 | Customer POs from email | Not built (PD-8.8, after Phase 4) | | |
| REQ-CO-16 | Payments and ageing | Later | | |

## 3. Non-functional requirements

| ID | Requirement (short) | Status | What was built | Evidence |
|---|---|---|---|---|
| NFR-01 | Local-first, only the mailbox fetch leaves | Partly (PD-7) | The app refuses other host names and a model server off this Mac; Gmail calls go to Google's fixed hosts only; the document reader has no network. A whole-app traffic check comes at release | TC-SET-03, TC-MAIL-16, TC-MAIL-23 |
| NFR-02 | Documents are data | Partly (Phase 4) | Document text is fenced in the prompt; hostile files cost one document. The validator and the person's Accept come in Phase 4 | TC-AI-03, TC-MAIL-20 to 24 |
| NFR-03 | Audit trail | Met | Every change writes who, when and from where; an admin-only, read-only Audit page. Accept, reject and snooze join it in Phases 4 and 5 | TC-AUD-01, TC-AUD-02, TC-EVT-06, TC-EVT-10 |
| NFR-04 | Runs unattended, recovers | Partly (Phase 6) | The scanner keeps its place and recovers from a crash; running as a service that restarts itself is PD-6.4 | TC-MAIL-12, TC-MAIL-13 |
| NFR-05 | Health page, stale-scan alert | Partly (Phase 5) | Home's Last scan tile and the model check; the scan error per mailbox on the Mailboxes screen. The stale-scan banner comes in Phase 5 | TC-MAIL-14, TC-MAIL-25, TC-OPS-03 |
| NFR-06 | Backup and tested restore | Partly (Phase 6) | `backup` and `restore` commands, with a restore that is checked to match; the nightly schedule is PD-6.5 | TC-OPS-01 |
| NFR-07 | Installs from Git on a clean machine | Not built (Phase 7) | The repository is on GitHub (2026-11-12); the clean-machine rehearsal is PD-7 |  |
| NFR-08 | No secrets or real documents in Git | Partly (Phase 7) | `.gitignore`, the pre-commit guard, a CI check, and a review of every file and version before the first push (2026-11-12). The repository scan is PD-7.3 | TC-SET-04, TC-MAIL-18 |
| NFR-09 | 127.0.0.1 by default; HTTPS on the network | Partly (Phase 6) | A non-loopback address is refused at start; HSTS only when HTTPS is on. Network access with HTTPS is PD-6.3a | TC-SET-03, TC-OPS-05 |
| NFR-10 | Model is a setting, one gateway | Partly (Phase 4) | The model name is a setting and `models/llm.py` makes every model call. A test that enforces the single gateway is still to come | TC-AI-03, TC-OPS-03 |
| NFR-11 | One document in 2 minutes or less | Not measured (PD-4.7) | The owner skipped the Phase 2 timing (PD-2.5); 3 documents on the 7b model took 23 to 106 seconds |  |
| NFR-12 | Keyboard use, no accessibility violations | Partly (Phase 5) | Every screen works with the keyboard and at phone width; the automated accessibility check is PD-5.5 | TC-WEB-02, TC-WEB-03 |
| NFR-13 | Sign-in, roles, lockout, CSRF, second factor | Partly (Phase 6) | Passwords (argon2id), roles, lockout, idle sign-out, CSRF and Origin checks, security headers, all browser-checked. The second factor is paused (DEC-34, OPEN-13) | TC-SIGN-01 to 11, TC-SEC-01, TC-SEC-02, TC-SEC-05 |
| NFR-14 | Reached from laptops on the office network | Not built (Phase 6) | | |

## 4. Targets

| ID | Target | Status | Evidence |
|---|---|---|---|
| T-1 | PO number correct on 100% | Not measured (PD-4.7) | The owner skipped the full Phase 2 run (PD-2.5, 2026-11-10). A 3-document check on the 7b model: 3 of 3. The first full score is PD-4.7 |
| T-2 | Document type correct on at least 98% | Not measured (PD-4.7) | 3 of 3 in the 3-document check; first full score PD-4.7 |
| T-3 | Line fields correct on at least 95% | Not measured (PD-4.7) | 7 of 7 lines in the 3-document check; the 4-document trial scored 34/36 fields on the 14b model |
| T-4 | Planted instructions followed: 0 | Not measured (PD-4.7) | 0 in the 3-document check; the trial: 0 on the 14b model, 1 of 1 on the others |
| T-5 | Wrong-PO matches the validator missed | Not measured (PD-4.7) | The validator is PD-4.2 |
| T-6 | 24 hour run, no loss or duplicate | Not measured (Phase 6) | |
| T-7 | Reconciliation set: every case right | Not measured (PD-8.4) | |
