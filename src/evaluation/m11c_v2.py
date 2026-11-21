"""M11c v2 evaluation harness (m11c-protocol-v2): shared model front-end round and gate.

Each run first calls the model once per task, in the run's seeded order (the front-end
round). The grounded decisions are then shared by every model-using configuration of
that run; validator_no_frontend (A1) never uses the model. A prediction's latency adds
the task's front-end wall time and llm_calls counts that task's HTTP attempts. Systems
still receive only SystemInput(task_id, query) and the split catalogue; task metadata
is used here only for scoring, the gate and reporting.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import random
import tempfile
from typing import Any, Iterable

from src.agent.baselines import DatasetCatalogue, register_catalogue
from src.agent.benchmark_frontend import FrontendDecision
from src.agent.model_frontend import LogicalCall, ModelFrontend, ground_frame, profile_catalogue
from src.datasets import LocalDatasetRegistry
from src.evaluation.m11b import M11bTask, score_m11b
from src.evaluation.m11c import (
    TaskRecord, ablation_systems, capability_matrix, formal_systems, summarize, system_input,
)

PROTOCOL_VERSION = "m11c-protocol-v2"
SEEDS = (11071, 11072, 11073)
FORMAL_IDS = ("fixed_workflow", "dynamic_agent", "validator_replan")
MODEL_FREE_IDS = ("validator_no_frontend",)
# Frozen in M11C_V2_EVALUATION_FREEZE.md section 5; judged separately for every formal dev run.
GATE = {"validator_strict_min": 120, "rank_unsupported_required": 8, "missing_place_min": 7,
        "model_failures_max": 3}


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _ordered(tasks: Iterable[M11bTask], seed: int) -> list[M11bTask]:
    order = sorted(tasks, key=lambda task: task.task_id)
    random.Random(seed).shuffle(order)
    return order


@dataclass
class FrontendRound:
    seed: int
    order: list[str]
    queries: dict[str, str]
    calls: dict[str, LogicalCall]
    decisions: dict[str, FrontendDecision]
    profile_sha256: str


def _ground(calls: dict[str, LogicalCall], queries: dict[str, str], catalogue: DatasetCatalogue,
            profile) -> dict[str, FrontendDecision]:
    with tempfile.TemporaryDirectory(prefix="m11c_v2_ground_") as temp:
        refs = register_catalogue(catalogue, LocalDatasetRegistry(Path(temp) / "datasets"))
        return {task_id: ground_frame(call.frame, refs, profile, catalogue.scope, queries[task_id])
                for task_id, call in calls.items()}


def frontend_round(frontend: ModelFrontend, tasks: Iterable[M11bTask], catalogue: DatasetCatalogue, *,
                   seed: int) -> FrontendRound:
    """One logical model call per task, in the run's seeded order."""
    profile = profile_catalogue(catalogue)
    order = _ordered(tasks, seed)
    calls = {task.task_id: frontend.call(task.query, profile) for task in order}
    queries = {t.task_id: t.query for t in order}
    return FrontendRound(seed, [t.task_id for t in order], queries, calls,
                         _ground(calls, queries, catalogue, profile), profile.sha256())


def round_records(fe_round: FrontendRound) -> list[dict[str, Any]]:
    return [{"seed": fe_round.seed, "order_index": index, "task_id": task_id,
             "call": fe_round.calls[task_id].record(),
             "decision": {key: getattr(fe_round.decisions[task_id], key)
                          for key in ("status", "operation", "reason_code", "command")}}
            for index, task_id in enumerate(fe_round.order)]


def replay_round(records: list[dict[str, Any]], tasks: Iterable[M11bTask], catalogue: DatasetCatalogue, *,
                 seed: int) -> FrontendRound:
    """Rebuild a round from recorded raw responses: no model call, frozen parser and grounding."""
    profile = profile_catalogue(catalogue)
    order = _ordered(tasks, seed)
    rows = sorted((row for row in records if row["seed"] == seed), key=lambda row: row["order_index"])
    if [row["task_id"] for row in rows] != [task.task_id for task in order]:
        raise ValueError("recorded front-end round does not match the seeded task order")
    calls = {row["task_id"]: LogicalCall.from_record(row["call"], profile) for row in rows}
    queries = {t.task_id: t.query for t in order}
    replayed = FrontendRound(seed, [t.task_id for t in order], queries, calls,
                             _ground(calls, queries, catalogue, profile), profile.sha256())
    for row in rows:
        decision = replayed.decisions[row["task_id"]]
        if {key: getattr(decision, key) for key in row["decision"]} != row["decision"]:
            raise ValueError("replayed decision differs from the recorded decision")
    return replayed


def round_stats(fe_round: FrontendRound) -> dict[str, Any]:
    calls = list(fe_round.calls.values())
    decisions = list(fe_round.decisions.values())
    return {
        "seed": fe_round.seed, "logical_calls": len(calls),
        "attempts": sum(len(call.attempts) for call in calls),
        "failures": sum(call.failed for call in calls), "retries": sum(call.retries for call in calls),
        "timeouts": sum(call.timeouts for call in calls),
        "attempt_outcomes": dict(sorted(Counter(a.outcome for call in calls for a in call.attempts).items())),
        "prompt_tokens": sum(call.prompt_tokens for call in calls),
        "eval_tokens": sum(call.eval_tokens for call in calls),
        "model_wall_ms": round(sum(call.wall_ms for call in calls), 3),
        "model_load_ms": round(sum(a.load_ms or 0.0 for call in calls for a in call.attempts), 3),
        "decision_status": dict(sorted(Counter(d.status for d in decisions).items())),
        "decision_reasons": dict(sorted(Counter(d.reason_code for d in decisions if d.reason_code).items())),
        "decision_operations": dict(sorted(Counter(d.operation for d in decisions if d.operation).items())),
    }


