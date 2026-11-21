from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from pydantic import ValidationError

from src.datasets import DatasetLineage, LocalDatasetRegistry
from src.tools import (
    AggregateByRegionInput,
    AggregationMetric,
    AnalysisToolError,
    AnalysisToolRegistry,
    AttributeFilterCondition,
    BufferFeaturesInput,
    FeaturesWithinInput,
    FeaturesWithinOutput,
    FilterAttributesInput,
    NearestFeaturesInput,
    RepairGeometryInput,
    ReprojectDatasetInput,
    SpatialJoinInput,
    ToolLimits,
    ToolRegistration,
    ToolSpec,
    build_analysis_tool_registry,
)


ROOT = Path(__file__).resolve().parents[1]
FIXTURE_DIR = ROOT / "data" / "fixtures" / "tools" / "m04b" / "v1"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(65536), b""):
            digest.update(block)
    return digest.hexdigest()


class M04bFixtureTests(unittest.TestCase):
    def test_manifest_matches_fixture_bytes_and_rows(self):
        manifest = json.loads((FIXTURE_DIR / "manifest.json").read_text("utf-8"))
        self.assertEqual(manifest["schema_version"], 1)
        self.assertEqual(manifest["fixture_version"], "m04b-v1")
        for filename, expected in manifest["fixtures"].items():
            with self.subTest(filename=filename):
                path = FIXTURE_DIR / filename
                document = json.loads(path.read_text("utf-8"))
                self.assertEqual(sha256(path), expected["sha256"])
                self.assertEqual(len(document["features"]), expected["row_count"])


class SpatialToolTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="m04b_tools_")
        self.root = Path(self.temp.name)
        self.datasets = LocalDatasetRegistry(self.root / "registry")
        self.tools = build_analysis_tool_registry()

    def tearDown(self):
        self.temp.cleanup()

    def register(self, filename: str, *, crs: str | None = None):
        manifest = json.loads((FIXTURE_DIR / "manifest.json").read_text("utf-8"))
        fixture = manifest["fixtures"][filename]
        return self.datasets.register_file(
            FIXTURE_DIR / filename,
            kind="vector",
            crs=crs or fixture["crs"],
            source="m04b-fixture",
            source_version="m04b-v1",
        )

    def register_document(self, name: str, document: dict, *, crs: str = "EPSG:3857"):
        path = self.root / name
        path.write_text(
            json.dumps(document, ensure_ascii=False, sort_keys=True),
            encoding="utf-8",
        )
        return self.datasets.register_file(
            path,
            kind="vector",
            crs=crs,
            source="m04b-test",
            source_version="v1",
        )

    def invoke(self, name: str, contract):
        return self.tools.invoke(name, contract, datasets=self.datasets)

    def records(self, ref):
        return list(self.datasets.read(ref.id).records)

    def assert_error_code(self, expected: str, callable_, *args, **kwargs):
        with self.assertRaises(AnalysisToolError) as raised:
            callable_(*args, **kwargs)
        self.assertEqual(raised.exception.code, expected)

    def assert_derived(self, result, parents: list, operation: str):
        self.assertNotIn(result.dataset.id, [item.id for item in parents])
        self.assertEqual(self.datasets.get(result.dataset.id), result.dataset)
        self.assertEqual(result.dataset.lifecycle, "temporary")
        self.assertIsNotNone(result.dataset.expires_at)
        ttl = (
            result.dataset.expires_at - result.dataset.created_at
        ).total_seconds()
        self.assertEqual(ttl, 86400)
        lineage = result.dataset.lineage[-1]
        self.assertEqual(lineage.operation, operation)
        self.assertEqual(lineage.parent_ids, [item.id for item in parents])
        self.assertRegex(lineage.parameters_hash or "", r"^sha256:[a-f0-9]{64}$")
        self.assertEqual(result.metadata.lineage, lineage)

    def test_registry_lists_quality_and_eight_spatial_tools(self):
        names = [item.name for item in self.tools.describe()]
        self.assertEqual(
            names,
            [
                "aggregate_by_region",
                "buffer_features",
                "features_within",
                "filter_attributes",
                "inspect_dataset_schema",
                "nearest_features",
                "repair_geometry",
                "reproject_dataset",
                "spatial_join",
                "validate_geometry",
            ],
        )
        self.assertTrue(all(item.mutates_input is False for item in self.tools.describe()))

    def test_new_contracts_are_strict_and_versioned(self):
        source = self.register("projected_sources.geojson")
        with self.assertRaises(ValidationError):
            BufferFeaturesInput.model_validate(
                {
                    "schema_version": "1.0",
                    "dataset": source.model_dump(mode="json"),
                    "distance_m": "5",
                }
            )
        with self.assertRaises(ValidationError):
            NearestFeaturesInput.model_validate(
                {
                    "dataset": source.model_dump(mode="json"),
                    "target_dataset": source.model_dump(mode="json"),
                    "k": 1,
                    "extra": True,
                }
            )

    def test_dataset_registry_accepts_explicit_derived_lineage(self):
        source = self.register("projected_sources.geojson")
        lineage = DatasetLineage(
            operation="test_derived",
            parent_ids=[source.id],
            timestamp=datetime.now(timezone.utc),
            parameters_hash="sha256:" + ("a" * 64),
        )
        result = self.datasets.register_file(
            FIXTURE_DIR / "empty.geojson",
            kind="vector",
            crs="EPSG:3857",
            source="m04b-test",
            source_version="v1",
            temporary=True,
            ttl_seconds=86400,
            lineage=[lineage],
        )
        self.assertEqual(result.lineage, [lineage])

    def test_registry_applies_limits_cumulatively_to_all_inputs(self):
        source = self.register("projected_sources.geojson")
        target = self.register("projected_targets.geojson")
        limited = build_analysis_tool_registry(max_input_features=6)
        self.assert_error_code(
            "ANALYSIS_TOOL_LIMIT_EXCEEDED",
            limited.invoke,
            "nearest_features",
            NearestFeaturesInput(dataset=source, target_dataset=target, k=1),
            datasets=self.datasets,
        )
        byte_limited = build_analysis_tool_registry(
            max_input_bytes=source.size_bytes + target.size_bytes - 1
        )
        self.assert_error_code(
            "ANALYSIS_TOOL_LIMIT_EXCEEDED",
            byte_limited.invoke,
            "nearest_features",
            NearestFeaturesInput(dataset=source, target_dataset=target, k=1),
            datasets=self.datasets,
        )

    def test_registry_verifies_every_input_remains_unmodified(self):
        source = self.register("projected_sources.geojson")
        regions = self.register("projected_regions.geojson")
        malicious = AnalysisToolRegistry()

        def mutate_secondary(contract, datasets):
            result = self.tools.invoke(
                "features_within", contract, datasets=datasets
            )
            datasets.resolve_path(contract.container_dataset.id).write_text(
                "{}", encoding="utf-8"
            )
            return result

        malicious.register(
            ToolRegistration(
                spec=ToolSpec(
                    name="dual_mutation_probe",
                    description="Test-only multi-input mutation probe",
                    input_contract="FeaturesWithinInput.v1",
                    output_contract="FeaturesWithinOutput.v1",
                    supported_dataset_kinds=["vector"],
                    produces_dataset=True,
                    mutates_input=False,
                    deterministic=True,
                    limits=ToolLimits(
                        max_input_features=100,
                        max_input_bytes=100000,
                        max_output_bytes=100000,
                    ),
                ),
                input_model=FeaturesWithinInput,
                output_model=FeaturesWithinOutput,
                handler=mutate_secondary,
            )
        )
        self.assert_error_code(
            "ANALYSIS_TOOL_INPUT_MUTATED",
            malicious.invoke,
            "dual_mutation_probe",
            FeaturesWithinInput(dataset=source, container_dataset=regions),
            datasets=self.datasets,
        )

    def test_filter_attributes_success_boundary_and_input_immutability(self):
        source = self.register("projected_sources.geojson")
        before = self.datasets.resolve_path(source.id).read_bytes()
        result = self.invoke(
            "filter_attributes",
            FilterAttributesInput(
                dataset=source,
                conditions=[
                    AttributeFilterCondition(
                        field="category", operator="eq", value="clinic"
                    ),
                    AttributeFilterCondition(field="score", operator="gte", value=10),
                ],
                logic="all",
            ),
        )
        records = self.records(result.dataset)
        self.assertEqual([item["id"] for item in records], ["source-1"])
        self.assertEqual(result.matched_feature_count, 1)
        self.assertEqual(self.datasets.resolve_path(source.id).read_bytes(), before)
        self.assert_derived(result, [source], "filter_attributes")

    def test_filter_attributes_empty_null_and_invalid_fields(self):
        empty = self.register("empty.geojson")
        result = self.invoke(
            "filter_attributes",
            FilterAttributesInput(dataset=empty, conditions=[], logic="all"),
        )
        self.assertEqual(result.dataset.row_count, 0)
        self.assertIn("dataset_empty", result.metadata.warnings)

        source = self.register("projected_sources.geojson")
        null_result = self.invoke(
            "filter_attributes",
            FilterAttributesInput(
                dataset=source,
                conditions=[
                    AttributeFilterCondition(
                        field="score", operator="is_null", value=None
                    )
                ],
            ),
        )
        self.assertEqual([item["id"] for item in self.records(null_result.dataset)], ["source-3"])
        self.assert_error_code(
            "ANALYSIS_TOOL_FIELD_MISSING",
            self.invoke,
            "filter_attributes",
            FilterAttributesInput(
                dataset=source,
                conditions=[
                    AttributeFilterCondition(field="missing", operator="eq", value=1)
                ],
            ),
        )
        self.assert_error_code(
            "ANALYSIS_TOOL_FIELD_TYPE_INVALID",
            self.invoke,
            "filter_attributes",
            FilterAttributesInput(
                dataset=source,
                conditions=[
                    AttributeFilterCondition(field="category", operator="gt", value=1)
                ],
            ),
        )

    def test_reproject_dataset_success_empty_equivalent_and_invalid_crs(self):
        source = self.register("geographic_points.geojson")
        result = self.invoke(
            "reproject_dataset",
            ReprojectDatasetInput(dataset=source, target_crs="EPSG:3857"),
        )
        point = self.records(result.dataset)[0]["geometry"]["coordinates"]
        self.assertTrue(math.isclose(point[0], 1468400, rel_tol=0.01))
        self.assertTrue(math.isclose(point[1], 7493000, rel_tol=0.01))
        self.assertEqual(result.dataset.crs, "EPSG:3857")
        self.assert_derived(result, [source], "reproject_dataset")

        empty = self.register("empty.geojson", crs="EPSG:4326")
        empty_result = self.invoke(
            "reproject_dataset",
            ReprojectDatasetInput(dataset=empty, target_crs="EPSG:3857"),
        )
        self.assertEqual(empty_result.dataset.row_count, 0)
        self.assertIn("dataset_empty", empty_result.metadata.warnings)

        equivalent = self.invoke(
            "reproject_dataset",
            ReprojectDatasetInput(dataset=source, target_crs="EPSG:4326"),
        )
        self.assertIn("source_and_target_crs_equivalent", equivalent.metadata.warnings)

        invalid = self.register("projected_sources.geojson", crs="EPSG:999999")
        self.assert_error_code(
            "ANALYSIS_TOOL_CRS_INVALID",
            self.invoke,
            "reproject_dataset",
            ReprojectDatasetInput(dataset=invalid, target_crs="EPSG:3857"),
        )

    def test_repair_geometry_success_empty_valid_unchanged_and_failure(self):
        invalid = self.register("invalid_bow_tie.geojson")
        result = self.invoke(
            "repair_geometry", RepairGeometryInput(dataset=invalid)
        )
        repaired = self.records(result.dataset)[0]
        self.assertEqual(repaired["id"], "invalid-1")
        self.assertEqual(repaired["properties"], {"name": "Bow Tie"})
        self.assertEqual(result.repaired_geometry_count, 1)
        self.assert_derived(result, [invalid], "repair_geometry")

        valid = self.register("projected_sources.geojson")
        valid_result = self.invoke(
            "repair_geometry", RepairGeometryInput(dataset=valid)
        )
        self.assertEqual(self.records(valid_result.dataset), self.records(valid))
        self.assertEqual(valid_result.repaired_geometry_count, 0)

        empty = self.register("empty.geojson")
        empty_result = self.invoke(
            "repair_geometry", RepairGeometryInput(dataset=empty)
        )
        self.assertEqual(empty_result.dataset.row_count, 0)
        self.assertIn("dataset_empty", empty_result.metadata.warnings)

        with patch("src.tools.spatial.make_valid") as mocked:
            from shapely.geometry import GeometryCollection

            mocked.return_value = GeometryCollection()
            self.assert_error_code(
                "ANALYSIS_TOOL_GEOMETRY_REPAIR_FAILED",
                self.invoke,
                "repair_geometry",
                RepairGeometryInput(dataset=invalid),
            )

    def test_buffer_features_success_feet_empty_and_distance_guards(self):
        source = self.register("projected_sources.geojson")
        result = self.invoke(
            "buffer_features",
            BufferFeaturesInput(dataset=source, distance_m=5.0, quad_segs=4),
        )
        self.assertEqual(result.dataset.row_count, source.row_count)
        self.assertTrue(
            all(item["geometry"]["type"] == "Polygon" for item in self.records(result.dataset))
        )
        self.assert_derived(result, [source], "buffer_features")

        feet = self.register("projected_sources.geojson", crs="EPSG:2263")
        feet_result = self.invoke(
            "buffer_features",
            BufferFeaturesInput(dataset=feet, distance_m=0.3048006096012192),
        )
        first_bounds = self.records(feet_result.dataset)[0]["geometry"]["coordinates"][0]
        xs = [point[0] for point in first_bounds]
        self.assertTrue(math.isclose(max(xs), 1.0, rel_tol=1e-6))

        empty = self.register("empty.geojson")
        empty_result = self.invoke(
            "buffer_features", BufferFeaturesInput(dataset=empty, distance_m=1.0)
        )
        self.assertEqual(empty_result.dataset.row_count, 0)

        geographic = self.register("geographic_points.geojson")
        self.assert_error_code(
            "ANALYSIS_TOOL_GEOGRAPHIC_DISTANCE_UNSUPPORTED",
            self.invoke,
            "buffer_features",
            BufferFeaturesInput(dataset=geographic, distance_m=5.0),
        )
        compound = self.register("projected_sources.geojson", crs="EPSG:7415")
        self.assert_error_code(
            "ANALYSIS_TOOL_CRS_UNIT_UNSUPPORTED",
            self.invoke,
            "buffer_features",
            BufferFeaturesInput(dataset=compound, distance_m=5.0),
        )
        invalid = self.register("invalid_bow_tie.geojson")
        self.assert_error_code(
            "ANALYSIS_TOOL_GEOMETRY_INVALID",
            self.invoke,
            "buffer_features",
            BufferFeaturesInput(dataset=invalid, distance_m=5.0),
        )

    def test_nearest_features_stable_k_boundary_empty_and_guards(self):
        source = self.register("projected_sources.geojson")
        target = self.register("projected_targets.geojson")
        result = self.invoke(
            "nearest_features",
            NearestFeaturesInput(dataset=source, target_dataset=target, k=2),
        )
        records = self.records(result.dataset)
        self.assertEqual(len(records), 8)
        self.assertEqual(
            [
                (item["properties"]["source_feature_id"], item["properties"]["target_feature_id"])
                for item in records[:2]
            ],
            [("source-1", "target-1"), ("source-1", "target-2")],
        )
        self.assertTrue(math.isclose(records[0]["properties"]["distance_m"], 5.0))
        self.assertIn("left_category", records[0]["properties"])
        self.assertIn("right_kind", records[0]["properties"])
        self.assert_derived(result, [source, target], "nearest_features")

        all_targets = self.invoke(
            "nearest_features",
            NearestFeaturesInput(dataset=source, target_dataset=target, k=10),
        )
        self.assertEqual(all_targets.dataset.row_count, 12)

        empty = self.register("empty.geojson")
        empty_result = self.invoke(
            "nearest_features",
            NearestFeaturesInput(dataset=source, target_dataset=empty, k=1),
        )
        self.assertEqual(empty_result.dataset.row_count, 0)
        self.assertIn("target_dataset_empty", empty_result.metadata.warnings)

        geographic = self.register("geographic_points.geojson")
        geographic_target = self.register("geographic_points.geojson")
        self.assert_error_code(
            "ANALYSIS_TOOL_GEOGRAPHIC_DISTANCE_UNSUPPORTED",
            self.invoke,
            "nearest_features",
            NearestFeaturesInput(
                dataset=geographic,
                target_dataset=geographic_target,
                k=1,
            ),
        )

    def test_dual_dataset_crs_mismatch_is_explicit(self):
        source = self.register("projected_sources.geojson")
        regions = self.register("projected_regions.geojson", crs="EPSG:4326")
        self.assert_error_code(
            "ANALYSIS_TOOL_CRS_MISMATCH",
            self.invoke,
            "features_within",
            FeaturesWithinInput(dataset=source, container_dataset=regions),
        )

    def test_features_within_strict_boundary_no_duplicates_empty_and_invalid(self):
        source = self.register("projected_sources.geojson")
        regions = self.register("projected_regions.geojson")
        result = self.invoke(
            "features_within",
            FeaturesWithinInput(dataset=source, container_dataset=regions),
        )
        self.assertEqual(
            [item["id"] for item in self.records(result.dataset)],
            ["source-1", "source-2", "source-3"],
        )
        self.assertEqual(result.matched_feature_count, 3)
        self.assert_derived(result, [source, regions], "features_within")

        empty = self.register("empty.geojson")
        empty_result = self.invoke(
            "features_within",
            FeaturesWithinInput(dataset=empty, container_dataset=regions),
        )
        self.assertEqual(empty_result.dataset.row_count, 0)

        invalid = self.register("invalid_bow_tie.geojson")
        self.assert_error_code(
            "ANALYSIS_TOOL_GEOMETRY_INVALID",
            self.invoke,
            "features_within",
            FeaturesWithinInput(dataset=source, container_dataset=invalid),
        )

    def test_spatial_join_inner_namespaces_order_predicates_and_empty(self):
        regions = self.register("projected_regions.geojson")
        source = self.register("projected_sources.geojson")
        result = self.invoke(
            "spatial_join",
            SpatialJoinInput(
                dataset=regions,
                right_dataset=source,
                predicate="intersects",
            ),
        )
        records = self.records(result.dataset)
        self.assertEqual(result.matched_pair_count, 4)
        self.assertEqual(
            [
                (item["properties"]["left_feature_id"], item["properties"]["right_feature_id"])
                for item in records
            ],
            [
                ("region-1", "source-1"),
                ("region-1", "source-2"),
                ("region-1", "source-boundary"),
                ("region-2", "source-3"),
            ],
        )
        self.assertEqual(records[0]["geometry"]["type"], "Polygon")
        self.assertIn("left_region_id", records[0]["properties"])
        self.assertIn("right_category", records[0]["properties"])
        self.assert_derived(result, [regions, source], "spatial_join")

        empty = self.register("empty.geojson")
        empty_result = self.invoke(
            "spatial_join",
            SpatialJoinInput(dataset=regions, right_dataset=empty, predicate="within"),
        )
        self.assertEqual(empty_result.dataset.row_count, 0)

        with self.assertRaises(ValidationError):
            SpatialJoinInput(
                dataset=regions, right_dataset=source, predicate="touches"
            )

    def test_aggregate_by_region_preserves_zero_nulls_and_stable_fields(self):
        regions = self.register("projected_regions.geojson")
        values = self.register("aggregate_values.geojson")
        result = self.invoke(
            "aggregate_by_region",
            AggregateByRegionInput(
                dataset=regions,
                values_dataset=values,
                region_id_field="region_id",
                values_region_id_field="joined_region_id",
                metrics=[
                    AggregationMetric(operation="count"),
                    AggregationMetric(operation="sum", field="value"),
                    AggregationMetric(operation="mean", field="value"),
                    AggregationMetric(operation="min", field="value"),
                    AggregationMetric(operation="max", field="value"),
                ],
            ),
        )
        records = self.records(result.dataset)
        by_region = {item["properties"]["region_id"]: item["properties"] for item in records}
        self.assertEqual(by_region["r1"]["agg_count"], 2)
        self.assertEqual(by_region["r1"]["agg_sum_value"], 30)
        self.assertEqual(by_region["r1"]["agg_mean_value"], 15)
        self.assertEqual(by_region["r1"]["agg_min_value"], 10)
        self.assertEqual(by_region["r1"]["agg_max_value"], 20)
        self.assertEqual(by_region["r2"]["agg_count"], 1)
        self.assertIsNone(by_region["r2"]["agg_sum_value"])
        self.assertEqual(by_region["r3"]["agg_count"], 0)
        self.assertIsNone(by_region["r3"]["agg_mean_value"])
        self.assertIn("unmatched_value_rows", result.metadata.warnings)
        self.assert_derived(result, [regions, values], "aggregate_by_region")

    def test_aggregate_empty_invalid_fields_types_and_collision(self):
        regions = self.register("projected_regions.geojson")
        empty = self.register("empty.geojson")
        result = self.invoke(
            "aggregate_by_region",
            AggregateByRegionInput(
                dataset=regions,
                values_dataset=empty,
                region_id_field="region_id",
                values_region_id_field="joined_region_id",
                metrics=[AggregationMetric(operation="count")],
            ),
        )
        self.assertEqual(
            [item["properties"]["agg_count"] for item in self.records(result.dataset)],
            [0, 0, 0],
        )

        values = self.register("aggregate_values.geojson")
        self.assert_error_code(
            "ANALYSIS_TOOL_FIELD_MISSING",
            self.invoke,
            "aggregate_by_region",
            AggregateByRegionInput(
                dataset=regions,
                values_dataset=values,
                region_id_field="missing",
                values_region_id_field="joined_region_id",
                metrics=[AggregationMetric(operation="count")],
            ),
        )
        self.assert_error_code(
            "ANALYSIS_TOOL_FIELD_TYPE_INVALID",
            self.invoke,
            "aggregate_by_region",
            AggregateByRegionInput(
                dataset=regions,
                values_dataset=values,
                region_id_field="region_id",
                values_region_id_field="joined_region_id",
                metrics=[AggregationMetric(operation="sum", field="joined_region_id")],
            ),
        )

        document = json.loads((FIXTURE_DIR / "projected_regions.geojson").read_text("utf-8"))
        for feature in document["features"]:
            feature["properties"]["agg_count"] = 99
        collision = self.register_document("collision.geojson", document)
        self.assert_error_code(
            "ANALYSIS_TOOL_FIELD_TYPE_INVALID",
            self.invoke,
            "aggregate_by_region",
            AggregateByRegionInput(
                dataset=collision,
                values_dataset=values,
                region_id_field="region_id",
                values_region_id_field="joined_region_id",
                metrics=[AggregationMetric(operation="count")],
            ),
        )

    def test_output_file_byte_limit_releases_derived_dataset(self):
        source = self.register("projected_sources.geojson")
        limited = build_analysis_tool_registry(max_output_bytes=200)
        before_ids = {item.id for item in self.datasets.list()}
        self.assert_error_code(
            "ANALYSIS_TOOL_OUTPUT_LIMIT_EXCEEDED",
            limited.invoke,
            "buffer_features",
            BufferFeaturesInput(dataset=source, distance_m=5.0),
            datasets=self.datasets,
        )
        self.assertEqual({item.id for item in self.datasets.list()}, before_ids)


if __name__ == "__main__":
    unittest.main()
