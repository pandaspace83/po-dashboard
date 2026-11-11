"""Build the test record's pages and its workbook from test-evidence/test_record.toml and chrome_run.json.

    uv run python scripts/test_record.py              # rewrite TEST_CASES.md, DEFECTS.md and SIGN_OFF.md
    uv run python scripts/test_record.py --check      # exit 1 if they are out of date or the record is inconsistent
    uv run python scripts/test_record.py --workbook   # also build the Excel workbook for a test cycle

The workbook (Read me, Test cases, Evidence, Defects & retests, Sign-off) goes to test-evidence/workbooks/ with the
date in its name. It stays on this Mac: .xlsx files are git-ignored and the commit guard refuses them. An existing
workbook is never overwritten, because it may hold a tester's results and a signature.
"""

from __future__ import annotations

import argparse
import json
import re
import struct
import subprocess
import sys
import tomllib
from collections import Counter
from datetime import date
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
EVIDENCE = ROOT / "test-evidence"
RECORD = EVIDENCE / "test_record.toml"
RUN = EVIDENCE / "chrome_run.json"
TESTS = ROOT / "tests"
PRIORITIES = ("Critical", "High", "Medium", "Low")
KINDS = {"browser": "Browser", "terminal": "Terminal", "automated": "Automated", "owner": "Owner only"}
SEVERITIES = ("Critical", "High", "Medium", "Low")
RESULTS = ["Pass", "Fail", "Blocked", "Not run", "N/A"]
GENERATED = "*Built from [test_record.toml](test_record.toml) by `scripts/test_record.py`. Do not edit by hand.*"


def load() -> tuple[dict[str, Any], dict[str, Any]]:
    record = tomllib.loads(RECORD.read_text(encoding="utf-8"))
    run = json.loads(RUN.read_text(encoding="utf-8")) if RUN.exists() else {"cases": {}}
    return record, run


def test_names() -> dict[str, set[str]]:
    """Test names by file. Methods count too, and a file that imports a shared contract (tests/mail_contract.py) also
    runs that contract's tests. A case names them as file::name or file::Class::name."""
    pattern = r"^\s*(?:async )?def (test_\w+)"
    names: dict[str, set[str]] = {}
    for path in TESTS.glob("test_*.py"):
        text = path.read_text(encoding="utf-8")
        found = set(re.findall(pattern, text, re.M))
        for contract in TESTS.glob("*_contract.py"):
            if f"from {contract.stem} import" in text:
                found |= set(re.findall(pattern, contract.read_text(encoding="utf-8"), re.M))
        names[path.name] = found
    return names


def problems(record: dict[str, Any], run: dict[str, Any]) -> list[str]:
    """What is wrong with the record itself: a test name that does not exist, a missing screenshot, a bad id."""
    found: list[str] = []
    cases = {c["id"]: c for c in record["case"]}
    ids = [c["id"] for c in record["case"]] + [d["id"] for d in record["defect"]] + [s["id"] for s in record["signoff"]]
    found += [f"{i} is used twice" for i, n in Counter(ids).items() if n > 1]
    names = test_names()
    for c in record["case"]:
        for key in ("id", "area", "title", "priority", "kind", "steps", "expected"):
            if not c.get(key):
                found.append(f"{c.get('id', '?')} has no {key}")
        if not re.fullmatch(r"TC-[A-Z]+-\d\d", c["id"]):
            found.append(f"{c['id']} is not an id like TC-AREA-01")
        if c["priority"] not in PRIORITIES:
            found.append(f"{c['id']}: priority {c['priority']!r}")
        if c["kind"] not in KINDS:
            found.append(f"{c['id']}: kind {c['kind']!r}")
        if c["kind"] == "automated" and not c.get("tests"):
            found.append(f"{c['id']} is automated but names no test")
        for ref in c.get("tests", []):
            file, _, name = ref.partition("::")
            name = name.rsplit("::", 1)[-1]
            if name not in names.get(file, set()):
                found.append(f"{c['id']}: no test {ref}")
        if c["kind"] == "browser" and run.get("cases"):
            got = run["cases"].get(c["id"])
            if got is None:
                found.append(f"{c['id']} is a browser case but is not in chrome_run.json")
            else:
                found += [f"{c['id']}: missing {s}" for s in got["screenshots"] if not (EVIDENCE / s).is_file()]
    for d in record["defect"]:
        if d["case"] not in cases:
            found.append(f"{d['id']}: no case {d['case']}")
        if d["severity"] not in SEVERITIES:
            found.append(f"{d['id']}: severity {d['severity']!r}")
        for key in ("failure_evidence", "retest_evidence"):
            shot = d.get(key, "").split(" ")[0]
            if shot and not (EVIDENCE / shot).is_file():
                found.append(f"{d['id']}: missing {shot}")
    for s in record["signoff"]:
        found += [f"{s['id']}: no case {c}" for c in s["cases"] if c not in cases]
    return found


