from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import shutil
import tempfile
import unittest

from pydantic import ValidationError

from src.datasets import (
    DatasetRef,
    DatasetRegistry,
    DatasetRegistryError,
    LocalDatasetRegistry,
)


ROOT = Path(__file__).resolve().parents[1]
FIXTURE_DIR = ROOT / "data" / "fixtures" / "datasets" / "v1"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(65536), b""):
            digest.update(block)
    return digest.hexdigest()


class DatasetFixtureTests(unittest.TestCase):
    def test_fixture_manifest_matches_content(self):
        manifest = json.loads((FIXTURE_DIR / "manifest.json").read_text("utf-8"))
        self.assertEqual(manifest["schema_version"], 1)
        for filename, expected in manifest["fixtures"].items():
            with self.subTest(filename=filename):
                self.assertEqual(sha256(FIXTURE_DIR / filename), expected["sha256"])


class DatasetRegistryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="geoai_registry_test_")
        self.root = Path(self.temp.name) / "registry"
        self.registry = LocalDatasetRegistry(self.root)

    def tearDown(self):
        self.temp.cleanup()

    def register_geojson(self, **overrides):
        arguments = {
            "kind": "vector",
            "crs": "EPSG:4326",
            "source": "fixture",
            "source_version": "v1",
        }
        arguments.update(overrides)
        return self.registry.register_file(FIXTURE_DIR / "places.geojson", **arguments)

    def assert_error_code(self, expected: str, callable_, *args, **kwargs):
        with self.assertRaises(DatasetRegistryError) as raised:
            callable_(*args, **kwargs)
        self.assertEqual(raised.exception.code, expected)

    def test_dataset_ref_is_strict_versioned_and_round_trips(self):
        ref = self.register_geojson()
        self.assertIsInstance(self.registry, DatasetRegistry)
        self.assertEqual(DatasetRef.model_validate_json(ref.model_dump_json()), ref)
        self.assertEqual(ref.schema_version, "1.0")
        self.assertTrue(ref.id.startswith("ds_"))
        self.assertTrue(ref.storage_uri.startswith("registry://datasets/"))
        self.assertNotIn(str(FIXTURE_DIR), ref.storage_uri)
        with self.assertRaises(ValidationError):
            DatasetRef.model_validate({**ref.model_dump(), "row_count": "3"})

    def test_contract_reserves_raster_and_network_formats(self):
        base = self.register_geojson().model_dump()
        for kind, format_name in (("raster", "geotiff"), ("network", "gtfs")):
            with self.subTest(kind=kind):
                payload = {
                    **base,
                    "kind": kind,
                    "format": format_name,
                    "storage_uri": (
                        f"registry://datasets/{base['id']}/data.{format_name}"
                    ),
                }
                self.assertEqual(DatasetRef.model_validate(payload).kind, kind)

    def test_register_and_read_geojson_with_metadata(self):
        ref = self.register_geojson()
        loaded = self.registry.read(ref.id)
        self.assertEqual(loaded.ref, ref)
        self.assertEqual(len(loaded.records), 3)
        self.assertEqual(ref.row_count, 3)
        self.assertEqual(ref.bbox, [13.1, 55.6, 13.3, 55.8])
        self.assertEqual({field.name for field in ref.fields}, {"capacity", "category", "name"})
        self.assertEqual(ref.lineage[0].operation, "register")

    def test_register_and_read_csv_with_metadata(self):
        ref = self.registry.register_file(
            FIXTURE_DIR / "population.csv",
            kind="table",
            crs="EPSG:4326",
            source="fixture",
            source_version="v1",
        )
        loaded = self.registry.read(ref.id)
        self.assertEqual(ref.format, "csv")
        self.assertEqual(ref.row_count, 3)
        self.assertIsNone(ref.bbox)
        self.assertEqual(loaded.records[0]["population"], "1200")
        with self.assertRaises(ValidationError):
            DatasetRef.model_validate({**ref.model_dump(), "bbox": [1.0, 2.0, 3.0, 4.0]})

    def test_registered_metadata_matches_fixture_manifest(self):
        manifest = json.loads((FIXTURE_DIR / "manifest.json").read_text("utf-8"))
        for filename, expected in manifest["fixtures"].items():
            with self.subTest(filename=filename):
                ref = self.registry.register_file(
                    FIXTURE_DIR / filename,
                    kind=expected["kind"],
                    crs=expected["crs"],
                    source="fixture",
                    source_version="v1",
                )
                self.assertEqual(ref.content_hash, f"sha256:{expected['sha256']}")
                self.assertEqual(ref.row_count, expected["row_count"])
                self.assertEqual(ref.bbox, expected["bbox"])

    def test_registration_copies_source_into_controlled_storage(self):
        source = Path(self.temp.name) / "source.geojson"
        shutil.copyfile(FIXTURE_DIR / "places.geojson", source)
        ref = self.registry.register_file(
            source,
            kind="vector",
            crs="EPSG:4326",
            source="upload",
            source_version="v1",
        )
        source.unlink()
        self.assertEqual(len(self.registry.read(ref.id).records), 3)

    def test_missing_crs_source_format_and_kind_have_stable_errors(self):
        self.assert_error_code(
            "DATASET_CRS_MISSING",
            self.registry.register_file,
            FIXTURE_DIR / "places.geojson",
            kind="vector",
            crs="",
            source="fixture",
            source_version="v1",
        )
        self.assert_error_code(
            "DATASET_SOURCE_MISSING",
            self.registry.register_file,
            FIXTURE_DIR / "missing.geojson",
            kind="vector",
            crs="EPSG:4326",
            source="fixture",
            source_version="v1",
        )
        unsupported = Path(self.temp.name) / "data.txt"
        unsupported.write_text("x", encoding="utf-8")
        self.assert_error_code(
            "DATASET_FORMAT_UNSUPPORTED",
            self.registry.register_file,
            unsupported,
            kind="table",
            crs="EPSG:4326",
            source="fixture",
            source_version="v1",
        )
        geotiff = Path(self.temp.name) / "data.tif"
        geotiff.write_bytes(b"not-a-real-geotiff")
        self.assert_error_code(
            "DATASET_FORMAT_UNSUPPORTED",
            self.registry.register_file,
            geotiff,
            kind="raster",
            crs="EPSG:4326",
            source="fixture",
            source_version="v1",
        )
        self.assert_error_code(
            "DATASET_KIND_FORMAT_MISMATCH",
            self.registry.register_file,
            FIXTURE_DIR / "places.geojson",
            kind="table",
            crs="EPSG:4326",
            source="fixture",
            source_version="v1",
        )

    def test_invalid_geojson_and_csv_are_rejected(self):
        for filename, kind in (("invalid.geojson", "vector"), ("invalid.csv", "table")):
            with self.subTest(filename=filename):
                self.assert_error_code(
                    "DATASET_FORMAT_INVALID",
                    self.registry.register_file,
                    FIXTURE_DIR / filename,
                    kind=kind,
                    crs="EPSG:4326",
                    source="fixture",
                    source_version="v1",
                )
        self.assertEqual(list((self.root / ".staging").iterdir()), [])

    def test_content_tampering_is_detected(self):
        ref = self.register_geojson()
        path = self.registry.resolve_path(ref.id)
        path.write_text(path.read_text("utf-8") + "\n", encoding="utf-8")
        self.assert_error_code("DATASET_CONTENT_HASH_MISMATCH", self.registry.get, ref.id)

    def test_metadata_hash_tampering_is_detected(self):
        ref = self.register_geojson()
        metadata_path = self.root / "datasets" / ref.id / "metadata.json"
        metadata = json.loads(metadata_path.read_text("utf-8"))
        metadata["content_hash"] = f"sha256:{'0' * 64}"
        metadata_path.write_text(json.dumps(metadata), encoding="utf-8")
        self.assert_error_code("DATASET_CONTENT_HASH_MISMATCH", self.registry.get, ref.id)

    def test_missing_content_is_detected(self):
        ref = self.register_geojson()
        self.registry.resolve_path(ref.id).unlink()
        self.assert_error_code("DATASET_CONTENT_MISSING", self.registry.get, ref.id)

    def test_tampered_storage_uri_is_rejected(self):
        ref = self.register_geojson()
        metadata_path = self.root / "datasets" / ref.id / "metadata.json"
        metadata = json.loads(metadata_path.read_text("utf-8"))
        metadata["storage_uri"] = "file:///outside.geojson"
        metadata_path.write_text(json.dumps(metadata), encoding="utf-8")
        self.assert_error_code("DATASET_STORAGE_VIOLATION", self.registry.get, ref.id)

    def test_dataset_id_is_never_interpreted_as_a_path(self):
        outside = Path(self.temp.name) / "outside.txt"
        outside.write_text("keep", encoding="utf-8")
        self.assert_error_code("DATASET_ID_INVALID", self.registry.get, "../outside")
        self.assertEqual(outside.read_text("utf-8"), "keep")

    def test_persistent_release_is_rejected(self):
        ref = self.register_geojson()
        self.assert_error_code("DATASET_NOT_TEMPORARY", self.registry.release, ref.id)
        self.assertEqual(self.registry.get(ref.id), ref)

    def test_temporary_dataset_can_be_released(self):
        ref = self.register_geojson(temporary=True, ttl_seconds=60)
        path = self.registry.resolve_path(ref.id)
        self.assertTrue(path.is_file())
        self.assertTrue(self.registry.release(ref.id))
        self.assertFalse(path.exists())
        self.assert_error_code("DATASET_NOT_FOUND", self.registry.get, ref.id)

    def test_expired_temporary_datasets_are_cleaned_up(self):
        ref = self.register_geojson(temporary=True, ttl_seconds=60)
        removed = self.registry.cleanup_expired(
            now=datetime.now(timezone.utc) + timedelta(minutes=2)
        )
        self.assertEqual(removed, [ref.id])
        self.assert_error_code("DATASET_NOT_FOUND", self.registry.get, ref.id)

    def test_temporary_dataset_requires_explicit_ttl(self):
        self.assert_error_code(
            "DATASET_TTL_INVALID",
            self.register_geojson,
            temporary=True,
        )

    def test_metadata_query_filters_kind_and_source(self):
        vector = self.register_geojson()
        table = self.registry.register_file(
            FIXTURE_DIR / "population.csv",
            kind="table",
            crs="EPSG:4326",
            source="statistics",
            source_version="v1",
        )
        self.assertEqual([item.id for item in self.registry.list(kind="vector")], [vector.id])
        self.assertEqual([item.id for item in self.registry.list(source="statistics")], [table.id])

    def test_concurrent_registration_never_overwrites_results(self):
        def register(_index):
            return self.register_geojson(temporary=True, ttl_seconds=60)

        with ThreadPoolExecutor(max_workers=8) as executor:
            refs = list(executor.map(register, range(24)))
        self.assertEqual(len({item.id for item in refs}), 24)
        self.assertEqual(len({item.storage_uri for item in refs}), 24)
        self.assertTrue(all(self.registry.resolve_path(item.id).is_file() for item in refs))


if __name__ == "__main__":
    unittest.main()
