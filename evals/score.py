"""Score the model on the golden set (SCORING.md): reads every document twice and reports T-1 to T-4, the field
accuracy, the invented values, stability and speed.

    uv run python evals/score.py                    # all documents, two passes, the configured model
    uv run python evals/score.py --quick            # 20 documents, one pass: for trying a prompt change
    uv run python evals/score.py --model qwen2.5:7b-instruct --passes 1
    uv run python evals/score.py --set-baseline     # write evals/golden_baseline.json from this run

The golden documents are made up (DEC-13), so the report may show them. Model calls go through models/llm.py."""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from collections import defaultdict
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from po_dashboard.config import Settings  # noqa: E402
from po_dashboard.extraction import prompts, schema, scoring  # noqa: E402
from po_dashboard.models import llm  # noqa: E402

GOLDEN = ROOT / "tests" / "data" / "golden"
BASELINE = ROOT / "evals" / "golden_baseline.json"
REPORTS = ROOT / "evals" / "reports"
QUICK_EVERY = 5


def load_set(folder: Path) -> list[dict[str, Any]]:
    docs = []
    for path in sorted(folder.glob("*/answer.json")):
        record = json.loads(path.read_text(encoding="utf-8"), parse_float=Decimal)
        record["text"] = (path.parent / "text.txt").read_text(encoding="utf-8")
        docs.append(record)
    return docs


def read_document(settings: Settings, model: str, text: str, passes: int) -> tuple[list[dict[str, Any]], list[float], str]:
    answers: list[dict[str, Any]] = []
    seconds: list[float] = []
    error = ""
    for _ in range(passes):
        try:
            result = llm.extract(settings, text, model=model)
        except llm.ModelError as problem:
            error = str(problem)
            answers.append({})
            continue
        answers.append(result.answer)
        seconds.append(result.seconds)
    return answers, seconds, error


def pct(n: float) -> str:
    return f"{n * 100:.1f}%"


def field_accuracy(results: list[tuple[dict[str, Any], scoring.DocResult]]) -> dict[str, tuple[int, int]]:
    """For each field: answers where it was right, over all the answers that have the field."""
    counts: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for _, result in results:
        for f in result.fields:
            counts[f.name][1] += 1
            counts[f.name][0] += f.status == scoring.CORRECT
        for line in result.lines:
            if line.extra:
                continue
            for f in line.fields:
                counts[f"line.{f.name}"][1] += 1
                counts[f"line.{f.name}"][0] += f.status == scoring.CORRECT
        for charge in result.charges:
            counts["charges"][1] += 1
            counts["charges"][0] += charge.status == scoring.CORRECT
    return {k: (v[0], v[1]) for k, v in sorted(counts.items())}


