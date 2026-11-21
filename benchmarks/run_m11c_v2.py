"""M11c v2 CLI (m11c-protocol-v2): local qwen2.5:3b front-end over the M11c systems.

Subcommands:
- dev-iterate: prompt development on public dev only (one front-end round, formal configs).
- freeze-stage2: exclusively write the stage-2 manifest (model digest and decoding, prompt
  SHA-256, frame schema, system configurations, source fingerprint, stage-1 document and
  private v2 receipt SHA-256).
- run-dev: verify stage 2, run all seven configurations x three seeds on public dev, then
  exclusively write the report, deterministic receipt, raw model responses and the stage-3
  gate verdict.
- run-private: only after a passed stage-3 gate, run the three formal configurations x three
  seeds once on the sealed private v2 package. Public output is aggregate-only; per-task
  records and raw model responses go to the custodian workspace.
- scan-leaks: count private strings found in public outputs (prints counts only).
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
from typing import Any, Optional

from benchmarks.run_m11b_scoring import DEV_TASKS, _git_state, _hash
from benchmarks.run_m11c_baselines import _environment, _fmt, _jsonl, _official_reports, split_report
from scripts.build_m11c_private_v2 import RECEIPT as PRIVATE_V2_RECEIPT, verify_sealed, workspace_path
from src.agent.model_frontend import (
    ATTEMPT_TIMEOUT_S, DECODING_OPTIONS, ENDPOINT, KEEP_ALIVE, MAX_ATTEMPTS, MODEL_DIGEST, MODEL_ID,
    OLLAMA_VERSION, PROMPT_PATH, ModelFrontend, frame_schema, load_prompt, profile_catalogue,
)
from src.evaluation.m11b import load_m11b_tasks
from src.evaluation.m11c import dev_catalogue, secondary_pass
from src.evaluation.m11c_v2 import (
    FORMAL_IDS, GATE, PROTOCOL_VERSION, SEEDS, IdentitySource, combined_matrix, evaluate_split,
    frontend_receipt, frontend_round, gate_check, model_systems, receipt_entries, replay_round,
    round_records, round_stats,
)

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "benchmarks/results/m11c_v2"
STAGE1_DOC = ROOT / "docs/roadmap/M11C_V2_EVALUATION_FREEZE.md"
STAGE2_NAME = "m11c_v2_stage2_manifest.json"
STAGE3_NAME = "stage3_dev_gate.json"
V1_REPORT = ROOT / "benchmarks/results/m11c/formal/m11c_comparison_v1/report.json"
PRIVATE_RUN_DIR = "m11c_v2_private_runs"
DEFINITIONS = {
    "strict_pass": "Official M11b v2.0 TaskScore.passed (scorer unchanged); primary metric.",
    "secondary_pass": "Diagnostic, non-official: every component passed except required_steps.",
    "frontend_round": "Per run, one logical model call per task in seeded order; decisions shared by "
                      "every model-using configuration; validator_no_frontend makes no model call.",
    "llm_calls": "HTTP attempts of the task's logical call (at most 2); identical across model configs.",
    "latency_ms": "System time plus the task's front-end wall time.",
    "model_failure": "Logical call without a schema-valid frame after 2 attempts; denominator = logical calls.",
    "executable": "Reached tool execution in every run of the configuration.",
    "aggregate_values": "M11b v2.0 references and scorer do not record or check per-region aggregate values.",
}


def _sha_bytes(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _write_exclusive(path: Path, text: str) -> None:
    with Path(path).open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(text)


def _dev_tasks(task_ids=None):
    return [t for t in load_m11b_tasks(DEV_TASKS) if task_ids is None or t.task_id in task_ids]


def source_fingerprint_v2() -> str:
    files = sorted(path for path in (ROOT / "src").rglob("*.py") if "__pycache__" not in path.parts)
    files += [ROOT / name for name in ("benchmarks/run_m11c_v2.py", "benchmarks/run_m11c_baselines.py",
                                       "benchmarks/run_m11b_scoring.py", "scripts/build_m11c_private_v2.py")]
    files.append(PROMPT_PATH)
    lines = [f"{path.relative_to(ROOT).as_posix()}:{hashlib.sha256(path.read_bytes()).hexdigest()}"
             for path in files]
    return _sha_bytes("\n".join(lines).encode("utf-8"))


def _identity_from_manifest(manifest: dict[str, Any]) -> dict[str, Any]:
    return dict(manifest["frontend_identity"])


def _model_block(service: Optional[dict[str, Any]]) -> dict[str, Any]:
    return {"model_id": MODEL_ID, "digest": MODEL_DIGEST, "ollama_version": OLLAMA_VERSION,
            "verified_service": service, "endpoint": ENDPOINT, "decoding_options": dict(DECODING_OPTIONS),
            "keep_alive": KEEP_ALIVE, "attempt_timeout_s": ATTEMPT_TIMEOUT_S, "max_attempts": MAX_ATTEMPTS,
            "cpu_only": True}


# ---------------------------------------------------------------- prompt development

def probe_tasks(probe_path: Path):
    """Dev-derived paraphrase probe: the original dev task's expectation with a reworded query."""
    by_id = {task.task_id: task for task in _dev_tasks()}
    rows = [json.loads(line) for line in Path(probe_path).read_text("utf-8").splitlines() if line.strip()]
    return [by_id[row["task_id"]].model_copy(update={"query": row["query"]}) for row in rows]


