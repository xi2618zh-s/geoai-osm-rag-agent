"""Versioned contracts for deterministic GeoAI benchmark evaluation."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Optional, TypeAlias, Union

from pydantic import ConfigDict, Field, StrictBool, StrictFloat, StrictInt, StrictStr, field_validator, model_validator

from src.contracts import StrictContract
from src.geodsl.contracts import GeoOperation


JsonScalar: TypeAlias = Union[StrictStr, StrictInt, StrictFloat, StrictBool]
BenchmarkOutcome: TypeAlias = Literal["success", "clarification", "error"]
FailureCategory: TypeAlias = Literal[
    "outcome_mismatch",
    "operation_mismatch",
    "exact_field_mismatch",
    "required_step_mismatch",
    "feature_set_mismatch",
    "numeric_tolerance_exceeded",
    "geometry_mismatch",
    "ranking_mismatch",
]
ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_.:-]{2,127}$"
HASH_PATTERN = r"^sha256:[a-f0-9]{64}$"


class EvaluationContract(StrictContract):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, strict=True)


class NumericExpectation(EvaluationContract):
    value: float
    absolute_tolerance: float = Field(default=0.0, ge=0.0)
    relative_tolerance: float = Field(default=0.0, ge=0.0, le=1.0)


class GeometryExpectation(EvaluationContract):
    bbox: list[float] = Field(min_length=4, max_length=4)
    min_iou: float = Field(ge=0.0, le=1.0)

    @field_validator("bbox")
    @classmethod
    def validate_bbox(cls, value: list[float]) -> list[float]:
        if not (value[0] < value[2] and value[1] < value[3]):
            raise ValueError("bbox must have positive width and height")
        return value


class TaskExpectation(EvaluationContract):
    outcome: BenchmarkOutcome
    operation: Optional[GeoOperation] = None
    exact_fields: dict[StrictStr, JsonScalar] = Field(default_factory=dict, max_length=64)
    required_steps: list[StrictStr] = Field(default_factory=list, max_length=64)
    feature_ids: list[StrictStr] = Field(default_factory=list, max_length=10000)
    numeric: dict[StrictStr, NumericExpectation] = Field(default_factory=dict, max_length=64)
    geometry: Optional[GeometryExpectation] = None
    ranking: list[StrictStr] = Field(default_factory=list, max_length=1000)
    ranking_min_ndcg: float = Field(default=1.0, ge=0.0, le=1.0)
    reason_code: Optional[StrictStr] = Field(default=None, min_length=1, max_length=128)

    @field_validator("required_steps", "feature_ids", "ranking")
    @classmethod
    def require_unique_values(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("benchmark expectation lists must contain unique values")
        if any(not item.strip() for item in value):
            raise ValueError("benchmark expectation values must not be blank")
        return value

    @model_validator(mode="after")
    def validate_outcome(self):
        if self.outcome == "success":
            if self.operation is None:
                raise ValueError("successful expectations require operation")
            if self.reason_code is not None:
                raise ValueError("successful expectations must not include reason_code")
        else:
            if self.operation is not None:
                raise ValueError("non-success expectations must not include operation")
            if self.reason_code is None:
                raise ValueError("non-success expectations require reason_code")
        return self


class BenchmarkTask(EvaluationContract):
    schema_version: Literal["1.0"] = "1.0"
    task_id: StrictStr = Field(pattern=ID_PATTERN)
    split: Literal["dev", "test"]
    difficulty: Literal["simple", "medium", "hard"]
    category: StrictStr = Field(pattern=r"^[a-z][a-z0-9_]{2,63}$")
    query: StrictStr = Field(min_length=1, max_length=2000)
    task_type: GeoOperation
    random_seed: int = Field(ge=0, le=2147483647)
    data_version: StrictStr = Field(min_length=1, max_length=128)
    expectation: TaskExpectation

    @field_validator("query")
    @classmethod
    def reject_blank_query(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("benchmark query must not be blank")
        return value.strip()

    @model_validator(mode="after")
    def match_success_operation(self):
        if (
            self.expectation.outcome == "success"
            and self.expectation.operation != self.task_type
        ):
            raise ValueError("successful expected operation must match task_type")
        return self


class BenchmarkPrediction(EvaluationContract):
    schema_version: Literal["1.0"] = "1.0"
    task_id: StrictStr = Field(pattern=ID_PATTERN)
    status: BenchmarkOutcome
    operation: Optional[GeoOperation] = None
    exact_fields: dict[StrictStr, JsonScalar] = Field(default_factory=dict, max_length=64)
    steps: list[StrictStr] = Field(default_factory=list, max_length=128)
    feature_ids: list[StrictStr] = Field(default_factory=list, max_length=10000)
    numeric_values: dict[StrictStr, float] = Field(default_factory=dict, max_length=64)
    ranking: list[StrictStr] = Field(default_factory=list, max_length=1000)
    bbox: Optional[list[float]] = Field(default=None, min_length=4, max_length=4)
    reason_code: Optional[StrictStr] = Field(default=None, min_length=1, max_length=128)
    latency_ms: float = Field(ge=0.0)
    tool_calls: int = Field(ge=0)
    llm_calls: int = Field(ge=0)
    estimated_cost_usd: float = Field(ge=0.0)

    @field_validator("steps", "feature_ids", "ranking")
    @classmethod
    def require_unique_values(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("prediction lists must contain unique values")
        return value

    @field_validator("bbox")
    @classmethod
    def validate_bbox(cls, value: Optional[list[float]]):
        if value is not None and not (value[0] < value[2] and value[1] < value[3]):
            raise ValueError("bbox must have positive width and height")
        return value

    @model_validator(mode="after")
    def validate_outcome(self):
        if self.status == "success":
            if self.operation is None:
                raise ValueError("successful predictions require operation")
            if self.reason_code is not None:
                raise ValueError("successful predictions must not include reason_code")
        else:
            if self.operation is not None:
                raise ValueError("non-success predictions must not include operation")
            if self.reason_code is None:
                raise ValueError("non-success predictions require reason_code")
        return self


class StepRecord(EvaluationContract):
    step_id: StrictStr = Field(pattern=ID_PATTERN)
    sequence: int = Field(ge=1)
    kind: Literal["planner", "tool", "validator", "report"]
    name: StrictStr = Field(min_length=1, max_length=128)
    status: Literal["success", "error", "skipped"]
    started_at: datetime
    ended_at: datetime
    latency_ms: float = Field(ge=0.0)
    input_dataset_ids: list[StrictStr] = Field(default_factory=list, max_length=32)
    output_dataset_ids: list[StrictStr] = Field(default_factory=list, max_length=32)

    @field_validator("started_at", "ended_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("trace timestamps must be timezone-aware")
        return value

    @model_validator(mode="after")
    def validate_time_order(self):
        if self.ended_at < self.started_at:
            raise ValueError("step ended_at must not precede started_at")
        return self


class BenchmarkTrace(EvaluationContract):
    schema_version: Literal["1.0"] = "1.0"
    trace_id: StrictStr = Field(pattern=ID_PATTERN)
    task_id: StrictStr = Field(pattern=ID_PATTERN)
    system_id: StrictStr = Field(pattern=ID_PATTERN)
    status: BenchmarkOutcome
    started_at: datetime
    ended_at: datetime
    steps: list[StepRecord] = Field(default_factory=list, max_length=256)
    prediction: BenchmarkPrediction
    failure_category: Optional[StrictStr] = Field(default=None, max_length=128)

    @field_validator("started_at", "ended_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("trace timestamps must be timezone-aware")
        return value

    @model_validator(mode="after")
    def validate_trace(self):
        if self.ended_at < self.started_at:
            raise ValueError("trace ended_at must not precede started_at")
        if self.prediction.task_id != self.task_id or self.prediction.status != self.status:
            raise ValueError("trace and prediction identity/status must match")
        sequences = [step.sequence for step in self.steps]
        if sequences != list(range(1, len(sequences) + 1)):
            raise ValueError("trace step sequence must be contiguous and ordered from 1")
        if len({step.step_id for step in self.steps}) != len(self.steps):
            raise ValueError("trace step_id values must be unique")
        return self


class MetricScore(EvaluationContract):
    name: StrictStr = Field(pattern=r"^[a-z][a-z0-9_]{2,63}$")
    score: float = Field(ge=0.0, le=1.0)
    passed: bool
    details: dict[str, Any] = Field(default_factory=dict)


class TaskScore(EvaluationContract):
    task_id: StrictStr = Field(pattern=ID_PATTERN)
    category: StrictStr
    difficulty: Literal["simple", "medium", "hard"]
    passed: bool
    overall_score: float = Field(ge=0.0, le=1.0)
    components: list[MetricScore] = Field(min_length=1)
    failure_categories: list[FailureCategory] = Field(default_factory=list)


class RunMetadata(EvaluationContract):
    schema_version: Literal["1.0"] = "1.0"
    run_id: StrictStr = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{2,127}$")
    generated_at: datetime
    split: Literal["dev", "test"]
    framework_calibration: bool
    system_id: StrictStr = Field(pattern=ID_PATTERN)
    model_id: Optional[StrictStr] = Field(default=None, min_length=1, max_length=256)
    prompt_version: Optional[StrictStr] = Field(default=None, min_length=1, max_length=128)
    scorer_version: Literal["m11a-v1"] = "m11a-v1"
    random_seeds: list[int] = Field(min_length=1)
    data_version: StrictStr
    tasks_hash: StrictStr = Field(pattern=HASH_PATTERN)
    predictions_hash: StrictStr = Field(pattern=HASH_PATTERN)
    config_hash: StrictStr = Field(pattern=HASH_PATTERN)
    git_commit: StrictStr = Field(min_length=1, max_length=64)
    working_tree_dirty: bool

    @field_validator("generated_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("report timestamp must be timezone-aware")
        return value


class RunSummary(EvaluationContract):
    task_count: int = Field(ge=1)
    passed_count: int = Field(ge=0)
    pass_rate: float = Field(ge=0.0, le=1.0)
    score_mean: float = Field(ge=0.0, le=1.0)
    score_stdev: float = Field(ge=0.0)
    score_p50: float = Field(ge=0.0, le=1.0)
    score_p95: float = Field(ge=0.0, le=1.0)
    latency_mean_ms: float = Field(ge=0.0)
    latency_p50_ms: float = Field(ge=0.0)
    latency_p95_ms: float = Field(ge=0.0)
    mean_tool_calls: float = Field(ge=0.0)
    mean_llm_calls: float = Field(ge=0.0)
    total_estimated_cost_usd: float = Field(ge=0.0)
    failure_category_counts: dict[StrictStr, int]


class BenchmarkRunReport(EvaluationContract):
    metadata: RunMetadata
    summary: RunSummary
    task_scores: list[TaskScore] = Field(min_length=1)