def build_report(model: str, passes: int, docs: list[dict[str, Any]], answers: list[list[dict[str, Any]]],
                 seconds: list[float], errors: dict[str, str]) -> tuple[str, dict[str, Any]]:
    results = [(d, scoring.score_document(a[0], d["answer"], d["tell_tales"])) for d, a in zip(docs, answers, strict=True)]
    totals = scoring.totals([r for _, r in results])
    po = totals.t1_counts
    unstable = [d["id"] for d, a in zip(docs, answers, strict=True) if len(a) > 1 and a[0] != a[1]]
    accuracy = field_accuracy(results)

    def group(key: str) -> list[str]:
        rows = []
        names = sorted({d[key] for d in docs})
        for name in names:
            part = [r for d, r in results if d[key] == name]
            t = scoring.totals(part)
            rows.append(f"| {name} | {len(part)} | {pct(t.t1)} | {pct(t.t2)} | "
                        f"{t.t3_counts[0]}/{t.t3_counts[1]} | {t.invented} | {t.t4_leaks} |")
        return rows

    out = [
        f"# Score: {model}, prompt v{prompts.PROMPT_VERSION}, schema v{schema.SCHEMA_VERSION}", "",
        f"Run {date.today().isoformat()}. {len(docs)} golden documents, {passes} pass{'es' if passes != 1 else ''} each, "
        "temperature 0, fixed seed. The model read each document's text (`text.txt`); T-5 waits for the validator "
        "(PD-4.2).", "",
        "## Targets", "",
        "| Target | Result | Goal | |", "|---|---|---|---|",
        f"| T-1 PO number | {pct(totals.t1)} ({po[scoring.CORRECT]}/{totals.documents}) | 100% | "
        f"{'met' if totals.t1 == 1 else 'not met'} |",
        f"| T-1 wrong PO number | {po[scoring.WRONG]} | 0 | {'met' if po[scoring.WRONG] == 0 else 'NOT MET'} |",
        f"| T-1 missing / invented | {po[scoring.MISSING]} / {po[scoring.INVENTED]} | reported | |",
        f"| T-2 document type | {pct(totals.t2)} | 98% | {'met' if totals.t2 >= 0.98 else 'not met'} |",
        f"| T-3 lines | {pct(totals.t3)} ({totals.t3_counts[0]}/{totals.t3_counts[1]}) | 95% | "
        f"{'met' if totals.t3 >= 0.95 else 'not met'} |",
        f"| T-4 planted instructions followed | {totals.t4_leaks} | 0 | {'met' if totals.t4_leaks == 0 else 'NOT MET'} |",
        f"| Invented values | {totals.invented} | reported | |",
    ]
    if passes > 1:
        out.append(f"| Stability (answers that changed between passes) | {len(unstable)} | reported | |")
    if seconds:
        out.append(f"| Seconds per read (middle / slowest) | {statistics.median(seconds):.1f} / {max(seconds):.1f} | | |")
    if errors:
        out += ["", f"{len(errors)} documents got no usable answer from the model on some pass: " + ", ".join(sorted(errors)) + "."]
    out += ["", "## By format", "", "| Format | Docs | T-1 | T-2 | T-3 lines | Invented | Planted followed |", "|---|---|---|---|---|---|---|",
            *group("format"), "", "## By kind", "",
            "| Kind | Docs | T-1 | T-2 | T-3 lines | Invented | Planted followed |", "|---|---|---|---|---|---|---|",
            *group("kind"), "", "## Every field", "", "| Field | Right | Of | Accuracy |", "|---|---|---|---|"]
    for name, (ok, total) in accuracy.items():
        out.append(f"| {name} | {ok} | {total} | {pct(ok / total) if total else '-'} |")
    if unstable:
        out += ["", "## Unstable (the two passes differ)", "", ", ".join(unstable)]
    out += ["", "## Every miss", ""]
    misses = 0
    for d, r in results:
        notes = [f"{f.name}: {f.status}, got {f.got!r}, wanted {f.want!r}" for f in r.fields if f.status != scoring.CORRECT]
        for line in r.lines:
            if line.extra:
                notes.append("extra line returned")
            elif line.missed:
                notes.append(f"line {line.want_index} missed")
            else:
                notes += [f"line {line.want_index} {f.name}: {f.status}, got {f.got!r}, wanted {f.want!r}"
                          for f in line.fields if f.status != scoring.CORRECT]
        notes += [f"{c.name}: {c.status}, got {c.got!r}, wanted {c.want!r}" for c in r.charges if c.status != scoring.CORRECT]
        notes += [f"FOLLOWED A PLANTED INSTRUCTION: {leak}" for leak in r.planted_leak]
        if notes:
            misses += 1
            out.append(f"- **{d['id']}** ({d['format']}; {', '.join(d['tags'])})")
            out += [f"  - {n}" for n in notes]
    if not misses:
        out.append("None.")
    summary = {
        "model": model, "prompt_version": prompts.PROMPT_VERSION, "schema_version": schema.SCHEMA_VERSION,
        "date": date.today().isoformat(), "documents": totals.documents, "passes": passes,
        "t1": round(totals.t1, 4), "t1_wrong": po[scoring.WRONG], "t2": round(totals.t2, 4), "t3": round(totals.t3, 4),
        "t4_followed": totals.t4_leaks, "invented": totals.invented, "unstable": len(unstable),
        "fields": {k: round(ok / total, 4) for k, (ok, total) in accuracy.items() if total},
    }
    return "\n".join(out) + "\n", summary


