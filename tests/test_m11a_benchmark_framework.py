from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
import unittest

from pydantic import ValidationError

from benchmarks.run_m11a_benchmark import run_benchmark
from src.evaluation import (
    BenchmarkPrediction,
    BenchmarkTask,
    BenchmarkTrace,
    StepRecord,
    evaluate_task,
    load_predictions,
    load_tasks,
)


ROOT = Path(__file__).resolve().parents[1]
TASKS_PATH = ROOT / "benchmarks" / "data" / "dev" / "m11a_tasks_v1.jsonl"
PREDICTIONS_PATH = (
    ROOT / "benchmarks" / "data" / "dev" / "m11a_calibration_predictions_v1.jsonl"
)


def task_payload(**overrides):
    payload = {
        "schema_version": "1.0",
        "task_id": "m11a_unit_001",
        "split": "dev",
        "difficulty": "simple",
        "category": "unit_contract",
        "query": "Find parks in Lund",
        "task_type": "search",
        "random_seed": 1101,
        "data_version": "m11a-synthetic-v1",
        "expectation": {
            "outcome": "success",
            "operation": "search",
            "exact_fields": {"place": "Lund"},
            "required_steps": ["resolve_place", "search"],
            "feature_ids": ["park-1", "park-2"],
            "numeric": {
                "count": {"value": 2.0, "absolute_tolerance": 0.0, "relative_tolerance": 0.0}
            },
            "ranking": ["park-1", "park-2"],
            "ranking_min_ndcg": 1.0,
        },
    }
    payload.update(overrides)
    return payload


def prediction_payload(**overrides):
    payload = {
        "schema_version": "1.0",
        "task_id": "m11a_unit_001",
        "status": "success",
        "operation": "search",
        "exact_fields": {"place": "Lund"},
        "steps": ["resolve_place", "search"],
        "feature_ids": ["park-1", "park-2"],
        "numeric_values": {"count": 2.0},
        "ranking": ["park-1", "park-2"],
        "bbox": None,
        "reason_code": None,
        "latency_ms": 12.5,
        "tool_calls": 2,
        "llm_calls": 0,
        "estimated_cost_usd": 0.0,
    }
    payload.update(overrides)
    return payload


class BenchmarkContractTests(unittest.TestCase):
    def test_task_and_prediction_are_strict_versioned_and_round_trip(self):
        task = BenchmarkTask.model_validate(task_payload())
        prediction = BenchmarkPrediction.model_validate(prediction_payload())
        self.assertEqual(BenchmarkTask.model_validate_json(task.model_dump_json()), task)
        self.assertEqual(
            BenchmarkPrediction.model_validate_json(prediction.model_dump_json()), prediction
        )
        with self.assertRaises(ValidationError):
            BenchmarkTask.model_validate({**task_payload(), "unknown": True})
        with self.assertRaises(ValidationError):
            BenchmarkPrediction.model_validate(
                {**prediction_payload(), "tool_calls": "2"}
            )

    def test_contracts_reject_duplicate_ids_and_inconsistent_outcomes(self):
        duplicate = task_payload()
        duplicate["expectation"]["feature_ids"] = ["park-1", "park-1"]
        with self.assertRaises(ValidationError):
            BenchmarkTask.model_validate(duplicate)
        with self.assertRaises(ValidationError):
            BenchmarkPrediction.model_validate(
                prediction_payload(status="clarification", reason_code=None)
            )

    def test_trace_requires_ordered_steps_and_matching_prediction(self):
        started = datetime(2026, 10, 5, 10, 0, tzinfo=timezone.utc)
        steps = [
            StepRecord(
                step_id="step-1",
                sequence=1,
                kind="tool",
                name="resolve_place",
                status="success",
                started_at=started,
                ended_at=started + timedelta(milliseconds=3),
                latency_ms=3.0,
            ),
            StepRecord(
                step_id="step-2",
                sequence=2,
                kind="tool",
                name="search",
                status="success",
                started_at=started + timedelta(milliseconds=3),
                ended_at=started + timedelta(milliseconds=8),
                latency_ms=5.0,
            ),
        ]
        trace = BenchmarkTrace(
            trace_id="trace-m11a-unit-001",
            task_id="m11a_unit_001",
            system_id="fixture_calibration_v1",
            status="success",
            started_at=started,
            ended_at=started + timedelta(milliseconds=8),
            steps=steps,
            prediction=BenchmarkPrediction.model_validate(prediction_payload()),
            failure_category=None,
        )
        self.assertEqual(BenchmarkTrace.model_validate_json(trace.model_dump_json()), trace)
        bad_steps = [steps[1], steps[0]]
        with self.assertRaises(ValidationError):
            BenchmarkTrace.model_validate({**trace.model_dump(), "steps": bad_steps})


