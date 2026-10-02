"""Explicit workflow state machine for constrained multi-tag OSM execution."""

from __future__ import annotations

from pathlib import Path
import tempfile
import time
from typing import Any
import uuid

from src.agent.contracts import StepRecord, WorkflowIntent, WorkflowPlan
from src.agent.planner import (
    PlanCompilationError,
    build_workflow_plan,
    compile_natural_language_plan,
)
from src.agent.tools import DEFAULT_TOOL_REGISTRY, ToolRegistry, ToolRegistryError
from src.config import OUTPUT_DIR
from src.observability import QueryTrace, log_event


def _resolve_reference(reference: str, context: dict[str, Any]) -> Any:
    current: Any = context
    for part in reference[1:].split("."):
        if not isinstance(current, dict) or part not in current:
            raise ToolRegistryError(
                "PLAN_REFERENCE_INVALID", f"Unresolved workflow reference: {reference}"
            )
        current = current[part]
    return current


def _resolve(value: Any, context: dict[str, Any]) -> Any:
    if isinstance(value, str) and value.startswith("$"):
        return _resolve_reference(value, context)
    if isinstance(value, list):
        return [_resolve(item, context) for item in value]
    if isinstance(value, dict):
        return {key: _resolve(item, context) for key, item in value.items()}
    return value


class WorkflowEngine:
    def __init__(self, registry: ToolRegistry | None = None):
        self.registry = registry or DEFAULT_TOOL_REGISTRY

    def execute(
        self,
        plan: WorkflowPlan,
        *,
        trace_id: str | None = None,
        trace: QueryTrace | None = None,
    ) -> dict[str, Any]:
        if trace is not None and trace_id is not None and trace.trace_id != trace_id:
            raise ValueError("trace and trace_id must refer to the same request")
        trace = trace or QueryTrace(trace_id)
        workflow_id = uuid.uuid4().hex
        records: list[StepRecord] = []
        statuses: dict[str, str] = {}
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        log_event(
            "workflow_started",
            trace_id=trace.trace_id,
            workflow_id=workflow_id,
            operation=plan.intent.operation,
            filter_count=len(plan.intent.filters),
            source=plan.intent.source,
        )

        with tempfile.TemporaryDirectory(prefix="geoai_workflow_", dir=OUTPUT_DIR) as directory:
            context: dict[str, Any] = {
                "trace_id": trace.trace_id,
                "workflow_id": workflow_id,
                "temp_dir": str(Path(directory)),
                "steps": {},
            }
            failed = False
            failure_code = None
            failure_message = None
            for step in plan.steps:
                unsatisfied = [
                    item for item in step.depends_on if statuses.get(item) != "succeeded"
                ]
                if failed or unsatisfied:
                    record = StepRecord(
                        step_id=step.step_id,
                        tool=step.tool,
                        status="skipped",
                        error_code="DEPENDENCY_NOT_SATISFIED",
                        error=(
                            "Skipped after an earlier failure"
                            if failed
                            else f"Unsatisfied dependencies: {unsatisfied}"
                        ),
                    )
                    records.append(record)
                    statuses[step.step_id] = "skipped"
                    continue

                record = StepRecord(
                    step_id=step.step_id,
                    tool=step.tool,
                    status="running",
                )
                started = time.perf_counter()
                try:
                    arguments = _resolve(step.arguments, context)
                    with trace.stage(f"tool_{step.step_id}"):
                        payload, attempts, recovered = self.registry.invoke(
                            step.tool,
                            arguments,
                            allow_retry=step.recovery == "retry_once",
                            trace_id=trace.trace_id,
                            step_id=step.step_id,
                        )
                    context["steps"][step.step_id] = payload.values
                    record.status = "succeeded"
                    record.attempts = attempts
                    record.recovered = recovered
                    record.output_summary = payload.summary
                    statuses[step.step_id] = "succeeded"
                    log_event(
                        "workflow_step_completed",
                        trace_id=trace.trace_id,
                        workflow_id=workflow_id,
                        step_id=step.step_id,
                        tool=step.tool,
                        attempts=attempts,
                        recovered=recovered,
                    )
                except ToolRegistryError as error:
                    record.status = "failed"
                    record.attempts = max(error.attempts, 1)
                    record.error_code = error.code
                    record.error = str(error)
                    statuses[step.step_id] = "failed"
                    failed = True
                    failure_code = error.code
                    failure_message = str(error)
                    log_event(
                        "workflow_step_failed",
                        trace_id=trace.trace_id,
                        workflow_id=workflow_id,
                        step_id=step.step_id,
                        tool=step.tool,
                        error_code=error.code,
                    )
                finally:
                    record.elapsed_ms = round(
                        (time.perf_counter() - started) * 1000, 2
                    )
                    records.append(record)

        timings = trace.snapshot()
        serialized_records = [item.model_dump(exclude_none=True) for item in records]
        if failed:
            log_event(
                "workflow_failed",
                trace_id=trace.trace_id,
                workflow_id=workflow_id,
                error_code=failure_code,
                total_ms=timings["total"],
            )
            return {
                "success": False,
                "workflow_id": workflow_id,
                "trace_id": trace.trace_id,
                "query": plan.query,
                "error_code": failure_code or "WORKFLOW_FAILED",
                "error": failure_message or "Workflow failed",
                "plan": plan.model_dump(),
                "step_records": serialized_records,
                "timings_ms": timings,
            }

        write_result = context["steps"]["write_geojson"]
        combine_result = context["steps"]["combine_features"]
        clip_result = context["steps"]["clip_city"]
        recovery_count = sum(1 for item in records if item.recovered)
        result = {
            "success": True,
            "workflow_id": workflow_id,
            "trace_id": trace.trace_id,
            "query": plan.query,
            "place": plan.intent.place,
            "operation": plan.intent.operation,
            "chosen_tags": [
                f"{item.key}={item.value}" for item in plan.intent.filters
            ],
            "count": write_result["count"],
            "counts_by_type": write_result["counts_by_type"],
            "matched_counts_by_tag": combine_result["counts_by_filter"],
            "geojson_path": write_result["geojson_path"],
            "geojson_filename": write_result["geojson_filename"],
            "clip_cache_hit": clip_result["clip_cache_hit"],
            "plan": plan.model_dump(),
            "step_records": serialized_records,
            "recovery_count": recovery_count,
            "decision_source": plan.intent.source,
            "timings_ms": timings,
        }
        log_event(
            "workflow_completed",
            trace_id=trace.trace_id,
            workflow_id=workflow_id,
            operation=plan.intent.operation,
            count=result["count"],
            recovery_count=recovery_count,
            total_ms=timings["total"],
        )
        return result


