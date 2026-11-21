"""M11c baseline comparison CLI (offline, deterministic systems, M11b v2.0 scorer).

Subcommands:
- prefreeze: public-dev capability matrix and one minimal run per agent (evidence only).
- freeze: exclusively write the frozen dev capability matrix and protocol manifest.
- run: verify the freeze, run formal configs on dev and private test and ablations on
  dev for every seed, score with the unchanged M11b v2.0 scorer, and write an
  exclusive JSON/Markdown report. Private results are published as aggregates only.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import sys
import tempfile
from typing import Any, Optional

from benchmarks.run_m11b_scoring import DEV_TASKS, TEST_TASKS, _git_state, _hash, score_predictions
from src.agent.baselines import DynamicAgentSystem, ValidatorReplanSystem
from src.evaluation.m11b import load_m11b_tasks, score_m11b
from src.evaluation.m11c import (
    FORMAL_IDS, PRIVATE_DECLARED_CRS, PRIVATE_DIR, PROTOCOL_VERSION, SEEDS, ablation_systems,
    across_runs, capability_matrix, dev_catalogue, formal_systems, p4_frontend_code,
    private_catalogue, run_system, secondary_pass, signature, source_fingerprint, stability,
    summarize, system_input,
)

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "benchmarks/results/m11c"
PRIVATE_RECEIPT = ROOT / "benchmarks/data/test/m11b_private_receipt_v1.json"
FREEZE_DOC = ROOT / "docs/roadmap/M11C_EVALUATION_FREEZE.md"
MATRIX_NAME = "m11c_dev_capability_matrix_v1.jsonl"
MANIFEST_NAME = "m11c_freeze_manifest_v1.json"
DEFINITIONS = {
    "strict_pass": "Official M11b v2.0 TaskScore.passed (scorer unchanged); primary metric.",
    "secondary_pass": "Diagnostic, non-official: same prediction, every component passed except "
                      "required_steps is ignored. Not an M11b pass rate and not end-to-end validation.",
    "executable": "System reaches tool execution: FW-A/FW-B front-end request with an operation "
                  "that has a template; agents front-end request and M07 status plan; A1 raw M07 plan.",
    "tool_sequence": "Analysis tools of the pass that produced the returned result; retries and "
                     "validate_geometry checks count in tool_calls only.",
    "subsets": "full: every task; common_executable: executable by fixed_workflow, dynamic_agent and "
               "validator_replan; agent_executable: by both agents; decision: benchmark expects "
               "non-success; supported_unexecutable_by_formal: supported tasks no formal config executes.",
    "stdev": "Across runs: sample standard deviation; within a run: population standard deviation.",
}


def _sha_bytes(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _write_exclusive(path: Path, text: str) -> None:
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(text)


def _jsonl(rows) -> str:
    return "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows)


def _dev_tasks(task_ids: Optional[set[str]] = None):
    tasks = load_m11b_tasks(DEV_TASKS)
    return [task for task in tasks if task_ids is None or task.task_id in task_ids]


def _dev_matrix_text(tasks) -> str:
    rows = capability_matrix(tasks, dev_catalogue(), formal_systems() + ablation_systems())
    by_id = {task.task_id: task for task in tasks}
    for row in rows:
        task = by_id[row["task_id"]]
        row.update(task_type=task.task_type, benchmark_capability=task.capability)
    return _jsonl(rows)


def _private_identity() -> dict[str, Any]:
    receipt = json.loads(PRIVATE_RECEIPT.read_text("utf-8"))
    identity = {"tasks_sha256": receipt["tasks_sha256"], "task_count": receipt["task_count"],
                "private_manifest_sha256": receipt["private_manifest_sha256"],
                "declared_crs": PRIVATE_DECLARED_CRS, "package_present": PRIVATE_DIR.is_dir()}
    if PRIVATE_DIR.is_dir():
        identity["catalogue"] = private_catalogue().identity()
    return identity


def freeze(output_root: Path = RESULTS / "freeze", *, task_ids: Optional[set[str]] = None) -> Path:
    tasks = _dev_tasks(task_ids)
    matrix_text = _dev_matrix_text(tasks)
    rows = [json.loads(line) for line in matrix_text.splitlines()]
    output_root = Path(output_root)
    matrix_path, manifest_path = output_root / MATRIX_NAME, output_root / MANIFEST_NAME
    if matrix_path.exists() or manifest_path.exists():
        raise FileExistsError("M11c freeze already exists")
    systems = formal_systems() + ablation_systems()
    counts = {s.system_id: sum(row["executable"][s.system_id] for row in rows) for s in systems}
    common = [row["task_id"] for row in rows if all(row["executable"][s] for s in FORMAL_IDS)]
    manifest = {
        "schema_version": "1.0", "protocol_version": PROTOCOL_VERSION, "seeds": list(SEEDS),
        "formal_systems": [s.config() for s in formal_systems()],
        "ablation_systems": [s.config() for s in ablation_systems()],
        "dev": {"tasks_sha256": _hash(DEV_TASKS), "task_count": len(tasks),
                "task_ids_sha256": _sha_bytes("\n".join(sorted(t.task_id for t in tasks)).encode()),
                "catalogue": dev_catalogue().identity()},
        "test": _private_identity(), "source_fingerprint": source_fingerprint(),
        "matrix_file": MATRIX_NAME, "matrix_sha256": _sha_bytes(matrix_text.encode("utf-8")),
        "dev_executable_counts": counts, "dev_common_executable_task_ids": common,
        "definitions": DEFINITIONS,
        "private_policy": "Private test runs only after this freeze; aggregates only; no tuning afterwards.",
    }
    output_root.mkdir(parents=True, exist_ok=True)
    _write_exclusive(matrix_path, matrix_text)
    _write_exclusive(manifest_path, json.dumps(manifest, sort_keys=True, indent=2) + "\n")
    return manifest_path


def _verify(manifest_path: Path, task_ids, seeds, *, check_fingerprint: bool = True) -> tuple[dict[str, Any], str]:
    raw = Path(manifest_path).read_bytes()
    manifest = json.loads(raw)
    if manifest.get("protocol_version") != PROTOCOL_VERSION:
        raise ValueError("protocol version differs from freeze")
    if check_fingerprint and manifest["source_fingerprint"] != source_fingerprint():
        raise ValueError("source code changed after freeze; publish a new protocol version")
    if [s.config() for s in formal_systems()] != manifest["formal_systems"] or \
            [s.config() for s in ablation_systems()] != manifest["ablation_systems"]:
        raise ValueError("system configuration differs from freeze")
    if not set(seeds) <= set(manifest["seeds"]):
        raise ValueError("seeds are not part of the frozen protocol")
    if _sha_bytes(_dev_matrix_text(_dev_tasks(task_ids)).encode("utf-8")) != manifest["matrix_sha256"]:
        raise ValueError("capability matrix differs from freeze")
    return manifest, _sha_bytes(raw)


def _execute_all(task_ids, seeds, include_private) -> dict[str, Any]:
    splits = {"dev": (_dev_tasks(task_ids), dev_catalogue(), formal_systems() + ablation_systems())}
    if include_private:
        receipt = json.loads(PRIVATE_RECEIPT.read_text("utf-8"))
        manifest_bytes = (PRIVATE_DIR / "m11b_private_manifest_v1.json").read_bytes()
        if hashlib.sha256(manifest_bytes).hexdigest() != receipt["private_manifest_sha256"] or \
                _hash(TEST_TASKS) != "sha256:" + receipt["tasks_sha256"]:
            raise ValueError("private package does not match the public receipt")
        splits["test"] = (load_m11b_tasks(TEST_TASKS), private_catalogue(), formal_systems())
    results = {}
    for split, (tasks, catalogue, systems) in splits.items():
        rows = capability_matrix(tasks, catalogue, systems)
        matrix = {row["task_id"]: row["executable"] for row in rows}
        declined = {row["task_id"]: row["declined"] for row in rows}
        runs: dict[str, list] = defaultdict(list)
        for seed in seeds:
            for system in systems:
                runs[system.system_id].append(run_system(
                    system, tasks, catalogue, seed=seed,
                    executable={tid: flags[system.system_id] for tid, flags in matrix.items()}))
        results[split] = {"tasks": tasks, "matrix": matrix, "declined": declined, "runs": dict(runs),
                          "configs": {s.system_id: s.config() for s in systems},
                          "catalogue": catalogue.identity()}
    return results


def split_report(tasks, runs, matrix, *, publish_task_rows: bool) -> dict[str, Any]:
    by_id = {task.task_id: task for task in tasks}
    ids = sorted(by_id)
    subsets = {
        "full": ids,
        "common_executable": [i for i in ids if all(matrix[i].get(s, False) for s in FORMAL_IDS)],
        "agent_executable": [i for i in ids if matrix[i].get("dynamic_agent") and matrix[i].get("validator_replan")],
        "decision": [i for i in ids if by_id[i].capability != "supported"],
        "supported_unexecutable_by_formal": [i for i in ids if by_id[i].capability == "supported"
                                             and not any(matrix[i].get(s, False) for s in FORMAL_IDS)],
    }
    types = sorted({task.task_type + "/" + task.capability for task in tasks})
    configs = {}
    for config, run_list in runs.items():
        per_subset = {}
        for name, members in subsets.items():
            keep = set(members)
            summaries = [summarize([r for r in run if r.task.task_id in keep]) for run in run_list]
            per_subset[name] = {"per_run": summaries, "across_runs": across_runs(summaries)}
        by_type = {}
        for group in types:
            summaries = [summarize([r for r in run if r.task.task_type + "/" + r.task.capability == group])
                         for run in run_list]
            by_type[group] = across_runs(summaries)
        first = run_list[0]
        errors = Counter((r.task.task_type, name) for r in first for name in r.score.failure_categories)
        analysis = {
            "failure_by_task_type": {f"{t}|{n}": c for (t, n), c in sorted(errors.items())},
            "declined_reasons": dict(sorted(Counter(r.prediction.reason_code for r in first
                                                    if not r.executable).items())),
            "execution_errors": dict(sorted(Counter(
                r.trace.get("execution", {}).get("error_code") for r in first
                if r.executable and r.trace.get("execution", {}).get("error_code")).items())),
            "replans": sum(r.trace.get("execution", {}).get("replan_count", 0) for r in first),
            "first_pass_failures": sum(1 for r in first if r.trace.get("execution", {}).get(
                "first_pass_success") is False),
        }
        if publish_task_rows:
            examples = defaultdict(list)
            for r in first:
                for name in r.score.failure_categories:
                    if len(examples[name]) < 3:
                        examples[name].append(r.task.task_id)
            analysis["examples"] = dict(sorted(examples.items()))
        configs[config] = {"subsets": per_subset, "by_task_type": by_type,
                           "stability": stability(run_list), "error_analysis": analysis}
    out = {"task_count": len(ids), "subsets": {name: len(members) for name, members in subsets.items()},
           "configs": configs}
    if publish_task_rows:
        out["subset_task_ids"] = subsets
        out["task_rows"] = [
            {"config": config, "run": index + 1, "task_id": r.task.task_id, "task_type": r.task.task_type,
             "executable": r.executable, "status": r.prediction.status, "reason_code": r.prediction.reason_code,
             "strict_pass": r.score.passed, "secondary_pass": secondary_pass(r.score),
             "failure_categories": r.score.failure_categories, "tool_sequence": r.prediction.tool_sequence,
             "tool_calls": r.prediction.tool_calls, "latency_ms": r.prediction.latency_ms}
            for config, run_list in runs.items() for index, run in enumerate(run_list) for r in run]
    return out


def _receipt(manifest_sha: str, seeds, results) -> dict[str, Any]:
    splits = {}
    for split, data in results.items():
        splits[split] = {}
        for config, run_list in data["runs"].items():
            entries = []
            for index, run in enumerate(run_list):
                summary = summarize(run)
                signatures = "\n".join(f"{r.task.task_id}:{signature(r)}" for r in run)
                entries.append({
                    "run": index + 1, "seed": seeds[index], "tasks": summary["tasks"],
                    "strict_pass": summary["strict_pass"], "secondary_pass": summary["secondary_pass"],
                    "executable": summary["executable"], "components": summary["components"],
                    "status_counts": summary["status_counts"], "reason_counts": summary["reason_counts"],
                    "failure_category_counts": summary["failure_category_counts"],
                    "tool_calls_total": summary["tool_calls_total"],
                    "signatures_sha256": _sha_bytes(signatures.encode("utf-8"))})
            splits[split][config] = entries
    return {"protocol_version": PROTOCOL_VERSION, "freeze_manifest_sha256": manifest_sha,
            "seeds": list(seeds), "splits": splits}


def deterministic_receipt(manifest_path: Path, *, task_ids: Optional[set[str]] = None,
                          seeds=SEEDS, include_private: bool = True,
                          check_fingerprint: bool = True) -> dict[str, Any]:
    """Re-run the frozen protocol; the audit may skip the whole-tree fingerprint but
    still requires identical system configurations and dev capability matrix."""
    _, manifest_sha = _verify(manifest_path, task_ids, seeds, check_fingerprint=check_fingerprint)
    return json.loads(json.dumps(_receipt(manifest_sha, list(seeds),
                                          _execute_all(task_ids, seeds, include_private))))


def _official_reports(run_dir: Path, split: str, tasks_path: Path, data) -> dict[str, Any]:
    checks = {}
    for config, run_list in data["runs"].items():
        for index, run in enumerate(run_list):
            name = f"{config}-run{index + 1}"
            text = _jsonl(r.prediction.model_dump(mode="json") for r in run)
            if split == "dev":
                folder = run_dir / "dev_predictions"
                folder.mkdir(exist_ok=True)
                path = folder / f"{name}.jsonl"
                _write_exclusive(path, text)
                _, report = score_predictions(tasks_path, path, run_dir / "m11b_reports" / split,
                                              run_id=name, system_id=config)
            else:
                with tempfile.TemporaryDirectory(prefix="m11c_private_") as temp:
                    path = Path(temp) / "predictions.jsonl"
                    path.write_text(text, encoding="utf-8")
                    _, report = score_predictions(tasks_path, path, run_dir / "m11b_reports" / split,
                                                  run_id=name, system_id=config)
            if report["summary"]["passed_count"] != sum(r.score.passed for r in run):
                raise AssertionError("official M11b report disagrees with in-memory scoring")
            checks[name] = {"passed_count": report["summary"]["passed_count"],
                            "official_release_verified": report["metadata"]["official_release_verified"]}
    return checks


def _environment() -> dict[str, Any]:
    import numpy, pydantic, pyproj, shapely
    return {"python": sys.version.split()[0], "platform": platform.platform(),
            "packages": {"numpy": numpy.__version__, "pydantic": pydantic.__version__,
                         "pyproj": pyproj.__version__, "shapely": shapely.__version__},
            "threads": {key: os.environ.get(key) for key in
                        ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS")}}


def _fmt(item: dict[str, Any], key: str, digits: int = 4) -> str:
    value = item.get(key)
    if not isinstance(value, dict):
        return "—"
    return f"{value['mean']:.{digits}f} ± {value['stdev']:.{digits}f}"


def _markdown(report: dict[str, Any]) -> str:
    meta = report["metadata"]
    lines = ["# M11c baseline comparison (offline deterministic systems)", "",
             f"- Protocol {meta['protocol_version']}; freeze manifest {meta['freeze_manifest_sha256']}; "
             f"freeze document {meta['freeze_document_sha256']}.",
             f"- Code: git {meta['git_commit']} (working tree dirty: {meta['working_tree_dirty']}); "
             f"source fingerprint {meta['source_fingerprint']}.",
             f"- Seeds {meta['seeds']} (task order only); runs per configuration: {len(meta['seeds'])}. "
             "No language model: model/prompt null, 0 LLM calls, cost 0 USD.",
             "- Primary metric: official M11b v2.0 strict pass. Secondary metric "
             "\"除 required_steps 外的严格通过率\" is diagnostic and non-official; it does not mean a "
             "non-validating system performed end-to-end validation.", ""]
    for split, data in report["splits"].items():
        scope = "public dev" if split == "dev" else "private test (aggregate only)"
        lines += [f"## {split}: {scope}", "",
                  "Subset sizes: " + ", ".join(f"{k} {v}" for k, v in data["subsets"].items()), "",
                  "| Config | Subset | Tasks | Strict pass rate | Secondary (diagnostic) | Coverage | "
                  "Latency mean ms | Tool calls mean | Stability |",
                  "|---|---|---|---|---|---|---|---|---|"]
        for config, item in data["configs"].items():
            for subset in ("full", "common_executable", "agent_executable", "decision"):
                stats = item["subsets"][subset]["across_runs"]
                if not stats.get("tasks"):
                    lines.append(f"| {config} | {subset} | 0 | — | — | — | — | — | — |")
                    continue
                lines.append(
                    f"| {config} | {subset} | {stats['tasks']} | {_fmt(stats, 'strict_pass_rate')} | "
                    f"{_fmt(stats, 'secondary_pass_rate')} | {_fmt(stats, 'coverage')} | "
                    f"{_fmt(stats, 'latency_mean_ms', 2)} | {_fmt(stats, 'tool_calls_mean', 2)} | "
                    f"{item['stability']['identical']}/{item['stability']['tasks']} |")
        lines += ["", "Strict pass per run (full subset): " + "; ".join(
            f"{config} {item['subsets']['full']['across_runs'].get('strict_pass_per_run')}"
            for config, item in data["configs"].items()), "",
            "Error analysis (run 1): " + "; ".join(
                f"{config} declined {json.dumps(item['error_analysis']['declined_reasons'], sort_keys=True)}, "
                f"failures {json.dumps(item['error_analysis']['failure_by_task_type'], sort_keys=True)}"
                for config, item in data["configs"].items()), ""]
    if "combined" in report:
        lines += ["## All 200 tasks (dev 160 + private 40), formal configurations", "",
                  "| Config | Strict pass per run | Mean rate | Secondary per run |", "|---|---|---|---|"]
        for config, item in report["combined"].items():
            lines.append(f"| {config} | {item['strict_pass_per_run']} | {item['strict_pass_rate_mean']:.4f} | "
                         f"{item['secondary_pass_per_run']} |")
        lines.append("")
    lines += ["## Limitations", "",
              "- Clean benchmark data cannot demonstrate retry/replan recovery benefit; if "
              "validator_no_replan equals validator_replan, no replan was triggered on this split. "
              "Fault recovery is M13 scope (M08 frozen evaluation has separate 4/4 recoverable-fault evidence).",
              "- Single-dataset buffer (24 dev, 8 private) is unsupported by every configuration and counted "
              "as failure. rank is not supported; no ranking capability is claimed.",
              "- Synthetic project-authored data; private split is a structural variant of the M04b fixture "
              "contract, not independent expert annotation. The front-end recognisers were written from public "
              "dev phrasing, so private results measure template-level generalisation only.",
              "- M03 Phase 2 public queries remain a catalog-informed internal evaluation; GTFS fixed stop "
              "buffers are not transit isochrones.", ""]
    return "\n".join(lines)


def run_comparison(output_root: Path, run_id: str, manifest_path: Path, *,
                   task_ids: Optional[set[str]] = None, seeds=SEEDS, include_private: bool = True) -> Path:
    manifest, manifest_sha = _verify(manifest_path, task_ids, seeds)
    output_root = Path(output_root)
    output_root.mkdir(parents=True, exist_ok=True)
    run_dir = output_root / run_id
    run_dir.mkdir(exist_ok=False)
    seeds = list(seeds)
    results = _execute_all(task_ids, seeds, include_private)
    commit, dirty = _git_state()
    report: dict[str, Any] = {
        "metadata": {
            "protocol_version": PROTOCOL_VERSION, "generated_at": datetime.now(timezone.utc).isoformat(),
            "freeze_manifest_sha256": manifest_sha,
            "freeze_document_sha256": _sha_bytes(FREEZE_DOC.read_bytes()) if FREEZE_DOC.is_file() else None,
            "git_commit": commit, "working_tree_dirty": dirty, "source_fingerprint": source_fingerprint(),
            "seeds": seeds, "environment": _environment(), "definitions": DEFINITIONS,
            "model_id": None, "prompt_version": None, "llm_calls": 0, "estimated_cost_usd": 0.0,
            "data": {"dev_tasks_sha256": _hash(DEV_TASKS), "test": manifest["test"] if include_private else None},
            "configs": {split: data["configs"] for split, data in results.items()},
            "catalogues": {split: data["catalogue"] for split, data in results.items()},
        },
        "splits": {split: split_report(data["tasks"], data["runs"], data["matrix"],
                                       publish_task_rows=(split == "dev"))
                   for split, data in results.items()},
    }
    with tempfile.TemporaryDirectory(prefix="m11c_tasks_") as temp:
        dev_tasks_path = DEV_TASKS
        if task_ids is not None:  # subset runs (tests) cannot claim the official release
            dev_tasks_path = Path(temp) / "dev_subset.jsonl"
            dev_tasks_path.write_text("".join(
                line + "\n" for line in DEV_TASKS.read_text("utf-8").splitlines()
                if line.strip() and json.loads(line)["task_id"] in task_ids), encoding="utf-8")
        report["official_m11b_reports"] = {split: _official_reports(
            run_dir, split, dev_tasks_path if split == "dev" else TEST_TASKS, data)
            for split, data in results.items()}
    if "test" in results:
        report["combined"] = {}
        for config in FORMAL_IDS:
            dev_runs, test_runs = results["dev"]["runs"][config], results["test"]["runs"][config]
            strict = [sum(r.score.passed for r in d) + sum(r.score.passed for r in t)
                      for d, t in zip(dev_runs, test_runs)]
            second = [sum(secondary_pass(r.score) for r in d) + sum(secondary_pass(r.score) for r in t)
                      for d, t in zip(dev_runs, test_runs)]
            total = len(results["dev"]["tasks"]) + len(results["test"]["tasks"])
            report["combined"][config] = {"tasks": total, "strict_pass_per_run": strict,
                                          "strict_pass_rate_mean": sum(strict) / len(strict) / total,
                                          "secondary_pass_per_run": second}
    dev_rows = report["splits"]["dev"].pop("task_rows")
    _write_exclusive(run_dir / "dev_task_results.jsonl", _jsonl(dev_rows))
    _write_exclusive(run_dir / "report.json", json.dumps(report, sort_keys=True, indent=2) + "\n")
    _write_exclusive(run_dir / "report.md", _markdown(report))
    receipt = _receipt(manifest_sha, seeds, results)
    _write_exclusive(run_dir / "deterministic_receipt.json", json.dumps(receipt, sort_keys=True, indent=2) + "\n")
    return run_dir


def prefreeze(output_root: Path = RESULTS / "prefreeze", run_id: str | None = None) -> Path:
    tasks = load_m11b_tasks(DEV_TASKS)
    catalogue = dev_catalogue()
    by_id = {task.task_id: task for task in tasks}
    matrix = capability_matrix(tasks, catalogue)
    for row in matrix:
        task = by_id[row["task_id"]]
        row.update(task_type=task.task_type, benchmark_capability=task.capability,
                   plan_matches_reference_tools=(row["planned_tools"] == task.expectation.tool_sequence
                                                 if task.capability == "supported" else None),
                   p4_frontend_code=p4_frontend_code(task.query), p4_applicable=False)
    systems = [DynamicAgentSystem(), ValidatorReplanSystem()]
    results, summary = {}, {}
    for system in systems:
        rows, strict, diagnostic = [], Counter(), Counter()
        for task in tasks:
            run = system.run(system_input(task), catalogue)
            score = score_m11b(task, run.prediction)
            other = [name for name in score.failure_categories if name != "required_step_mismatch"]
            group = f"{task.task_type}/{task.capability}"
            strict[group] += int(score.passed)
            diagnostic[group] += int(not other)
            rows.append({"task_id": task.task_id, "status": run.prediction.status,
                         "reason_code": run.prediction.reason_code, "passed": score.passed,
                         "failure_categories": score.failure_categories,
                         "tool_sequence": run.prediction.tool_sequence,
                         "tool_calls": run.prediction.tool_calls,
                         "latency_ms": run.prediction.latency_ms, "trace": run.trace})
        results[system.system_id] = rows
        summary[system.system_id] = {
            "config": system.config(), "strict_pass": sum(strict.values()),
            "result_correct_excluding_required_steps": sum(diagnostic.values()),
            "by_group_strict": dict(sorted(strict.items())),
            "by_group_excluding_required_steps": dict(sorted(diagnostic.items())),
        }
    groups = Counter(f"{task.task_type}/{task.capability}" for task in tasks)
    commit, dirty = _git_state()
    now = datetime.now(timezone.utc)
    resolved = run_id or now.strftime("%Y%m%dT%H%M%SZ") + "-dev-prefreeze"
    run_dir = Path(output_root) / resolved
    run_dir.mkdir(parents=True, exist_ok=False)
    metadata = {
        "label": "M11c pre-freeze public-dev verification; not the formal comparison",
        "generated_at": now.isoformat(), "git_commit": commit, "working_tree_dirty": dirty,
        "tasks_hash": _hash(DEV_TASKS), "split": "dev", "catalogue": catalogue.identity(),
        "private_data_read": False, "model_calls": 0,
    }
    matrix_counts = Counter(
        (row["task_type"], row["benchmark_capability"], row["frontend_status"],
         row["frontend_reason"], row["planner_status"], tuple(row["planned_tools"]))
        for row in matrix)
    report = {"metadata": metadata, "group_sizes": dict(sorted(groups.items())),
              "matrix_summary": [{"task_type": k[0], "benchmark_capability": k[1],
                                  "frontend_status": k[2], "frontend_reason": k[3],
                                  "planner_status": k[4], "planned_tools": list(k[5]),
                                  "count": v} for k, v in sorted(matrix_counts.items(), key=str)],
              "p4_frontend_codes": dict(sorted(Counter(r["p4_frontend_code"] for r in matrix).items())),
              "systems": summary, "task_results": results}
    _write_exclusive(run_dir / "capability_matrix_dev.jsonl", _jsonl(matrix))
    _write_exclusive(run_dir / "verification_dev.json", json.dumps(report, sort_keys=True, indent=2) + "\n")
    lines = ["# M11c pre-freeze public-dev verification", "",
             "Not the formal M11c comparison: one run per agent system on public dev only; "
             "no private data read; offline deterministic systems with 0 model calls.", "",
             "| Group (task_type/benchmark capability) | Tasks | " +
             " | ".join(f"{s} strict | {s} excl. required_steps" for s in summary) + " |",
             "|---|---|" + "---|---|" * len(summary)]
    for group, size in sorted(groups.items()):
        cells = []
        for system_id, item in summary.items():
            cells += [str(item["by_group_strict"].get(group, 0)),
                      str(item["by_group_excluding_required_steps"].get(group, 0))]
        lines.append(f"| {group} | {size} | " + " | ".join(cells) + " |")
    lines += ["", f"P4 fixed workflow front-end codes (not applicable to M04 GeoJSON tasks): "
              f"{json.dumps(report['p4_frontend_codes'], sort_keys=True)}", ""]
    _write_exclusive(run_dir / "verification_dev.md", "\n".join(lines))
    return run_dir


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    pre = sub.add_parser("prefreeze", help="public-dev capability matrix and minimal verification")
    pre.add_argument("--output-root", type=Path, default=RESULTS / "prefreeze")
    pre.add_argument("--run-id")
    frz = sub.add_parser("freeze", help="exclusively write frozen dev matrix and protocol manifest")
    frz.add_argument("--output-root", type=Path, default=RESULTS / "freeze")
    run = sub.add_parser("run", help="formal three-configuration comparison plus dev ablations")
    run.add_argument("--manifest", type=Path, default=RESULTS / "freeze" / MANIFEST_NAME)
    run.add_argument("--output-root", type=Path, default=RESULTS / "formal")
    run.add_argument("--run-id", default="m11c_comparison_v1")
    run.add_argument("--dev-only", action="store_true", help="skip the private test split")
    args = parser.parse_args()
    if args.command == "prefreeze":
        print(f"M11c pre-freeze dev verification written to {prefreeze(args.output_root, args.run_id)}")
    elif args.command == "freeze":
        print(f"M11c freeze manifest written to {freeze(args.output_root)}")
    else:
        path = run_comparison(args.output_root, args.run_id, args.manifest,
                              include_private=not args.dev_only)
        print(f"M11c comparison written to {path}")


if __name__ == "__main__":
    main()
