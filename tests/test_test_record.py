"""The test record (test-evidence/test_record.toml) stays true: every test it names exists, every screenshot it shows
is there, the pages built from it are up to date, and the made-up sample files still do what its cases say."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from po_dashboard.ledger import orders, vendor_import
from po_dashboard.ledger import quickbooks as qb
from test_gate import env  # noqa: F401
from test_orders_web import client, make_vendor  # noqa: F401

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "tests" / "data"


def builder() -> ModuleType:
    spec = importlib.util.spec_from_file_location("test_record_builder", ROOT / "scripts" / "test_record.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_the_record_names_only_tests_and_screenshots_that_exist() -> None:
    build = builder()
    record, run = build.load()
    assert build.problems(record, run) == []


def test_the_pages_built_from_the_record_are_up_to_date() -> None:
    build = builder()
    record, run = build.load()
    for name, text in build.pages(record, run).items():
        assert (ROOT / "test-evidence" / name).read_text(encoding="utf-8") == text, (
            f"test-evidence/{name} is out of date: run uv run python scripts/test_record.py"
        )


def test_every_browser_case_has_a_step_in_the_chrome_script() -> None:
    build = builder()
    record, _ = build.load()
    script = (ROOT / "scripts" / "browser_evidence.py").read_text(encoding="utf-8")
    for case in record["case"]:
        if case["kind"] == "browser":
            assert f'@case("{case["id"]}")' in script, case["id"]


def test_the_sample_vendor_files_do_what_the_cases_say(env) -> None:  # noqa: ANN001
    with Session(env[1]) as db:
        first = vendor_import.import_vendors(db, (DATA / "vendors_sample.xlsx").read_bytes(), filename="v.xlsx")
        db.commit()
        assert len(first.created) == 3
        again = vendor_import.import_vendors(db, (DATA / "vendors_sample.xlsx").read_bytes(), filename="v.xlsx")
        assert (len(again.created), len(again.updated), len(again.unchanged)) == (0, 0, 3)
        problems = vendor_import.import_vendors(db, (DATA / "vendors_problems.csv").read_bytes(), filename="p.csv")
        messages = " ".join(row.message for row in problems.skipped)
        assert len(problems.created) == 1 and len(problems.skipped) == 3
        assert "Contact email is required" in messages and "already the contact email of" in messages
        assert "already belongs to" in messages


def test_the_bad_po_file_names_row_3(client: TestClient, env) -> None:  # noqa: ANN001, F811
    make_vendor(client, "Acme Fasteners", "acme.example")
    with Session(env[1]) as db:
        try:
            orders.import_csv(db, (DATA / "purchase_orders_bad_row.csv").read_bytes(), filename="bad.csv")
        except orders.ImportRejected as problem:
            assert any(e.row == 3 and "abc" in e.message for e in problem.errors)
        else:
            raise AssertionError("the bad file was imported")
    assert qb.is_report((DATA / "quickbooks_open_pos_110526.csv").read_bytes(), "x.csv")
