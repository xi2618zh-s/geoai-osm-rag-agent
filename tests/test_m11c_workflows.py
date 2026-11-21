"""M11c fixed Workflow (FW-A), template ablation (FW-B) and validator ablations A1-A3."""
from pathlib import Path
import tempfile
import unittest

from src.agent.baselines import (
    DynamicAgentSystem, FixedWorkflowSystem, RawQueryValidatorSystem, STRICT_EMPTY_POLICY,
    TEMPLATE_OPERATIONS, TemplateWorkflowSystem, ValidatorReplanSystem, register_catalogue,
)
from src.agent.benchmark_frontend import interpret
from src.agent.dynamic_planner import plan_request
from src.datasets import LocalDatasetRegistry
from src.evaluation.m11b import load_m11b_tasks, score_m11b
from src.evaluation.m11c import capability_matrix, dev_catalogue, system_input

ROOT = Path(__file__).resolve().parents[1]
TASKS = load_m11b_tasks(ROOT / "benchmarks/data/dev/m11b_tasks_v1.jsonl")
BY_QUERY = {task.query: task for task in TASKS}
NEAREST = "Find 2 nearest synthetic targets for sources with score at least 3"
SEARCH = "Search synthetic sources: category clinic, score gte 0"
EMPTY_SEARCH = "Search synthetic sources: category school, score gte 25"
RANK = "Rank the synthetic regions by unsupported criterion 1"
MISSING = "Find synthetic category 1 without specifying a place"
BUFFER = "Make a fixed 1 metre buffer of synthetic sources"
ONE_PER_TEMPLATE = [
    SEARCH, NEAREST,
    "Find synthetic sources with score at least 4 strictly within regions",
    "Join synthetic regions to sources with score at least 2 by contains",
    "Aggregate synthetic region values by sum after value at least 5",
]


class StructuredFrontend(unittest.TestCase):
    def test_structured_conditions_equal_m07_parse_for_every_executable_dev_task(self):
        with tempfile.TemporaryDirectory(prefix="m11c_struct_") as temp:
            refs = register_catalogue(dev_catalogue(), LocalDatasetRegistry(Path(temp) / "d"))
            checked = 0
            for task in TASKS:
                decision = interpret(task.query, refs, scope="Synthetic dev catalogue")
                if decision.status != "request":
                    continue
                plan = plan_request(decision.request)
                self.assertEqual(plan.status, "plan", task.task_id)
                self.assertEqual(decision.conditions, plan.business_parameters["conditions"], task.task_id)
                checked += 1
            self.assertEqual(checked, 120)

    def test_structured_roles_and_parameters(self):
        with tempfile.TemporaryDirectory(prefix="m11c_struct_") as temp:
            refs = register_catalogue(dev_catalogue(), LocalDatasetRegistry(Path(temp) / "d"))
            nearest = interpret(NEAREST, refs, scope="Synthetic dev catalogue")
            self.assertEqual(nearest.roles, {"source": "sources", "target": "targets"})
            self.assertEqual(nearest.parameters, {"k": 2})
            aggregate = interpret(ONE_PER_TEMPLATE[4], refs, scope="Synthetic dev catalogue")
            self.assertEqual(aggregate.parameters, {"metric": "sum", "metric_field": "value",
                                                    "region_id_field": "region_id",
                                                    "values_region_id_field": "joined_region_id"})


