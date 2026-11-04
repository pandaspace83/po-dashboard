# PO Dashboard

A local-first purchasing dashboard. It scans a mailbox for vendor replies to your purchase orders, reads the
acknowledgements, shipment notices and invoices with a local model, and tracks every PO line from sent to closed. It
tells you which lines need a follow-up. Everything runs on your own computer; the only network traffic is the mailbox.

> **Status, 2026-11-04: design only.** The requirements, decisions, architecture and plan are written and waiting for
> approval. Nothing is built yet. See [docs/PLAN.md](docs/PLAN.md).

| Document | What it is |
|---|---|
| [docs/REQUIREMENTS.md](docs/REQUIREMENTS.md) | What the app must do |
| [docs/DECISIONS.md](docs/DECISIONS.md) | Decisions made and questions still open |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | How it is designed |
| [docs/PLAN.md](docs/PLAN.md) | The build order and its status |
| [docs/AUDIT.md](docs/AUDIT.md) | Each requirement against what was built |
| [docs/OPERATIONS.md](docs/OPERATIONS.md) | Running it day to day, and when it breaks |
| [docs/BUILD_HISTORY.md](docs/BUILD_HISTORY.md) | How it was built, with dates |

Install and run instructions arrive with Phase 0 of the plan. This repository is public: it holds code, docs and
synthetic test data only.
