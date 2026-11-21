from __future__ import annotations

import json
import math
from pathlib import Path
import sys
import tempfile
import unittest

from pydantic import ValidationError
from shapely.geometry import box, mapping, shape

from src.evaluation.m11b import (
    M11bPrediction, M11bTask, load_m11b_tasks, score_m11b,
)
from scripts.build_m11b_dataset import _json_bytes, _tasks_bytes, build_dataset, audit_dataset
from scripts.build_m11b_trace_provenance import audit_traces

ROOT = Path(__file__).resolve().parents[1]
TASKS = ROOT / "benchmarks/data/dev/m11b_tasks_v1.jsonl"
MANIFEST = ROOT / "benchmarks/data/dev/m11b_manifest_v1.json"
TRACES = ROOT / "benchmarks/data/dev/m11b_tool_traces_v1.jsonl"

# The frozen M11b release was built on Windows. GEOS buffer arithmetic can differ in
# the last floating-point digit on other platforms, so byte identity of a rebuild is
# required on the reference platform only; elsewhere the rebuild must match the
# frozen release structurally, with floats equal within a relative 1e-9.
REFERENCE_PLATFORM = sys.platform == "win32"
# Fields that are hashes of geometry-bearing bytes and therefore cannot match when
# a coordinate differs in its last digit.
GEOMETRY_HASH_KEYS = frozenset({"tasks_sha256", "input_hashes", "output_hash"})


def _numerically_equal(left, right, *, ignore=frozenset()):
    if isinstance(left, bool) or isinstance(right, bool):
        return left is right
    if isinstance(left, float) or isinstance(right, float):
        return (isinstance(left, (int, float)) and isinstance(right, (int, float))
                and math.isclose(left, right, rel_tol=1e-9, abs_tol=1e-9))
    if isinstance(left, dict) and isinstance(right, dict):
        return (left.keys() == right.keys()
                and all(key in ignore or _numerically_equal(left[key], right[key], ignore=ignore)
                        for key in left))
    if isinstance(left, list) and isinstance(right, list):
        return len(left) == len(right) and all(
            _numerically_equal(a, b, ignore=ignore) for a, b in zip(left, right))
    return left == right


def _json_records(data: bytes):
    text = data.decode("utf-8")
    return [json.loads(line) for line in text.splitlines() if line.strip()]


