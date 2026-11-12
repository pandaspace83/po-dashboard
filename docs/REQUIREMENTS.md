# PO Dashboard: Requirements

2026-11-04 · Shashank Panda · **Status: v0.4, 2026-11-12: customer orders and sales invoices added (Phase 8)**

The requirements the build is audited against ([AUDIT.md](AUDIT.md)). DEC and OPEN IDs refer to
[DECISIONS.md](DECISIONS.md). Priority: **Must** (the app is not usable without it), **Should** (planned in v1),
**Later** (agreed as a direction, not in v1).

## 1. What the app is for

A purchasing person sends purchase orders (POs) to vendors. Vendors answer by email: an order acknowledgement with
promised dates (which can differ line by line), then shipment notices with tracking numbers (partial or full), then
invoices. The app watches the company's purchasing mailbox or mailboxes, reads those emails and their attachments, keeps one record per PO line from
"sent" to "closed", and tells the buyer which lines need a follow-up with the vendor.

**A second workflow, from Phase 8 (DEC-56).** Customers also send purchase orders to RaceSpec, and RaceSpec invoices
them in QuickBooks Desktop. The app keeps those customer POs, takes the sales invoices from a QuickBooks export a person
uploads, and reports where the two disagree: an order with no invoice yet, a partial or excess invoice, a wrong price,
a duplicate invoice. This is separate from purchasing: its own records and screens, sharing only sign-in, the audit
trail, uploads and the number types. Purchasing (the rest of this document) is unchanged by it.

Everything runs on one always-on Mac in the office, and people use it from a browser on their own laptops (DEC-28).
Everyone signed in sees the same company-wide data (DEC-29). Another team can install its own copy from Git.

## 2. The life of a PO line

```
Sent -> Acknowledged -> Partially shipped -> Shipped -> Invoiced = Closed
            |                                              ^
            +-- date slips, short confirmations ----------- + manual: received, closed short, cancelled
```

A PO is closed when every line is closed. A partial invoice closes only the lines it covers; the rest stay open and
appear in the open-items report.

## 3. Functional requirements

### Intake (IN)

| ID | Requirement | Priority |
|---|---|---|
| REQ-IN-01 | Scan each connected mailbox on a schedule (default every 5 minutes, configurable). After downtime or sleep it catches up without missing or duplicating a message. | Must |
| REQ-IN-02 | Mailbox access is read-only. The app never sends, moves, flags or deletes mail (DEC-07). | Must |
| REQ-IN-03 | Read the email body and attachments: text PDF, scanned PDF or image (OCR), XLSX, CSV, DOCX. | Must |
| REQ-IN-04 | Keep every original message and attachment unchanged, and link each extracted fact back to its source and page or cell. | Must |
| REQ-IN-05 | Skip a message or attachment already processed (same message id or same content hash). | Must |
| REQ-IN-06 | Mail sources are interchangeable: Gmail (the build and the demo), the company's system at the switch, expected to be Microsoft 365 (DEC-31), and a folder of `.eml` files (tests, demo) (DEC-06). | Must |
| REQ-IN-07 | A mailbox is connected on the provider's own page where it offers one, can be reconnected and disconnected, and shows its own state. One or more mailboxes can be connected. What the page tells the person is true (DEC-30). | Must |

### Purchase orders (PO)

| ID | Requirement | Priority |
|---|---|---|
| REQ-PO-01 | Create a PO by hand: vendor, PO number, date sent, currency, lines (item, description, quantity, unit, price, requested date). Quantities can be decimal, and each PO has its own currency (DEC-24). | Must |
| REQ-PO-02 | Import POs from a CSV file. | Must |
| REQ-PO-03 | Read a PO from an email sent from or copied to a connected mailbox, body or PDF (the same extractor as other documents), accepted by a person like any other document. The vendor is found from the recipient address (DEC-17). | Must |
| REQ-PO-04 | Vendors have a name, the email domains they write from and a contact; a message is matched to a vendor by sender domain and PO number. | Must |

### Acknowledgements (ACK)

| ID | Requirement | Priority |
|---|---|---|
| REQ-ACK-01 | Classify each document as purchase order, acknowledgement, shipment notice, invoice or other. | Must |
| REQ-ACK-02 | From an acknowledgement, per line: confirmed quantity, confirmed price, promised date. Dates may differ by line; a line may be confirmed short or with no date. | Must |
| REQ-ACK-03 | Nothing changes a PO until a person accepts the extracted document. The person can correct any field first, or reject it (DEC-08). | Must |
| REQ-ACK-04 | Keep the history of every promised date per line, and flag a line whose date moved later (a slip). | Must |
| REQ-ACK-05 | A document that matches no PO or no vendor goes to an "Unmatched" queue, never guessed (DEC-09). | Must |
| REQ-ACK-06 | Show price and quantity differences against the PO next to each extracted line. | Must |

