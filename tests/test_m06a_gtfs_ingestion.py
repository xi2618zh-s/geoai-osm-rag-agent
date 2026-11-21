"""Offline contract tests for fixed-source M06a GTFS ingestion."""

from __future__ import annotations

import csv
from datetime import date
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from src.datasets import LocalDatasetRegistry
from src.gtfs.ingest import (
    GtfsValidationError,
    active_service_ids,
    active_trip_ids,
    gtfs_local_datetime,
    load_gtfs_feed,
    parse_gtfs_time,
)
from src.gtfs.source import EXPECTED_SHA256, GtfsSourceError, download_frozen_source


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "data/fixtures/gtfs/sverige2/lund_20251004_v1"
ARCHIVE = FIXTURE / "lund_subset.gtfs.zip"


class FakeResponse(io.BytesIO):
    status = 200

    def __init__(self, payload: bytes, *, url: str, content_type: str = "application/zip"):
        super().__init__(payload)
        self._url = url
        self.headers = {"Content-Type": content_type, "Content-Length": str(len(payload))}

    def geturl(self) -> str:
        return self._url


class M06aGtfsIngestionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.temp_path = Path(self.temp.name)

    def assert_code(self, code: str, call, *args, **kwargs):
        with self.assertRaises((GtfsValidationError, GtfsSourceError)) as raised:
            call(*args, **kwargs)
        self.assertEqual(raised.exception.code, code)

    def mutate_zip(self, change) -> Path:
        with zipfile.ZipFile(ARCHIVE) as source:
            payloads = {name: source.read(name) for name in source.namelist()}
        change(payloads)
        target = self.temp_path / "mutated.zip"
        with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as output:
            for name, content in sorted(payloads.items()):
                output.writestr(name, content)
        return target

    @staticmethod
    def change_first_row(payloads: dict[str, bytes], name: str, field: str, value: str):
        reader = csv.DictReader(io.StringIO(payloads[name].decode("utf-8")))
        rows = list(reader)
        rows[0][field] = value
        output = io.StringIO(newline="")
        writer = csv.DictWriter(output, fieldnames=reader.fieldnames, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
        payloads[name] = output.getvalue().encode("utf-8")

    def test_official_fixture_manifest_and_source_lineage(self):
        manifest = json.loads((FIXTURE / "manifest.json").read_text("utf-8"))
        self.assertEqual(manifest["license_id"], "CC0-1.0")
        self.assertEqual(manifest["feed_version"], "2025-10-04")
        self.assertEqual(len(manifest["selected_trip_ids"]), 5)
        for record in manifest["files"]:
            content = (FIXTURE / record["path"]).read_bytes()
            self.assertEqual(hashlib.sha256(content).hexdigest(), record["sha256"])
            self.assertEqual(len(content), record["size_bytes"])
        with zipfile.ZipFile(ARCHIVE) as archive:
            for record in manifest["table_records"]:
                content = archive.read(record["path"])
                self.assertEqual(hashlib.sha256(content).hexdigest(), record["sha256"])

    def test_valid_feed_and_gtfs_time_over_24_hours(self):
        feed = load_gtfs_feed(ARCHIVE)
        self.assertEqual(feed.agency_timezone, "Europe/Stockholm")
        self.assertEqual(feed.feed_version, "2025-10-04")
        self.assertEqual(len(feed.stops), 42)
        self.assertEqual(len(feed.routes), 2)
        self.assertEqual(len(feed.trips), 5)
        self.assertEqual(len(feed.stop_times), 128)
        self.assertEqual(parse_gtfs_time("24:09:00"), 24 * 3600 + 9 * 60)
        self.assertEqual(parse_gtfs_time("25:00:01"), 25 * 3600 + 1)
        local = gtfs_local_datetime(date(2025, 10, 4), "24:09:00", "Europe/Stockholm")
        self.assertEqual((local.year, local.month, local.day, local.hour, local.minute), (2025, 10, 5, 0, 9))
        self.assertIsNotNone(local.utcoffset())

    def test_service_date_exception_addition_and_no_service(self):
        feed = load_gtfs_feed(ARCHIVE)
        self.assertEqual(active_service_ids(feed, date(2025, 10, 4)), ("000349", "000376"))
        self.assertEqual(
            active_trip_ids(feed, date(2025, 10, 4)),
            ("22760731000310", "22760731000312", "22760731000314", "23000803010171"),
        )
        self.assertEqual(active_trip_ids(feed, date(2027, 1, 1)), ())

    def test_calendar_dates_only_feed_is_valid(self):
        path = self.mutate_zip(lambda files: files.pop("calendar.txt"))
        feed = load_gtfs_feed(path)
        self.assertEqual(active_service_ids(feed, date(2025, 10, 4)), ("000349", "000376"))

    def test_calendar_only_feed_is_valid_and_can_have_no_trips(self):
        path = self.mutate_zip(lambda files: files.pop("calendar_dates.txt"))
        feed = load_gtfs_feed(path)
        self.assertEqual(active_trip_ids(feed, date(2025, 10, 4)), ())

    def test_calendar_dates_removal_overrides_weekly_service(self):
        def change(files):
            self.change_first_row(files, "calendar.txt", "saturday", "1")
            reader = csv.DictReader(io.StringIO(files["calendar.txt"].decode("utf-8")))
            rows = list(reader)
            for row in rows:
                if row["service_id"] == "000015":
                    row["saturday"] = "1"
            output = io.StringIO(newline="")
            writer = csv.DictWriter(output, fieldnames=reader.fieldnames, lineterminator="\n")
            writer.writeheader(); writer.writerows(rows)
            files["calendar.txt"] = output.getvalue().encode("utf-8")
            files["calendar_dates.txt"] += b"000015,20251004,2\n"
        feed = load_gtfs_feed(self.mutate_zip(change))
        self.assertNotIn("000015", active_service_ids(feed, date(2025, 10, 4)))
        self.assertIn("000015", active_service_ids(feed, date(2025, 10, 11)))

    def test_missing_required_and_both_calendars(self):
        path = self.mutate_zip(lambda files: files.pop("stop_times.txt"))
        self.assert_code("GTFS_REQUIRED_FILE_MISSING", load_gtfs_feed, path)
        path = self.mutate_zip(lambda files: (files.pop("calendar.txt"), files.pop("calendar_dates.txt")))
        self.assert_code("GTFS_REQUIRED_FILE_MISSING", load_gtfs_feed, path)

    def test_missing_stop_route_trip_and_service_references(self):
        cases = (
            ("stop_times.txt", "stop_id", "missing-stop"),
            ("stop_times.txt", "trip_id", "missing-trip"),
            ("trips.txt", "route_id", "missing-route"),
            ("trips.txt", "service_id", "missing-service"),
            ("routes.txt", "agency_id", "missing-agency"),
        )
        for filename, field, value in cases:
            with self.subTest(filename=filename, field=field):
                path = self.mutate_zip(
                    lambda files, n=filename, f=field, v=value: self.change_first_row(files, n, f, v)
                )
                self.assert_code("GTFS_FOREIGN_KEY_MISSING", load_gtfs_feed, path)

    def test_duplicate_id_and_stop_sequence(self):
        def duplicate_stop(files):
            lines = files["stops.txt"].splitlines()
            files["stops.txt"] += lines[1] + b"\n"
        self.assert_code("GTFS_DUPLICATE_ID", load_gtfs_feed, self.mutate_zip(duplicate_stop))
        def duplicate_sequence(files):
            lines = files["stop_times.txt"].splitlines()
            files["stop_times.txt"] += lines[1] + b"\n"
        self.assert_code("GTFS_DUPLICATE_ID", load_gtfs_feed, self.mutate_zip(duplicate_sequence))

    def test_bad_times_and_dates(self):
        for value in ("24:60:00", "-1:00:00", "not-time", "24:00:60"):
            with self.subTest(value=value):
                self.assert_code("GTFS_TIME_INVALID", parse_gtfs_time, value)
        path = self.mutate_zip(lambda files: self.change_first_row(files, "stop_times.txt", "departure_time", "24:60:00"))
        self.assert_code("GTFS_TIME_INVALID", load_gtfs_feed, path)
        path = self.mutate_zip(lambda files: self.change_first_row(files, "calendar_dates.txt", "date", "20250230"))
        self.assert_code("GTFS_DATE_INVALID", load_gtfs_feed, path)

    def test_bad_zip_and_zip_slip_rejected(self):
        bad = self.temp_path / "bad.zip"
        bad.write_bytes(b"not a ZIP")
        self.assert_code("GTFS_ARCHIVE_INVALID", load_gtfs_feed, bad)
        path = self.mutate_zip(lambda files: files.__setitem__("../escape.txt", b"x"))
        self.assert_code("GTFS_ARCHIVE_INVALID", load_gtfs_feed, path)

    def test_registry_outputs_verified_network_dataset_ref(self):
        registry = LocalDatasetRegistry(self.temp_path / "registry")
        ref = registry.register_file(
            ARCHIVE, kind="network", crs="EPSG:4326",
            source="Samtrafiken GTFS Sverige 2", source_version="2025-10-04",
        )
        self.assertEqual((ref.kind, ref.format, ref.row_count), ("network", "gtfs", 42))
        self.assertEqual(ref.storage_uri, f"registry://datasets/{ref.id}/data.gtfs")
        self.assertEqual(len(registry.read(ref.id).records), 42)
        self.assertEqual(registry.get(ref.id).content_hash, ref.content_hash)
        self.assertIsNotNone(ref.bbox)

    def test_registry_rejects_invalid_gtfs_without_published_dataset(self):
        registry = LocalDatasetRegistry(self.temp_path / "registry")
        invalid = self.mutate_zip(lambda files: files.pop("trips.txt"))
        with self.assertRaises(Exception) as raised:
            registry.register_file(
                invalid, kind="network", crs="EPSG:4326",
                source="fixture", source_version="v1",
            )
        self.assertEqual(raised.exception.code, "GTFS_REQUIRED_FILE_MISSING")
        self.assertEqual(registry.list(), [])

    def test_explicit_fetcher_offline_mock_and_no_partial_output(self):
        from src.gtfs.source import SOURCE_URL
        payload = b"PK\x03\x04not-a-valid-zip"
        with patch("src.gtfs.source.EXPECTED_SIZE_BYTES", len(payload)), patch(
            "src.gtfs.source.EXPECTED_SHA256", hashlib.sha256(payload).hexdigest()
        ):
            self.assert_code(
                "GTFS_ARCHIVE_INVALID", download_frozen_source,
                self.temp_path / "source", opener=lambda request, timeout: FakeResponse(payload, url=SOURCE_URL),
            )
        self.assertFalse((self.temp_path / "source").exists())
        with patch("src.gtfs.source.urlopen", side_effect=OSError("offline network blocked")):
            self.assert_code("GTFS_DOWNLOAD_FAILED", download_frozen_source, self.temp_path / "blocked")
        self.assertFalse((self.temp_path / "blocked").exists())
        self.assert_code(
            "GTFS_SOURCE_HASH_MISMATCH", download_frozen_source,
            self.temp_path / "wrong-hash", expected_sha256="0" * 64,
        )
        self.assertFalse((self.temp_path / "wrong-hash").exists())


if __name__ == "__main__":
    unittest.main()