def dev_iterate(frontend: ModelFrontend, output_root: Path, run_id: str, *, task_ids=None,
                seed: int = SEEDS[0], probe_path: Optional[Path] = None) -> Path:
    """Prompt-development run on public dev (or its paraphrase probe); evidence, not a formal result."""
    service = frontend.verify_model()
    run_dir = Path(output_root) / run_id
    run_dir.mkdir(parents=True, exist_ok=False)
    tasks = probe_tasks(probe_path) if probe_path else _dev_tasks(task_ids)
    catalogue = dev_catalogue()
    fe_round = frontend_round(frontend, tasks, catalogue, seed=seed)
    result = evaluate_split(tasks, catalogue, fe_round, frontend.identity(), seed=seed, include_ablations=False)
    gate = gate_check(result["validator_replan"], fe_round)
    by_type: dict[str, dict[str, Any]] = defaultdict(lambda: defaultdict(int))
    rows = []
    records = {r["task_id"]: r for r in round_records(fe_round)}
    for config, run in result.items():
        for r in run:
            group = f"{r.task.task_type}/{r.task.capability}"
            by_type[group]["tasks"] += config == "validator_replan"
            by_type[group][f"{config}_strict"] += int(r.score.passed)
            by_type[group][f"{config}_secondary"] += int(secondary_pass(r.score))
    for r in result["validator_replan"]:
        rows.append({"task_id": r.task.task_id, "task_type": r.task.task_type, "capability": r.task.capability,
                     "query": r.task.query, "frame": records[r.task.task_id]["call"]["frame"],
                     "attempts": [a["outcome"] for a in records[r.task.task_id]["call"]["attempts"]],
                     "decision": records[r.task.task_id]["decision"], "strict_pass": r.score.passed,
                     "failure_categories": r.score.failure_categories,
                     "status": r.prediction.status, "reason_code": r.prediction.reason_code})
    summary = {"label": "M11c v2 prompt-development iteration on public dev; not a formal result",
               "input": (f"dev-derived paraphrase probe {Path(probe_path).name} (diagnostic only, never the gate)"
                         if probe_path else "public dev tasks"),
               "generated_at": datetime.now(timezone.utc).isoformat(), "seed": seed,
               "model": _model_block(service), "prompt": {"label": frontend.prompt.label,
                                                          "sha256": frontend.prompt.sha256},
               "frontend": round_stats(fe_round), "gate_counts": gate,
               "strict_by_config": {c: sum(r.score.passed for r in run) for c, run in result.items()},
               "by_type": {k: dict(v) for k, v in sorted(by_type.items())}}
    _write_exclusive(run_dir / "iteration.json", json.dumps(summary, sort_keys=True, indent=2) + "\n")
    _write_exclusive(run_dir / "frames.jsonl", _jsonl(rows))
    _write_exclusive(run_dir / "prompt.txt", frontend.prompt.text)
    return run_dir


# ---------------------------------------------------------------- stage 2 freeze

def _stage2_body(frontend: ModelFrontend, service, task_ids) -> dict[str, Any]:
    tasks, catalogue = _dev_tasks(task_ids), dev_catalogue()
    profile = profile_catalogue(catalogue)
    identity = frontend.identity()
    schema_bytes = json.dumps(frame_schema(profile), sort_keys=True, separators=(",", ":")).encode("utf-8")
    return {
        "schema_version": "1.0", "protocol_version": PROTOCOL_VERSION, "stage": 2,
        "stage1_document_sha256": _sha_bytes(STAGE1_DOC.read_bytes()),
        "private_v2_receipt_sha256": _sha_bytes(PRIVATE_V2_RECEIPT.read_bytes()) if PRIVATE_V2_RECEIPT.is_file() else None,
        "model": _model_block(service), "frontend_identity": identity,
        "prompt": {"label": frontend.prompt.label, "sha256": frontend.prompt.sha256,
                   "file": PROMPT_PATH.relative_to(ROOT).as_posix()},
        "frame_schema": {"structure": "frozen; role and field enums filled from the declared catalogue",
                         "dev_instance_sha256": _sha_bytes(schema_bytes)},
        "dev": {"tasks_sha256": _hash(DEV_TASKS), "task_count": len(tasks),
                "task_ids_sha256": _sha_bytes("\n".join(sorted(t.task_id for t in tasks)).encode("utf-8")),
                "catalogue": catalogue.identity(), "profile_sha256": profile.sha256()},
        "systems": [s.config() for s in model_systems(IdentitySource(identity), include_ablations=True)],
        "seeds": list(SEEDS), "gate": dict(GATE), "source_fingerprint": source_fingerprint_v2(),
        "definitions": DEFINITIONS,
    }


