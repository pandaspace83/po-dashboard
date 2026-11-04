# PO Dashboard: Operations runbook

2026-11-04 · **Draft written before the build.** Commands marked *(PD-n.n)* are filled in and tested when that task is
done (PLAN.md). Until then this page says what the runbook must cover.

## 1. Where things live

| What | Where |
|---|---|
| Code | the cloned repository, anywhere under `~/Projects` (never Desktop, Documents or iCloud) |
| Database, stored originals, logs | the data folder, default `~/Library/Application Support/PO Dashboard/` (PD-6.5) |
| Backups | a folder the user chooses, outside iCloud; 35 days kept |
| Mailbox secret | the macOS Keychain, entry "PO Dashboard mailbox" |
| Settings | the Settings screen (stored in the database); `.env` only for development, git-ignored |

## 2. Install and first run (PD-0.3, PD-7.1)

1. Clone the repository. 2. `scripts/setup.sh` (it states the model size, 9 GB, and asks before downloading).
3. `scripts/demo.sh` loads the synthetic sample mailbox. 4. Open http://127.0.0.1:8000.
5. Settings → mail source → enter the mailbox details (the secret goes to the Keychain). 6. Install the service (§3).

## 3. Run as a service (PD-6.4)

A launchd user agent starts the app at login and restarts it if it stops. Commands for install, status, stop and
uninstall are `scripts/service.sh install|status|stop|uninstall`. Logs: the data folder's `logs/`. Logs hold ids and
counts, never document text.

**Sleep.** The Mac does not scan while asleep. On waking, the first scan reads everything since the last success, so
nothing is missed. To scan overnight, keep the Mac awake (System Settings → Battery / Energy).

## 4. What to check each day

The dashboard health strip: **last scan** (should be within 10 minutes while the Mac is awake), **model reachable**,
**documents waiting for review**, **last backup**. A red banner or a `scan_stale` alert means a scan has not succeeded
for 30 minutes.

## 5. When something goes wrong

| Symptom | Likely cause | Do this |
|---|---|---|
| Banner: "mailbox sign-in failed" | secret expired or revoked; permission changed | Check the secret's expiry with the admin; re-enter it in Settings. Permission changes can take up to 2 hours to apply |
| Banner: "can't read mailbox" after a change | the broad `Mail.Read` grant removed, or RBAC scope wrong | Ask the admin to check the app's scope covers the one mailbox |
| Banner: "model not reachable" | Ollama not running, or the model missing | Start Ollama; `ollama list` shows `qwen2.5:14b-instruct`; pull it if missing |
| Documents stuck "processing" | the app stopped mid-document | Restart the service; the document is retried; nothing is lost because the position moves only after storage |
| Every document is flagged or Unmatched | wrong vendor domains, or POs not entered | Check the vendor's domains; enter the missing POs |
| Slow extraction | machine short of memory | Close other apps; or choose a smaller model in Settings (lower accuracy) |
| Wrong data was accepted | a person accepted a misread document | Edit the facts on the PO (recorded in the audit trail); report the document so it joins the golden set |
| The Mac was off for days | | Start it; the catch-up scan reads everything since the last success |

## 6. Backup and restore (PD-6.5)

Nightly copy of the database and stored files to the chosen folder, 35 days kept. Restore: stop the service, run
`scripts/restore.sh <backup>`, start the service. The restore is tested in PD-6.5 on a copy, never on the live data.

## 7. Update (PD-6.5)

`git pull`, then restart the service. On start the app backs up the database, then applies migrations. If a migration
fails the app does not start and says why; restore the backup and report it.

## 8. Moving to another machine

Install there (§2), then copy the data folder only if the old machine is retired: stop both services first, so two
copies never scan the same mailbox with different saved positions.

## 9. Leaving the mailbox

To stop the app reading mail: stop the service, then ask the admin to remove the app's role assignment. Local data
stays until the owner deletes the data folder.
