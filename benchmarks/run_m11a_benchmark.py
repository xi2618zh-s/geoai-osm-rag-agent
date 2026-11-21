"""Run the offline M11a framework-calibration benchmark."""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import statistics
import subprocess
from typing import Sequence

from src.evaluation import (
    BenchmarkRunReport,
    RunMetadata,
    RunSummary,
    evaluate_task,
    load_predictions,
    load_tasks,
)


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TASKS = ROOT / "benchmarks" / "data" / "dev" / "m11a_tasks_v1.jsonl"
DEFAULT_PREDICTIONS = (
    ROOT / "benchmarks" / "data" / "dev" / "m11a_calibration_predictions_v1.jsonl"
)
DEFAULT_OUTPUT_ROOT = ROOT / "benchmarks" / "results" / "runs"
SYSTEM_ID = "fixture_calibration_v1"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(65536), b""):
            digest.update(block)
    return f"sha256:{digest.hexdigest()}"


def _percentile(values: Sequence[float], percent: float) -> float:
    if not values:
        raise ValueError("percentile requires at least one value")
    ordered = sorted(float(value) for value in values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * percent / 100.0
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = position - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction


def _git_state() -> tuple[str, bool]:
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        ).stdout.strip()
        dirty = bool(
            subprocess.run(
                ["git", "status", "--porcelain"],
                cwd=ROOT,
                check=True,
                capture_output=True,
                text=True,
                timeout=5,
            ).stdout.strip()
        )
        return commit, dirty
    except (OSError, subprocess.SubprocessError):
        return "unknown", True


def _render_markdown(report: BenchmarkRunReport) -> str:
    metadata = report.metadata
    summary = report.summary
    failures = summary.failure_category_counts
    failure_lines = (
        "\n".join(f"- `{name}`: {count}" for name, count in sorted(failures.items()))
        if failures
        else "- None"
    )
    rows = "\n".join(
        f"| `{item.task_id}` | {item.category} | {item.difficulty} | "
        f"{'PASS' if item.passed else 'FAIL'} | {item.overall_score:.4f} | "
        f"{', '.join(item.failure_categories) or '-'} |"
        for item in report.task_scores
    )
    return f"""# M11a Framework Calibration Report

> Framework calibration only. These visible dev fixtures validate contracts, scorers, and reporting; they are not dynamic-Agent, Validator, hidden-test, or production performance evidence.

## Run identity

- Run ID: `{metadata.run_id}`
- Generated: `{metadata.generated_at.isoformat()}`
- Split: `{metadata.split}`
- System: `{metadata.system_id}`
- Model: `{metadata.model_id or 'none (fixture calibration)'}`
- Prompt: `{metadata.prompt_version or 'none (fixture calibration)'}`
- Scorer: `{metadata.scorer_version}`
- Data version: `{metadata.data_version}`
- Git commit: `{metadata.git_commit}`
- Working tree dirty: `{str(metadata.working_tree_dirty).lower()}`
- Tasks hash: `{metadata.tasks_hash}`
- Predictions hash: `{metadata.predictions_hash}`
- Config hash: `{metadata.config_hash}`

## Summary

- Tasks: {summary.task_count}
- Passed: {summary.passed_count}
- Pass rate: {summary.pass_rate:.4f}
- Score mean / stdev: {summary.score_mean:.4f} / {summary.score_stdev:.4f}
- Score P50 / P95: {summary.score_p50:.4f} / {summary.score_p95:.4f}
- Latency mean / P50 / P95 ms: {summary.latency_mean_ms:.3f} / {summary.latency_p50_ms:.3f} / {summary.latency_p95_ms:.3f}
- Mean tool / LLM calls: {summary.mean_tool_calls:.3f} / {summary.mean_llm_calls:.3f}
- Total estimated cost USD: {summary.total_estimated_cost_usd:.6f}

## Failure categories

{failure_lines}

## Per-task deterministic scores

| Task | Category | Difficulty | Result | Score | Failures |
|---|---|---|---|---:|---|
{rows}
"""