def freeze_stage2(frontend: ModelFrontend, output_root: Path = RESULTS / "freeze", *, task_ids=None) -> Path:
    service = frontend.verify_model()
    path = Path(output_root) / STAGE2_NAME
    if path.exists():
        raise FileExistsError("M11c v2 stage-2 manifest already exists")
    body = _stage2_body(frontend, service, task_ids)
    Path(output_root).mkdir(parents=True, exist_ok=True)
    _write_exclusive(path, json.dumps(body, sort_keys=True, indent=2) + "\n")
    return path


def _verify_stage2(manifest_path: Path, frontend: Optional[ModelFrontend], task_ids, *,
                   check_fingerprint: bool = True) -> tuple[dict[str, Any], str]:
    raw = Path(manifest_path).read_bytes()
    manifest = json.loads(raw)
    if manifest.get("protocol_version") != PROTOCOL_VERSION or manifest.get("stage") != 2:
        raise ValueError("not an m11c-protocol-v2 stage-2 manifest")
    if manifest["stage1_document_sha256"] != _sha_bytes(STAGE1_DOC.read_bytes()):
        raise ValueError("stage-1 freeze document changed")
    if check_fingerprint and manifest["source_fingerprint"] != source_fingerprint_v2():
        raise ValueError("source code or prompt file changed after stage 2; publish a new version")
    identity = _identity_from_manifest(manifest)
    if frontend is not None:
        if frontend.prompt.sha256 != manifest["prompt"]["sha256"] or frontend.identity() != identity:
            raise ValueError("prompt or front-end identity differs from stage 2")
        if frontend.verify_model()["digest"] != manifest["model"]["digest"]:
            raise ValueError("model digest differs from stage 2")
    systems = [s.config() for s in model_systems(IdentitySource(identity), include_ablations=True)]
    if systems != manifest["systems"]:
        raise ValueError("system configuration differs from stage 2")
    tasks = _dev_tasks(task_ids)
    if manifest["dev"]["tasks_sha256"] != _hash(DEV_TASKS) or manifest["dev"]["task_ids_sha256"] != \
            _sha_bytes("\n".join(sorted(t.task_id for t in tasks)).encode("utf-8")):
        raise ValueError("dev tasks differ from stage 2")
    if manifest["dev"]["profile_sha256"] != profile_catalogue(dev_catalogue()).sha256():
        raise ValueError("dev catalogue profile differs from stage 2")
    return manifest, _sha_bytes(raw)


# ---------------------------------------------------------------- formal dev run and stage 3

def _v1_comparison(report_dev: dict[str, Any]) -> dict[str, Any]:
    if not V1_REPORT.is_file():
        return {"available": False}
    v1 = json.loads(V1_REPORT.read_text("utf-8"))["splits"]["dev"]["configs"]
    out = {"available": True, "v1_report_sha256": _sha_bytes(V1_REPORT.read_bytes()), "configs": {}}
    for config, item in report_dev["configs"].items():
        old = v1.get(config)
        new_full = item["subsets"]["full"]["across_runs"]
        row = {"v2_strict_per_run": new_full.get("strict_pass_per_run"),
               "v2_secondary_per_run": new_full.get("secondary_pass_per_run")}
        if old:
            old_full = old["subsets"]["full"]["across_runs"]
            row.update(v1_strict_per_run=old_full.get("strict_pass_per_run"),
                       v1_secondary_per_run=old_full.get("secondary_pass_per_run"))
            row["by_task_type"] = {
                group: {"v1_strict_per_run": old["by_task_type"].get(group, {}).get("strict_pass_per_run"),
                        "v2_strict_per_run": stats.get("strict_pass_per_run")}
                for group, stats in item["by_task_type"].items()}
        out["configs"][config] = row
    return out