def run_agent_workflow(
    query: str,
    *,
    place: str | None = None,
    operation: str | None = None,
    filters: list[Any] | None = None,
    trace_id: str | None = None,
    registry: ToolRegistry | None = None,
    retriever=None,
) -> dict[str, Any]:
    trace = QueryTrace(trace_id)
    query = str(query).strip()
    try:
        with trace.stage("plan_compilation"):
            if place is None and operation is None and filters is None:
                plan = compile_natural_language_plan(query, retriever=retriever)
            else:
                intent = WorkflowIntent(
                    place=place,
                    operation=operation,
                    filters=filters,
                    source="explicit",
                )
                plan = build_workflow_plan(query, intent)
    except PlanCompilationError as error:
        timings = trace.snapshot()
        return {
            "success": False,
            "workflow_id": uuid.uuid4().hex,
            "trace_id": trace.trace_id,
            "query": query,
            "error_code": error.code,
            "error": str(error),
            "plan": None,
            "step_records": [],
            "timings_ms": timings,
        }
    except (TypeError, ValueError) as error:
        timings = trace.snapshot()
        return {
            "success": False,
            "workflow_id": uuid.uuid4().hex,
            "trace_id": trace.trace_id,
            "query": query,
            "error_code": "PLAN_SCHEMA_INVALID",
            "error": str(error),
            "plan": None,
            "step_records": [],
            "timings_ms": timings,
        }
    return WorkflowEngine(registry).execute(plan, trace=trace)


def available_workflow_tools() -> list[dict[str, Any]]:
    return DEFAULT_TOOL_REGISTRY.describe()
