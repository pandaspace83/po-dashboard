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