def _execute_dev(frontend_or_records, tasks, catalogue, identity, seeds, *, replay: bool):
    runs: dict[str, list] = defaultdict(list)
    rounds = []
    for seed in seeds:
        if replay:
            fe_round = replay_round(frontend_or_records, tasks, catalogue, seed=seed)
        else:
            fe_round = frontend_round(frontend_or_records, tasks, catalogue, seed=seed)
        result = evaluate_split(tasks, catalogue, fe_round, identity, seed=seed, include_ablations=True)
        for config, records in result.items():
            runs[config].append(records)
        rounds.append(fe_round)
    return runs, rounds


def _dev_receipt(manifest_sha: str, seeds, runs, rounds) -> dict[str, Any]:
    gates = [gate_check(runs["validator_replan"][i], rounds[i]) for i in range(len(seeds))]
    return {"protocol_version": PROTOCOL_VERSION, "stage2_manifest_sha256": manifest_sha, "seeds": list(seeds),
            "splits": {"dev": receipt_entries(runs, list(seeds))},
            "frontend": {"dev": [frontend_receipt(r) for r in rounds]},
            "gate": [{"seed": g["seed"], "counts": g["counts"], "passed": g["passed"]} for g in gates]}


def _receipt_bytes(receipt: dict[str, Any]) -> bytes:
    return (json.dumps(receipt, sort_keys=True, indent=2) + "\n").encode("utf-8")


def _markdown(report: dict[str, Any]) -> str:
    meta = report["metadata"]
    lines = [f"# M11c v2 comparison ({meta['split_label']})", "",
             f"- Protocol {meta['protocol_version']}; stage-2 manifest {meta['stage2_manifest_sha256']}; "
             f"stage-1 document {meta['stage1_document_sha256']}.",
             f"- Model {meta['model']['model_id']} (digest {meta['model']['digest']}), Ollama "
             f"{meta['model']['ollama_version']}, CPU only; options {json.dumps(meta['model']['decoding_options'], sort_keys=True)}; "
             f"prompt {meta['prompt']['label']} sha256:{meta['prompt']['sha256']}.",
             f"- Code: git {meta['git_commit']} (dirty: {meta['working_tree_dirty']}); source fingerprint "
             f"{meta['source_fingerprint']}. Seeds {meta['seeds']}; cost 0 USD (local CPU).",
             "- Primary metric: official M11b v2.0 strict pass. \"除 required_steps 外的严格通过率\" is a "
             "diagnostic secondary metric only.", ""]
    for split, data in report["splits"].items():
        lines += [f"## {split}", "", "Subset sizes: " + ", ".join(f"{k} {v}" for k, v in data["subsets"].items()), "",
                  "| Config | Subset | Tasks | Strict pass rate | Secondary (diagnostic) | Coverage | "
                  "Latency mean ms | Tool calls mean | Stability |", "|---|---|---|---|---|---|---|---|---|"]
        for config, item in data["configs"].items():
            for subset in ("full", "common_executable", "agent_executable", "decision"):
                stats = item["subsets"][subset]["across_runs"]
                if not stats.get("tasks"):
                    lines.append(f"| {config} | {subset} | 0 | — | — | — | — | — | — |")
                    continue
                lines.append(f"| {config} | {subset} | {stats['tasks']} | {_fmt(stats, 'strict_pass_rate')} | "
                             f"{_fmt(stats, 'secondary_pass_rate')} | {_fmt(stats, 'coverage')} | "
                             f"{_fmt(stats, 'latency_mean_ms', 2)} | {_fmt(stats, 'tool_calls_mean', 2)} | "
                             f"{item['stability']['identical']}/{item['stability']['tasks']} |")
        lines += ["", "Strict pass per run (full): " + "; ".join(
            f"{c} {i['subsets']['full']['across_runs'].get('strict_pass_per_run')}" for c, i in data["configs"].items()),
            "", "Strict pass per run by task type/capability:", ""]
        for config, item in data["configs"].items():
            lines.append(f"- {config}: " + "; ".join(f"{g} {s.get('strict_pass_per_run')}"
                                                      for g, s in item["by_task_type"].items()))
        lines += ["", "Front-end per run: " + "; ".join(
            f"seed {s['seed']}: calls {s['logical_calls']}, attempts {s['attempts']}, failures {s['failures']}, "
            f"retries {s['retries']}, timeouts {s['timeouts']}, prompt/eval tokens {s['prompt_tokens']}/"
            f"{s['eval_tokens']}, model wall {s['model_wall_ms'] / 1000:.1f} s"
            for s in data["frontend"]["per_run"]), ""]
        if "gate" in data:
            lines += ["Gate per run (validator strict >= 120/160, rank 8/8, missing_place >= 7/8, failures <= 3/160): "
                      + "; ".join(f"seed {g['seed']} {json.dumps(g['counts'], sort_keys=True)} passed={g['passed']}"
                                  for g in data["gate"]), ""]
        lines += ["Error analysis (run 1): " + "; ".join(
            f"{c} declined {json.dumps(i['error_analysis']['declined_reasons'], sort_keys=True)}, failures "
            f"{json.dumps(i['error_analysis']['failure_by_task_type'], sort_keys=True)}"
            for c, i in data["configs"].items()), ""]
    if report.get("v1_comparison", {}).get("available"):
        lines += ["## Change relative to m11c-protocol-v1 (public dev, rule front-end)", "",
                  "| Config | v1 strict per run | v2 strict per run | v1 secondary | v2 secondary |", "|---|---|---|---|---|"]
        for config, row in report["v1_comparison"]["configs"].items():
            lines.append(f"| {config} | {row.get('v1_strict_per_run')} | {row['v2_strict_per_run']} | "
                         f"{row.get('v1_secondary_per_run')} | {row['v2_secondary_per_run']} |")
        lines.append("")
    lines += ["## Limitations", "",
              "- 120/160 validator strict is only the entry gate to the sealed private evaluation, not a claim of "
              "improvement. Single-dataset buffer stays in the denominator and is unsupported; rank is unsupported.",
              "- M11b v2.0 references and the strict scorer do not record or check per-region aggregate values "
              "(aggregate tasks are scored on tool path, status, region set, geometry and CRS).",
              "- Clean data cannot show retry/replan recovery benefit. Prompt examples include public dev tasks "
              "and dev-derived paraphrases, so public dev numbers are optimistic for unseen phrasing.", ""]
    return "\n".join(lines)