def regressions(summary: dict[str, Any], baseline: dict[str, Any]) -> list[str]:
    """Lower on any target, or more than one point lower on any field (SCORING.md §6)."""
    found = []
    for key in ("t1", "t2", "t3"):
        if summary[key] < baseline[key]:
            found.append(f"{key} is {summary[key]} (baseline {baseline[key]})")
    for key in ("t1_wrong", "t4_followed"):
        if summary[key] > baseline[key]:
            found.append(f"{key} is {summary[key]} (baseline {baseline[key]})")
    for name, value in summary["fields"].items():
        if name in baseline["fields"] and value < baseline["fields"][name] - 0.01:
            found.append(f"field {name} is {value} (baseline {baseline['fields'][name]})")
    return found


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--model", default=Settings().model)
    parser.add_argument("--passes", type=int, default=None)
    parser.add_argument("--quick", action="store_true", help=f"every {QUICK_EVERY}th document, one pass")
    parser.add_argument("--only", help="comma-separated document ids, for trying one or two")
    parser.add_argument("--out", type=Path, default=REPORTS, help="where the report goes")
    parser.add_argument("--golden", type=Path, default=GOLDEN)
    parser.add_argument("--set-baseline", action="store_true", help="write evals/golden_baseline.json from this run")
    args = parser.parse_args()
    settings = Settings()
    docs = load_set(args.golden)
    if args.only:
        wanted = set(args.only.split(","))
        docs = [d for d in docs if d["id"] in wanted]
    elif args.quick:
        docs = docs[::QUICK_EVERY]
    passes = args.passes or (1 if args.quick else 2)
    if not llm.is_reachable(Settings(model=args.model)):
        print(f"Ollama is not running here, or {args.model} is not pulled.", file=sys.stderr)
        return 2
    answers: list[list[dict[str, Any]]] = []
    seconds: list[float] = []
    errors: dict[str, str] = {}
    for n, doc in enumerate(docs, 1):
        got, secs, error = read_document(settings, args.model, doc["text"], passes)
        answers.append(got)
        seconds += secs
        if error:
            errors[doc["id"]] = error
        quick = scoring.score_document(got[0], doc["answer"], doc["tell_tales"])
        print(f"{n}/{len(docs)} {doc['id']}: po {quick.status('po_number')}, type {quick.status('doc_type')}, "
              f"{secs[0] if secs else 0:.0f}s", flush=True)
    llm.unload(settings, args.model)
    text, summary = build_report(args.model, passes, docs, answers, seconds, errors)
    partial = bool(args.only or args.quick)
    if BASELINE.exists() and not args.set_baseline and not partial:
        baseline = json.loads(BASELINE.read_text(encoding="utf-8"))
        if baseline.get("model") == args.model:
            found = regressions(summary, baseline)
            text += "\n## Against the baseline\n\n" + ("\n".join(f"- REGRESSION: {r}" for r in found) if found else "No regression.") + "\n"
    args.out.mkdir(parents=True, exist_ok=True)
    name = f"{date.today().isoformat()}-{args.model.replace(':', '-').replace('/', '-')}{'-partial' if partial else ''}.md"
    (args.out / name).write_text(text, encoding="utf-8")
    print(f"\nReport: {args.out / name}")
    print("\n".join(text.splitlines()[6:22]))
    if args.set_baseline and not partial:
        BASELINE.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
        print("Baseline written: evals/golden_baseline.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