class M11bDatasetTests(unittest.TestCase):
    def assertRebuildMatches(self, rebuilt: bytes, frozen: bytes, label: str):
        """Byte identity on the reference platform; numeric equality elsewhere."""
        if rebuilt == frozen:
            return
        if REFERENCE_PLATFORM:
            self.fail(f"{label}: byte-level rebuild differs on the reference platform")
        self.assertTrue(
            _numerically_equal(_json_records(rebuilt), _json_records(frozen), ignore=GEOMETRY_HASH_KEYS),
            f"{label}: rebuild differs beyond floating-point tolerance",
        )

    def test_release_has_160_unique_tasks_and_real_tool_provenance(self):
        tasks = load_m11b_tasks(TASKS)
        self.assertEqual(len(tasks), 160)
        self.assertEqual(len({t.task_id for t in tasks}), 160)
        self.assertEqual(len({t.query.casefold() for t in tasks}), 160)
        counts = {}
        for task in tasks:
            counts[task.task_type] = counts.get(task.task_type, 0) + 1
            self.assertEqual(task.split, "dev")
            if task.capability == "supported":
                self.assertTrue(task.expectation.tool_sequence)
                self.assertEqual(task.expectation.status, "success")
        self.assertEqual(counts, {
            "search": 32, "within_distance": 24, "nearest": 24,
            "within": 24, "spatial_join": 24,
            "aggregate_by_region": 24, "rank": 8,
        })
        self.assertEqual(sum(t.capability == "supported" for t in tasks), 144)
        self.assertEqual(sum(t.capability == "clarification" for t in tasks), 8)
        self.assertEqual(sum(t.capability == "unsupported" for t in tasks), 8)
        if REFERENCE_PLATFORM:
            self.assertTrue(audit_dataset(TASKS, MANIFEST))
        else:
            task_bytes, manifest_bytes = _tasks_bytes()
            self.assertRebuildMatches(task_bytes, TASKS.read_bytes(), "M11b tasks")
            self.assertRebuildMatches(manifest_bytes, MANIFEST.read_bytes(), "M11b manifest")

    def test_real_m04_tool_trace_provenance_is_byte_reproducible(self):
        trace_path = TRACES
        rows = [json.loads(line) for line in trace_path.read_text("utf-8").splitlines()]
        tasks = load_m11b_tasks(TASKS)
        self.assertEqual(len(rows), 160)
        self.assertEqual([row["task_id"] for row in rows], [task.task_id for task in tasks])
        for row, task in zip(rows, tasks):
            self.assertEqual([call["name"] for call in row["calls"]],
                             task.expectation.tool_sequence)
            for call in row["calls"]:
                self.assertTrue(call["parameters_hash"].startswith("sha256:"))
                self.assertTrue(call["output_hash"].startswith("sha256:"))
        if REFERENCE_PLATFORM:
            self.assertTrue(audit_traces())
        else:
            rebuilt_rows = []
            _tasks_bytes(rebuilt_rows)
            rebuilt = b"".join(_json_bytes(row) for row in rebuilt_rows)
            self.assertRebuildMatches(rebuilt, TRACES.read_bytes(), "M11b tool traces")

    def test_build_is_byte_reproducible_and_exclusive(self):
        with tempfile.TemporaryDirectory(prefix="m11b_build_") as temp:
            output = Path(temp)
            built_tasks, built_manifest = build_dataset(output)
            self.assertRebuildMatches(built_tasks.read_bytes(), TASKS.read_bytes(), "M11b tasks")
            self.assertRebuildMatches(built_manifest.read_bytes(), MANIFEST.read_bytes(), "M11b manifest")
            with self.assertRaises(FileExistsError):
                build_dataset(output)