def _report(split_label: str, manifest: dict[str, Any], manifest_sha: str, seeds, split: str,
            tasks, runs, rounds, *, publish_task_rows: bool, gates=None) -> dict[str, Any]:
    commit, dirty = _git_state()
    data = split_report(tasks, runs, combined_matrix(runs), publish_task_rows=publish_task_rows)
    data["frontend"] = {"per_run": [round_stats(r) for r in rounds],
                        "decisions_identical_across_runs": sum(
                            len({r.decisions[t].model_dump_json() for r in rounds}) == 1 for t in rounds[0].order),
                        "tasks": len(rounds[0].order)}
    if gates is not None:
        data["gate"] = gates
    return {"metadata": {"protocol_version": PROTOCOL_VERSION, "split_label": split_label,
                         "generated_at": datetime.now(timezone.utc).isoformat(),
                         "stage2_manifest_sha256": manifest_sha,
                         "stage1_document_sha256": manifest["stage1_document_sha256"],
                         "private_v2_receipt_sha256": manifest["private_v2_receipt_sha256"],
                         "git_commit": commit, "working_tree_dirty": dirty,
                         "source_fingerprint": source_fingerprint_v2(), "seeds": list(seeds),
                         "environment": _environment(), "definitions": DEFINITIONS, "model": manifest["model"],
                         "prompt": manifest["prompt"], "frontend_identity": manifest["frontend_identity"],
                         "configs": {s["system_id"]: s for s in manifest["systems"]},
                         "estimated_cost_usd": 0.0},
            "splits": {split: data}}


def run_dev(frontend: ModelFrontend, manifest_path: Path, output_root: Path = RESULTS / "formal",
            run_id: str = "m11c_v2_dev_formal", *, task_ids=None, seeds=SEEDS) -> Path:
    manifest, manifest_sha = _verify_stage2(manifest_path, frontend, task_ids)
    run_dir = Path(output_root) / run_id
    Path(output_root).mkdir(parents=True, exist_ok=True)
    run_dir.mkdir(exist_ok=False)
    seeds = list(seeds)
    tasks, catalogue = _dev_tasks(task_ids), dev_catalogue()
    runs, rounds = _execute_dev(frontend, tasks, catalogue, frontend.identity(), seeds, replay=False)
    calls_text = _jsonl(row for r in rounds for row in round_records(r))
    _write_exclusive(run_dir / "frontend_calls.jsonl", calls_text)
    receipt = _dev_receipt(manifest_sha, seeds, runs, rounds)
    gates = receipt["gate"]
    report = _report("public dev", manifest, manifest_sha, seeds, "dev", tasks, runs, rounds,
                     publish_task_rows=True, gates=gates)
    with tempfile.TemporaryDirectory(prefix="m11c_v2_tasks_") as temp:
        tasks_path = DEV_TASKS
        if task_ids is not None:
            tasks_path = Path(temp) / "dev_subset.jsonl"
            tasks_path.write_text("".join(line + "\n" for line in DEV_TASKS.read_text("utf-8").splitlines()
                                          if line.strip() and json.loads(line)["task_id"] in task_ids), "utf-8")
        report["official_m11b_reports"] = {"dev": _official_reports(run_dir, "dev", tasks_path, {"runs": runs})}
    report["v1_comparison"] = _v1_comparison(report["splits"]["dev"])
    rows = report["splits"]["dev"].pop("task_rows")
    _write_exclusive(run_dir / "dev_task_results.jsonl", _jsonl(rows))
    _write_exclusive(run_dir / "report.json", json.dumps(report, sort_keys=True, indent=2) + "\n")
    _write_exclusive(run_dir / "report.md", _markdown(report))
    receipt_bytes = _receipt_bytes(receipt)
    with (run_dir / "deterministic_receipt.json").open("xb") as stream:
        stream.write(receipt_bytes)
    stage3 = {"protocol_version": PROTOCOL_VERSION, "stage": 3, "stage2_manifest_sha256": manifest_sha,
              "gate_passed": all(g["passed"] for g in gates), "runs": gates, "thresholds": dict(GATE),
              "result_sha256": {name: _sha_bytes((run_dir / name).read_bytes()) for name in
                                ("report.json", "report.md", "deterministic_receipt.json", "frontend_calls.jsonl",
                                 "dev_task_results.jsonl")}}
    _write_exclusive(run_dir / STAGE3_NAME, json.dumps(stage3, sort_keys=True, indent=2) + "\n")
    return run_dir