# ---- small helpers


def anchor(cid: str) -> str:
    return cid.lower()


def chrome(c: dict[str, Any], run: dict[str, Any]) -> str:
    if c["kind"] != "browser":
        return "Automated tests" if c["kind"] == "automated" else "—"
    got = run.get("cases", {}).get(c["id"])
    return f"{got['result']} {run['date']}" if got else "Not run"


def shots(c: dict[str, Any], run: dict[str, Any]) -> list[str]:
    got = run.get("cases", {}).get(c["id"])
    return list(got["screenshots"]) if got else []


def cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def numbered(steps: list[str]) -> str:
    return "\n".join(f"{n}. {step}" for n, step in enumerate(steps, 1))


def commit() -> str:
    out = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True, text=True)
    return out.stdout.strip() or "unknown"


# ---- the pages


def run_line(run: dict[str, Any]) -> str:
    if not run.get("cases"):
        return "No Chrome run is recorded yet."
    return (
        f"Latest Chrome run: {run['date']}, {run['browser']}, by {run['by']}, on commit {run['commit']}: "
        f"**{run['passed']} of {run['total']}** browser cases pass."
    )


def test_cases_page(record: dict[str, Any], run: dict[str, Any]) -> str:
    cases = record["case"]
    by_priority = Counter(c["priority"] for c in cases)
    by_kind = Counter(KINDS[c["kind"]] for c in cases)
    out = [
        "# Test cases",
        "",
        GENERATED,
        "",
        f"{record['meta']['product']}. Scope: {record['meta']['scope']}",
        "",
        f"**{len(cases)} cases:** "
        + ", ".join(f"{by_priority[p]} {p}" for p in PRIORITIES if by_priority[p])
        + "; "
        + ", ".join(f"{n} {k.lower()}" for k, n in by_kind.items())
        + ".",  # fmt: skip
        "",
        run_line(run) + " Automated tests: `uv run pytest`. Defects: [DEFECTS.md](DEFECTS.md). "
        "Sign-off: [SIGN_OFF.md](SIGN_OFF.md).",
        "",
        "**To run them by hand:**",
        "",
        *[f"- {line}" for line in record["meta"]["setup"]],
        "",
        "## Summary",
        "",
        "| ID | Title | Priority | How | Claude, Chrome | Owner, last result |",
        "|---|---|---|---|---|---|",
    ]
    for c in cases:
        out.append(
            f"| [{c['id']}](#{anchor(c['id'])}) | {cell(c['title'])} | {c['priority']} | {KINDS[c['kind']]} "
            f"| {chrome(c, run)} | {cell(c.get('owner', '—'))} |"
        )
    area = ""
    for c in cases:
        if c["area"] != area:
            area = c["area"]
            out += ["", f"## {area}"]
        details = [f"**Priority:** {c['priority']}", f"**How:** {KINDS[c['kind']]}"]
        if c.get("refs"):
            details.append("**Refs:** " + ", ".join(c["refs"]))
        if c.get("was"):
            details.append(f"**Was:** {c['was']}")
        out += ["", f'<a id="{anchor(c["id"])}"></a>', "", f"### {c['id']}: {c['title']}", "", " · ".join(details), ""]
        out += ["**Steps**", "", numbered(c["steps"]), "", f"**Expected:** {c['expected']}", ""]
        if c.get("tests"):
            out += ["**Automated tests:** " + ", ".join(f"`{t}`" for t in c["tests"]), ""]
        latest = f"**Latest:** Claude, Chrome: {chrome(c, run)}." if c["kind"] == "browser" else "**Latest:**"
        got = run.get("cases", {}).get(c["id"])
        if got and got.get("detail"):
            latest += f" ({got['detail']})"
        out += [latest + f" Owner: {c.get('owner', 'not recorded')}.", ""]
        for n, shot in enumerate(shots(c, run), 1):
            label = "Screenshot" if n == 1 else f"Screenshot {n}"
            out += [f"<details><summary>{label}</summary>", "", f"![{c['id']} in Chrome]({shot})", "", "</details>", ""]
    return "\n".join(out).rstrip() + "\n"


