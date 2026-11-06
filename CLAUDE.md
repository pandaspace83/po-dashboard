# PO Dashboard

A local-first purchasing dashboard. It watches the company's purchasing mailbox or mailboxes for vendor replies to purchase orders (acknowledgements,
shipment notices, invoices), extracts them with a local model, lets a person accept them, tracks every PO line to
closed, and raises in-app alerts for lines that need a vendor follow-up. Python (FastAPI, SQLite), server-rendered
pages with HTMX, Ollama with `qwen2.5:14b-instruct`.

Requirements: [docs/REQUIREMENTS.md](docs/REQUIREMENTS.md). Decisions and open questions (`DEC-nn`, never
renumbered): [docs/DECISIONS.md](docs/DECISIONS.md). Design: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md). Build order
and status: [docs/PLAN.md](docs/PLAN.md). Audit: [docs/AUDIT.md](docs/AUDIT.md). Runbook: [docs/OPERATIONS.md](docs/OPERATIONS.md).

## Rules that always apply

- **Plan first.** For a new phase or a large change, update [docs/PLAN.md](docs/PLAN.md) and wait for the owner's go-ahead.
- **Documentation before every phase (DEC-25).** Before a phase's first code, do its `PD-n.0` task: bring the docs in
  line with what really happened, ask the owner every OPEN question that phase needs, and write down any design it needs.
- **Mail is read-only (DEC-07).** No send, move, flag or delete call anywhere. A test enforces it.
- **Documents are data (DEC-09).** Document text is fenced in every prompt and never acts as an instruction. A
  person accepts every extracted document (DEC-08). Never guess a PO.
- **Real documents (DEC-13).** Only synthetic data goes in `tests/data/` and Git. Before reading a real vendor
  document, ask the owner to confirm it: name the file and where it came from. Real files stay in `real-docs/`
  (git-ignored). No document text in logs, errors or test output.
- **Local only (DEC-01).** No cloud AI, no cloud hosting, no telemetry. Model calls go through `models/llm.py` only.
- **Secrets.** Mailbox secrets live in the Keychain. Never in a file, a log, a test or a commit. The test Gmail
  account's address and password never go in the repo or this chat: the owner enters them.
- **Where files live.** The project is under `~/Projects/po-dashboard`, never iCloud, Desktop or Documents.
- **Size.** Say the size before downloading or creating anything over 1 GB (the 14b model is 9 GB).
- **Git.** Commit or push only when asked. Run `git status` first and stage files by name, never `git add -A` or
  `git add .`, and not a folder or a wildcard either. A laptop-only file created in the repo goes into `.gitignore`. The
  repository is public (DEC-19) and the licence is chosen (DEC-23), but a push still needs the owner to ask for it.
- **Models.** Say once, with the reason, before a step that suits a different model (Opus for design, scoring design
  and security review; Sonnet for following the plan).

## Commands

- Set up: `scripts/setup.sh` (asks before the 9 GB model pull; installs the pre-commit guard)
- Run: `uv run po-dashboard serve` (http://127.0.0.1:8000)
- Check: `uv run ruff check . && uv run mypy && uv run pytest`

## Things learned the hard way

- macOS: `sed -i ''` needs the empty string.
- The first model call after Ollama loads a 14b model can take minutes; the second is far faster.
- `git diff --name-only` quotes non-ASCII names, so a check on the suffix misses `Müller.pdf`. Use `-z`.
- `read -p` prints its prompt only on a terminal. Anything that must be stated goes through `echo`.
- `ollama list | grep -q` can fail under `set -o pipefail`, because grep stops reading early. Read the list first.
- Run a script before ticking its task. Three Phase 0 bugs were found only by running the real thing.
- A test client that always sends a proper `Origin` cannot find browser behaviour. `Referrer-Policy: no-referrer` made
  real browsers send `Origin: null` and the Origin check refused a real sign-in. Answer a form post with a redirect, so
  Reload never repeats an action. Restart the server after every change; check its start time against the file.
- HTMX's response events (`beforeSwap`, `afterRequest`) carry the sending element as `detail.requestConfig.elt`, not
  `detail.elt`; `htmx:afterSettle` fires once for each swapped element, not once per swap. Drive real Chrome
  (Playwright with `channel="chrome"`, run from the scratchpad) before ticking script work: the tests cannot see it.
- Read new warnings in test output; don't just count passes. SQLAlchemy reads `cache_ok` from each `TypeDecorator` class
  itself, so a subclass without it silently skips the query cache.
