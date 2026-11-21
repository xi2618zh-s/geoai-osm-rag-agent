"""M07 grammar m07.2: additive threshold, multi-condition and pre-filter productions.

Added in the M11c session (ADR-0018). Original m07.1 commands must plan exactly
as before; new productions only cover inputs that were previously unsupported.
Execution parity is checked against direct M04 invocations, never against M11b
benchmark references.
"""
import json
from pathlib import Path
import tempfile
import unittest

from src.agent.dynamic_planner import (
    M07PlanRequest, PlanError, plan_request, validate_model_plan_json,
)
from src.agent.validation import ValidationPolicy, execute_validated_plan
from src.datasets import LocalDatasetRegistry
from src.tools import (
    ANALYSIS_TOOL_REGISTRY, AggregateByRegionInput, AggregationMetric,
    AttributeFilterCondition, FeaturesWithinInput, FilterAttributesInput,
    NearestFeaturesInput, SpatialJoinInput,
)

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "data/fixtures/tools/m04b/v1"
PLACE = "Synthetic catalogue"


def _request(query: str) -> dict:
    return {
        "query": query,
        "datasets": {
            "sources": {"kind": "vector", "source": "m04b-fixture:projected_sources",
                        "crs": "EPSG:3857", "required_fields": ["category", "score"]},
            "targets": {"kind": "vector", "source": "m04b-fixture:projected_targets",
                        "crs": "EPSG:3857", "required_fields": []},
            "regions": {"kind": "vector", "source": "m04b-fixture:projected_regions",
                        "crs": "EPSG:3857", "required_fields": ["region_id"]},
            "values": {"kind": "vector", "source": "m04b-fixture:aggregate_values",
                       "crs": "EPSG:3857", "required_fields": ["joined_region_id", "value"]},
        },
        "place_crs": "EPSG:3857", "analysis_crs": "EPSG:3857", "output_crs": "EPSG:3857",
    }


class BackwardCompatibility(unittest.TestCase):
    def test_frozen_m07_cases_keep_m071_source_version_and_tools(self):
        for name in ("m07_plan_cases_v1.jsonl", "m07_plan_cases_v2.jsonl"):
            for line in (ROOT / "benchmarks/data/dev" / name).read_text("utf-8").splitlines():
                row = json.loads(line)
                result = plan_request(row["request"])
                if result.status == "plan":
                    self.assertEqual(result.plan.source_version, "m07.1", row["case_id"])
                    self.assertEqual([s.tool for s in result.tool_steps], row["expected"]["tools"])
                    self.assertEqual(result.business_parameters, row["expected"]["parameters"])
                else:
                    self.assertEqual(result.error_code, "M07_UNSUPPORTED_TASK")

    def test_single_identifier_equality_still_uses_original_production(self):
        result = plan_request(_request(f"search sources where category = clinic in {PLACE}"))
        self.assertEqual(result.task_class, "filtered_search")
        self.assertEqual(result.plan.source_version, "m07.1")
        self.assertEqual(result.business_parameters,
                         {"role": "sources", "field": "category", "operator": "eq", "value": "clinic"})

    def test_rank_remains_unsupported(self):
        result = plan_request(_request(f"rank regions by region_id desc in {PLACE}"))
        self.assertEqual(result.status, "unsupported")
        self.assertEqual(result.error_code, "M07_UNSUPPORTED_TASK")


