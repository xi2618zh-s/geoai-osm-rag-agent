"""M11c evaluation harness: catalogues, system inputs and capability matrix.

Systems receive only SystemInput(task_id, query) and the split's full dataset
catalogue. Benchmark task metadata and reference answers stay on the evaluator
side and are used only for scoring and reporting.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import random
import statistics
import tempfile
from typing import Any, Iterable, Literal, Optional

from pydantic import Field, StrictStr, model_validator

from src.agent.baselines import (
    STRICT_EMPTY_POLICY, CatalogueFile, DatasetCatalogue, DynamicAgentSystem,
    FixedWorkflowSystem, RawQueryValidatorSystem, SystemInput, TemplateWorkflowSystem,
    ValidatorReplanSystem, register_catalogue,
)
from src.agent.benchmark_frontend import interpret
from src.agent.dynamic_planner import plan_request
from src.datasets import LocalDatasetRegistry
from src.evaluation.m11b import M11bPrediction, M11bScore, M11bTask, score_m11b

PROTOCOL_VERSION = "m11c-protocol-v1"
SEEDS = (11071, 11072, 11073)
FORMAL_IDS = ("fixed_workflow", "dynamic_agent", "validator_replan")
COMPONENTS = ("status_reason", "operation_match", "exact_fields", "required_steps",
              "tool_sequence", "feature_f1", "numeric_tolerance", "crs_match",
              "geometry_equivalence")

ROOT = Path(__file__).resolve().parents[2]
DEV_FIXTURES = ROOT / "data/fixtures/tools/m04b/v1"
PRIVATE_DIR = ROOT / "benchmarks/data/test/private"
# Evaluator-side declaration: the private package ships no CRS metadata. The value
# was taken as a bare EPSG token from the hidden builder, without reading content.
PRIVATE_DECLARED_CRS = "EPSG:3857"


PRIVATE_V2_VERSION = "m11c-private-test-v2"
PRIVATE_V2_FAMILIES = ("search", "buffer", "nearest", "within", "spatial_join",
                       "aggregate_by_region", "rank", "missing_place")


class M11cPrivateTask(M11bTask):
    """M11b v2.0 task semantics for the sealed private v2 holdout (protocol v2)."""

    task_id: StrictStr = Field(pattern=r"^m11b_test_2[0-9]{2}$")
    split: Literal["test"] = "test"
    data_version: Literal["m11c-private-test-v2"] = PRIVATE_V2_VERSION

    @model_validator(mode="after")
    def capability_matches(self):  # overrides the v1 data-version rule only
        if self.capability == "supported":
            if self.task_type == "rank" or self.expectation.status != "success":
                raise ValueError("supported task must have supported operation and success reference")
            if not self.source_fixtures:
                raise ValueError("supported task requires source fixtures")
        elif self.capability == "unsupported":
            if self.task_type != "rank" or self.expectation.reason_code != "unsupported_rank":
                raise ValueError("rank unsupported task must use unsupported_rank")
        elif self.expectation.status != "clarification":
            raise ValueError("clarification task must expect clarification")
        return self


def load_m11c_private_v2_tasks(path: Path) -> list[M11cPrivateTask]:
    rows, ids, queries = [], set(), set()
    for line_number, line in enumerate(Path(path).read_text("utf-8").splitlines(), 1):
        if not line.strip():
            continue
        task = M11cPrivateTask.model_validate_json(line)
        norm = " ".join(task.query.casefold().split())
        if task.task_id in ids or norm in queries:
            raise ValueError(f"duplicate private v2 ID or query at line {line_number}")
        ids.add(task.task_id)
        queries.add(norm)
        rows.append(task)
    if not rows:
        raise ValueError("empty private v2 task file")
    return rows


def _role(file_name: str) -> str:
    return Path(file_name).stem.rsplit("_", 1)[-1].casefold()


def dev_catalogue() -> DatasetCatalogue:
    manifest = json.loads((DEV_FIXTURES / "manifest.json").read_text("utf-8"))
    files = tuple(CatalogueFile(
        role=_role(name), path=DEV_FIXTURES / name, crs=item["crs"],
        source="m04b-fixture", source_version=manifest["fixture_version"],
        sha256=item["sha256"]) for name, item in sorted(manifest["fixtures"].items()))
    return DatasetCatalogue(name="m04b-v1", scope="Synthetic dev catalogue", files=files)


def private_catalogue() -> DatasetCatalogue:
    manifest_path = PRIVATE_DIR / "m11b_private_manifest_v1.json"
    if not manifest_path.is_file():
        raise FileNotFoundError("private M11b package is not present in this workspace")
    manifest = json.loads(manifest_path.read_text("utf-8"))
    files = tuple(CatalogueFile(
        role=_role(name), path=PRIVATE_DIR / name, crs=PRIVATE_DECLARED_CRS,
        source="m11b-private", source_version=manifest["data_version"], sha256=digest)
        for name, digest in sorted(manifest["source_sha256"].items()))
    return DatasetCatalogue(name=manifest["data_version"], scope="Synthetic test catalogue", files=files)


def system_input(task: M11bTask) -> SystemInput:
    return SystemInput(task_id=task.task_id, query=task.query)


class _OfflineTagRetriever:
    """P4 tag retrieval stand-in; M11b GeoJSON tasks must never reach OSM tag search."""

    def retrieve(self, query, k=3):
        raise RuntimeError("P4 OSM tag retrieval reached")


def p4_frontend_code(query: str) -> str:
    """Native P4 compiler outcome; P4 data path is OSM PBF, not M04 DatasetRefs."""
    from src.agent.planner import PlanCompilationError, compile_natural_language_intent
    try:
        compile_natural_language_intent(query, retriever=_OfflineTagRetriever())
    except PlanCompilationError as error:
        return error.code
    except RuntimeError:
        return "P4_TAG_RETRIEVAL_REACHED"
    return "P4_COMPILED_OSM_INTENT"


def capability_matrix(tasks: Iterable[M11bTask], catalogue: DatasetCatalogue,
                      systems: Iterable[Any] = ()) -> list[dict[str, Any]]:
    """Per-task front-end/planner decisions and each system's routing, without executing tools."""
    systems = list(systems)
    rows = []
    with tempfile.TemporaryDirectory(prefix="m11c_matrix_") as temp:
        refs = register_catalogue(catalogue, LocalDatasetRegistry(Path(temp) / "datasets"))
        for task in tasks:
            item = system_input(task)
            decision = interpret(item.query, refs, scope=catalogue.scope)
            row = {"task_id": item.task_id, "frontend_status": decision.status,
                   "frontend_operation": decision.operation,
                   "frontend_reason": decision.reason_code, "command": decision.command,
                   "planner_status": None, "planner_task_class": None,
                   "planner_error": None, "planned_tools": []}
            if decision.status == "request":
                plan = plan_request(decision.request)
                row.update(planner_status=plan.status, planner_task_class=plan.task_class,
                           planner_error=plan.error_code or (plan.clarification.reason_code
                                                             if plan.clarification else None),
                           planned_tools=[step.tool for step in plan.tool_steps])
            routes = {system.system_id: system.route(item.query, refs, catalogue) for system in systems}
            row["executable"] = {name: route.executable for name, route in routes.items()}
            row["declined"] = {name: list(route.declined) if route.declined else None
                               for name, route in routes.items()}
            rows.append(row)
    return rows


