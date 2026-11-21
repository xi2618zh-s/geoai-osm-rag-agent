"""M11c benchmark system configurations over the same M04 data and tools.

Each system sees only a SystemInput (task ID and query) plus a declared dataset
catalogue. Every run loads the catalogue into a fresh DatasetRegistry, so no
state is shared between tasks, systems or repeated runs. All configurations are
offline and deterministic: no language model is called and cost is zero.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
import hashlib
import json
from pathlib import Path
import tempfile
import time
from typing import Any, Literal, Optional

from pydantic import Field, StrictStr
from shapely.geometry import GeometryCollection, mapping, shape
from shapely.ops import unary_union

from src.agent.benchmark_frontend import FRONTEND_VERSION, FrontendDecision, interpret
from src.agent.dynamic_planner import (
    EXTENDED_GRAMMAR_VERSION, M07PlanRequest, PlanningResult, plan_request,
)
from src.agent.validation import ValidationPolicy, _Runner, execute_validated_plan
from src.datasets import DatasetRef, LocalDatasetRegistry
from src.evaluation.contracts import EvaluationContract
from src.evaluation.m11b import M11bPrediction
from src.tools import ANALYSIS_TOOL_REGISTRY

SYSTEMS_VERSION = "m11c-baselines-v1"
VALIDATION_POLICY = ValidationPolicy(allow_empty=True, max_features=10000)
STRICT_EMPTY_POLICY = ValidationPolicy(allow_empty=False, max_features=10000)


@dataclass(frozen=True)
class CatalogueFile:
    role: str
    path: Path
    crs: str
    source: str
    source_version: str
    sha256: str


@dataclass(frozen=True)
class DatasetCatalogue:
    name: str
    scope: str
    files: tuple[CatalogueFile, ...]

    def identity(self) -> dict[str, Any]:
        return {"name": self.name, "scope": self.scope,
                "files": {item.role: {"file": item.path.name, "crs": item.crs,
                                      "sha256": item.sha256} for item in self.files}}


class SystemInput(EvaluationContract):
    schema_version: Literal["1.0"] = "1.0"
    task_id: StrictStr = Field(pattern=r"^m11b_(dev|test)_[0-9]{3}$")
    query: StrictStr = Field(min_length=1, max_length=2000)


@dataclass
class SystemRun:
    prediction: M11bPrediction
    trace: dict[str, Any] = field(default_factory=dict)


def register_catalogue(catalogue: DatasetCatalogue, registry) -> dict[str, DatasetRef]:
    refs: dict[str, DatasetRef] = {}
    for item in catalogue.files:
        if hashlib.sha256(item.path.read_bytes()).hexdigest() != item.sha256:
            raise ValueError(f"catalogue file hash mismatch: {item.path.name}")
        if item.role in refs:
            raise ValueError(f"duplicate catalogue role: {item.role}")
        refs[item.role] = registry.register_file(
            item.path, kind="vector", crs=item.crs, source=item.source,
            source_version=item.source_version)
    return refs


def _ms(start: float) -> float:
    return round((time.perf_counter() - start) * 1000, 3)


def format_success(task_id: str, operation: str, ref: DatasetRef, registry, *,
                   steps: list[str], tool_sequence: list[str], tool_calls: int,
                   latency_ms: float) -> M11bPrediction:
    """Encode a verified-or-not final DatasetRef in the M11b v2.0 answer schema."""
    records = list(registry.read(ref.id).records)
    ids = [str(item["id"]) for item in records]
    if len(ids) != len(set(ids)):
        raise ValueError("final output has duplicate Feature IDs")
    geometries = [shape(item["geometry"]) for item in records]
    combined = unary_union(geometries) if geometries else GeometryCollection()
    numeric = {"count": float(len(records))}
    if combined.area > 0:
        numeric["area_m2"] = float(combined.area)
    distances = [float(item.get("properties", {}).get("distance_m")) for item in records
                 if item.get("properties", {}).get("distance_m") is not None]
    if distances:
        numeric["distance_m"] = min(distances)
    return M11bPrediction(
        task_id=task_id, status="success", operation=operation,
        exact_fields={"crs": ref.crs, "row_count": len(records)}, steps=steps,
        tool_sequence=tool_sequence, feature_ids=ids, numeric_values=numeric,
        geometry=json.loads(json.dumps(mapping(combined))), crs=ref.crs,
        latency_ms=latency_ms, tool_calls=tool_calls, llm_calls=0, estimated_cost_usd=0.0)


def _declined(task_id: str, status: str, reason: str, operation: Optional[str],
              latency_ms: float) -> M11bPrediction:
    return M11bPrediction(
        task_id=task_id, status=status, reason_code=reason,
        operation=operation if operation == "rank" else None,
        steps=["clarify"] if status == "clarification" else ["reject_unsupported"],
        latency_ms=latency_ms)


@dataclass
class Route:
    """Tool-free routing decision shared by runs and the capability matrix."""

    decision: FrontendDecision
    plan: Optional[PlanningResult] = None
    declined: Optional[tuple[str, str]] = None
    trace: dict[str, Any] = field(default_factory=dict)

    @property
    def executable(self) -> bool:
        return self.declined is None


def _frontend_trace(decision: FrontendDecision) -> dict[str, Any]:
    return {"status": decision.status, "operation": decision.operation,
            "reason_code": decision.reason_code, "command": decision.command}


class _System:
    system_id = "abstract"
    # Optional injected decision source (m11c-protocol-v2): callable(query, refs, catalogue) ->
    # FrontendDecision with identity(). None keeps the rule front-end and the v1 configuration.
    frontend = None

    def config(self) -> dict[str, Any]:
        base = {"system_id": self.system_id, "systems_version": SYSTEMS_VERSION,
                "frontend_version": FRONTEND_VERSION, "model_id": None, "prompt_version": None}
        if self.frontend is not None:
            base.update(self.frontend.identity())
        return base

    def _frontend_decision(self, query, refs, catalogue) -> FrontendDecision:
        if self.frontend is not None:
            return self.frontend(query, refs, catalogue)
        return interpret(query, refs, scope=catalogue.scope)

    def route(self, query: str, refs: dict[str, DatasetRef], catalogue: DatasetCatalogue) -> Route:
        raise NotImplementedError

    def run(self, item: SystemInput, catalogue: DatasetCatalogue) -> SystemRun:
        item = SystemInput.model_validate(item.model_dump())
        started = time.perf_counter()
        trace: dict[str, Any] = {"system_id": self.system_id}
        with tempfile.TemporaryDirectory(prefix="m11c_run_") as temp:
            registry = LocalDatasetRegistry(Path(temp) / "datasets")
            refs = register_catalogue(catalogue, registry)  # integrity failures abort the run
            try:
                stage = time.perf_counter()
                route = self.route(item.query, refs, catalogue)
                trace.update(route.trace)
                trace["routing_ms"] = _ms(stage)
                if not route.executable:
                    status, reason = route.declined
                    return SystemRun(_declined(item.task_id, status, reason, route.decision.operation,
                                               _ms(started)), trace)
                stage = time.perf_counter()
                prediction = self._execute(item, route, refs, registry, trace, started)
                trace["execution_ms"] = _ms(stage)
                return SystemRun(prediction, trace)
            except Exception as error:  # an unexpected system fault is a recorded task failure
                trace["system_exception"] = type(error).__name__
                return SystemRun(M11bPrediction(task_id=item.task_id, status="error",
                                                reason_code="system_exception",
                                                latency_ms=_ms(started)), trace)

    def _execute(self, item: SystemInput, route: Route, refs: dict[str, DatasetRef],
                 registry, trace: dict[str, Any], started: float) -> M11bPrediction:
        raise NotImplementedError


class _AgentSystem(_System):
    """Front-end, then the M07 planner decides plannability before any tool runs."""

    def config(self) -> dict[str, Any]:
        return {**super().config(), "planner_grammar": EXTENDED_GRAMMAR_VERSION}

    def _decide(self, query, refs, catalogue) -> FrontendDecision:
        return self._frontend_decision(query, refs, catalogue)

    def route(self, query, refs, catalogue) -> Route:
        decision = self._decide(query, refs, catalogue)
        trace: dict[str, Any] = {"frontend": _frontend_trace(decision)}
        if decision.status != "request":
            status = "clarification" if decision.status == "clarification" else "error"
            return Route(decision, None, (status, decision.reason_code), trace)
        plan = plan_request(decision.request)
        trace["planner"] = {"status": plan.status, "task_class": plan.task_class,
                            "error_code": plan.error_code,
                            "source_version": plan.plan.source_version if plan.plan else None,
                            "tools": [step.tool for step in plan.tool_steps]}
        if plan.status == "clarification":
            return Route(decision, plan, ("clarification", plan.clarification.reason_code), trace)
        if plan.status != "plan":
            operation = decision.operation or plan.task_class
            reason = (f"unsupported_{operation}" if plan.error_code == "M07_UNSUPPORTED_TASK"
                      else plan.error_code)
            return Route(decision, plan, ("error", reason), trace)
        return Route(decision, plan, None, trace)


class DynamicAgentSystem(_AgentSystem):
    """M07 planning plus direct execution: no result validation, retry or replan."""

    system_id = "dynamic_agent"

    def _execute(self, item, route, refs, registry, trace, started):
        plan = route.plan
        current = {role: refs[role] for role in route.decision.request.datasets}
        executed: list[str] = []
        try:
            for step in plan.tool_steps:
                registration = ANALYSIS_TOOL_REGISTRY.get(step.tool)
                contract = registration.input_model(**_Runner.payload(step, current))
                output = ANALYSIS_TOOL_REGISTRY.invoke(step.tool, contract, datasets=registry)
                executed.append(step.tool)
                current[step.output_role] = output.dataset
            final = current[plan.tool_steps[-1].output_role]
            trace["execution"] = {"status": "completed", "tool_calls": len(executed)}
            return format_success(item.task_id, plan.intent.task_type, final, registry,
                                  steps=["load_fixture", "execute_m04"], tool_sequence=executed,
                                  tool_calls=len(executed), latency_ms=_ms(started))
        except Exception as error:  # report, never mask, execution failures
            code = getattr(error, "code", type(error).__name__)
            trace["execution"] = {"status": "failed", "error_code": code, "tool_calls": len(executed) + 1}
            return M11bPrediction(task_id=item.task_id, status="error", reason_code="execution_failed",
                                  operation=plan.intent.task_type, steps=["load_fixture", "execute_m04"],
                                  tool_sequence=executed, tool_calls=len(executed) + 1,
                                  latency_ms=_ms(started))


class ValidatorReplanSystem(_AgentSystem):
    """M07 planning plus M08 validated execution with one deterministic re-grounded replan."""

    def __init__(self, *, policy: ValidationPolicy = VALIDATION_POLICY, allow_replan: bool = True,
                 system_id: str = "validator_replan"):
        self.policy = policy
        self.allow_replan = allow_replan
        self.system_id = system_id

    def config(self) -> dict[str, Any]:
        return {**super().config(), "validation_policy": self.policy.model_dump(mode="json"),
                "replan": "regrounded_once" if self.allow_replan else None}

    def _execute(self, item, route, refs, registry, trace, started):
        plan = route.plan
        bindings = {role: refs[role] for role in route.decision.request.datasets}

        def replan(request, previous, finding):
            return request, plan_request(request), dict(bindings)

        outcome = execute_validated_plan(
            route.decision.request, plan, bindings, registry, policy=self.policy,
            replan=replan if self.allow_replan else None)
        trace["execution"] = {
            "status": outcome.status, "error_code": outcome.error_code,
            "first_pass_success": outcome.first_pass_success,
            "tool_calls": outcome.tool_call_count, "replan_count": outcome.replan_count,
            "findings": [finding.code for finding in outcome.findings],
            "recoveries": [f"{event.action}:{event.status}" for event in outcome.recoveries]}
        if outcome.status == "success":
            final_pass = outcome.step_records[-len(plan.tool_steps):]
            try:
                return format_success(
                    item.task_id, plan.intent.task_type, outcome.final_dataset, registry,
                    steps=["load_fixture", "execute_m04", "validate_result"],
                    tool_sequence=[record.tool for record in final_pass],
                    tool_calls=outcome.tool_call_count, latency_ms=_ms(started))
            except ValueError:
                trace["execution"]["encoding_error"] = True
                return M11bPrediction(task_id=item.task_id, status="error", reason_code="invalid_output",
                                      operation=plan.intent.task_type, steps=["load_fixture", "execute_m04"],
                                      tool_calls=outcome.tool_call_count, latency_ms=_ms(started))
        return M11bPrediction(
            task_id=item.task_id, status="partial", reason_code=outcome.error_code,
            operation=plan.intent.task_type, steps=["load_fixture", "execute_m04"],
            tool_sequence=[r.tool for r in outcome.step_records if r.status == "succeeded"],
            tool_calls=outcome.tool_call_count, latency_ms=_ms(started))


class RawQueryValidatorSystem(ValidatorReplanSystem):
    """Ablation A1: front-end off; the raw query and whole catalogue go straight to M07+M08."""

    def __init__(self):
        super().__init__(system_id="validator_no_frontend")

    def config(self) -> dict[str, Any]:
        return {**super().config(), "frontend_version": None}

    def _decide(self, query, refs, catalogue) -> FrontendDecision:
        counts = Counter(ref.crs for ref in refs.values())
        crs = sorted(counts.items(), key=lambda item: (-item[1], item[0]))[0][0]
        request = M07PlanRequest(
            query=" ".join(query.split()),
            datasets={role: {"kind": ref.kind, "source": f"{ref.source}:{role}", "crs": ref.crs,
                             "required_fields": sorted(item.name for item in ref.fields)}
                      for role, ref in refs.items()},
            place_crs=crs, analysis_crs=crs, output_crs=crs)
        return FrontendDecision(status="request", command=request.query, request=request,
                                evidence=["frontend:disabled"])


TEMPLATE_OPERATIONS = ("search", "nearest", "within", "spatial_join", "aggregate_by_region")
_FILTER_SLOT = {"search": "role", "nearest": "source", "within": "source",
                "spatial_join": "right", "aggregate_by_region": "values"}


class _TemplateSystem(_System):
    """Fixed tool chains chosen by front-end operation: no planner gate, clarification turn,
    result validation, retry or replan. Declines are reported as errors."""

    templates: tuple[str, ...] = ()

    def config(self) -> dict[str, Any]:
        return {**super().config(), "templates": list(self.templates), "planner_grammar": None,
                "clarification": None, "validation": None, "replan": None}

    def route(self, query, refs, catalogue) -> Route:
        decision = self._frontend_decision(query, refs, catalogue)
        trace: dict[str, Any] = {"frontend": _frontend_trace(decision)}
        if decision.status != "request":
            return Route(decision, None, ("error", decision.reason_code), trace)
        if decision.operation not in self.templates:
            return Route(decision, None, ("error", f"unsupported_{decision.operation}"), trace)
        trace["template"] = decision.operation
        return Route(decision, None, None, trace)

    def _execute(self, item, route, refs, registry, trace, started):
        decision = route.decision
        operation, roles, params = decision.operation, decision.roles, decision.parameters
        succeeded: list[str] = []
        attempted = 0

        def call(name: str, payload: dict[str, Any]) -> DatasetRef:
            nonlocal attempted
            attempted += 1
            registration = ANALYSIS_TOOL_REGISTRY.get(name)
            output = ANALYSIS_TOOL_REGISTRY.invoke(name, registration.input_model(**payload),
                                                   datasets=registry)
            succeeded.append(name)
            return output.dataset

        try:
            filtered = call("filter_attributes", {"dataset": refs[roles[_FILTER_SLOT[operation]]],
                                                  "conditions": decision.conditions, "logic": "all"})
            if operation == "search":
                final = filtered
            elif operation == "nearest":
                final = call("nearest_features", {"dataset": filtered,
                                                  "target_dataset": refs[roles["target"]], "k": params["k"]})
            elif operation == "within":
                final = call("features_within", {"dataset": filtered,
                                                 "container_dataset": refs[roles["container"]]})
            elif operation == "spatial_join":
                final = call("spatial_join", {"dataset": refs[roles["left"]], "right_dataset": filtered,
                                              "predicate": params["predicate"]})
            else:
                final = call("aggregate_by_region", {
                    "dataset": refs[roles["regions"]], "values_dataset": filtered,
                    "region_id_field": params["region_id_field"],
                    "values_region_id_field": params["values_region_id_field"],
                    "metrics": [{"operation": params["metric"], "field": params["metric_field"]}]})
            trace["execution"] = {"status": "completed", "tool_calls": attempted}
            return format_success(item.task_id, operation, final, registry,
                                  steps=["load_fixture", "execute_m04"], tool_sequence=succeeded,
                                  tool_calls=attempted, latency_ms=_ms(started))
        except Exception as error:  # report, never mask, execution failures
            trace["execution"] = {"status": "failed", "tool_calls": attempted,
                                  "error_code": getattr(error, "code", type(error).__name__)}
            return M11bPrediction(task_id=item.task_id, status="error", reason_code="execution_failed",
                                  operation=operation, steps=["load_fixture", "execute_m04"],
                                  tool_sequence=succeeded, tool_calls=attempted, latency_ms=_ms(started))


class FixedWorkflowSystem(_TemplateSystem):
    """FW-A: port of the P4 fixed-workflow architecture onto M04 data (search only)."""

    system_id = "fixed_workflow"
    templates = ("search",)


class TemplateWorkflowSystem(_TemplateSystem):
    """FW-B ablation: five fixed filter->operation chains; not a port of P4."""

    system_id = "template_workflow"
    templates = TEMPLATE_OPERATIONS
