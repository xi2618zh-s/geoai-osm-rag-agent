"""Evaluate frozen M09 public synthetic multi-turn cases; exclusive receipt."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from tests.test_m09_session_state import CASES, M09SessionTests

CASES_PATH = Path("benchmarks/data/dev/m09_turn_cases_v1.jsonl")
OUTPUT = Path("benchmarks/results/m09/dev/m09_turn_eval_v1.json")


def evaluate() -> dict:
    if len(CASES) != 16:
        raise ValueError("M09 frozen matrix changed")
    counts = {"recompute_dependents": 0, "append_filter": 0, "replace_source": 0, "compare_verified_count": 0}
    rows = []
    correct = reuse = invalidation = comparison = 0
    for case in CASES:
        fixture = M09SessionTests("test_frozen_matrix_shape")
        fixture.setUp()
        try:
            result = fixture.run_case(case)
        finally:
            fixture.tearDown()
        expected = case["expected_action"]
        counts[expected] += 1
        correct += result.accepted and result.action == {
            "recompute_dependents": "change_distance",
            "append_filter": "add_exclusion",
            "replace_source": "switch_city",
            "compare_verified_count": "compare",
        }[expected]
        reuse += expected == "append_filter" and result.reused_steps == ["step_1", "step_2"] and result.executed_steps == ["step_3"]
        invalidation += expected == "replace_source" and result.reused_steps == [] and result.executed_steps == ["step_1"]
        comparison += expected == "compare_verified_count" and result.snapshot.comparison is not None and result.snapshot.comparison.delta_count == -2
        rows.append({
            "case_id": case["case_id"], "action": result.action,
            "accepted": result.accepted, "reused_steps": result.reused_steps,
            "executed_steps": result.executed_steps,
            "version": result.snapshot.version,
            "comparison_delta": result.snapshot.comparison.delta_count if result.snapshot.comparison else None,
        })
    if any(value != 4 for value in counts.values()):
        raise ValueError("M09 task-class quotas changed")
    metrics = {
        "correct_state_update": {"passed": correct, "total": 16, "rate": correct / 16},
        "unchanged_upstream_reuse": {"passed": reuse, "total": 4, "rate": reuse / 4},
        "city_source_invalidation": {"passed": invalidation, "total": 4, "rate": invalidation / 4},
        "verified_count_comparison": {"passed": comparison, "total": 4, "rate": comparison / 4},
    }
    return {
        "schema_version": "1.0", "evaluation_id": "m09_turn_eval_v1",
        "source": "public synthetic offline DatasetRef sessions",
        "cases_sha256": hashlib.sha256(CASES_PATH.read_bytes()).hexdigest(),
        "case_count": 16, "metrics": metrics, "cases": rows,
        "thresholds": {"correct_state_update": "16/16", "unchanged_upstream_reuse": "4/4", "city_source_invalidation": "4/4", "verified_count_comparison": "4/4"},
        "passed": correct == 16 and reuse == 4 and invalidation == 4 and comparison == 4,
        "limitations": "Small visible project-authored fixtures and bounded English edits; no real model, network, production city coverage, hidden set or transit isochrone. M03 Phase 2 retrieval queries are catalog-informed.",
    }


def main() -> None:
    report = evaluate()
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    with OUTPUT.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(report, stream, ensure_ascii=False, sort_keys=True, indent=2)
        stream.write("\n")
    print(json.dumps(report["metrics"], sort_keys=True))
    if not report["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()

