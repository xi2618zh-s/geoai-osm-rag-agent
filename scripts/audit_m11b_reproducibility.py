"""Byte-rebuild and leakage-boundary audit for M11b public dev release."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from scripts.build_m11b_dataset import ROOT, audit_dataset
from scripts.build_m11b_hidden import audit_private, PRIVATE_DIR
from scripts.build_m11b_trace_provenance import audit_traces
from src.evaluation.m11b import M11bPrediction, load_m11b_tasks, score_m11b

TASKS = ROOT / "benchmarks/data/dev/m11b_tasks_v1.jsonl"
MANIFEST = ROOT / "benchmarks/data/dev/m11b_manifest_v1.json"


def _normalized(value: str) -> str:
    return " ".join(value.casefold().split())


def audit_leakage() -> dict:
    tasks = load_m11b_tasks(TASKS)
    public_queries = {_normalized(task.query) for task in tasks}
    overlapping = []
    comparison_files = list((ROOT / "benchmarks/data/m03").rglob("*.jsonl"))
    comparison_files += [path for path in (ROOT / "benchmarks/data/dev").glob("*.jsonl")
                         if path != TASKS]
    for path in comparison_files:
        for line in path.read_text("utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            query = row.get("query")
            if isinstance(query, str) and _normalized(query) in public_queries:
                overlapping.append(f"{path.name}:{query}")
    if overlapping:
        raise ValueError(f"public benchmark query overlap: {overlapping[:5]}")
    hidden = load_m11b_tasks(PRIVATE_DIR / "m11b_test_tasks_v1.jsonl")
    hidden_queries = {_normalized(task.query) for task in hidden}
    if hidden_queries & public_queries:
        raise ValueError("dev/test exact query overlap")
    for path in comparison_files:
        for line in path.read_text("utf-8").splitlines():
            if line.strip():
                row = json.loads(line)
                query = row.get("query")
                if isinstance(query, str) and _normalized(query) in hidden_queries:
                    raise ValueError("hidden query leaked into public benchmark data")
    for folder in ("src/agent", "data/knowledge/osm", "data/fixtures"):
        for path in (ROOT / folder).rglob("*"):
            if path.is_file() and path.suffix.lower() in {".py", ".json", ".jsonl", ".geojson", ".md", ".tsv"}:
                text = _normalized(path.read_text("utf-8", errors="ignore"))
                if any(query in text for query in hidden_queries):
                    raise ValueError("hidden query leaked into prompt, knowledge, or development fixture")
    test_files = [path for path in (ROOT / "benchmarks/data/test").glob("*")
                  if path.is_file()]
    if {path.name for path in test_files} != {"README.md", "m11b_private_receipt_v1.json"}:
        raise ValueError("hidden test directory contains a tracked payload")
    return {"public_query_overlap": 0, "private_query_overlap": 0, "hidden_payload_files": 0,
            "public_task_count": len(tasks)}


def _reference_prediction(task):
    expected = task.expectation
    return M11bPrediction(task_id=task.task_id, status=expected.status,
        operation=task.task_type if task.capability == "supported" else None,
        reason_code=expected.reason_code, exact_fields=expected.exact_fields,
        steps=expected.required_steps, tool_sequence=expected.tool_sequence,
        feature_ids=expected.feature_ids,
        numeric_values={key: value.value for key, value in expected.numeric.items()},
        geometry=expected.geometry, crs=expected.crs, ranking=expected.ranking)


def audit() -> dict:
    audit_dataset(TASKS, MANIFEST)
    audit_traces()
    leakage = audit_leakage()
    audit_private()
    tasks = load_m11b_tasks(TASKS)
    manifest = json.loads(MANIFEST.read_text("utf-8"))
    actual_hash = hashlib.sha256(TASKS.read_bytes()).hexdigest()
    if actual_hash != manifest["tasks_sha256"]:
        raise ValueError("M11b manifest tasks hash mismatch")
    scores = [score_m11b(task, _reference_prediction(task)) for task in tasks]
    passed = sum(item.passed for item in scores)
    if passed != 160:
        raise ValueError(f"reference grading failed: {passed}/160")
    return {"data_version": manifest["data_version"], "tasks_sha256": actual_hash,
            "reference_grader_pass": passed, "private_reference_count": 40, "m04_trace_count": 160, "task_count": len(tasks) + 40, **leakage}


if __name__ == "__main__":
    print(json.dumps(audit(), ensure_ascii=False, sort_keys=True))



