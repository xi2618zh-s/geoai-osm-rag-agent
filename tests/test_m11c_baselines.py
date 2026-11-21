"""M11c baseline systems: front-end grounding, input isolation and dev-only runs."""
import inspect
from pathlib import Path
import tempfile
import unittest

from src.agent import benchmark_frontend
from src.agent.baselines import (
    DynamicAgentSystem, SystemInput, ValidatorReplanSystem, register_catalogue,
)
from src.agent.benchmark_frontend import interpret
from src.datasets import LocalDatasetRegistry
from src.evaluation.m11b import load_m11b_tasks, score_m11b
from src.evaluation.m11c import dev_catalogue, system_input

ROOT = Path(__file__).resolve().parents[1]
DEV_TASKS = {task.query: task for task in load_m11b_tasks(ROOT / "benchmarks/data/dev/m11b_tasks_v1.jsonl")}
SCOPE = "Synthetic dev catalogue"


class FrontendGrounding(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix="m11c_frontend_")
        cls.registry = LocalDatasetRegistry(Path(cls.temp.name) / "datasets")
        cls.catalogue = register_catalogue(dev_catalogue(), cls.registry)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def decide(self, query, catalogue=None):
        return interpret(query, self.catalogue if catalogue is None else catalogue, scope=SCOPE)

    def test_dev_catalogue_declares_every_m04b_fixture_with_unique_roles(self):
        self.assertEqual(set(self.catalogue), {"values", "empty", "points", "tie",
                                               "regions", "sources", "targets"})

    def test_search_binds_role_conditions_and_scope(self):
        decision = self.decide("Search synthetic sources: category clinic, score gte 0")
        self.assertEqual(decision.status, "request")
        self.assertEqual(decision.operation, "search")
        self.assertEqual(decision.command,
                         f"search sources where category = clinic and score >= 0 in {SCOPE}")
        self.assertEqual(set(decision.request.datasets), {"sources"})
        self.assertEqual(decision.request.datasets["sources"].required_fields, ["category", "score"])
        self.assertEqual(decision.request.analysis_crs, "EPSG:3857")

    def test_spatial_operations_render_m07_commands(self):
        cases = {
            "Find 2 nearest synthetic targets for sources with score at least 3":
                f"find 2 nearest targets to sources where score >= 3 in {SCOPE}",
            "Find synthetic sources with score at least 4 strictly within regions":
                f"find sources where score >= 4 within regions in {SCOPE}",
            "Join synthetic regions to sources with score at least 2 by contains":
                f"join regions with sources where score >= 2 on contains in {SCOPE}",
            "Aggregate synthetic region values by count after value at least 5":
                f"aggregate count by region_id from values where value >= 5 into regions using joined_region_id in {SCOPE}",
            "Aggregate synthetic region values by sum after value at least 5":
                f"aggregate sum value by region_id from values where value >= 5 into regions using joined_region_id in {SCOPE}",
        }
        for query, command in cases.items():
            self.assertIn(query, DEV_TASKS)
            decision = self.decide(query)
            self.assertEqual(decision.status, "request", query)
            self.assertEqual(decision.command, command)

    def test_single_dataset_buffer_is_explicitly_unsupported(self):
        decision = self.decide("Make a fixed 1 metre buffer of synthetic sources")
        self.assertEqual((decision.status, decision.operation, decision.reason_code),
                         ("unsupported", "buffer", "unsupported_buffer"))

    def test_rank_is_unsupported_before_place_grounding(self):
        for catalogue in (None, {}):
            decision = self.decide("Rank the synthetic regions by unsupported criterion 1", catalogue)
            self.assertEqual((decision.status, decision.reason_code), ("unsupported", "unsupported_rank"))

    def test_missing_place_clarifies_even_with_full_catalogue(self):
        for catalogue in (None, {}):
            decision = self.decide("Find synthetic category 1 without specifying a place", catalogue)
            self.assertEqual((decision.status, decision.reason_code), ("clarification", "missing_place"))

    def test_partially_bound_query_asks_about_unknown_dataset(self):
        decision = self.decide("Find 2 nearest synthetic hospitals for sources with score at least 1")
        self.assertEqual((decision.status, decision.reason_code), ("clarification", "unknown_dataset"))

    def test_frontend_reads_only_query_catalogue_and_scope(self):
        self.assertEqual(list(inspect.signature(interpret).parameters), ["query", "catalogue", "scope"])
        source = inspect.getsource(benchmark_frontend)
        for forbidden in ("expectation", "capability", "task_type", "feature_ids", "m11b_tasks"):
            self.assertNotIn(forbidden, source)


