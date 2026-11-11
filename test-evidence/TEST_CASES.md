# Test cases

*Built from [test_record.toml](test_record.toml) by `scripts/test_record.py`. Do not edit by hand.*

PO Dashboard (RaceSpec Internal Dashboard). Scope: Phase 0 and Phase 1: sign-in, people, vendors, purchase orders, imports, the QuickBooks starting load, entries by hand, Home, Open items, Audit, page behaviour, security and speed.

**93 cases:** 44 Critical, 36 High, 13 Medium; 2 terminal, 31 automated, 59 browser, 1 owner only.

Latest Chrome run: 11/07/2026, Google Chrome 156.0.8078.81, by Claude, scripts/browser_evidence.py, on commit 7f2d081 with uncommitted changes: **59 of 59** browser cases pass. Automated tests: `uv run pytest`. Defects: [DEFECTS.md](DEFECTS.md). Sign-off: [SIGN_OFF.md](SIGN_OFF.md).

**To run them by hand:**

- cd ~/Projects/po-dashboard
- export PO_DASHBOARD_DATA="$HOME/Projects/po-dashboard-check-data"   (a throwaway folder; never the real one)
- uv run po-dashboard user create-admin boss@example.com   (choose a password, twice; nothing shows as you type)
- uv run po-dashboard serve   (leave this window open), then open http://127.0.0.1:8000 and sign in
- Made-up files to use, all in tests/data: vendors_sample.xlsx, vendors_problems.csv, purchase_orders_sample.csv, purchase_orders_bad_row.csv, quickbooks_open_pos_110526.csv
- Run the cases in the order listed: later cases use what earlier ones created.
- Clean up afterwards: Ctrl-C in the server window, then rm -r "$HOME/Projects/po-dashboard-check-data" and unset PO_DASHBOARD_DATA

## Summary