class DeterministicScorerTests(unittest.TestCase):
    def test_perfect_prediction_passes_every_applicable_component(self):
        score = evaluate_task(
            BenchmarkTask.model_validate(task_payload()),
            BenchmarkPrediction.model_validate(prediction_payload()),
        )
        self.assertTrue(score.passed)
        self.assertEqual(score.overall_score, 1.0)
        self.assertEqual(score.failure_categories, [])

    def test_outcome_operation_exact_field_and_step_failures_are_classified(self):
        prediction = prediction_payload(
            operation="rank",
            exact_fields={"place": "Malmo"},
            steps=["search", "resolve_place"],
        )
        score = evaluate_task(
            BenchmarkTask.model_validate(task_payload()),
            BenchmarkPrediction.model_validate(prediction),
        )
        self.assertFalse(score.passed)
        self.assertIn("operation_mismatch", score.failure_categories)
        self.assertIn("exact_field_mismatch", score.failure_categories)
        self.assertIn("required_step_mismatch", score.failure_categories)

    def test_feature_numeric_and_ranking_scores_are_partial_and_deterministic(self):
        prediction = prediction_payload(
            feature_ids=["park-1", "park-3"],
            numeric_values={"count": 2.2},
            ranking=["park-2", "park-1"],
        )
        score = evaluate_task(
            BenchmarkTask.model_validate(task_payload()),
            BenchmarkPrediction.model_validate(prediction),
        )
        components = {item.name: item for item in score.components}
        self.assertAlmostEqual(components["feature_set_f1"].score, 0.5)
        self.assertFalse(components["numeric_tolerance"].passed)
        self.assertGreater(components["ranking_ndcg"].score, 0.0)
        self.assertLess(components["ranking_ndcg"].score, 1.0)

    def test_numeric_relative_tolerance_and_bbox_iou_threshold(self):
        payload = task_payload()
        payload["expectation"]["numeric"] = {
            "area_m2": {
                "value": 1000.0,
                "absolute_tolerance": 0.0,
                "relative_tolerance": 0.02,
            }
        }
        payload["expectation"]["geometry"] = {
            "bbox": [0.0, 0.0, 10.0, 10.0],
            "min_iou": 0.8,
        }
        prediction = prediction_payload(
            numeric_values={"area_m2": 1019.0}, bbox=[0.0, 0.0, 9.0, 10.0]
        )
        score = evaluate_task(
            BenchmarkTask.model_validate(payload),
            BenchmarkPrediction.model_validate(prediction),
        )
        components = {item.name: item for item in score.components}
        self.assertTrue(components["numeric_tolerance"].passed)
        self.assertTrue(components["geometry_bbox_iou"].passed)
        self.assertAlmostEqual(components["geometry_bbox_iou"].score, 0.9)


class BenchmarkDatasetAndRunnerTests(unittest.TestCase):
    def test_visible_dev_fixture_has_24_unique_tasks_and_all_operations(self):
        tasks = load_tasks(TASKS_PATH)
        self.assertEqual(len(tasks), 24)
        self.assertEqual(len({task.task_id for task in tasks}), 24)
        self.assertEqual({task.split for task in tasks}, {"dev"})
        self.assertEqual(
            {task.task_type for task in tasks},
            {
                "search",
                "within_distance",
                "nearest",
                "within",
                "spatial_join",
                "aggregate_by_region",
                "rank",
            },
        )

    def test_calibration_predictions_match_task_ids(self):
        tasks = load_tasks(TASKS_PATH)
        predictions = load_predictions(PREDICTIONS_PATH)
        self.assertEqual(
            {task.task_id for task in tasks},
            {prediction.task_id for prediction in predictions},
        )

    def test_loader_rejects_duplicate_ids(self):
        row = json.dumps(task_payload(), ensure_ascii=False)
        with tempfile.TemporaryDirectory(prefix="m11a_duplicate_") as temp:
            path = Path(temp) / "duplicate.jsonl"
            path.write_text(f"{row}\n{row}\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "Duplicate benchmark task_id"):
                load_tasks(path)

    def test_one_offline_run_writes_versioned_json_and_markdown_report(self):
        with tempfile.TemporaryDirectory(prefix="m11a_report_") as temp:
            run_dir, report = run_benchmark(
                tasks_path=TASKS_PATH,
                predictions_path=PREDICTIONS_PATH,
                output_root=Path(temp),
                run_id="20261005T120000Z-testhash",
            )
            self.assertEqual(report.metadata.split, "dev")
            self.assertTrue(report.metadata.framework_calibration)
            self.assertIsNone(report.metadata.model_id)
            self.assertIsNone(report.metadata.prompt_version)
            self.assertEqual(report.summary.task_count, 24)
            self.assertIsNotNone(report.summary.score_stdev)
            self.assertIsNotNone(report.summary.score_p50)
            self.assertIsNotNone(report.summary.score_p95)
            self.assertEqual(len(report.task_scores), 24)
            self.assertTrue((run_dir / "report.json").is_file())
            self.assertTrue((run_dir / "report.md").is_file())
            markdown = (run_dir / "report.md").read_text("utf-8")
            self.assertIn("Framework calibration", markdown)
            self.assertIn("Failure categories", markdown)
            with self.assertRaises(FileExistsError):
                run_benchmark(
                    tasks_path=TASKS_PATH,
                    predictions_path=PREDICTIONS_PATH,
                    output_root=Path(temp),
                    run_id="20261005T120000Z-testhash",
                )


if __name__ == "__main__":
    unittest.main()
