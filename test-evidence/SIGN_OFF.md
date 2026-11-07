# User sign-off before go-live

*Built from [test_record.toml](test_record.toml) by `scripts/test_record.py`. Do not edit by hand.*

The checks that matter most to the business. A person signs off only when every row passes. **15 rows can be tested now**; the others are required before go-live and are tested when their phase is built. Each row names the test cases that prove it, in [TEST_CASES.md](TEST_CASES.md). Use the workbook (`uv run python scripts/test_record.py --workbook`) to record results and sign.

| ID | Check | Why it matters | Test cases | Status | Result | Initials | Date |
|---|---|---|---|---|---|---|---|
| SO-01 | Only signed-in people reach anything; a wrong password and an unknown email look the same; five wrong tries lock the account. | The app holds every PO, price and vendor contact. | [TC-SIGN-01](TEST_CASES.md#tc-sign-01), [TC-SIGN-02](TEST_CASES.md#tc-sign-02), [TC-SIGN-08](TEST_CASES.md#tc-sign-08) | Ready | | | |
| SO-02 | Buyers cannot reach People or Audit; a disabled person is cut off at once; a temporary password is shown once and must be changed. | Access must follow the person's role on the day, not last week's. | [TC-SIGN-04](TEST_CASES.md#tc-sign-04), [TC-SIGN-05](TEST_CASES.md#tc-sign-05), [TC-SIGN-06](TEST_CASES.md#tc-sign-06), [TC-SIGN-07](TEST_CASES.md#tc-sign-07) | Ready | | | |
| SO-03 | Nothing is ever created twice: Reload, Back, a second press of Create, the same file again. | A doubled PO or shipment gives wrong counts and wrong follow-ups. | [TC-PO-02](TEST_CASES.md#tc-po-02), [TC-PIMP-02](TEST_CASES.md#tc-pimp-02), [TC-QB-03](TEST_CASES.md#tc-qb-03), [TC-QB-07](TEST_CASES.md#tc-qb-07), [TC-VIMP-02](TEST_CASES.md#tc-vimp-02), [TC-EVT-07](TEST_CASES.md#tc-evt-07) | Ready | | | |
| SO-04 | Every import is all or nothing, and a bad row is named. | Half a file loaded is worse than none: nobody knows which half. | [TC-PIMP-03](TEST_CASES.md#tc-pimp-03), [TC-PIMP-04](TEST_CASES.md#tc-pimp-04), [TC-QB-02](TEST_CASES.md#tc-qb-02) | Ready | | | |
| SO-05 | Quantities and prices are exact; the only rounding is a QuickBooks unit price, which is shown and recorded. | Money and quantities decide what is owed and what is still to come. | [TC-PO-01](TEST_CASES.md#tc-po-01), [TC-PO-04](TEST_CASES.md#tc-po-04), [TC-QB-05](TEST_CASES.md#tc-qb-05), [TC-SEC-06](TEST_CASES.md#tc-sec-06) | Ready | | | |
| SO-06 | The go-live load: the company's QuickBooks export previews with counts that match QuickBooks, every waiting vendor is added, then Create. | This is the starting point every later alert is measured from. | [TC-QB-01](TEST_CASES.md#tc-qb-01), [TC-QB-04](TEST_CASES.md#tc-qb-04), [TC-QB-07](TEST_CASES.md#tc-qb-07), [TC-QB-09](TEST_CASES.md#tc-qb-09) | Ready | | | |
| SO-07 | Every vendor has an email; a domain belongs to one vendor only; vendors without a domain are marked. | Replies are matched to vendors by their address. A wrong match puts a vendor's answer on the wrong PO. | [TC-VEN-03](TEST_CASES.md#tc-ven-03), [TC-VEN-06](TEST_CASES.md#tc-ven-06), [TC-VIMP-01](TEST_CASES.md#tc-vimp-01), [TC-VIMP-03](TEST_CASES.md#tc-vimp-03) | Ready | | | |
| SO-08 | A line's status is right through a whole life: confirmed short, shipped in parts, invoiced, over-billed, settled by hand, reopened. | Status drives every count and every follow-up. | [TC-EVT-01](TEST_CASES.md#tc-evt-01), [TC-EVT-03](TEST_CASES.md#tc-evt-03), [TC-EVT-04](TEST_CASES.md#tc-evt-04), [TC-EVT-05](TEST_CASES.md#tc-evt-05), [TC-EVT-09](TEST_CASES.md#tc-evt-09), [TC-EVT-11](TEST_CASES.md#tc-evt-11) | Ready | | | |
| SO-09 | Late, slipped and waiting-for-acknowledgement lines are flagged, and Home's counts agree with Open items. | This is the early warning the app exists for. | [TC-EVT-08](TEST_CASES.md#tc-evt-08), [TC-HOME-01](TEST_CASES.md#tc-home-01), [TC-HOME-03](TEST_CASES.md#tc-home-03), [TC-HOME-05](TEST_CASES.md#tc-home-05) | Ready | | | |
| SO-10 | The Open items CSV opens correctly in Excel, and a formula in a cell stays plain text. | The file goes to other people; a formula could run on their machine. | [TC-HOME-04](TEST_CASES.md#tc-home-04) | Ready | | | |
| SO-11 | Every change is in the audit trail with who and where; only admins can read it. | When a number is questioned, the trail is the answer. | [TC-AUD-01](TEST_CASES.md#tc-aud-01), [TC-AUD-02](TEST_CASES.md#tc-aud-02), [TC-SIGN-06](TEST_CASES.md#tc-sign-06) | Ready | | | |
| SO-12 | Wrong or hostile files are refused with a plain message: old .xls, macros, oversize, the wrong report. | Files come from outside the company. | [TC-VIMP-05](TEST_CASES.md#tc-vimp-05), [TC-SEC-03](TEST_CASES.md#tc-sec-03), [TC-SEC-04](TEST_CASES.md#tc-sec-04), [TC-QB-08](TEST_CASES.md#tc-qb-08), [TC-PIMP-06](TEST_CASES.md#tc-pimp-06) | Ready | | | |
| SO-13 | Works in Safari and Chrome on the office laptops, at phone width, and from the keyboard. | Everyone uses it, on whatever they have. | [TC-WEB-01](TEST_CASES.md#tc-web-01), [TC-WEB-02](TEST_CASES.md#tc-web-02), [TC-WEB-03](TEST_CASES.md#tc-web-03) | Ready | | | |
| SO-14 | Every page answers within 0.3 seconds with a year's worth of data. | A slow page gets skipped. | [TC-WEB-05](TEST_CASES.md#tc-web-05) | Ready | | | |
| SO-15 | Local only: the app refuses other host names and model servers off this Mac; no real document or secret is in the repository. | Company data never leaves the Mac (DEC-01, DEC-13); the repository is public. | [TC-SET-03](TEST_CASES.md#tc-set-03), [TC-SET-04](TEST_CASES.md#tc-set-04), [TC-SEC-05](TEST_CASES.md#tc-sec-05) | Ready | | | |
| SO-16 | Mail is read-only: the app cannot send, move, flag or delete, and is refused on any mailbox it was not given. | DEC-07. A tool that can delete mail must never be pointed at the purchasing mailbox. | — | Not built: PD-3.5, PD-6.2 | | | |
| SO-17 | A person accepts every extracted document before it changes anything; a PO is never guessed; document text never acts as an instruction. | DEC-08, DEC-09. The model reads vendor documents; a person decides. | — | Not built: Phase 4 (PD-4.4 to PD-4.6) | | | |
| SO-18 | Extraction accuracy on the golden set meets the agreed score, and the whole pipeline is re-scored. | The score is the evidence that the model can be trusted with real replies. | — | Not built: PD-2.5, PD-4.7 | | | |
| SO-19 | A reply that matches no vendor or PO is held in Unmatched, never dropped. | A lost reply is a missed delivery date. | — | Not built: PD-4.3 | | | |
| SO-20 | Follow-up alerts fire for the agreed rules, and clear when the vendor answers. | The alerts are the point of the app. | — | Not built: Phase 5 | | | |
| SO-21 | Nightly backups, 35 days kept, a restore tested on a copy, and a backup before every upgrade. | Today there are no automatic backups: until PD-6.5, copy the data folder by hand before every upgrade. | — | Not built: PD-6.5 | | | |
| SO-22 | Runs as a service on the office Mac: restarts after a crash or power cut, catches up on mail missed while it was down; a 24-hour soak test with a restart. | Nobody watches it; it must look after itself. | — | Not built: PD-3.4, PD-6.4, PD-6.6 | | | |
| SO-23 | Office-network access over HTTPS, with a second factor, and the session cookie marked Secure. | Other laptops reach the app over the network only once this is in place (DEC-28, DEC-34). | — | Not built: PD-6.3a | | | |
| SO-24 | The weekly comparison with QuickBooks closes lines that QuickBooks has closed. | Without it, lines received in QuickBooks stay open in the app. | — | Waiting for OPEN-20; PD-1.10 | | | |
| SO-25 | The release gate passes: every test, the golden set against its baseline, a scan for secrets and real documents. | One command that says the build is fit to release. | — | Not built: PD-0.5, PD-7.3 | | | |

## Decision

- [ ] Approved for go-live
- [ ] Approved with conditions:
- [ ] Not approved

Name: __________  Role: __________  Date: __________  Signature: __________