def replay_receipt(run_dir: Path, manifest_path: Path, *, task_ids=None, check_fingerprint: bool = False) -> bytes:
    """Rebuild the deterministic receipt from recorded raw model responses (no model call)."""
    manifest, manifest_sha = _verify_stage2(manifest_path, None, task_ids, check_fingerprint=check_fingerprint)
    published = json.loads((Path(run_dir) / "deterministic_receipt.json").read_text("utf-8"))
    records = [json.loads(line) for line in (Path(run_dir) / "frontend_calls.jsonl").read_text("utf-8").splitlines()]
    seeds = published["seeds"]
    runs, rounds = _execute_dev(records, _dev_tasks(task_ids), dev_catalogue(), _identity_from_manifest(manifest),
                                seeds, replay=True)
    return _receipt_bytes(_dev_receipt(manifest_sha, seeds, runs, rounds))


# ---------------------------------------------------------------- private v2 (once, after the gate)

def private_evaluation(frontend: ModelFrontend, workspace, *, seeds=SEEDS,
                       receipt_path: Path = PRIVATE_V2_RECEIPT) -> tuple[dict[str, Any], dict[str, Any]]:
    """Formal configurations on the sealed package; returns (public aggregate, private detail)."""
    tasks, catalogue = verify_sealed(workspace, receipt_path)
    runs: dict[str, list] = defaultdict(list)
    rounds = []
    for seed in seeds:
        fe_round = frontend_round(frontend, tasks, catalogue, seed=seed)
        for config, records in evaluate_split(tasks, catalogue, fe_round, frontend.identity(), seed=seed,
                                              include_ablations=False).items():
            runs[config].append(records)
        rounds.append(fe_round)
    public = split_report(tasks, runs, combined_matrix(runs), publish_task_rows=False)
    public["frontend"] = {"per_run": [round_stats(r) for r in rounds],
                          "decisions_identical_across_runs": sum(
                              len({r.decisions[t].model_dump_json() for r in rounds}) == 1 for t in rounds[0].order),
                          "tasks": len(rounds[0].order)}
    public["receipt"] = {"splits": {"private_v2": receipt_entries(runs, list(seeds))},
                         "frontend": [frontend_receipt(r) for r in rounds]}
    by_id = {t.task_id: t for t in tasks}
    private = {"frontend_calls": [row for r in rounds for row in round_records(r)],
               "task_rows": [{"config": config, "run": index + 1, "task_id": r.task.task_id,
                              "query": by_id[r.task.task_id].query, "task_type": r.task.task_type,
                              "executable": r.executable, "status": r.prediction.status,
                              "reason_code": r.prediction.reason_code, "strict_pass": r.score.passed,
                              "secondary_pass": secondary_pass(r.score),
                              "failure_categories": r.score.failure_categories,
                              "tool_sequence": r.prediction.tool_sequence, "latency_ms": r.prediction.latency_ms,
                              "llm_calls": r.prediction.llm_calls}
                             for config, run_list in runs.items() for index, run in enumerate(run_list) for r in run]}
    return public, private


