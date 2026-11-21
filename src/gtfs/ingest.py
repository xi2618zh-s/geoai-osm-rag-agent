"""Strict, bounded GTFS Schedule ingestion with explicit service-day semantics."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
import io
import math
from pathlib import Path
import re
from typing import Mapping
import zipfile
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


REQUIRED_FILES = frozenset({"agency.txt", "stops.txt", "routes.txt", "trips.txt", "stop_times.txt"})
WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
MAX_ARCHIVE_BYTES = 20_000_000
MAX_UNCOMPRESSED_BYTES = 50_000_000
MAX_MEMBER_BYTES = 30_000_000
GTFS_TIME_RE = re.compile(r"^([0-9]{1,2}):([0-5][0-9]):([0-5][0-9])$")


class GtfsValidationError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def fail(code: str, message: str):
    raise GtfsValidationError(code, message)


@dataclass(frozen=True)
class GtfsFeed:
    agency_timezone: str
    feed_version: str | None
    agencies: tuple[dict[str, str], ...]
    stops: tuple[dict[str, str], ...]
    routes: tuple[dict[str, str], ...]
    trips: tuple[dict[str, str], ...]
    stop_times: tuple[dict[str, str], ...]
    calendar: tuple[dict[str, str], ...]
    calendar_dates: tuple[dict[str, str], ...]
    bbox: tuple[float, float, float, float]


def parse_gtfs_time(value: str) -> int:
    if not isinstance(value, str):
        fail("GTFS_TIME_INVALID", "GTFS time must be HH:MM:SS")
    match = GTFS_TIME_RE.fullmatch(value)
    if match is None:
        fail("GTFS_TIME_INVALID", f"invalid GTFS time: {value!r}")
    hour, minute, second = map(int, match.groups())
    return hour * 3600 + minute * 60 + second


def gtfs_local_datetime(service_date: date, gtfs_time: str, timezone_name: str) -> datetime:
    if not isinstance(service_date, date) or isinstance(service_date, datetime):
        fail("GTFS_DATE_INVALID", "service_date must be a date")
    try:
        zone = ZoneInfo(timezone_name)
    except (ZoneInfoNotFoundError, TypeError, ValueError) as error:
        raise GtfsValidationError("GTFS_TIMEZONE_INVALID", str(error)) from error
    seconds = parse_gtfs_time(gtfs_time)
    return datetime.combine(service_date, time.min, tzinfo=zone) + timedelta(seconds=seconds)


def _date(value: str) -> date:
    if not isinstance(value, str) or re.fullmatch(r"[0-9]{8}", value) is None:
        fail("GTFS_DATE_INVALID", f"invalid GTFS date: {value!r}")
    try:
        return datetime.strptime(value, "%Y%m%d").date()
    except ValueError as error:
        raise GtfsValidationError("GTFS_DATE_INVALID", str(error)) from error


def _required(row: Mapping[str, str], field: str, table: str) -> str:
    value = row.get(field)
    if value is None or not value.strip():
        fail("GTFS_FIELD_MISSING", f"{table}.{field} is required")
    return value.strip()


def _unique(rows: tuple[dict[str, str], ...], field: str, table: str) -> set[str]:
    values = [_required(row, field, table) for row in rows]
    if len(values) != len(set(values)):
        fail("GTFS_DUPLICATE_ID", f"duplicate {table}.{field}")
    return set(values)


def _read_table(archive: zipfile.ZipFile, name: str, required_columns: set[str]) -> tuple[dict[str, str], ...]:
    try:
        with archive.open(name) as source:
            with io.TextIOWrapper(source, encoding="utf-8-sig", newline="") as text:
                reader = csv.DictReader(text)
                headers = reader.fieldnames
                if not headers or any(not field or not field.strip() for field in headers):
                    fail("GTFS_SCHEMA_INVALID", f"{name} has an invalid header")
                if len(headers) != len(set(headers)) or not required_columns.issubset(headers):
                    fail("GTFS_SCHEMA_INVALID", f"{name} missing/duplicate columns")
                rows = []
                for row in reader:
                    if None in row or any(value is None for value in row.values()):
                        fail("GTFS_SCHEMA_INVALID", f"{name} has malformed CSV width")
                    rows.append(row)
                return tuple(rows)
    except (UnicodeError, csv.Error, RuntimeError, zipfile.BadZipFile) as error:
        raise GtfsValidationError("GTFS_SCHEMA_INVALID", f"{name}: {error}") from error


def load_gtfs_feed(path: Path) -> GtfsFeed:
    archive_path = Path(path)
    if not archive_path.is_file():
        fail("GTFS_ARCHIVE_INVALID", "GTFS ZIP does not exist")
    if archive_path.stat().st_size > MAX_ARCHIVE_BYTES:
        fail("GTFS_LIMIT_EXCEEDED", "GTFS ingestion requires a bounded regional/subset ZIP")
    try:
        archive = zipfile.ZipFile(archive_path)
    except (OSError, zipfile.BadZipFile) as error:
        raise GtfsValidationError("GTFS_ARCHIVE_INVALID", str(error)) from error
    with archive:
        infos = archive.infolist()
        names = [info.filename for info in infos]
        if not names or len(names) != len(set(names)) or len(names) > 50:
            fail("GTFS_ARCHIVE_INVALID", "empty, duplicate, or oversized archive inventory")
        if any(
            name.startswith("/") or "\\" in name or "/" in name or name in {".", ".."}
            or not name.endswith(".txt") or info.flag_bits & 0x1
            or info.compress_type not in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}
            for name, info in zip(names, infos)
        ):
            fail("GTFS_ARCHIVE_INVALID", "unsafe or unsupported archive entry")
        if (any(info.file_size > MAX_MEMBER_BYTES for info in infos)
                or sum(info.file_size for info in infos) > MAX_UNCOMPRESSED_BYTES):
            fail("GTFS_LIMIT_EXCEEDED", "GTFS uncompressed content exceeds M06a limit")
        if not REQUIRED_FILES.issubset(names) or not ({"calendar.txt", "calendar_dates.txt"} & set(names)):
            fail("GTFS_REQUIRED_FILE_MISSING", "required GTFS Schedule file is missing")

        columns = {
            "agency.txt": {"agency_name", "agency_url", "agency_timezone"},
            "stops.txt": {"stop_id", "stop_name", "stop_lat", "stop_lon"},
            "routes.txt": {"route_id", "route_type"},
            "trips.txt": {"route_id", "service_id", "trip_id"},
            "stop_times.txt": {"trip_id", "arrival_time", "departure_time", "stop_id", "stop_sequence"},
            "calendar.txt": {"service_id", *WEEKDAYS, "start_date", "end_date"},
            "calendar_dates.txt": {"service_id", "date", "exception_type"},
            "feed_info.txt": {"feed_publisher_name", "feed_publisher_url", "feed_lang"},
        }
        tables = {
            name: _read_table(archive, name, columns[name]) if name in names else ()
            for name in columns
        }

    agencies = tables["agency.txt"]
    stops = tables["stops.txt"]
    routes = tables["routes.txt"]
    trips = tables["trips.txt"]
    stop_times = tables["stop_times.txt"]
    calendar = tables["calendar.txt"]
    calendar_dates = tables["calendar_dates.txt"]
    if not agencies or not stops or not routes or not trips or not stop_times:
        fail("GTFS_SCHEMA_INVALID", "core GTFS tables must be non-empty")
    agency_ids = _unique(agencies, "agency_id", "agency.txt") if len(agencies) > 1 else {
        agencies[0].get("agency_id", "")
    }
    zones = {_required(row, "agency_timezone", "agency.txt") for row in agencies}
    if len(zones) != 1:
        fail("GTFS_TIMEZONE_INVALID", "all agencies must share one timezone")
    agency_timezone = next(iter(zones))
    try:
        ZoneInfo(agency_timezone)
    except ZoneInfoNotFoundError as error:
        raise GtfsValidationError("GTFS_TIMEZONE_INVALID", agency_timezone) from error

    stop_ids = _unique(stops, "stop_id", "stops.txt")
    route_ids = _unique(routes, "route_id", "routes.txt")
    trip_ids = _unique(trips, "trip_id", "trips.txt")
    coordinates = []
    for row in stops:
        _required(row, "stop_name", "stops.txt")
        try:
            lat, lon = float(row["stop_lat"]), float(row["stop_lon"])
        except ValueError as error:
            raise GtfsValidationError("GTFS_COORDINATE_INVALID", str(error)) from error
        if not (math.isfinite(lat) and math.isfinite(lon) and -90 <= lat <= 90 and -180 <= lon <= 180):
            fail("GTFS_COORDINATE_INVALID", "invalid stop coordinates")
        coordinates.append((lon, lat))
        parent = row.get("parent_station", "").strip()
        if parent and parent not in stop_ids:
            fail("GTFS_FOREIGN_KEY_MISSING", "stops.parent_station missing")
    for row in routes:
        agency_id = row.get("agency_id", "").strip()
        if len(agencies) > 1 and not agency_id:
            fail("GTFS_FIELD_MISSING", "routes.agency_id required for multi-agency feed")
        if agency_id and agency_id not in agency_ids:
            fail("GTFS_FOREIGN_KEY_MISSING", "routes.agency_id missing")
        if not row.get("route_short_name", "").strip() and not row.get("route_long_name", "").strip():
            fail("GTFS_FIELD_MISSING", "route requires short or long name")
        _required(row, "route_type", "routes.txt")
    calendar_ids = _unique(calendar, "service_id", "calendar.txt")
    exception_keys = set()
    date_service_ids = set()
    for row in calendar:
        first, last = _date(row["start_date"]), _date(row["end_date"])
        if first > last or any(row[day] not in {"0", "1"} for day in WEEKDAYS):
            fail("GTFS_DATE_INVALID", "invalid calendar range or weekday flag")
    for row in calendar_dates:
        service_id = _required(row, "service_id", "calendar_dates.txt")
        service_date = _date(row["date"])
        if row["exception_type"] not in {"1", "2"}:
            fail("GTFS_DATE_INVALID", "invalid calendar exception type")
        key = (service_id, service_date)
        if key in exception_keys:
            fail("GTFS_DUPLICATE_ID", "duplicate calendar_dates service/date")
        exception_keys.add(key)
        date_service_ids.add(service_id)
    service_ids = calendar_ids | date_service_ids
    for row in trips:
        if row["route_id"] not in route_ids or row["service_id"] not in service_ids:
            fail("GTFS_FOREIGN_KEY_MISSING", "trips route/service reference missing")

    sequences = set()
    by_trip: dict[str, list[tuple[int, int | None, int | None]]] = {item: [] for item in trip_ids}
    for row in stop_times:
        trip_id = _required(row, "trip_id", "stop_times.txt")
        if trip_id not in trip_ids or row["stop_id"] not in stop_ids:
            fail("GTFS_FOREIGN_KEY_MISSING", "stop_times trip/stop reference missing")
        sequence_text = _required(row, "stop_sequence", "stop_times.txt")
        if not sequence_text.isdecimal() or int(sequence_text) < 1:
            fail("GTFS_SCHEMA_INVALID", "stop_sequence must be a positive integer")
        sequence = int(sequence_text)
        key = (trip_id, sequence)
        if key in sequences:
            fail("GTFS_DUPLICATE_ID", "duplicate trip_id/stop_sequence")
        sequences.add(key)
        arrival = parse_gtfs_time(row["arrival_time"]) if row["arrival_time"] else None
        departure = parse_gtfs_time(row["departure_time"]) if row["departure_time"] else None
        if (arrival is None) != (departure is None):
            fail("GTFS_TIME_INVALID", "arrival and departure must be present together")
        if arrival is not None and arrival > departure:
            fail("GTFS_TIME_INVALID", "arrival after departure at a stop")
        by_trip[trip_id].append((sequence, arrival, departure))
    for trip_id, events in by_trip.items():
        if len(events) < 2:
            fail("GTFS_SCHEMA_INVALID", f"trip {trip_id} must have at least two stop times")
        ordered = sorted(events)
        if ordered[0][1] is None or ordered[-1][2] is None:
            fail("GTFS_TIME_INVALID", "first/last stop must have times")
        last_departure = -1
        for _sequence, arrival, departure in ordered:
            if arrival is not None:
                if arrival < last_departure:
                    fail("GTFS_TIME_INVALID", "trip times decrease across stop sequence")
                last_departure = departure if departure is not None else last_departure

    info = tables["feed_info.txt"]
    feed_version = info[0].get("feed_version") if info else None
    return GtfsFeed(
        agency_timezone=agency_timezone,
        feed_version=feed_version,
        agencies=agencies,
        stops=stops,
        routes=routes,
        trips=trips,
        stop_times=stop_times,
        calendar=calendar,
        calendar_dates=calendar_dates,
        bbox=(
            min(point[0] for point in coordinates), min(point[1] for point in coordinates),
            max(point[0] for point in coordinates), max(point[1] for point in coordinates),
        ),
    )


def active_service_ids(feed: GtfsFeed, service_date: date) -> tuple[str, ...]:
    if not isinstance(service_date, date) or isinstance(service_date, datetime):
        fail("GTFS_DATE_INVALID", "service_date must be a date")
    weekday = WEEKDAYS[service_date.weekday()]
    active = {
        row["service_id"]
        for row in feed.calendar
        if _date(row["start_date"]) <= service_date <= _date(row["end_date"])
        and row[weekday] == "1"
    }
    for row in feed.calendar_dates:
        if _date(row["date"]) == service_date:
            if row["exception_type"] == "1":
                active.add(row["service_id"])
            else:
                active.discard(row["service_id"])
    return tuple(sorted(active))


def active_trip_ids(feed: GtfsFeed, service_date: date) -> tuple[str, ...]:
    services = set(active_service_ids(feed, service_date))
    return tuple(sorted(row["trip_id"] for row in feed.trips if row["service_id"] in services))
