# PO Dashboard

A local-first purchasing dashboard. It scans a mailbox for vendor replies to your purchase orders, reads the
acknowledgements, shipment notices and invoices with a local model, and tracks every PO line from sent to closed. It
tells you which lines need a follow-up. Everything runs on one always-on Mac in your office, and people use it from a browser on
their own laptops; the only traffic leaving the office is the mailbox connection.

> **Status, 2026-11-04: early build.** The plan is approved and the app shell runs, showing one page and a health strip.
> Sign-in, purchase orders and mail scanning are not built yet. See [docs/PLAN.md](docs/PLAN.md).

| Document | What it is |
|---|---|
| [docs/REQUIREMENTS.md](docs/REQUIREMENTS.md) | What the app must do |
| [docs/DECISIONS.md](docs/DECISIONS.md) | Decisions made and questions still open |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | How it is designed |
| [docs/PLAN.md](docs/PLAN.md) | The build order and its status |
| [docs/AUDIT.md](docs/AUDIT.md) | Each requirement against what was built |
| [docs/OPERATIONS.md](docs/OPERATIONS.md) | Running it day to day, and when it breaks |
| [docs/BUILD_HISTORY.md](docs/BUILD_HISTORY.md) | How it was built, with dates |

## Run it

macOS only for now. You need [uv](https://docs.astral.sh/uv/) and [Ollama](https://ollama.com); Tesseract is needed
for scanned PDFs later.

```sh
scripts/setup.sh              # installs dependencies; states the 9 GB model size and asks before downloading
uv run po-dashboard serve     # http://127.0.0.1:8000
```

To check a change: `uv run ruff check . && uv run mypy && uv run pytest`.

This repository is public: it holds code, docs and synthetic test data only. It is published to be read, not reused:
see [LICENSE](LICENSE).
