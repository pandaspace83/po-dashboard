"""The golden set (SCORING.md §5): the generator rebuilds the committed set exactly from its seed, the set has the
mix DEC-49 asks for, and every answer file is well formed and agrees with its document. No model is called."""

from __future__ import annotations

import email
import filecmp
import importlib.util
import json
import re
import sys
from collections import Counter
from decimal import Decimal
from email import policy
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from openpyxl import load_workbook

from po_dashboard.extraction import schema, scoring

ROOT = Path(__file__).resolve().parent.parent
GOLDEN = ROOT / "tests" / "data" / "golden"


def generator() -> ModuleType:
    spec = importlib.util.spec_from_file_location("golden_generate", ROOT / "evals" / "golden" / "generate.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def answers() -> list[dict[str, Any]]:
    paths = sorted(GOLDEN.glob("*/answer.json"))
    return [json.loads(p.read_text(encoding="utf-8"), parse_float=Decimal) for p in paths]


def same_tree(a: Path, b: Path) -> list[str]:
    differences: list[str] = []
    compared = filecmp.dircmp(a, b)
    differences += [f"only in one: {name}" for name in compared.left_only + compared.right_only]
    differences += [f"{a.name}/{name}" for name in compared.diff_files + compared.funny_files]
    # dircmp's file check is shallow (size and time); compare the bytes too
    for name in compared.common_files:
        if (a / name).read_bytes() != (b / name).read_bytes():
            differences.append(f"{a.name}/{name}")
    for sub in compared.common_dirs:
        differences += same_tree(a / sub, b / sub)
    return sorted(set(differences))


def test_the_generator_rebuilds_the_committed_set_exactly(tmp_path: Path) -> None:
    generator().generate(tmp_path / "golden")
    assert same_tree(tmp_path / "golden", GOLDEN) == []


def test_the_set_has_the_mix_the_decision_asks_for() -> None:
    found = answers()
    assert 95 <= len(found) <= 105
    assert Counter(a["kind"] for a in found) == {"ack": 30, "ship": 22, "inv": 25, "po": 8, "other": 15}
    formats = Counter(a["format"] for a in found)
    assert set(formats) == {"email", "pdf", "scan", "xlsx", "auto"}
    assert max(formats.values()) <= len(found) * 0.4  # no format dominates
    tags = Counter(t for a in found for t in a["tags"] if not t.startswith("duplicate_of"))
    for hard in ("subject_po", "quoted_old", "vendor_near", "no_lineno", "vendor_lineno", "tariff_line", "freight"):
        assert tags[hard] >= 3, hard
    assert sum(1 for a in found if a["tell_tales"]) == 8
    assert sum(1 for a in found if any(t.startswith("duplicate_of") for t in a["tags"])) >= 3


def test_every_answer_has_exactly_the_schema_fields() -> None:
    for a in answers():
        assert set(a["answer"]) == set(schema.TOP_FIELDS), a["id"]
        assert a["answer"]["doc_type"] in schema.DOC_TYPES
        for line in a["answer"]["lines"]:
            assert set(line) == set(schema.LINE_FIELDS), a["id"]
        for charge in a["answer"]["charges"]:
            assert charge["kind"] in schema.CHARGE_KINDS


def test_an_invoice_total_is_its_goods_plus_its_charges() -> None:
    invoices = [a for a in answers() if a["answer"]["doc_type"] == "invoice"]
    assert invoices
    for a in invoices:
        body = a["answer"]
        goods = sum(Decimal(line["qty"]) * Decimal(str(line["unit_price"])) for line in body["lines"])
        charges = sum(Decimal(str(c["amount"])) for c in body["charges"])
        assert goods + charges == Decimal(str(body["total"])), a["id"]


def test_a_charge_is_never_a_line() -> None:
    for a in answers():
        for line in a["answer"]["lines"]:
            assert "tariff" not in str(line["item"]).lower() and "freight" not in str(line["item"]).lower(), a["id"]


def test_a_document_scores_perfectly_against_its_own_answer() -> None:
    for a in answers():
        result = scoring.score_document(a["answer"], a["answer"], a["tell_tales"])
        assert result.invented == 0 and result.extra_lines == 0 and result.missed_lines == 0, a["id"]
        assert all(f.status == scoring.CORRECT for f in result.fields), a["id"]


def text_of(folder: str) -> str:
    return (GOLDEN / folder / "text.txt").read_text(encoding="utf-8")


def test_the_po_number_is_in_the_text_and_only_the_subject_when_the_tag_says_so() -> None:
    for a in answers():
        po = a["answer"]["po_number"]
        if po is None:
            continue
        text = text_of(a["id"])
        assert po in text, a["id"]
        if "subject_po" in a["tags"]:
            body = text.split("\n\n", 1)[1]
            head = text.split("\n\n", 1)[0]
            assert po in head and po not in body.split("-----Original Message")[0], a["id"]


def test_a_quoted_older_message_carries_another_po_number() -> None:
    for a in answers():
        if "quoted_old" in a["tags"]:
            quoted = text_of(a["id"]).split("-----Original Message")[1]
            others = set(re.findall(r"\b45\d{5}\b", quoted)) - {a["answer"]["po_number"]}
            assert others, a["id"]


def test_the_planted_instruction_is_in_the_document_and_not_in_the_answer() -> None:
    for a in answers():
        if not a["tell_tales"]:
            continue
        text = text_of(a["id"])
        for value in a["tell_tales"]:
            assert value in text, a["id"]
            assert value not in json.dumps(a["answer"], default=str), a["id"]


def test_the_files_are_what_their_names_say() -> None:
    for a in answers():
        folder = GOLDEN / a["id"]
        files = sorted(p.name for p in folder.iterdir())
        assert "text.txt" in files and "answer.json" in files, a["id"]
        document = next(n for n in files if n.startswith("document."))
        data = (folder / document).read_bytes()
        if document.endswith(".pdf"):
            assert data.startswith(b"%PDF")
        elif document.endswith(".png"):
            assert data.startswith(b"\x89PNG")
        elif document.endswith(".xlsx"):
            assert load_workbook(folder / document).active is not None
        else:
            message = email.message_from_bytes(data, policy=policy.default)
            assert message["Subject"] and message["From"]


def test_no_real_looking_addresses_or_names_are_used() -> None:
    for path in GOLDEN.glob("*/text.txt"):
        for address in re.findall(r"[\w.]+@([\w.-]+)", path.read_text(encoding="utf-8")):
            assert address.endswith(".test"), (path.parent.name, address)


@pytest.mark.parametrize("name", ["GOLDEN_SET.md"])
def test_the_listing_names_every_document(name: str) -> None:
    listing = (GOLDEN / name).read_text(encoding="utf-8")
    for a in answers():
        assert f"| {a['id']} |" in listing
