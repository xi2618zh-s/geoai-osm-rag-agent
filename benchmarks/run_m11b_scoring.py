"""Score M11b v2.0 prediction JSONL without executing systems or exposing holdout tasks."""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import statistics
import subprocess

from src.evaluation.m11b import M11bPrediction, load_m11b_tasks, score_m11b

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT_ROOT = ROOT / "benchmarks/results/runs"
DEV_TASKS = ROOT / "benchmarks/data/dev/m11b_tasks_v1.jsonl"
TEST_TASKS = ROOT / "benchmarks/data/test/private/m11b_test_tasks_v1.jsonl"


def _hash(path: Path) -> str:
    return "sha256:" + hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _percentile(values: list[float], percent: float) -> float:
    ordered = sorted(values)
    position = (len(ordered) - 1) * percent / 100
    low = int(position)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


def _git_state():
    try:
        commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT,
                                capture_output=True, check=True, text=True,
                                timeout=5).stdout.strip()
        dirty = bool(subprocess.run(["git", "status", "--porcelain"], cwd=ROOT,
                                    capture_output=True, check=True, text=True,
                                    timeout=5).stdout.strip())
        return commit, dirty
    except (OSError, subprocess.SubprocessError):
        return "unknown", True


def _load_predictions(path: Path):
    rows = {}
    for line_number, line in enumerate(Path(path).read_text("utf-8").splitlines(), 1):
        if not line.strip():
            continue
        row = M11bPrediction.model_validate_json(line)
        if row.task_id in rows:
            raise ValueError(f"duplicate prediction ID at line {line_number}")
        rows[row.task_id] = row
    if not rows:
        raise ValueError("empty M11b prediction file")
    return rows


def _official_release(tasks_path: Path, split: str, tasks_hash: str) -> bool:
    expected_path = DEV_TASKS if split == "dev" else TEST_TASKS
    if tasks_path.resolve() != expected_path.resolve():
        return False
    manifest_path = (ROOT / "benchmarks/data/dev/m11b_manifest_v1.json"
                     if split == "dev" else
                     ROOT / "benchmarks/data/test/m11b_private_receipt_v1.json")
    manifest = json.loads(manifest_path.read_text("utf-8"))
    return tasks_hash == "sha256:" + manifest["tasks_sha256"]


def score_predictions(tasks_path: Path, predictions_path: Path,
                      output_root: Path = DEFAULT_OUTPUT_ROOT, *,
                      run_id: str | None = None, system_id: str,
                      model_id: str | None = None,
                      prompt_version: str | None = None):
    tasks_path, predictions_path = Path(tasks_path), Path(predictions_path)
    tasks = load_m11b_tasks(tasks_path)
    predictions = _load_predictions(predictions_path)
    task_ids = {task.task_id for task in tasks}
    if task_ids != set(predictions):
        raise ValueError("M11b task/prediction ID mismatch")
    splits = {task.split for task in tasks}
    versions = {task.data_version for task in tasks}
    if len(splits) != 1 or len(versions) != 1:
        raise ValueError("mixed splits or data versions")
    split = next(iter(splits))
    scores = [score_m11b(task, predictions[task.task_id]) for task in tasks]
    values = [item.overall_score for item in scores]
    passed = sum(item.passed for item in scores)
    ordered = [predictions[task.task_id] for task in tasks]
    latencies = [item.latency_ms for item in ordered]
    task_hash, prediction_hash = _hash(tasks_path), _hash(predictions_path)
    config = {"scorer_version": "m11b-v2.0", "system_id": system_id,
              "model_id": model_id, "prompt_version": prompt_version,
              "tasks_hash": task_hash, "predictions_hash": prediction_hash}
    config_hash = "sha256:" + hashlib.sha256(json.dumps(
        config, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
    now = datetime.now(timezone.utc)
    resolved_id = run_id or now.strftime("%Y%m%dT%H%M%SZ") + "-" + config_hash[7:19]
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{2,127}", resolved_id):
        raise ValueError("invalid run ID")
    git_commit, dirty = _git_state()
    official = _official_release(tasks_path, split, task_hash)
    failures = Counter(category for item in scores for category in item.failure_categories)
    report = {
        "metadata": {
            "schema_version": "2.0", "scorer_version": "m11b-v2.0",
            "run_id": resolved_id, "generated_at": now.isoformat(),
            "system_id": system_id, "model_id": model_id,
            "prompt_version": prompt_version, "split": split,
            "data_version": next(iter(versions)), "official_release_verified": official,
            "reference_calibration": False,
            "random_seeds": sorted({task.random_seed for task in tasks}) if split == "dev" else None,
            "private_seed_recorded_in_ignored_manifest": split == "test",
            "tasks_hash": task_hash, "predictions_hash": prediction_hash,
            "config_hash": config_hash, "git_commit": git_commit,
            "working_tree_dirty": dirty,
        },
        "summary": {
            "task_count": len(tasks), "passed_count": passed,
            "pass_rate": passed / len(tasks),
            "score_mean": statistics.fmean(values),
            "score_stdev": statistics.pstdev(values),
            "score_p50": _percentile(values, 50),
            "score_p95": _percentile(values, 95),
            "latency_mean_ms": statistics.fmean(latencies),
            "latency_p50_ms": _percentile(latencies, 50),
            "latency_p95_ms": _percentile(latencies, 95),
            "mean_tool_calls": statistics.fmean(p.tool_calls for p in ordered),
            "mean_llm_calls": statistics.fmean(p.llm_calls for p in ordered),
            "total_estimated_cost_usd": sum(p.estimated_cost_usd for p in ordered),
            "failure_category_counts": dict(sorted(failures.items())),
        },
    }
    if split == "dev":
        report["task_scores"] = [score.model_dump(mode="json") for score in scores]
    output_root = Path(output_root)
    output_root.mkdir(parents=True, exist_ok=True)
    run_dir = output_root / resolved_id
    run_dir.mkdir(exist_ok=False)
    (run_dir / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8")
    summary = report["summary"]
    scope = "private test aggregate only" if split == "test" else "public development"
    markdown = (
        f"# M11b deterministic scoring ({scope})\n\n"
        f"- Run: {resolved_id}\n"
        f"- Data: {report['metadata']['data_version']}\n"
        f"- Official full release: {official}\n"
        f"- Tasks: {len(tasks)}; passed: {passed}; pass rate: {summary['pass_rate']:.4f}\n"
        f"- Mean/stdev/P50/P95 score: {summary['score_mean']:.4f} / "
        f"{summary['score_stdev']:.4f} / {summary['score_p50']:.4f} / "
        f"{summary['score_p95']:.4f}\n"
        f"- Failure classifications: {json.dumps(summary['failure_category_counts'], sort_keys=True)}\n\n"
        "Scores describe the supplied predictions only. No M11c comparison was run.\n"
    )
    (run_dir / "report.md").write_text(markdown, encoding="utf-8")
    return run_dir, report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tasks", type=Path, default=DEV_TASKS)
    parser.add_argument("--predictions", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--run-id")
    parser.add_argument("--system-id", required=True)
    parser.add_argument("--model-id")
    parser.add_argument("--prompt-version")
    args = parser.parse_args()
    run_dir, report = score_predictions(args.tasks, args.predictions,
        args.output_root, run_id=args.run_id, system_id=args.system_id,
        model_id=args.model_id, prompt_version=args.prompt_version)
    print(f"M11b {report['metadata']['split']} scoring: "
          f"{report['summary']['passed_count']}/{report['summary']['task_count']} "
          f"written to {run_dir}")


if __name__ == "__main__":
    main()