def run_benchmark(
    *,
    tasks_path: Path = DEFAULT_TASKS,
    predictions_path: Path = DEFAULT_PREDICTIONS,
    output_root: Path = DEFAULT_OUTPUT_ROOT,
    run_id: str | None = None,
) -> tuple[Path, BenchmarkRunReport]:
    tasks_path = Path(tasks_path)
    predictions_path = Path(predictions_path)
    tasks = load_tasks(tasks_path)
    predictions = load_predictions(predictions_path)
    task_ids = {task.task_id for task in tasks}
    prediction_by_id = {prediction.task_id: prediction for prediction in predictions}
    if task_ids != set(prediction_by_id):
        missing = sorted(task_ids - set(prediction_by_id))
        extra = sorted(set(prediction_by_id) - task_ids)
        raise ValueError(f"Task/prediction ID mismatch; missing={missing}, extra={extra}")
    splits = {task.split for task in tasks}
    versions = {task.data_version for task in tasks}
    if len(splits) != 1 or len(versions) != 1:
        raise ValueError("A benchmark run requires exactly one split and data version")

    task_scores = [evaluate_task(task, prediction_by_id[task.task_id]) for task in tasks]
    scores = [item.overall_score for item in task_scores]
    ordered_predictions = [prediction_by_id[task.task_id] for task in tasks]
    latencies = [item.latency_ms for item in ordered_predictions]
    tasks_hash = _sha256(tasks_path)
    predictions_hash = _sha256(predictions_path)
    config = {
        "scorer_version": "m11a-v1",
        "system_id": SYSTEM_ID,
        "tasks_hash": tasks_hash,
        "predictions_hash": predictions_hash,
    }
    config_digest = hashlib.sha256(
        json.dumps(config, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    generated_at = datetime.now(timezone.utc)
    resolved_run_id = run_id or f"{generated_at.strftime('%Y%m%dT%H%M%SZ')}-{config_digest[:12]}"
    git_commit, working_tree_dirty = _git_state()
    failure_counts = Counter(
        category for score in task_scores for category in score.failure_categories
    )
    report = BenchmarkRunReport(
        metadata=RunMetadata(
            run_id=resolved_run_id,
            generated_at=generated_at,
            split=next(iter(splits)),
            framework_calibration=True,
            system_id=SYSTEM_ID,
            model_id=None,
            prompt_version=None,
            random_seeds=sorted({task.random_seed for task in tasks}),
            data_version=next(iter(versions)),
            tasks_hash=tasks_hash,
            predictions_hash=predictions_hash,
            config_hash=f"sha256:{config_digest}",
            git_commit=git_commit,
            working_tree_dirty=working_tree_dirty,
        ),
        summary=RunSummary(
            task_count=len(tasks),
            passed_count=sum(item.passed for item in task_scores),
            pass_rate=sum(item.passed for item in task_scores) / len(task_scores),
            score_mean=statistics.fmean(scores),
            score_stdev=statistics.pstdev(scores),
            score_p50=_percentile(scores, 50),
            score_p95=_percentile(scores, 95),
            latency_mean_ms=statistics.fmean(latencies),
            latency_p50_ms=_percentile(latencies, 50),
            latency_p95_ms=_percentile(latencies, 95),
            mean_tool_calls=statistics.fmean(item.tool_calls for item in ordered_predictions),
            mean_llm_calls=statistics.fmean(item.llm_calls for item in ordered_predictions),
            total_estimated_cost_usd=sum(
                item.estimated_cost_usd for item in ordered_predictions
            ),
            failure_category_counts=dict(sorted(failure_counts.items())),
        ),
        task_scores=task_scores,
    )
    run_dir = Path(output_root) / resolved_run_id
    run_dir.mkdir(parents=True, exist_ok=False)
    (run_dir / "report.json").write_text(
        report.model_dump_json(indent=2) + "\n", encoding="utf-8"
    )
    (run_dir / "report.md").write_text(_render_markdown(report), encoding="utf-8")
    return run_dir, report


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tasks", type=Path, default=DEFAULT_TASKS)
    parser.add_argument("--predictions", type=Path, default=DEFAULT_PREDICTIONS)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--run-id", default=None)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    run_dir, report = run_benchmark(
        tasks_path=args.tasks,
        predictions_path=args.predictions,
        output_root=args.output_root,
        run_id=args.run_id,
    )
    print(
        f"[PASS] M11a calibration: {report.summary.passed_count}/"
        f"{report.summary.task_count} tasks; report={run_dir}"
    )


if __name__ == "__main__":
    main()