def formal_systems() -> list[Any]:
    return [FixedWorkflowSystem(), DynamicAgentSystem(), ValidatorReplanSystem()]


def ablation_systems() -> list[Any]:
    """Public-dev ablations frozen with the protocol: FW-B and validator A1-A3."""
    return [TemplateWorkflowSystem(), RawQueryValidatorSystem(),
            ValidatorReplanSystem(policy=STRICT_EMPTY_POLICY, system_id="validator_strict_empty"),
            ValidatorReplanSystem(allow_replan=False, system_id="validator_no_replan")]


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def source_fingerprint() -> str:
    """Working-tree identity of the code that defines and runs the systems."""
    files = sorted(path for path in (ROOT / "src").rglob("*.py") if "__pycache__" not in path.parts)
    files += [ROOT / "benchmarks/run_m11c_baselines.py", ROOT / "benchmarks/run_m11b_scoring.py"]
    lines = [f"{path.relative_to(ROOT).as_posix()}:{_sha(path.read_bytes())}" for path in files]
    return "sha256:" + _sha("\n".join(lines).encode("utf-8"))


@dataclass
class TaskRecord:
    task: M11bTask
    prediction: M11bPrediction
    score: M11bScore
    trace: dict[str, Any]
    executable: bool


def secondary_pass(score: M11bScore) -> bool:
    """Diagnostic, non-official: strict pass ignoring only the required_steps component."""
    return all(item.passed for item in score.components if item.name != "required_steps")


