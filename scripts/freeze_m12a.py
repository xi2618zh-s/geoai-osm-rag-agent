"""Write the M12a freeze manifest (exclusive create) or verify the current tree against it.

The manifest pins the protocol document, cases, oracle references, fixtures and
precheck reports before any scenario-layer code exists. The evaluation runner and
audit refuse to run when any pinned file changed.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "benchmarks/results/m12/m12a/freeze/m12a_freeze_manifest_v1.json"
PINNED = (
    "docs/roadmap/M12A_EVALUATION_FREEZE.md",
    "benchmarks/data/dev/m12a_scenario_cases_v1.jsonl",
    "benchmarks/data/dev/m12a_references_v1.json",
    "benchmarks/m12a_oracle.py",
    "scripts/build_m12a_references.py",
    "scripts/build_m12_lund_data.py",
    "benchmarks/results/m12/precheck/m12_data_precheck_v1.json",
    "benchmarks/results/m12/precheck/m12_data_precheck_v2.json",
    "data/fixtures/population/scb/m12_lund_v1/manifest.json",
    "data/fixtures/population/scb/m12_lund_v1/download_receipt.json",
    "data/fixtures/population/scb/m12_lund_v1/scb_population_grid_2025_lund_20km_raw.geojson",
    "data/fixtures/osm/m12_lund_poi_v1/manifest.json",
    "data/fixtures/osm/m12_lund_poi_v1/README.md",
    "data/fixtures/osm/m12_lund_poi_v1/lund_poi_points_v1.geojson",
)
THRESHOLDS = {"cases_passed": 13, "case_count": 13, "share_relative_tolerance": 1e-6,
              "share_zero_absolute_tolerance": 1e-12, "min_success_analysis_steps": 5,
              "deterministic_runs": 2, "max_tool_calls": 64, "max_plan_steps": 12}


def current_hashes() -> dict[str, str]:
    return {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in PINNED}


def verify(path: Path = MANIFEST) -> dict:
    manifest = json.loads(Path(path).read_bytes())
    changed = sorted(name for name, sha in manifest["files"].items()
                     if hashlib.sha256((ROOT / name).read_bytes()).hexdigest() != sha)
    if changed:
        raise SystemExit(f"M12a frozen files changed: {changed}")
    return manifest


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("freeze", "verify"))
    args = parser.parse_args(argv)
    if args.command == "verify":
        verify()
        print(f"[PASS] M12a freeze manifest {hashlib.sha256(MANIFEST.read_bytes()).hexdigest()}")
        return 0
    if (ROOT / "src/scenarios").exists():
        raise SystemExit("Freeze must precede scenario-layer code: src/scenarios already exists")
    manifest = {
        "schema": "m12a-freeze-manifest-v1", "protocol": "m12a-protocol-v1", "grammar": "m12a-grammar-v1",
        "plan": "m12a-plan-v1", "frozen_on": "2026-10-08", "files": current_hashes(), "thresholds": THRESHOLDS,
        "note": "Frozen after precheck gate v2, fixtures and the independent oracle, before any src/scenarios code.",
    }
    MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    with MANIFEST.open("xb") as stream:
        stream.write((json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8"))
    print(f"[FROZEN] {MANIFEST.relative_to(ROOT).as_posix()} {hashlib.sha256(MANIFEST.read_bytes()).hexdigest()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
