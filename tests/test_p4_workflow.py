from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.agent.contracts import WorkflowIntent, WorkflowRequest
from src.agent.planner import (
    PlanCompilationError,
    build_workflow_plan,
    compile_natural_language_plan,
)
from src.agent.tools import (
    ToolPayload,
    ToolRegistry,
    ToolRegistryError,
    ToolSpec,
    _combine_tool,
    _write_tool,
)
from src.agent.workflow import WorkflowEngine, run_agent_workflow
from src.osm.extractor import OSMFeature
from src.rag.retriever import RetrievedChunk


def hit(key: str, value: str, score: float = 1.0) -> RetrievedChunk:
    return RetrievedChunk(
        score=score,
        page_content=f"OSM tag {key}={value}",
        url="https://example.test",
        title=f"{key}={value}",
        key=key,
        value=value,
        retrieval_method="hybrid_rrf",
    )


class FakeRetriever:
    def retrieve(self, query: str, k: int = 3):
        lowered = query.lower()
        mapping = [
            ("caf", ("amenity", "cafe")),
            ("restaurant", ("amenity", "restaurant")),
            ("terminal", ("aeroway", "terminal")),
            ("aerodrome", ("aeroway", "aerodrome")),
        ]
        for token, tag in mapping:
            if token in lowered:
                return [hit(*tag)]
        return []


def feature(osm_type: str, osm_id: int, **tags) -> OSMFeature:
    return OSMFeature(
        osm_type=osm_type,
        osm_id=osm_id,
        name=None,
        tags=tags,
        geometry={"type": "Point", "coordinates": [13.2, 55.7]},
    )


class PlannerContractTests(unittest.TestCase):
    def test_natural_or_compiles_to_grounded_dependency_order(self):
        plan = compile_natural_language_plan(
            "Find cafes or restaurants in Lund", retriever=FakeRetriever()
        )
        self.assertEqual(plan.intent.place, "Lund")
        self.assertEqual(plan.intent.operation, "union")
        self.assertEqual(
            {(item.key, item.value) for item in plan.intent.filters},
            {("amenity", "cafe"), ("amenity", "restaurant")},
        )
        self.assertEqual(len(plan.decisions), 2)
        self.assertEqual(
            [step.tool for step in plan.steps],
            [
                "geocode_place",
                "clip_osm",
                "extract_tag",
                "extract_tag",
                "combine_features",
                "write_geojson",
            ],
        )

    def test_natural_and_is_rejected_as_ambiguous(self):
        with self.assertRaises(PlanCompilationError) as raised:
            compile_natural_language_plan(
                "Find cafes and restaurants in Lund", retriever=FakeRetriever()
            )
        self.assertEqual(raised.exception.code, "PLAN_OPERATION_AMBIGUOUS")

    def test_duplicate_filters_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "filters must be unique"):
            WorkflowIntent(
                place="Lund",
                operation="union",
                filters=[
                    {"key": "amenity", "value": "cafe"},
                    {"key": "amenity", "value": "cafe"},
                ],
                source="explicit",
            )

    def test_request_mode_forbids_mixed_natural_and_explicit_fields(self):
        with self.assertRaisesRegex(ValueError, "must not include"):
            WorkflowRequest(
                mode="natural_language",
                query="cafes or restaurants in Lund",
                place="Lund",
            )