class ConditionalSearch(unittest.TestCase):
    def test_multi_condition_search_plans_one_filter_step(self):
        result = plan_request(_request(
            f"search sources where category = clinic and score >= 5 in {PLACE}"))
        self.assertEqual(result.status, "plan")
        self.assertEqual(result.task_class, "conditional_search")
        self.assertEqual(result.plan.source_version, "m07.2")
        self.assertEqual(result.intent.task_type, "search")
        self.assertEqual([s.tool for s in result.tool_steps], ["filter_attributes"])
        self.assertEqual(result.tool_steps[0].parameters, {
            "conditions": [{"field": "category", "operator": "eq", "value": "clinic"},
                           {"field": "score", "operator": "gte", "value": 5}],
            "logic": "all"})
        self.assertEqual(len(result.intent.attribute_filters), 2)
        self.assertEqual(result.business_parameters["conditions"][1],
                         {"field": "score", "operator": "gte", "value": 5})

    def test_operator_symbols_and_number_types(self):
        cases = {"=": "eq", "!=": "ne", ">": "gt", ">=": "gte", "<": "lt", "<=": "lte"}
        for symbol, name in cases.items():
            result = plan_request(_request(f"search sources where score {symbol} 2.5 in {PLACE}"))
            self.assertEqual(result.status, "plan", symbol)
            condition = result.tool_steps[0].parameters["conditions"][0]
            self.assertEqual(condition["operator"], name)
            self.assertIs(type(condition["value"]), float)
        integer = plan_request(_request(f"search sources where score = 5 in {PLACE}"))
        self.assertIs(type(integer.tool_steps[0].parameters["conditions"][0]["value"]), int)

    def test_undeclared_field_is_rejected(self):
        result = plan_request(_request(f"search sources where secret >= 1 and score >= 2 in {PLACE}"))
        self.assertEqual(result.status, "unsupported")
        self.assertEqual(result.error_code, "M07_FIELD_UNKNOWN")

    def test_condition_cap_and_unsafe_values(self):
        many = " and ".join(["score >= 1"] * 9)
        self.assertEqual(plan_request(_request(f"search sources where {many} in {PLACE}")).status,
                         "unsupported")
        for value in ("../x", "C:/x", "1; drop"):
            result = plan_request(_request(f"search sources where score = {value} in {PLACE}"))
            self.assertNotEqual(result.status, "plan", value)

    def test_missing_place_still_clarifies(self):
        result = plan_request(_request("find 2 nearest targets to sources where score >= 1"))
        self.assertEqual(result.status, "clarification")
        self.assertEqual(result.clarification.reason_code, "missing_place")


class PreFilteredOperations(unittest.TestCase):
    def assertPlan(self, query, task_class, operation, tools, filter_input, op_inputs):
        result = plan_request(_request(query))
        self.assertEqual(result.status, "plan", result.error_code)
        self.assertEqual(result.task_class, task_class)
        self.assertEqual(result.intent.task_type, operation)
        self.assertEqual(result.plan.source_version, "m07.2")
        self.assertEqual([s.tool for s in result.tool_steps], tools)
        self.assertEqual(result.tool_steps[0].input_roles, [filter_input])
        self.assertEqual(result.tool_steps[1].input_roles, op_inputs)
        self.assertEqual(result.tool_steps[1].depends_on, ["step_1"])
        validated = validate_model_plan_json(result.model_dump_json(), _request(query))
        self.assertEqual(validated, result)
        return result

    def test_filtered_nearest(self):
        result = self.assertPlan(
            f"find 2 nearest targets to sources where score >= 3 in {PLACE}",
            "filtered_nearest", "nearest", ["filter_attributes", "nearest_features"],
            "sources", ["filtered_sources", "targets"])
        self.assertEqual(result.tool_steps[1].parameters, {"k": 2})
        self.assertEqual(result.business_parameters["filter_role"], "sources")

    def test_filtered_within(self):
        self.assertPlan(
            f"find sources where score >= 4 within regions in {PLACE}",
            "filtered_within", "within", ["filter_attributes", "features_within"],
            "sources", ["filtered_sources", "regions"])

    def test_filtered_spatial_join_filters_right_role(self):
        result = self.assertPlan(
            f"join regions with sources where score >= 2 on contains in {PLACE}",
            "filtered_spatial_join", "spatial_join", ["filter_attributes", "spatial_join"],
            "sources", ["regions", "filtered_sources"])
        self.assertEqual(result.tool_steps[1].parameters, {"predicate": "contains"})

    def test_filtered_aggregate_count_and_field_metrics(self):
        result = self.assertPlan(
            f"aggregate count by region_id from values where value >= 5 into regions using joined_region_id in {PLACE}",
            "filtered_aggregate_by_region", "aggregate_by_region",
            ["filter_attributes", "aggregate_by_region"], "values", ["regions", "filtered_values"])
        self.assertEqual(result.tool_steps[1].parameters["metrics"], [{"operation": "count", "field": None}])
        summed = plan_request(_request(
            f"aggregate sum value by region_id from values where value >= 5 into regions using joined_region_id in {PLACE}"))
        self.assertEqual(summed.tool_steps[1].parameters["metrics"], [{"operation": "sum", "field": "value"}])
        unclear = plan_request(_request(
            f"aggregate mean by region_id from values where value >= 5 into regions using joined_region_id in {PLACE}"))
        self.assertEqual(unclear.status, "clarification")
        self.assertEqual(unclear.clarification.reason_code, "ambiguous_target")

    def test_static_budget_and_whitelist_unchanged(self):
        result = plan_request(_request(f"find 1 nearest targets to sources where score >= 0 in {PLACE}"))
        self.assertTrue(all(step.tool in {"filter_attributes", "nearest_features"} for step in result.tool_steps))
        tampered = json.loads(result.model_dump_json())
        tampered["tool_steps"][0]["tool"] = "reproject_dataset"
        with self.assertRaises(PlanError):
            validate_model_plan_json(json.dumps(tampered), _request(result.intent.query))


