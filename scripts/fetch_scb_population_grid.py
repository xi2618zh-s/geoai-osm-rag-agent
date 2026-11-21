"""Download one bounded SCB 2025 population-grid GeoJSON plus a receipt.

This network entrypoint is intentionally separate from the offline
``load_population_dataset`` DatasetRef normalization tool.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.population import (
    PopulationAdapterError,
    ScbPopulationGridRequest,
    download_scb_population_grid,
)
from src.population.contracts import SCB_PROPERTY_NAMES


SOURCE_PAGE = "https://www.scb.se/en/services/open-data-api/open-geodata/grid-statistics/"
LICENSE_PAGE = "https://www.scb.se/en/services/open-data-api/"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Fetch a fixed-BBOX SCB 2025 1 km population-grid GeoJSON."
    )
    parser.add_argument(
        "--bbox",
        nargs=4,
        type=float,
        required=True,
        metavar=("MIN_X", "MIN_Y", "MAX_X", "MAX_Y"),
        help="EPSG:3006 bounding box; the request is sorted by objectid.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        required=True,
        help="Destination .geojson path. A sibling .receipt.json is also written.",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Explicitly allow replacement of both output and receipt files.",
    )
    return parser


def _atomic_write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent, delete=False
    ) as stream:
        temporary = Path(stream.name)
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())
    try:
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    output = args.output.expanduser().resolve()
    if output.suffix.lower() != ".geojson":
        raise SystemExit("--output must end in .geojson")
    receipt_path = output.with_suffix(".receipt.json")
    if not args.overwrite and (output.exists() or receipt_path.exists()):
        raise SystemExit("Refusing to overwrite output or receipt without --overwrite")
    request = ScbPopulationGridRequest(bbox=list(args.bbox))
    try:
        download = download_scb_population_grid(request)
    except PopulationAdapterError as error:
        raise SystemExit(f"{error.code}: {error}") from error
    receipt = {
        **download.receipt.model_dump(mode="json"),
        "request": request.model_dump(mode="json"),
        "property_names": list(SCB_PROPERTY_NAMES),
        "source_page": SOURCE_PAGE,
        "license_page": LICENSE_PAGE,
        "output_filename": output.name,
    }
    receipt_payload = (
        json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    _atomic_write(output, download.content)
    _atomic_write(receipt_path, receipt_payload)
    print(f"Saved {download.receipt.feature_count} features to {output}")
    print(f"Receipt: {receipt_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