def run_private(frontend: ModelFrontend, manifest_path: Path, dev_run_dir: Path, workspace,
                output_root: Path = RESULTS / "private", run_id: str = "m11c_v2_private_formal", *,
                seeds=SEEDS, receipt_path: Path = PRIVATE_V2_RECEIPT) -> Path:
    manifest, manifest_sha = _verify_stage2(manifest_path, frontend, None)
    stage3 = json.loads((Path(dev_run_dir) / STAGE3_NAME).read_text("utf-8"))
    if stage3.get("stage2_manifest_sha256") != manifest_sha:
        raise ValueError("stage-3 verdict belongs to a different stage-2 manifest")
    for name, digest in stage3["result_sha256"].items():
        if _sha_bytes((Path(dev_run_dir) / name).read_bytes()) != digest:
            raise ValueError("dev result files changed after the stage-3 verdict")
    if stage3.get("gate_passed") is not True:
        raise ValueError("stage-3 public dev gate not passed; private v2 evaluation refused")
    if _sha_bytes(Path(receipt_path).read_bytes()) != manifest["private_v2_receipt_sha256"]:
        raise ValueError("private v2 receipt differs from stage 2")
    workspace = workspace_path(workspace)
    public_dir = Path(output_root) / run_id
    private_dir = workspace / PRIVATE_RUN_DIR / run_id
    if public_dir.exists() or private_dir.exists() or (Path(output_root).is_dir() and any(Path(output_root).iterdir())):
        raise FileExistsError("the formal private v2 evaluation has already been run")
    public, private = private_evaluation(frontend, workspace, seeds=seeds, receipt_path=receipt_path)
    return write_private_outputs(public_dir, private_dir, manifest, manifest_sha,
                                 _sha_bytes((Path(dev_run_dir) / STAGE3_NAME).read_bytes()), seeds, public, private)


def write_private_outputs(public_dir: Path, private_dir: Path, manifest: dict[str, Any], manifest_sha: str,
                          stage3_sha: str, seeds, public: dict[str, Any], private: dict[str, Any]) -> Path:
    private_dir.mkdir(parents=True, exist_ok=False)
    _write_exclusive(private_dir / "frontend_calls.jsonl", _jsonl(private["frontend_calls"]))
    _write_exclusive(private_dir / "task_rows.jsonl", _jsonl(private["task_rows"]))
    public_dir.mkdir(parents=True, exist_ok=False)
    receipt = {"protocol_version": PROTOCOL_VERSION, "stage2_manifest_sha256": manifest_sha,
               "stage3_sha256": stage3_sha, "seeds": list(seeds), **public.pop("receipt")}
    commit, dirty = _git_state()
    report = {"metadata": {"protocol_version": PROTOCOL_VERSION, "split_label": "sealed private v2, aggregate only",
                           "generated_at": datetime.now(timezone.utc).isoformat(),
                           "stage2_manifest_sha256": manifest_sha,
                           "stage1_document_sha256": manifest["stage1_document_sha256"],
                           "stage3_sha256": receipt["stage3_sha256"],
                           "private_v2_receipt_sha256": manifest["private_v2_receipt_sha256"],
                           "git_commit": commit, "working_tree_dirty": dirty,
                           "source_fingerprint": source_fingerprint_v2(), "seeds": list(seeds),
                           "environment": _environment(), "definitions": DEFINITIONS, "model": manifest["model"],
                           "prompt": manifest["prompt"], "frontend_identity": manifest["frontend_identity"],
                           "configs": {s["system_id"]: s for s in manifest["systems"] if s["system_id"] in FORMAL_IDS},
                           "estimated_cost_usd": 0.0, "content_published": False},
              "splits": {"private_v2": public}}
    _write_exclusive(public_dir / "report.json", json.dumps(report, sort_keys=True, indent=2) + "\n")
    _write_exclusive(public_dir / "report.md", _markdown(report))
    _write_exclusive(public_dir / "deterministic_receipt.json", json.dumps(receipt, sort_keys=True, indent=2) + "\n")
    return public_dir


def replay_private(public_dir: Path, manifest_path: Path, workspace, *,
                   receipt_path: Path = PRIVATE_V2_RECEIPT) -> dict[str, Any]:
    """Rebuild the private aggregate receipt from the workspace's recorded raw responses."""
    manifest, _ = _verify_stage2(manifest_path, None, None, check_fingerprint=False)
    published = json.loads((Path(public_dir) / "deterministic_receipt.json").read_text("utf-8"))
    tasks, catalogue = verify_sealed(workspace, receipt_path)
    private_dir = workspace_path(workspace) / PRIVATE_RUN_DIR / Path(public_dir).name
    records = [json.loads(line) for line in (private_dir / "frontend_calls.jsonl").read_text("utf-8").splitlines()]
    identity = _identity_from_manifest(manifest)
    runs: dict[str, list] = defaultdict(list)
    rounds = []
    for seed in published["seeds"]:
        fe_round = replay_round(records, tasks, catalogue, seed=seed)
        for config, result in evaluate_split(tasks, catalogue, fe_round, identity, seed=seed,
                                             include_ablations=False).items():
            runs[config].append(result)
        rounds.append(fe_round)
    rebuilt = {"splits": {"private_v2": receipt_entries(runs, list(published["seeds"]))},
               "frontend": [frontend_receipt(r) for r in rounds]}
    if json.loads(json.dumps(rebuilt)) != {key: published[key] for key in ("splits", "frontend")}:
        raise ValueError("private v2 receipt differs from the replay")
    return {"verified": True, "receipt_sha256": _sha_bytes((Path(public_dir) / "deterministic_receipt.json").read_bytes())}


