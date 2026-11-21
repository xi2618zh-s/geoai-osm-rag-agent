"""M08 deterministic result validation and bounded recovery for M07 plans."""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Callable, Literal, Optional

from pydantic import ConfigDict, Field, StrictStr, model_validator

from src.agent.dynamic_planner import (
    M07PlanRequest, PlanningResult, PUBLIC_TOOLS, PlanError,
    validate_model_plan_json,
)
from src.contracts import StrictContract
from src.datasets import DatasetRef, DatasetRegistry, DatasetRegistryError
from src.tools import ANALYSIS_TOOL_REGISTRY, AnalysisToolError, AnalysisToolRegistry

MAX_TOOL_CALLS = 24
MAX_REPLANS = 1


class M08Contract(StrictContract):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, strict=True)


class ValidationPolicy(M08Contract):
    schema_version: Literal["1.0"] = "1.0"
    allow_empty: bool
    max_features: int = Field(ge=1, le=10000)
    allow_crs_repair: bool = False
    allow_geometry_repair: bool = False
    alternate_sources: dict[StrictStr, DatasetRef] = Field(default_factory=dict)
    expected_min_count: Optional[int] = Field(default=None, ge=0)
    expected_max_count: Optional[int] = Field(default=None, ge=0)
    required_final_fields: list[StrictStr] = Field(default_factory=list)

    @model_validator(mode="after")
    def check_bounds(self):
        if self.expected_min_count is not None and self.expected_max_count is not None:
            if self.expected_min_count > self.expected_max_count:
                raise ValueError("expected_min_count exceeds expected_max_count")
        return self


class ValidationFinding(M08Contract):
    code: StrictStr
    stage: Literal["plan", "input", "tool", "output", "final", "replan"]
    step_id: Optional[StrictStr] = None
    detail: StrictStr


class RecoveryEvent(M08Contract):
    reason_code: StrictStr
    action: Literal["retry_same_parameters", "repair_crs", "repair_geometry", "alternate_source", "replan_once"]
    status: Literal["succeeded", "failed"]
    step_id: Optional[StrictStr] = None
    input_ids: list[StrictStr] = Field(default_factory=list)
    output_id: Optional[StrictStr] = None


class StepExecution(M08Contract):
    step_id: StrictStr
    tool: StrictStr
    status: Literal["succeeded", "failed"]
    attempts: int = Field(ge=0, le=2)
    input_ids: list[StrictStr]
    output_id: Optional[StrictStr] = None
    error_code: Optional[StrictStr] = None


class ValidationOutcome(M08Contract):
    schema_version: Literal["1.0"] = "1.0"
    status: Literal["success", "partial"]
    first_pass_success: bool
    final_dataset: Optional[DatasetRef] = None
    error_code: Optional[StrictStr] = None
    findings: list[ValidationFinding] = Field(default_factory=list)
    recoveries: list[RecoveryEvent] = Field(default_factory=list)
    step_records: list[StepExecution] = Field(default_factory=list)
    tool_call_count: int = Field(ge=0, le=MAX_TOOL_CALLS)
    replan_count: int = Field(ge=0, le=MAX_REPLANS)
    limitations: list[StrictStr] = Field(default_factory=list)

    @model_validator(mode="after")
    def consistent_status(self):
        if self.status == "success" and (self.final_dataset is None or self.error_code is not None):
            raise ValueError("success needs final dataset and no error")
        if self.status == "partial" and (self.error_code is None or not self.limitations):
            raise ValueError("partial result needs error code and limitation")
        return self


class _Failure(Exception):
    def __init__(self, code: str, stage: str, detail: str, step_id: str | None = None):
        super().__init__(detail)
        self.code, self.stage, self.detail, self.step_id = code, stage, detail, step_id


def _finding(error: _Failure) -> ValidationFinding:
    return ValidationFinding(code=error.code, stage=error.stage, detail=error.detail, step_id=error.step_id)


def _classify_tool(error: Exception, step_id: str) -> _Failure:
    if isinstance(error, TimeoutError) or isinstance(getattr(error, "__cause__", None), TimeoutError):
        return _Failure("M08_TOOL_TIMEOUT", "tool", "Tool timed out", step_id)
    if isinstance(error, AnalysisToolError):
        code = error.code
        if code == "ANALYSIS_TOOL_NOT_REGISTERED":
            return _Failure("M08_TOOL_UNAVAILABLE", "tool", "Declared tool unavailable", step_id)
        if "LIMIT" in code:
            return _Failure("M08_LIMIT_EXCEEDED", "tool", "Tool resource limit exceeded", step_id)
        if "CRS" in code or "GEOGRAPHIC_DISTANCE" in code:
            return _Failure("M08_CRS_MISMATCH", "tool", "Tool rejected CRS", step_id)
        if "GEOMETRY" in code:
            return _Failure("M08_GEOMETRY_INVALID", "tool", "Tool rejected geometry", step_id)
    return _Failure("M08_TOOL_FAILED", "tool", "Deterministic tool failed", step_id)


