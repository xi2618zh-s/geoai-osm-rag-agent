"""Build the frozen M12a reference answers with the independent oracle (exclusive create).

Only ``benchmarks.m12a_oracle`` (json/shapely/pyproj) computes the answers; the system
under test is not imported. Output records oracle, fixture and case-file hashes.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from benchmarks import m12a_oracle

CASES = ROOT / "benchmarks/data/dev/m12a_scenario_cases_v1.jsonl"
OUTPUT = ROOT / "benchmarks/data/dev/m12a_references_v1.json"
SCB = ROOT / "data/fixtures/population/scb/m12_lund_v1/scb_population_grid_2025_lund_20km_raw.geojson"
POI = ROOT / "data/fixtures/osm/m12_lund_poi_v1/lund_poi_points_v1.geojson"
HASHED = (
    "benchmarks/m12a_oracle.py", "benchmarks/data/dev/m12a_scenario_cases_v1.jsonl",
    "data/fixtures/population/scb/m12_lund_v1/manifest.json",
    "data/fixtures/population/scb/m12_lund_v1/scb_population_grid_2025_lund_20km_raw.geojson",
    "data/fixtures/osm/m12_lund_poi_v1/manifest.json",
    "data/fixtures/osm/m12_lund_poi_v1/lund_poi_points_v1.geojson",
)


def reference_key(parameters: dict) -> str:
    return f"d{parameters['distance_m']}_b{parameters['age_band']}_n{parameters['threshold']}"


def build() -> dict:
    cases = [json.loads(line) for line in CASES.read_text(encoding="utf-8").splitlines() if line.strip()]
    references = {}
    for case in cases:
        expected = case["expected"]
        if expected["reference"] is None:
            continue
        key = reference_key(expected["parameters"])
        if key != expected["reference"]:
            raise SystemExit(f"{case['case_id']}: reference key {expected['reference']} != {key}")
        if key not in references:
            params = expected["parameters"]
            references[key] = {"parameters": params, **m12a_oracle.reference(
                SCB, POI, distance_m=params["distance_m"], band=params["age_band"], threshold=params["threshold"])}
    return {
        "schema": "m12a-references-v1", "oracle": "benchmarks/m12a_oracle.py",
        "hashes": {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in HASHED},
        "case_count": len(cases), "references": dict(sorted(references.items())),
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    parser.add_argument("--check", action="store_true", help="rebuild in memory and compare with --output")
    args = parser.parse_args(argv)
    payload = (json.dumps(build(), ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")
    if args.check:
        if Path(args.output).read_bytes() != payload:
            raise SystemExit("M12a references differ from an oracle rebuild")
        print(f"[PASS] M12a references {hashlib.sha256(payload).hexdigest()}")
        return 0
    with Path(args.output).open("xb") as stream:
        stream.write(payload)
    print(json.dumps({"output": Path(args.output).name, "sha256": hashlib.sha256(payload).hexdigest()}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
