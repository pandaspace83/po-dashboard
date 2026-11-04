# PO Dashboard: Decisions and open questions

2026-11-04 · Shashank Panda

DEC numbers are never renumbered. A reversed decision keeps its number and gets a new DEC that says so.
Status: **Decided** (the owner agreed), **Assumed** (a working default to confirm), **Open** (needs an answer).

## 1. Decisions

| ID | Date | Decision | Status | Why |
|---|---|---|---|---|
| DEC-01 | 2026-11-04 | **Local-first.** The app, database, stored files and extraction model run on the user's computer. The only network traffic is the mailbox fetch. No cloud hosting, no cloud AI. | Decided | The owner: "everything has to stay" local. Vendor prices and orders are commercial data. Same stance as LegalLens (its DEC-08). |
| DEC-02 | 2026-11-04 | **Python 3.13 managed by uv**, FastAPI, Pydantic v2, SQLAlchemy 2 with Alembic migrations. | Assumed | Python was the owner's requirement. 3.13 matches LegalLens; moving to 3.14 is a later, tested step. |
| DEC-03 | 2026-11-04 | **Front end: server-rendered pages (Jinja) with HTMX, our own CSS, no Node and no build step.** HTMX is copied into `static/`, with no CDN, so the app works offline. | Assumed | Looks and feels like a modern app, and a friend installs with one tool chain. Replaces my first suggestion of Tailwind, which needs a build step or a CDN. A React app like LegalLens's is the alternative if the UI later needs it. |
| DEC-04 | 2026-11-04 | **SQLite in WAL mode**, one file in a data folder outside the repo. Not encrypted in v1. | Assumed | One file to back up. Vendor data is less sensitive than client documents, so no SQLCipher; the data folder is per-user and git-ignored. Revisit if the friend's machine is shared. |
| DEC-05 | 2026-11-04 | **Extraction model: `qwen2.5:14b-instruct` through Ollama**, with a JSON-schema constrained answer, temperature 0. The model name is a setting; every call goes through `models/llm.py`. | Decided | The owner chose accuracy. On the 4-document trial it scored 34/36 against 32/36 (llama3.2:3b) and 30/36 (qwen2.5:7b), and was the only model that ignored planted instructions. Re-scored on the golden set in Phase 2. |
| DEC-06 | 2026-11-04 | **One `MailSource` interface with three providers:** Microsoft Graph (production), Gmail (testing), a folder of `.eml` files (tests and demo). | Decided | The owner's admin is setting up the Microsoft 365 mailbox; Gmail is the stand-in until then. The folder provider makes tests repeatable. |
| DEC-07 | 2026-11-04 | **Read-only mailbox access.** The Graph app gets `Mail.Read` only, limited to the one mailbox with Exchange Online RBAC for Applications. The code never calls send, move, update or delete. | Decided | Least privilege. Microsoft's docs warn that a broad `Mail.Read` grant must be removed or the limit does not apply. |
| DEC-08 | 2026-11-04 | **A person accepts every extracted document** before it changes a PO. Auto-accept is not in v1. | Decided | The owner's flow: "once the acknowledgement is accepted". The model is the best local option, not a certain one. |
| DEC-09 | 2026-11-04 | **Documents are data.** Email and attachment text is fenced in every prompt and never acts as an instruction. Extracted values are validated against the PO (PO exists, vendor matches sender, line exists, quantity and date plausible). A failure goes to the review queue flagged, or to Unmatched; the app never guesses a PO. | Decided | The trial showed two of three models follow instructions planted in a document. |
| DEC-10 | 2026-11-04 | **Alerts are in-app only for now.** Automatic vendor email is decided later. | Decided | The owner, 2026-11-04. |
| DEC-11 | 2026-11-04 | **Each person or team installs their own copy from Git.** The owner and the friend each run their own. | Decided | The owner. *Updated by DEC-20: the sign-in part of the first wording is reversed.* |
| DEC-12 | 2026-11-04 | **Runs as a background service** (macOS launchd, started at login, restarted on failure). The scanner is a loop inside the app process. Windows and Linux are added only if the friend needs them. | Assumed | "It runs locally even after we go live" means unattended, not a terminal left open. The mailbox's delta tokens let it catch up after sleep. |
| DEC-13 | 2026-11-04 | **Only synthetic test data goes into Git.** Real vendor documents, the database, `.env` and credentials are git-ignored. Real documents are used only after the owner confirms each one. | Decided | The owner's global rules and DEC-01. |
| DEC-14 | 2026-11-04 | **A PO closes when every line's invoiced quantity reaches its ordered quantity**, or the line is closed by hand (received, short, cancelled). | Assumed | The owner: the invoice arrives once the PO is completely received. The app has no goods-receipt feed. |
| DEC-15 | 2026-11-04 | **Docs follow the LegalLens set:** requirements, decisions, architecture, phased plan, audit against requirements, build history, test checklist, operations runbook, CLAUDE.md. They live in `docs/`. | Decided | The owner asked for the same documentation as LegalLens. |
| DEC-16 | 2026-11-04 | **Documents are Markdown in the repo** (the LegalLens "Claude Doc master" arrangement is not used). | Assumed | The friend reads them in Git. Say if you want a Claude Doc master copy too. |

