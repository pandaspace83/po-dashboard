# Defects and retests

*Built from [test_record.toml](test_record.toml) by `scripts/test_record.py`. Do not edit by hand.*

Every failure is logged here the moment it is found, by whoever finds it: the case, the browser, what was seen and a screenshot; then the cause, the fix and its commit; then a retest with its own screenshot. Failures before 11/07/2026 are in [BUILD_HISTORY.md](../docs/BUILD_HISTORY.md) and are not repeated here.

| ID | Found | Case | Severity | Title | Status |
|---|---|---|---|---|---|
| [DEF-001](#def-001) | 11/07/2026 | [TC-QB-01](TEST_CASES.md#tc-qb-01) | Medium | The QuickBooks report uploaded on Import CSV or Excel was refused with a list of missing columns | Fixed, waiting for the owner's retest |
| [DEF-002](#def-002) | 11/07/2026 | [TC-SIGN-06](TEST_CASES.md#tc-sign-06) | Low | A buyer opening an admin address saw raw code instead of a page | Fixed, waiting for the owner's retest |

<a id="def-001"></a>

## DEF-001: The QuickBooks report uploaded on Import CSV or Excel was refused with a list of missing columns

**Found:** 11/07/2026 by Owner, QuickBooks check step 4 · **Case:** TC-QB-01 · **Browser:** Not stated · **Severity:** Medium · **Status:** Fixed, waiting for the owner's retest

**Steps to reproduce**

1. Purchase orders, Import CSV or Excel (instead of Load from QuickBooks, the button beside it).
2. Choose tests/data/quickbooks_open_pos_110526.csv and press Import.

**Expected:** The QuickBooks preview, or at least a message saying which button to use.

**Actual:** "Nothing was imported. Fix these and try again: Row 1: Missing column: po_number, vendor, currency, line_no, quantity, unit, unit_price ..." followed by the file's columns.

**Seen:** screenshots/DEF-001-before.png (recreated by Claude on commit 7f2d081)

![DEF-001 as found](screenshots/DEF-001-before.png)

**Cause:** The two upload buttons sit side by side with the same icon, and the import page only knew its own format.

**Fix:** PD-1.10a.5: Import CSV or Excel recognises the QuickBooks report and shows the QuickBooks preview (nothing is created until Create); the app's own file on the QuickBooks page is refused with a link to Import. 3 new tests. Not committed yet.

**Retest:** 11/07/2026, Claude in Chrome: pass (TC-PIMP-06, TC-QB-08). Owner's retest of step 4 and new steps 15 and 16: waiting.

![DEF-001 retested](screenshots/TC-PIMP-06.png)


<a id="def-002"></a>

## DEF-002: A buyer opening an admin address saw raw code instead of a page

**Found:** 11/07/2026 by Claude, browser evidence run in Chrome · **Case:** TC-SIGN-06 · **Browser:** Google Chrome 156.0.8078.81 (scripts/browser_evidence.py) · **Severity:** Low · **Status:** Fixed, waiting for the owner's retest

**Steps to reproduce**

1. Sign in as a buyer.
2. Type /admin/users or /admin/audit in the address bar.

**Expected:** A refusal page saying Admins only, in the app's own layout.

**Actual:** The access was refused (status 403), but the browser showed the raw answer {"detail":"Admins only."} on a blank page. A mistyped address (404) and an unreadable form (422) did the same.

**Seen:** screenshots/DEF-002-before.png

![DEF-002 as found](screenshots/DEF-002-before.png)

**Cause:** Refusals raised as errors used the web framework's default answer, meant for scripts. The sign-in check of 11/04/2026 looked for the words Admins only, which the raw answer also contains.

**Fix:** Refusals, missing pages and unreadable forms are now pages in the app's layout with a Go to Home button, for browsers and HTMX; a script that does not ask for HTML still gets the short JSON. 3 new tests; new case TC-WEB-06. Not committed yet.

**Retest:** 11/07/2026, Claude in Chrome: pass (TC-SIGN-06, TC-WEB-06). Owner's retest: waiting.

![DEF-002 retested](screenshots/TC-SIGN-06.png)
