"""Offline, test-first reference cases for service-day stop accessibility."""

from __future__ import annotations

from datetime import date
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from pydantic import ValidationError

from src.datasets import LocalDatasetRegistry
from src.tools import AnalysisToolError
from src.tools.accessibility_contracts import (
    AnalyzeGtfsStopCoverageInput,
    GtfsTimeWindow,
)
from src.tools.accessibility_registry import ACCESSIBILITY_TOOL_REGISTRY


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "data/fixtures/gtfs/m06b/v1"
REAL_ARCHIVE = ROOT / "data/fixtures/gtfs/sverige2/lund_20251004_v1/lund_subset.gtfs.zip"
EXPECTED = json.loads((FIXTURE / "expected.json").read_text("utf-8"))


class M06bAccessibilityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.datasets = LocalDatasetRegistry(self.root / "registry")
        self.gtfs = self.datasets.register_file(
            FIXTURE / "schedule.gtfs.zip", kind="network", crs="EPSG:4326",
            source="project-authored M06b fixture", source_version="v1",
        )
        self.population = self.datasets.register_file(
            FIXTURE / "population.geojson", kind="vector", crs="EPSG:3006",
            source="project-authored M06b fixture", source_version="v1",
        )
        self.pois = self.datasets.register_file(
            FIXTURE / "pois.geojson", kind="vector", crs="EPSG:3006",
            source="project-authored M06b fixture", source_version="v1",
        )

    def contract(self, *, windows=None, **changes):
        values = {
            "dataset": self.gtfs,
            "population_dataset": self.population,
            "poi_dataset": self.pois,
            "service_date": date(2025, 10, 4),
            "timezone": "Europe/Stockholm",
            "windows": windows or [
                GtfsTimeWindow(window_id="morning", start_time="08:00:00", end_time="09:00:00"),
                GtfsTimeWindow(window_id="night", start_time="24:00:00", end_time="25:00:00"),
            ],
            "buffer_distance_m": 250.0,
            "quad_segs": 8,
        }
        values.update(changes)
        return AnalyzeGtfsStopCoverageInput(**values)

    def run_tool(self, contract=None):
        return ACCESSIBILITY_TOOL_REGISTRY.invoke(
            "analyze_gtfs_stop_coverage", contract or self.contract(),
            datasets=self.datasets,
        )

    def test_fixture_manifest_hashes_and_repeat_build(self):
        manifest = json.loads((FIXTURE / "manifest.json").read_text("utf-8"))
        self.assertEqual((manifest["license_id"], manifest["fixture_id"]), ("CC0-1.0", "v1"))
        for item in manifest["files"]:
            content = (FIXTURE / item["path"]).read_bytes()
            self.assertEqual(hashlib.sha256(content).hexdigest(), item["sha256"])
            self.assertEqual(len(content), item["size_bytes"])
        repeat = self.root / "repeat" / "v1"
        result = subprocess.run(
            [sys.executable, str(ROOT / "scripts/build_m06b_fixture.py"),
             "--output-dir", str(repeat)],
            cwd=ROOT, capture_output=True, text=True, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        for item in manifest["files"]:
            self.assertEqual((repeat / item["path"]).read_bytes(), (FIXTURE / item["path"]).read_bytes())
        self.assertEqual((repeat / "manifest.json").read_bytes(), (FIXTURE / "manifest.json").read_bytes())
        second = subprocess.run(
            [sys.executable, str(ROOT / "scripts/build_m06b_fixture.py"),
             "--output-dir", str(repeat)],
            cwd=ROOT, capture_output=True, text=True, check=False,
        )
        self.assertNotEqual(second.returncode, 0)

    def test_strict_contract_and_invalid_time_windows(self):
        with self.assertRaises(ValidationError):
            AnalyzeGtfsStopCoverageInput(**self.contract().model_dump(), surprise="forbidden")
        for bad in [
            {"window_id": "x", "start_time": "09:00:00", "end_time": "09:00:00"},
            {"window_id": "x", "start_time": "invalid", "end_time": "10:00:00"},
            {"window_id": "x", "start_time": "08:00:00", "end_time": "48:00:00"},
        ]:
            with self.subTest(bad=bad), self.assertRaises(ValidationError):
                GtfsTimeWindow(**bad)
        with self.assertRaises(ValidationError):
            self.contract(windows=[
                GtfsTimeWindow(window_id="same", start_time="08:00:00", end_time="09:00:00"),
                GtfsTimeWindow(window_id="same", start_time="08:30:00", end_time="10:00:00"),
            ])

    def test_morning_frequency_routes_coverage_and_conservative_population(self):
        result = self.run_tool()
        morning = result.windows[0]
        expected = EXPECTED["morning"]
        self.assertEqual(
            (morning.departure_count, morning.trip_count, morning.route_count, morning.active_stop_count),
            (expected["departure_count"], expected["trip_count"], expected["route_count"], expected["stop_count"]),
        )
        self.assertEqual(
            {item.stop_id: item.departure_count for item in morning.stop_frequencies},
            expected["stop_frequency"],
        )
        self.assertEqual(
            {item.route_id: item.departure_count for item in morning.route_frequencies},
            expected["route_departures"],
        )
        self.assertEqual(morning.fully_covered_grid_ids, expected["fully_covered_grid_ids"])
        self.assertEqual(
            morning.partially_intersecting_grid_ids, expected["partially_intersecting_grid_ids"]
        )
        self.assertEqual(morning.covered_population_lower_bound, expected["population_lower_bound"])
        self.assertEqual(morning.covered_poi_ids, expected["covered_poi_ids"])
        self.assertEqual(morning.covered_poi_count, 2)
        self.assertEqual((result.dataset.kind, result.dataset.crs, result.dataset.row_count),
                         ("vector", "EPSG:3006", 5))
        features = self.datasets.read(result.dataset.id).records
        self.assertEqual(
            [(feature["properties"]["window_id"], feature["properties"]["stop_id"]) for feature in features],
            [("morning", "A"), ("morning", "B"), ("morning", "C"), ("night", "C"), ("night", "D")],
        )

    def test_night_window_belongs_to_original_service_date_and_comparison(self):
        result = self.run_tool()
        night = result.windows[1]
        expected = EXPECTED["night"]
        self.assertEqual((night.start_time, night.end_time), ("24:00:00", "25:00:00"))
        self.assertEqual(
            (night.departure_count, night.trip_count, night.route_count, night.active_stop_count),
            (expected["departure_count"], expected["trip_count"], expected["route_count"], expected["stop_count"]),
        )
        self.assertEqual(
            {item.stop_id: item.departure_count for item in night.stop_frequencies},
            expected["stop_frequency"],
        )
        self.assertEqual(night.fully_covered_grid_ids, expected["fully_covered_grid_ids"])
        self.assertEqual(night.covered_population_lower_bound, 30)
        self.assertEqual(night.covered_poi_ids, ["P2"])
        comparison = result.comparisons[0]
        self.assertEqual((comparison.base_window_id, comparison.compared_window_id), ("morning", "night"))
        self.assertEqual((comparison.delta_departures, comparison.delta_active_stops,
                          comparison.delta_routes, comparison.delta_population_lower_bound,
                          comparison.delta_poi_count), (-3, -1, -1, -100, -1))

    def test_half_open_boundary_and_calendar_removal(self):
        early = self.run_tool(self.contract(windows=[
            GtfsTimeWindow(window_id="early", start_time="08:00:00", end_time="08:30:00"),
        ]))
        self.assertEqual(early.windows[0].departure_count, 2)
        self.assertEqual(
            {item.stop_id: item.departure_count for item in early.windows[0].stop_frequencies},
            {"A": 1, "B": 1},
        )
        removed = self.run_tool(self.contract(service_date=date(2025, 10, 6)))
        self.assertEqual([item.departure_count for item in removed.windows], [0, 0])
        self.assertEqual(removed.dataset.row_count, 0)
        self.assertEqual(removed.windows[0].covered_population_lower_bound, 0)
        self.assertIn("no_scheduled_departures_in_window", removed.metadata.warnings)

    def test_optional_inputs_and_empty_result_have_explicit_null_or_zero(self):
        result = self.run_tool(self.contract(population_dataset=None, poi_dataset=None))
        self.assertIsNone(result.windows[0].covered_population_lower_bound)
        self.assertIsNone(result.windows[0].covered_poi_count)
        self.assertIsNone(result.comparisons[0].delta_population_lower_bound)
        self.assertIsNone(result.comparisons[0].delta_poi_count)
        empty = self.run_tool(self.contract(service_date=date(2027, 1, 1)))
        self.assertEqual(empty.dataset.row_count, 0)
        self.assertEqual(empty.windows[0].fully_covered_grid_ids, [])
        self.assertEqual(empty.windows[0].covered_poi_ids, [])

    def test_crs_geometry_and_population_schema_fail_without_partial_output(self):
        bad_crs = self.datasets.register_file(
            FIXTURE / "population.geojson", kind="vector", crs="EPSG:4326",
            source="test", source_version="v1",
        )
        before = {item.id for item in self.datasets.list()}
        with self.assertRaises(AnalysisToolError) as raised:
            self.run_tool(self.contract(population_dataset=bad_crs))
        self.assertEqual(raised.exception.code, "ANALYSIS_TOOL_CRS_MISMATCH")
        self.assertEqual({item.id for item in self.datasets.list()}, before)
        bad_population = json.loads((FIXTURE / "population.geojson").read_text("utf-8"))
        bad_population["features"][0]["properties"]["population_total"] = -1
        path = self.root / "bad_population.geojson"
        path.write_text(json.dumps(bad_population), encoding="utf-8")
        bad = self.datasets.register_file(path, kind="vector", crs="EPSG:3006",
                                          source="test", source_version="v1")
        before = {item.id for item in self.datasets.list()}
        with self.assertRaises(AnalysisToolError) as raised:
            self.run_tool(self.contract(population_dataset=bad))
        self.assertEqual(raised.exception.code, "ANALYSIS_TOOL_GTFS_POPULATION_INVALID")
        self.assertEqual({item.id for item in self.datasets.list()}, before)

    def test_lineage_deterministic_content_and_offline_execution(self):
        prior = {item.id: item.content_hash for item in self.datasets.list()}
        with patch("urllib.request.urlopen", side_effect=AssertionError("network forbidden")):
            first = self.run_tool()
            second = self.run_tool()
        self.assertNotEqual(first.dataset.id, second.dataset.id)
        self.assertEqual(first.dataset.content_hash, second.dataset.content_hash)
        self.assertEqual(set(first.dataset.lineage[0].parent_ids),
                         {self.gtfs.id, self.population.id, self.pois.id})
        self.assertEqual(first.dataset.lineage[0].parameters_hash,
                         second.dataset.lineage[0].parameters_hash)
        self.assertEqual(first.dataset.lifecycle, "temporary")
        for item_id, digest in prior.items():
            self.assertEqual(self.datasets.get(item_id).content_hash, digest)

    def test_real_official_m06a_fixture_is_compatible_but_not_a_complete_timetable(self):
        real = self.datasets.register_file(
            REAL_ARCHIVE, kind="network", crs="EPSG:4326",
            source="Samtrafiken GTFS Sverige 2", source_version="2025-10-04",
        )
        result = self.run_tool(self.contract(
            dataset=real, population_dataset=None, poi_dataset=None,
        ))
        self.assertGreater(result.windows[0].departure_count, 0)
        self.assertGreater(result.windows[1].departure_count, 0)
        self.assertIn("gtfs_feed_completeness_not_verified", result.metadata.warnings)


if __name__ == "__main__":
    unittest.main()
