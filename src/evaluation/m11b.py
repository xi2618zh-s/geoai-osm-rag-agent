"""M11b v2.0 public task and deterministic grading contracts.

This module is additive: M11a v1.0 contracts and scorer remain unchanged.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any, Literal

from pydantic import Field, StrictStr, field_validator, model_validator
from pyproj import CRS
from shapely.geometry import shape
from shapely.errors import GEOSException

from .contracts import EvaluationContract, MetricScore, NumericExpectation

ALLOWED_TOOLS = frozenset({
    "filter_attributes", "reproject_dataset", "repair_geometry",
    "buffer_features", "nearest_features", "features_within",
    "spatial_join", "aggregate_by_region",
})
SUPPORTED_OPERATIONS = frozenset({
    "search", "within_distance", "nearest", "within",
    "spatial_join", "aggregate_by_region",
})
Outcome = Literal["success", "clarification", "error", "partial"]
Operation = Literal[
    "search", "within_distance", "nearest", "within",
    "spatial_join", "aggregate_by_region", "rank",
]


class M11bExpectation(EvaluationContract):
    status: Literal["success", "clarification", "error"]
    reason_code: StrictStr | None = None
    exact_fields: dict[str, str | int | float | bool] = Field(default_factory=dict)
    required_steps: list[StrictStr] = Field(default_factory=list)
    tool_sequence: list[StrictStr] = Field(default_factory=list)
    feature_ids: list[StrictStr] = Field(default_factory=list)
    numeric: dict[StrictStr, NumericExpectation] = Field(default_factory=dict)
    geometry: dict[str, Any] | None = None
    crs: StrictStr | None = None
    min_iou: float = Field(default=0.999999, ge=0, le=1)
    max_hausdorff: float = Field(default=1e-6, ge=0)
    ranking: list[StrictStr] = Field(default_factory=list)
    ranking_min_ndcg: float = Field(default=1, ge=0, le=1)

    @field_validator("feature_ids", "ranking")
    @classmethod
    def unique_ids(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)) or any(not item.strip() for item in value):
            raise ValueError("feature/ranking IDs must be unique and nonblank")
        return value

    @field_validator("tool_sequence")
    @classmethod
    def allow_tools(cls, value: list[str]) -> list[str]:
        if any(item not in ALLOWED_TOOLS for item in value):
            raise ValueError("benchmark tool sequence contains an unregistered M04 tool")
        return value

    @field_validator("max_hausdorff", "min_iou")
    @classmethod
    def finite_threshold(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("geometry thresholds must be finite")
        return value

    @model_validator(mode="after")
    def consistent(self):
        if self.status == "success":
            if self.reason_code is not None or self.geometry is None or self.crs is None:
                raise ValueError("success requires geometry and CRS, without reason code")
            if not self.tool_sequence:
                raise ValueError("success requires real M04 tool sequence")
            try:
                CRS.from_user_input(self.crs)
                geometry = shape(self.geometry)
            except (ValueError, TypeError, GEOSException) as error:
                raise ValueError("reference CRS or geometry invalid") from error
            if not geometry.is_valid:
                raise ValueError("reference geometry invalid")
            for key, spec in self.numeric.items():
                if not all(math.isfinite(x) for x in (spec.value, spec.absolute_tolerance, spec.relative_tolerance)):
                    raise ValueError("reference numeric specification must be finite")
                if (key == "count" or key.endswith("_count")) and (spec.value != int(spec.value) or spec.absolute_tolerance != 0 or spec.relative_tolerance != 0):
                    raise ValueError("count reference must be an exact integer")
        elif self.reason_code is None:
            raise ValueError("non-success requires reason code")
        return self


class M11bTask(EvaluationContract):
    schema_version: Literal["2.0"] = "2.0"
    task_id: StrictStr = Field(pattern=r"^m11b_(dev|test)_[0-9]{3}$")
    split: Literal["dev", "test"] = "dev"
    task_type: Operation
    category: StrictStr
    difficulty: Literal["simple", "medium", "hard"]
    capability: Literal["supported", "unsupported", "clarification"]
    query: StrictStr = Field(min_length=1, max_length=2000)
    random_seed: int = Field(ge=0)
    data_version: Literal["m11b-public-dev-v1", "m11b-private-test-v1"] = "m11b-public-dev-v1"
    source_fixtures: list[StrictStr] = Field(default_factory=list)
    expectation: M11bExpectation

    @model_validator(mode="after")
    def capability_matches(self):
        if not self.task_id.startswith(f"m11b_{self.split}_"):
            raise ValueError("task ID and split mismatch")
        if self.data_version != ("m11b-public-dev-v1" if self.split == "dev" else "m11b-private-test-v1"):
            raise ValueError("task data version and split mismatch")
        if self.capability == "supported":
            if self.task_type not in SUPPORTED_OPERATIONS or self.expectation.status != "success":
                raise ValueError("supported task must have supported operation and success reference")
            if not self.source_fixtures:
                raise ValueError("supported task requires source fixtures")
        elif self.capability == "unsupported":
            if self.task_type != "rank" or self.expectation.reason_code != "unsupported_rank":
                raise ValueError("rank unsupported task must use unsupported_rank")
        elif self.expectation.status != "clarification":
            raise ValueError("clarification task must expect clarification")
        return self


class M11bPrediction(EvaluationContract):
    schema_version: Literal["2.0"] = "2.0"
    task_id: StrictStr = Field(pattern=r"^m11b_(dev|test)_[0-9]{3}$")
    status: Outcome
    operation: Operation | None = None
    reason_code: StrictStr | None = None
    exact_fields: dict[str, str | int | float | bool] = Field(default_factory=dict)
    steps: list[StrictStr] = Field(default_factory=list)
    tool_sequence: list[StrictStr] = Field(default_factory=list)
    feature_ids: list[StrictStr] = Field(default_factory=list)
    numeric_values: dict[StrictStr, float] = Field(default_factory=dict)
    geometry: dict[str, Any] | None = None
    crs: StrictStr | None = None
    ranking: list[StrictStr] = Field(default_factory=list)
    latency_ms: float = Field(default=0.0, ge=0)
    tool_calls: int = Field(default=0, ge=0)
    llm_calls: int = Field(default=0, ge=0)
    estimated_cost_usd: float = Field(default=0.0, ge=0)

    @field_validator("latency_ms", "estimated_cost_usd")
    @classmethod
    def finite_nonnegative(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("runtime measurements must be finite")
        return value

    @field_validator("feature_ids", "ranking")
    @classmethod
    def unique_ids(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)) or any(not item.strip() for item in value):
            raise ValueError("feature/ranking IDs must be unique and nonblank")
        return value

    @field_validator("tool_sequence")
    @classmethod
    def allow_tools(cls, value: list[str]) -> list[str]:
        if any(item not in ALLOWED_TOOLS for item in value):
            raise ValueError("prediction tool sequence contains an unregistered M04 tool")
        return value

    @field_validator("numeric_values")
    @classmethod
    def finite_numbers(cls, value: dict[str, float]) -> dict[str, float]:
        if any(not math.isfinite(item) for item in value.values()):
            raise ValueError("numeric predictions must be finite")
        return value


class M11bScore(EvaluationContract):
    task_id: StrictStr
    passed: bool
    overall_score: float = Field(ge=0, le=1)
    components: list[MetricScore]
    failure_categories: list[StrictStr]


def load_m11b_tasks(path: Path) -> list[M11bTask]:
    rows: list[M11bTask] = []
    ids: set[str] = set()
    queries: set[str] = set()
    for line_number, line in enumerate(Path(path).read_text("utf-8").splitlines(), 1):
        if not line.strip():
            continue
        task = M11bTask.model_validate_json(line)
        norm_query = " ".join(task.query.casefold().split())
        if task.task_id in ids or norm_query in queries:
            raise ValueError(f"duplicate M11b ID or query at line {line_number}")
        ids.add(task.task_id)
        queries.add(norm_query)
        rows.append(task)
    if not rows:
        raise ValueError("empty M11b task file")
    return rows


def _metric(name: str, score: float, passed: bool, **details: Any) -> MetricScore:
    return MetricScore(name=name, score=max(0.0, min(1.0, score)), passed=passed, details=details)


def _lcs(left: list[str], right: list[str]) -> int:
    previous = [0] * (len(right) + 1)
    for item in left:
        current = [0]
        for index, other in enumerate(right, 1):
            current.append(previous[index - 1] + 1 if item == other else
                           max(previous[index], current[-1]))
        previous = current
    return previous[-1]


def _feature_metric(expected: list[str], actual: list[str]) -> MetricScore:
    left, right = set(expected), set(actual)
    if not left and not right:
        precision = recall = f1 = 1.0
    elif not left or not right:
        precision = recall = f1 = 0.0
    else:
        tp = len(left & right)
        precision = tp / len(right)
        recall = tp / len(left)
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return _metric("feature_f1", f1, left == right, precision=precision,
                   recall=recall, true_positive=len(left & right))


def _geometry_metric(expected: M11bExpectation, prediction: M11bPrediction) -> tuple[MetricScore, MetricScore]:
    try:
        crs_pass = prediction.crs is not None and expected.crs is not None and CRS.from_user_input(prediction.crs) == CRS.from_user_input(expected.crs)
    except (ValueError, TypeError):
        crs_pass = False
    crs_metric = _metric("crs_match", float(crs_pass), crs_pass,
                         expected=expected.crs, actual=prediction.crs)
    if not crs_pass or prediction.geometry is None:
        return crs_metric, _metric("geometry_equivalence", 0.0, False, reason="missing_geometry_or_crs")
    try:
        left = shape(expected.geometry)
        right = shape(prediction.geometry)
        if not left.is_valid or not right.is_valid:
            return crs_metric, _metric("geometry_equivalence", 0.0, False, reason="invalid_geometry")
        if left.is_empty or right.is_empty:
            both = left.is_empty and right.is_empty
            return crs_metric, _metric("geometry_equivalence", float(both), both, mode="empty")
        if left.area > 0 or right.area > 0:
            union = left.union(right).area
            iou = left.intersection(right).area / union if union > 0 else 0.0
            passed = iou + 1e-12 >= expected.min_iou
            return crs_metric, _metric("geometry_equivalence", iou, passed, mode="area_iou", iou=iou)
        distance = left.hausdorff_distance(right)
        passed = left.equals(right) or distance <= expected.max_hausdorff
        return crs_metric, _metric("geometry_equivalence", float(passed), passed,
                                   mode="topology_hausdorff", distance=distance)
    except (ValueError, TypeError, GEOSException) as error:
        return crs_metric, _metric("geometry_equivalence", 0.0, False, reason=type(error).__name__)


def _ndcg(expected: list[str], actual: list[str]) -> float:
    relevance = {value: len(expected) - index for index, value in enumerate(expected)}
    def dcg(values: list[str]) -> float:
        return sum((2 ** relevance.get(value, 0) - 1) / math.log2(i + 2)
                   for i, value in enumerate(values[:len(expected)]))
    ideal = dcg(expected)
    return dcg(actual) / ideal if ideal else 1.0


def score_m11b(task: M11bTask, prediction: M11bPrediction) -> M11bScore:
    if task.task_id != prediction.task_id:
        raise ValueError("task ID mismatch")
    expected = task.expectation
    components: list[MetricScore] = []
    status_pass = expected.status == prediction.status and expected.reason_code == prediction.reason_code
    components.append(_metric("status_reason", float(status_pass), status_pass,
                              expected_status=expected.status, actual_status=prediction.status,
                              expected_reason=expected.reason_code, actual_reason=prediction.reason_code))
    if task.capability == "supported":
        operation_pass = prediction.operation == task.task_type
        components.append(_metric("operation_match", float(operation_pass), operation_pass))
        matched = [key for key, value in expected.exact_fields.items()
                   if key in prediction.exact_fields and type(prediction.exact_fields[key]) is type(value)
                   and prediction.exact_fields[key] == value]
        fields_pass = len(matched) == len(expected.exact_fields)
        components.append(_metric("exact_fields", len(matched) / len(expected.exact_fields)
                                  if expected.exact_fields else 1.0, fields_pass,
                                  matched=matched, missing=[k for k in expected.exact_fields if k not in prediction.exact_fields]))
        matched_steps = _lcs(expected.required_steps, prediction.steps)
        components.append(_metric("required_steps", matched_steps / len(expected.required_steps)
                                  if expected.required_steps else 1.0,
                                  matched_steps == len(expected.required_steps)))
        tools_pass = expected.tool_sequence == prediction.tool_sequence
        components.append(_metric("tool_sequence", float(tools_pass), tools_pass,
                                  expected=expected.tool_sequence, actual=prediction.tool_sequence))
        components.append(_feature_metric(expected.feature_ids, prediction.feature_ids))
        numeric_matches = 0
        numeric_details: dict[str, Any] = {}
        for key, spec in expected.numeric.items():
            actual = prediction.numeric_values.get(key)
            tolerance = max(spec.absolute_tolerance, abs(spec.value) * spec.relative_tolerance)
            if key == "count" or key.endswith("_count"):
                passed = actual is not None and math.isfinite(actual) and actual == spec.value and float(actual).is_integer()
            else:
                passed = actual is not None and math.isfinite(actual) and abs(actual - spec.value) <= tolerance + 1e-12
            numeric_matches += int(passed)
            numeric_details[key] = {"expected": spec.value, "actual": actual,
                                    "allowed_error": tolerance, "passed": passed}
        components.append(_metric("numeric_tolerance", numeric_matches / len(expected.numeric)
                                  if expected.numeric else 1.0,
                                  numeric_matches == len(expected.numeric), **numeric_details))
        components.extend(_geometry_metric(expected, prediction))
        if expected.ranking:
            ndcg = _ndcg(expected.ranking, prediction.ranking)
            components.append(_metric("ranking_ndcg", ndcg,
                                      ndcg + 1e-12 >= expected.ranking_min_ndcg))
    else:
        matched_steps = _lcs(expected.required_steps, prediction.steps)
        components.append(_metric("required_steps", matched_steps / len(expected.required_steps)
                                  if expected.required_steps else 1.0,
                                  matched_steps == len(expected.required_steps)))
        tools_pass = not prediction.tool_sequence
        components.append(_metric("tool_sequence", float(tools_pass), tools_pass))
    names = {
        "status_reason": "status_mismatch", "operation_match": "operation_mismatch",
        "exact_fields": "field_mismatch", "required_steps": "required_step_mismatch",
        "tool_sequence": "tool_sequence_mismatch", "feature_f1": "feature_set_mismatch",
        "numeric_tolerance": "numeric_tolerance_exceeded", "crs_match": "crs_mismatch",
        "geometry_equivalence": "geometry_mismatch", "ranking_ndcg": "ranking_mismatch",
    }
    failures = [names[c.name] for c in components if not c.passed]
    return M11bScore(task_id=task.task_id, passed=not failures,
                     overall_score=sum(c.score for c in components) / len(components),
                     components=components, failure_categories=failures)







