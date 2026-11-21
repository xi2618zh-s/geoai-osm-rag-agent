"""Run the offline synthetic M06b stop-service reference without persistent output."""

from __future__ import annotations

from datetime import date
import json
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.datasets import LocalDatasetRegistry
from src.tools import (
    ACCESSIBILITY_TOOL_REGISTRY,
    AnalyzeGtfsStopCoverageInput,
    GtfsTimeWindow,
)


FIXTURE = ROOT / "data/fixtures/gtfs/m06b/v1"


def main() -> int:
    expected = json.loads((FIXTURE / "expected.json").read_text("utf-8"))
    with tempfile.TemporaryDirectory(prefix="m06b_reference_") as directory:
        registry = LocalDatasetRegistry(Path(directory))
        gtfs = registry.register_file(
            FIXTURE / "schedule.gtfs.zip", kind="network", crs="EPSG:4326",
            source="project-authored M06b fixture", source_version="v1",
        )
        population = registry.register_file(
            FIXTURE / "population.geojson", kind="vector", crs="EPSG:3006",
            source="project-authored M06b fixture", source_version="v1",
        )
        pois = registry.register_file(
            FIXTURE / "pois.geojson", kind="vector", crs="EPSG:3006",
            source="project-authored M06b fixture", source_version="v1",
        )
        result = ACCESSIBILITY_TOOL_REGISTRY.invoke(
            "analyze_gtfs_stop_coverage",
            AnalyzeGtfsStopCoverageInput(
                dataset=gtfs,
                population_dataset=population,
                poi_dataset=pois,
                service_date=date.fromisoformat(expected["service_date"]),
                timezone=expected["timezone"],
                windows=[
                    GtfsTimeWindow(window_id="morning", start_time="08:00:00", end_time="09:00:00"),
                    GtfsTimeWindow(window_id="night", start_time="24:00:00", end_time="25:00:00"),
                ],
                buffer_distance_m=float(expected["buffer_distance_m"]),
            ),
            datasets=registry,
        )
        summary = {
            "fixture_kind": "project-authored synthetic, not a complete timetable",
            "service_date": result.service_date.isoformat(),
            "timezone": result.timezone,
            "buffer_crs": result.buffer_crs,
            "buffer_distance_m": result.buffer_distance_m,
            "windows": [
                {
                    "window_id": item.window_id,
                    "departure_count": item.departure_count,
                    "trip_count": item.trip_count,
                    "route_count": item.route_count,
                    "stop_count": item.active_stop_count,
                    "population_lower_bound": item.covered_population_lower_bound,
                    "poi_count": item.covered_poi_count,
                }
                for item in result.windows
            ],
            "comparisons": [
                item.model_dump(mode="json") for item in result.comparisons
            ],
            "warnings": result.metadata.warnings,
        }
        for item in summary["windows"]:
            expected_window = expected[item["window_id"]]
            for actual_key, expected_key in (
                ("departure_count", "departure_count"),
                ("trip_count", "trip_count"),
                ("route_count", "route_count"),
                ("stop_count", "stop_count"),
                ("population_lower_bound", "population_lower_bound"),
            ):
                if item[actual_key] != expected_window[expected_key]:
                    raise AssertionError(f"{item['window_id']} {actual_key} differs from fixture reference")
            if item["poi_count"] != len(expected_window["covered_poi_ids"]):
                raise AssertionError(f"{item['window_id']} POI count differs from fixture reference")
        print(json.dumps(summary, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
