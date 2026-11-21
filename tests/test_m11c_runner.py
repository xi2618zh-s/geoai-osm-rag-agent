"""M11c freeze/run harness: exclusivity, drift refusal, private aggregation, determinism."""
import json
from pathlib import Path
import tempfile
import unittest

from benchmarks.run_m11c_baselines import deterministic_receipt, freeze, run_comparison
from src.agent.baselines import DynamicAgentSystem, ValidatorReplanSystem
from src.evaluation.m11b import load_m11b_tasks, score_m11b
from src.evaluation.m11c import (
    PROTOCOL_VERSION, SEEDS, ablation_systems, dev_catalogue, formal_systems,
    run_system, secondary_pass, stability, summarize, system_input,
)

ROOT = Path(__file__).resolve().parents[1]
TASKS = load_m11b_tasks(ROOT / "benchmarks/data/dev/m11b_tasks_v1.jsonl")
# One task per behaviour: search, nearest, buffer, rank, missing place, empty search.
SUBSET = {"m11b_dev_001", "m11b_dev_059", "m11b_dev_025", "m11b_dev_145", "m11b_dev_153", "m11b_dev_018"}


class Definitions(unittest.TestCase):
    def test_protocol_systems_and_seeds(self):
        self.assertEqual(PROTOCOL_VERSION, "m11c-protocol-v1")
        self.assertEqual(SEEDS, (11071, 11072, 11073))
        self.assertEqual([s.system_id for s in formal_systems()],
                         ["fixed_workflow", "dynamic_agent", "validator_replan"])
        self.assertEqual([s.system_id for s in ablation_systems()],
                         ["template_workflow", "validator_no_frontend", "validator_strict_empty",
                          "validator_no_replan"])

    def test_secondary_metric_ignores_only_required_steps(self):
        task = next(t for t in TASKS if t.task_id == "m11b_dev_001")
        run = DynamicAgentSystem().run(system_input(task), dev_catalogue())
        score = score_m11b(task, run.prediction)
        self.assertFalse(score.passed)
        self.assertTrue(secondary_pass(score))
        broken = run.prediction.model_copy(update={"feature_ids": ["source-1"]})
        self.assertFalse(secondary_pass(score_m11b(task, broken)))

    def test_summary_and_stability_on_dev_subset(self):
        tasks = [t for t in TASKS if t.task_id in SUBSET]
        records = run_system(ValidatorReplanSystem(), tasks, dev_catalogue(), seed=11071,
                             executable={t.task_id: True for t in tasks})
        self.assertEqual([r.task.task_id for r in records], sorted(SUBSET))
        summary = summarize(records)
        self.assertEqual(summary["tasks"], 6)
        self.assertEqual(summary["components"]["required_steps"]["applicable"], 6)
        again = run_system(ValidatorReplanSystem(), tasks, dev_catalogue(), seed=11072,
                           executable={t.task_id: True for t in tasks})
        self.assertEqual(stability([records, again]), {"tasks": 6, "identical": 6, "rate": 1.0})
        self.assertEqual(summarize([])["tasks"], 0)


class SystemFaults(unittest.TestCase):
    def test_unexpected_routing_fault_is_a_recorded_failure_not_a_crash(self):
        system = ValidatorReplanSystem()

        def broken(*args, **kwargs):
            raise RuntimeError("unexpected")

        system.route = broken
        task = next(t for t in TASKS if t.task_id == "m11b_dev_001")
        run = system.run(system_input(task), dev_catalogue())
        self.assertEqual((run.prediction.status, run.prediction.reason_code), ("error", "system_exception"))
        self.assertEqual(run.trace["system_exception"], "RuntimeError")
        self.assertFalse(score_m11b(task, run.prediction).passed)


class FreezeAndRun(unittest.TestCase):
    def test_freeze_is_exclusive_and_run_refuses_drift_and_keeps_private_aggregate(self):
        with tempfile.TemporaryDirectory(prefix="m11c_runner_") as temp:
            root = Path(temp)
            manifest = freeze(root / "freeze", task_ids=SUBSET)
            with self.assertRaises(FileExistsError):
                freeze(root / "freeze", task_ids=SUBSET)
            frozen = json.loads(manifest.read_text("utf-8"))
            self.assertEqual(frozen["protocol_version"], PROTOCOL_VERSION)
            self.assertEqual(frozen["dev_executable_counts"]["validator_replan"], 3)
            self.assertEqual(frozen["dev_executable_counts"]["fixed_workflow"], 2)
            tampered = root / "tampered.json"
            tampered.write_text(json.dumps({**frozen, "source_fingerprint": "sha256:" + "0" * 64}), "utf-8")
            with self.assertRaises(ValueError):
                run_comparison(root / "runs", "tampered-run", tampered, task_ids=SUBSET,
                               seeds=(11071,), include_private=False)
            run_dir = run_comparison(root / "runs", "subset-run", manifest, task_ids=SUBSET,
                                     seeds=(11071, 11072), include_private=False)
            report = json.loads((run_dir / "report.json").read_text("utf-8"))
            self.assertEqual(report["metadata"]["freeze_manifest_sha256"][:7], "sha256:")
            dev = report["splits"]["dev"]
            self.assertEqual(set(dev["configs"]), {"fixed_workflow", "dynamic_agent", "validator_replan",
                                                   "template_workflow", "validator_no_frontend",
                                                   "validator_strict_empty", "validator_no_replan"})
            self.assertEqual(dev["subsets"]["common_executable"], 2)
            self.assertTrue((run_dir / "m11b_reports/dev/validator_replan-run1/report.json").is_file())
            self.assertTrue((run_dir / "report.md").is_file())
            with self.assertRaises(FileExistsError):
                run_comparison(root / "runs", "subset-run", manifest, task_ids=SUBSET,
                               seeds=(11071,), include_private=False)
            receipt = json.loads((run_dir / "deterministic_receipt.json").read_text("utf-8"))
            again = deterministic_receipt(manifest, task_ids=SUBSET, seeds=(11071, 11072),
                                          include_private=False)
            self.assertEqual(receipt, again)

    def test_private_split_summary_publishes_no_task_rows(self):
        from benchmarks.run_m11c_baselines import split_report
        tasks = [t for t in TASKS if t.task_id in SUBSET]
        runs = {system.system_id: [run_system(system, tasks, dev_catalogue(), seed=11071,
                                              executable={t.task_id: True for t in tasks})]
                for system in formal_systems()}
        matrix = {t.task_id: {s: True for s in runs} for t in tasks}
        private = split_report(tasks, runs, matrix, publish_task_rows=False)
        text = json.dumps(private)
        self.assertNotIn("m11b_dev_", text)
        self.assertNotIn("task_rows", private)
        public = split_report(tasks, runs, matrix, publish_task_rows=True)
        self.assertIn("m11b_dev_001", json.dumps(public["task_rows"]))


if __name__ == "__main__":
    unittest.main()