class ToolRegistryTests(unittest.TestCase):
    def test_registry_rejects_non_allow_listed_tool(self):
        with self.assertRaises(ToolRegistryError) as raised:
            ToolRegistry().invoke(
                "shell",
                {},
                allow_retry=False,
                trace_id="trace",
                step_id="step",
            )
        self.assertEqual(raised.exception.code, "TOOL_NOT_REGISTERED")

    def test_combine_union_deduplicates_and_intersection_requires_all_tags(self):
        shared = feature("node", 1, amenity="cafe", cuisine="restaurant")
        cafe_only = feature("node", 2, amenity="cafe")
        restaurant_only = feature("way", 3, amenity="restaurant")
        base = {
            "filters": [
                {"key": "amenity", "value": "cafe"},
                {"key": "amenity", "value": "restaurant"},
            ],
            "feature_sets": [[shared, cafe_only], [shared, restaurant_only]],
        }
        union = _combine_tool({**base, "operation": "union"})
        intersection = _combine_tool({**base, "operation": "intersection"})
        self.assertEqual(len(union.values["combined"]), 3)
        self.assertEqual(len(intersection.values["combined"]), 1)
        self.assertEqual(
            intersection.values["combined"][0]["matched_filters"],
            ["amenity=cafe", "amenity=restaurant"],
        )

    def test_geojson_writer_preserves_filter_provenance(self):
        combined = [
            {
                "feature": feature("way", 7, amenity="cafe"),
                "matched_filters": ["amenity=cafe"],
            }
        ]
        with tempfile.TemporaryDirectory() as directory:
            with patch("src.agent.tools.OUTPUT_DIR", Path(directory)):
                payload = _write_tool(
                    {
                        "combined": combined,
                        "counts_by_filter": {
                            "amenity=cafe": 1,
                            "amenity=restaurant": 0,
                        },
                        "operation": "union",
                        "filters": [
                            {"key": "amenity", "value": "cafe"},
                            {"key": "amenity", "value": "restaurant"},
                        ],
                        "place": "Lund",
                        "query": "cafes or restaurants",
                    }
                )
            document = json.loads(Path(payload.values["geojson_path"]).read_text("utf-8"))
        self.assertEqual(
            document["features"][0]["properties"]["matched_filters"],
            ["amenity=cafe"],
        )
        self.assertEqual(
            document["metadata"]["workflow"]["counts_by_filter"]["amenity=cafe"],
            1,
        )


def fake_registry(*, fail_clip_once: bool = False, fail_extract: bool = False):
    registry = ToolRegistry()
    state = {"clip_calls": 0}

    def geocode(_arguments):
        return ToolPayload({"bbox": (1.0, 2.0, 3.0, 4.0)}, {"bbox": [1, 2, 3, 4]})

    def clip(_arguments):
        state["clip_calls"] += 1
        if fail_clip_once and state["clip_calls"] == 1:
            raise TimeoutError("temporary clip timeout")
        return ToolPayload(
            {"sub_pbf": Path("fixture.pbf"), "clip_cache_hit": False},
            {"clip_cache_hit": False},
        )

    def extract(arguments):
        if fail_extract:
            raise RuntimeError("broken fixture")
        value = arguments["tag"]["value"]
        item = feature("node", 1 if value == "cafe" else 2, amenity=value)
        return ToolPayload({"features": [item], "tag": f"amenity={value}"}, {"count": 1})

    def combine(arguments):
        items = []
        for index, features in enumerate(arguments["feature_sets"]):
            for item in features:
                items.append(
                    {
                        "feature": item,
                        "matched_filters": [
                            f"{arguments['filters'][index]['key']}="
                            f"{arguments['filters'][index]['value']}"
                        ],
                    }
                )
        return ToolPayload(
            {
                "combined": items,
                "counts_by_filter": {
                    "amenity=cafe": 1,
                    "amenity=restaurant": 1,
                },
                "counts_by_type": {"node": 2, "way": 0, "relation": 0},
            },
            {"deduplicated_count": len(items)},
        )

    def write(arguments):
        return ToolPayload(
            {
                "geojson_path": "workflow.geojson",
                "geojson_filename": "workflow.geojson",
                "count": len(arguments["combined"]),
                "counts_by_type": {"node": len(arguments["combined"]), "way": 0, "relation": 0},
            },
            {"count": len(arguments["combined"])},
        )

    specs = [
        ToolSpec("geocode_place", "test", frozenset({"place", "trace_id"}), frozenset(), ("bbox",), geocode),
        ToolSpec(
            "clip_osm",
            "test",
            frozenset({"bbox", "temp_dir"}),
            frozenset(),
            ("sub_pbf", "clip_cache_hit"),
            clip,
            retryable_exceptions=(TimeoutError,),
            max_attempts=2,
        ),
        ToolSpec(
            "extract_tag",
            "test",
            frozenset({"sub_pbf", "tag", "temp_dir", "step_id"}),
            frozenset(),
            ("features", "tag"),
            extract,
        ),
        ToolSpec(
            "combine_features",
            "test",
            frozenset({"operation", "filters", "feature_sets"}),
            frozenset(),
            ("combined", "counts_by_filter", "counts_by_type"),
            combine,
        ),
        ToolSpec(
            "write_geojson",
            "test",
            frozenset({"combined", "counts_by_filter", "operation", "filters", "place", "query"}),
            frozenset(),
            ("geojson_path", "geojson_filename", "count", "counts_by_type"),
            write,
        ),
    ]
    for spec in specs:
        registry.register(spec)
    return registry


