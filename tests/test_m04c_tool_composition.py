from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path
import tempfile
import unittest

from shapely.geometry import shape

from src.datasets import LocalDatasetRegistry
from src.tools import (
    AggregateByRegionInput,
    AggregationMetric,
    AnalysisToolError,
    BufferFeaturesInput,
    ReprojectDatasetInput,
    SpatialJoinInput,
    build_analysis_tool_registry,
)


ROOT = Path(__file__).resolve().parents[1]
FIXTURE_DIR = ROOT / "data" / "fixtures" / "tools" / "m04c" / "v1"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(65536), b""):
            digest.update(block)
    return digest.hexdigest()


class M04cFixtureTests(unittest.TestCase):
    def test_manifest_matches_all_inputs_and_references(self):
        manifest = json.loads((FIXTURE_DIR / "manifest.json").read_text("utf-8"))
        self.assertEqual(manifest["schema_version"], 1)
        self.assertEqual(manifest["fixture_version"], "m04c-v1")
        for filename, expected in manifest["fixtures"].items():
            with self.subTest(filename=filename):
                path = FIXTURE_DIR / filename
                self.assertEqual(sha256(path), expected["sha256"])
                if expected["row_count"] is not None:
                    document = json.loads(path.read_text("utf-8"))
                    self.assertEqual(len(document["features"]), expected["row_count"])


class ToolCompositionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="m04c_composition_")
        self.datasets = LocalDatasetRegistry(Path(self.temp.name) / "registry")
        self.tools = build_analysis_tool_registry()
        self.summary = json.loads(
            (FIXTURE_DIR / "expected_summary.json").read_text("utf-8")
        )
        self.expected_final = json.loads(
            (FIXTURE_DIR / "expected_final.geojson").read_text("utf-8")
        )

    def tearDown(self):
        self.temp.cleanup()

    def register(self, filename: str):
        manifest = json.loads((FIXTURE_DIR / "manifest.json").read_text("utf-8"))
        entry = manifest["fixtures"][filename]
        return self.datasets.register_file(
            FIXTURE_DIR / filename,
            kind="vector",
            crs=entry["crs"],
            source="m04c-fixture",
            source_version="m04c-v1",
        )

    def invoke(self, name: str, contract, *, tools=None):
        return (tools or self.tools).invoke(name, contract, datasets=self.datasets)

    def records(self, ref):
        return list(self.datasets.read(ref.id).records)

    def assert_error_code(self, expected: str, callable_, *args, **kwargs):
        with self.assertRaises(AnalysisToolError) as raised:
            callable_(*args, **kwargs)
        self.assertEqual(raised.exception.code, expected)

    def run_chain(self, facilities, regions, *, tools=None):
        registry = tools or self.tools
        reprojected = self.invoke(
            "reproject_dataset",
            ReprojectDatasetInput(dataset=facilities, target_crs="EPSG:3857"),
            tools=registry,
        )
        buffered = self.invoke(
            "buffer_features",
            BufferFeaturesInput(
                dataset=reprojected.dataset,
                distance_m=75.0,
                quad_segs=8,
            ),
            tools=registry,
        )
        joined = self.invoke(
            "spatial_join",
            SpatialJoinInput(
                dataset=regions,
                right_dataset=buffered.dataset,
                predicate="intersects",
            ),
            tools=registry,
        )
        aggregated = self.invoke(
            "aggregate_by_region",
            AggregateByRegionInput(
                dataset=regions,
                values_dataset=joined.dataset,
                region_id_field="region_id",
                values_region_id_field="left_region_id",
                metrics=[
                    AggregationMetric(operation="count"),
                    AggregationMetric(operation="sum", field="right_weight"),
                    AggregationMetric(operation="mean", field="right_weight"),
                ],
            ),
            tools=registry,
        )
        return {
            "reproject": reprojected,
            "buffer": buffered,
            "join": joined,
            "aggregate": aggregated,
        }

    def assert_geojson_matches_reference(self, actual_records, expected_document):
        expected_records = expected_document["features"]
        self.assertEqual(len(actual_records), len(expected_records))
        geometry_tolerance = self.summary["final_geometry_hausdorff_tolerance"]
        area_tolerance = self.summary["final_area_abs_tolerance"]
        for actual, expected in zip(actual_records, expected_records):
            self.assertEqual(actual.get("id"), expected.get("id"))
            self.assertEqual(actual["properties"], expected["properties"])
            actual_geometry = shape(actual["geometry"])
            expected_geometry = shape(expected["geometry"])
            self.assertLessEqual(
                actual_geometry.hausdorff_distance(expected_geometry),
                geometry_tolerance,
            )
            self.assertLessEqual(
                abs(actual_geometry.area - expected_geometry.area),
                area_tolerance,
            )

    def test_reference_chain_matches_intermediate_and_final_expectations(self):
        facilities = self.register("facilities_wgs84.geojson")
        regions = self.register("regions_3857.geojson")
        facilities_before = self.datasets.resolve_path(facilities.id).read_bytes()
        regions_before = self.datasets.resolve_path(regions.id).read_bytes()

        results = self.run_chain(facilities, regions)

        reprojected_records = self.records(results["reproject"].dataset)
        expected_coordinates = self.summary["reprojected_coordinates"]
        coordinate_tolerance = self.summary["coordinate_abs_tolerance"]
        for feature in reprojected_records:
            actual = feature["geometry"]["coordinates"]
            expected = expected_coordinates[feature["id"]]
            self.assertTrue(
                math.isclose(actual[0], expected[0], abs_tol=coordinate_tolerance)
            )
            self.assertTrue(
                math.isclose(actual[1], expected[1], abs_tol=coordinate_tolerance)
            )

        for feature in self.records(results["buffer"].dataset):
            self.assertTrue(
                math.isclose(
                    shape(feature["geometry"]).area,
                    self.summary["buffer_area"],
                    abs_tol=self.summary["buffer_area_abs_tolerance"],
                )
            )

        join_pairs = [
            [
                item["properties"]["left_feature_id"],
                item["properties"]["right_feature_id"],
            ]
            for item in self.records(results["join"].dataset)
        ]
        self.assertEqual(join_pairs, self.summary["join_pairs"])
        self.assert_geojson_matches_reference(
            self.records(results["aggregate"].dataset), self.expected_final
        )

        expected_parents = {
            "reproject": [facilities.id],
            "buffer": [results["reproject"].dataset.id],
            "join": [regions.id, results["buffer"].dataset.id],
            "aggregate": [regions.id, results["join"].dataset.id],
        }
        for name, result in results.items():
            lineage = result.dataset.lineage[-1]
            self.assertEqual(lineage.parent_ids, expected_parents[name])
            self.assertEqual(result.metadata.lineage, lineage)
            self.assertRegex(lineage.parameters_hash or "", r"^sha256:[a-f0-9]{64}$")
            self.assertEqual(result.dataset.lifecycle, "temporary")
            self.assertEqual(
                (result.dataset.expires_at - result.dataset.created_at).total_seconds(),
                86400,
            )

        self.assertEqual(
            self.datasets.resolve_path(facilities.id).read_bytes(), facilities_before
        )
        self.assertEqual(self.datasets.resolve_path(regions.id).read_bytes(), regions_before)

    def test_empty_source_propagates_to_zero_aggregates(self):
        empty = self.register("empty_wgs84.geojson")
        regions = self.register("regions_3857.geojson")
        results = self.run_chain(empty, regions)
        records = self.records(results["aggregate"].dataset)
        self.assertEqual([item["properties"]["agg_count"] for item in records], [0, 0, 0])
        self.assertTrue(
            all(item["properties"]["agg_sum_right_weight"] is None for item in records)
        )
        self.assertTrue(
            all(item["properties"]["agg_mean_right_weight"] is None for item in records)
        )
        self.assertIn("dataset_empty", results["reproject"].metadata.warnings)
        self.assertIn("dataset_empty", results["buffer"].metadata.warnings)
        self.assertIn("right_dataset_empty", results["join"].metadata.warnings)
        self.assertIn("values_dataset_empty", results["aggregate"].metadata.warnings)

    def test_chain_requires_explicit_reprojection_before_meter_buffer(self):
        facilities = self.register("facilities_wgs84.geojson")
        self.assert_error_code(
            "ANALYSIS_TOOL_GEOGRAPHIC_DISTANCE_UNSUPPORTED",
            self.invoke,
            "buffer_features",
            BufferFeaturesInput(dataset=facilities, distance_m=75.0),
        )

    def test_invalid_geometry_stops_without_buffer_output(self):
        invalid = self.register("invalid_wgs84.geojson")
        reprojected = self.invoke(
            "reproject_dataset",
            ReprojectDatasetInput(dataset=invalid, target_crs="EPSG:3857"),
        )
        before_ids = {item.id for item in self.datasets.list()}
        self.assert_error_code(
            "ANALYSIS_TOOL_GEOMETRY_INVALID",
            self.invoke,
            "buffer_features",
            BufferFeaturesInput(dataset=reprojected.dataset, distance_m=75.0),
        )
        self.assertEqual({item.id for item in self.datasets.list()}, before_ids)

    def test_cumulative_limit_stops_at_join_without_partial_output(self):
        facilities = self.register("facilities_wgs84.geojson")
        regions = self.register("regions_3857.geojson")
        limited = build_analysis_tool_registry(max_input_features=5)
        reprojected = self.invoke(
            "reproject_dataset",
            ReprojectDatasetInput(dataset=facilities, target_crs="EPSG:3857"),
            tools=limited,
        )
        buffered = self.invoke(
            "buffer_features",
            BufferFeaturesInput(dataset=reprojected.dataset, distance_m=75.0),
            tools=limited,
        )
        before_ids = {item.id for item in self.datasets.list()}
        self.assert_error_code(
            "ANALYSIS_TOOL_LIMIT_EXCEEDED",
            self.invoke,
            "spatial_join",
            SpatialJoinInput(
                dataset=regions,
                right_dataset=buffered.dataset,
                predicate="intersects",
            ),
            tools=limited,
        )
        self.assertEqual({item.id for item in self.datasets.list()}, before_ids)

    def test_two_runs_have_identical_stage_content_hashes(self):
        facilities = self.register("facilities_wgs84.geojson")
        regions = self.register("regions_3857.geojson")
        first = self.run_chain(facilities, regions)
        second = self.run_chain(facilities, regions)
        for stage in self.summary["chain"]:
            key = {
                "reproject_dataset": "reproject",
                "buffer_features": "buffer",
                "spatial_join": "join",
                "aggregate_by_region": "aggregate",
            }[stage]
            self.assertNotEqual(first[key].dataset.id, second[key].dataset.id)
            self.assertEqual(
                first[key].dataset.content_hash,
                second[key].dataset.content_hash,
            )
            self.assertEqual(
                self.datasets.resolve_path(first[key].dataset.id).read_bytes(),
                self.datasets.resolve_path(second[key].dataset.id).read_bytes(),
            )

    def test_reference_comparator_detects_geometry_drift(self):
        drifted = deepcopy(self.expected_final)
        for coordinate in drifted["features"][0]["geometry"]["coordinates"][0]:
            coordinate[0] += 0.01
        with self.assertRaises(AssertionError):
            self.assert_geojson_matches_reference(
                self.expected_final["features"], drifted
            )


if __name__ == "__main__":
    unittest.main()