def run_system(system, tasks: Iterable[M11bTask], catalogue: DatasetCatalogue, *, seed: int,
               executable: dict[str, bool]) -> list[TaskRecord]:
    """Run every task once in seeded order; return records in canonical task order."""
    order = sorted(tasks, key=lambda task: task.task_id)
    random.Random(seed).shuffle(order)
    records = {}
    for task in order:
        run = system.run(system_input(task), catalogue)
        records[task.task_id] = TaskRecord(task, run.prediction, score_m11b(task, run.prediction),
                                           run.trace, executable[task.task_id])
    return [records[key] for key in sorted(records)]


def signature(record: TaskRecord) -> str:
    """Deterministic outcome identity of one task run (timings excluded)."""
    p = record.prediction
    payload = {"status": p.status, "reason_code": p.reason_code, "operation": p.operation,
               "passed": record.score.passed, "failures": record.score.failure_categories,
               "feature_ids": sorted(p.feature_ids), "tool_sequence": p.tool_sequence,
               "tool_calls": p.tool_calls, "numeric": p.numeric_values, "geometry": p.geometry,
               "crs": p.crs}
    return _sha(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8"))


def stability(runs: list[list[TaskRecord]]) -> dict[str, Any]:
    count = len(runs[0]) if runs else 0
    identical = sum(1 for index in range(count)
                    if len({signature(run[index]) for run in runs}) == 1)
    return {"tasks": count, "identical": identical, "rate": identical / count if count else None}


def _percentile(values: list[float], percent: float) -> float:
    ordered = sorted(values)
    position = (len(ordered) - 1) * percent / 100
    low = int(position)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


def summarize(records: list[TaskRecord]) -> dict[str, Any]:
    count = len(records)
    if not count:
        return {"tasks": 0}
    strict = sum(record.score.passed for record in records)
    secondary = sum(secondary_pass(record.score) for record in records)
    executable = sum(record.executable for record in records)
    components = {}
    for name in COMPONENTS:
        applicable = [c for record in records for c in record.score.components if c.name == name]
        components[name] = {"passed": sum(c.passed for c in applicable), "applicable": len(applicable)}
    scores = [record.score.overall_score for record in records]
    latencies = [record.prediction.latency_ms for record in records]
    calls = [record.prediction.tool_calls for record in records]
    return {
        "tasks": count, "strict_pass": strict, "strict_pass_rate": strict / count,
        "secondary_pass": secondary, "secondary_pass_rate": secondary / count,
        "executable": executable, "coverage": executable / count, "components": components,
        "score_mean": statistics.fmean(scores), "score_stdev": statistics.pstdev(scores),
        "score_p50": _percentile(scores, 50), "score_p95": _percentile(scores, 95),
        "latency_mean_ms": statistics.fmean(latencies), "latency_stdev_ms": statistics.pstdev(latencies),
        "latency_p50_ms": _percentile(latencies, 50), "latency_p95_ms": _percentile(latencies, 95),
        "tool_calls_mean": statistics.fmean(calls), "tool_calls_total": sum(calls),
        "llm_calls_total": sum(record.prediction.llm_calls for record in records),
        "estimated_cost_usd_total": sum(record.prediction.estimated_cost_usd for record in records),
        "status_counts": dict(sorted(Counter(r.prediction.status for r in records).items())),
        "reason_counts": dict(sorted(Counter(r.prediction.reason_code for r in records
                                             if r.prediction.reason_code).items())),
        "failure_category_counts": dict(sorted(Counter(
            name for record in records for name in record.score.failure_categories).items())),
    }


def across_runs(summaries: list[dict[str, Any]]) -> dict[str, Any]:
    """Mean and sample standard deviation over repeated runs of one configuration."""
    if not summaries or not summaries[0]["tasks"]:
        return {"runs": len(summaries), "tasks": 0}
    out: dict[str, Any] = {"runs": len(summaries), "tasks": summaries[0]["tasks"],
                           "strict_pass_per_run": [item["strict_pass"] for item in summaries],
                           "secondary_pass_per_run": [item["secondary_pass"] for item in summaries]}
    for key in ("strict_pass_rate", "secondary_pass_rate", "coverage", "score_mean",
                "latency_mean_ms", "latency_p50_ms", "latency_p95_ms", "tool_calls_mean"):
        values = [item[key] for item in summaries]
        out[key] = {"mean": statistics.fmean(values),
                    "stdev": statistics.stdev(values) if len(values) > 1 else 0.0}
    return out
