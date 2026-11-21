from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from pydantic import ValidationError

from src.datasets import LocalDatasetRegistry
from src.population import (
    HttpResponse,
    PopulationAdapterError,
    ScbPopulationGridRequest,
    build_scb_population_grid_url,
    download_scb_population_grid,
    run_fully_contained_grid_population,
)
from src.population.contracts import FullyContainedGridPopulationInput
from src.tools import (
    ANALYSIS_TOOL_REGISTRY,
    AnalysisToolError,
    LoadPopulationDatasetInput,
    POPULATION_TOOL_REGISTRY,
    QUALITY_TOOL_REGISTRY,
    build_population_tool_registry,
)


ROOT = Path(__file__).resolve().parents[1]
FIXTURE_DIR = ROOT / "data" / "fixtures" / "population" / "scb" / "v1"
RAW_FIXTURE = FIXTURE_DIR / "scb_population_grid_2025_raw.geojson"
EXPECTED_STANDARD_FIELDS = {
    "grid_id",
    "source_grid_id_scb",
    "population_reference_date",
    "population_total",
    "population_status",
    "population_male",
    "population_female",
    "population_age_0_4",
    "population_age_5_9",
    "population_age_10_14",
    "population_age_15_19",
    "population_age_20_24",
    "population_age_25_29",
    "population_age_30_34",
    "population_age_35_39",
    "population_age_40_44",
    "population_age_45_49",
    "population_age_50_54",
    "population_age_55_59",
    "population_age_60_64",
    "population_age_65_69",
    "population_age_70_74",
    "population_age_75_79",
    "population_age_80_84",
    "population_age_85_89",
    "population_age_90_94",
    "population_age_95_99",
    "population_age_100_plus",
}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class M05FixtureTests(unittest.TestCase):
    def test_manifest_hashes_query_schema_and_reference_are_fixed(self):
        manifest = json.loads((FIXTURE_DIR / "manifest.json").read_text("utf-8"))
        self.assertEqual(manifest["fixture_version"], "m05-scb-population-v1")
        self.assertEqual(manifest["layer"], "stat:befolkning_1km_2025")
        self.assertEqual(manifest["crs"], "EPSG:3006")
        self.assertEqual(manifest["license"], "CC0-1.0")
        request = manifest["request"]
        self.assertEqual(request["wfs_version"], "1.1.0")
        self.assertEqual(request["type_name"], manifest["layer"])
        self.assertEqual(request["srs_name"], "EPSG:3006")
        self.assertEqual(request["sort_by"], "objectid")
        self.assertEqual(len(request["bbox"]), 4)
        self.assertIn("propertyName=", request["canonical_url"])
        self.assertNotIn("maxFeatures=", request["canonical_url"])
        self.assertEqual(
            manifest["schema"]["sha256"],
            "45f9c1ba7397a126cc0a303bd85d75f45d357100e8aaa423b421396a46621c25",
        )
        self.assertIsNone(manifest["http_metadata"]["last_modified"])
        self.assertIsNone(manifest["http_metadata"]["etag"])
        for filename, entry in manifest["fixtures"].items():
            with self.subTest(filename=filename):
                self.assertEqual(sha256(FIXTURE_DIR / filename), entry["sha256"])

        raw = json.loads(RAW_FIXTURE.read_text("utf-8"))
        self.assertEqual(raw["crs"]["properties"]["name"], "urn:ogc:def:crs:EPSG::3006")
        self.assertEqual(len(raw["features"]), 16)
        object_ids = [item["properties"]["objectid"] for item in raw["features"]]
        self.assertEqual(object_ids, sorted(object_ids))
        self.assertEqual(
            {item["properties"]["referenstid"] for item in raw["features"]},
            {"20251231"},
        )


