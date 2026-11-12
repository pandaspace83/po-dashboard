# Engineering review against the owner's 111-topic checklist

2026-11-10 to 2026-11-11 · written for the owner · the rule book is `~/.claude/engineering-checklist.md` and is run for
every new project.

**Method.** Nine read-only review agents, one per theme, read the code, the tests and the docs. Each gave every topic a
verdict for *this* project: **Needed now**, **Needed later** (with a phase or trigger) or **Not applicable** (with the
reason). Claude then did the small, safe fixes and checked each with a test. Nothing was pushed. The project is one app on
one always-on office Mac for a few people (DEC-28), local only (DEC-01), so most large-system topics do not apply, and the
report says so instead of padding.

## The short version

- **Real bugs found and fixed (concurrency):** a double click or two simultaneous submits of an invoice ended in a 500
  error, and `INV-1` and `inv-1` could both be stored; two people closing the same line both "succeeded" and the second
  overwrote who closed it; wrong-password counting could undercount under parallel guesses; every request wrote the
  session row.
- **The biggest gap, now closed: backups.** Stored originals cannot be recreated once the mailbox has moved on, and the
  plan only said "nightly copy" (unsafe for a live SQLite file). `po-dashboard backup` and `restore` now exist, tested by
  restoring and comparing. A safety copy is also made before any migration.
- **No vulnerabilities found.** No string-built SQL, no unescaped template output, no URL fetched from content, CSRF and
  access control strong. HTTPS is the one real gap for the network step, and it is designed (PD-6.3a).
- **Logging did not exist.** Now: `logs/app.log`, a request id and timing on every response, no message text ever, with
  a test that proves it.
- **Needed soon, built into the work already planned:** retries with backoff for Gmail (PD-3.5); one scan at a time, a work
  cap and a "model down" state (PD-3.4); the queue and dead-letter columns (PD-4.0a, on the `extraction` table: DEC-54); the network hardening (PD-6.3a).

## Done in this review

| Change | Where | Proof |
|---|---|---|
| Backup and restore, online SQLite copy, manifest of hashes, refuses synced folders, never overwrites or deletes | `ops/backup.py`, `cli.py` | `tests/test_backup.py` (TC-OPS-01) |
| Safety copy before an upgrade; refuse a database from a newer app | `db/migrate.py` | `tests/test_backup.py` |
| Duplicate invoice is a message, and `INV-1` = `inv-1` in the database too (migration 0006) | `ledger/events.py` | `tests/test_concurrency.py` (TC-OPS-02) |
| Closing a line is one guarded update; the second person is told | `ledger/closing.py` | `tests/test_concurrency.py` |
| Failed sign-ins counted by the database; session row written once a minute | `auth/service.py` | `tests/test_concurrency.py` |
| Message id unique within a mailbox; messages indexed by date | migration 0006 | `tests/test_concurrency.py` |
| Model call: 5-second connect time-out, long read; `ModelUnavailable` | `models/llm.py` | `tests/test_extraction.py` (TC-OPS-03) |
| `/health` checks the database | `web.py` | `tests/test_web.py` |
| Logging, request id, `Server-Timing`, error type only | `ops/logs.py`, `auth/gate.py` | `tests/test_logging.py` (TC-OPS-04) |
| `Permissions-Policy`, `Cross-Origin-Opener-Policy`, HSTS only when HTTPS is on; CSRF cookie path | `auth/gate.py` | `tests/test_gate.py` (TC-OPS-05) |
| CI workflow, Dependabot, upper bounds on dependencies | `.github/`, `pyproject.toml` | locked install and lock check run locally |

## Verdicts by theme

**1. Traffic and edge.** Not applicable: load balancing, reverse proxy (Uvicorn serves HTTPS itself, DEC-28), API gateway,
CDN (DEC-03), WAF, DDoS (office network, no public port), TCP vs UDP, HTTP/2 and 3, gRPC, CORS (same origin; the right
setting is no CORS headers). Needed later (PD-6.3a): inbound rate limiting as a per-IP sign-in limit; DNS, meaning the
`.local` name tested from a real laptop. Needed now, with the Gmail client (PD-3.5): outbound rate limiting, meaning
`Retry-After`.

**2. Caching and data access.** Not applicable: caching of app data (it would only add stale-status risk; the 0.3 s page
test passes on 1,000 POs), cache invalidation, connection pooling (a local file), read replicas, sharding, partitioning,
build caching (`uv.lock`). Done: every foreign key is indexed; page queries are whole-table. Fixed: indexes on stored
messages. Later: the one small N+1 on the single-PO page; replication is backup (done above).

