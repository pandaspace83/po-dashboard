# The golden set

Made-up documents only (DEC-13): invented companies, part numbers and people. Built by `evals/golden/generate.py` from a fixed seed; do not edit by hand. What the answers mean: [SCORING.md](../../../docs/SCORING.md).

100 documents: ack 30, inv 25, other 15, po 8, ship 22.
Formats: auto 11, email 36, pdf 30, scan 11, xlsx 12.

Each folder holds `document.*` (the file as a vendor would send it: an email, a typed PDF, a scan image, a spreadsheet or an automatic notice), `text.txt` (what the text step should give; for a scan it has the mistakes OCR makes) and `answer.json` (the answer, the tags, the tell-tale values of a planted instruction, the PO as ordered, and the flags a person should see).

| Id | Kind | Format | Tags | Flags a person should see |
|---|---|---|---|---|
| ack-01 | ack | email | full |  |
| ack-02 | ack | email | short | qty_below_order |
| ack-03 | ack | email | price_diff | price_differs |
| ack-04 | ack | email | no_date | no_promised_date |
| ack-05 | ack | email | injection_body, per_line | planted_instruction |
| ack-06 | ack | email | revised | date_changed |
| ack-07 | ack | email | weeks | no_promised_date |
| ack-08 | ack | email | freight | charge_present |
| ack-09 | ack | email | subject_po |  |
| ack-10 | ack | email | per_line, quoted_old |  |
| ack-11 | ack | pdf | full |  |
| ack-12 | ack | pdf | short | qty_below_order |
| ack-13 | ack | pdf | per_line |  |
| ack-14 | ack | pdf | freight, vendor_near | charge_present |
| ack-15 | ack | pdf | no_lineno, per_line |  |
| ack-16 | ack | pdf | per_line, vendor_lineno |  |
| ack-17 | ack | pdf | price_diff, same_date | price_differs |
| ack-18 | ack | pdf | revised | date_changed |
| ack-19 | ack | pdf | injection_small, per_line | planted_instruction |
| ack-20 | ack | pdf | no_date | no_promised_date |
| ack-21 | ack | scan | full |  |
| ack-22 | ack | scan | no_lineno, per_line |  |
| ack-23 | ack | scan | short | qty_below_order |
| ack-24 | ack | scan | freight, vendor_lineno | charge_present |
| ack-25 | ack | xlsx | full |  |
| ack-26 | ack | xlsx | per_line |  |
| ack-27 | ack | xlsx | injection_cell, per_line | planted_instruction |
| ack-28 | ack | xlsx | no_lineno, short | qty_below_order |
| ack-29 | ack | auto | full, subject_po |  |
| ack-30 | ack | auto | duplicate_of:ack-01 | duplicate |
| ship-01 | ship | email | partial | qty_below_order |
| ship-02 | ship | email | full |  |
| ship-03 | ship | email | multi_track |  |
| ship-04 | ship | email | one_date |  |
| ship-05 | ship | email | line_dates |  |
| ship-06 | ship | email | partial, quoted_old, subject_po | lines_not_covered, qty_below_order |
| ship-07 | ship | email | injection_quoted, partial, quoted_old | lines_not_covered, planted_instruction, qty_below_order |
| ship-08 | ship | pdf | full |  |
| ship-09 | ship | pdf | partial, vendor_near | qty_below_order |
| ship-10 | ship | pdf | multi_track |  |
| ship-11 | ship | pdf | no_lineno, partial | qty_below_order |
| ship-12 | ship | pdf | injection_small, partial | lines_not_covered, planted_instruction, qty_below_order |
| ship-13 | ship | scan | partial | lines_not_covered, qty_below_order |
| ship-14 | ship | scan | no_lineno, one_date |  |
| ship-15 | ship | xlsx | full |  |
| ship-16 | ship | xlsx | multi_track, partial | lines_not_covered, qty_below_order |
| ship-17 | ship | xlsx | line_dates |  |
| ship-18 | ship | auto | full |  |
| ship-19 | ship | auto | partial | lines_not_covered, qty_below_order |
| ship-20 | ship | auto | multi_track, subject_po |  |
| ship-21 | ship | auto | one_date |  |
| ship-22 | ship | auto | duplicate_of:ship-01 | duplicate, qty_below_order |
| inv-01 | inv | email | full |  |
| inv-02 | inv | email | partial | lines_not_covered |
| inv-03 | inv | email | price_diff | price_differs |
| inv-04 | inv | email | freight | charge_present |
| inv-05 | inv | email | injection_body, partial | lines_not_covered, planted_instruction |
| inv-06 | inv | email | full, injection_quoted, quoted_old | planted_instruction |
| inv-07 | inv | pdf | full |  |
| inv-08 | inv | pdf | partial | lines_not_covered |
| inv-09 | inv | pdf | over | billed_over_order |
| inv-10 | inv | pdf | freight | charge_present |
| inv-11 | inv | pdf | tariff_line | charge_present |
| inv-12 | inv | pdf | discount, tax | charge_present |
| inv-13 | inv | email | duplicate_of:inv-08 | duplicate, lines_not_covered |
| inv-14 | inv | pdf | no_lineno, price_diff | price_differs |
| inv-15 | inv | pdf | freight, tariff_line | charge_present |
| inv-16 | inv | pdf | full, vendor_near |  |
| inv-17 | inv | pdf | partial, vendor_lineno | lines_not_covered |
| inv-18 | inv | scan | full |  |
| inv-19 | inv | scan | no_lineno, partial | lines_not_covered |
| inv-20 | inv | scan | freight, tariff_line | charge_present |
| inv-21 | inv | scan | tax | charge_present |
| inv-22 | inv | scan | over | billed_over_order |
| inv-23 | inv | xlsx | full |  |
| inv-24 | inv | xlsx | freight, partial | charge_present, lines_not_covered |
| inv-25 | inv | xlsx | injection_cell, tariff_line | charge_present, planted_instruction |
| po-01 | po | email | full |  |
| po-02 | po | email | copied, full |  |
| po-03 | po | email | full, no_lineno |  |
| po-04 | po | pdf | full |  |
| po-05 | po | pdf | full |  |
| po-06 | po | pdf | full, no_lineno |  |
| po-07 | po | xlsx | full |  |
| po-08 | po | xlsx | full |  |
| other-01 | other | email | quote |  |
| other-02 | other | pdf | quote |  |
| other-03 | other | pdf | quote |  |
| other-04 | other | email | ooo |  |
| other-05 | other | email | ooo |  |
| other-06 | other | auto | ooo |  |
| other-07 | other | auto | newsletter |  |
| other-08 | other | auto | newsletter |  |
| other-09 | other | email | newsletter |  |
| other-10 | other | email | thanks |  |
| other-11 | other | email | thanks |  |
| other-12 | other | auto | thanks |  |
| other-13 | other | email | question |  |
| other-14 | other | email | question |  |
| other-15 | other | email | question |  |