| ID | Title | Priority | How | Claude, Chrome | Owner, last result |
|---|---|---|---|---|---|
| [TC-SET-01](#tc-set-01) | Set-up states the 9 GB model download and never starts it without a yes | High | Terminal | — | — |
| [TC-SET-02](#tc-set-02) | The first admin is made in Terminal with a hidden password prompt | High | Terminal | — | Pass 11/04/2026 |
| [TC-SET-03](#tc-set-03) | The app stays local: it refuses other host names and a model server off this Mac | Critical | Automated | Automated tests | — |
| [TC-SET-04](#tc-set-04) | The commit guard refuses real documents and secrets outside tests/data | Critical | Automated | Automated tests | — |
| [TC-SET-05](#tc-set-05) | The data folder and database are private to the Mac's user, and upgrades are safe to repeat | High | Automated | Automated tests | — |
| [TC-SIGN-01](#tc-sign-01) | A signed-out visitor sees only the sign-in page | Critical | Browser | Pass 11/07/2026 | Pass 11/04/2026 (Safari, Chrome) |
| [TC-SIGN-02](#tc-sign-02) | A wrong password and an unknown email get the same message | Critical | Browser | Pass 11/07/2026 | Pass 11/04/2026 (Safari, Chrome) |
| [TC-SIGN-03](#tc-sign-03) | Signing in opens Home, with the account menu at the top right | High | Browser | Pass 11/07/2026 | Pass 11/06/2026 |
| [TC-SIGN-04](#tc-sign-04) | An admin adds a person and sees the temporary password once | Critical | Browser | Pass 11/07/2026 | Pass 11/04/2026 (Safari, Chrome) |
| [TC-SIGN-05](#tc-sign-05) | A temporary password leads only to Change password, which refuses a weak password | Critical | Browser | Pass 11/07/2026 | Pass 11/04/2026 (Safari, Chrome) |
| [TC-SIGN-06](#tc-sign-06) | A buyer cannot open the admin screens | Critical | Browser | Pass 11/07/2026 | Pass 11/05/2026 (Safari, Chrome), before DEF-002 was found: retest needed |
| [TC-SIGN-07](#tc-sign-07) | A disabled person is cut off and gets the ordinary refusal | Critical | Browser | Pass 11/07/2026 | Pass 11/04/2026 (Safari, Chrome) |
| [TC-SIGN-08](#tc-sign-08) | Five wrong passwords lock the account, even against the right one, until 15 minutes pass or an admin unlocks it | Critical | Browser | Pass 11/07/2026 | Pass 11/04/2026 (Safari, Chrome) |
| [TC-SIGN-09](#tc-sign-09) | 30 idle minutes sign a person out, even with a page that updates itself left open | High | Automated | Automated tests | — |
| [TC-SIGN-10](#tc-sign-10) | The session cookie is locked down, and the last admin cannot be removed | Critical | Automated | Automated tests | Pass 11/04/2026 (Safari, Chrome) |
| [TC-SIGN-11](#tc-sign-11) | Sign out, then sign in again, with no security-token error | Medium | Browser | Pass 11/07/2026 | Pass 11/06/2026 |
| [TC-VIMP-01](#tc-vimp-01) | A vendor file (Excel or CSV) creates the vendors with their domains | High | Browser | Pass 11/07/2026 | Pass 11/06/2026 (CSV); the .xlsx file: no problem reported 11/07/2026 |
| [TC-VIMP-02](#tc-vimp-02) | Uploading the same vendor file again changes nothing | Critical | Browser | Pass 11/07/2026 | Pass 11/06/2026 |
| [TC-VIMP-03](#tc-vimp-03) | Each vendor row needs an email, and an email or domain held by another vendor skips only that row | Critical | Browser | Pass 11/07/2026 | Pass 11/06/2026 |
| [TC-VIMP-04](#tc-vimp-04) | An existing vendor with a new email gets the new domain, and its other details are kept | High | Automated | Automated tests | Pass 11/06/2026 |
| [TC-VIMP-05](#tc-vimp-05) | An old Excel file (.xls) is refused with what to do | Medium | Browser | Pass 11/07/2026 | No problem reported 11/07/2026 (QuickBooks check step 2) |
| [TC-VEN-01](#tc-ven-01) | A vendor is added on its own page and lands on its vendor page | High | Browser | Pass 11/07/2026 | Pass 11/05/2026 (Safari, Chrome) |
| [TC-VEN-02](#tc-ven-02) | A vendor name that already exists, in any case, is refused and the boxes keep what was typed | High | Browser | Pass 11/07/2026 | Pass 11/05/2026 (Safari, Chrome) |
| [TC-VEN-03](#tc-ven-03) | A domain belongs to one vendor only, so a reply can never match the wrong vendor | Critical | Browser | Pass 11/07/2026 | Pass 11/06/2026 |
| [TC-VEN-04](#tc-ven-04) | Domains are checked, added and removed on the vendor page | Medium | Browser | Pass 11/07/2026 | Pass 11/05/2026 (Safari, Chrome) |
| [TC-VEN-05](#tc-ven-05) | Saving a vendor shows a Saved note and the page does not jump | Medium | Browser | Pass 11/07/2026 | Pass 11/06/2026 |
| [TC-VEN-06](#tc-ven-06) | A vendor with no domain is marked, because its replies cannot be matched | High | Browser | Pass 11/07/2026 | Pass 11/05/2026 (Safari, Chrome) |
| [TC-VEN-07](#tc-ven-07) | The vendor list searches as you type, sorts by heading, and / jumps to search | Medium | Browser | Pass 11/07/2026 | Pass 11/06/2026 |
| [TC-VEN-08](#tc-ven-08) | Markup typed into a vendor name is shown as text, never run | Critical | Browser | Pass 11/07/2026 | Pass 11/05/2026 (Safari, Chrome) |
| [TC-PO-01](#tc-po-01) | A PO entered by hand keeps exact numbers and starts as Sent | Critical | Browser | Pass 11/07/2026 | Pass 11/05/2026 (Safari, Chrome) |
| [TC-PO-02](#tc-po-02) | Reload after creating a PO never creates a second one | Critical | Browser | Pass 11/07/2026 | Pass 11/06/2026 |
| [TC-PO-03](#tc-po-03) | A wrong line value is refused naming the line and field, and every box keeps what was typed | High | Browser | Pass 11/07/2026 | Pass 11/06/2026 |
| [TC-PO-04](#tc-po-04) | More than 4 decimal places is refused; nothing is ever rounded | Critical | Browser | Pass 11/07/2026 | Pass 11/05/2026 (Safari, Chrome) |
| [TC-PO-05](#tc-po-05) | The vendor box searches as you type, and a name not picked from the list is refused | High | Browser | Pass 11/07/2026 | Pass 11/06/2026 |
| [TC-PO-06](#tc-po-06) | An existing PO number is refused in any case | Critical | Automated | Automated tests | — |
| [TC-PO-07](#tc-po-07) | The PO list filters as you choose, reads dates as MM/DD/YYYY, and Clear resets it | High | Browser | Pass 11/07/2026 | Pass 11/06/2026 |
| [TC-PO-08](#tc-po-08) | The PO list shows 50 a page and sorts by any heading | Medium | Automated | Automated tests | — |
| [TC-PO-09](#tc-po-09) | A buyer, not only an admin, can use Vendors and Purchase orders | High | Browser | Pass 11/07/2026 | Pass 11/05/2026 (Safari, Chrome) |
| [TC-PIMP-01](#tc-pimp-01) | A PO file imports every PO and line, and the template downloads | High | Browser | Pass 11/07/2026 | Pass 11/06/2026 |
| [TC-PIMP-02](#tc-pimp-02) | Importing the same file again adds nothing and overwrites nothing | Critical | Browser | Pass 11/07/2026 | Pass 11/06/2026 |
| [TC-PIMP-03](#tc-pimp-03) | One bad row rejects the whole file, naming the row | Critical | Browser | Pass 11/07/2026 | Pass 11/05/2026 (Safari, Chrome) |
| [TC-PIMP-04](#tc-pimp-04) | A vendor not in the app is refused, never guessed or created | Critical | Automated | Automated tests | — |
| [TC-PIMP-05](#tc-pimp-05) | Excel files with real date cells, Windows-1252 accents and formulas are read safely | High | Automated | Automated tests | — |
| [TC-PIMP-06](#tc-pimp-06) | The QuickBooks report uploaded on Import CSV or Excel becomes the QuickBooks preview | High | Browser | Pass 11/07/2026 | Not yet run |
| [TC-QB-01](#tc-qb-01) | The preview shows what the file would do and changes nothing | Critical | Browser | Pass 11/07/2026 | Step 4 failed 11/07/2026: DEF-001 |
| [TC-QB-02](#tc-qb-02) | Create loads all of them or none, and lands on the PO list | Critical | Browser | Pass 11/07/2026 | Not yet run |
| [TC-QB-03](#tc-qb-03) | Back and Create again can never load the file twice | Critical | Browser | Pass 11/07/2026 | Not yet run |
| [TC-QB-04](#tc-qb-04) | A loaded PO keeps the QuickBooks date, the customer, and what was already received | High | Browser | Pass 11/07/2026 | Not yet run |
| [TC-QB-05](#tc-qb-05) | A unit price that cannot be exact is rounded to 4 places, and the line's history says so | Critical | Browser | Pass 11/07/2026 | Not yet run |
| [TC-QB-06](#tc-qb-06) | Loaded POs are not flagged for an acknowledgement the app never had the chance to see | High | Browser | Pass 11/07/2026 | Not yet run |
| [TC-QB-07](#tc-qb-07) | Loading the same file again creates nothing; adding the waiting vendor lets its PO in | Critical | Browser | Pass 11/07/2026 | Not yet run |
| [TC-QB-08](#tc-qb-08) | A file that is not the report is refused naming what is missing; the app's own file is pointed to Import | Medium | Browser | Pass 11/07/2026 | Not yet run |
| [TC-QB-09](#tc-qb-09) | The company's real export previews with counts that match QuickBooks | Critical | Owner only | — | Not yet run (Claude's dry run 11/06/2026: 0 problems; counts only) |
| [TC-EVT-01](#tc-evt-01) | A short confirmation at another price is recorded and the purchaser is told to update the PO | Critical | Browser | Pass 11/07/2026 | Pass 11/05/2026 (Safari, Chrome) |
| [TC-EVT-02](#tc-evt-02) | A later promised date is shown as a slip | High | Browser | Pass 11/07/2026 | Pass 11/05/2026 (Safari, Chrome) |
| [TC-EVT-03](#tc-evt-03) | Shipments move a line from Partially shipped to Shipped, with who entered them | Critical | Browser | Pass 11/07/2026 | Pass 11/05/2026 (Safari, Chrome) |
| [TC-EVT-04](#tc-evt-04) | An invoice starts at the PO price; a short-confirmed line stays open until someone settles it | Critical | Browser | Pass 11/07/2026 | Pass 11/05/2026 (Safari, Chrome) |
| [TC-EVT-05](#tc-evt-05) | Over-billing and a price above the PO are flagged and still recorded | Critical | Browser | Pass 11/07/2026 | Pass 11/05/2026 (Safari, Chrome) |
| [TC-EVT-06](#tc-evt-06) | Removing an entry needs a reason and undoes its effect | High | Browser | Pass 11/07/2026 | Pass 11/05/2026 (Safari, Chrome) |
| [TC-EVT-07](#tc-evt-07) | Reload after recording or removing anything repeats nothing | Critical | Automated | Automated tests | Pass 11/05/2026 (Safari, Chrome) |
| [TC-EVT-08](#tc-evt-08) | A promised date in the past makes the line late, on the PO, in the list and in Late only | High | Browser | Pass 11/07/2026 | Pass 11/05/2026 (Safari, Chrome) |
| [TC-EVT-09](#tc-evt-09) | Settling by hand needs a reason, stays in place, and can be reopened with a reason | High | Browser | Pass 11/07/2026 | Pass 11/06/2026 |
| [TC-EVT-10](#tc-evt-10) | A line's timeline keeps everything that happened, including what was taken back | High | Browser | Pass 11/07/2026 | Pass 11/06/2026 |
| [TC-EVT-11](#tc-evt-11) | The status of every line follows the agreed table in every combination | Critical | Automated | Automated tests | — |
| [TC-HOME-01](#tc-home-01) | Home shows the six counts and a Needs attention list with the next step | High | Browser | Pass 11/07/2026 | Pass 11/06/2026 |
| [TC-HOME-02](#tc-home-02) | Home updates itself within a minute | High | Browser | Pass 11/07/2026 | Pass 11/06/2026 |
| [TC-HOME-03](#tc-home-03) | Open items filters as you choose, and a late tile opens it filtered | Medium | Browser | Pass 11/07/2026 | Pass 11/06/2026 |
| [TC-HOME-04](#tc-home-04) | The Open items CSV downloads as a file and a formula in a cell stays plain text | Critical | Browser | Pass 11/07/2026 | Pass 11/06/2026 |
| [TC-HOME-05](#tc-home-05) | A PO sent with no acknowledgement is flagged only after two working days | High | Automated | Automated tests | — |
| [TC-AUD-01](#tc-aud-01) | An admin sees every change, newest first, and can narrow it | High | Browser | Pass 11/07/2026 | Pass 11/06/2026 |
| [TC-AUD-02](#tc-aud-02) | Every change to the ledger writes an audit record with who and where, and nothing secret | Critical | Automated | Automated tests | — |
| [TC-WEB-01](#tc-web-01) | Pages change without a white flash, and Back and Forward work | Medium | Browser | Pass 11/07/2026 | Pass 11/06/2026 |
| [TC-WEB-02](#tc-web-02) | At phone width the page never scrolls sideways | High | Browser | Pass 11/07/2026 | Pass 11/06/2026 |
| [TC-WEB-03](#tc-web-03) | Everything can be reached with the keyboard | Medium | Browser | Pass 11/07/2026 | Pass 11/06/2026 |
| [TC-WEB-04](#tc-web-04) | Dark mode on the Mac leaves the app light and readable | Medium | Browser | Pass 11/07/2026 | Pass 11/04/2026 (Safari, Chrome) |
| [TC-WEB-05](#tc-web-05) | Every page answers within 0.3 seconds with a year's worth of data | High | Automated | Automated tests | — |
| [TC-WEB-06](#tc-web-06) | A mistyped address or an unreadable form shows a page a person can read | Medium | Browser | Pass 11/07/2026 | Not yet run |
| [TC-SEC-01](#tc-sec-01) | Every change needs the session's token and must come from the app's own pages | Critical | Automated | Automated tests | — |
| [TC-SEC-02](#tc-sec-02) | Pages carry the security headers, are not cached, and run no inline script or style | High | Automated | Automated tests | — |
| [TC-SEC-03](#tc-sec-03) | Hostile spreadsheets are refused: macros, zip bombs, entity tricks, huge sheets, links to files | Critical | Automated | Automated tests | — |
| [TC-SEC-04](#tc-sec-04) | Uploads over 2 MB, or that do not state their size, are refused before they are read | High | Automated | Automated tests | — |
| [TC-SEC-05](#tc-sec-05) | Passwords are stored only as argon2id hashes; no secret is in the database file or the logs | Critical | Automated | Automated tests | — |
| [TC-SEC-06](#tc-sec-06) | Quantities and money are exact everywhere | Critical | Automated | Automated tests | — |
| [TC-AI-01](#tc-ai-01) | The golden set is rebuilt exactly from its seed, and has the mix the owner asked for | High | Automated | Automated tests | — |
| [TC-AI-02](#tc-ai-02) | Each scoring rule gives the verdict SCORING.md says, with made-up model answers | Critical | Automated | Automated tests | — |
| [TC-AI-03](#tc-ai-03) | The document is fenced as data, and the model call is local, repeatable and leaks no text | Critical | Automated | Automated tests | — |
| [TC-MAIL-01](#tc-mail-01) | Mail is read-only: nothing in the app can send, move, flag or delete a message | Critical | Automated | Automated tests | — |
| [TC-MAIL-02](#tc-mail-02) | Every mail source passes the same contract (folder now; Gmail and Microsoft 365 later) | High | Automated | Automated tests | — |
| [TC-MAIL-03](#tc-mail-03) | The folder reader ignores what is not mail and cannot be tricked out of its folder | High | Automated | Automated tests | — |
| [TC-MAIL-04](#tc-mail-04) | The mailbox table keeps its own rules | Medium | Automated | Automated tests | — |
| [TC-MAIL-05](#tc-mail-05) | Every original message and attachment is kept unchanged, once, under its hash | Critical | Automated | Automated tests | — |
| [TC-MAIL-06](#tc-mail-06) | A hostile message cannot hurt intake: odd file names, broken mail, huge or too many files | Critical | Automated | Automated tests | — |
| [TC-MAIL-07](#tc-mail-07) | The store is private, never half-written, rolls back cleanly, keeps message text out of logs and honours the start date | High | Automated | Automated tests | — |

## Set-up and running

<a id="tc-set-01"></a>

### TC-SET-01: Set-up states the 9 GB model download and never starts it without a yes

**Priority:** High · **How:** Terminal · **Refs:** PD-0.3, CLAUDE.md: Size

**Steps**

1. Run scripts/setup.sh on a Mac without the model.
2. Answer anything but yes when it asks about the download.

**Expected:** It states the size (about 9 GB) before asking, and nothing is downloaded without a yes.

**Automated tests:** `test_setup_script.py::test_states_the_size_and_does_not_pull_without_a_yes`, `test_setup_script.py::test_no_answer_at_all_does_not_pull`, `test_setup_script.py::test_pulls_on_a_yes`

**Latest:** Owner: not recorded.


<a id="tc-set-02"></a>

### TC-SET-02: The first admin is made in Terminal with a hidden password prompt

**Priority:** High · **How:** Terminal · **Refs:** PD-1.9b

**Steps**

1. Run uv run po-dashboard user create-admin boss@example.com with a fresh data folder.
2. Run it a second time.

**Expected:** The password is asked twice and never shown. There is no way to give it on the command line. The second run is refused because an account exists.

**Automated tests:** `test_cli.py::test_create_admin_uses_a_hidden_prompt_and_signs_in`, `test_cli.py::test_there_is_no_way_to_pass_a_password_on_the_command_line`, `test_cli.py::test_create_admin_refuses_once_accounts_exist`

**Latest:** Owner: Pass 11/04/2026.


<a id="tc-set-03"></a>

### TC-SET-03: The app stays local: it refuses other host names and a model server off this Mac

**Priority:** Critical · **How:** Automated · **Refs:** DEC-01, NFR local only

**Steps**

1. Covered by the tests named: a request with another host name, and a model server address that is not this Mac.

**Expected:** Both are refused. Health answers only 'up'. No API schema is served.

**Automated tests:** `test_web.py::test_non_loopback_host_refused`, `test_web.py::test_remote_model_server_refused`, `test_web.py::test_health_says_only_up`, `test_web.py::test_no_api_schema_is_served`, `test_gate.py::test_an_unknown_host_name_is_refused`

**Latest:** Owner: not recorded.


<a id="tc-set-04"></a>

### TC-SET-04: The commit guard refuses real documents and secrets outside tests/data

**Priority:** Critical · **How:** Automated · **Refs:** DEC-13, PD-0.6

**Steps**

1. Covered by the tests named: staging a .pdf, .xlsx, .csv, .eml or .env outside tests/data, and a rename out of tests/data.

**Expected:** The commit is refused and names the file. Made-up files in tests/data and code pass.

**Automated tests:** `test_precommit_guard.py::test_hook_refuses_staged_documents`, `test_precommit_guard.py::test_hook_refuses_a_rename_out_of_tests_data`, `test_precommit_guard.py::test_hook_allows_synthetic_files_and_code`

**Latest:** Owner: not recorded.


<a id="tc-set-05"></a>

### TC-SET-05: The data folder and database are private to the Mac's user, and upgrades are safe to repeat

**Priority:** High · **How:** Automated · **Refs:** PD-1.1a, ARCHITECTURE §11

**Steps**

1. Covered by the tests named.

**Expected:** The folder and database are owner-only, even if they were opened up. The database schema matches the code. Running the upgrade twice changes nothing.

**Automated tests:** `test_database.py::test_data_folder_and_database_are_owner_only`, `test_database.py::test_an_open_data_folder_is_tightened`, `test_database.py::test_migration_matches_the_models`, `test_database.py::test_upgrade_twice_is_harmless`

**Latest:** Owner: not recorded.


## Sign-in and accounts

<a id="tc-sign-01"></a>

### TC-SIGN-01: A signed-out visitor sees only the sign-in page

**Priority:** Critical · **How:** Browser · **Refs:** ARCHITECTURE §10.1, PD-1.9c · **Was:** Sign-in check #1

**Steps**

1. Open http://127.0.0.1:8000 while signed out.

**Expected:** The sign-in page; the address changes to /sign-in. No account details are shown.

**Automated tests:** `test_gate.py::test_every_route_refuses_a_request_with_no_session`, `test_gate.py::test_a_browser_is_sent_to_the_sign_in_page_and_a_script_gets_401`

**Latest:** Claude, Chrome: Pass 11/07/2026. Owner: Pass 11/04/2026 (Safari, Chrome).

<details><summary>Screenshot</summary>

![TC-SIGN-01 in Chrome](screenshots/TC-SIGN-01.png)

</details>


<a id="tc-sign-02"></a>

### TC-SIGN-02: A wrong password and an unknown email get the same message

**Priority:** Critical · **How:** Browser · **Refs:** ARCHITECTURE §10.1 · **Was:** Sign-in check #2, #3

**Steps**

1. Sign in as boss@example.com with a wrong password.
2. Sign in as nobody@example.com with any password.

**Expected:** Both say "The email or password is not right." word for word, and you stay on the sign-in page.

**Automated tests:** `test_auth.py::test_wrong_password_and_unknown_email_get_the_same_refusal`, `test_gate.py::test_wrong_password_and_unknown_email_look_the_same`

**Latest:** Claude, Chrome: Pass 11/07/2026. Owner: Pass 11/04/2026 (Safari, Chrome).

<details><summary>Screenshot</summary>

![TC-SIGN-02 in Chrome](screenshots/TC-SIGN-02.png)

</details>


<a id="tc-sign-03"></a>

### TC-SIGN-03: Signing in opens Home, with the account menu at the top right

**Priority:** High · **How:** Browser · **Refs:** PD-1.9c, DEC-45 · **Was:** Sign-in check #4, #5; Design check #2

**Steps**

1. Sign in as boss@example.com with the right password.
2. Click the person icon at the top right; press Esc; open it again and click elsewhere.

**Expected:** Home opens. The menu shows the email, Change password and Sign out. Esc closes it, and so does a click elsewhere.

**Automated tests:** `test_gate.py::test_sign_in_sets_a_locked_down_cookie_and_opens_the_home_page`

**Latest:** Claude, Chrome: Pass 11/07/2026. Owner: Pass 11/06/2026.

<details><summary>Screenshot</summary>

![TC-SIGN-03 in Chrome](screenshots/TC-SIGN-03.png)

</details>


<a id="tc-sign-04"></a>

### TC-SIGN-04: An admin adds a person and sees the temporary password once

**Priority:** Critical · **How:** Browser · **Refs:** PD-1.9b, ARCHITECTURE §10.1 · **Was:** Sign-in check #6, #7

**Steps**

1. As boss, open People.
2. Add buyer@example.com as a buyer.
3. Click People again.

**Expected:** A 20-character temporary password is shown once. After clicking People again it is gone and cannot be shown again. Reload repeats nothing.

**Automated tests:** `test_gate.py::test_admin_adds_a_person_and_sees_the_temporary_password_once`, `test_gate.py::test_a_reload_after_adding_a_person_repeats_nothing`, `test_gate.py::test_the_shown_password_expires_and_belongs_to_one_admin`

**Latest:** Claude, Chrome: Pass 11/07/2026. Owner: Pass 11/04/2026 (Safari, Chrome).

<details><summary>Screenshot</summary>

![TC-SIGN-04 in Chrome](screenshots/TC-SIGN-04.png)

</details>


<a id="tc-sign-05"></a>

### TC-SIGN-05: A temporary password leads only to Change password, which refuses a weak password

**Priority:** Critical · **How:** Browser · **Refs:** PD-1.9b · **Was:** Sign-in check #8, #9, #10

**Steps**

1. Sign out, then sign in as buyer@example.com with the temporary password.
2. Click Home.
3. Try the new password abc.
4. Choose a good new password of 12 or more characters.

**Expected:** Change password opens, and Home sends you back to it. abc is refused with a reason (the browser asks for 12 or more characters before anything is sent). The good password opens Home, and a buyer has no People link.

**Automated tests:** `test_gate.py::test_a_temporary_password_can_reach_nothing_until_it_is_changed`, `test_auth.py::test_weak_passwords_are_refused`, `test_auth.py::test_changing_the_password_ends_other_sessions_and_clears_the_flag`

**Latest:** Claude, Chrome: Pass 11/07/2026. Owner: Pass 11/04/2026 (Safari, Chrome).

<details><summary>Screenshot</summary>

![TC-SIGN-05 in Chrome](screenshots/TC-SIGN-05.png)

</details>


<a id="tc-sign-06"></a>

### TC-SIGN-06: A buyer cannot open the admin screens

**Priority:** Critical · **How:** Browser · **Refs:** ARCHITECTURE §10.1 · **Was:** Sign-in check #11; PO check #44

**Steps**

1. As a buyer, type /admin/users in the address bar.
2. Type /admin/audit.

**Expected:** Both are refused with a page headed "Admins only" and a Go to Home button (never raw code). The top bar shows no People or Audit link to a buyer.

**Automated tests:** `test_gate.py::test_a_buyer_gets_403_on_the_admin_screens`, `test_gate.py::test_a_refusal_in_the_browser_is_a_page_not_raw_json`, `test_audit_screen.py::test_a_buyer_and_a_stranger_cannot_see_it`

**Latest:** Claude, Chrome: Pass 11/07/2026. Owner: Pass 11/05/2026 (Safari, Chrome), before DEF-002 was found: retest needed.

<details><summary>Screenshot</summary>

![TC-SIGN-06 in Chrome](screenshots/TC-SIGN-06.png)

</details>


<a id="tc-sign-07"></a>

### TC-SIGN-07: A disabled person is cut off and gets the ordinary refusal

**Priority:** Critical · **How:** Browser · **Refs:** PD-1.9b · **Was:** Sign-in check #12

**Steps**

1. As boss, on People, press Disable for the buyer.
2. Try to sign in as the buyer with the right password.
3. As boss, press Enable.

**Expected:** The buyer gets "The email or password is not right." After Enable the buyer can sign in again.

**Automated tests:** `test_auth.py::test_a_disabled_account_gets_the_ordinary_refusal`, `test_gate.py::test_a_disabled_person_is_cut_off_at_once`

**Latest:** Claude, Chrome: Pass 11/07/2026. Owner: Pass 11/04/2026 (Safari, Chrome).

<details><summary>Screenshot</summary>

![TC-SIGN-07 in Chrome](screenshots/TC-SIGN-07.png)

</details>


<a id="tc-sign-08"></a>

### TC-SIGN-08: Five wrong passwords lock the account, even against the right one, until 15 minutes pass or an admin unlocks it

**Priority:** Critical · **How:** Browser · **Refs:** ARCHITECTURE §10.1 · **Was:** Sign-in check #13, #14

**Steps**

1. Sign in as the buyer with a wrong password 5 times.
2. Then use the right password.
3. As boss, press Unlock for the buyer; sign in as the buyer.

**Expected:** "Too many wrong attempts. Try again in 15 minutes." The right password is still refused while locked. After Unlock the buyer signs in.

**Automated tests:** `test_auth.py::test_five_failures_lock_the_account_even_for_the_right_password`, `test_gate.py::test_five_wrong_passwords_lock_the_account_in_the_browser_too`

**Latest:** Claude, Chrome: Pass 11/07/2026. Owner: Pass 11/04/2026 (Safari, Chrome).

<details><summary>Screenshot</summary>

![TC-SIGN-08 in Chrome](screenshots/TC-SIGN-08.png)

</details>


<a id="tc-sign-09"></a>

### TC-SIGN-09: 30 idle minutes sign a person out, even with a page that updates itself left open

**Priority:** High · **How:** Automated · **Refs:** ARCHITECTURE §10.1, DEC-45 · **Was:** Sign-in check #15; Design check #17 (optional, not yet done by hand)

**Steps**

1. Optional by hand: leave Home open and untouched for 31 minutes.
2. Covered by the tests named.

**Expected:** The tab moves to the sign-in page by itself. The page's own updates do not count as activity.

**Automated tests:** `test_auth.py::test_idle_and_absolute_expiry`, `test_web_standards.py::test_a_background_update_does_not_count_as_activity`, `test_web_standards.py::test_a_page_left_open_is_still_signed_out_after_30_idle_minutes`

**Latest:** Owner: not recorded.


<a id="tc-sign-10"></a>

### TC-SIGN-10: The session cookie is locked down, and the last admin cannot be removed

**Priority:** Critical · **How:** Automated · **Refs:** ARCHITECTURE §10.1 · **Was:** Sign-in check, things to look at

**Steps**

1. Covered by the tests named. By hand: in the browser's developer tools, the po_session cookie.

**Expected:** po_session is HttpOnly and SameSite Strict (Secure comes with HTTPS in Phase 6). The token changes at each stage. The last admin cannot be disabled or demoted, and an admin cannot disable themselves.

**Automated tests:** `test_gate.py::test_sign_in_sets_a_locked_down_cookie_and_opens_the_home_page`, `test_gate.py::test_the_session_token_changes_at_each_stage`, `test_auth.py::test_the_last_admin_cannot_be_disabled_or_demoted`, `test_auth.py::test_an_admin_cannot_disable_themselves`

**Latest:** Owner: Pass 11/04/2026 (Safari, Chrome).


<a id="tc-sign-11"></a>

### TC-SIGN-11: Sign out, then sign in again, with no security-token error

**Priority:** Medium · **How:** Browser · **Refs:** DEC-45 · **Was:** Design check #22

**Steps**

1. Person icon, Sign out.
2. Sign in again.

**Expected:** The sign-in page, then Home. No "security token" error.

**Automated tests:** `test_gate.py::test_sign_out_ends_the_session`, `test_gate.py::test_a_new_sign_in_never_reuses_a_token`

**Latest:** Claude, Chrome: Pass 11/07/2026. Owner: Pass 11/06/2026.

<details><summary>Screenshot</summary>

![TC-SIGN-11 in Chrome](screenshots/TC-SIGN-11.png)

</details>


## Vendor import

<a id="tc-vimp-01"></a>

### TC-VIMP-01: A vendor file (Excel or CSV) creates the vendors with their domains

**Priority:** High · **How:** Browser · **Refs:** DEC-44, DEC-47, PD-1.11 · **Was:** PO check #45, #46; QuickBooks check #1

**Steps**

1. Vendors, Import vendors.
2. Choose tests/data/vendors_sample.xlsx and press Import.

**Expected:** The cursor starts on the file chooser. In place, with no page reload: "Done. 3 created" (Acme Fasteners, Northwind Metals, Initech Tools), and the Vendors page lists them with the domains of their emails.

**Automated tests:** `test_vendor_import.py::test_a_new_vendor_is_created_with_its_domains_and_the_domain_of_its_contact_email`, `test_sheets.py::test_vendors_can_be_uploaded_as_an_excel_file`, `test_vendor_import.py::test_the_page_opens_with_the_cursor_on_the_file_chooser_and_offers_a_template`

**Latest:** Claude, Chrome: Pass 11/07/2026. Owner: Pass 11/06/2026 (CSV); the .xlsx file: no problem reported 11/07/2026.

<details><summary>Screenshot</summary>

![TC-VIMP-01 in Chrome](screenshots/TC-VIMP-01.png)

</details>


<a id="tc-vimp-02"></a>

### TC-VIMP-02: Uploading the same vendor file again changes nothing

**Priority:** Critical · **How:** Browser · **Refs:** DEC-44 · **Was:** PO check #47

**Steps**

1. Upload tests/data/vendors_sample.xlsx again.

**Expected:** "0 created, 0 updated, 3 already up to date". Nothing is duplicated.

**Automated tests:** `test_vendor_import.py::test_uploading_the_same_file_again_changes_nothing`

**Latest:** Claude, Chrome: Pass 11/07/2026. Owner: Pass 11/06/2026.

<details><summary>Screenshot</summary>

![TC-VIMP-02 in Chrome](screenshots/TC-VIMP-02.png)

</details>


<a id="tc-vimp-03"></a>

### TC-VIMP-03: Each vendor row needs an email, and an email or domain held by another vendor skips only that row

**Priority:** Critical · **How:** Browser · **Refs:** DEC-44, OPEN-19 · **Was:** PO check #49, #50, #51

**Steps**

1. Upload tests/data/vendors_problems.csv: one row with no contact_email, one whose email is Acme's contact email, one whose email_domains is Northwind's domain, and one good new vendor (Umbrella Freight).

**Expected:** The first three are listed with their reasons ("Contact email is required", "already the contact email of ...", "already belongs to ...") and skipped. Umbrella Freight is created.

**Automated tests:** `test_vendor_import.py::test_the_vendor_name_and_an_email_are_required`, `test_vendor_import.py::test_an_email_already_the_contact_of_another_vendor_is_refused`, `test_vendor_import.py::test_a_domain_owned_by_another_vendor_skips_only_that_row`

**Latest:** Claude, Chrome: Pass 11/07/2026. Owner: Pass 11/06/2026.

<details><summary>Screenshot</summary>

![TC-VIMP-03 in Chrome](screenshots/TC-VIMP-03.png)

</details>


<a id="tc-vimp-04"></a>

### TC-VIMP-04: An existing vendor with a new email gets the new domain, and its other details are kept

**Priority:** High · **How:** Automated · **Refs:** DEC-44 · **Was:** PO check #48

**Steps**

1. Covered by the tests named. By hand: a row for an existing vendor with an email at a new domain.

**Expected:** The vendor is updated, not refused: the new domain is added; existing details are not overwritten.

**Automated tests:** `test_vendor_import.py::test_an_existing_vendor_with_a_new_email_gets_the_new_domain_added`, `test_vendor_import.py::test_blank_contact_details_are_filled_but_existing_ones_are_not_overwritten`

**Latest:** Owner: Pass 11/06/2026.


<a id="tc-vimp-05"></a>

### TC-VIMP-05: An old Excel file (.xls) is refused with what to do

**Priority:** Medium · **How:** Browser · **Refs:** DEC-47 · **Was:** QuickBooks check #2

**Steps**

1. Upload a file saved in the old .xls format.

**Expected:** "This is the old Excel format (.xls) ... Save it as .xlsx or CSV." Nothing changes.

**Automated tests:** `test_sheets.py::test_files_that_are_not_readable_spreadsheets_are_refused`

**Latest:** Claude, Chrome: Pass 11/07/2026. Owner: No problem reported 11/07/2026 (QuickBooks check step 2).

<details><summary>Screenshot</summary>

![TC-VIMP-05 in Chrome](screenshots/TC-VIMP-05.png)

</details>


## Vendors

<a id="tc-ven-01"></a>

### TC-VEN-01: A vendor is added on its own page and lands on its vendor page

**Priority:** High · **How:** Browser · **Refs:** REQ-PO-01, PD-1.2, DEC-45 · **Was:** PO check #1, #2; Design check #3

**Steps**

1. Vendors, Add a vendor.
2. Name Contoso Bearings, domains contoso.example, mail.contoso.example; Add vendor.

**Expected:** The cursor starts in Name. You land on the vendor page showing both domains.

**Automated tests:** `test_orders_web.py::test_a_vendor_is_added_and_listed_with_its_domains`, `test_orders_web.py::test_each_new_form_page_has_the_cursor_ready_in_one_box`

**Latest:** Claude, Chrome: Pass 11/07/2026. Owner: Pass 11/05/2026 (Safari, Chrome).

<details><summary>Screenshot</summary>

![TC-VEN-01 in Chrome](screenshots/TC-VEN-01.png)

</details>


<a id="tc-ven-02"></a>

### TC-VEN-02: A vendor name that already exists, in any case, is refused and the boxes keep what was typed

**Priority:** High · **How:** Browser · **Refs:** PD-1.2 · **Was:** PO check #3

**Steps**

1. Add a vendor named contoso bearings (other case).

**Expected:** "A vendor with that name already exists", and every box keeps what you typed.

**Automated tests:** `test_orders.py::test_a_vendor_name_is_unique_ignoring_case`, `test_orders_web.py::test_a_refused_vendor_keeps_what_was_typed_and_says_why`

**Latest:** Claude, Chrome: Pass 11/07/2026. Owner: Pass 11/05/2026 (Safari, Chrome).

<details><summary>Screenshot</summary>

![TC-VEN-02 in Chrome](screenshots/TC-VEN-02.png)

</details>


<a id="tc-ven-03"></a>

### TC-VEN-03: A domain belongs to one vendor only, so a reply can never match the wrong vendor

**Priority:** Critical · **How:** Browser · **Refs:** REQ-ACK-05, PD-1.2 · **Was:** Design check #9

**Steps**

1. On Contoso Bearings' page, add the domain northwind.example (Northwind Metals' domain, from the vendor file).

**Expected:** A red message says it already belongs to Northwind Metals, shown in view. Nothing is added.

**Automated tests:** `test_orders.py::test_a_domain_belongs_to_one_vendor`, `test_orders_web.py::test_adding_a_domain_already_held_in_any_case_says_so`

**Latest:** Claude, Chrome: Pass 11/07/2026. Owner: Pass 11/06/2026.

<details><summary>Screenshot</summary>

![TC-VEN-03 in Chrome](screenshots/TC-VEN-03.png)

</details>


<a id="tc-ven-04"></a>

### TC-VEN-04: Domains are checked, added and removed on the vendor page

**Priority:** Medium · **How:** Browser · **Refs:** PD-1.2 · **Was:** PO check #4, #4b

**Steps**

1. On Contoso Bearings' page add the domain not a domain.
2. Add contoso.co.uk, then press it to remove it.
3. Add CONTOSO.example (other case).

**Expected:** not a domain is refused with a reason. contoso.co.uk appears, then goes. CONTOSO.example says "contoso.example is already added to this vendor".

**Automated tests:** `test_orders.py::test_a_domain_must_look_like_one`, `test_orders_web.py::test_domains_are_added_and_removed_from_the_vendor_page`

**Latest:** Claude, Chrome: Pass 11/07/2026. Owner: Pass 11/05/2026 (Safari, Chrome).

<details><summary>Screenshot</summary>

![TC-VEN-04 in Chrome](screenshots/TC-VEN-04.png)

</details>


<a id="tc-ven-05"></a>

### TC-VEN-05: Saving a vendor shows a Saved note and the page does not jump

**Priority:** Medium · **How:** Browser · **Refs:** DEC-45 · **Was:** PO check #4b; Design check #8

**Steps**

1. On Contoso Bearings' page, change Notes and press Save.

**Expected:** A green Saved note that fades after about 3 seconds. The page stays where it was. The change is in the audit trail.

**Automated tests:** `test_orders_web.py::test_a_save_shows_a_fixed_saved_note_that_cannot_be_forged`, `test_orders_web.py::test_a_vendor_can_be_edited_and_the_change_is_audited_with_who_and_where`

**Latest:** Claude, Chrome: Pass 11/07/2026. Owner: Pass 11/06/2026.

<details><summary>Screenshot</summary>

![TC-VEN-05 in Chrome](screenshots/TC-VEN-05.png)

</details>


<a id="tc-ven-06"></a>

### TC-VEN-06: A vendor with no domain is marked, because its replies cannot be matched

**Priority:** High · **How:** Browser · **Refs:** REQ-ACK-05 · **Was:** PO check #5

**Steps**

1. Add a vendor Hooli Plastics with no domains and no email.
2. Look at the Vendors list.

**Expected:** It is listed, marked "no domain: replies cannot be matched".

**Automated tests:** `test_orders_web.py::test_a_vendor_is_added_and_listed_with_its_domains`

**Latest:** Claude, Chrome: Pass 11/07/2026. Owner: Pass 11/05/2026 (Safari, Chrome).

<details><summary>Screenshot</summary>

![TC-VEN-06 in Chrome](screenshots/TC-VEN-06.png)

</details>


<a id="tc-ven-07"></a>

### TC-VEN-07: The vendor list searches as you type, sorts by heading, and / jumps to search

**Priority:** Medium · **How:** Browser · **Refs:** DEC-45 · **Was:** Design check #5, #6, #7

**Steps**

1. On Vendors type north in the search box and wait.
2. Press Back.
3. Click a blank part of the page and press /.
4. Click the Vendor heading twice.

**Expected:** Only Northwind shows and the address has ?q=north; Back shows the full list. / puts the cursor in search. The heading shows ▲ then ▼ and the order reverses.

**Automated tests:** `test_vendor_import.py::test_vendor_search_finds_by_name_or_domain_and_treats_wildcards_as_text`, `test_web_standards.py::test_a_heading_sorts_and_a_second_click_reverses`

**Latest:** Claude, Chrome: Pass 11/07/2026. Owner: Pass 11/06/2026.

<details><summary>Screenshot</summary>

![TC-VEN-07 in Chrome](screenshots/TC-VEN-07.png)

</details>


<a id="tc-ven-08"></a>

### TC-VEN-08: Markup typed into a vendor name is shown as text, never run

**Priority:** Critical · **How:** Browser · **Refs:** Security · **Was:** PO check #16

**Steps**

1. Add a vendor named <b>Bold</b>.
2. Look at the vendor list and the vendor page.

**Expected:** The name shows literally as <b>Bold</b>, not in bold.

**Automated tests:** `test_vendor_import.py::test_markup_in_a_vendor_name_is_shown_as_text`, `test_orders_web.py::test_what_people_type_is_escaped_everywhere_it_is_shown`

**Latest:** Claude, Chrome: Pass 11/07/2026. Owner: Pass 11/05/2026 (Safari, Chrome).

<details><summary>Screenshot</summary>

![TC-VEN-08 in Chrome](screenshots/TC-VEN-08.png)

</details>


## Purchase orders

<a id="tc-po-01"></a>

### TC-PO-01: A PO entered by hand keeps exact numbers and starts as Sent

**Priority:** Critical · **How:** Browser · **Refs:** REQ-PO-02, PD-1.2 · **Was:** PO check #6, #8

**Steps**

1. Purchase orders, New purchase order.
2. PO number PO-1001, vendor Acme (pick it from the list), currency usd, date sent a few days ago, line 1: Bolt, quantity 10.5, unit EA, price 2.5. Create.

**Expected:** The cursor starts in PO number; Buyer is your email. The PO page shows PO PO-1001, currency USD, the line as 10.5 EA at 2.50 USD, status Sent.

**Automated tests:** `test_orders_web.py::test_a_po_is_entered_and_its_page_shows_lines_in_exact_numbers`, `test_orders.py::test_a_po_is_created_with_its_lines_in_order`

**Latest:** Claude, Chrome: Pass 11/07/2026. Owner: Pass 11/05/2026 (Safari, Chrome).

<details><summary>Screenshot</summary>

![TC-PO-01 in Chrome](screenshots/TC-PO-01.png)

</details>


<a id="tc-po-02"></a>

### TC-PO-02: Reload after creating a PO never creates a second one

**Priority:** Critical · **How:** Browser · **Refs:** DEC-45 · **Was:** PO check #9; Design check #13

**Steps**

1. On the new PO's page press Reload a few times.
2. Open Purchase orders and search PO-1001.

**Expected:** The page just reloads. There is exactly one PO-1001.

**Automated tests:** `test_orders_web.py::test_reloading_after_creating_a_po_does_not_create_another`

**Latest:** Claude, Chrome: Pass 11/07/2026. Owner: Pass 11/06/2026.

<details><summary>Screenshot</summary>

![TC-PO-02 in Chrome](screenshots/TC-PO-02.png)

</details>


<a id="tc-po-03"></a>

### TC-PO-03: A wrong line value is refused naming the line and field, and every box keeps what was typed

**Priority:** High · **How:** Browser · **Refs:** PD-1.2, DEC-45 · **Was:** PO check #7, #27; Design check #14

**Steps**

1. New purchase order PO-1002, vendor Acme, line 1 with quantity 0. Create.

**Expected:** "Line 1: Quantity must be more than zero" at the top, with the cursor on it. Every box keeps its value. Back and Forward give no 'page not found'.

**Automated tests:** `test_orders_web.py::test_a_refused_po_keeps_every_box_and_lists_every_problem`, `test_orders.py::test_a_bad_line_value_is_refused_naming_the_line_and_field`

**Latest:** Claude, Chrome: Pass 11/07/2026. Owner: Pass 11/06/2026.

<details><summary>Screenshot</summary>

![TC-PO-03 in Chrome](screenshots/TC-PO-03.png)

</details>


<a id="tc-po-04"></a>

### TC-PO-04: More than 4 decimal places is refused; nothing is ever rounded

**Priority:** Critical · **How:** Browser · **Refs:** ARCHITECTURE §4 (exact numbers) · **Was:** PO check #10

**Steps**

1. New purchase order with a quantity of 1.00001. Create.

**Expected:** Refused: at most 4 decimal places are kept; nothing is rounded.

**Automated tests:** `test_ledger.py::test_too_many_decimal_places_are_refused_not_rounded`, `test_ledger.py::test_floats_and_other_inexact_values_are_refused`, `test_orders.py::test_the_numbers_in_a_file_are_exact`

**Latest:** Claude, Chrome: Pass 11/07/2026. Owner: Pass 11/05/2026 (Safari, Chrome).

<details><summary>Screenshot</summary>

![TC-PO-04 in Chrome](screenshots/TC-PO-04.png)

</details>


<a id="tc-po-05"></a>

### TC-PO-05: The vendor box searches as you type, and a name not picked from the list is refused

**Priority:** High · **How:** Browser · **Refs:** PD-1.12 · **Was:** PO check #52, #53; Design check #12, #13

**Steps**

1. New purchase order: type acm in Vendor; press the down arrow and Enter.
2. Then type Acme without picking it, and press Create.

**Expected:** A short list appears under the box and Enter picks Acme Fasteners. Unpicked, the box says "Pick a vendor from the list."

**Automated tests:** `test_vendor_import.py::test_the_po_form_and_filters_use_the_search_box_not_a_long_list`

**Latest:** Claude, Chrome: Pass 11/07/2026. Owner: Pass 11/06/2026.

<details><summary>Screenshot</summary>

![TC-PO-05 in Chrome](screenshots/TC-PO-05.png)

</details>


<a id="tc-po-06"></a>

### TC-PO-06: An existing PO number is refused in any case

**Priority:** Critical · **How:** Automated · **Refs:** REQ-PO-02

**Steps**

1. Covered by the tests named. By hand: a new PO with the number po-1001.

**Expected:** Refused; the existing PO is not touched.

**Automated tests:** `test_orders_web.py::test_an_existing_po_number_is_refused_in_any_case`, `test_ledger.py::test_a_po_number_is_unique`

**Latest:** Owner: not recorded.


<a id="tc-po-07"></a>

### TC-PO-07: The PO list filters as you choose, reads dates as MM/DD/YYYY, and Clear resets it

**Priority:** High · **How:** Browser · **Refs:** DEC-45, PD-1.5 · **Was:** PO check #15, #28, #29; Design check #11

**Steps**

1. Purchase orders: type 1001 in Search; choose a Status; pick a Sent from date from the calendar.
2. Press Clear.
3. Search for %.

**Expected:** The cursor starts in Search. The list follows each choice with no Show button. Dates read like 11/04/2026. Clear resets. % finds nothing ("No purchase orders match").

**Automated tests:** `test_web_standards.py::test_filters_update_the_list_in_place_and_keep_the_sort`, `test_orders_web.py::test_the_list_shows_open_and_closed_pos_and_searches_literally`, `test_expected.py::test_sent_dates_filter_and_a_wrong_value_is_reported_not_trusted`

**Latest:** Claude, Chrome: Pass 11/07/2026. Owner: Pass 11/06/2026.

<details><summary>Screenshot</summary>

![TC-PO-07 in Chrome](screenshots/TC-PO-07.png)

</details>


<a id="tc-po-08"></a>

### TC-PO-08: The PO list shows 50 a page and sorts by any heading

**Priority:** Medium · **How:** Automated · **Refs:** DEC-45

**Steps**

1. Covered by the tests named; by hand with more than 50 POs.

**Expected:** "1–50 of N"; Next and Back move between pages; a heading sorts and a second click reverses; empty values sort last.

**Automated tests:** `test_web_standards.py::test_the_po_list_shows_50_a_page_and_says_how_many`, `test_web_standards.py::test_a_heading_sorts_and_a_second_click_reverses`, `test_web_standards.py::test_empty_values_sort_last_whichever_way`

**Latest:** Owner: not recorded.


<a id="tc-po-09"></a>

### TC-PO-09: A buyer, not only an admin, can use Vendors and Purchase orders

**Priority:** High · **How:** Browser · **Refs:** ARCHITECTURE §10.1 · **Was:** PO check #17

**Steps**

1. Sign in as a buyer; open Vendors, Purchase orders and New purchase order.

**Expected:** Every page opens and works for the buyer.

**Automated tests:** `test_orders_web.py::test_a_buyer_not_only_an_admin_can_use_them`

**Latest:** Claude, Chrome: Pass 11/07/2026. Owner: Pass 11/05/2026 (Safari, Chrome).

<details><summary>Screenshot</summary>

![TC-PO-09 in Chrome](screenshots/TC-PO-09.png)

</details>


## Purchase order import

<a id="tc-pimp-01"></a>

### TC-PIMP-01: A PO file imports every PO and line, and the template downloads

**Priority:** High · **How:** Browser · **Refs:** REQ-PO-04, DEC-47 · **Was:** PO check #11, #12; Design check #10

**Steps**

1. Purchase orders, Import CSV or Excel. Press Download a template.
2. Choose tests/data/purchase_orders_sample.csv and press Import.

**Expected:** The cursor is on the file chooser; the template downloads as a .csv file. You land on the PO list with "Imported 2 purchase orders" (PO-2001 and PO-2002).

**Automated tests:** `test_orders_web.py::test_a_csv_file_is_uploaded_and_the_person_lands_on_the_list`, `test_orders.py::test_the_sample_file_imports_every_po_and_line`, `test_orders_web.py::test_the_template_downloads_as_csv_and_names_every_column`

**Latest:** Claude, Chrome: Pass 11/07/2026. Owner: Pass 11/06/2026.

<details><summary>Screenshot</summary>

![TC-PIMP-01 in Chrome](screenshots/TC-PIMP-01.png)

</details>


<a id="tc-pimp-02"></a>

### TC-PIMP-02: Importing the same file again adds nothing and overwrites nothing

**Priority:** Critical · **How:** Browser · **Refs:** REQ-PO-04 · **Was:** PO check #13; Design check #10

**Steps**

1. Import tests/data/purchase_orders_sample.csv again.

**Expected:** The page stays on Import and lists "PO number PO-2001 already exists. Nothing is overwritten." Nothing is added.

**Automated tests:** `test_orders.py::test_an_existing_po_number_is_never_overwritten`

**Latest:** Claude, Chrome: Pass 11/07/2026. Owner: Pass 11/06/2026.

<details><summary>Screenshot</summary>

![TC-PIMP-02 in Chrome](screenshots/TC-PIMP-02.png)

</details>


<a id="tc-pimp-03"></a>

### TC-PIMP-03: One bad row rejects the whole file, naming the row

**Priority:** Critical · **How:** Browser · **Refs:** REQ-PO-04 · **Was:** PO check #14

**Steps**

1. Import tests/data/purchase_orders_bad_row.csv (a made-up file whose row 3 has the quantity abc).

**Expected:** "Row 3: Quantity: 'abc' is not a number" (the row named), and nothing is imported.

**Automated tests:** `test_orders.py::test_one_bad_row_rejects_the_whole_file_and_every_problem_is_listed`, `test_orders.py::test_a_file_that_fails_midway_leaves_nothing_behind`, `test_orders_web.py::test_a_bad_file_imports_nothing_and_the_page_lists_the_rows`

**Latest:** Claude, Chrome: Pass 11/07/2026. Owner: Pass 11/05/2026 (Safari, Chrome).

<details><summary>Screenshot</summary>

![TC-PIMP-03 in Chrome](screenshots/TC-PIMP-03.png)

</details>


<a id="tc-pimp-04"></a>

### TC-PIMP-04: A vendor not in the app is refused, never guessed or created

**Priority:** Critical · **How:** Automated · **Refs:** DEC-09: never guess

**Steps**

1. Covered by the tests named. By hand: a row whose vendor is not in the app.

**Expected:** The row is listed as an unknown vendor; nothing is imported and no vendor is created.

**Automated tests:** `test_orders.py::test_an_unknown_vendor_is_refused_not_guessed_or_created`

**Latest:** Owner: not recorded.


<a id="tc-pimp-05"></a>

### TC-PIMP-05: Excel files with real date cells, Windows-1252 accents and formulas are read safely

**Priority:** High · **How:** Automated · **Refs:** DEC-47

**Steps**

1. Covered by the tests named.

**Expected:** Dates in Excel cells read correctly; accents from QuickBooks or older Excel are kept; a formula is stored as text and never run.

**Automated tests:** `test_sheets.py::test_purchase_orders_can_be_uploaded_as_an_excel_file_with_real_date_cells`, `test_orders.py::test_a_file_saved_by_quickbooks_or_old_excel_in_windows_1252_is_read_as_it_is`, `test_orders.py::test_a_spreadsheet_formula_is_stored_as_text_and_never_run`

**Latest:** Owner: not recorded.


<a id="tc-pimp-06"></a>

### TC-PIMP-06: The QuickBooks report uploaded on Import CSV or Excel becomes the QuickBooks preview

**Priority:** High · **How:** Browser · **Refs:** DEC-46, PD-1.10a.5, DEF-001 · **Was:** QuickBooks check #15 (added after DEF-001)

**Steps**

1. Purchase orders, Import CSV or Excel (the button beside Load from QuickBooks).
2. Choose tests/data/quickbooks_open_pos_110526.csv and press Import.

**Expected:** No list of missing columns. The QuickBooks preview, headed "This file is the QuickBooks Open POs report, so it is read here ...", with nothing changed yet and the cursor on Create.

**Automated tests:** `test_quickbooks.py::test_the_report_uploaded_on_the_import_page_becomes_the_quickbooks_preview`, `test_quickbooks.py::test_a_file_that_is_neither_still_gets_the_import_pages_own_message`

**Latest:** Claude, Chrome: Pass 11/07/2026. Owner: Not yet run.

<details><summary>Screenshot</summary>

![TC-PIMP-06 in Chrome](screenshots/TC-PIMP-06.png)

</details>


## QuickBooks starting load

<a id="tc-qb-01"></a>

### TC-QB-01: The preview shows what the file would do and changes nothing

**Priority:** Critical · **How:** Browser · **Refs:** DEC-46 · **Was:** QuickBooks check #3, #4, #5

**Steps**

1. Purchase orders, Load from QuickBooks.
2. Choose tests/data/quickbooks_open_pos_110526.csv and press Show what it would do.
3. In another tab, open Purchase orders.

**Expected:** "Nothing has changed yet". 3 purchase orders with 6 lines to create; 1 waiting for a vendor (Globex Wire); 1 line partly received; 1 price rounded (10.00 ÷ 3 = 3.3333); export date 11/05/2026 from the file name; the cursor on Create 3 purchase orders. The other tab shows no new POs.

**Automated tests:** `test_quickbooks.py::test_the_page_previews_then_creates_and_reload_cannot_repeat_it`, `test_quickbooks.py::test_the_plan_reads_lines_and_skips_headings_and_totals`

**Latest:** Claude, Chrome: Pass 11/07/2026. Owner: Step 4 failed 11/07/2026: DEF-001.

<details><summary>Screenshot</summary>

![TC-QB-01 in Chrome](screenshots/TC-QB-01.png)

</details>


<a id="tc-qb-02"></a>

### TC-QB-02: Create loads all of them or none, and lands on the PO list

**Priority:** Critical · **How:** Browser · **Refs:** DEC-46 · **Was:** QuickBooks check #6

**Steps**

1. On the preview press Create 3 purchase orders.

**Expected:** "Loaded 3 purchase orders from QuickBooks. 1 is waiting for its vendor ..."; 5001, 5002 and 5004 are listed.

**Automated tests:** `test_quickbooks.py::test_the_load_creates_the_pos_as_the_owner_decided`, `test_quickbooks.py::test_a_failure_part_way_creates_nothing`

**Latest:** Claude, Chrome: Pass 11/07/2026. Owner: Not yet run.

<details><summary>Screenshot</summary>

![TC-QB-02 in Chrome](screenshots/TC-QB-02.png)

</details>


<a id="tc-qb-03"></a>

### TC-QB-03: Back and Create again can never load the file twice

**Priority:** Critical · **How:** Browser · **Refs:** DEC-46 · **Was:** QuickBooks check #7

**Steps**

1. After Create, press the browser's Back, then Create again if it shows.

**Expected:** "That preview has expired or was already used." Still three POs, never six.

**Automated tests:** `test_quickbooks.py::test_the_page_previews_then_creates_and_reload_cannot_repeat_it`, `test_quickbooks.py::test_one_persons_preview_cannot_be_used_by_another`, `test_quickbooks.py::test_a_preview_expires_after_15_minutes`

**Latest:** Claude, Chrome: Pass 11/07/2026. Owner: Not yet run.

<details><summary>Screenshot</summary>

![TC-QB-03 in Chrome](screenshots/TC-QB-03.png)

</details>


<a id="tc-qb-04"></a>

### TC-QB-04: A loaded PO keeps the QuickBooks date, the customer, and what was already received

**Priority:** High · **How:** Browser · **Refs:** DEC-46 · **Was:** QuickBooks check #8

**Steps**

1. Open PO 5002.

**Expected:** Date sent: not set; PO date (QuickBooks): 10/22/2026. The line reads "Sheet:Alu 2mm · for Café Racing" (accent right), status Partially shipped. History has "Received per QuickBooks export of 11/05/2026".

**Automated tests:** `test_quickbooks.py::test_the_po_page_shows_the_quickbooks_date_the_customer_and_the_history`

**Latest:** Claude, Chrome: Pass 11/07/2026. Owner: Not yet run.

<details><summary>Screenshot</summary>

![TC-QB-04 in Chrome](screenshots/TC-QB-04.png)

</details>


<a id="tc-qb-05"></a>

### TC-QB-05: A unit price that cannot be exact is rounded to 4 places, and the line's history says so

**Priority:** Critical · **How:** Browser · **Refs:** DEC-46 · **Was:** QuickBooks check #9

**Steps**

1. Open PO 5001, Timeline by line, line 2.

**Expected:** "Unit price 3.3333 worked out from the QuickBooks total 10.00 ÷ 3, rounded (export of 11/05/2026)". The lines are Bolts, Nuts, Washers in file order, unit EA.

**Automated tests:** `test_quickbooks.py::test_a_unit_price_is_exact_or_rounded_to_four_places`

**Latest:** Claude, Chrome: Pass 11/07/2026. Owner: Not yet run.

<details><summary>Screenshot</summary>

![TC-QB-05 in Chrome](screenshots/TC-QB-05.png)

</details>


<a id="tc-qb-06"></a>

### TC-QB-06: Loaded POs are not flagged for an acknowledgement the app never had the chance to see

**Priority:** High · **How:** Browser · **Refs:** DEC-46 · **Was:** QuickBooks check #10

**Steps**

1. Go Home after the load.

**Expected:** The loaded POs are not listed as waiting for an acknowledgement.

**Automated tests:** `test_quickbooks.py::test_loaded_pos_are_not_flagged_for_an_acknowledgement_the_app_never_saw`

**Latest:** Claude, Chrome: Pass 11/07/2026. Owner: Not yet run.

<details><summary>Screenshot</summary>

![TC-QB-06 in Chrome](screenshots/TC-QB-06.png)

</details>


<a id="tc-qb-07"></a>

### TC-QB-07: Loading the same file again creates nothing; adding the waiting vendor lets its PO in

**Priority:** Critical · **How:** Browser · **Refs:** DEC-46 · **Was:** QuickBooks check #11, #12

**Steps**

1. Load the same file again.
2. Add the vendor Globex Wire (any email), load the file again and Create.

**Expected:** First: "Nothing new to create", 3 already in the app, Globex still waiting, no Create button. Then: 1 to create (5003); after Create there are four POs.

**Automated tests:** `test_quickbooks.py::test_loading_again_changes_nothing_and_a_new_vendor_lets_a_waiting_po_in`, `test_quickbooks.py::test_a_po_number_already_in_the_app_is_never_changed`

**Latest:** Claude, Chrome: Pass 11/07/2026. Owner: Not yet run.

<details><summary>Screenshot</summary>

![TC-QB-07 in Chrome](screenshots/TC-QB-07.png)

</details>

<details><summary>Screenshot 2</summary>

![TC-QB-07 in Chrome](screenshots/TC-QB-07-2.png)

</details>


<a id="tc-qb-08"></a>

### TC-QB-08: A file that is not the report is refused naming what is missing; the app's own file is pointed to Import

**Priority:** Medium · **How:** Browser · **Refs:** DEC-46, PD-1.10a.5 · **Was:** QuickBooks check #13, #16

**Steps**

1. On Load from QuickBooks upload the vendor file.
2. Upload the PO template (Import CSV or Excel, Download a template).

**Expected:** First: "This does not look like the QuickBooks Open POs report: it has no 'date', 'num', ... column." Second: "This is a purchase order file in the app's own import format ..." with a Go to Import CSV or Excel button that opens that page.

**Automated tests:** `test_quickbooks.py::test_a_file_that_is_not_the_report_is_refused_naming_what_is_missing`, `test_quickbooks.py::test_the_apps_own_import_file_on_the_quickbooks_page_says_where_it_goes`, `test_quickbooks.py::test_a_hostile_or_wrong_file_is_a_clear_message`

**Latest:** Claude, Chrome: Pass 11/07/2026. Owner: Not yet run.

<details><summary>Screenshot</summary>

![TC-QB-08 in Chrome](screenshots/TC-QB-08.png)

</details>

<details><summary>Screenshot 2</summary>

![TC-QB-08 in Chrome](screenshots/TC-QB-08-2.png)

</details>


<a id="tc-qb-09"></a>

### TC-QB-09: The company's real export previews with counts that match QuickBooks

**Priority:** Critical · **How:** Owner only · **Refs:** DEC-46, DEC-13 · **Was:** QuickBooks check #14

**Steps**

1. With the company's own export (it stays in real-docs/ on this Mac), Load from QuickBooks, Show what it would do.
2. Compare the counts with QuickBooks. Press Cancel unless this is the go-live load.

**Expected:** The counts of POs, lines, partly received lines and rounded prices match QuickBooks; 0 problems. Write no names or figures anywhere in the repository.

**Latest:** Owner: Not yet run (Claude's dry run 11/06/2026: 0 problems; counts only).


## Entries by hand

<a id="tc-evt-01"></a>

### TC-EVT-01: A short confirmation at another price is recorded and the purchaser is told to update the PO

**Priority:** Critical · **How:** Browser · **Refs:** REQ-ACK, DEC-36, PD-1.4 · **Was:** PO check #18, #19

**Steps**

1. Open PO-1001 (10.5 ordered at 2.50), Record acknowledgement.
2. Confirmed quantity 6, price 2.75, promised date in the future. Record.

**Expected:** The cursor starts in the first Confirmed quantity. Back on the PO page the line shows Acknowledged and the promised date, "The vendor's price 2.75 differs from the PO price 2.5", and "confirmed 6, not 10.5. Update the original purchase order".

**Automated tests:** `test_entries_web.py::test_a_short_confirmation_tells_the_purchaser_to_update_the_original_po`, `test_events.py::test_a_different_price_is_flagged_next_to_the_line`, `test_status.py::test_a_line_confirmed_short_never_closes_by_itself`

**Latest:** Claude, Chrome: Pass 11/07/2026. Owner: Pass 11/05/2026 (Safari, Chrome).

<details><summary>Screenshot</summary>

![TC-EVT-01 in Chrome](screenshots/TC-EVT-01.png)

</details>


<a id="tc-evt-02"></a>

### TC-EVT-02: A later promised date is shown as a slip

**Priority:** High · **How:** Browser · **Refs:** DEC-36 · **Was:** PO check #20

**Steps**

1. Record a second acknowledgement on PO-1001 with a later promised date.

**Expected:** Both dates are shown, joined by an arrow, with a red slipped label on the later one.

**Automated tests:** `test_entries_web.py::test_a_later_promised_date_is_shown_as_a_slip`, `test_events.py::test_promised_dates_are_kept_and_a_later_date_is_a_slip`

**Latest:** Claude, Chrome: Pass 11/07/2026. Owner: Pass 11/05/2026 (Safari, Chrome).

<details><summary>Screenshot</summary>

![TC-EVT-02 in Chrome](screenshots/TC-EVT-02.png)

</details>


<a id="tc-evt-03"></a>

### TC-EVT-03: Shipments move a line from Partially shipped to Shipped, with who entered them

**Priority:** Critical · **How:** Browser · **Refs:** DEC-36, PD-1.4 · **Was:** PO check #21, #22, #31

**Steps**

1. Record shipment on PO-1001: carrier UPS, tracking 1Z999AA10123456784, quantity 3.
2. Record another shipment of 3.

**Expected:** First the status is Partially shipped, then Shipped. History lists each shipment with who entered it, and Track with the carrier opens UPS's own page in a new tab.

**Automated tests:** `test_events.py::test_a_partial_then_full_shipment_moves_the_line`, `test_expected.py::test_known_carriers_get_a_link_to_their_own_page`, `test_entries_web.py::test_a_po_goes_from_sent_to_closed_through_the_pages`

**Latest:** Claude, Chrome: Pass 11/07/2026. Owner: Pass 11/05/2026 (Safari, Chrome).

<details><summary>Screenshot</summary>

![TC-EVT-03 in Chrome](screenshots/TC-EVT-03.png)

</details>


<a id="tc-evt-04"></a>

### TC-EVT-04: An invoice starts at the PO price; a short-confirmed line stays open until someone settles it

**Priority:** Critical · **How:** Browser · **Refs:** DEC-36 · **Was:** PO check #23

**Steps**

1. Record invoice on PO-1001: number INV-1, quantity 6. Record.

**Expected:** The price box starts at the PO price. The line still shows Shipped with 4.5 open, because it was confirmed short; the update-the-PO note stays.

**Automated tests:** `test_entries_web.py::test_the_invoice_form_starts_with_the_price_on_the_po`, `test_status.py::test_a_line_confirmed_short_never_closes_by_itself`, `test_events.py::test_a_partial_invoice_closes_only_what_it_covers`

**Latest:** Claude, Chrome: Pass 11/07/2026. Owner: Pass 11/05/2026 (Safari, Chrome).

<details><summary>Screenshot</summary>

![TC-EVT-04 in Chrome](screenshots/TC-EVT-04.png)

</details>


<a id="tc-evt-05"></a>

### TC-EVT-05: Over-billing and a price above the PO are flagged and still recorded

**Priority:** Critical · **How:** Browser · **Refs:** DEC-36 · **Was:** PO check #24

**Steps**

1. Record invoice INV-2 for quantity 20 at price 3.

**Expected:** Recorded, with "Invoiced ... more than the 10.5 ordered" and "above the PO price". The line becomes Closed.

**Automated tests:** `test_entries_web.py::test_a_different_price_and_over_billing_are_flagged_and_still_recorded`, `test_events.py::test_over_billing_is_flagged_but_still_recorded`

**Latest:** Claude, Chrome: Pass 11/07/2026. Owner: Pass 11/05/2026 (Safari, Chrome).

<details><summary>Screenshot</summary>

![TC-EVT-05 in Chrome](screenshots/TC-EVT-05.png)

</details>


<a id="tc-evt-06"></a>

### TC-EVT-06: Removing an entry needs a reason and undoes its effect

**Priority:** High · **How:** Browser · **Refs:** NFR-03 · **Was:** PO check #25

**Steps**

1. In History, press Remove on INV-2 with the reason empty.
2. Give a reason and press Remove.

**Expected:** Without a reason the browser asks for one. With one, INV-2 goes, its notes clear and the line returns to Shipped. The removal is audited.

**Automated tests:** `test_entries_web.py::test_an_entry_is_removed_with_a_reason_and_its_effect_goes`, `test_events.py::test_removal_is_audited_with_what_the_entry_said_and_why`

**Latest:** Claude, Chrome: Pass 11/07/2026. Owner: Pass 11/05/2026 (Safari, Chrome).

<details><summary>Screenshot</summary>

![TC-EVT-06 in Chrome](screenshots/TC-EVT-06.png)

</details>


<a id="tc-evt-07"></a>

### TC-EVT-07: Reload after recording or removing anything repeats nothing

**Priority:** Critical · **How:** Automated · **Refs:** DEC-45 · **Was:** PO check #26

**Steps**

1. Covered by the tests named. By hand: press Reload after each entry above.

**Expected:** Nothing is recorded or removed a second time.

**Automated tests:** `test_entries_web.py::test_recording_then_reloading_repeats_nothing`, `test_web_standards.py::test_a_refused_form_sent_by_htmx_is_kept_out_of_the_history`

**Latest:** Owner: Pass 11/05/2026 (Safari, Chrome).


<a id="tc-evt-08"></a>

### TC-EVT-08: A promised date in the past makes the line late, on the PO, in the list and in Late only

**Priority:** High · **How:** Browser · **Refs:** REQ-DB, DEC-36 · **Was:** PO check #30

**Steps**

1. On PO-2001, record an acknowledgement promising a date in the past.
2. Open Purchase orders and tick Late only.

**Expected:** The PO page shows the line as N days late in red; the list says "1 late, up to N days"; Late only finds the PO.

**Automated tests:** `test_expected.py::test_a_late_line_is_flagged_on_the_po_page_and_the_list_and_a_filter_finds_it`, `test_expected.py::test_a_line_is_late_the_day_after_its_date_and_not_on_the_day`

**Latest:** Claude, Chrome: Pass 11/07/2026. Owner: Pass 11/05/2026 (Safari, Chrome).

<details><summary>Screenshot</summary>

![TC-EVT-08 in Chrome](screenshots/TC-EVT-08.png)

</details>


<a id="tc-evt-09"></a>

### TC-EVT-09: Settling by hand needs a reason, stays in place, and can be reopened with a reason

**Priority:** High · **How:** Browser · **Refs:** PD-1.5, DEC-45 · **Was:** PO check #32, #33, #34; Design check #15

**Steps**

1. On PO-2002, open Settle lines by hand; choose Cancelled for a line; press Settle with no reason, then with a reason.
2. Press Reopen on that line with a reason.

**Expected:** Without a reason the browser asks for one. With one, the button shows it is working, the line shows Cancelled with "Settled by hand", and the page stays where it was. Reopen returns the line to its earlier status.

**Automated tests:** `test_closing.py::test_a_reason_is_required_and_a_choice_too`, `test_closing.py::test_reopening_needs_a_reason_and_puts_the_line_back`, `test_closing.py::test_a_closed_line_cannot_be_settled_again_and_is_not_offered`

**Latest:** Claude, Chrome: Pass 11/07/2026. Owner: Pass 11/06/2026.

<details><summary>Screenshot</summary>

![TC-EVT-09 in Chrome](screenshots/TC-EVT-09.png)

</details>

<details><summary>Screenshot 2</summary>

![TC-EVT-09 in Chrome](screenshots/TC-EVT-09-2.png)

</details>


<a id="tc-evt-10"></a>

### TC-EVT-10: A line's timeline keeps everything that happened, including what was taken back

**Priority:** High · **How:** Browser · **Refs:** NFR-03 · **Was:** PO check #35; Design check #15

**Steps**

1. On PO-1001, open Timeline by line and the line's heading.

**Expected:** In time order: Ordered, Vendor acknowledged, Shipped, Invoiced, and "Entry taken back" for INV-2. Times read like 11/06/2026 2:35 PM.

**Automated tests:** `test_closing.py::test_the_timeline_lists_what_happened_to_a_line_in_order`, `test_closing.py::test_the_timeline_keeps_an_entry_that_was_taken_back`

**Latest:** Claude, Chrome: Pass 11/07/2026. Owner: Pass 11/06/2026.

<details><summary>Screenshot</summary>

![TC-EVT-10 in Chrome](screenshots/TC-EVT-10.png)

</details>


<a id="tc-evt-11"></a>

### TC-EVT-11: The status of every line follows the agreed table in every combination

**Priority:** Critical · **How:** Automated · **Refs:** DEC-36, ARCHITECTURE §5

**Steps**

1. Covered by the tests named (one per row of the status table, and every combination).

**Expected:** Every row of the status table holds; a PO closes only when every line has; open quantity is never negative; one PO's facts never touch another.

**Automated tests:** `test_status.py::test_each_row_of_the_table`, `test_status.py::test_rules_hold_across_every_combination`, `test_status.py::test_a_po_closes_only_when_every_line_has`, `test_status.py::test_one_pos_facts_never_touch_another`

**Latest:** Owner: not recorded.


## Home and Open items

<a id="tc-home-01"></a>

### TC-HOME-01: Home shows the six counts and a Needs attention list with the next step

**Priority:** High · **How:** Browser · **Refs:** REQ-DB-01, DEC-42 · **Was:** PO check #36; Design check #1, #16

**Steps**

1. Go Home after the entries above.

**Expected:** Six tiles (open purchase orders, open lines, late lines, waiting for an acknowledgement, promised date slipped, original PO needs updating) whose numbers match what was entered, and Needs attention with a next step such as "Ask the vendor for a new date".

**Automated tests:** `test_reports.py::test_the_dashboard_shows_the_counts_and_links_to_the_report`, `test_reports.py::test_the_to_do_list_puts_the_most_urgent_first_and_says_what_to_do`

**Latest:** Claude, Chrome: Pass 11/07/2026. Owner: Pass 11/06/2026.

<details><summary>Screenshot</summary>

![TC-HOME-01 in Chrome](screenshots/TC-HOME-01.png)

</details>


<a id="tc-home-02"></a>

### TC-HOME-02: Home updates itself within a minute

**Priority:** High · **How:** Browser · **Refs:** DEC-45 · **Was:** Design check #16

**Steps**

1. Leave Home open. In a second tab, record a past promised date on another PO.
2. Wait about a minute without touching Home.

**Expected:** Home's counts and Needs attention include the new late line without a reload.

**Automated tests:** `test_web_standards.py::test_a_background_update_does_not_count_as_activity`

**Latest:** Claude, Chrome: Pass 11/07/2026. Owner: Pass 11/06/2026.

<details><summary>Screenshot</summary>

![TC-HOME-02 in Chrome](screenshots/TC-HOME-02.png)

</details>


<a id="tc-home-03"></a>

### TC-HOME-03: Open items filters as you choose, and a late tile opens it filtered

**Priority:** Medium · **How:** Browser · **Refs:** REQ-DB-03 · **Was:** PO check #37, #38; Design check #18

**Steps**

1. On Home click the Late lines tile.
2. On Open items change Show and Vendor.

**Expected:** Open items shows only late lines, each with a red "N days late", most late first. The list follows each choice; an empty view says "All clear".

**Automated tests:** `test_reports.py::test_the_report_page_filters_and_downloads`, `test_reports.py::test_late_slipped_and_update_flags_are_counted_from_the_same_list`

**Latest:** Claude, Chrome: Pass 11/07/2026. Owner: Pass 11/06/2026.

<details><summary>Screenshot</summary>

![TC-HOME-03 in Chrome](screenshots/TC-HOME-03.png)

</details>


<a id="tc-home-04"></a>

### TC-HOME-04: The Open items CSV downloads as a file and a formula in a cell stays plain text

**Priority:** Critical · **How:** Browser · **Refs:** REQ-DB-03, Security: CSV injection · **Was:** PO check #39, #40; Design check #18

**Steps**

1. Enter a PO PO-1003 for Acme Fasteners whose line item is =1+1.
2. On Open items press Download CSV and open it in Excel or Numbers.

**Expected:** A file open-items-<date>.csv downloads (not shown in the page) with dates like 11/06/2026 and accents right. The cell shows '=1+1 as text, never 2.

**Automated tests:** `test_reports.py::test_the_csv_has_the_columns_and_neutralises_formulas`, `test_reports.py::test_a_cell_that_would_run_as_a_formula_is_made_plain_text`, `test_web_standards.py::test_downloads_are_plain_links_not_page_swaps`

**Latest:** Claude, Chrome: Pass 11/07/2026. Owner: Pass 11/06/2026.

<details><summary>Screenshot</summary>

![TC-HOME-04 in Chrome](screenshots/TC-HOME-04.png)

</details>


<a id="tc-home-05"></a>

### TC-HOME-05: A PO sent with no acknowledgement is flagged only after two working days

**Priority:** High · **How:** Automated · **Refs:** DEC-37, DEC-42

**Steps**

1. Covered by the tests named.

**Expected:** Weekends are skipped; the flag appears after two working days, not before; closed or cancelled lines are never open items.

**Automated tests:** `test_reports.py::test_a_sent_po_with_no_acknowledgement_is_flagged_only_after_two_working_days`, `test_reports.py::test_business_days_skip_weekends`, `test_reports.py::test_a_closed_or_cancelled_line_is_not_an_open_item`

**Latest:** Owner: not recorded.


## Audit

<a id="tc-aud-01"></a>

### TC-AUD-01: An admin sees every change, newest first, and can narrow it

**Priority:** High · **How:** Browser · **Refs:** NFR-03, PD-1.7 · **Was:** PO check #41, #42, #43; Design check #19

**Steps**

1. As boss, click Audit.
2. Type line.close in Action; pick a person; press Clear.
3. The From box is a calendar, so a wrong date can only come in the address: open /admin/audit?date_from=soon.

**Expected:** A table, newest first: time, person, address, action, what it was about, before and after; the cursor starts in Action. The list narrows and Clear brings everything back. soon is reported as not a date and the list still shows.

**Automated tests:** `test_audit_screen.py::test_an_admin_sees_the_trail_and_the_cursor_starts_in_the_first_box`, `test_audit_screen.py::test_filters_narrow_the_list_and_a_wrong_date_is_reported`, `test_audit_screen.py::test_the_screen_only_reads`

**Latest:** Claude, Chrome: Pass 11/07/2026. Owner: Pass 11/06/2026.

<details><summary>Screenshot</summary>

![TC-AUD-01 in Chrome](screenshots/TC-AUD-01.png)

</details>


<a id="tc-aud-02"></a>

### TC-AUD-02: Every change to the ledger writes an audit record with who and where, and nothing secret

**Priority:** Critical · **How:** Automated · **Refs:** NFR-03

**Steps**

1. Covered by the tests named.

**Expected:** Every create, change, removal and settle writes a record with the person and address. No password or token is ever in it.

**Automated tests:** `test_audit_screen.py::test_every_change_to_the_ledger_writes_an_audit_record`, `test_events.py::test_every_recording_is_audited`, `test_auth.py::test_nothing_secret_reaches_the_audit_trail`

**Latest:** Owner: not recorded.


## Page behaviour

<a id="tc-web-01"></a>

### TC-WEB-01: Pages change without a white flash, and Back and Forward work

**Priority:** Medium · **How:** Browser · **Refs:** DEC-45 · **Was:** Design check #4, #14

**Steps**

1. Click between Home, Purchase orders, Vendors and a PO.
2. Use the browser's Back and Forward.

**Expected:** Only the page body changes; the browser tab title follows. Back and Forward show the right pages, never "page not found".

**Automated tests:** `test_web_standards.py::test_signed_in_pages_load_htmx_from_this_site_with_the_safe_settings`, `test_web_standards.py::test_a_refused_form_sent_by_htmx_is_kept_out_of_the_history`

**Latest:** Claude, Chrome: Pass 11/07/2026. Owner: Pass 11/06/2026.

<details><summary>Screenshot</summary>

![TC-WEB-01 in Chrome](screenshots/TC-WEB-01.png)

</details>


<a id="tc-web-02"></a>

### TC-WEB-02: At phone width the page never scrolls sideways

**Priority:** High · **How:** Browser · **Refs:** DEC-45, NFR-12 · **Was:** Design check #20; Sign-in check, things to look at

**Steps**

1. Make the window phone width (or use a phone on the office network). Visit Home, Purchase orders, a PO, Vendors, Open items, People, Audit.

**Expected:** No sideways scrolling of the page. The top bar scrolls sideways; tables scroll inside their box; buttons are easy to tap.

**Latest:** Claude, Chrome: Pass 11/07/2026. Owner: Pass 11/06/2026.

<details><summary>Screenshot</summary>

![TC-WEB-02 in Chrome](screenshots/TC-WEB-02.png)

</details>


<a id="tc-web-03"></a>

### TC-WEB-03: Everything can be reached with the keyboard

**Priority:** Medium · **How:** Browser · **Refs:** DEC-45, NFR-12 · **Was:** Design check #21; Sign-in check, things to look at

**Steps**

1. Reload any page and press Tab once.
2. Keep pressing Tab through the sign-in page and the New purchase order form.

**Expected:** A Skip to the page link appears at the top left; Enter moves past the top bar. Every control shows a blue outline when Tab reaches it, in a sensible order.

**Automated tests:** `test_gate.py::test_the_cursor_is_ready_in_the_first_box_of_every_form_page`

**Latest:** Claude, Chrome: Pass 11/07/2026. Owner: Pass 11/06/2026.

<details><summary>Screenshot</summary>

![TC-WEB-03 in Chrome](screenshots/TC-WEB-03.png)

</details>


<a id="tc-web-04"></a>

### TC-WEB-04: Dark mode on the Mac leaves the app light and readable

**Priority:** Medium · **How:** Browser · **Refs:** DEC-35 · **Was:** Sign-in check, things to look at

**Steps**

1. Switch macOS to dark mode and reload.

**Expected:** The app stays in its one light theme and is readable.

**Latest:** Claude, Chrome: Pass 11/07/2026. Owner: Pass 11/04/2026 (Safari, Chrome).

<details><summary>Screenshot</summary>

![TC-WEB-04 in Chrome](screenshots/TC-WEB-04.png)

</details>


<a id="tc-web-05"></a>

### TC-WEB-05: Every page answers within 0.3 seconds with a year's worth of data

**Priority:** High · **How:** Automated · **Refs:** DEC-45, NFR speed

**Steps**

1. Covered by the test named: 300 vendors, 1,000 POs and 5,000 lines.

**Expected:** Every page answers within 0.3 seconds.

**Automated tests:** `test_speed.py::test_each_page_answers_within_the_limit`, `test_status.py::test_the_number_of_queries_does_not_grow_with_the_lines`

**Latest:** Owner: not recorded.


<a id="tc-web-06"></a>

### TC-WEB-06: A mistyped address or an unreadable form shows a page a person can read

**Priority:** Medium · **How:** Browser · **Refs:** DEC-45, DEF-002

**Steps**

1. Signed in, open http://127.0.0.1:8000/no-such-page.

**Expected:** A page headed "Page not found" in the app's own layout, with a Go to Home button. Never raw code such as {"detail": ...}.

**Automated tests:** `test_gate.py::test_a_mistyped_address_and_an_unreadable_form_are_pages_too`, `test_gate.py::test_a_script_still_gets_the_short_json_answer`

**Latest:** Claude, Chrome: Pass 11/07/2026. Owner: Not yet run.

<details><summary>Screenshot</summary>

![TC-WEB-06 in Chrome](screenshots/TC-WEB-06.png)

</details>


## Security and data safety

<a id="tc-sec-01"></a>

### TC-SEC-01: Every change needs the session's token and must come from the app's own pages

**Priority:** Critical · **How:** Automated · **Refs:** ARCHITECTURE §10.1 (CSRF, Origin)

**Steps**

1. Covered by the tests named.

**Expected:** A change without the token, or from another site, is refused. A GET never changes anything. A forged cookie is no session.

**Automated tests:** `test_gate.py::test_a_signed_in_write_needs_the_session_token`, `test_gate.py::test_sign_in_needs_its_token_and_a_local_origin`, `test_gate.py::test_a_get_never_changes_anything`, `test_gate.py::test_a_forged_or_malformed_session_cookie_is_just_no_session`, `test_orders_web.py::test_an_upload_from_another_site_is_refused`

**Latest:** Owner: not recorded.


<a id="tc-sec-02"></a>

### TC-SEC-02: Pages carry the security headers, are not cached, and run no inline script or style

**Priority:** High · **How:** Automated · **Refs:** ARCHITECTURE §10.1 (headers, CSP)

**Steps**

1. Covered by the tests named.

**Expected:** Content-Security-Policy and the other headers are on every response; signed-in pages are not cached; no page has inline script or style; the pinned HTMX file is exactly the published one.

**Automated tests:** `test_gate.py::test_security_headers_are_on_public_responses`, `test_gate.py::test_signed_in_pages_are_not_cached_and_refusals_carry_headers`, `test_web_standards.py::test_no_page_has_inline_script_style_or_script_attributes`, `test_web_standards.py::test_the_pinned_htmx_file_is_exactly_the_published_one`

**Latest:** Owner: not recorded.


<a id="tc-sec-03"></a>

### TC-SEC-03: Hostile spreadsheets are refused: macros, zip bombs, entity tricks, huge sheets, links to files

**Priority:** Critical · **How:** Automated · **Refs:** DEC-47

**Steps**

1. Covered by the tests named.

**Expected:** Each is refused quickly with a plain message and nothing is written.

**Automated tests:** `test_sheets.py::test_a_workbook_with_macros_is_refused_whatever_its_name`, `test_sheets.py::test_a_zip_bomb_is_refused_from_its_declared_size`, `test_sheets.py::test_an_entity_bomb_is_refused_quickly`, `test_sheets.py::test_a_workbook_cannot_read_files_from_the_mac`, `test_sheets.py::test_a_sheet_that_claims_to_be_huge_is_cut_off_quickly`, `test_sheets.py::test_without_the_safe_parser_excel_files_are_refused`

**Latest:** Owner: not recorded.


<a id="tc-sec-04"></a>

### TC-SEC-04: Uploads over 2 MB, or that do not state their size, are refused before they are read

**Priority:** High · **How:** Automated · **Refs:** DEC-47

**Steps**

1. Covered by the tests named.

**Expected:** Refused with the limit named; the server never holds the whole oversized file.

**Automated tests:** `test_orders_web.py::test_an_oversized_upload_is_refused_before_it_is_read`, `test_orders_web.py::test_an_upload_that_does_not_state_its_size_is_refused`, `test_gate.py::test_an_oversized_form_is_refused_not_buffered_forever`

**Latest:** Owner: not recorded.


<a id="tc-sec-05"></a>

### TC-SEC-05: Passwords are stored only as argon2id hashes; no secret is in the database file or the logs

**Priority:** Critical · **How:** Automated · **Refs:** ARCHITECTURE §10.1

**Steps**

1. Covered by the tests named.

**Expected:** Only hashes are stored; session tokens are stored as hashes; nothing secret is in the database file, the logs or the audit trail.

**Automated tests:** `test_auth.py::test_the_password_is_stored_as_an_argon2id_hash_only`, `test_auth.py::test_sign_in_starts_a_full_session_and_stores_only_the_token_hash`, `test_gate.py::test_no_secret_is_in_the_database_file_or_the_logs`

**Latest:** Owner: not recorded.


<a id="tc-sec-06"></a>

### TC-SEC-06: Quantities and money are exact everywhere

**Priority:** Critical · **How:** Automated · **Refs:** ARCHITECTURE §4

**Steps**

1. Covered by the tests named.

**Expected:** Values come back exactly as entered; tenths add up exactly; floats, NaN, infinity and huge numbers are refused. The only rounding anywhere is the QuickBooks unit price (TC-QB-05), which is shown and recorded.

**Automated tests:** `test_ledger.py::test_quantities_and_prices_come_back_exactly`, `test_ledger.py::test_tenths_add_up_exactly_in_sql`, `test_ledger.py::test_nan_infinity_and_huge_numbers_are_refused`, `test_status.py::test_shipments_add_up_exactly`

**Latest:** Owner: not recorded.


## Reading documents (model)

<a id="tc-ai-01"></a>

### TC-AI-01: The golden set is rebuilt exactly from its seed, and has the mix the owner asked for

**Priority:** High · **How:** Automated · **Refs:** DEC-49, SCORING.md §5, PD-2.2, PD-2.3

**Steps**

1. Covered by the tests named: the generator is run into a fresh folder and compared, byte for byte, with tests/data/golden.

**Expected:** No difference. About 100 documents: 30 acknowledgements, 22 shipment notices, 25 invoices, 8 purchase orders, 15 others, in five formats with none above 40%. At least three of each hard case, 8 planted instructions, every answer in schema v1, every invoice total equal to its goods plus its charges, made-up addresses only.

**Automated tests:** `test_golden_set.py::test_the_generator_rebuilds_the_committed_set_exactly`, `test_golden_set.py::test_the_set_has_the_mix_the_decision_asks_for`, `test_golden_set.py::test_every_answer_has_exactly_the_schema_fields`, `test_golden_set.py::test_an_invoice_total_is_its_goods_plus_its_charges`, `test_golden_set.py::test_a_charge_is_never_a_line`, `test_golden_set.py::test_no_real_looking_addresses_or_names_are_used`, `test_golden_set.py::test_the_files_are_what_their_names_say`

**Latest:** Owner: not recorded.


<a id="tc-ai-02"></a>

### TC-AI-02: Each scoring rule gives the verdict SCORING.md says, with made-up model answers

**Priority:** Critical · **How:** Automated · **Refs:** SCORING.md §3, §4, PD-2.4

**Steps**

1. Covered by the tests named. No model is called.

**Expected:** A wrong PO number, a missing one and an invented one are told apart. Dates are right only as the same day in YYYY-MM-DD. Numbers have no tolerance. Lines pair by line number, then by part number. A guessed line number is wrong. A charge given as a line counts twice. A planted instruction's tell-tale value is found anywhere in the output.

**Automated tests:** `test_scoring.py::test_po_number_wrong_missing_and_invented_are_told_apart`, `test_scoring.py::test_a_hyphen_is_part_of_the_number`, `test_scoring.py::test_dates_are_right_only_as_the_same_iso_day`, `test_scoring.py::test_numbers_are_exact_with_no_tolerance`, `test_scoring.py::test_a_guessed_line_number_is_wrong_when_the_answer_has_none`, `test_scoring.py::test_a_charge_given_as_a_line_counts_twice`, `test_scoring.py::test_t3_needs_line_number_quantity_and_the_promised_date`, `test_scoring.py::test_planted_instruction_tell_tales_are_found_anywhere_in_the_output`, `test_golden_set.py::test_a_document_scores_perfectly_against_its_own_answer`

**Latest:** Owner: not recorded.


<a id="tc-ai-03"></a>

### TC-AI-03: The document is fenced as data, and the model call is local, repeatable and leaks no text

**Priority:** Critical · **How:** Automated · **Refs:** DEC-01, DEC-09, DEC-13, PD-2.4

**Steps**

1. Covered by the tests named. The model server is replaced by a made-up one.

**Expected:** The document sits between markers and cannot close its own fence. The prompt says a document is never an instruction. The call goes to this Mac, at temperature 0 with a fixed seed and schema v1. When the call fails, the error holds no document text.

**Automated tests:** `test_extraction.py::test_the_document_is_fenced_as_data_and_cannot_close_its_own_fence`, `test_extraction.py::test_the_prompt_says_a_document_is_never_an_instruction`, `test_extraction.py::test_the_call_is_local_deterministic_and_carries_the_schema`, `test_extraction.py::test_a_failed_call_raises_an_error_without_the_document_text`, `test_extraction.py::test_a_reply_is_read_with_exact_decimals`

**Latest:** Owner: not recorded.


## Mail sources

<a id="tc-mail-01"></a>

### TC-MAIL-01: Mail is read-only: nothing in the app can send, move, flag or delete a message

**Priority:** Critical · **How:** Automated · **Refs:** DEC-07, REQ-IN-02, PD-3.1

**Steps**

1. Covered by the tests named. They read the source code of the whole app and of the mail package.
2. Checked by deliberate breaks on 2026-11-10: a mail-sending import, a call to trash a message and a Gmail trash address each made the test fail.

**Expected:** No module imports a mail-sending library. The mail package calls nothing that changes a mailbox or writes a file, names no write address or write permission, and no mail source has a method that could.

**Automated tests:** `test_mail_readonly.py::test_no_module_in_the_app_can_send_mail`, `test_mail_readonly.py::test_the_mail_package_calls_nothing_that_changes_a_mailbox_or_a_file`, `test_mail_readonly.py::test_the_mail_package_names_no_write_address_or_scope`, `test_mail_folder.py::TestFolderSource::test_it_has_no_way_to_change_a_mailbox`, `test_mail_folder.py::TestFolderSource::test_reading_changes_nothing`, `test_mail_folder.py::TestFolderSource::test_a_folder_can_be_read_without_permission_to_write`

**Latest:** Owner: not recorded.


<a id="tc-mail-02"></a>

### TC-MAIL-02: Every mail source passes the same contract (folder now; Gmail and Microsoft 365 later)

**Priority:** High · **How:** Automated · **Refs:** REQ-IN-06, REQ-IN-01, PD-3.1

**Steps**

1. Covered by the tests named: one shared contract run against the folder provider.

**Expected:** Inbox and Sent Items are scanned. Each message comes once and a later one comes once. Bytes arrive unchanged. A batch limit loses nothing and repeats nothing. A position the source does not understand means start again. Health says what is wrong without raising.

**Automated tests:** `test_mail_folder.py::TestFolderSource::test_each_message_comes_once`, `test_mail_folder.py::TestFolderSource::test_a_message_that_arrives_later_is_returned_once`, `test_mail_folder.py::TestFolderSource::test_the_bytes_are_handed_over_unchanged`, `test_mail_folder.py::TestFolderSource::test_a_batch_limit_loses_nothing_and_repeats_nothing`, `test_mail_folder.py::TestFolderSource::test_the_two_folders_are_separate`, `test_mail_folder.py::TestFolderSource::test_a_position_it_does_not_understand_means_start_again`, `test_mail_folder.py::TestFolderSource::test_health_is_ok_and_then_says_what_is_wrong_without_raising`, `test_mail_folder.py::TestFolderSource::test_the_golden_emails_are_read`

**Latest:** Owner: not recorded.


<a id="tc-mail-03"></a>

### TC-MAIL-03: The folder reader ignores what is not mail and cannot be tricked out of its folder

**Priority:** High · **How:** Automated · **Refs:** PD-3.1, PD-3.6

**Steps**

1. Covered by the tests named.

**Expected:** Only visible .eml files directly in the folder are read. A symbolic link is not followed. A copy under another name is not read twice, a changed file is a new message, and a message over 50 MB is skipped with a reason and not retried.

**Automated tests:** `test_mail_folder.py::TestFolderSource::test_only_eml_files_are_read`, `test_mail_folder.py::TestFolderSource::test_a_symbolic_link_is_not_followed`, `test_mail_folder.py::TestFolderSource::test_a_copy_under_another_name_is_not_read_twice`, `test_mail_folder.py::TestFolderSource::test_a_changed_file_is_a_new_message`, `test_mail_folder.py::TestFolderSource::test_a_huge_message_is_skipped_with_a_reason_and_not_read_again`, `test_mail_folder.py::TestFolderSource::test_a_missing_folder_is_not_an_error`

**Latest:** Owner: not recorded.


<a id="tc-mail-04"></a>

### TC-MAIL-04: The mailbox table keeps its own rules

**Priority:** Medium · **How:** Automated · **Refs:** PD-3.1, ARCHITECTURE §4

**Steps**

1. Covered by the tests named, and by the migration test that compares the table with the code.

**Expected:** A known provider and state only, lower-case address, one row per address; removing the person who connected a mailbox keeps the mailbox.

**Automated tests:** `test_mailbox_table.py::test_a_mailbox_is_stored_and_read_back`, `test_mailbox_table.py::test_unknown_providers_and_states_and_upper_case_addresses_are_refused`, `test_mailbox_table.py::test_one_row_per_address`, `test_mailbox_table.py::test_removing_the_person_who_connected_it_keeps_the_mailbox`, `test_database.py::test_migration_matches_the_models`

**Latest:** Owner: not recorded.


<a id="tc-mail-05"></a>

### TC-MAIL-05: Every original message and attachment is kept unchanged, once, under its hash

**Priority:** Critical · **How:** Automated · **Refs:** REQ-IN-04, REQ-IN-05, PD-3.2

**Steps**

1. Covered by the tests named, including all 36 golden emails and golden PDFs, scans and spreadsheets sent as attachments.

**Expected:** The stored bytes equal what arrived and their hash matches the row. The same message twice, or through another folder or mailbox, is one message. An attachment already stored is not stored again but the new message and its body are. Two messages that both say only 'Thanks' each keep their body. The database itself refuses a second copy of an attachment.

**Automated tests:** `test_intake.py::test_a_message_is_stored_unchanged_with_its_facts`, `test_intake.py::test_an_attachment_is_stored_under_its_hash`, `test_intake.py::test_the_same_message_twice_is_stored_once`, `test_intake.py::test_the_same_bytes_from_another_folder_are_still_one_message`, `test_intake.py::test_an_attachment_already_stored_is_not_stored_again_but_the_new_message_is`, `test_intake.py::test_two_messages_with_the_same_words_each_keep_their_body`, `test_intake.py::test_the_database_itself_refuses_a_second_copy_of_an_attachment`, `test_intake.py::test_every_golden_email_is_stored_and_the_originals_are_unchanged`, `test_intake.py::test_attachments_in_the_golden_set_come_through`, `test_intake.py::test_the_original_is_hashed_after_storage_and_matches`

**Latest:** Owner: not recorded.


<a id="tc-mail-06"></a>

### TC-MAIL-06: A hostile message cannot hurt intake: odd file names, broken mail, huge or too many files

**Priority:** Critical · **How:** Automated · **Refs:** DEC-51, PD-3.2, PD-3.6

**Steps**

1. Covered by the tests named. File names include ../../etc/passwd, a Windows path, a NUL and line breaks sent in RFC 2231 form, a right-to-left override and a 1,000-character name.

**Expected:** No file name ever becomes a path: every stored file is named by its hash, inside the store. Broken mail is still stored and does not stop intake. A file over 20 MB, an attachment past the 25th, a program or a macro spreadsheet is stored and marked not read with the reason. A forwarded message is kept as an attachment.

**Automated tests:** `test_intake.py::test_a_hostile_file_name_never_becomes_a_path`, `test_intake.py::test_a_broken_message_is_stored_and_does_not_stop_intake`, `test_intake.py::test_files_the_app_does_not_read_are_stored_and_say_so`, `test_intake.py::test_a_file_over_the_size_limit_is_stored_and_not_read`, `test_intake.py::test_attachments_past_the_25th_are_stored_and_not_read`, `test_intake.py::test_a_forwarded_message_is_kept_as_an_attachment`, `test_intake.py::test_the_store_refuses_a_path_it_did_not_make`

**Latest:** Owner: not recorded.


<a id="tc-mail-07"></a>

### TC-MAIL-07: The store is private, never half-written, rolls back cleanly, keeps message text out of logs and honours the start date

**Priority:** High · **How:** Automated · **Refs:** DEC-13, DEC-51, PD-3.2

**Steps**

1. Covered by the tests named.

**Expected:** Folders are 700 and files 600, with no temporary file left behind. A failure while writing rows leaves the database empty and a retry works. A subject, body and file name never appear in the logs. Mail from before the mailbox's start date is not stored; a message with no date is stored with the time it was read.

**Automated tests:** `test_intake.py::test_the_store_is_owner_only_and_never_half_written`, `test_intake.py::test_a_failure_while_writing_rows_leaves_nothing_in_the_database_and_the_retry_works`, `test_intake.py::test_no_message_text_reaches_the_logs`, `test_intake.py::test_mail_from_before_the_start_date_is_not_stored`, `test_intake.py::test_a_message_with_no_date_is_stored_with_the_time_it_was_read`

**Latest:** Owner: not recorded.