**3. Deployment and delivery.** Needed now: CI (written; runs on first push). Not applicable: Docker (the model needs the
Mac's GPU and the Keychain is out of a container's reach), Kubernetes, service discovery, autoscaling, horizontal scaling
(one process by design: now stated in OPERATIONS §7), feature flags, blue-green, canary, rolling, Terraform, Helm, cost,
cold starts of a cloud function, serverless limits. Later: rollbacks and infrastructure as code (the launchd file, PD-6.4);
vertical scaling (RAM for the 9 GB model); the slow first model call after a restart (PD-6.4). Tag a release after each phase.

**4. Resilience.** Done: idempotent intake (unique hashes), the time-out split. Needed later, in the scanner: retries and
backoff for Gmail only (the model call is deterministic and slow, so the next scan is its retry), backpressure as a work cap
per tick, health checks beyond up and down (last scan, model). Not applicable: circuit breakers, chaos engineering as a
programme (the planned crash and soak tests cover it), network partitions, leader election (one scan at a time is a lock),
liveness and readiness probes (no orchestrator), failover (a restart is the failover, via launchd), multi-region.

**5. Messaging and async.** Not applicable: pub/sub, event-driven architecture, distributed transactions and sagas (one
database; a file and a row are made safe by temp-name-and-rename and hash dedupe), WebSockets, long polling, server-sent
events, webhooks (they need a public address, which breaks local-first). Needed later: a message queue, as a database
table of pending documents (PD-4.0a); a dead letter queue, as a "failed documents" list with a retry cap and a button
(PD-4.0a); cron, as the scanner loop and launchd (PD-3.4, PD-6.4).

**6. Concurrency and consistency.** Not applicable: CAP, eventual consistency, distributed locks, garbage collection, clock
skew (the server stamps all times). Fixed: the races above. Needed later: optimistic locking, meaning a `version` column on
POs and lines with a "changed by someone else, reload" page, before several buyers edit at once over HTTPS (it needs a
migration and a decision, so it is proposed, not done); `BEGIN IMMEDIATE` and deadlock avoidance with the scanner (PD-3.4,
measured first, because it makes every read take the write lock); a server-side double-submit guard for acknowledgements
and shipments (the busy-button helps; a form nonce is the real fix, with PD-4.5). Held upload previews are bounded and
expire, so memory is fine.

**7. Observability and operations.** Done: logging, request id and timing. Needed later: metrics and observability as
structured local logs plus an admin status page (Phase 3 to 5, no outside tools, DEC-01); alerts, as the in-app
`scan_stale` (PD-5.4); a few lines of SLOs in OPERATIONS once live; throughput and tail latency as documents per hour and
the slowest extraction (Phase 4). Not applicable: distributed tracing, error budgets, P99 (too few requests). Incidents and
postmortems: the defect log is the blameless record; add a live-incident section at go-live; name who restarts and logs in
to the office Mac (OPERATIONS §3).

**8. Security.** Done and strong: access control, CSRF, SQL injection, XSS, SSRF, private files; OAuth (PD-3.5:
`state`, PKCE, fixed redirect, re-checked on Opus in PD-3.6); the Keychain through `keyring` (PD-3.5); hostile mail and
attachments (PD-3.6, DEC-53: each document read in a sandboxed process with time and memory limits, headers parsed
from raw bytes, a cap on MIME parts, five image formats only). Needed later: TLS and encryption in transit (PD-6.3a,
with the coupling checks listed there); a FileVault check; a malware scan is still open (ARCHITECTURE §10). Not
applicable: JWT rotation (server-side sessions), IAM beyond the two roles.

**9. Data lifecycle and versioning.** Done: online backup, restore test, safety copy before an upgrade, a refusal of a
newer database, dependency upper bounds. Later: nightly schedule, off-Mac copy and pruning with the owner's yes (PD-6.5);
downgrade tests for each migration (add as migrations grow); semantic version and a tag at go-live, shown on the health
strip. Not applicable: API versioning (internal; the extraction prompt and schema are versioned already).

## What was deliberately not done, and why

- **`BEGIN IMMEDIATE` on every transaction:** it would make plain page views take the write lock. Do it with the scanner
  (PD-3.4) and measure.
- **A polling pause for hidden tabs:** HTMX's filter expression needs script evaluation, which the strict page policy
  forbids (`allowEval` is off).
- ~~A `version` column for optimistic locking~~ **Done 2026-11-11 (DEC-52, TC-OPS-06):** on `vendor` and `po_line`; migration 0007.
- **Pruning old backups:** removing files needs the owner's yes (global rule).
- **Pinning CI actions to commit SHAs:** needs network access to read the SHAs; do it at the first push.
