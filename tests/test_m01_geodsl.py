from __future__ import annotations

import json
import unittest
from pathlib import Path

from pydantic import ValidationError

from src.agent.contracts import WorkflowIntent
from src.agent.planner import build_workflow_plan
from src.geodsl.adapters import workflow_plan_to_geodsl
from src.geodsl.contracts import (
    AttributeFilter,
    ClarificationRequest,
    DatasetRequirement,
    Distance,
    GeoAnalysisPlan,
    GeoAnalysisStep,
    GeoTaskIntent,
    MetricDefinition,
    OutputRequirement,
    PlaceReference,
    SortDefinition,
    SpatialPredicate,
)


ROOT = Path(__file__).resolve().parents[1]
FIXTURE_DIR = ROOT / "data" / "fixtures" / "geodsl" / "v1"
MODELS = {
    model.__name__: model
    for model in (
        AttributeFilter,
        ClarificationRequest,
        DatasetRequirement,
        Distance,
        GeoAnalysisPlan,
        GeoAnalysisStep,
        GeoTaskIntent,
        MetricDefinition,
        OutputRequirement,
        PlaceReference,
        SortDefinition,
        SpatialPredicate,
    )
}


def load_jsonl(name: str) -> list[dict]:
    rows = []
    with (FIXTURE_DIR / name).open("r", encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, start=1):
            if line.strip():
                row = json.loads(line)
                row["line_number"] = line_number
                rows.append(row)
    return rows


class GeoDSLFixtureTests(unittest.TestCase):
    def test_fixture_inventory_has_at_least_thirty_cases_and_all_models_exist(self):
        rows = load_jsonl("valid.jsonl") + load_jsonl("invalid.jsonl")
        self.assertGreaterEqual(len(rows), 30)
        self.assertEqual(len({row["id"] for row in rows}), len(rows))
        self.assertFalse({row["model"] for row in rows}.difference(MODELS))

    def test_valid_fixtures_validate_and_round_trip(self):
        for row in load_jsonl("valid.jsonl"):
            with self.subTest(id=row["id"], line=row["line_number"]):
                model_type = MODELS[row["model"]]
                instance = model_type.model_validate(row["payload"], strict=True)
                restored = model_type.model_validate_json(instance.model_dump_json())
                self.assertEqual(restored, instance)

    def test_valid_intents_cover_every_mvp_operation(self):
        covered = {
            row["payload"]["task_type"]
            for row in load_jsonl("valid.jsonl")
            if row["model"] == "GeoTaskIntent"
        }
        self.assertEqual(
            covered,
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

    def test_invalid_fixtures_are_rejected(self):
        for row in load_jsonl("invalid.jsonl"):
            with self.subTest(id=row["id"], line=row["line_number"]):
                with self.assertRaises(ValidationError):
                    MODELS[row["model"]].model_validate(row["payload"], strict=True)

    def test_distance_values_normalize_to_meters(self):
        self.assertEqual(Distance(value=500, unit="m").meters, 500.0)
        self.assertEqual(Distance(value=1.5, unit="km").meters, 1500.0)
        self.assertAlmostEqual(Distance(value=2, unit="mi").meters, 3218.688)

    def test_contracts_are_strict_without_caller_flags(self):
        with self.assertRaises(ValidationError):
            Distance(value="500", unit="m")

    def test_versioned_models_publish_json_schema(self):
        plan_schema = GeoAnalysisPlan.model_json_schema()
        clarification_schema = ClarificationRequest.model_json_schema()
        self.assertEqual(plan_schema["properties"]["schema_version"]["const"], "1.0")
        self.assertEqual(
            clarification_schema["properties"]["schema_version"]["const"],
            "1.0",
        )


class LegacyWorkflowAdapterTests(unittest.TestCase):
    def workflow(self, operation: str = "union"):
        intent = WorkflowIntent(
            place="Lund",
            operation=operation,
            filters=[
                {"key": "amenity", "value": "cafe"},
                {"key": "amenity", "value": "restaurant"},
            ],
            source="explicit",
        )
        return build_workflow_plan("Find cafes or restaurants in Lund", intent)

    def test_old_or_workflow_converts_to_search_subset(self):
        converted = workflow_plan_to_geodsl(self.workflow())
        self.assertEqual(converted.schema_version, "1.0")
        self.assertEqual(converted.source, "legacy_workflow")
        self.assertEqual(converted.intent.task_type, "search")
        self.assertEqual(converted.intent.place.name, "Lund")
        self.assertEqual(converted.intent.analysis_crs, "EPSG:4326")
        self.assertEqual(converted.steps[0].operation, "search")
        self.assertEqual(converted.steps[0].filter_logic, "any")
        self.assertEqual(
            {(item.field, item.value) for item in converted.steps[0].attribute_filters},
            {("tags.amenity", "cafe"), ("tags.amenity", "restaurant")},
        )
        self.assertEqual(
            GeoAnalysisPlan.model_validate_json(converted.model_dump_json()),
            converted,
        )

    def test_legacy_intersection_preserves_all_filter_semantics(self):
        converted = workflow_plan_to_geodsl(self.workflow("intersection"))
        self.assertEqual(converted.steps[0].filter_logic, "all")

    def test_adapter_does_not_mutate_legacy_plan(self):
        original = self.workflow()
        before = original.model_dump(mode="json")
        workflow_plan_to_geodsl(original)
        self.assertEqual(original.model_dump(mode="json"), before)


if __name__ == "__main__":
    unittest.main()