def decisions_sha256(fe_round: FrontendRound) -> str:
    rows = [{"task_id": task_id, **row["decision"], "frame": row["call"]["frame"]}
            for task_id, row in zip(fe_round.order, round_records(fe_round))]
    return _sha(json.dumps(rows, sort_keys=True, separators=(",", ":")).encode("utf-8"))


class RoundDecisionSource:
    """Decision source injected into systems: returns the run's shared grounded decision."""

    def __init__(self, fe_round: FrontendRound, identity: dict[str, Any]):
        self._by_query = {fe_round.queries[task_id]: decision for task_id, decision in fe_round.decisions.items()}
        self._identity = dict(identity)

    def __call__(self, query, refs, catalogue) -> FrontendDecision:
        return self._by_query[query]

    def identity(self) -> dict[str, Any]:
        return dict(self._identity)


class IdentitySource(RoundDecisionSource):
    """Identity-only source used to record configurations in the stage-2 manifest."""

    def __init__(self, identity: dict[str, Any]):
        self._by_query, self._identity = {}, dict(identity)


def model_systems(source, *, include_ablations: bool) -> list[Any]:
    systems = formal_systems() + (ablation_systems() if include_ablations else [])
    for system in systems:
        if system.system_id not in MODEL_FREE_IDS:
            system.frontend = source
    return systems


def _run_configuration(system, tasks, catalogue, fe_round: FrontendRound, *, seed: int,
                       executable: dict[str, bool]) -> list[TaskRecord]:
    records = {}
    for task in _ordered(tasks, seed):
        run = system.run(system_input(task), catalogue)
        prediction = run.prediction
        if system.system_id not in MODEL_FREE_IDS:
            call = fe_round.calls[task.task_id]
            prediction = prediction.model_copy(update={
                "latency_ms": round(prediction.latency_ms + call.wall_ms, 3), "llm_calls": len(call.attempts)})
        records[task.task_id] = TaskRecord(task, prediction, score_m11b(task, prediction), run.trace,
                                           executable[task.task_id])
    return [records[key] for key in sorted(records)]


def evaluate_split(tasks, catalogue: DatasetCatalogue, fe_round: FrontendRound, identity: dict[str, Any], *,
                   seed: int, include_ablations: bool) -> dict[str, list[TaskRecord]]:
    """Run every configuration once on the round's shared decisions (seeded task order)."""
    tasks = list(tasks)
    systems = model_systems(RoundDecisionSource(fe_round, identity), include_ablations=include_ablations)
    matrix = {row["task_id"]: row["executable"] for row in capability_matrix(tasks, catalogue, systems)}
    return {system.system_id: _run_configuration(
        system, tasks, catalogue, fe_round, seed=seed,
        executable={task_id: flags[system.system_id] for task_id, flags in matrix.items()})
        for system in systems}


def combined_matrix(runs: dict[str, list[list[TaskRecord]]]) -> dict[str, dict[str, bool]]:
    """A task counts as executable by a configuration only if it was executable in every run."""
    matrix: dict[str, dict[str, bool]] = {}
    for config, run_list in runs.items():
        for run in run_list:
            for record in run:
                row = matrix.setdefault(record.task.task_id, {})
                row[config] = row.get(config, True) and record.executable
    return matrix


def gate_check(validator_records: list[TaskRecord], fe_round: FrontendRound) -> dict[str, Any]:
    """Frozen stage-1 entry gate for one formal public-dev run."""
    strict = sum(record.score.passed for record in validator_records)
    rank_total = sum(record.task.task_type == "rank" for record in validator_records)
    rank = sum(record.task.task_type == "rank" and record.prediction.status == "error"
               and record.prediction.reason_code == "unsupported_rank" for record in validator_records)
    missing_total = sum(record.task.capability == "clarification" for record in validator_records)
    missing = sum(record.task.capability == "clarification" and record.prediction.status == "clarification"
                  and record.prediction.reason_code == "missing_place" for record in validator_records)
    failures = round_stats(fe_round)["failures"]
    counts = {"validator_strict": strict, "tasks": len(validator_records), "rank_unsupported": rank,
              "rank_tasks": rank_total, "missing_place": missing, "missing_place_tasks": missing_total,
              "model_failures": failures, "logical_calls": len(fe_round.calls)}
    passed = (strict >= GATE["validator_strict_min"] and rank == GATE["rank_unsupported_required"] == rank_total
              and missing >= GATE["missing_place_min"] and failures <= GATE["model_failures_max"])
    return {"seed": fe_round.seed, "counts": counts, "thresholds": dict(GATE), "passed": passed}


def receipt_entries(runs: dict[str, list[list[TaskRecord]]], seeds) -> dict[str, list[dict[str, Any]]]:
    from src.evaluation.m11c import signature
    out = {}
    for config, run_list in runs.items():
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
                "tool_calls_total": summary["tool_calls_total"], "llm_calls_total": summary["llm_calls_total"],
                "signatures_sha256": "sha256:" + _sha(signatures.encode("utf-8"))})
        out[config] = entries
    return out


def frontend_receipt(fe_round: FrontendRound) -> dict[str, Any]:
    stats = round_stats(fe_round)
    return {key: stats[key] for key in ("seed", "logical_calls", "attempts", "failures", "retries", "timeouts",
                                        "attempt_outcomes", "prompt_tokens", "eval_tokens", "decision_status",
                                        "decision_reasons")} | {"decisions_sha256": decisions_sha256(fe_round),
                                                                "profile_sha256": fe_round.profile_sha256}
