"""Deterministic scorers for M11 benchmark tasks."""

from __future__ import annotations

import math
from typing import Protocol, runtime_checkable

from .contracts import BenchmarkPrediction, BenchmarkTask, MetricScore, TaskScore


@runtime_checkable
class TaskScorer(Protocol):
    name: str

    def score(self, task: BenchmarkTask, prediction: BenchmarkPrediction) -> MetricScore | None:
        ...


class OutcomeScorer:
    name = "outcome_match"

    def score(self, task, prediction):
        expected = task.expectation
        status_matches = prediction.status == expected.outcome
        reason_matches = expected.reason_code == prediction.reason_code
        passed = status_matches and reason_matches
        return MetricScore(
            name=self.name,
            score=1.0 if passed else 0.0,
            passed=passed,
            details={
                "expected_status": expected.outcome,
                "predicted_status": prediction.status,
                "expected_reason_code": expected.reason_code,
                "predicted_reason_code": prediction.reason_code,
            },
        )


class OperationScorer:
    name = "operation_match"

    def score(self, task, prediction):
        if task.expectation.operation is None:
            return None
        passed = prediction.operation == task.expectation.operation
        return MetricScore(
            name=self.name,
            score=1.0 if passed else 0.0,
            passed=passed,
            details={
                "expected": task.expectation.operation,
                "predicted": prediction.operation,
            },
        )


class ExactFieldScorer:
    name = "exact_fields"

    def score(self, task, prediction):
        expected = task.expectation.exact_fields
        if not expected:
            return None
        matched = [key for key, value in expected.items() if prediction.exact_fields.get(key) == value]
        score = len(matched) / len(expected)
        return MetricScore(
            name=self.name,
            score=score,
            passed=len(matched) == len(expected),
            details={"matched": matched, "expected_count": len(expected)},
        )


def _lcs_length(left: list[str], right: list[str]) -> int:
    previous = [0] * (len(right) + 1)
    for left_item in left:
        current = [0]
        for index, right_item in enumerate(right, 1):
            if left_item == right_item:
                current.append(previous[index - 1] + 1)
            else:
                current.append(max(previous[index], current[-1]))
        previous = current
    return previous[-1]


class RequiredStepScorer:
    name = "required_steps"

    def score(self, task, prediction):
        required = task.expectation.required_steps
        if not required:
            return None
        matched = _lcs_length(required, prediction.steps)
        return MetricScore(
            name=self.name,
            score=matched / len(required),
            passed=matched == len(required),
            details={"matched_in_order": matched, "required_count": len(required)},
        )


class FeatureSetScorer:
    name = "feature_set_f1"

    def score(self, task, prediction):
        expected = set(task.expectation.feature_ids)
        if not expected:
            return None
        predicted = set(prediction.feature_ids)
        true_positive = len(expected & predicted)
        precision = true_positive / len(predicted) if predicted else 0.0
        recall = true_positive / len(expected)
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
        return MetricScore(
            name=self.name,
            score=f1,
            passed=predicted == expected,
            details={"precision": precision, "recall": recall, "true_positive": true_positive},
        )


class NumericToleranceScorer:
    name = "numeric_tolerance"

    def score(self, task, prediction):
        expected = task.expectation.numeric
        if not expected:
            return None
        matches = 0
        details = {}
        for key, specification in expected.items():
            actual = prediction.numeric_values.get(key)
            tolerance = max(
                specification.absolute_tolerance,
                abs(specification.value) * specification.relative_tolerance,
            )
            error = None if actual is None else abs(actual - specification.value)
            passed = error is not None and error <= tolerance + 1e-12
            matches += int(passed)
            details[key] = {
                "expected": specification.value,
                "actual": actual,
                "absolute_error": error,
                "allowed_error": tolerance,
                "passed": passed,
            }
        return MetricScore(
            name=self.name,
            score=matches / len(expected),
            passed=matches == len(expected),
            details=details,
        )


def _bbox_iou(left: list[float], right: list[float]) -> float:
    intersection_width = max(0.0, min(left[2], right[2]) - max(left[0], right[0]))
    intersection_height = max(0.0, min(left[3], right[3]) - max(left[1], right[1]))
    intersection = intersection_width * intersection_height
    left_area = (left[2] - left[0]) * (left[3] - left[1])
    right_area = (right[2] - right[0]) * (right[3] - right[1])
    union = left_area + right_area - intersection
    return intersection / union if union > 0 else 0.0


class GeometryScorer:
    name = "geometry_bbox_iou"

    def score(self, task, prediction):
        expected = task.expectation.geometry
        if expected is None:
            return None
        iou = _bbox_iou(expected.bbox, prediction.bbox) if prediction.bbox else 0.0
        return MetricScore(
            name=self.name,
            score=iou,
            passed=iou + 1e-12 >= expected.min_iou,
            details={"iou": iou, "minimum": expected.min_iou},
        )


def _ndcg(expected: list[str], predicted: list[str]) -> float:
    relevance = {item: len(expected) - index for index, item in enumerate(expected)}

    def dcg(items: list[str]) -> float:
        return sum(
            (2 ** relevance.get(item, 0) - 1) / math.log2(index + 2)
            for index, item in enumerate(items[: len(expected)])
        )

    ideal = dcg(expected)
    return dcg(predicted) / ideal if ideal else 0.0


class RankingScorer:
    name = "ranking_ndcg"

    def score(self, task, prediction):
        expected = task.expectation.ranking
        if not expected:
            return None
        ndcg = _ndcg(expected, prediction.ranking)
        return MetricScore(
            name=self.name,
            score=min(1.0, ndcg),
            passed=ndcg + 1e-12 >= task.expectation.ranking_min_ndcg,
            details={"ndcg": ndcg, "minimum": task.expectation.ranking_min_ndcg},
        )


DEFAULT_SCORERS: tuple[TaskScorer, ...] = (
    OutcomeScorer(),
    OperationScorer(),
    ExactFieldScorer(),
    RequiredStepScorer(),
    FeatureSetScorer(),
    NumericToleranceScorer(),
    GeometryScorer(),
    RankingScorer(),
)

FAILURE_BY_COMPONENT = {
    "outcome_match": "outcome_mismatch",
    "operation_match": "operation_mismatch",
    "exact_fields": "exact_field_mismatch",
    "required_steps": "required_step_mismatch",
    "feature_set_f1": "feature_set_mismatch",
    "numeric_tolerance": "numeric_tolerance_exceeded",
    "geometry_bbox_iou": "geometry_mismatch",
    "ranking_ndcg": "ranking_mismatch",
}


def evaluate_task(
    task: BenchmarkTask,
    prediction: BenchmarkPrediction,
    scorers: tuple[TaskScorer, ...] = DEFAULT_SCORERS,
) -> TaskScore:
    if prediction.task_id != task.task_id:
        raise ValueError("prediction task_id does not match benchmark task")
    components = [result for scorer in scorers if (result := scorer.score(task, prediction))]
    if not components:
        raise ValueError("at least one scorer must apply to a task")
    failures = [FAILURE_BY_COMPONENT[item.name] for item in components if not item.passed]
    return TaskScore(
        task_id=task.task_id,
        category=task.category,
        difficulty=task.difficulty,
        passed=not failures,
        overall_score=sum(item.score for item in components) / len(components),
        components=components,
        failure_categories=failures,
    )

