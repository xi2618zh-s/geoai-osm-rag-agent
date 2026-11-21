from pathlib import Path
import tempfile
import unittest

from benchmarks.run_m11b_scoring import score_predictions
from src.evaluation.m11b import M11bPrediction, load_m11b_tasks

ROOT = Path(__file__).resolve().parents[1]


class M11bRunnerTests(unittest.TestCase):
    def _prediction(self, task):
        e = task.expectation
        return M11bPrediction(task_id=task.task_id, status=e.status,
            operation=task.task_type if task.capability == "supported" else None,
            reason_code=e.reason_code, exact_fields=e.exact_fields,
            steps=e.required_steps, tool_sequence=e.tool_sequence,
            feature_ids=e.feature_ids,
            numeric_values={k: v.value for k, v in e.numeric.items()},
            geometry=e.geometry, crs=e.crs, latency_ms=5.0,
            tool_calls=len(e.tool_sequence))

    def test_dev_scoring_is_exclusive_and_reports_failure_class(self):
        tasks = load_m11b_tasks(ROOT / "benchmarks/data/dev/m11b_tasks_v1.jsonl")[:2]
        with tempfile.TemporaryDirectory(prefix="m11b_runner_") as temp:
            root = Path(temp)
            task_file = root / "tasks.jsonl"
            prediction_file = root / "predictions.jsonl"
            task_file.write_text("".join(t.model_dump_json() + "\n" for t in tasks), encoding="utf-8")
            good = self._prediction(tasks[0])
            bad = self._prediction(tasks[1]).model_copy(update={"feature_ids": []})
            prediction_file.write_text(good.model_dump_json() + "\n" + bad.model_dump_json() + "\n",
                                       encoding="utf-8")
            run_dir, report = score_predictions(task_file, prediction_file,
                root / "runs", run_id="m11b-test-run", system_id="unit_fixture")
            self.assertEqual(report["summary"]["task_count"], 2)
            self.assertEqual(report["summary"]["passed_count"], 1)
            self.assertIn("feature_set_mismatch", report["summary"]["failure_category_counts"])
            self.assertTrue((run_dir / "report.json").is_file())
            self.assertTrue((run_dir / "report.md").is_file())
            with self.assertRaises(FileExistsError):
                score_predictions(task_file, prediction_file, root / "runs",
                                  run_id="m11b-test-run", system_id="unit_fixture")

    def test_hidden_report_omits_task_payload_and_reference_details(self):
        private_tasks = ROOT / "benchmarks/data/test/private/m11b_test_tasks_v1.jsonl"
        if not private_tasks.is_file():
            self.skipTest("private holdout intentionally absent from checkout")
        task = load_m11b_tasks(private_tasks)[0]
        with tempfile.TemporaryDirectory(prefix="m11b_hidden_runner_") as temp:
            root = Path(temp)
            task_file = root / "hidden.jsonl"
            prediction_file = root / "predictions.jsonl"
            task_file.write_text(task.model_dump_json() + "\n", encoding="utf-8")
            prediction_file.write_text(self._prediction(task).model_dump_json() + "\n",
                                       encoding="utf-8")
            run_dir, report = score_predictions(task_file, prediction_file,
                root / "runs", run_id="m11b-hidden-test", system_id="unit_fixture")
            self.assertIsNone(report["metadata"]["random_seeds"])
            self.assertNotIn("task_scores", report)
            self.assertNotIn(task.query, (run_dir / "report.json").read_text("utf-8"))
            self.assertNotIn(task.query, (run_dir / "report.md").read_text("utf-8"))


if __name__ == "__main__":
    unittest.main()