def scan_leaks(paths: list[Path], workspace, *, receipt_path: Path = PRIVATE_V2_RECEIPT) -> dict[str, int]:
    """Count private strings (queries, IDs, role/field names, text values) in public files."""
    tasks, catalogue = verify_sealed(workspace, receipt_path)
    profile = profile_catalogue(catalogue)
    words = {"roles": set(profile.roles()), "fields": {f.name for d in profile.datasets for f in d.fields},
             "text_values": {json.loads(raw) for d in profile.datasets for f in d.fields
                             for kind, raw in f.distinct if kind == "str" and len(json.loads(raw)) >= 4}}
    texts = "\n".join(Path(p).read_text("utf-8") for path in paths for p in
                      ([path] if Path(path).is_file() else sorted(Path(path).rglob("*")))
                      if Path(p).is_file())
    counts = {"queries": sum(texts.count(t.query) for t in tasks),
              "task_ids": sum(texts.count(t.task_id) for t in tasks)}
    for name, values in words.items():
        counts[name] = sum(len(re.findall(rf"(?<![A-Za-z0-9_]){re.escape(v)}(?![A-Za-z0-9_])", texts))
                           for v in values)
    return counts


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    it = sub.add_parser("dev-iterate")
    it.add_argument("--run-id", required=True)
    it.add_argument("--output-root", type=Path, default=RESULTS / "dev_iterations")
    it.add_argument("--probe", type=Path, help="dev-derived paraphrase probe file (diagnostic only)")
    frz = sub.add_parser("freeze-stage2")
    frz.add_argument("--output-root", type=Path, default=RESULTS / "freeze")
    dev = sub.add_parser("run-dev")
    dev.add_argument("--manifest", type=Path, default=RESULTS / "freeze" / STAGE2_NAME)
    dev.add_argument("--output-root", type=Path, default=RESULTS / "formal")
    dev.add_argument("--run-id", default="m11c_v2_dev_formal")
    prv = sub.add_parser("run-private")
    prv.add_argument("--workspace", default=os.environ.get("GEOAI_M11C_PRIVATE_V2_DIR"))
    prv.add_argument("--manifest", type=Path, default=RESULTS / "freeze" / STAGE2_NAME)
    prv.add_argument("--dev-run", type=Path, default=RESULTS / "formal" / "m11c_v2_dev_formal")
    prv.add_argument("--output-root", type=Path, default=RESULTS / "private")
    leak = sub.add_parser("scan-leaks")
    leak.add_argument("--workspace", default=os.environ.get("GEOAI_M11C_PRIVATE_V2_DIR"))
    leak.add_argument("paths", nargs="+", type=Path)
    rep = sub.add_parser("replay-private")
    rep.add_argument("--workspace", default=os.environ.get("GEOAI_M11C_PRIVATE_V2_DIR"))
    rep.add_argument("--manifest", type=Path, default=RESULTS / "freeze" / STAGE2_NAME)
    rep.add_argument("--public-dir", type=Path, default=RESULTS / "private" / "m11c_v2_private_formal")
    args = parser.parse_args()
    if args.command == "scan-leaks":
        print(json.dumps(scan_leaks(args.paths, args.workspace), sort_keys=True))
        return
    if args.command == "replay-private":
        print(json.dumps(replay_private(args.public_dir, args.manifest, args.workspace), sort_keys=True))
        return
    frontend = ModelFrontend(load_prompt())
    if args.command == "dev-iterate":
        print(f"iteration written to {dev_iterate(frontend, args.output_root, args.run_id, probe_path=args.probe)}")
    elif args.command == "freeze-stage2":
        print(f"stage-2 manifest written to {freeze_stage2(frontend, args.output_root)}")
    elif args.command == "run-dev":
        path = run_dev(frontend, args.manifest, args.output_root, args.run_id)
        gate = json.loads((path / STAGE3_NAME).read_text("utf-8"))
        print(f"dev formal run written to {path}; gate passed: {gate['gate_passed']}")
    else:
        path = run_private(frontend, args.manifest, args.dev_run, args.workspace, args.output_root)
        print(f"private v2 aggregate report written to {path}")


if __name__ == "__main__":
    main()