class ExecutionParity(unittest.TestCase):
    """New plans run through M08 and equal a direct, independent M04 tool chain."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="m07_ext_")
        self.registry = LocalDatasetRegistry(Path(self.temp.name) / "datasets")
        manifest = json.loads((FIXTURES / "manifest.json").read_text("utf-8"))
        self.refs = {}
        for stem, role in (("projected_sources", "sources"), ("projected_targets", "targets"),
                           ("projected_regions", "regions"), ("aggregate_values", "values")):
            self.refs[role] = self.registry.register_file(
                FIXTURES / f"{stem}.geojson", kind="vector",
                crs=manifest["fixtures"][f"{stem}.geojson"]["crs"],
                source="m04b-fixture", source_version="m04b-v1")

    def tearDown(self):
        self.temp.cleanup()

    def _validated(self, query):
        request = M07PlanRequest.model_validate(_request(query))
        plan = plan_request(request)
        self.assertEqual(plan.status, "plan")
        return execute_validated_plan(
            request, plan, dict(self.refs), self.registry,
            policy=ValidationPolicy(allow_empty=True, max_features=10000))

    def _filter(self, role, field, value):
        return ANALYSIS_TOOL_REGISTRY.invoke("filter_attributes", FilterAttributesInput(
            dataset=self.refs[role], conditions=[AttributeFilterCondition(
                field=field, operator="gte", value=value)]), datasets=self.registry).dataset

    def _records(self, ref):
        return [(item["id"], json.dumps(item["geometry"], sort_keys=True))
                for item in self.registry.read(ref.id).records]

    def test_nearest_within_join_aggregate_match_direct_chain(self):
        cases = [
            (f"find 2 nearest targets to sources where score >= 3 in {PLACE}",
             lambda f: ANALYSIS_TOOL_REGISTRY.invoke("nearest_features", NearestFeaturesInput(
                 dataset=f, target_dataset=self.refs["targets"], k=2), datasets=self.registry),
             ("sources", "score", 3)),
            (f"find sources where score >= 4 within regions in {PLACE}",
             lambda f: ANALYSIS_TOOL_REGISTRY.invoke("features_within", FeaturesWithinInput(
                 dataset=f, container_dataset=self.refs["regions"]), datasets=self.registry),
             ("sources", "score", 4)),
            (f"join regions with sources where score >= 2 on intersects in {PLACE}",
             lambda f: ANALYSIS_TOOL_REGISTRY.invoke("spatial_join", SpatialJoinInput(
                 dataset=self.refs["regions"], right_dataset=f, predicate="intersects"),
                 datasets=self.registry),
             ("sources", "score", 2)),
            (f"aggregate sum value by region_id from values where value >= 5 into regions using joined_region_id in {PLACE}",
             lambda f: ANALYSIS_TOOL_REGISTRY.invoke("aggregate_by_region", AggregateByRegionInput(
                 dataset=self.refs["regions"], values_dataset=f, region_id_field="region_id",
                 values_region_id_field="joined_region_id",
                 metrics=[AggregationMetric(operation="sum", field="value")]), datasets=self.registry),
             ("values", "value", 5)),
        ]
        for query, direct, (role, field, value) in cases:
            outcome = self._validated(query)
            self.assertEqual(outcome.status, "success", (query, outcome.error_code))
            self.assertTrue(outcome.first_pass_success)
            expected = direct(self._filter(role, field, value)).dataset
            self.assertEqual(self._records(outcome.final_dataset), self._records(expected), query)
            self.assertEqual([r.tool for r in outcome.step_records],
                             ["filter_attributes", outcome.step_records[1].tool])

    def test_conditional_search_matches_direct_filter(self):
        outcome = self._validated(f"search sources where category = clinic and score <= 10 in {PLACE}")
        self.assertEqual(outcome.status, "success")
        direct = ANALYSIS_TOOL_REGISTRY.invoke("filter_attributes", FilterAttributesInput(
            dataset=self.refs["sources"], conditions=[
                AttributeFilterCondition(field="category", operator="eq", value="clinic"),
                AttributeFilterCondition(field="score", operator="lte", value=10)]),
            datasets=self.registry).dataset
        self.assertEqual(self._records(outcome.final_dataset), self._records(direct))


if __name__ == "__main__":
    unittest.main()
