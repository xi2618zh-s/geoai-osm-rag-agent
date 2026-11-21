"""M12a formal evaluation (m12a-protocol-v1).

``run``     verify the freeze manifest and the oracle references, run all 13 frozen
            cases twice in fresh registries, require identical deterministic
            receipts, score against the frozen thresholds and write exclusive outputs.
``receipt`` run once and print the deterministic receipt SHA-256 (used by the audit).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import platform
import subprocess
import sys
import tempfile
from importlib import metadata
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import build_m12a_references, freeze_m12a
from src.evaluation import m12a
from src.scenarios.html import render_scenario_html

RESULTS = ROOT / "benchmarks/results/m12/m12a/formal_v1"
DEMO_HTML = ROOT / "docs/demo/m12a_school_coverage_gaps_v1.html"
SNAPSHOT = RESULTS / "scenario_report_m12a_A1.json"
FINGERPRINT_FILES = ("src/scenarios", "src/evaluation/m12a.py", "benchmarks/run_m12a_scenario.py")


def canonical(value) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")


def source_fingerprint() -> str:
    files = []
    for name in FINGERPRINT_FILES:
        path = ROOT / name
        files += sorted(p for p in path.rglob("*.py") if "__pycache__" not in p.parts) if path.is_dir() else [path]
    lines = [f"{p.relative_to(ROOT).as_posix()}:{hashlib.sha256(p.read_bytes()).hexdigest()}" for p in files]
    return "sha256:" + hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()


def preflight() -> dict:
    manifest = freeze_m12a.verify()
    rebuilt = canonical(build_m12a_references.build())
    if rebuilt != build_m12a_references.OUTPUT.read_bytes():
        raise SystemExit("M12a references differ from an oracle rebuild")
    return manifest


def evaluate_once() -> dict:
    with tempfile.TemporaryDirectory(prefix="m12a_eval_") as tmp:
        result = m12a.evaluate(Path(tmp))
    receipt = m12a.deterministic_receipt(result["records"], result["scores"])
    return {**result, "receipt": receipt, "receipt_bytes": canonical(receipt)}


def _git() -> dict:
    def run(*args):
        try:
            return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip()
        except Exception:
            return None
    return {"commit": run("rev-parse", "HEAD"), "dirty": bool(run("status", "--porcelain"))}


def markdown(report: dict) -> str:
    lines = [
        "# M12a school coverage gaps — formal evaluation (m12a-protocol-v1)", "",
        f"- Cases passed: **{report['cases_passed']}/{report['case_count']}** (threshold {report['thresholds']['cases_passed']}/"
        f"{report['thresholds']['case_count']}); deterministic receipts identical across two runs: {report['deterministic']}.",
        f"- Freeze manifest {report['freeze_manifest_sha256']}; receipt {report['receipt_sha256']}; "
        f"source fingerprint {report['source_fingerprint']}.",
        "- Front end: restricted command grammar (m12a-grammar-v1), not open-ended natural-language input. No LLM calls.",
        "- Public dev cases written by the project author; references from an independent oracle by the same author "
        "(independent implementation, not independent labelling); not a hidden test set.", "",
        "| Case | Category | Passed | Failed components |", "|---|---|---|---|",
    ]
    for item in report["cases"]:
        failed = [name for name, ok in item["components"].items() if not ok]
        lines.append(f"| {item['case_id']} | {item['category']} | {item['passed']} | {', '.join(failed) or '-'} |")
    lines += ["", "Population sums cover the SCB cells returned for the study rectangle (including edge cells); "
              "they are not the exact population of the 20x20 km area. Distances above 2000 m are upper bounds.", ""]
    return "\n".join(lines)


def cmd_run(_args) -> int:
    if RESULTS.exists() or DEMO_HTML.exists():
        raise SystemExit("M12a formal results already exist; refusing to overwrite")
    manifest = preflight()
    first, second = evaluate_once(), evaluate_once()
    deterministic = first["receipt_bytes"] == second["receipt_bytes"]
    cases = {case["case_id"]: case for case in m12a.load_cases()}
    scores = first["scores"]
    passed = sum(1 for score in scores if score["passed"])
    thresholds = manifest["thresholds"]
    report = {
        "schema": "m12a-formal-report-v1", "protocol": manifest["protocol"],
        "freeze_manifest_sha256": hashlib.sha256(freeze_m12a.MANIFEST.read_bytes()).hexdigest(),
        "receipt_sha256": hashlib.sha256(first["receipt_bytes"]).hexdigest(),
        "source_fingerprint": source_fingerprint(), "git": _git(), "deterministic": deterministic,
        "thresholds": thresholds, "cases_passed": passed, "case_count": len(scores),
        "accepted": deterministic and passed == thresholds["cases_passed"] == len(scores) == thresholds["case_count"],
        "cases": [{**score, "category": cases[score["case_id"]]["category"]} for score in scores],
        "environment": {"python": platform.python_version(),
                        "packages": {name: metadata.version(name) for name in ("shapely", "pyproj", "pydantic")}},
        "llm_calls": 0,
    }
    snapshot_report, _ = first["snapshot"]
    snapshot_bytes = canonical(snapshot_report.model_dump(mode="json"))
    page = render_scenario_html(snapshot_report)
    RESULTS.mkdir(parents=True)
    with (RESULTS / "deterministic_receipt.json").open("xb") as stream:
        stream.write(first["receipt_bytes"])
    with (RESULTS / "report.json").open("xb") as stream:
        stream.write(canonical(report))
    with (RESULTS / "report.md").open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(markdown(report))
    with (RESULTS / "case_results.jsonl").open("x", encoding="utf-8", newline="\n") as stream:
        for record, score in zip(first["records"], scores):
            stream.write(json.dumps({"record": record, "score": score}, ensure_ascii=False, sort_keys=True) + "\n")
    with SNAPSHOT.open("xb") as stream:
        stream.write(snapshot_bytes)
    DEMO_HTML.parent.mkdir(parents=True, exist_ok=True)
    with DEMO_HTML.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(page)
    print(json.dumps({"accepted": report["accepted"], "cases_passed": passed, "case_count": len(scores),
                      "deterministic": deterministic, "receipt_sha256": report["receipt_sha256"]}))
    return 0 if report["accepted"] else 2


def cmd_receipt(_args) -> int:
    preflight()
    print(hashlib.sha256(evaluate_once()["receipt_bytes"]).hexdigest())
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=("run", "receipt"))
    args = parser.parse_args(argv)
    return {"run": cmd_run, "receipt": cmd_receipt}[args.command](args)


if __name__ == "__main__":
    raise SystemExit(main())
