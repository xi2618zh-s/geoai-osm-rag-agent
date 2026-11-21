"""Bounded execution of typed scenario plans with M08-style validation (ADR-0021)."""
from __future__ import annotations

from typing import Any, Callable, Optional

from pydantic import ValidationError

from src.agent.validation import RecoveryEvent, ValidationFinding, ValidationPolicy
from src.datasets import DatasetRef, DatasetRegistry, DatasetRegistryError
from src.tools import AnalysisToolError

from .bindings import BindingFailure, check_binding
from .contracts import MAX_TOOL_CALLS, ScenarioCatalogue, ScenarioError, ScenarioOutcome, ScenarioPlan, StepRecord
from .gate import tool_payload, validate_plan
from .registry import SCENARIO_TOOL_REGISTRY

Replanner = Callable[[ScenarioCatalogue], ScenarioPlan]
RECOVERABLE_BINDING_CODES = frozenset({"SCENARIO_CRS_IMPLAUSIBLE", "SCENARIO_DATASET_UNAVAILABLE"})


class _Failure(Exception):
    def __init__(self, code: str, stage: str, detail: str, step_id: Optional[str] = None):
        super().__init__(detail)
        self.code, self.stage, self.detail, self.step_id = code, stage, detail, step_id


def _is_timeout(error: BaseException) -> bool:
    return isinstance(error, TimeoutError) or isinstance(getattr(error, "__cause__", None), TimeoutError)