class InputIsolation(unittest.TestCase):
    def test_system_input_carries_only_task_id_and_query(self):
        self.assertEqual(set(SystemInput.model_fields), {"schema_version", "task_id", "query"})

    def test_task_metadata_changes_do_not_reach_systems(self):
        task = DEV_TASKS["Find 2 nearest synthetic targets for sources with score at least 3"]
        mutated = task.model_copy(update={"task_type": "rank", "category": "x", "capability": "unsupported",
                                          "expectation": task.expectation.model_copy(update={"feature_ids": []})})
        self.assertEqual(system_input(task), system_input(mutated))


class DevSystemRuns(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalogue = dev_catalogue()
        cls.dynamic = DynamicAgentSystem()
        cls.validator = ValidatorReplanSystem()

    def run_both(self, query):
        task = DEV_TASKS[query]
        return task, self.dynamic.run(system_input(task), self.catalogue), self.validator.run(system_input(task), self.catalogue)

    def test_supported_operations_reproduce_m04_reference_through_both_agents(self):
        queries = [
            "Search synthetic sources: category clinic, score gte 0",
            "Find 2 nearest synthetic targets for sources with score at least 3",
            "Find synthetic sources with score at least 4 strictly within regions",
            "Join synthetic regions to sources with score at least 2 by contains",
            "Aggregate synthetic region values by sum after value at least 5",
        ]
        for query in queries:
            task, dynamic, validator = self.run_both(query)
            self.assertEqual(validator.prediction.steps, ["load_fixture", "execute_m04", "validate_result"])
            self.assertEqual(dynamic.prediction.steps, ["load_fixture", "execute_m04"])
            self.assertTrue(score_m11b(task, validator.prediction).passed, query)
            failed = score_m11b(task, dynamic.prediction).failure_categories
            self.assertEqual(failed, ["required_step_mismatch"], query)
            self.assertEqual(dynamic.prediction.tool_sequence, validator.prediction.tool_sequence)
            self.assertGreater(validator.prediction.tool_calls, dynamic.prediction.tool_calls)
            for run in (dynamic, validator):
                self.assertEqual((run.prediction.llm_calls, run.prediction.estimated_cost_usd), (0, 0.0))

    def test_empty_result_is_a_valid_success(self):
        task, dynamic, validator = self.run_both("Search synthetic sources: category school, score gte 25")
        self.assertEqual(validator.prediction.status, "success")
        self.assertEqual(validator.prediction.feature_ids, [])
        self.assertEqual(validator.prediction.numeric_values, {"count": 0.0})
        self.assertTrue(score_m11b(task, validator.prediction).passed)

    def test_non_executable_tasks_never_call_tools(self):
        expected = {
            "Rank the synthetic regions by unsupported criterion 1": ("error", "unsupported_rank", ["reject_unsupported"]),
            "Find synthetic category 1 without specifying a place": ("clarification", "missing_place", ["clarify"]),
            "Make a fixed 1 metre buffer of synthetic sources": ("error", "unsupported_buffer", ["reject_unsupported"]),
        }
        for query, (status, reason, steps) in expected.items():
            _, dynamic, validator = self.run_both(query)
            for run in (dynamic, validator):
                self.assertEqual((run.prediction.status, run.prediction.reason_code, run.prediction.steps),
                                 (status, reason, steps))
                self.assertEqual((run.prediction.tool_sequence, run.prediction.tool_calls), ([], 0))


if __name__ == "__main__":
    unittest.main()