class WorkflowBaselines(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalogue = dev_catalogue()
        cls.fw_a = FixedWorkflowSystem()
        cls.fw_b = TemplateWorkflowSystem()
        cls.validator = ValidatorReplanSystem()

    def run_on(self, system, query):
        return system.run(system_input(BY_QUERY[query]), self.catalogue).prediction

    def test_template_set_is_five_operations_without_single_dataset_buffer(self):
        self.assertEqual(TEMPLATE_OPERATIONS, ("search", "nearest", "within", "spatial_join", "aggregate_by_region"))
        self.assertEqual(FixedWorkflowSystem.templates, ("search",))
        self.assertNotIn("buffer", TEMPLATE_OPERATIONS)

    def test_fixed_workflow_runs_search_only_without_validation(self):
        prediction = self.run_on(self.fw_a, SEARCH)
        self.assertEqual((prediction.status, prediction.steps, prediction.tool_sequence),
                         ("success", ["load_fixture", "execute_m04"], ["filter_attributes"]))
        self.assertEqual(score_m11b(BY_QUERY[SEARCH], prediction).failure_categories, ["required_step_mismatch"])
        declined = self.run_on(self.fw_a, NEAREST)
        self.assertEqual((declined.status, declined.reason_code, declined.tool_calls),
                         ("error", "unsupported_nearest", 0))

    def test_fixed_workflow_has_no_clarification_turn(self):
        missing = self.run_on(self.fw_a, MISSING)
        self.assertEqual((missing.status, missing.reason_code), ("error", "missing_place"))
        self.assertFalse(score_m11b(BY_QUERY[MISSING], missing).passed)
        rank = self.run_on(self.fw_a, RANK)
        self.assertTrue(score_m11b(BY_QUERY[RANK], rank).passed)

    def test_template_workflow_matches_validator_results_on_each_template(self):
        for query in ONE_PER_TEMPLATE:
            template = self.run_on(self.fw_b, query)
            validated = self.run_on(self.validator, query)
            self.assertEqual(template.tool_sequence, validated.tool_sequence, query)
            self.assertEqual((template.feature_ids, template.geometry, template.numeric_values),
                             (validated.feature_ids, validated.geometry, validated.numeric_values), query)
            self.assertEqual(score_m11b(BY_QUERY[query], template).failure_categories,
                             ["required_step_mismatch"], query)
        buffer = self.run_on(self.fw_b, BUFFER)
        self.assertEqual((buffer.status, buffer.reason_code), ("error", "unsupported_buffer"))


class ValidatorAblations(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalogue = dev_catalogue()

    def run_on(self, system, query):
        return system.run(system_input(BY_QUERY[query]), self.catalogue).prediction

    def test_a1_frontend_off_passes_raw_query_to_m07(self):
        raw = RawQueryValidatorSystem()
        for query in (SEARCH, RANK, MISSING):
            prediction = self.run_on(raw, query)
            self.assertEqual((prediction.status, prediction.reason_code), ("clarification", "missing_place"), query)
        self.assertEqual(self.run_on(raw, BUFFER).status, "error")

    def test_a2_strict_empty_policy_turns_valid_empty_answer_into_partial(self):
        strict = ValidatorReplanSystem(policy=STRICT_EMPTY_POLICY, system_id="validator_strict_empty")
        prediction = self.run_on(strict, EMPTY_SEARCH)
        self.assertEqual((prediction.status, prediction.reason_code), ("partial", "M08_EMPTY_UNEXPECTED"))
        self.assertTrue(score_m11b(BY_QUERY[SEARCH], self.run_on(strict, SEARCH)).passed)

    def test_a3_no_replan_is_identical_on_clean_task(self):
        full = self.run_on(ValidatorReplanSystem(), NEAREST)
        no_replan = self.run_on(ValidatorReplanSystem(allow_replan=False, system_id="validator_no_replan"), NEAREST)
        self.assertEqual(full.model_dump(exclude={"latency_ms"}), no_replan.model_dump(exclude={"latency_ms"}))


class MatrixFlags(unittest.TestCase):
    def test_executable_counts_per_configuration_on_public_dev(self):
        systems = [FixedWorkflowSystem(), TemplateWorkflowSystem(), DynamicAgentSystem(),
                   ValidatorReplanSystem(), RawQueryValidatorSystem()]
        rows = capability_matrix(TASKS, dev_catalogue(), systems)
        counts = {system.system_id: sum(row["executable"][system.system_id] for row in rows)
                  for system in systems}
        self.assertEqual(counts, {"fixed_workflow": 24, "template_workflow": 120, "dynamic_agent": 120,
                                  "validator_replan": 120, "validator_no_frontend": 0})
        common = [row for row in rows if all(row["executable"][s] for s in
                                              ("fixed_workflow", "dynamic_agent", "validator_replan"))]
        self.assertEqual(len(common), 24)


if __name__ == "__main__":
    unittest.main()
