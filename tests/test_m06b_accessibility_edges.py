"""Additional offline failure and cross-module checks for M06b."""

from __future__ import annotations

import csv
from datetime import date
import io
import json
from pathlib import Path
import tempfile
import unittest
import zipfile

from src.datasets import LocalDatasetRegistry
from src.tools import (
    ACCESSIBILITY_TOOL_REGISTRY,
    POPULATION_TOOL_REGISTRY,
    AnalysisToolError,
    AnalyzeGtfsStopCoverageInput,
    GtfsTimeWindow,
    LoadPopulationDatasetInput,
)


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "data/fixtures/gtfs/m06b/v1"
SCB_RAW = ROOT / "data/fixtures/population/scb/v1/scb_population_grid_2025_raw.geojson"


class M06bEdgeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.datasets = LocalDatasetRegistry(self.root / "registry")
        self.gtfs = self.datasets.register_file(
            FIXTURE / "schedule.gtfs.zip", kind="network", crs="EPSG:4326",
            source="M06b synthetic fixture", source_version="v1",
        )
        self.population = self.datasets.register_file(
            FIXTURE / "population.geojson", kind="vector", crs="EPSG:3006",
            source="M06b synthetic fixture", source_version="v1",
        )
        self.pois = self.datasets.register_file(
            FIXTURE / "pois.geojson", kind="vector", crs="EPSG:3006",
            source="M06b synthetic fixture", source_version="v1",
        )

    def analyze(self, **changes):
        values = {
            "dataset": self.gtfs,
            "population_dataset": self.population,
            "poi_dataset": self.pois,
            "service_date": date(2025, 10, 4),
            "timezone": "Europe/Stockholm",
            "windows": [GtfsTimeWindow(
                window_id="morning", start_time="08:00:00", end_time="09:00:00",
            )],
            "buffer_distance_m": 250.0,
        }
        values.update(changes)
        return ACCESSIBILITY_TOOL_REGISTRY.invoke(
            "analyze_gtfs_stop_coverage",
            AnalyzeGtfsStopCoverageInput(**values),
            datasets=self.datasets,
        )

    def modified_vector(self, name: str, change):
        document = json.loads((FIXTURE / name).read_text("utf-8"))
        change(document)
        path = self.root / name
        path.write_text(json.dumps(document), encoding="utf-8")
        return self.datasets.register_file(
            path, kind="vector", crs="EPSG:3006", source="mutated test", source_version="v1",
        )

    def test_pickup_prohibited_stop_is_not_counted_as_service(self):
        with zipfile.ZipFile(FIXTURE / "schedule.gtfs.zip") as original:
            tables = {name: original.read(name) for name in original.namelist()}
        reader = csv.DictReader(io.StringIO(tables["stop_times.txt"].decode("utf-8")))
        rows = list(reader)
        for row in rows:
            row["pickup_type"] = "1" if row["trip_id"] == "T1" and row["stop_id"] == "B" else "0"
        out = io.StringIO(newline="")
        writer = csv.DictWriter(out, fieldnames=[*reader.fieldnames, "pickup_type"], lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
        tables["stop_times.txt"] = out.getvalue().encode("utf-8")
        archive = self.root / "pickup.gtfs.zip"
        with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as target:
            for name, data in sorted(tables.items()):
                target.writestr(name, data)
        changed = self.datasets.register_file(
            archive, kind="network", crs="EPSG:4326", source="mutated test", source_version="v1",
        )
        result = self.analyze(dataset=changed, windows=[
            GtfsTimeWindow(window_id="early", start_time="08:00:00", end_time="08:30:00")
        ])
        self.assertEqual(result.windows[0].departure_count, 1)
        self.assertEqual([item.stop_id for item in result.windows[0].stop_frequencies], ["A"])

    def test_conditional_pickup_is_not_regular_frequency_and_invalid_value_fails(self):
        with zipfile.ZipFile(FIXTURE / "schedule.gtfs.zip") as original:
            tables = {name: original.read(name) for name in original.namelist()}
        reader = csv.DictReader(io.StringIO(tables["stop_times.txt"].decode("utf-8")))
        original_rows = list(reader)
        for pickup_value, expected_code in [("2", None), ("9", "ANALYSIS_TOOL_GTFS_PICKUP_INVALID")]:
            rows = [dict(row) for row in original_rows]
            for row in rows:
                row["pickup_type"] = pickup_value if row["trip_id"] == "T1" and row["stop_id"] == "B" else "0"
            out = io.StringIO(newline="")
            writer = csv.DictWriter(out, fieldnames=[*reader.fieldnames, "pickup_type"], lineterminator="\n")
            writer.writeheader()
            writer.writerows(rows)
            tables["stop_times.txt"] = out.getvalue().encode("utf-8")
            archive = self.root / f"pickup_{pickup_value}.gtfs.zip"
            with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as target:
                for name, data in sorted(tables.items()):
                    target.writestr(name, data)
            changed = self.datasets.register_file(
                archive, kind="network", crs="EPSG:4326", source="mutated test", source_version="v1",
            )
            if expected_code:
                with self.assertRaises(AnalysisToolError) as raised:
                    self.analyze(dataset=changed)
                self.assertEqual(raised.exception.code, expected_code)
            else:
                result = self.analyze(dataset=changed, windows=[
                    GtfsTimeWindow(window_id="early", start_time="08:00:00", end_time="08:30:00")
                ])
                self.assertEqual(result.windows[0].departure_count, 1)
                self.assertIn("conditional_pickup_excluded_from_regular_frequency", result.metadata.warnings)
    def test_null_population_is_flagged_and_partial_grid_never_counted(self):
        changed = self.modified_vector(
            "population.geojson",
            lambda doc: doc["features"][0]["properties"].__setitem__("population_total", None),
        )
        result = self.analyze(population_dataset=changed)
        summary = result.windows[0]
        self.assertEqual(summary.fully_covered_grid_ids, ["G1", "G3"])
        self.assertEqual(summary.partially_intersecting_grid_ids, ["G2"])
        self.assertEqual(summary.covered_population_lower_bound, 30)
        self.assertEqual(summary.population_unknown_grid_count, 1)
        self.assertIn("population_covered_grid_value_unknown", result.metadata.warnings)

    def test_poi_geometry_and_declared_crs_fail_without_output(self):
        bad_geometry = self.modified_vector(
            "pois.geojson",
            lambda doc: doc["features"][0].__setitem__(
                "geometry", {"type": "LineString", "coordinates": [[0, 0], [1, 1]]}
            ),
        )
        before = {item.id for item in self.datasets.list()}
        with self.assertRaises(AnalysisToolError) as raised:
            self.analyze(poi_dataset=bad_geometry)
        self.assertEqual(raised.exception.code, "ANALYSIS_TOOL_GEOMETRY_INVALID")
        self.assertEqual({item.id for item in self.datasets.list()}, before)
        bad_crs = self.modified_vector(
            "pois.geojson",
            lambda doc: doc["crs"]["properties"].__setitem__("name", "EPSG:4326"),
        )
        with self.assertRaises(AnalysisToolError) as raised:
            self.analyze(poi_dataset=bad_crs)
        self.assertEqual(raised.exception.code, "ANALYSIS_TOOL_CRS_MISMATCH")

    def test_timezone_mismatch_is_stable_error(self):
        with self.assertRaises(AnalysisToolError) as raised:
            self.analyze(timezone="UTC")
        self.assertEqual(raised.exception.code, "ANALYSIS_TOOL_GTFS_TIMEZONE_MISMATCH")

    def test_m05_normalized_population_dataset_is_accepted(self):
        raw = self.datasets.register_file(
            SCB_RAW, kind="vector", crs="EPSG:3006",
            source="Statistics Sweden (SCB)", source_version="2025",
        )
        normalized = POPULATION_TOOL_REGISTRY.invoke(
            "load_population_dataset", LoadPopulationDatasetInput(dataset=raw),
            datasets=self.datasets,
        ).dataset
        result = self.analyze(population_dataset=normalized)
        self.assertEqual(result.windows[0].departure_count, 5)
        self.assertIsInstance(result.windows[0].covered_population_lower_bound, int)
        self.assertIn("population_reference_date_differs_from_service_date", result.metadata.warnings)
        self.assertEqual(set(result.dataset.lineage[0].parent_ids),
                         {self.gtfs.id, normalized.id, self.pois.id})


if __name__ == "__main__":
    unittest.main()