class M11bScorerTests(unittest.TestCase):
    def setUp(self):
        self.task = next(t for t in load_m11b_tasks(TASKS) if t.capability == "supported")
        e = self.task.expectation
        self.good = M11bPrediction(
            task_id=self.task.task_id, status=e.status, operation=self.task.task_type,
            reason_code=None, exact_fields=e.exact_fields,
            steps=e.required_steps, tool_sequence=e.tool_sequence,
            feature_ids=e.feature_ids, numeric_values={k: v.value for k, v in e.numeric.items()},
            geometry=e.geometry, crs=e.crs,
        )

    def test_reference_passes_and_missing_field_partial_or_tool_fails(self):
        self.assertTrue(score_m11b(self.task, self.good).passed)
        bad = self.good.model_copy(update={"status": "partial", "exact_fields": {},
            "tool_sequence": self.good.tool_sequence + ["filter_attributes"]})
        score = score_m11b(self.task, bad)
        self.assertFalse(score.passed)
        self.assertIn("status_mismatch", score.failure_categories)
        self.assertIn("field_mismatch", score.failure_categories)
        self.assertIn("tool_sequence_mismatch", score.failure_categories)

    def test_geometry_uses_shape_not_bbox_and_crs_is_explicit(self):
        task = next(t for t in load_m11b_tasks(TASKS)
                    if t.capability == "supported" and t.task_type == "within_distance")
        e = task.expectation
        good = M11bPrediction(task_id=task.task_id, status="success",
            operation=task.task_type, exact_fields=e.exact_fields,
            steps=e.required_steps, tool_sequence=e.tool_sequence,
            feature_ids=e.feature_ids, numeric_values={k: v.value for k, v in e.numeric.items()},
            geometry=e.geometry, crs=e.crs)
        self.assertTrue(score_m11b(task, good).passed)
        wrong = good.model_copy(update={"geometry": mapping(box(*shape(e.geometry).bounds))})
        self.assertIn("geometry_mismatch", score_m11b(task, wrong).failure_categories)
        wrong_crs = good.model_copy(update={"crs": "EPSG:4326"})
        self.assertIn("crs_mismatch", score_m11b(task, wrong_crs).failure_categories)

    def test_empty_sets_and_invalid_geometry(self):
        task = next(t for t in load_m11b_tasks(TASKS)
                    if t.capability == "supported" and not t.expectation.feature_ids)
        e = task.expectation
        good = M11bPrediction(task_id=task.task_id, status="success",
            operation=task.task_type, exact_fields=e.exact_fields,
            steps=e.required_steps, tool_sequence=e.tool_sequence,
            feature_ids=[], numeric_values={k: v.value for k, v in e.numeric.items()},
            geometry=e.geometry, crs=e.crs)
        score = score_m11b(task, good)
        self.assertEqual(next(c for c in score.components if c.name == "feature_f1").score, 1.0)
        invalid = good.model_copy(update={"geometry": {
            "type": "Polygon", "coordinates": [[[0,0],[2,2],[0,2],[2,0],[0,0]]]
        }})
        self.assertIn("geometry_mismatch", score_m11b(task, invalid).failure_categories)

    def test_unsupported_rank_and_tool_permission(self):
        task = next(t for t in load_m11b_tasks(TASKS) if t.capability == "unsupported")
        good = M11bPrediction(task_id=task.task_id, status="error",
            reason_code="unsupported_rank", steps=["reject_unsupported"])
        self.assertTrue(score_m11b(task, good).passed)
        missing_step = good.model_copy(update={"steps": []})
        self.assertIn("required_step_mismatch", score_m11b(task, missing_step).failure_categories)
        self.assertFalse(score_m11b(task, good.model_copy(update={"reason_code": "other"})).passed)
        payload = self.task.model_dump(mode="json")
        payload["expectation"]["tool_sequence"] = ["private_tool"]
        with self.assertRaises(ValidationError):
            M11bTask.model_validate(payload)
        bad_split = self.task.model_dump(mode="json")
        bad_split["split"] = "test"
        with self.assertRaises(ValidationError):
            M11bTask.model_validate(bad_split)
        bad_version = self.task.model_dump(mode="json")
        bad_version["data_version"] = "m11b-private-test-v1"
        with self.assertRaises(ValidationError):
            M11bTask.model_validate(bad_version)


    def test_feature_precision_recall_and_numeric_tolerance_boundary(self):
        task = next(t for t in load_m11b_tasks(TASKS)
                    if t.capability == "supported" and len(t.expectation.feature_ids) >= 2)
        e = task.expectation
        good = M11bPrediction(task_id=task.task_id, status="success",
            operation=task.task_type, exact_fields=e.exact_fields,
            steps=e.required_steps, tool_sequence=e.tool_sequence,
            feature_ids=e.feature_ids, numeric_values={k: v.value for k, v in e.numeric.items()},
            geometry=e.geometry, crs=e.crs)
        subset = good.model_copy(update={"feature_ids": e.feature_ids[:1]})
        metric = next(c for c in score_m11b(task, subset).components if c.name == "feature_f1")
        self.assertEqual(metric.details["precision"], 1.0)
        self.assertEqual(metric.details["recall"], 1 / len(e.feature_ids))
        count_bad = good.model_copy(update={"numeric_values": {
            **good.numeric_values, "count": good.numeric_values["count"] + 1
        }})
        self.assertIn("numeric_tolerance_exceeded",
                      score_m11b(task, count_bad).failure_categories)
        near_count = good.model_copy(update={"numeric_values": {
            **good.numeric_values, "count": good.numeric_values["count"] + 5e-13
        }})
        self.assertIn("numeric_tolerance_exceeded",
                      score_m11b(task, near_count).failure_categories)
        missing = good.model_copy(update={"geometry": None})
        self.assertIn("geometry_mismatch", score_m11b(task, missing).failure_categories)
if __name__ == "__main__":
    unittest.main()







