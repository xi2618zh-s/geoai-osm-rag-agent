"""Build the project-authored, deterministic M06b schedule/spatial fixture offline."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import uuid
import zipfile


TABLES = {
    "agency.txt": "agency_id,agency_name,agency_url,agency_timezone\nA,Fixture Transit,https://example.org,Europe/Stockholm\n",
    "stops.txt": (
        "stop_id,stop_name,stop_lat,stop_lon\n"
        "A,Fixture A,55.7073446614,13.1857087920\n"
        "B,Fixture B,55.7073681538,13.1872993814\n"
        "C,Fixture C,55.7082682957,13.2493341830\n"
        "D,Fixture D,55.7087177567,13.2811482500\n"
    ),
    "routes.txt": (
        "route_id,agency_id,route_short_name,route_long_name,route_type\n"
        "R1,A,1,,3\nR2,A,2,,3\n"
    ),
    "trips.txt": (
        "route_id,service_id,trip_id\n"
        "R1,WEEKEND,T1\nR1,WEEKEND,T2\nR2,WEEKEND,T3\n"
        "R2,WEEKEND,T4\nR1,WEEKDAY,T5\n"
    ),
    "stop_times.txt": (
        "trip_id,arrival_time,departure_time,stop_id,stop_sequence\n"
        "T1,08:00:00,08:00:00,A,1\nT1,08:10:00,08:10:00,B,2\n"
        "T2,08:30:00,08:30:00,A,1\nT2,08:40:00,08:40:00,B,2\n"
        "T3,08:45:00,08:45:00,C,1\nT3,09:10:00,09:10:00,D,2\n"
        "T4,24:10:00,24:10:00,C,1\nT4,24:30:00,24:30:00,D,2\n"
        "T5,08:15:00,08:15:00,A,1\nT5,08:25:00,08:25:00,B,2\n"
    ),
    "calendar.txt": (
        "service_id,monday,tuesday,wednesday,thursday,friday,saturday,sunday,start_date,end_date\n"
        "WEEKDAY,1,0,0,0,0,0,0,20251001,20251031\n"
        "WEEKEND,0,0,0,0,0,0,0,20251001,20251031\n"
    ),
    "calendar_dates.txt": (
        "service_id,date,exception_type\n"
        "WEEKDAY,20251006,2\nWEEKEND,20251004,1\n"
    ),
    "feed_info.txt": (
        "feed_publisher_name,feed_publisher_url,feed_lang,feed_version\n"
        "Fixture Transit,https://example.org,en,m06b-synthetic-v1\n"
    ),
}


def rectangle(x1: float, y1: float, x2: float, y2: float) -> dict:
    return {
        "type": "Polygon",
        "coordinates": [[
            [x1, y1], [x2, y1], [x2, y2], [x1, y2], [x1, y1],
        ]],
    }


def feature(fid: str, geometry: dict, properties: dict) -> dict:
    return {"type": "Feature", "id": fid, "geometry": geometry, "properties": properties}


def json_bytes(value: dict) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
        + "\n"
    ).encode("utf-8")


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    output = args.output_dir.resolve()
    if output.exists():
        raise SystemExit("M06B_FIXTURE_EXISTS: refusing to overwrite")
    output.parent.mkdir(parents=True, exist_ok=True)
    staging = output.parent / f".staging-{output.name}-{uuid.uuid4().hex[:10]}"
    staging.mkdir()
    try:
        archive_path = staging / "schedule.gtfs.zip"
        with zipfile.ZipFile(archive_path, "w") as archive:
            for name, content in sorted(TABLES.items()):
                info = zipfile.ZipInfo(name, (1980, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = 0o644 << 16
                archive.writestr(info, content.encode("utf-8"))

        population = {
            "type": "FeatureCollection",
            "crs": {"type": "name", "properties": {"name": "EPSG:3006"}},
            "features": [
                feature("G1", rectangle(385925, 6174925, 386175, 6175075),
                        {"grid_id": "G1", "population_total": 100, "population_reference_date": "2025-12-31"}),
                feature("G2", rectangle(386250, 6174900, 386500, 6175100),
                        {"grid_id": "G2", "population_total": 80, "population_reference_date": "2025-12-31"}),
                feature("G3", rectangle(389925, 6174925, 390075, 6175075),
                        {"grid_id": "G3", "population_total": 30, "population_reference_date": "2025-12-31"}),
            ],
        }
        pois = {
            "type": "FeatureCollection",
            "crs": {"type": "name", "properties": {"name": "EPSG:3006"}},
            "features": [
                feature("P1", {"type": "Point", "coordinates": [386050, 6175000]}, {"poi_id": "P1"}),
                feature("P2", {"type": "Point", "coordinates": [390000, 6175000]}, {"poi_id": "P2"}),
                feature("P3", {"type": "Point", "coordinates": [395000, 6175000]}, {"poi_id": "P3"}),
            ],
        }
        (staging / "population.geojson").write_bytes(json_bytes(population))
        (staging / "pois.geojson").write_bytes(json_bytes(pois))
        expected = {
            "schema_version": "1.0",
            "service_date": "2025-10-04",
            "timezone": "Europe/Stockholm",
            "buffer_distance_m": 250,
            "morning": {
                "departure_count": 5, "trip_count": 3, "route_count": 2, "stop_count": 3,
                "stop_frequency": {"A": 2, "B": 2, "C": 1},
                "route_departures": {"R1": 4, "R2": 1},
                "fully_covered_grid_ids": ["G1", "G3"],
                "partially_intersecting_grid_ids": ["G2"],
                "population_lower_bound": 130,
                "covered_poi_ids": ["P1", "P2"],
            },
            "night": {
                "departure_count": 2, "trip_count": 1, "route_count": 1, "stop_count": 2,
                "stop_frequency": {"C": 1, "D": 1},
                "route_departures": {"R2": 2},
                "fully_covered_grid_ids": ["G3"],
                "partially_intersecting_grid_ids": [],
                "population_lower_bound": 30,
                "covered_poi_ids": ["P2"],
            },
        }
        (staging / "expected.json").write_bytes(json_bytes(expected))
        readme = (
            "# M06b deterministic synthetic fixture\n\n"
            "Project-authored CC0-1.0 GTFS and EPSG:3006 geometries, not a real timetable. "
            "Stops A/B overlap under 250 m buffers; C/D are distant. "
            "The 2025-10-04 service is added through calendar_dates; weekday service "
            "is removed on 2025-10-06. T4 departs at 24:10/24:30 on the Oct 4 service day. "
            "Population is a fully-contained-grid conservative lower bound; partially "
            "intersecting G2 is excluded. P1 lies in both A/B buffers and is counted once.\n"
        ).encode("utf-8")
        (staging / "README.md").write_bytes(readme)
        records = []
        for name in ("README.md", "schedule.gtfs.zip", "population.geojson", "pois.geojson", "expected.json"):
            payload = (staging / name).read_bytes()
            records.append({"path": name, "sha256": sha256(payload), "size_bytes": len(payload)})
        manifest = {
            "schema_version": "1.0",
            "fixture_id": output.name,
            "origin": "Project-authored synthetic GTFS and spatial data",
            "license_id": "CC0-1.0",
            "crs": {"gtfs": "EPSG:4326", "spatial": "EPSG:3006"},
            "generation_script": "scripts/build_m06b_fixture.py",
            "files": records,
        }
        (staging / "manifest.json").write_bytes(json_bytes(manifest))
        os.replace(staging, output)
        print(json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2))
    finally:
        if staging.exists():
            shutil.rmtree(staging)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
