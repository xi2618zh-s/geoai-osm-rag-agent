"""P4 plan-compilation benchmark with an optional real multi-tag E2E run."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import statistics
import time
from typing import Any

from benchmarks.run_benchmark import percentile
from src.agent.planner import PlanCompilationError, compile_natural_language_plan
from src.agent.workflow import run_agent_workflow
from src.rag.hybrid_retriever import build_retriever


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATASET = ROOT / "benchmarks" / "data" / "workflow_benchmark_v1.jsonl"
DEFAULT_JSON = ROOT / "benchmarks" / "results" / "p4_workflow.json"
DEFAULT_MD = ROOT / "benchmarks" / "results" / "p4_workflow.md"


def load_cases(path: Path) -> list[dict[str, Any]]:
    with Path(path).open("r", encoding="utf-8") as stream:
        return [json.loads(line) for line in stream if line.strip()]


def _tags(plan) -> set[str]:
    return {f"{item.key}={item.value}" for item in plan.intent.filters}


def _rate(rows: list[dict[str, Any]], key: str) -> float:
    return sum(bool(row.get(key)) for row in rows) / len(rows) if rows else 0.0


def evaluate_plans(cases: list[dict[str, Any]]) -> dict[str, Any]:
    retriever = build_retriever(local_files_only=True)
    rows: list[dict[str, Any]] = []
    latencies: list[float] = []
    for index, case in enumerate(cases, start=1):
        print(f"[P4 plan {index}/{len(cases)}] {case['id']}")
        started = time.perf_counter()
        try:
            plan = compile_natural_language_plan(case["query"], retriever=retriever)
            error_code = None
        except PlanCompilationError as error:
            plan = None
            error_code = error.code
        elapsed = (time.perf_counter() - started) * 1000
        latencies.append(elapsed)
        predicted_tags = _tags(plan) if plan else set()
        expected_tags = set(case["expected_filters"])
        row = {
            "id": case["id"],
            "query": case["query"],
            "difficulty": case["difficulty"],
            "plan_valid": plan is not None,
            "operation_correct": bool(
                plan and plan.intent.operation == case["expected_operation"]
            ),
            "place_correct": bool(plan and plan.intent.place == case["expected_place"]),
            "filters_correct": predicted_tags == expected_tags,
            "exact_plan_correct": bool(
                plan
                and plan.intent.operation == case["expected_operation"]
                and plan.intent.place == case["expected_place"]
                and predicted_tags == expected_tags
            ),
            "predicted_filters": sorted(predicted_tags),
            "expected_filters": sorted(expected_tags),
            "step_count": len(plan.steps) if plan else 0,
            "latency_ms": round(elapsed, 3),
            "error_code": error_code,
        }
        rows.append(row)
    return {
        "n": len(rows),
        "plan_validity_rate": _rate(rows, "plan_valid"),
        "operation_accuracy": _rate(rows, "operation_correct"),
        "place_accuracy": _rate(rows, "place_correct"),
        "filter_set_accuracy": _rate(rows, "filters_correct"),
        "exact_plan_accuracy": _rate(rows, "exact_plan_correct"),
        "latency_ms": {
            "mean": statistics.mean(latencies) if latencies else None,
            "p50": percentile(latencies, 50),
            "p95": percentile(latencies, 95),
        },
        "rows": rows,
    }


def evaluate_e2e(query: str | None) -> dict[str, Any] | None:
    if not query:
        return None
    print(f"[P4 E2E] {query}")
    started = time.perf_counter()
    result = run_agent_workflow(query)
    elapsed = (time.perf_counter() - started) * 1000
    records = result.get("step_records", [])
    succeeded = sum(item.get("status") == "succeeded" for item in records)
    return {
        "query": query,
        "success": bool(result.get("success")),
        "operation": result.get("operation"),
        "chosen_tags": result.get("chosen_tags", []),
        "count": result.get("count"),
        "counts_by_type": result.get("counts_by_type"),
        "matched_counts_by_tag": result.get("matched_counts_by_tag"),
        "step_success_rate": succeeded / len(records) if records else 0.0,
        "step_count": len(records),
        "recovery_count": result.get("recovery_count", 0),
        "clip_cache_hit": result.get("clip_cache_hit"),
        "elapsed_ms": round(elapsed, 3),
        "workflow_id": result.get("workflow_id"),
        "trace_id": result.get("trace_id"),
        "geojson_filename": result.get("geojson_filename"),
        "error_code": result.get("error_code"),
        "step_records": records,
    }


def _pct(value: float) -> str:
    return f"{value * 100:.1f}%"


def render(report: dict[str, Any]) -> str:
    plan = report["plan_compilation"]
    lines = [
        "# P4 Constrained Workflow Benchmark",
        "",
        f"Generated: {report['generated_at']}",
        f"Dataset: `{report['dataset']}`",
        "",
        "## Plan compilation",
        "",
        "| Metric | Result |",
        "|---|---:|",
        f"| Cases | {plan['n']} |",
        f"| Plan validity | {_pct(plan['plan_validity_rate'])} |",
        f"| Operation accuracy | {_pct(plan['operation_accuracy'])} |",
        f"| Place accuracy | {_pct(plan['place_accuracy'])} |",
        f"| Filter-set accuracy | {_pct(plan['filter_set_accuracy'])} |",
        f"| Exact-plan accuracy | {_pct(plan['exact_plan_accuracy'])} |",
        f"| Planning latency P50 | {plan['latency_ms']['p50']:.1f} ms |",
        f"| Planning latency P95 | {plan['latency_ms']['p95']:.1f} ms |",
        "",
        "## Per-case results",
        "",
        "| Case | Valid | Exact | Predicted filters |",
        "|---|---:|---:|---|",
    ]
    for row in plan["rows"]:
        lines.append(
            f"| {row['id']} | {row['plan_valid']} | {row['exact_plan_correct']} | "
            f"{', '.join(row['predicted_filters']) or '-'} |"
        )
    e2e = report.get("e2e")
    if e2e:
        lines.extend(
            [
                "",
                "## Real multi-tag E2E",
                "",
                "| Metric | Result |",
                "|---|---:|",
                f"| Success | {e2e['success']} |",
                f"| Operation | {e2e['operation']} |",
                f"| Chosen tags | {', '.join(e2e['chosen_tags'])} |",
                f"| Features | {e2e['count']} |",
                f"| Step success | {_pct(e2e['step_success_rate'])} |",
                f"| Steps | {e2e['step_count']} |",
                f"| Recovered steps | {e2e['recovery_count']} |",
                f"| Clip cache hit | {e2e['clip_cache_hit']} |",
                f"| E2E latency | {e2e['elapsed_ms']:.1f} ms |",
            ]
        )
    lines.extend(
        [
            "",
            "## Scope and limitations",
            "",
            "- Natural-language P4 planning deliberately supports explicit English OR queries only.",
            "- Free-form AND is rejected because ordinary conjunction is ambiguous; structured plans can request object-level intersection.",
            "- The 12 cases and tag catalog are project-authored, not an external production benchmark.",
            "- This is a constrained deterministic planner and allow-listed tool workflow, not autonomous planning or multi-agent execution.",
        ]
    )
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--output-json", type=Path, default=DEFAULT_JSON)
    parser.add_argument("--output-md", type=Path, default=DEFAULT_MD)
    parser.add_argument("--e2e-query")
    args = parser.parse_args()
    cases = load_cases(args.dataset)
    try:
        dataset_label = args.dataset.resolve().relative_to(ROOT.resolve()).as_posix()
    except ValueError:
        dataset_label = args.dataset.name
    report = {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "dataset": dataset_label,
        "plan_compilation": evaluate_plans(cases),
        "e2e": evaluate_e2e(args.e2e_query),
    }
    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_md.parent.mkdir(parents=True, exist_ok=True)
    with args.output_json.open("w", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
    args.output_md.write_text(render(report), encoding="utf-8")
    print(f"[P4] Wrote {args.output_json}")
    print(f"[P4] Wrote {args.output_md}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
