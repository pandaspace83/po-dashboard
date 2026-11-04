# How PO Dashboard was built

The real order and dates of the work. Every date comes from the dated status line of a plan, the git history, or a
record in this repository.

## Method

Spec first, as with LegalLens: requirements, decisions, architecture, a phased plan approved before any code, tests at
every layer, and an audit of what was built against the requirements. The owner directs and approves; Claude Code
writes the documents and the code and credits itself as co-author on each commit.

## Timeline

| Date | What happened |
|---|---|
| 2026-11-04 | **Idea.** The owner described the problem: POs go to vendors; acknowledgements with line-by-line promised dates, shipment notices with tracking numbers and invoices come back; partial receipts leave lines open; a report of open items and a way to chase vendors is needed. Python, a decent front end, everything local. |
| 2026-11-04 | **Scoping answers.** Mail comes from a Microsoft mailbox scanned every few minutes; alerts are in-app only for now; the PO format is "the standard global one" (corrected: there is none, X12 and EDIFACT are the nearest, see ARCHITECTURE §4); sample documents will be uploaded later. |
| 2026-11-04 | **Model trial.** Three installed models were scored on 4 made-up documents (36 fields), twice. qwen2.5:14b-instruct 34/36 and the only one to ignore planted instructions; llama3.2:3b 32/36; qwen2.5:7b-instruct 30/36. The owner chose accuracy: qwen2.5:14b-instruct (DEC-05). Script: `evals/model_trial_2026-11-04.py`. |
| 2026-11-04 | **Further answers.** Gmail for testing until the Microsoft mailbox is ready; the owner's admin is creating it; the project goes to Git and a friend runs their own copy on their own machine; docs to follow the LegalLens set. |
| 2026-11-04 | **Documents written:** requirements, decisions (16), architecture, plan (8 phases), audit skeleton, operations runbook draft. Waiting for the owner's approval. |
| 2026-11-04 | **Plan approved; Phase 0 built.** The app shell, setup script, pre-commit guard and first tests, committed locally. Nothing pushed; no GitHub remote exists yet. |
| 2026-11-04 | **Currencies and quantities decided (DEC-24).** Several currencies, decimal quantities. Tax was written down as "not modelled" on an assumption; the owner moved the question to Phase 2 (OPEN-11). |
| 2026-11-04 | **Documentation pass before Phase 1, at the owner's request; it became the rule for every phase (DEC-25).** Running the Phase 0 work for real, which had not been done before ticking it, found these bugs, all now fixed with tests: `setup.sh` offered the 9 GB download with the model already installed, and did not state the size when input was piped; the pre-commit guard let through non-ASCII file names and renames out of `tests/data/`, and refused legitimate names with spaces; the health strip said "model reachable" when only Ollama was up. The docs also still said the licence was open, though DEC-23 had settled it. |
| 2026-11-04 | **Sign-in design written on Opus (ARCHITECTURE §10.1, DEC-26, DEC-27).** Starts from LegalLens. It also found that sign-in cannot protect the files from someone using the same macOS account (DEC-27), and that the line status table contradicted itself (now ordered, §5). |
| 2026-11-04 | **The deployment changed, before any mail code existed.** The owner's answers: one always-on Mac in the office, used from laptops over the office network (DEC-28); company-wide visibility (DEC-29); purchasing-only mailboxes connected on the provider's own page (DEC-30); a Gmail test account for the build, switching at the demo (DEC-31); provider sign-in preferred at the switch (DEC-32). A proposed page of our own for mailbox passwords was rejected: Microsoft 365 no longer accepts plain passwords for mail, and the reassuring message it needed would have been false. |
| 2026-11-04 | **One mailbox in v1 (DEC-33).** The owner: route all purchasing mail to `purchasing@`; more mailboxes later, with keyword rules. Claude placed that in v2. Phase 1 started with PD-1.1a. |
| 2026-11-04 | **Second factor paused (DEC-34); sign-in core built (PD-1.9b).** The owner paused the 6-digit code until Sign in with Microsoft is settled. Passwords, sessions, lockout, temporary passwords, the last-admin rule and the `user` commands are built and tested, and the real commands were run end to end. |
| 2026-11-04 | **Sign-in pages built (PD-1.9c).** A gate in front of every route (host, session, CSRF, Origin, security headers), the sign-in, change-password and People pages, and `/health` cut to up or down. The test sweeps every route in the app's own schema, so a future route is checked automatically. Run against the real server with real HTTP requests. |