class PopulationDownloadTests(unittest.TestCase):
    def setUp(self):
        self.request = ScbPopulationGridRequest(
            bbox=[385000.0, 6174000.0, 387000.0, 6176000.0]
        )
        self.manifest = json.loads((FIXTURE_DIR / "manifest.json").read_text("utf-8"))

    def test_request_is_strict_and_url_is_canonical(self):
        self.assertEqual(
            build_scb_population_grid_url(self.request),
            self.manifest["request"]["canonical_url"],
        )
        with self.assertRaises(ValidationError):
            ScbPopulationGridRequest.model_validate(
                {"bbox": [385000.0, 6174000.0, 387000.0, 6176000.0], "extra": True}
            )
        with self.assertRaises(ValidationError):
            ScbPopulationGridRequest(bbox=[True, 6174000.0, 387000.0, 6176000.0])
        with self.assertRaises(ValidationError):
            ScbPopulationGridRequest(bbox=[387000.0, 6174000.0, 385000.0, 6176000.0])

    def test_download_validates_data_and_schema_without_real_network(self):
        raw = RAW_FIXTURE.read_bytes()
        schema = b"""<?xml version='1.0'?><xsd:schema xmlns:xsd='http://www.w3.org/2001/XMLSchema'><xsd:element name='objectid'/><xsd:element name='rutid_inspire'/><xsd:element name='beftotalt'/><xsd:element name='referenstid'/><xsd:element name='sp_geometry'/></xsd:schema>"""
        seen: list[str] = []

        def fake_get(url: str) -> HttpResponse:
            seen.append(url)
            if "DescribeFeatureType" in url:
                return HttpResponse(200, {"Content-Type": "text/xml"}, schema)
            return HttpResponse(
                200,
                {"Content-Type": "application/json", "ETag": '"fixture-etag"'},
                raw,
            )

        result = download_scb_population_grid(
            self.request,
            http_get=fake_get,
            now=lambda: datetime(2026, 10, 5, tzinfo=timezone.utc),
        )
        self.assertEqual(result.content, raw)
        self.assertEqual(result.receipt.feature_count, 16)
        self.assertEqual(result.receipt.etag, '"fixture-etag"')
        self.assertIsNone(result.receipt.last_modified)
        self.assertEqual(result.receipt.dataset_year, 2025)
        self.assertEqual(result.receipt.reference_dates, ["20251231"])
        self.assertEqual(result.receipt.content_sha256, hashlib.sha256(raw).hexdigest())
        self.assertEqual(result.receipt.schema_sha256, hashlib.sha256(schema).hexdigest())
        self.assertEqual(len(seen), 2)

    def test_download_errors_do_not_collapse_into_schema_errors(self):
        def network_failure(_url: str) -> HttpResponse:
            raise TimeoutError("offline")

        with self.assertRaises(PopulationAdapterError) as raised:
            download_scb_population_grid(self.request, http_get=network_failure)
        self.assertEqual(raised.exception.code, "POPULATION_ADAPTER_DOWNLOAD_FAILED")

        schema = b"""<xsd:schema xmlns:xsd='http://www.w3.org/2001/XMLSchema'><xsd:element name='objectid'/><xsd:element name='rutid_inspire'/><xsd:element name='beftotalt'/><xsd:element name='referenstid'/><xsd:element name='sp_geometry'/></xsd:schema>"""

        def malformed_response(url: str) -> HttpResponse:
            if "DescribeFeatureType" in url:
                return HttpResponse(200, {"Content-Type": "text/xml"}, schema)
            return HttpResponse(200, {"Content-Type": "application/json"}, b"not-json")

        with self.assertRaises(PopulationAdapterError) as raised:
            download_scb_population_grid(self.request, http_get=malformed_response)
        self.assertEqual(raised.exception.code, "POPULATION_ADAPTER_RESPONSE_INVALID")


class PopulationToolTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="m05_population_")
        self.temp_path = Path(self.temp.name)
        self.datasets = LocalDatasetRegistry(self.temp_path / "registry")
        self.tools = build_population_tool_registry()

    def tearDown(self):
        self.temp.cleanup()

    def register_raw(self, document=None, *, crs="EPSG:3006"):
        if document is None:
            path = RAW_FIXTURE
        else:
            path = self.temp_path / f"raw_{len(self.datasets.list())}.geojson"
            path.write_text(
                json.dumps(document, ensure_ascii=False, sort_keys=True), encoding="utf-8"
            )
        return self.datasets.register_file(
            path,
            kind="vector",
            crs=crs,
            source="Statistics Sweden (SCB)",
            source_version="befolkning_1km_2025",
        )

    def invoke(self, raw, *, tools=None):
        return (tools or self.tools).invoke(
            "load_population_dataset",
            LoadPopulationDatasetInput(dataset=raw, provider="scb", dataset_year=2025),
            datasets=self.datasets,
        )

    def assert_error(self, expected, raw):
        with self.assertRaises(AnalysisToolError) as raised:
            self.invoke(raw)
        self.assertEqual(raised.exception.code, expected)

    def raw_document(self):
        return json.loads(RAW_FIXTURE.read_text("utf-8"))

    def test_contract_is_strict_and_tool_isolated_in_population_registry(self):
        raw = self.register_raw()
        with self.assertRaises(ValidationError):
            LoadPopulationDatasetInput.model_validate(
                {
                    "dataset": raw.model_dump(mode="json"),
                    "provider": "scb",
                    "dataset_year": 2025,
                    "unknown": True,
                }
            )
        names = [item.name for item in self.tools.describe()]
        self.assertIn("load_population_dataset", names)
        self.assertEqual(len(names), 11)
        self.assertNotIn(
            "load_population_dataset",
            [item.name for item in ANALYSIS_TOOL_REGISTRY.describe()],
        )
        self.assertNotIn(
            "load_population_dataset",
            [item.name for item in QUALITY_TOOL_REGISTRY.describe()],
        )
        self.assertIn(
            "load_population_dataset",
            [item.name for item in POPULATION_TOOL_REGISTRY.describe()],
        )

    def test_success_maps_all_fields_lineage_ttl_and_preserves_input(self):
        raw = self.register_raw()
        raw_before = self.datasets.resolve_path(raw.id).read_bytes()
        result = self.invoke(raw)
        output = self.datasets.read(result.dataset.id)
        self.assertNotEqual(result.dataset.id, raw.id)
        self.assertEqual(result.dataset.lifecycle, "temporary")
        self.assertIsNotNone(result.dataset.expires_at)
        self.assertEqual(result.provider, "scb")
        self.assertEqual(result.layer, "stat:befolkning_1km_2025")
        self.assertEqual(result.dataset_year, 2025)
        self.assertEqual(result.field_mapping_version, "scb-population-grid-2025-v1")
        self.assertEqual(result.adapter_version, "1.0")
        self.assertEqual(result.feature_count, 16)
        self.assertEqual(result.reported_count, 16)
        self.assertEqual(result.missing_or_suppressed_count, 0)
        self.assertEqual(set(output.records[0]["properties"]), EXPECTED_STANDARD_FIELDS)
        self.assertEqual(
            output.records[0]["properties"]["grid_id"],
            "SE_CRS3006RES1000mN6173000E384000",
        )
        self.assertEqual(output.records[0]["properties"]["population_reference_date"], "2025-12-31")
        self.assertEqual(output.records[0]["properties"]["population_status"], "reported")
        self.assertEqual(output.records[0]["geometry"], self.raw_document()["features"][0]["geometry"])
        self.assertEqual(self.datasets.resolve_path(raw.id).read_bytes(), raw_before)
        self.assertEqual(result.metadata.lineage.parent_ids, [raw.id])
        self.assertEqual(result.metadata.lineage.operation, "load_population_dataset")
        self.assertEqual(result.dataset.lineage, [result.metadata.lineage])
        self.assertIn("population_values_are_disclosure_controlled", result.metadata.warnings)
        self.assertIn("population_components_may_not_sum_to_total", result.metadata.warnings)

    def test_missing_core_field_and_duplicate_grid_id_are_schema_errors(self):
        missing = self.raw_document()
        del missing["features"][0]["properties"]["rutid_inspire"]
        self.assert_error(
            "ANALYSIS_TOOL_POPULATION_SCHEMA_INVALID", self.register_raw(missing)
        )

        duplicate = self.raw_document()
        duplicate["features"][1]["properties"]["rutid_inspire"] = duplicate["features"][0]["properties"]["rutid_inspire"]
        self.assert_error(
            "ANALYSIS_TOOL_POPULATION_SCHEMA_INVALID", self.register_raw(duplicate)
        )

    def test_invalid_date_and_population_values_are_value_errors(self):
        invalid_date = self.raw_document()
        invalid_date["features"][0]["properties"]["referenstid"] = "20250230"
        self.assert_error(
            "ANALYSIS_TOOL_POPULATION_VALUE_INVALID", self.register_raw(invalid_date)
        )

        for value in (-1, True, 1.5, "12"):
            with self.subTest(value=value):
                document = self.raw_document()
                document["features"][0]["properties"]["beftotalt"] = value
                self.assert_error(
                    "ANALYSIS_TOOL_POPULATION_VALUE_INVALID", self.register_raw(document)
                )

    def test_zero_null_optional_missing_and_component_mismatch_are_valid(self):
        document = self.raw_document()
        first = document["features"][0]["properties"]
        first["beftotalt"] = 0
        first["man"] = 999
        second = document["features"][1]["properties"]
        del second["beftotalt"]
        del second["kvinna"]
        third = document["features"][2]["properties"]
        third["beftotalt"] = ":"
        result = self.invoke(self.register_raw(document))
        records = self.datasets.read(result.dataset.id).records
        self.assertEqual(records[0]["properties"]["population_total"], 0)
        self.assertEqual(records[0]["properties"]["population_status"], "reported")
        self.assertIsNone(records[1]["properties"]["population_total"])
        self.assertIsNone(records[1]["properties"]["population_female"])
        self.assertEqual(
            records[1]["properties"]["population_status"], "missing_or_suppressed"
        )
        self.assertIsNone(records[2]["properties"]["population_total"])
        self.assertEqual(
            records[2]["properties"]["population_status"], "missing_or_suppressed"
        )
        self.assertEqual(result.missing_or_suppressed_count, 2)
        self.assertIn("population_values_missing_or_suppressed", result.metadata.warnings)
        self.assertIn("population_components_may_not_sum_to_total", result.metadata.warnings)

    def test_crs_declaration_and_dataset_ref_must_both_equal_epsg_3006(self):
        self.assert_error(
            "ANALYSIS_TOOL_CRS_MISMATCH", self.register_raw(crs="EPSG:3857")
        )
        document = self.raw_document()
        document["crs"]["properties"]["name"] = "EPSG:3857"
        self.assert_error(
            "ANALYSIS_TOOL_CRS_MISMATCH", self.register_raw(document, crs="EPSG:3006")
        )

    def test_empty_wfs_feature_collection_is_supported(self):
        document = {
            "type": "FeatureCollection",
            "features": [],
            "crs": {"type": "name", "properties": {"name": "EPSG:3006"}},
        }
        result = self.invoke(self.register_raw(document))
        self.assertEqual(result.feature_count, 0)
        self.assertEqual(self.datasets.get(result.dataset.id).row_count, 0)
        self.assertIn("population_dataset_empty", result.metadata.warnings)

    def test_output_limit_releases_derived_dataset(self):
        raw = self.register_raw()
        limited = build_population_tool_registry(max_output_bytes=200)
        before = {item.id for item in self.datasets.list()}
        with self.assertRaises(AnalysisToolError) as raised:
            self.invoke(raw, tools=limited)
        self.assertEqual(raised.exception.code, "ANALYSIS_TOOL_OUTPUT_LIMIT_EXCEEDED")
        self.assertEqual({item.id for item in self.datasets.list()}, before)


class PopulationReferenceCaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="m05_reference_")
        self.datasets = LocalDatasetRegistry(Path(self.temp.name) / "registry")
        self.tools = build_population_tool_registry()
        self.expected = json.loads(
            (FIXTURE_DIR / "expected_reference.json").read_text("utf-8")
        )

    def tearDown(self):
        self.temp.cleanup()

    def register(self, path: Path, *, source: str):
        return self.datasets.register_file(
            path,
            kind="vector",
            crs="EPSG:3006",
            source=source,
            source_version="m05-v1",
        )

    def test_fully_contained_grid_population_reference(self):
        raw = self.register(RAW_FIXTURE, source="Statistics Sweden (SCB)")
        poi = self.register(FIXTURE_DIR / "poi_reference.geojson", source="m05-fixture")
        population = self.tools.invoke(
            "load_population_dataset",
            LoadPopulationDatasetInput(dataset=raw, provider="scb", dataset_year=2025),
            datasets=self.datasets,
        )
        result = run_fully_contained_grid_population(
            FullyContainedGridPopulationInput(
                poi_dataset=poi,
                population_dataset=population.dataset,
                poi_id_field="poi_id",
                buffer_distance_m=self.expected["buffer_distance_m"],
                quad_segs=self.expected["quad_segs"],
            ),
            datasets=self.datasets,
            tools=self.tools,
        )
        self.assertEqual(result.method, self.expected["method"])
        self.assertEqual(
            result.fully_contained_grid_ids,
            self.expected["fully_contained_grid_ids"],
        )
        self.assertEqual(
            result.fully_contained_grid_count,
            self.expected["fully_contained_grid_count"],
        )
        self.assertEqual(
            result.partially_intersecting_excluded_grid_count,
            self.expected["partially_intersecting_excluded_grid_count"],
        )
        self.assertEqual(
            result.fully_contained_grid_population,
            self.expected["fully_contained_grid_population"],
        )
        self.assertEqual(result.warning, self.expected["warning"])
        final = self.datasets.read(result.dataset.id)
        self.assertEqual(final.records[0]["properties"]["agg_count"], 4)
        self.assertEqual(
            final.records[0]["properties"]["agg_sum_right_population_total"],
            20412,
        )


if __name__ == "__main__":
    unittest.main()