def step_cache_key(step, inputs: list[DatasetRef]) -> str:
    """Fingerprint a deterministic step, its parameters and exact input versions."""
    payload = {
        "tool": step.tool, "parameters": step.parameters,
        "input_roles": step.input_roles,
        "inputs": [{"id": ref.id, "content_hash": ref.content_hash} for ref in inputs],
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()

class _Runner:
    def __init__(self, request, plan, bindings, datasets, policy, tools, conclusion, replan, cached_outputs):
        self.request = request
        self.plan = plan
        self.base_bindings = dict(bindings)
        self.datasets = datasets
        self.policy = policy
        self.tools = tools
        self.conclusion = conclusion
        self.replan = replan
        self.cached_outputs = dict(cached_outputs)
        self.findings: list[ValidationFinding] = []
        self.recoveries: list[RecoveryEvent] = []
        self.step_records: list[StepExecution] = []
        self.tool_call_count = 0
        self.replan_count = 0
        self.last_valid: DatasetRef | None = None
        self.used_alternates: set[str] = set()
        self.used_crs_repairs: set[str] = set()
        self.used_geometry_repairs: set[str] = set()

    def invoke(self, name: str, payload: Any):
        if self.tool_call_count >= MAX_TOOL_CALLS:
            raise _Failure("M08_BUDGET_EXCEEDED", "tool", "Tool invocation cap reached")
        self.tool_call_count += 1
        return self.tools.invoke(name, payload, datasets=self.datasets)

    def current(self, ref: DatasetRef, stage: str, step_id: str) -> DatasetRef:
        try:
            current = self.datasets.get(ref.id, verify=True)
            if current != ref:
                raise ValueError("stale reference")
            self.datasets.read(ref.id)
            return current
        except (DatasetRegistryError, ValueError) as error:
            code = "M08_DATASET_UNAVAILABLE" if stage == "input" else "M08_OUTPUT_UNREADABLE"
            raise _Failure(code, stage, "DatasetRef is missing, stale, corrupt or unreadable", step_id) from error

    def geometry(self, ref: DatasetRef, stage: str, step_id: str) -> None:
        if ref.kind != "vector":
            raise _Failure("M08_SCHEMA_MISMATCH", stage, "Expected vector DatasetRef", step_id)
        try:
            model = self.tools.get("validate_geometry").input_model
            result = self.invoke("validate_geometry", model(dataset=ref, max_issues=10))
        except (_Failure, Exception) as error:
            if isinstance(error, _Failure):
                raise
            raise _Failure("M08_GEOMETRY_INVALID", stage, "Geometry validation failed", step_id) from error
        if not result.is_valid or result.invalid_geometry_count or result.empty_geometry_count:
            raise _Failure("M08_GEOMETRY_INVALID", stage, "Invalid or empty geometry found", step_id)

    def _repair(self, action: str, ref: DatasetRef, role: str, step_id: str, reason: _Failure) -> DatasetRef:
        tool = "reproject_dataset" if action == "repair_crs" else "repair_geometry"
        params = {"dataset": ref}
        if action == "repair_crs":
            params["target_crs"] = self.request.analysis_crs
        try:
            contract = self.tools.get(tool).input_model(**params)
            output = self.invoke(tool, contract)
            candidate = self.current(output.dataset, "output", step_id)
            if action == "repair_geometry":
                self.geometry(candidate, "output", step_id)
            self.recoveries.append(RecoveryEvent(reason_code=reason.code, action=action, status="succeeded", step_id=step_id, input_ids=[ref.id], output_id=candidate.id))
            return candidate
        except Exception as error:
            self.recoveries.append(RecoveryEvent(reason_code=reason.code, action=action, status="failed", step_id=step_id, input_ids=[ref.id]))
            if isinstance(error, _Failure):
                raise error
            raise _Failure(reason.code, "input", "Declared repair failed", step_id) from error

    def prepare(self, role: str, ref: DatasetRef, step_id: str) -> DatasetRef:
        descriptor = self.request.datasets.get(role)
        if descriptor is None:
            raise _Failure("M08_PLAN_INVALID", "plan", "Role is absent from request", step_id)
        try:
            ref = self.current(ref, "input", step_id)
        except _Failure as problem:
            self.findings.append(_finding(problem))
            alternate = self.policy.alternate_sources.get(role)
            if alternate is None or role in self.used_alternates:
                raise
            self.used_alternates.add(role)
            try:
                ref = self.current(alternate, "input", step_id)
                self.recoveries.append(RecoveryEvent(reason_code=problem.code, action="alternate_source", status="succeeded", step_id=step_id, input_ids=[alternate.id], output_id=alternate.id))
            except _Failure:
                self.recoveries.append(RecoveryEvent(reason_code=problem.code, action="alternate_source", status="failed", step_id=step_id, input_ids=[alternate.id]))
                raise problem
        if ref.kind != descriptor.kind:
            raise _Failure("M08_SCHEMA_MISMATCH", "input", "Bound dataset kind differs from intent", step_id)
        if ref.crs != descriptor.crs:
            problem = _Failure("M08_CRS_MISMATCH", "input", "Bound dataset CRS differs from intent", step_id)
            self.findings.append(_finding(problem))
            if not self.policy.allow_crs_repair or role in self.used_crs_repairs:
                raise problem
            self.used_crs_repairs.add(role)
            ref = self._repair("repair_crs", ref, role, step_id, problem)
        present = {field.name for field in ref.fields}
        if not set(descriptor.required_fields).issubset(present):
            raise _Failure("M08_SCHEMA_MISMATCH", "input", "Required field missing from bound dataset", step_id)
        try:
            self.geometry(ref, "input", step_id)
        except _Failure as problem:
            self.findings.append(_finding(problem))
            if problem.code != "M08_GEOMETRY_INVALID" or not self.policy.allow_geometry_repair or role in self.used_geometry_repairs:
                raise
            self.used_geometry_repairs.add(role)
            ref = self._repair("repair_geometry", ref, role, step_id, problem)
        if ref.row_count > self.policy.max_features:
            raise _Failure("M08_LIMIT_EXCEEDED", "input", "Bound dataset exceeds policy feature cap", step_id)
        return ref

    @staticmethod
    def payload(step, refs):
        inputs = [refs[role] for role in step.input_roles]
        p = step.parameters
        if step.tool == "filter_attributes":
            return {"dataset": inputs[0], "conditions": p["conditions"], "logic": p["logic"]}
        if step.tool == "buffer_features":
            return {"dataset": inputs[0], "distance_m": p["distance_m"]}
        if step.tool == "nearest_features":
            return {"dataset": inputs[0], "target_dataset": inputs[1], "k": p["k"]}
        if step.tool == "features_within":
            return {"dataset": inputs[0], "container_dataset": inputs[1]}
        if step.tool == "spatial_join":
            return {"dataset": inputs[0], "right_dataset": inputs[1], "predicate": p["predicate"]}
        if step.tool == "aggregate_by_region":
            return {"dataset": inputs[0], "values_dataset": inputs[1], **p}
        raise _Failure("M08_TOOL_UNAVAILABLE", "tool", "Tool not admitted for runtime", step.step_id)

    def validate_output(self, ref: DatasetRef, step_id: str) -> DatasetRef:
        ref = self.current(ref, "output", step_id)
        if ref.kind != "vector" or ref.crs != self.request.analysis_crs:
            raise _Failure("M08_CRS_MISMATCH", "output", "Output kind or CRS differs from plan", step_id)
        if ref.row_count > self.policy.max_features:
            raise _Failure("M08_LIMIT_EXCEEDED", "output", "Output exceeds policy feature cap", step_id)
        self.geometry(ref, "output", step_id)
        if ref.row_count == 0 and not self.policy.allow_empty:
            raise _Failure("M08_EMPTY_UNEXPECTED", "output", "Empty output requires explicit allow_empty", step_id)
        return ref

    def validate_final(self, ref: DatasetRef) -> None:
        if self.policy.expected_min_count is not None and ref.row_count < self.policy.expected_min_count:
            raise _Failure("M08_CONSTRAINT_UNMET", "final", "Final count below explicit minimum")
        if self.policy.expected_max_count is not None and ref.row_count > self.policy.expected_max_count:
            raise _Failure("M08_CONSTRAINT_UNMET", "final", "Final count above explicit maximum")
        if not set(self.policy.required_final_fields).issubset({item.name for item in ref.fields}):
            raise _Failure("M08_SCHEMA_MISMATCH", "final", "Final output misses required fields")
        if self.conclusion is not None:
            match = re.fullmatch(r"Found ([0-9]+) features\.", self.conclusion)
            if match is None:
                raise _Failure("M08_CONCLUSION_UNVERIFIABLE", "final", "Conclusion is outside the count evidence template")
            if int(match.group(1)) != ref.row_count:
                raise _Failure("M08_CONCLUSION_MISMATCH", "final", "Conclusion count differs from verified result")

    def run_once(self) -> DatasetRef:
        refs = dict(self.base_bindings)
        for step in self.plan.tool_steps:
            prepared = {}
            for role in step.input_roles:
                if role not in refs:
                    raise _Failure("M08_PLAN_INVALID", "plan", "Unavailable role in step", step.step_id)
                prepared[role] = self.prepare(role, refs[role], step.step_id) if role in self.request.datasets else self.validate_output(refs[role], step.step_id)
                refs[role] = prepared[role]
            input_ids = [prepared[role].id for role in step.input_roles]
            if step.tool not in PUBLIC_TOOLS:
                raise _Failure("M08_TOOL_UNAVAILABLE", "tool", "Tool is not agent-public", step.step_id)
            try:
                registration = self.tools.get(step.tool)
                contract = registration.input_model(**self.payload(step, refs))
            except Exception as error:
                raise _Failure("M08_TOOL_UNAVAILABLE", "tool", "Tool unavailable or input contract invalid", step.step_id) from error
            cache_key = step_cache_key(step, [prepared[role] for role in step.input_roles])
            cached = self.cached_outputs.get(cache_key)
            if cached is not None:
                try:
                    candidate = self.validate_output(cached, step.step_id)
                except _Failure:
                    pass
                else:
                    refs[step.output_role] = candidate
                    self.last_valid = candidate
                    self.step_records.append(StepExecution(step_id=step.step_id, tool=step.tool, status="succeeded", attempts=0, input_ids=input_ids, output_id=candidate.id))
                    continue
            attempts = 0
            retry_reason = None
            while True:
                attempts += 1
                try:
                    output = self.invoke(step.tool, contract)
                    break
                except _Failure:
                    raise
                except Exception as error:
                    problem = _classify_tool(error, step.step_id)
                    self.findings.append(_finding(problem))
                    if problem.code == "M08_TOOL_TIMEOUT" and attempts == 1:
                        retry_reason = problem
                        continue
                    if retry_reason is not None:
                        self.recoveries.append(RecoveryEvent(reason_code=retry_reason.code, action="retry_same_parameters", status="failed", step_id=step.step_id, input_ids=input_ids))
                    self.step_records.append(StepExecution(step_id=step.step_id, tool=step.tool, status="failed", attempts=attempts, input_ids=input_ids, error_code=problem.code))
                    raise problem
            candidate = getattr(output, "dataset", None)
            if not isinstance(candidate, DatasetRef):
                problem = _Failure("M08_OUTPUT_UNREADABLE", "output", "Tool did not return a DatasetRef", step.step_id)
                if retry_reason is not None:
                    self.recoveries.append(RecoveryEvent(reason_code=retry_reason.code, action="retry_same_parameters", status="failed", step_id=step.step_id, input_ids=input_ids))
                self.step_records.append(StepExecution(step_id=step.step_id, tool=step.tool, status="failed", attempts=attempts, input_ids=input_ids, error_code=problem.code))
                raise problem
            try:
                candidate = self.validate_output(candidate, step.step_id)
            except _Failure as problem:
                self.findings.append(_finding(problem))
                if problem.code == "M08_GEOMETRY_INVALID" and self.policy.allow_geometry_repair and step.output_role not in self.used_geometry_repairs:
                    self.used_geometry_repairs.add(step.output_role)
                    try:
                        candidate = self._repair("repair_geometry", candidate, step.output_role, step.step_id, problem)
                        candidate = self.validate_output(candidate, step.step_id)
                    except _Failure:
                        if retry_reason is not None:
                            self.recoveries.append(RecoveryEvent(reason_code=retry_reason.code, action="retry_same_parameters", status="failed", step_id=step.step_id, input_ids=input_ids))
                        raise
                else:
                    if retry_reason is not None:
                        self.recoveries.append(RecoveryEvent(reason_code=retry_reason.code, action="retry_same_parameters", status="failed", step_id=step.step_id, input_ids=input_ids))
                    self.step_records.append(StepExecution(step_id=step.step_id, tool=step.tool, status="failed", attempts=attempts, input_ids=input_ids, error_code=problem.code))
                    raise
            if retry_reason is not None:
                self.recoveries.append(RecoveryEvent(reason_code=retry_reason.code, action="retry_same_parameters", status="succeeded", step_id=step.step_id, input_ids=input_ids, output_id=candidate.id))
            refs[step.output_role] = candidate
            self.last_valid = candidate
            self.step_records.append(StepExecution(step_id=step.step_id, tool=step.tool, status="succeeded", attempts=attempts, input_ids=input_ids, output_id=candidate.id))
        final = refs[self.plan.tool_steps[-1].output_role]
        self.validate_final(final)
        return final

    def outcome(self, status: str, code: str | None = None) -> ValidationOutcome:
        return ValidationOutcome(
            status=status, first_pass_success=status == "success" and not self.recoveries and not self.findings,
            final_dataset=self.last_valid, error_code=code,
            findings=self.findings, recoveries=self.recoveries, step_records=self.step_records,
            tool_call_count=self.tool_call_count, replan_count=self.replan_count,
            limitations=[f"Validation stopped: {code}"] if code else [],
        )

    def execute(self) -> ValidationOutcome:
        try:
            validate_model_plan_json(self.plan.model_dump_json(), self.request)
        except (PlanError, ValueError) as error:
            problem = _Failure("M08_PLAN_INVALID", "plan", "Plan did not pass M07 static validation")
            self.findings.append(_finding(problem))
            return self.outcome("partial", problem.code)
        while True:
            try:
                final = self.run_once()
                self.last_valid = final
                return self.outcome("success")
            except _Failure as problem:
                if not self.findings or self.findings[-1].code != problem.code:
                    self.findings.append(_finding(problem))
                if problem.step_id and not (self.step_records and self.step_records[-1].step_id == problem.step_id and self.step_records[-1].status == "failed"):
                    step = next((item for item in self.plan.tool_steps if item.step_id == problem.step_id), None)
                    if step is not None:
                        input_ids = [self.base_bindings[role].id for role in step.input_roles if role in self.base_bindings]
                        self.step_records.append(StepExecution(step_id=step.step_id, tool=step.tool, status="failed", attempts=0, input_ids=input_ids, error_code=problem.code))
                if self.replan is None or self.replan_count >= MAX_REPLANS:
                    return self.outcome("partial", problem.code)
                self.replan_count += 1
                try:
                    proposal = self.replan(self.request, self.plan, self.findings[-1])
                    if not isinstance(proposal, tuple) or len(proposal) != 3:
                        raise ValueError("replan must return request, plan and bindings")
                    next_request, next_plan, next_bindings = proposal
                    next_request = next_request if isinstance(next_request, M07PlanRequest) else M07PlanRequest.model_validate(next_request)
                    if not isinstance(next_plan, PlanningResult) or next_request != self.request:
                        raise ValueError("replan changed original task")
                    validate_model_plan_json(next_plan.model_dump_json(), next_request)
                    if next_plan.business_parameters != self.plan.business_parameters:
                        raise ValueError("replan changed business parameters")
                    self.request, self.plan, self.base_bindings = next_request, next_plan, dict(next_bindings)
                    self.recoveries.append(RecoveryEvent(reason_code=problem.code, action="replan_once", status="succeeded", input_ids=[r.id for r in self.base_bindings.values()]))
                except Exception:
                    failure = _Failure("M08_REPLAN_INVALID", "replan", "Replan proposal failed static validation")
                    self.findings.append(_finding(failure))
                    self.recoveries.append(RecoveryEvent(reason_code=problem.code, action="replan_once", status="failed"))
                    return self.outcome("partial", failure.code)


def execute_validated_plan(
    request: M07PlanRequest | dict[str, Any],
    plan: PlanningResult,
    bindings: dict[str, DatasetRef],
    datasets: DatasetRegistry,
    *,
    policy: ValidationPolicy,
    tools: AnalysisToolRegistry = ANALYSIS_TOOL_REGISTRY,
    replan: Optional[Callable[[M07PlanRequest, PlanningResult, ValidationFinding], tuple[Any, Any, Any]]] = None,
    conclusion: Optional[str] = None,
    cached_outputs: Optional[dict[str, DatasetRef]] = None,
) -> ValidationOutcome:
    """Execute only a statically valid M07 plan, validate every result and bound recovery."""
    parsed = request if isinstance(request, M07PlanRequest) else M07PlanRequest.model_validate(request)
    return _Runner(parsed, plan, bindings, datasets, policy, tools, conclusion, replan, cached_outputs or {}).execute()
