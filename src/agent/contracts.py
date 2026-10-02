"""Strict plan, step, request, and response contracts for P4 workflows."""

from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import Field, StrictStr, field_validator, model_validator

from src.contracts import OSMTag, StrictContract


WorkflowOperation = Literal["union", "intersection"]
WorkflowSource = Literal["explicit", "deterministic_compiler"]


class WorkflowIntent(StrictContract):
    place: StrictStr = Field(min_length=1, max_length=300)
    operation: WorkflowOperation
    filters: list[OSMTag] = Field(min_length=2, max_length=5)
    source: WorkflowSource

    @field_validator("place")
    @classmethod
    def reject_blank_place(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("place must not be blank")
        return value.strip()

    @model_validator(mode="after")
    def require_unique_filters(self):
        tags = [(item.key, item.value) for item in self.filters]
        if len(tags) != len(set(tags)):
            raise ValueError("workflow filters must be unique")
        return self


class FilterDecision(StrictContract):
    clause: StrictStr = Field(min_length=1, max_length=1000)
    selected_tag: OSMTag
    score: float
    retrieval_method: StrictStr
    candidates: list[dict[str, Any]] = Field(default_factory=list)


class WorkflowStep(StrictContract):
    step_id: StrictStr = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    tool: StrictStr = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    depends_on: list[StrictStr] = Field(default_factory=list)
    arguments: dict[str, Any] = Field(default_factory=dict)
    recovery: Literal["none", "retry_once"] = "none"


class WorkflowPlan(StrictContract):
    version: Literal["1.0"] = "1.0"
    query: StrictStr = Field(min_length=1, max_length=2000)
    intent: WorkflowIntent
    decisions: list[FilterDecision] = Field(default_factory=list)
    steps: list[WorkflowStep] = Field(min_length=1, max_length=16)

    @model_validator(mode="after")
    def validate_step_graph(self):
        seen: set[str] = set()
        for step in self.steps:
            if step.step_id in seen:
                raise ValueError(f"duplicate workflow step: {step.step_id}")
            missing = [item for item in step.depends_on if item not in seen]
            if missing:
                raise ValueError(
                    f"step {step.step_id} depends on unknown or future steps: {missing}"
                )
            seen.add(step.step_id)
        return self


class StepRecord(StrictContract):
    step_id: StrictStr
    tool: StrictStr
    status: Literal["pending", "running", "succeeded", "failed", "skipped"]
    attempts: int = Field(default=0, ge=0)
    recovered: bool = False
    elapsed_ms: float = Field(default=0.0, ge=0.0)
    output_summary: dict[str, Any] = Field(default_factory=dict)
    error_code: Optional[StrictStr] = None
    error: Optional[StrictStr] = None


class WorkflowRequest(StrictContract):
    mode: Literal["natural_language", "explicit_plan"] = "natural_language"
    query: StrictStr = Field(min_length=1, max_length=2000)
    place: Optional[StrictStr] = Field(default=None, min_length=1, max_length=300)
    operation: Optional[WorkflowOperation] = None
    filters: Optional[list[OSMTag]] = Field(default=None, min_length=2, max_length=5)

    @field_validator("query")
    @classmethod
    def reject_blank_query(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("query must not be blank")
        return value.strip()

    @model_validator(mode="after")
    def validate_mode_fields(self):
        supplied = self.place is not None or self.operation is not None or self.filters is not None
        if self.mode == "natural_language" and supplied:
            raise ValueError(
                "natural_language mode must not include place, operation, or filters"
            )
        if self.mode == "explicit_plan":
            if self.place is None or self.operation is None or self.filters is None:
                raise ValueError(
                    "explicit_plan mode requires place, operation, and filters"
                )
        return self


class WorkflowSuccessResponse(StrictContract):
    status: Literal["success"] = "success"
    workflow_id: StrictStr
    trace_id: StrictStr
    query: StrictStr
    place: StrictStr
    operation: WorkflowOperation
    chosen_tags: list[StrictStr]
    count: int = Field(ge=0)
    counts_by_type: dict[str, int]
    matched_counts_by_tag: dict[str, int]
    geojson_url: StrictStr
    plan: dict[str, Any]
    step_records: list[dict[str, Any]]
    recovery_count: int = Field(ge=0)
    clip_cache_hit: bool
    timings_ms: dict[str, float]


class WorkflowErrorResponse(StrictContract):
    status: Literal["error"] = "error"
    error_code: StrictStr
    message: StrictStr
    workflow_id: StrictStr
    trace_id: StrictStr
    query: StrictStr
    plan: Optional[dict[str, Any]] = None
    step_records: list[dict[str, Any]] = Field(default_factory=list)
    timings_ms: dict[str, float] = Field(default_factory=dict)
