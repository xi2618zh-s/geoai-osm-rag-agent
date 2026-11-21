"""Derive an immutable, small Lund GTFS fixture from the pinned official archive."""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import sys
import uuid
import zipfile

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.gtfs.source import EXPECTED_SHA256, SOURCE_LICENSE, SOURCE_URL, SOURCE_VERSION

SOURCE_SHA256 = EXPECTED_SHA256
SELECTED_TRIP_IDS = (
    "22760731000017",  # Weekday example; no service on 2025-10-04.
    "22760731000310",  # Lund route 7, 08:00 at Lund C.
    "22760731000312",  # Lund route 7, 08:31 at Lund C.
    "22760731000314",  # Lund route 7, 09:00 at Lund C.
    "23000803010171",  # Öresundståg, 24:09 at Lund C.
)
FILES = (
    "agency.txt", "stops.txt", "routes.txt", "trips.txt", "stop_times.txt",
    "calendar.txt", "calendar_dates.txt", "feed_info.txt",
)


def sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def read_table(archive: zipfile.ZipFile, name: str, *, keep=None) -> tuple[list[str], list[dict[str, str]]]:
    with archive.open(name) as stream:
        with io.TextIOWrapper(stream, encoding="utf-8-sig", newline="") as text:
            reader = csv.DictReader(text)
            if not reader.fieldnames:
                raise ValueError(f"missing GTFS header: {name}")
            return list(reader.fieldnames), [row for row in reader if keep is None or keep(row)]


def csv_bytes(headers: list[str], rows: list[dict[str, str]]) -> bytes:
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=headers, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return output.getvalue().encode("utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    source_dir = args.source_dir.resolve()
    output = args.output_dir.resolve()
    if output.exists():
        raise SystemExit("GTFS_FIXTURE_EXISTS: refusing to overwrite fixture")
    archive_path = source_dir / "source.zip"
    receipt = json.loads((source_dir / "receipt.json").read_text("utf-8"))
    content_hash = sha256(archive_path.read_bytes())
    if content_hash != SOURCE_SHA256 or receipt["sha256"] != SOURCE_SHA256:
        raise ValueError("GTFS_SOURCE_HASH_MISMATCH")
    if (receipt["source_url"], receipt["source_version"], receipt["license_id"]) != (
        SOURCE_URL, SOURCE_VERSION, SOURCE_LICENSE
    ):
        raise ValueError("GTFS_SOURCE_METADATA_MISMATCH")

    trip_ids = set(SELECTED_TRIP_IDS)
    with zipfile.ZipFile(archive_path) as archive:
        tables = {
            name: read_table(archive, name, keep=(lambda row: row["trip_id"] in trip_ids) if name == "stop_times.txt" else None)
            for name in FILES
        }
    trips = [row for row in tables["trips.txt"][1] if row["trip_id"] in trip_ids]
    if {row["trip_id"] for row in trips} != trip_ids:
        raise ValueError("GTFS_FIXTURE_TRIP_MISSING")
    route_ids = {row["route_id"] for row in trips}
    service_ids = {row["service_id"] for row in trips}
    stop_times = [row for row in tables["stop_times.txt"][1] if row["trip_id"] in trip_ids]
    stop_ids = {row["stop_id"] for row in stop_times}
    routes = [row for row in tables["routes.txt"][1] if row["route_id"] in route_ids]
    agency_ids = {row["agency_id"] for row in routes}
    selected = {
        "agency.txt": [row for row in tables["agency.txt"][1] if row["agency_id"] in agency_ids],
        "stops.txt": [row for row in tables["stops.txt"][1] if row["stop_id"] in stop_ids],
        "routes.txt": routes,
        "trips.txt": trips,
        "stop_times.txt": stop_times,
        "calendar.txt": [row for row in tables["calendar.txt"][1] if row["service_id"] in service_ids],
        "calendar_dates.txt": [row for row in tables["calendar_dates.txt"][1] if row["service_id"] in service_ids],
        "feed_info.txt": tables["feed_info.txt"][1],
    }
    if ({row["stop_id"] for row in selected["stops.txt"]} != stop_ids
            or {row["route_id"] for row in routes} != route_ids
            or {row["agency_id"] for row in selected["agency.txt"]} != agency_ids
            or {row["service_id"] for row in selected["calendar.txt"]} != service_ids):
        raise ValueError("GTFS_FIXTURE_REFERENCE_MISSING")
    sort_key = {
        "agency.txt": lambda row: row["agency_id"],
        "stops.txt": lambda row: row["stop_id"],
        "routes.txt": lambda row: row["route_id"],
        "trips.txt": lambda row: row["trip_id"],
        "stop_times.txt": lambda row: (row["trip_id"], int(row["stop_sequence"])),
        "calendar.txt": lambda row: row["service_id"],
        "calendar_dates.txt": lambda row: (row["service_id"], row["date"]),
        "feed_info.txt": lambda row: row["feed_id"],
    }
    table_payloads = {
        name: csv_bytes(tables[name][0], sorted(selected[name], key=sort_key[name]))
        for name in FILES
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    staging = output.parent / f".staging-{output.name}-{uuid.uuid4().hex[:10]}"
    staging.mkdir()
    try:
        zip_path = staging / "lund_subset.gtfs.zip"
        with zipfile.ZipFile(zip_path, "w") as fixture:
            for name in FILES:
                info = zipfile.ZipInfo(name, (1980, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = 0o644 << 16
                fixture.writestr(info, table_payloads[name])
        readme = (
            "# Lund GTFS Sverige 2 fixed-source fixture\n\n"
            "Five actual trip records and their complete stop-time chains are selected "
            "from the official 2025-10-04 historical archive. Related agency, stop, "
            "route, calendar, calendar_dates and feed_info rows are retained. "
            "Rows are reordered deterministically and CSV is serialized as UTF-8/LF; "
            "trip times, identifiers, coordinates and service dates are unchanged. "
            "This is a tiny contract fixture, not a complete timetable.\n\n"
            f"Source: {SOURCE_URL}\nLicense: CC0-1.0 (Trafiklab/Samtrafiken).\n"
        ).encode("utf-8")
        (staging / "README.md").write_bytes(readme)
        manifest = {
            "schema_version": "1.0",
            "fixture_id": output.name,
            "source_url": SOURCE_URL,
            "source_version": SOURCE_VERSION,
            "source_sha256": SOURCE_SHA256,
            "source_retrieved_at": receipt["retrieved_at"],
            "source_etag": receipt["etag"],
            "source_last_modified": receipt["last_modified"],
            "license_id": SOURCE_LICENSE,
            "license_page": receipt["license_page"],
            "feed_version": selected["feed_info.txt"][0]["feed_version"],
            "agency_timezone": "Europe/Stockholm",
            "selection_rule": "Exact selected_trip_ids; retain all their stop_times and referenced rows; retain all calendar_dates for their service_ids; preserve field values; sort rows and UTF-8/LF serialize.",
            "selected_trip_ids": list(SELECTED_TRIP_IDS),
            "table_records": [
                {"path": name, "row_count": len(selected[name]), "sha256": sha256(table_payloads[name]), "size_bytes": len(table_payloads[name])}
                for name in FILES
            ],
            "files": [
                {"path": "README.md", "sha256": sha256(readme), "size_bytes": len(readme)},
                {"path": zip_path.name, "sha256": sha256(zip_path.read_bytes()), "size_bytes": zip_path.stat().st_size},
            ],
        }
        (staging / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )
        os.replace(staging, output)
    finally:
        if staging.exists():
            shutil.rmtree(staging)
    print(json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
