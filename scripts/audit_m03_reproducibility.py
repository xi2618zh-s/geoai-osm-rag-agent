"""Rebuild the immutable M03 catalog and index once and compare every byte."""

from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
CATALOG_NAME = "m03_phase1_2026-10-05_v1"
INDEX_NAME = "m03_phase1_v2_e8f8c211"
RAW_DIR = ROOT / "data/knowledge/osm/v2/raw" / CATALOG_NAME
CATALOG_DIR = ROOT / "data/knowledge/osm/v2/releases" / CATALOG_NAME
INDEX_DIR = ROOT / "faiss_index" / INDEX_NAME
MODEL_RECEIPT = ROOT / "data/knowledge/osm/v2/model/multilingual_minilm_l12_v2_e8f8c211/receipt.json"


def run(*args: str) -> None:
    result = subprocess.run(
        [sys.executable, *args],
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        check=False,
    )
    if result.returncode:
        raise RuntimeError(f"rebuild failed ({result.returncode}):\n{result.stdout}")


def compare(label: str, original: Path, repeated: Path) -> None:
    expected = sorted(path.name for path in original.iterdir() if path.is_file())
    actual = sorted(path.name for path in repeated.iterdir() if path.is_file())
    if expected != actual:
        raise ValueError(f"{label}: file set differs: {expected!r} vs {actual!r}")
    for name in expected:
        original_hash = hashlib.sha256((original / name).read_bytes()).hexdigest()
        repeated_hash = hashlib.sha256((repeated / name).read_bytes()).hexdigest()
        if original_hash != repeated_hash:
            raise ValueError(f"{label}: {name} differs: {original_hash} vs {repeated_hash}")
        print(f"[PASS] {label}/{name}: {original_hash}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-dir", type=Path, required=True)
    args = parser.parse_args()
    if not args.model_dir.is_dir():
        raise ValueError("pinned model directory is missing")
    # Only this freshly created, uniquely named temporary directory is removed.
    with tempfile.TemporaryDirectory(prefix=".m03-repro-", dir=ROOT) as temporary:
        base = Path(temporary)
        repeated_catalog = base / "catalog" / CATALOG_NAME
        run(
            str(ROOT / "scripts/build_osm_knowledge.py"),
            "--candidates", str(ROOT / "data/knowledge/osm/v2/candidates/m03_phase1_candidates_v1.json"),
            "--candidate-sha256", str(ROOT / "data/knowledge/osm/v2/candidates/m03_phase1_candidates_v1.sha256"),
            "--raw-dir", str(RAW_DIR),
            "--curation", str(ROOT / "data/knowledge/osm/v2/curation/m03_phase1_curation_v2.json"),
            "--curation-sha256", str(ROOT / "data/knowledge/osm/v2/curation/m03_phase1_curation_v2.sha256"),
            "--output-dir", str(repeated_catalog),
            "--catalog-version", "m03-phase1-2026-10-05.v1",
            "--build-timestamp", "2026-10-05T00:00:00Z",
        )
        compare("catalog", CATALOG_DIR, repeated_catalog)

        repeated_index = base / "index" / INDEX_NAME
        run(
            str(ROOT / "scripts/build_m03_indexes.py"),
            "--release-dir", str(repeated_catalog),
            "--model-dir", str(args.model_dir.resolve()),
            "--model-receipt", str(MODEL_RECEIPT),
            "--output-dir", str(repeated_index),
            "--build-timestamp", "2026-10-05T00:00:00Z",
        )
        compare("index", INDEX_DIR, repeated_index)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
