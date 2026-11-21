from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from pydantic import ValidationError

from src.datasets import LocalDatasetRegistry
from src.tools import (
    AnalysisToolError,
    AnalysisToolRegistry,
    InspectDatasetSchemaInput,
    InspectDatasetSchemaOutput,
    ToolLimits,
    ToolRegistration,
    ToolSpec,
    ValidateGeometryInput,
    build_quality_tool_registry,
)


ROOT = Path(__file__).resolve().parents[1]
FIXTURE_DIR = ROOT / "data" / "fixtures" / "tools" / "v1"
DATASET_FIXTURE_DIR = ROOT / "data" / "fixtures" / "datasets" / "v1"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(65536), b""):
            digest.update(block)
    return digest.hexdigest()


class ToolFixtureTests(unittest.TestCase):
    def test_manifest_matches_fixture_bytes(self):
        manifest = json.loads((FIXTURE_DIR / "manifest.json").read_text("utf-8"))
        self.assertEqual(manifest["schema_version"], 1)
        for filename, expected in manifest["fixtures"].items():
            with self.subTest(filename=filename):
                self.assertEqual(sha256(FIXTURE_DIR / filename), expected["sha256"])


class ToolContractTests(unittest.TestCase):
    def test_tool_spec_is_strict_versioned_and_round_trips(self):
        spec = ToolSpec(
            name="quality_check",
            description="Check a dataset without mutating it",
            input_contract="QualityInput.v1",
            output_contract="QualityOutput.v1",
            supported_dataset_kinds=["vector"],
            produces_dataset=False,
            mutates_input=False,
            deterministic=True,
            limits=ToolLimits(
                max_input_features=100,
                max_input_bytes=10000,
                max_output_bytes=5000,
            ),
        )
        self.assertEqual(ToolSpec.model_validate_json(spec.model_dump_json()), spec)
        with self.assertRaises(ValidationError):
            ToolSpec.model_validate({**spec.model_dump(), "unknown": True})
        with self.assertRaises(ValidationError):
            ToolSpec.model_validate({**spec.model_dump(), "mutates_input": True})

    def test_input_contracts_are_strict(self):
        with self.assertRaises(ValidationError):
            InspectDatasetSchemaInput.model_validate(
                {"schema_version": "1.0", "dataset": {}, "extra": "forbidden"}
            )
        with self.assertRaises(ValidationError):
            ValidateGeometryInput.model_validate(
                {"schema_version": "1.0", "dataset": {}, "max_issues": "10"}
            )


class QualityToolTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="m04a_tools_")
        self.datasets = LocalDatasetRegistry(Path(self.temp.name) / "registry")
        self.tools = build_quality_tool_registry()

    def tearDown(self):
        self.temp.cleanup()

    def register_geojson(self, filename: str):
        return self.datasets.register_file(
            FIXTURE_DIR / filename,
            kind="vector",
            crs="EPSG:4326",
            source="m04a-fixture",
            source_version="v1",
        )

    def assert_error_code(self, expected: str, callable_, *args, **kwargs):
        with self.assertRaises(AnalysisToolError) as raised:
            callable_(*args, **kwargs)
        self.assertEqual(raised.exception.code, expected)

    def test_registry_describes_only_m04a_quality_tools(self):
        descriptions = self.tools.describe()
        self.assertEqual(
            [item.name for item in descriptions],
            ["inspect_dataset_schema", "validate_geometry"],
        )
        self.assertTrue(all(item.mutates_input is False for item in descriptions))

    def test_registry_rejects_unknown_tool_and_extra_input(self):
        ref = self.register_geojson("valid_geometries.geojson")
        self.assert_error_code(
            "ANALYSIS_TOOL_NOT_REGISTERED",
            self.tools.invoke,
            "shell",
            {},
            datasets=self.datasets,
        )
        self.assert_error_code(
            "ANALYSIS_TOOL_INPUT_INVALID",
            self.tools.invoke,
            "inspect_dataset_schema",
            {"schema_version": "1.0", "dataset": ref.model_dump(), "extra": True},
            datasets=self.datasets,
        )

    def test_inspect_dataset_schema_reports_metadata_without_mutation(self):
        ref = self.register_geojson("valid_geometries.geojson")
        path = self.datasets.resolve_path(ref.id)
        before = path.read_bytes()
        result = self.tools.invoke(
            "inspect_dataset_schema",
            InspectDatasetSchemaInput(dataset=ref),
            datasets=self.datasets,
        )
        self.assertEqual(result.dataset, ref)
        self.assertEqual(result.row_count, 3)
        self.assertEqual({field.name for field in result.fields}, {"category", "name"})
        self.assertEqual(result.metadata.lineage.operation, "inspect_dataset_schema")
        self.assertEqual(result.metadata.lineage.parent_ids, [ref.id])
        self.assertEqual(result.metadata.statistics["row_count"], 3)
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(self.datasets.get(ref.id).content_hash, ref.content_hash)

    def test_inspect_table_schema_is_supported(self):
        ref = self.datasets.register_file(
            DATASET_FIXTURE_DIR / "population.csv",
            kind="table",
            crs="EPSG:4326",
            source="m02-fixture",
            source_version="v1",
        )
        result = self.tools.invoke(
            "inspect_dataset_schema",
            {"schema_version": "1.0", "dataset": ref.model_dump(mode="json")},
            datasets=self.datasets,
        )
        self.assertEqual(result.kind, "table")
        self.assertIsNone(result.bbox)

    def test_stale_dataset_ref_is_rejected(self):
        ref = self.register_geojson("valid_geometries.geojson")
        stale = ref.model_copy(update={"row_count": ref.row_count + 1})
        self.assert_error_code(
            "ANALYSIS_TOOL_DATASET_REF_STALE",
            self.tools.invoke,
            "inspect_dataset_schema",
            InspectDatasetSchemaInput(dataset=stale),
            datasets=self.datasets,
        )

    def test_input_feature_limit_is_enforced_before_execution(self):
        ref = self.register_geojson("valid_geometries.geojson")
        limited = build_quality_tool_registry(max_input_features=2)
        self.assert_error_code(
            "ANALYSIS_TOOL_LIMIT_EXCEEDED",
            limited.invoke,
            "inspect_dataset_schema",
            InspectDatasetSchemaInput(dataset=ref),
            datasets=self.datasets,
        )

    def test_output_byte_limit_is_enforced_after_validation(self):
        ref = self.register_geojson("valid_geometries.geojson")
        limited = build_quality_tool_registry(max_output_bytes=1)
        self.assert_error_code(
            "ANALYSIS_TOOL_OUTPUT_LIMIT_EXCEEDED",
            limited.invoke,
            "inspect_dataset_schema",
            InspectDatasetSchemaInput(dataset=ref),
            datasets=self.datasets,
        )

    def test_registry_detects_handler_input_mutation(self):
        ref = self.register_geojson("valid_geometries.geojson")
        malicious = AnalysisToolRegistry()

        def mutate_input(contract, datasets):
            result = self.tools.invoke(
                "inspect_dataset_schema", contract, datasets=datasets
            )
            datasets.resolve_path(contract.dataset.id).write_text("{}", encoding="utf-8")
            return result

        malicious.register(
            ToolRegistration(
                spec=ToolSpec(
                    name="mutation_probe",
                    description="Test-only handler that violates the no-mutation contract",
                    input_contract="InspectDatasetSchemaInput.v1",
                    output_contract="InspectDatasetSchemaOutput.v1",
                    supported_dataset_kinds=["vector"],
                    produces_dataset=False,
                    mutates_input=False,
                    deterministic=True,
                    limits=ToolLimits(
                        max_input_features=100,
                        max_input_bytes=100000,
                        max_output_bytes=100000,
                    ),
                ),
                input_model=InspectDatasetSchemaInput,
                output_model=InspectDatasetSchemaOutput,
                handler=mutate_input,
            )
        )
        self.assert_error_code(
            "ANALYSIS_TOOL_INPUT_MUTATED",
            malicious.invoke,
            "mutation_probe",
            InspectDatasetSchemaInput(dataset=ref),
            datasets=self.datasets,
        )

    def test_validate_geometry_accepts_valid_mixed_types_without_mutation(self):
        ref = self.register_geojson("valid_geometries.geojson")
        path = self.datasets.resolve_path(ref.id)
        before = path.read_bytes()
        result = self.tools.invoke(
            "validate_geometry",
            ValidateGeometryInput(dataset=ref, max_issues=20),
            datasets=self.datasets,
        )
        self.assertTrue(result.is_valid)
        self.assertEqual(result.valid_geometry_count, 3)
        self.assertEqual(result.invalid_geometry_count, 0)
        self.assertEqual(
            result.geometry_type_counts,
            {"LineString": 1, "Point": 1, "Polygon": 1},
        )
        self.assertEqual(result.issues, [])
        self.assertEqual(path.read_bytes(), before)

    def test_validate_geometry_detects_self_intersection(self):
        ref = self.register_geojson("invalid_topology.geojson")
        result = self.tools.invoke(
            "validate_geometry",
            ValidateGeometryInput(dataset=ref, max_issues=20),
            datasets=self.datasets,
        )
        self.assertFalse(result.is_valid)
        self.assertEqual(result.valid_geometry_count, 0)
        self.assertEqual(result.invalid_geometry_count, 1)
        self.assertEqual(result.issues[0].feature_id, "bow-tie-1")
        self.assertIn("Self-intersection", result.issues[0].reason)

    def test_validate_empty_dataset_returns_warning_not_failure(self):
        ref = self.register_geojson("empty.geojson")
        result = self.tools.invoke(
            "validate_geometry",
            ValidateGeometryInput(dataset=ref, max_issues=20),
            datasets=self.datasets,
        )
        self.assertTrue(result.is_valid)
        self.assertEqual(result.metadata.warnings, ["dataset_empty"])

    def test_validate_geometry_rejects_table_dataset(self):
        ref = self.datasets.register_file(
            DATASET_FIXTURE_DIR / "population.csv",
            kind="table",
            crs="EPSG:4326",
            source="m02-fixture",
            source_version="v1",
        )
        self.assert_error_code(
            "ANALYSIS_TOOL_DATASET_KIND_UNSUPPORTED",
            self.tools.invoke,
            "validate_geometry",
            ValidateGeometryInput(dataset=ref, max_issues=20),
            datasets=self.datasets,
        )

    def test_issue_limit_truncates_details_but_preserves_counts(self):
        ref = self.register_geojson("invalid_topology.geojson")
        result = self.tools.invoke(
            "validate_geometry",
            ValidateGeometryInput(dataset=ref, max_issues=0),
            datasets=self.datasets,
        )
        self.assertEqual(result.invalid_geometry_count, 1)
        self.assertEqual(result.issues, [])
        self.assertIn("issues_truncated", result.metadata.warnings)


if __name__ == "__main__":
    unittest.main()
