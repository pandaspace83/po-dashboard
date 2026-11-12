# PO Dashboard

A local-first purchasing dashboard. It watches a company's purchasing mailbox for vendor replies to purchase orders
(acknowledgements, shipment notices, invoices), reads them with a model that runs on the same Mac, lets a person accept
each one, and tracks every PO line from sent to closed. It raises in-app alerts for lines that need a follow-up with the
vendor. It runs on one always-on Mac in the office and is used from a browser on people's own laptops; the only traffic
that leaves the office is the connection to the mailbox.

Python (FastAPI, SQLite), server-rendered pages with HTMX, and Ollama with `qwen2.5:14b-instruct`. macOS only for now.

> **Status: Phases 0 to 3 are built; Phase 4 (reading documents with the model) is in progress.** The table below is
> updated at the end of every phase, and the detail is in [docs/PLAN.md](docs/PLAN.md).

## What it is for

The company's own system (QuickBooks) holds the records. This app is an **early warning** on top of it. It catches what
a vendor said by email, shows which PO lines are late, short, slipped or unanswered, and tells the purchaser before they
would notice on their own. It is judged by whether it helps with that, not by whether it keeps records.

## Scope

**In version 1**
- Purchase orders and vendors, entered by hand, imported from CSV or Excel, or loaded from a QuickBooks "Open POs" report.
- One shared purchasing mailbox, read-only (Gmail for the build, the company's own system at go-live).
- Reading each email and attachment (PDF, scan, spreadsheet, Word) into acknowledgement, shipment and invoice facts.
- A review queue where a person accepts, edits or rejects every extracted document before it changes a PO.
- Line status computed from accepted facts; a dashboard, an open-items report and a CSV export.
- Alerts and drafted follow-up messages that a person sends. Sign-in with roles, an audit trail, and backup and restore.

**Not in version 1**
- Sending, moving or deleting mail: the app only ever reads (DEC-07). Automatic vendor email is a later decision.
- Cloud services of any kind: no cloud model, no cloud hosting, no telemetry (DEC-01).
- More than one mailbox, carrier look-ups, and automatic acceptance of what the model reads.

## Approach

- **Spec first.** Requirements, decisions, architecture and a phased plan are written and agreed before code, and each
  phase starts by bringing the documents in line with what really happened.
- **A person accepts everything.** The model is the best local option, not a certain one, so nothing it reads changes a
  PO until someone accepts it (DEC-08). It never guesses a PO.
- **Documents are data.** Email and attachment text is fenced in every prompt and never acts as an instruction (DEC-09);
  the reading of each file runs in a separate, sandboxed process with limits (DEC-53), because hostile files are expected.
- **Local only, and private.** Everything stays on the office Mac. Mailbox secrets live in the macOS Keychain. This
  repository holds code, documents and made-up test data only.
- **Measured, not assumed.** A golden set of about 100 made-up documents scores the model against targets, and a test
  record lists every test case with its evidence, including the defects found and how each was fixed.

## Where it stands

| Phase | What | State |
|---|---|---|
| 0 | The app shell, set-up script and commit guard | Done |
| 1 | Sign-in, purchase orders, vendors, imports, line status, dashboard, audit trail | Done |
| 2 | The golden set and the scoring code | Done (the full model score comes with Phase 4) |
| 3 | Mail sources, storing and reading documents, the scanner, Gmail, the Mailboxes screen | Built; the Gmail check with the real account is the owner's last step |
| 4 | Reading documents with the model, matching, validation, the review queue | In progress: design done, the queue and the extractor built |
| 5 | Alerts and follow-up | Not started |
| 6 | The company's mail system, the office Mac, running for real | Not started |
| 7 | A second machine, the final audit, release | Not started |

## The documents

| Document | What it is |
|---|---|
| [docs/REQUIREMENTS.md](docs/REQUIREMENTS.md) | What the app must do |
| [docs/DECISIONS.md](docs/DECISIONS.md) | Decisions made and questions still open |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | How it is designed |
| [docs/PLAN.md](docs/PLAN.md) | The build order and its status, task by task |
| [docs/SCORING.md](docs/SCORING.md) | How the model is scored |
| [docs/AUDIT.md](docs/AUDIT.md) | Each requirement against what was built |
| [docs/OPERATIONS.md](docs/OPERATIONS.md) | Running it day to day, and when it breaks |
| [docs/ENGINEERING_REVIEW.md](docs/ENGINEERING_REVIEW.md) | The review against an engineering checklist |
| [docs/BUILD_HISTORY.md](docs/BUILD_HISTORY.md) | How it was built, with dates |
| [test-evidence/](test-evidence/) | The test record: cases, defects, sign-off |

## Run it

macOS only for now. You need [uv](https://docs.astral.sh/uv/), [Ollama](https://ollama.com) and Tesseract (for scanned
documents).

```sh
scripts/setup.sh                                  # installs dependencies; states the 9 GB model size and asks before downloading
uv run po-dashboard user create-admin you@example.com   # the first person who can sign in, with a password prompt
uv run po-dashboard serve                         # http://127.0.0.1:8000
```

To check a change: `uv run ruff check . && uv run mypy && uv run pytest`. The same checks run on GitHub for every push.

This repository is public: it holds code, documents and synthetic test data only. It is published to be read, not reused:
see [LICENSE](LICENSE).
