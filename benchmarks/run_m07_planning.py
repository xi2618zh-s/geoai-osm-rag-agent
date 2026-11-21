"""Run the frozen, public M07 offline plan evaluation once per output path."""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path

from src.agent.dynamic_planner import plan_request, validate_model_plan_json

DEFAULT_CASES = Path("benchmarks/data/dev/m07_plan_cases_v2.jsonl")
DEFAULT_OUTPUT = Path("benchmarks/results/m07/dev/m07_plan_eval_v2.json")
CLASSES = {
    "search", "filtered_search", "within_distance", "nearest", "within",
    "spatial_join", "aggregate_by_region", "rank",
}
THRESHOLD = 0.95


def evaluate(cases_path: Path = DEFAULT_CASES) -> dict:
    data = cases_path.read_bytes()
    cases = [json.loads(line) for line in data.decode("utf-8").splitlines() if line]
    version = "v2" if cases_path.name.endswith("_v2.jsonl") else "v1"
    expected_classes = CLASSES | ({"aggregate_sum"} if version == "v2" else set())
    total = 90 if version == "v2" else 80
    supported_total = 80 if version == "v2" else 70
    if len(cases) != total or Counter(x["task_class"] for x in cases) != {name: 10 for name in expected_classes}:
        raise ValueError("M07 frozen case count or class coverage differs from " + version)
    supported = [x for x in cases if x["expected"]["status"] == "plan"]
    unsupported = [x for x in cases if x["expected"]["status"] == "unsupported"]
    if len(supported) != supported_total or len(unsupported) != 10:
        raise ValueError("M07 metric denominators differ from freeze")
    legal = selected = parameters = rejected = 0
    failures = []
    for case in cases:
        expected = case["expected"]
        result = plan_request(case["request"])
        if expected["status"] == "plan":
            legal_ok = False
            if result.status == "plan":
                try:
                    validate_model_plan_json(result.model_dump_json(), case["request"])
                    legal_ok = True
                except Exception:
                    pass
            tools_ok = legal_ok and [step.tool for step in result.tool_steps] == expected["tools"]
            params_ok = legal_ok and result.business_parameters == expected["parameters"]
            legal += legal_ok
            selected += tools_ok
            parameters += params_ok
            if not (legal_ok and tools_ok and params_ok):
                failures.append({"case_id": case["case_id"], "status": result.status, "legal": legal_ok, "tools": tools_ok, "parameters": params_ok})
        else:
            ok = result.status == "unsupported" and result.error_code == "M07_UNSUPPORTED_TASK" and not result.tool_steps
            rejected += ok
            if not ok:
                failures.append({"case_id": case["case_id"], "status": result.status, "error_code": result.error_code})
    metrics = {
        "plan_legality": {"passed": legal, "total": supported_total, "rate": legal / supported_total},
        "tool_selection_accuracy": {"passed": selected, "total": supported_total, "rate": selected / supported_total},
        "parameter_accuracy": {"passed": parameters, "total": supported_total, "rate": parameters / supported_total},
        "unsupported_rejection": {"passed": rejected, "total": 10, "rate": rejected / 10},
    }
    passed = all(metrics[name]["rate"] >= THRESHOLD for name in ("plan_legality", "tool_selection_accuracy", "parameter_accuracy")) and rejected == 10
    return {
        "schema_version": "1.0", "evaluation_id": "m07_plan_eval_" + version,
        "source": "public synthetic offline development cases",
        "cases_sha256": hashlib.sha256(data).hexdigest(),
        "case_count": total, "supported_count": supported_total, "unsupported_count": 10,
        "threshold": THRESHOLD, "metrics": metrics, "failures": failures,
        "passed": passed,
        "limitation": "Constrained command grammar and declared fixture metadata; no unrestricted language, real model, hidden set, or tool execution. M03 Phase 2 retrieval queries are catalog-informed.",
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    report = evaluate(args.cases)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(report, stream, ensure_ascii=False, sort_keys=True, indent=2)
        stream.write("\n")
    print(json.dumps(report["metrics"], sort_keys=True))
    if not report["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