class _Runner:
    def __init__(self, plan, catalogue, registry, policy, replanner, tools, max_tool_calls):
        self.plan, self.catalogue, self.registry = plan, catalogue, registry
        self.policy, self.replanner, self.tools = policy, replanner, tools
        self.max_tool_calls = max_tool_calls
        self.initial_plan_tools = [step.tool for step in plan.steps]
        self.findings: list[ValidationFinding] = []
        self.recoveries: list[RecoveryEvent] = []
        self.step_records: list[StepRecord] = []
        self.tool_calls: list[str] = []
        self.bindings: dict[str, DatasetRef] = {}
        self.outputs: dict[str, DatasetRef] = {}
        self.replan_count = 0

    # ------------------------------------------------------------------ helpers
    def _note(self, failure: _Failure) -> None:
        self.findings.append(ValidationFinding(code=failure.code, stage=failure.stage,
                                               step_id=failure.step_id, detail=failure.detail))

    def _invoke(self, name: str, payload: Any, step_id: str):
        if len(self.tool_calls) >= self.max_tool_calls:
            raise _Failure("SCENARIO_BUDGET_EXCEEDED", "tool", "tool-call cap reached", step_id)
        self.tool_calls.append(name)
        return self.tools.invoke(name, payload, datasets=self.registry)

    def _outcome(self, status: str, failure: Optional[_Failure] = None) -> ScenarioOutcome:
        final = None
        if status == "success":
            final = self.outputs[self.plan.steps[-1].output_role]
        return ScenarioOutcome(
            status=status, first_pass_success=status == "success" and not self.recoveries and not self.findings,
            plan=self.plan, initial_plan_tools=self.initial_plan_tools, bindings=self.bindings,
            outputs=self.outputs, final_dataset=final,
            error_code=failure.code if failure else None, failed_step=failure.step_id if failure else None,
            findings=self.findings, recoveries=self.recoveries, step_records=self.step_records,
            tool_calls=self.tool_calls, replan_count=self.replan_count,
            limitations=[f"Scenario stopped: {failure.code} ({failure.detail})"] if failure else [],
        )

    # ------------------------------------------------------------------ binding
    def _bind(self) -> dict[str, DatasetRef]:
        bindings: dict[str, DatasetRef] = {}
        for step in self.plan.steps:
            for role in step.input_roles:
                if role in self.catalogue.entries and role not in bindings:
                    bindings[role] = check_binding(self.catalogue.entries[role], self.registry, self.catalogue)
        return bindings

    def _recover_binding(self, failure: BindingFailure) -> bool:
        alternate = self.policy.alternate_sources.get(failure.role)
        if (failure.code not in RECOVERABLE_BINDING_CODES or alternate is None
                or self.replanner is None or self.replan_count >= 1):
            return False
        entry = self.catalogue.entries[failure.role].model_copy(update={"dataset": alternate, "declared_crs": alternate.crs})
        try:
            check_binding(entry, self.registry, self.catalogue)
        except BindingFailure:
            self.recoveries.append(RecoveryEvent(reason_code=failure.code, action="alternate_source", status="failed",
                                                 input_ids=[alternate.id]))
            return False
        self.recoveries.append(RecoveryEvent(reason_code=failure.code, action="alternate_source", status="succeeded",
                                             input_ids=[alternate.id], output_id=alternate.id))
        catalogue = self.catalogue.with_entry(failure.role, dataset=alternate, declared_crs=alternate.crs)
        try:
            plan = self.replanner(catalogue)
            validate_plan(plan, catalogue, tools=self.tools)
        except Exception as error:
            self.recoveries.append(RecoveryEvent(reason_code=failure.code, action="replan_once", status="failed"))
            raise _Failure("SCENARIO_REPLAN_INVALID", "replan", "replanned scenario failed the static gate") from error
        self.recoveries.append(RecoveryEvent(reason_code=failure.code, action="replan_once", status="succeeded",
                                             input_ids=[alternate.id]))
        self.catalogue, self.plan, self.replan_count = catalogue, plan, 1
        return True

    # ------------------------------------------------------------------ steps
    def _validate_output(self, ref: DatasetRef, step) -> DatasetRef:
        try:
            current = self.registry.get(ref.id, verify=True)
            if current != ref:
                raise ValueError("stale output")
            self.registry.read(ref.id)
        except (DatasetRegistryError, ValueError) as error:
            raise _Failure("SCENARIO_OUTPUT_UNREADABLE", "output", "tool output is unreadable", step.step_id) from error
        if ref.kind != "vector" or ref.crs != self.catalogue.analysis_crs:
            raise _Failure("SCENARIO_CRS_MISMATCH", "output", "output kind or CRS differs from the analysis CRS", step.step_id)
        if ref.row_count > self.policy.max_features:
            raise _Failure("SCENARIO_LIMIT_EXCEEDED", "output", "output exceeds the policy feature cap", step.step_id)
        check = self.tools.get("validate_geometry").input_model(dataset=ref, max_issues=10)
        result = self._invoke("validate_geometry", check, step.step_id)
        if not result.is_valid or result.invalid_geometry_count or result.empty_geometry_count:
            raise _Failure("SCENARIO_GEOMETRY_INVALID", "output", "output geometry is invalid", step.step_id)
        if ref.row_count == 0 and step.empty_policy == "reject":
            raise _Failure("SCENARIO_EMPTY_UNEXPECTED", "output", "empty output is not allowed at this step", step.step_id)
        return ref

    def _run_step(self, step, refs: dict[str, DatasetRef]) -> None:
        input_ids = [refs[role].id for role in step.input_roles]
        attempts = 0
        try:
            contract = self.tools.get(step.tool).input_model(**tool_payload(step, refs))
        except (ValidationError, TypeError, ValueError, AnalysisToolError) as error:
            failure = _Failure("SCENARIO_TOOL_FAILED", "tool", "tool input contract rejected", step.step_id)
            self.step_records.append(StepRecord(step_id=step.step_id, tool=step.tool, status="failed", attempts=0,
                                                input_ids=input_ids, error_code=failure.code))
            raise failure from error
        output = None
        try:
            while output is None:
                attempts += 1
                try:
                    output = self._invoke(step.tool, contract, step.step_id)
                except _Failure:
                    raise
                except Exception as error:
                    if _is_timeout(error):
                        failure = _Failure("SCENARIO_TOOL_TIMEOUT", "tool", "tool timed out", step.step_id)
                        self._note(failure)
                        if attempts == 1:
                            continue
                        raise failure from error
                    code = error.code if isinstance(error, AnalysisToolError) else error.__class__.__name__
                    raise _Failure("SCENARIO_TOOL_FAILED", "tool", f"tool failed: {code}", step.step_id) from error
            candidate = self._validate_output(output.dataset, step)
        except _Failure as failure:
            self.step_records.append(StepRecord(step_id=step.step_id, tool=step.tool, status="failed",
                                                attempts=min(attempts, 2), input_ids=input_ids, error_code=failure.code))
            raise
        if attempts > 1:
            self.recoveries.append(RecoveryEvent(reason_code="SCENARIO_TOOL_TIMEOUT", action="retry_same_parameters",
                                                 status="succeeded", step_id=step.step_id, input_ids=input_ids,
                                                 output_id=candidate.id))
        warnings = list(getattr(getattr(output, "metadata", None), "warnings", []) or [])
        self.step_records.append(StepRecord(step_id=step.step_id, tool=step.tool, status="succeeded", attempts=attempts,
                                            input_ids=input_ids, output_id=candidate.id, warnings=warnings))
        refs[step.output_role] = candidate
        self.outputs[step.output_role] = candidate

    def _final_checks(self, final: DatasetRef) -> None:
        if final.row_count == 0 and not self.policy.allow_empty:
            raise _Failure("SCENARIO_EMPTY_UNEXPECTED", "final", "empty final result requires allow_empty")
        if self.policy.expected_min_count is not None and final.row_count < self.policy.expected_min_count:
            raise _Failure("SCENARIO_CONSTRAINT_UNMET", "final", "final count below minimum")
        if self.policy.expected_max_count is not None and final.row_count > self.policy.expected_max_count:
            raise _Failure("SCENARIO_CONSTRAINT_UNMET", "final", "final count above maximum")
        if not set(self.policy.required_final_fields).issubset({field.name for field in final.fields}):
            raise _Failure("SCENARIO_SCHEMA_MISMATCH", "final", "final output misses required fields")

    def execute(self) -> ScenarioOutcome:
        if self.policy.allow_crs_repair or self.policy.allow_geometry_repair:
            failure = _Failure("SCENARIO_POLICY_INVALID", "plan", "scenario execution never repairs CRS or geometry")
            self._note(failure)
            return self._outcome("partial", failure)
        try:
            while True:
                try:
                    validate_plan(self.plan, self.catalogue, tools=self.tools)
                except ScenarioError as error:
                    raise _Failure("SCENARIO_PLAN_INVALID", "plan", str(error)) from error
                try:
                    self.bindings = self._bind()
                    break
                except BindingFailure as problem:
                    failure = _Failure(problem.code, "input", f"{problem.role}: {problem.detail}")
                    self._note(failure)
                    if not self._recover_binding(problem):
                        return self._outcome("partial", failure)
            refs = dict(self.bindings)
            for step in self.plan.steps:
                self._run_step(step, refs)
            self._final_checks(refs[self.plan.steps[-1].output_role])
        except _Failure as failure:
            if not self.findings or self.findings[-1].code != failure.code:
                self._note(failure)
            return self._outcome("partial", failure)
        return self._outcome("success")


def execute_scenario_plan(
    plan: ScenarioPlan, catalogue: ScenarioCatalogue, registry: DatasetRegistry, *,
    policy: ValidationPolicy, replanner: Optional[Replanner] = None,
    tools=SCENARIO_TOOL_REGISTRY, max_tool_calls: int = MAX_TOOL_CALLS,
) -> ScenarioOutcome:
    """Execute only a statically valid plan; reject implausible bindings before any tool call."""
    if type(max_tool_calls) is not int or not 1 <= max_tool_calls <= MAX_TOOL_CALLS:
        raise ValueError(f"max_tool_calls must be 1..{MAX_TOOL_CALLS}")
    return _Runner(plan, catalogue, registry, policy, replanner, tools, max_tool_calls).execute()