def defects_page(record: dict[str, Any]) -> str:
    out = [
        "# Defects and retests",
        "",
        GENERATED,
        "",
        "Every failure is logged here the moment it is found, by whoever finds it: the case, the browser, what was "
        "seen and a screenshot; then the cause, the fix and its commit; then a retest with its own screenshot. "
        "Failures before 11/07/2026 are in [BUILD_HISTORY.md](../docs/BUILD_HISTORY.md) and are not repeated here.",
        "",
        "| ID | Found | Case | Severity | Title | Status |",
        "|---|---|---|---|---|---|",
    ]
    for d in record["defect"]:
        out.append(
            f"| [{d['id']}](#{anchor(d['id'])}) | {d['found']} | [{d['case']}](TEST_CASES.md#{anchor(d['case'])}) "
            f"| {d['severity']} | {cell(d['title'])} | {d['status']} |"
        )
    for d in record["defect"]:
        before = d.get("failure_evidence", "").split(" ")[0]
        after = d.get("retest_evidence", "").split(" ")[0]
        out += [
            "",
            f'<a id="{anchor(d["id"])}"></a>',
            "",
            f"## {d['id']}: {d['title']}",
            "",
            f"**Found:** {d['found']} by {d['found_by']} · **Case:** {d['case']} · **Browser:** {d['browser']} · "
            f"**Severity:** {d['severity']} · **Status:** {d['status']}",
            "",
            "**Steps to reproduce**",
            "",
            numbered(d["steps"]),
            "",
            f"**Expected:** {d['expected']}",
            "",
            f"**Actual:** {d['actual']}",
            "",
        ]
        if before:
            out += [f"**Seen:** {d['failure_evidence']}", "", f"![{d['id']} as found]({before})", ""]
        out += [
            f"**Cause:** {d['cause']}",
            "",
            f"**Fix:** {d['fix']}" + (f" Commit {d['fix_commit']}." if d.get("fix_commit") else " Not committed yet."),
            "",
            f"**Retest:** {d['retest']}",
            "",
        ]
        if after:
            out += [f"![{d['id']} retested]({after})", ""]
    return "\n".join(out).rstrip() + "\n"