class WorkflowEngineTests(unittest.TestCase):
    def plan(self):
        intent = WorkflowIntent(
            place="Lund",
            operation="union",
            filters=[
                {"key": "amenity", "value": "cafe"},
                {"key": "amenity", "value": "restaurant"},
            ],
            source="explicit",
        )
        return build_workflow_plan("cafes or restaurants", intent)

    def test_engine_records_success_and_retry_recovery(self):
        result = WorkflowEngine(fake_registry(fail_clip_once=True)).execute(
            self.plan(), trace_id="workflow-retry"
        )
        self.assertTrue(result["success"])
        self.assertEqual(result["count"], 2)
        self.assertEqual(result["recovery_count"], 1)
        clip = next(item for item in result["step_records"] if item["step_id"] == "clip_city")
        self.assertEqual(clip["attempts"], 2)
        self.assertTrue(clip["recovered"])
        self.assertTrue(all(item["status"] == "succeeded" for item in result["step_records"]))

    def test_engine_marks_downstream_steps_skipped_after_failure(self):
        result = WorkflowEngine(fake_registry(fail_extract=True)).execute(
            self.plan(), trace_id="workflow-failure"
        )
        self.assertFalse(result["success"])
        self.assertEqual(result["error_code"], "TOOL_EXTRACT_TAG_FAILED")
        statuses = {item["step_id"]: item["status"] for item in result["step_records"]}
        self.assertEqual(statuses["extract_tag_1"], "failed")
        self.assertEqual(statuses["extract_tag_2"], "skipped")
        self.assertEqual(statuses["combine_features"], "skipped")
        self.assertEqual(statuses["write_geojson"], "skipped")

    def test_entrypoint_includes_plan_compilation_in_one_trace(self):
        result = run_agent_workflow(
            "cafes or restaurants",
            place="Lund",
            operation="union",
            filters=[
                {"key": "amenity", "value": "cafe"},
                {"key": "amenity", "value": "restaurant"},
            ],
            trace_id="workflow-complete-trace",
            registry=fake_registry(),
        )
        self.assertTrue(result["success"])
        self.assertEqual(result["trace_id"], "workflow-complete-trace")
        self.assertIn("plan_compilation", result["timings_ms"])
        self.assertGreaterEqual(
            result["timings_ms"]["total"],
            result["timings_ms"]["plan_compilation"],
        )


class WorkflowFlaskTests(unittest.TestCase):
    def setUp(self):
        from app_min import app

        self.client = app.test_client()

    def test_workflow_endpoint_returns_strict_success_contract(self):
        result = {
            "success": True,
            "workflow_id": "workflow-id",
            "trace_id": "trace-id",
            "query": "Find cafes or restaurants in Lund",
            "place": "Lund",
            "operation": "union",
            "chosen_tags": ["amenity=cafe", "amenity=restaurant"],
            "count": 3,
            "counts_by_type": {"node": 2, "way": 1, "relation": 0},
            "matched_counts_by_tag": {"amenity=cafe": 1, "amenity=restaurant": 2},
            "geojson_filename": "workflow.geojson",
            "plan": {"version": "1.0"},
            "step_records": [],
            "recovery_count": 0,
            "clip_cache_hit": True,
            "timings_ms": {"total": 10.0},
        }
        with patch("app_min.run_agent_workflow", return_value=result):
            response = self.client.post(
                "/workflow",
                json={
                    "mode": "natural_language",
                    "query": "Find cafes or restaurants in Lund",
                },
                headers={"X-Trace-ID": "trace-id"},
            )
        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertEqual(body["status"], "success")
        self.assertEqual(body["chosen_tags"], result["chosen_tags"])
        self.assertEqual(body["geojson_url"], "/output/workflow.geojson")


if __name__ == "__main__":
    unittest.main()