### Shipments (SHP)

| ID | Requirement | Priority |
|---|---|---|
| REQ-SHP-01 | From a shipment notice: carrier, tracking number(s), which lines and quantities, expected arrival. One notice can cover part of a PO; one PO can have many shipments. | Must |
| REQ-SHP-02 | A shipment-level arrival date applies to every line in it unless a line has its own. | Must |

### Invoices (INV)

| ID | Requirement | Priority |
|---|---|---|
| REQ-INV-01 | From an invoice: invoice number, date, per PO line quantity and price. | Must |
| REQ-INV-02 | A line closes when invoiced quantity reaches ordered quantity. A partial invoice leaves the other lines, and the unmet part of a line, open. A line confirmed short or at zero never closes by itself: the purchaser is alerted that the original PO needs updating, and a person closes the line by hand (DEC-36). | Must |
| REQ-INV-03 | A PO closes when all its lines are closed. | Must |
| REQ-INV-04 | Manual actions on a line, each with a reason: mark received, close short, cancel. | Must |
| REQ-INV-05 | Flag an invoice that bills more quantity or a higher price than the PO. | Should |
| REQ-INV-06 | Read charges that are not goods (freight, tariff, tax, other) apart from the lines, as printed; check that the lines plus the charges equal the total; never work out a tax or tariff rate (DEC-48). | Must |

### Dashboard and reports (DB)

| ID | Requirement | Priority |
|---|---|---|
| REQ-DB-01 | Dashboard: open POs, open lines, overdue lines, lines without an acknowledgement, date slips, documents waiting for review, unmatched documents. | Must |
| REQ-DB-02 | PO list with search and filters (vendor, status, date); PO detail with a line-by-line timeline of ordered, confirmed, shipped, invoiced and each source document. | Must |
| REQ-DB-03 | Open-items report: every open line with vendor, item, open quantity, promised date, days late, last vendor contact; export to CSV. | Must |
| REQ-DB-04 | Review queue: each incoming document shown beside its extracted fields; accept, edit or reject. | Must |
| REQ-DB-05 | Every signed-in person sees every PO, vendor, document and alert (DEC-29). | Must |

### Follow-up (FU)

| ID | Requirement | Priority |
|---|---|---|
| REQ-FU-01 | Rules (see ARCHITECTURE §8) raise in-app alerts on lines that need a follow-up. An alert clears itself when its condition clears. | Must |
| REQ-FU-02 | Alert actions: mark contacted (with a note), snooze until a date, dismiss. A snoozed or dismissed alert returns if the line's state changes. | Must |
| REQ-FU-03 | A drafted follow-up message per alert, for the buyer to copy. The app does not send it. | Should |
| REQ-FU-04 | Automatic email to vendors. | Later (DEC-10) |

### Customer orders and sales invoices (CO, Phase 8, DEC-56 to DEC-59)

| ID | Requirement | Priority |
|---|---|---|
| REQ-CO-01 | Customers: a name, the names QuickBooks uses for them in exports, email domains, a contact, active or not. Created and edited by hand; an export's name matches a customer only exactly, never because it looks similar. | Must |
| REQ-CO-02 | Customer POs entered by hand: customer, the customer's PO number, PO date, currency, the total as stated (optional), and lines (line number, item, description, quantity, unit, unit price, the line amount as stated, optional). A PO number is unique for its customer, not across customers. Exact decimals, never rounded. | Must |
| REQ-CO-03 | Customer POs imported from CSV or Excel, previewed before anything is made. A PO for a customer the app does not know waits for that customer; one already in the app with the same terms is skipped; one with different terms becomes a revision for review. | Must |
| REQ-CO-04 | A revised customer PO keeps the original and every revision. A revision changes nothing until a person approves it. | Must |
| REQ-CO-05 | One intake service takes structured customer PO data from any source (the form, a file, and later the email reader) with the same checks. A PO whose customer cannot be identified waits for a person. | Must |
| REQ-CO-06 | QuickBooks Desktop sales invoice exports (CSV or Excel) are uploaded with a saved column mapping, previewed with every missing required field named, and imported with the period they cover. Each upload, its file and every source row are kept, with who and when. | Must |
| REQ-CO-07 | Uploading again is safe: an invoice already known is never counted twice. An invoice that changed since an earlier upload, or two different invoices with one number, wait for a person and never overwrite what was accepted. | Must |
| REQ-CO-08 | Invoices are told from credit memos, and goods from charges (freight, tax, discounts, other). A credit memo or a charge never changes billed quantities by itself. | Must |
| REQ-CO-09 | Invoices are matched to customer POs and their lines by plain code, in a fixed order of evidence (customer, QuickBooks reference, PO reference, item, quantity, price, currency), never by a similar name or an equal amount alone. An ambiguous match waits for a person. | Must |
| REQ-CO-10 | Each customer PO shows a status worked out from the evidence: matched, matched by amount only, partially invoiced, over-invoiced, no invoice in the uploaded files, not covered by any uploaded file, needs review, closed or cancelled by hand. | Must |
| REQ-CO-11 | An exception queue lists every discrepancy with the evidence behind it, marked either a confirmed discrepancy or a preliminary alert, filtered by customer, type, status and date. | Must |
| REQ-CO-12 | A customer PO with no invoice is flagged "possibly unbilled" only when it is older than a set number of days (30 by default) and the uploaded files cover that period. An open order is never called overdue or unbilled otherwise. | Must |
| REQ-CO-13 | A person can confirm, override or reject a match, close or cancel a customer PO, and approve or reject a revision. An override needs a reason. Every one is in the audit trail with before and after. | Must |
| REQ-CO-14 | A Customer orders section (orders, exceptions, sales invoices with their import history, customers), apart from the purchasing screens, with a CSV export that cannot run formulas. | Must |
| REQ-CO-15 | Customer POs read from email in the purchasing mailbox, accepted by a person like other documents (after Phase 4). | Should |
| REQ-CO-16 | Payments received and ageing of what customers owe. | Later |