| DEC-17 | 2026-11-04 | **POs arrive by email in the scanned mailbox** (the buyer sends or copies each PO there). The same extractor reads them as a `purchase_order` document type; a person accepts it like any other. The PO's vendor is found from the recipient address, since the sender is the buyer. Manual entry and CSV remain as a fallback. | Decided | The owner, 2026-11-04. Makes REQ-PO-03 a Must and adds a document type, so the golden set needs PO emails and PDFs too. |
| DEC-18 | 2026-11-04 | **macOS only for now.** | Decided | The owner's system; the friend's is probably macOS but unconfirmed. |
| DEC-19 | 2026-11-04 | **The GitHub repository is public.** Everything in it is readable by anyone, so only synthetic data, code and these docs go in; no real vendor names, prices, mailbox addresses or credentials, ever. | Decided | The owner. Raises the stakes of DEC-13 and RISK-07. |

| DEC-20 | 2026-11-04 | **Several people can use one installation, behind a login.** Each person has an account (email and password, hashed with argon2id), a role (**admin**: users and settings; **buyer**: everything else) and a session with idle timeout, lockout after repeated failures, and CSRF protection. Every audit entry names the person. A second factor (TOTP, as in LegalLens) is planned in the same phase. Reverses the "no sign-in" part of DEC-11 and changes NFR-09. | Decided | The owner, 2026-11-04: shared use, and "some sort of auth" so access to the machine is not enough. |
| DEC-21 | 2026-11-04 | **Scan the whole Inbox** of the mailbox. | Decided | The owner: only vendor mail arrives there (OPEN-05). Keep the validator: anything that is not a PO document is classed "other" and ignored. |
| DEC-22 | 2026-11-04 | **Alert defaults as in ARCHITECTURE §8** (2 business days, 1 day grace, 2 days, 3 days). All are settings; the owner will finalise them later. | Decided | The owner (OPEN-07). |
| DEC-23 | 2026-11-04 | **Licence: all rights reserved** (readable, not reusable), the same text as LegalLens. The repository is public. | Decided | The owner (OPEN-03). |

## 2. Open questions

| ID | Question | Needed by |
|---|---|---|
| OPEN-01 | ~~How do POs get into the app?~~ **Answered 2026-11-04:** POs are emailed to the mailbox (DEC-17). Manual entry and CSV stay as a fallback. | Closed |
| OPEN-02 | ~~The friend's computer.~~ **Answered 2026-11-04:** probably macOS, not confirmed; build for macOS (DEC-18). The 14b model needs about 9 GB of memory on top of the app, so 16 GB is the practical minimum. | Closed |
| OPEN-03 | ~~The Git licence.~~ **Answered 2026-11-04:** all rights reserved (DEC-23). | Closed |
| OPEN-04 | **Gmail test method:** IMAP with an app password on a dedicated test account, or the Gmail API with OAuth. I lean to IMAP (no Google Cloud project; the API's testing-mode tokens expire after about a week). To confirm at Phase 3. Use a dedicated test account, not a personal inbox. | Phase 3 |
| OPEN-05 | ~~Which mail folder is scanned.~~ **Answered 2026-11-04:** the whole Inbox (DEC-21). | Closed |
| OPEN-06 | ~~More than one person per install.~~ **Answered 2026-11-04:** yes, with a login (DEC-20). | Closed |
| OPEN-07 | ~~Alert thresholds.~~ **Defaults accepted 2026-11-04** (DEC-22); the owner will finalise later. | Closed |
| OPEN-08 | **Units, currencies, tax:** one currency, or several? Are partial-quantity units (metres, kilos) used? | Phase 1 |
| OPEN-10 | **How do the other people reach the shared installation?** (a) They sit at the same Mac and use its browser, or (b) they open it from their own computers over the office network. (b) needs the app to listen on the network, which requires HTTPS (a certificate made for the Mac) and a firewall check; the app stays on this machine's own address until you choose (b). | Phase 1 (login), Phase 6 (network) |
| OPEN-09 | **Real sample documents** from the owner, when available, to re-score the model and shape the fields. | Phase 2 and later |