def signoff_page(record: dict[str, Any]) -> str:
    ready = [s for s in record["signoff"] if s["status"] == "Ready"]
    out = [
        "# User sign-off before go-live",
        "",
        GENERATED,
        "",
        "The checks that matter most to the business. A person signs off only when every row passes. "
        f"**{len(ready)} rows can be tested now**; the others are required before go-live and are tested when "
        "their phase is built. Each row names the test cases that prove it, in [TEST_CASES.md](TEST_CASES.md). "
        "Use the workbook (`uv run python scripts/test_record.py --workbook`) to record results and sign.",
        "",
        "| ID | Check | Why it matters | Test cases | Status | Result | Initials | Date |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for s in record["signoff"]:
        cases = ", ".join(f"[{c}](TEST_CASES.md#{anchor(c)})" for c in s["cases"]) or "—"
        out.append(f"| {s['id']} | {cell(s['check'])} | {cell(s['why'])} | {cases} | {s['status']} | | | |")
    out += [
        "",
        "## Decision",
        "",
        "- [ ] Approved for go-live",
        "- [ ] Approved with conditions:",
        "- [ ] Not approved",
        "",
        "Name: __________  Role: __________  Date: __________  Signature: __________",
        "",
    ]
    return "\n".join(out)


PAGES = {"TEST_CASES.md": test_cases_page, "DEFECTS.md": defects_page, "SIGN_OFF.md": signoff_page}


def pages(record: dict[str, Any], run: dict[str, Any]) -> dict[str, str]:
    return {
        "TEST_CASES.md": test_cases_page(record, run),
        "DEFECTS.md": defects_page(record),
        "SIGN_OFF.md": signoff_page(record),
    }


# ---- the workbook


def png_size(path: Path) -> tuple[int, int]:
    head = path.read_bytes()[:24]
    width, height = struct.unpack(">II", head[16:24])
    return int(width), int(height)


def lines_needed(text: str, width: float) -> int:
    chars = max(int(width * 1.1), 8)
    return sum(max(1, -(-len(part) // chars)) for part in str(text).split("\n"))


def workbook(record: dict[str, Any], run: dict[str, Any], target: Path) -> None:
    import xlsxwriter  # a dev dependency; only the workbook needs it

    book = xlsxwriter.Workbook(str(target))
    book.set_properties({"title": "PO Dashboard test workbook", "author": "PO Dashboard test record",
                         "comments": "Made-up data only."})  # fmt: skip
    base = {"font_name": "Calibri", "font_size": 11, "valign": "top", "text_wrap": True}
    f_text = book.add_format(base)
    f_head = book.add_format({**base, "bold": True, "font_color": "#FFFFFF", "bg_color": "#2B2B2B", "border": 1})
    f_title = book.add_format({"font_name": "Calibri", "bold": True, "font_size": 16})
    f_sub = book.add_format({**base, "bold": True, "font_size": 12})
    f_fill = book.add_format({**base, "bg_color": "#FFF8DC", "border": 1, "border_color": "#C8C8C8"})
    f_link = book.add_format({**base, "font_color": "#1F5FBF", "underline": 1})
    f_grey = book.add_format({**base, "font_color": "#707070"})
    f_pass = book.add_format({"bg_color": "#D9F2D9", "font_color": "#1E5E1E"})
    f_fail = book.add_format({"bg_color": "#F8D7DA", "font_color": "#8A1C24"})
    f_amber = book.add_format({"bg_color": "#FFF0C2", "font_color": "#7A5A00"})
    f_crit = book.add_format({**base, "bold": True, "font_color": "#B00020"})

    def colour_results(sheet: Any, area: str) -> None:
        for word, fmt in (("Pass", f_pass), ("Fail", f_fail), ("Blocked", f_amber), ("Not built", f_amber)):
            sheet.conditional_format(area, {"type": "text", "criteria": "begins with", "value": word, "format": fmt})

    def table(
        sheet: Any, headers: list[tuple[str, float]], rows: list[list[Any]], fill_from: int | None = None
    ) -> None:
        for col, (name, width) in enumerate(headers):
            sheet.set_column(col, col, width)
            sheet.write(0, col, name, f_head)
        for r, row in enumerate(rows, 1):
            height = 1
            for col, value in enumerate(row):
                fmt = f_fill if fill_from is not None and col >= fill_from else f_text
                if isinstance(value, tuple):  # (text, link)
                    sheet.write_url(r, col, value[1], f_link, value[0])
                    continue
                if col == 3 and value == "Critical":
                    fmt = f_crit
                sheet.write(r, col, value, fmt)
                height = max(height, lines_needed(value, headers[col][1]))
            sheet.set_row(r, min(15 * height + 4, 409))
        sheet.freeze_panes(1, 1)
        sheet.autofilter(0, 0, max(len(rows), 1), len(headers) - 1)

    cases = record["case"]
    by_priority = Counter(c["priority"] for c in cases)

    # Read me
    readme = book.add_worksheet("Read me")
    readme.set_column(0, 0, 110)
    readme.write(0, 0, f"{record['meta']['product']}: test workbook", f_title)
    lines = [
        ("", None),
        (f"Built {date.today().strftime('%m/%d/%Y')} from commit {commit()}. Made-up data only.", None),
        (f"Scope: {record['meta']['scope']}", None),
        (
            f"{len(cases)} test cases: "
            + ", ".join(f"{by_priority[p]} {p}" for p in PRIORITIES if by_priority[p])
            + f". {len(record['defect'])} defects logged. {len(record['signoff'])} sign-off checks.",
            None,
        ),  # fmt: skip
        (run_line(run).replace("**", ""), None),
        ("", None),
        ("The tabs", f_sub),
        (
            "Test cases: every case, step by step, with the expected result, the automated tests that cover it, the "
            "latest results, and yellow columns for this test cycle: tester, date, Safari, Chrome, notes.",
            None,
        ),
        (
            "Evidence: the screenshot of each case's result from the latest Chrome run, and the defects' screenshots.",
            None,
        ),
        (
            "Defects & retests: each failure, its cause, fix and retest; yellow rows for new ones found in this cycle.",
            None,
        ),
        ("Sign-off: the checks that matter most before go-live, and the decision with name, role and date.", None),
        ("", None),
        ("Before you start", f_sub),
        *[(f"• {line}", None) for line in record["meta"]["setup"]],
        ("", None),
        ("Recording results", f_sub),
        (
            "Pass: what you saw matches Expected. Fail: anything else; describe it in Notes, take a screenshot, and "
            "add a row on Defects & retests (next DEF number) the moment you find it. Blocked: an earlier failure "
            "stops this one. N/A: does not apply to this browser.",
            None,
        ),
        ("Write no real names, prices, addresses or documents anywhere in this workbook.", None),
        ("", None),
        (
            "The source of every case is test-evidence/test_record.toml in the repository; this workbook is built from "
            "it and is never edited back into it. Results worth keeping are copied into the record.",
            f_grey,
        ),
    ]
    for r, (text, fmt) in enumerate(lines, 1):
        readme.write(r, 0, text, fmt or f_text)

    # Where each screenshot goes on the Evidence tab, worked out first so the Test cases tab can link to it
    images: list[tuple[str, str, str, int]] = []  # key, title, file, row
    where: dict[str, int] = {}
    row = 0
    wanted = [(c["id"], f"{c['id']}: {c['title']}", s) for c in cases for s in shots(c, run)]
    for d in record["defect"]:
        for key, label in (("failure_evidence", "as found"), ("retest_evidence", "retested")):
            shot = d.get(key, "").split(" ")[0]
            if shot:
                wanted.append((f"{d['id']}-{label}", f"{d['id']} {label}: {d['title']}", shot))
    for key, title, shot in wanted:
        path = EVIDENCE / shot
        if not path.is_file():
            continue
        where.setdefault(key, row)
        images.append((key, title, shot, row))
        width, height = png_size(path)
        row += 3 + int(height * min(0.6, 760 / width) / 20)

    sheet = book.add_worksheet("Test cases")
    headers = [("ID", 12), ("Area", 16), ("Title", 34), ("Priority", 10), ("How", 11), ("Steps", 48),
               ("Expected result", 48), ("Automated tests", 34), ("Claude, Chrome", 14), ("Owner, last result", 20),
               ("Screenshot", 12), ("Tester", 12), ("Date", 11), ("Safari", 10), ("Chrome", 10),
               ("Notes / defect ID", 30)]  # fmt: skip
    rows: list[list[Any]] = []
    for c in cases:
        link: Any = ("See it", f"internal:'Evidence'!A{where[c['id']] + 1}") if c["id"] in where else ""
        rows.append([c["id"], c["area"], c["title"], c["priority"], KINDS[c["kind"]], numbered(c["steps"]),
                     c["expected"], "\n".join(c.get("tests", [])), chrome(c, run), c.get("owner", ""), link,
                     "", "", "", "", ""])  # fmt: skip
    table(sheet, headers, rows, fill_from=11)
    sheet.data_validation(1, 13, len(rows), 14, {"validate": "list", "source": RESULTS})
    colour_results(sheet, f"I2:I{len(rows) + 1}")
    colour_results(sheet, f"N2:O{len(rows) + 1}")
    sheet.activate()

    evidence = book.add_worksheet("Evidence")
    evidence.set_column(0, 0, 100)
    for _key, title, shot, at in images:
        path = EVIDENCE / shot
        width, _height = png_size(path)
        scale = min(0.6, 760 / width)
        evidence.write(at, 0, f"{title}  ({shot})", f_sub)
        evidence.insert_image(at + 1, 0, str(path), {"x_scale": scale, "y_scale": scale, "object_position": 3})

    defects = book.add_worksheet("Defects & retests")
    headers = [("ID", 10), ("Found", 11), ("Found by", 18), ("Case", 12), ("Browser", 16), ("Severity", 10),
               ("Title", 34), ("Steps to reproduce", 40), ("Expected", 30), ("Actual", 40), ("Cause", 36),
               ("Fix", 40), ("Fix commit", 11), ("Retest", 34), ("Status", 18), ("As found", 11),
               ("Retested", 11)]  # fmt: skip
    rows = []
    for d in record["defect"]:
        before = (("See it", f"internal:'Evidence'!A{where[d['id'] + '-as found'] + 1}")
                  if d["id"] + "-as found" in where else "")  # fmt: skip
        after = (("See it", f"internal:'Evidence'!A{where[d['id'] + '-retested'] + 1}")
                 if d["id"] + "-retested" in where else "")  # fmt: skip
        rows.append([d["id"], d["found"], d["found_by"], d["case"], d["browser"], d["severity"], d["title"],
                     numbered(d["steps"]), d["expected"], d["actual"], d["cause"], d["fix"],
                     d.get("fix_commit") or "not yet", d["retest"], d["status"], before, after])  # fmt: skip
    last = len(record["defect"])
    nxt = last + 1
    rows += [[f"DEF-{nxt + n:03d}", "", "", "", "", "", "", "", "", "", "", "", "", "", "", "", ""] for n in range(15)]
    table(defects, headers, rows)
    for r in range(last + 1, len(rows) + 1):
        for col in range(1, 15):
            defects.write_blank(r, col, None, f_fill)
    defects.data_validation(last + 1, 5, len(rows), 5, {"validate": "list", "source": list(SEVERITIES)})
    defects.data_validation(last + 1, 14, len(rows), 14, {"validate": "list", "source": [
        "Open", "Fixed, waiting for retest", "Verified", "Closed", "Not a defect"]})  # fmt: skip

    sign = book.add_worksheet("Sign-off")
    headers = [("ID", 8), ("Check", 54), ("Why it matters", 40), ("Test cases", 22), ("Status", 22), ("Result", 11),
               ("Tested by", 14), ("Date", 11), ("Notes", 30)]  # fmt: skip
    rows = [[s["id"], s["check"], s["why"], ", ".join(s["cases"]) or "—", s["status"], "", "", "", ""]
            for s in record["signoff"]]  # fmt: skip
    table(sign, headers, rows, fill_from=5)
    sign.data_validation(1, 5, len(rows), 5, {"validate": "list", "source": ["Pass", "Fail", "Not tested", "N/A"]})
    colour_results(sign, f"E2:F{len(rows) + 1}")
    r = len(rows) + 2
    sign.write(r, 1, "Decision", f_sub)
    sign.write(r + 1, 1, "Go-live needs every row Pass, including the Not built rows once their phase is done.", f_grey)
    for n, label in enumerate(("Decision", "Conditions", "Name", "Role", "Date", "Signature")):
        sign.write(r + 2 + n, 0, label, f_head)
        sign.write_blank(r + 2 + n, 1, None, f_fill)
    sign.data_validation(r + 2, 1, r + 2, 1, {"validate": "list", "source": [
        "Approved for go-live", "Approved with conditions", "Not approved"]})  # fmt: skip
    book.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--check", action="store_true", help="change nothing; exit 1 if anything is out of date")
    parser.add_argument("--workbook", action="store_true", help="also build the Excel workbook for a test cycle")
    args = parser.parse_args()
    record, run = load()
    wrong = problems(record, run)
    built = pages(record, run)
    stale = [name for name, text in built.items() if not (EVIDENCE / name).exists()
             or (EVIDENCE / name).read_text(encoding="utf-8") != text]  # fmt: skip
    if args.check:
        for line in wrong:
            print("problem:", line)
        for name in stale:
            print(f"out of date: test-evidence/{name} (run scripts/test_record.py)")
        return 1 if wrong or stale else 0
    if wrong:
        print("The record has problems; nothing was written:", *wrong, sep="\n  ")
        return 1
    for name, text in built.items():
        (EVIDENCE / name).write_text(text, encoding="utf-8")
    print("Wrote", ", ".join(f"test-evidence/{n}" for n in built))
    if args.workbook:
        folder = EVIDENCE / "workbooks"
        folder.mkdir(exist_ok=True)
        target = folder / f"PO-Dashboard-test-workbook-{date.today().isoformat()}.xlsx"
        if target.exists():
            print(f"{target.relative_to(ROOT)} already exists and may hold results; move or rename it first.")
            return 1
        workbook(record, run, target)
        print(f"Wrote {target.relative_to(ROOT)} (stays on this Mac; git-ignored)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