## 4. Non-functional requirements (NFR)

| ID | Requirement | Priority |
|---|---|---|
| NFR-01 | Local-first. Document text, extraction, the database and logs stay on the office Mac. The only traffic is the mailbox fetch, provider sign-in if chosen, and encrypted browser traffic on the office network (DEC-01, DEC-28). | Must |
| NFR-02 | Documents are data. Text inside an email or attachment can never act as an instruction, and extracted values are checked against the PO before a person sees them (DEC-09). | Must |
| NFR-03 | Audit trail: every accept, edit, reject, manual close, snooze, setting change, sign-in and failed sign-in is recorded with time and person, and can be listed by an admin. | Must |
| NFR-04 | Runs unattended as a background service once the office Mac's account is logged in, restarts itself after a crash, and recovers its place in the mailbox (DEC-12). | Must |
| NFR-05 | A health page and a banner show the last successful scan, the scan error if any, and whether the model is reachable. A scan that has not succeeded for 30 minutes raises an alert. | Must |
| NFR-06 | Backup and tested restore of the database and stored originals. | Must |
| NFR-07 | A new user installs from Git and reaches a working demo with the documented steps, on a clean machine, without help (DEC-11). | Must |
| NFR-08 | No secret, real vendor document or database is ever committed to Git (DEC-13). | Must |
| NFR-09 | The app listens on 127.0.0.1 by default, is turned on for the network on the office Mac (DEC-28), and refuses to listen on the network without HTTPS (DEC-20). | Must |
| NFR-13 | Sign-in: argon2id password hashes, roles (admin, buyer), session idle timeout, lockout after repeated failures, CSRF protection, and a second factor (TOTP, paused until Sign in with Microsoft is settled, DEC-34). An unauthenticated request reaches nothing but the sign-in page, its stylesheet and an up-or-down health check (DEC-20, DEC-26). | Must |
| NFR-14 | People reach the app from a browser on their own laptops, on the office network. A laptop installs nothing, and trusts the app's certificate once (DEC-28). | Must |
| NFR-10 | The extraction model is a setting, and every model call goes through one gateway module (DEC-05). | Must |
| NFR-11 | One document is processed in 2 minutes or less on the target machine. | Should |
| NFR-12 | The UI works with a keyboard and has no accessibility violations on its main screens (automated check). | Should |

## 5. Targets

Measured on the golden set of synthetic documents (PLAN Phase 2). They are targets until measured; the
2026-11-04 trial on four documents scored 34/36 fields (BUILD_HISTORY).

| ID | Target |
|---|---|
| T-1 | PO number correct on 100% of golden documents. A wrong PO is the worst error. |
| T-2 | Document type correct on at least 98%. |
| T-3 | Line number, quantity and promised date correct on at least 95% of lines. |
| T-4 | Planted instructions followed: 0. |
| T-5 | Documents that a person should reject but the validator did not flag: reported, with a goal of 0 for wrong-PO matches. |
| T-6 | No message missed or duplicated across a 24 hour run with a restart in the middle. |
| T-7 | Every case of the synthetic reconciliation set (DEC-59's ten scenarios and their variants) gets its expected status and exceptions: 100%. |
