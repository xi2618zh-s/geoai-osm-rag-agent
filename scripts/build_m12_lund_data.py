"""M12 Lund scenario data: precheck gate and offline fixture builders (ADR-0024).

Subcommands
-----------
precheck   Read already-downloaded inputs (SCB WFS GeoJSON, fixed GTFS archive,
           local OSM PBF) and evaluate a data gate: ``--gate-version v1`` is the
           original plan v2 section 2.1 gate (kept to reproduce the published v1
           failure report); ``v2`` is the user-approved revision of plan v2.1
           section 2.2. Writes one JSON report (exclusive create); never writes
           fixtures and never touches the network.
build-scb  Publish the downloaded SCB grid bytes unchanged plus receipt and a
           manifest into a new fixture directory (CC0). Requires a passed v2 report.
build-osm  Extract school/supermarket/pharmacy representative points from the
           local PBF with the Python ``osmium`` package (no osmium CLI) into a new
           ODbL fixture directory, separate from CC0 data. Requires a passed v2 report.

Reports and manifests record file names and hashes only, never local paths.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import os
import shutil
import sys
import uuid
import zipfile
from datetime import date
from importlib import metadata
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import osmium
from pyproj import Transformer
from shapely.geometry import LineString, Point, Polygon, shape
from shapely.ops import linemerge, polygonize, unary_union
from shapely.validation import make_valid

STUDY_BBOX_3006 = (378000.0, 6166000.0, 398000.0, 6186000.0)
POI_MARGIN_M = 3000.0
POI_CATEGORIES = (
    ("school", "amenity", "school"),
    ("supermarket", "shop", "supermarket"),
    ("pharmacy", "amenity", "pharmacy"),
)
SERVICE_DATE = date(2025, 10, 7)
ORIGIN_STOP_NAME = "Lund Centralstation"
ORIGIN_WINDOW = ("07:30:00", "08:30:00")
GTFS_EXPECTED_SHA256 = "12a4a7169d8423641da7f15125092423f6f9834f3b70725719b1ce147eb48f69"
SCB_REQUIRED_PROPERTIES = None  # resolved lazily from src.population.contracts
SUPPRESSION_TOKENS = {"", "-", ":", "..", "..."}
GATE_V1 = {
    "scb_feature_count_min": 100, "scb_feature_count_max": 400,
    "scb_population_sum_min": 50000, "scb_ald5_9_nonnull_share_min": 0.5,
    "scb_bytes_max": 5_000_000,
    "osm_school_min": 10, "osm_supermarket_min": 10, "osm_pharmacy_min": 5,
    "m12a_distances_m": [800, 1000, 1500], "m12a_nontrivial_distances_min": 2,
    "gtfs_study_trips_min": 200, "gtfs_routes_min": 10,
    "gtfs_origin_departures_min": 10, "gtfs_subset_uncompressed_bytes_max": 20_000_000,
}
# Gate v1 is kept byte-for-byte so the published v1 failure report stays reproducible.
GATE = GATE_V1
# Gate v2 (plan v2.1 section 2.2, user-approved 2026-10-08): the WFS BBOX filter returns
# cells intersecting or touching the BBOX, so a 20x20 km BBOX on whole-km lines can touch
# 22x22 = 484 one-km cells; 400 was a geometric estimation error.
GATE_V2 = {**GATE_V1, "scb_feature_count_max": 484}
WFS_GEOMETRY_ELEMENT = "sp_geometry"
SCB_IDENTIFIER_FIELDS = ("rutid_inspire", "rutid_scb", "referenstid")
V2_AMENDMENTS = (
    "SCB feature-count maximum 400 -> 484: the WFS 1.1.0 BBOX filter returns 1 km cells intersecting or "
    "touching the BBOX (up to 22x22 for a 20x20 km BBOX on whole-km lines); 400 was a geometric estimation error.",
    "Property check is per feature: sp_geometry is the WFS geometry element and is checked as the top-level "
    "GeoJSON geometry; every other M05 property name must be a key of every feature, with values following "
    "the M05 rules (identifiers, rutstorl 1000, 2025 reference date, unique rutid_inspire, population values "
    "null/suppression token/non-negative integer).",
    "Edge-cell rule: all returned cells are kept unclipped; population sums describe the returned cells and are "
    "never the exact population of the 20x20 km area.",
)
HEADER_KEYS = (
    "generator", "timestamp", "osmosis_replication_timestamp",
    "osmosis_replication_sequence_number", "osmosis_replication_base_url",
)
OSM_LICENSE = "ODbL-1.0"
OSM_ATTRIBUTION = "© OpenStreetMap contributors"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")


def _write_exclusive(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as stream:
        stream.write(payload)


def expanded_bbox(margin: float = POI_MARGIN_M) -> tuple[float, float, float, float]:
    x0, y0, x1, y1 = STUDY_BBOX_3006
    return (x0 - margin, y0 - margin, x1 + margin, y1 + margin)


def wgs84_envelope(bbox_3006: tuple[float, float, float, float]) -> tuple[float, float, float, float]:
    to_wgs = Transformer.from_crs("EPSG:3006", "EPSG:4326", always_xy=True)
    return tuple(to_wgs.transform_bounds(*bbox_3006, densify_pts=21))


def _inside(x: float, y: float, bbox: tuple[float, float, float, float]) -> bool:
    return bbox[0] <= x <= bbox[2] and bbox[1] <= y <= bbox[3]


# --------------------------------------------------------------------------- SCB

def _scb_value(raw: Any) -> int | None:
    if raw is None or (isinstance(raw, str) and raw.strip() in SUPPRESSION_TOKENS):
        return None
    if isinstance(raw, bool) or not isinstance(raw, int) or raw < 0:
        raise ValueError(f"unexpected SCB value: {raw!r}")
    return raw


def scb_summary_v1_missing_properties(features: list[dict[str, Any]]) -> list[str]:
    """Gate v1 property check (known defect, kept for reproducibility): union of keys
    over all features compared with every DescribeFeatureType name, including the
    geometry element."""
    from src.population.contracts import SCB_PROPERTY_NAMES

    present = set().union(*(set(f.get("properties", {})) for f in features)) if features else set()
    return sorted(set(SCB_PROPERTY_NAMES) - present)


def scb_summary(path: Path) -> dict[str, Any]:
    """Gate v1 SCB summary (unchanged; produced m12_data_precheck_v1.json)."""
    payload = Path(path).read_bytes()
    document = json.loads(payload)
    features = document.get("features", [])
    missing = scb_summary_v1_missing_properties(features)
    totals = [_scb_value(f["properties"].get("beftotalt")) for f in features]
    band = [_scb_value(f["properties"].get("ald5_9")) for f in features]
    xs, ys = [], []
    for feature in features:
        minx, miny, maxx, maxy = shape(feature["geometry"]).bounds
        xs += [minx, maxx]
        ys += [miny, maxy]
    nonnull_band = sum(1 for item in band if item is not None)
    summary = {
        "file_name": Path(path).name, "sha256": hashlib.sha256(payload).hexdigest(),
        "bytes": len(payload), "feature_count": len(features),
        "missing_properties": missing,
        "population_total_nonnull_sum": sum(item for item in totals if item is not None),
        "population_total_null_count": sum(1 for item in totals if item is None),
        "ald5_9_nonnull_count": nonnull_band,
        "ald5_9_nonnull_share": (nonnull_band / len(features)) if features else 0.0,
        "data_bbox_3006": [min(xs), min(ys), max(xs), max(ys)] if features else None,
    }
    summary["checks"] = {
        "feature_count": GATE["scb_feature_count_min"] <= len(features) <= GATE["scb_feature_count_max"],
        "properties_complete": not missing,
        "population_sum": summary["population_total_nonnull_sum"] >= GATE["scb_population_sum_min"],
        "ald5_9_nonnull_share": summary["ald5_9_nonnull_share"] >= GATE["scb_ald5_9_nonnull_share_min"],
        "bytes": len(payload) <= GATE["scb_bytes_max"],
    }
    return summary


def _population_fields() -> tuple[str, ...]:
    from src.population.contracts import SCB_PROPERTY_NAMES

    return tuple(name for name in SCB_PROPERTY_NAMES if name in {"beftotalt", "man", "kvinna"} or name.startswith("ald"))


def _rule_population_value(raw: Any) -> str:
    """Classify per the M05 rule: 'value', 'null_or_token' or 'violation'."""
    if raw is None or (isinstance(raw, str) and raw.strip() in SUPPRESSION_TOKENS):
        return "null_or_token"
    if isinstance(raw, bool) or not isinstance(raw, int) or raw < 0:
        return "violation"
    return "value"


def scb_property_audit(features: list[dict[str, Any]]) -> dict[str, Any]:
    """Gate v2 per-feature check aligned with the M05 rules (M05 itself is not modified)."""
    from src.population.contracts import SCB_PROPERTY_NAMES

    required = [name for name in SCB_PROPERTY_NAMES if name != WFS_GEOMETRY_ELEMENT]
    population_fields = _population_fields()
    missing: dict[str, int] = {}
    violations: dict[str, int] = {}
    null_or_token = {name: 0 for name in population_fields}
    geometry_invalid = 0
    seen_ids: set[str] = set()

    def flag(rule: str) -> None:
        violations[rule] = violations.get(rule, 0) + 1

    for feature in features:
        properties = feature.get("properties")
        if not isinstance(properties, dict):
            flag("properties_object")
            properties = {}
        for key in required:
            if key not in properties:
                missing[key] = missing.get(key, 0) + 1
        if "objectid" in properties:
            value = properties["objectid"]
            if isinstance(value, bool) or not isinstance(value, int):
                flag("objectid")
        if "rutstorl" in properties:
            value = properties["rutstorl"]
            if isinstance(value, bool) or not isinstance(value, int) or value != 1000:
                flag("rutstorl")
        for key in SCB_IDENTIFIER_FIELDS:
            if key in properties and (not isinstance(properties[key], str) or not properties[key].strip()):
                flag("identifier_string")
        reference = properties.get("referenstid")
        if isinstance(reference, str) and reference.strip():
            text = reference.strip()
            try:
                parsed = date(int(text[:4]), int(text[4:6]), int(text[6:8])) if len(text) == 8 and text.isdigit() else None
            except ValueError:
                parsed = None
            if parsed is None or parsed.year != 2025:
                flag("referenstid")
        grid_id = properties.get("rutid_inspire")
        if isinstance(grid_id, str) and grid_id.strip():
            if grid_id in seen_ids:
                flag("rutid_inspire_duplicate")
            seen_ids.add(grid_id)
        for key in population_fields:
            if key not in properties:
                continue
            kind = _rule_population_value(properties[key])
            if kind == "violation":
                flag("population_value")
            elif kind == "null_or_token":
                null_or_token[key] += 1
        try:
            geometry = shape(feature.get("geometry")) if feature.get("geometry") is not None else None
            valid = (geometry is not None and geometry.geom_type in {"Polygon", "MultiPolygon"}
                     and not geometry.is_empty and geometry.is_valid)
        except Exception:
            valid = False
        if not valid:
            geometry_invalid += 1
    return {
        "feature_count": len(features), "required_property_keys": required,
        "geometry_element": WFS_GEOMETRY_ELEMENT, "geometry_rule": "top-level GeoJSON geometry is a valid non-empty Polygon/MultiPolygon",
        "missing_key_counts": dict(sorted(missing.items())), "rule_violation_counts": dict(sorted(violations.items())),
        "population_null_or_token_counts": null_or_token, "geometry_invalid_count": geometry_invalid,
        "passed": not missing and not violations and geometry_invalid == 0,
    }


def scb_summary_v2(path: Path, gate: dict[str, Any] = GATE_V2) -> dict[str, Any]:
    payload = Path(path).read_bytes()
    features = json.loads(payload).get("features", [])
    audit = scb_property_audit(features)

    def reported(feature, key):
        value = (feature.get("properties") or {}).get(key)
        return value if _rule_population_value(value) == "value" else None

    totals = [reported(f, "beftotalt") for f in features]
    band = [reported(f, "ald5_9") for f in features]
    study = Polygon.from_bounds(*STUDY_BBOX_3006)
    inside_cells = inside_pop = edge_cells = edge_pop = 0
    xs, ys = [], []
    for feature, total in zip(features, totals):
        try:
            geometry = shape(feature["geometry"])
        except Exception:
            continue
        minx, miny, maxx, maxy = geometry.bounds
        xs += [minx, maxx]
        ys += [miny, maxy]
        if geometry.difference(study).area <= 1e-6 * max(geometry.area, 1.0):
            inside_cells += 1
            inside_pop += total or 0
        else:
            edge_cells += 1
            edge_pop += total or 0
    nonnull_band = sum(1 for item in band if item is not None)
    population_sum = sum(item for item in totals if item is not None)
    summary = {
        "file_name": Path(path).name, "sha256": hashlib.sha256(payload).hexdigest(), "bytes": len(payload),
        "feature_count": len(features), "property_audit": audit,
        "population_total_reported_sum": population_sum,
        "population_total_not_reported_count": sum(1 for item in totals if item is None),
        "ald5_9_reported_count": nonnull_band,
        "ald5_9_reported_share": (nonnull_band / len(features)) if features else 0.0,
        "data_bbox_3006": [min(xs), min(ys), max(xs), max(ys)] if xs else None,
        "edge_cell_rule": {
            "rule": "All 1 km cells returned by the SCB WFS 1.1.0 BBOX filter are kept unclipped; this includes "
                    "cells intersecting or touching the study BBOX boundary.",
            "cells_within_study_bbox": inside_cells, "population_within_study_bbox_cells": inside_pop,
            "cells_touching_or_crossing_edge": edge_cells, "population_edge_cells": edge_pop,
            "population_statement": f"Reported population summed over the {len(features)} returned SCB cells "
                                    "(cells intersecting or touching the study BBOX, unclipped); this is not the exact "
                                    "population of the 20x20 km area.",
        },
    }
    summary["checks"] = {
        "feature_count": gate["scb_feature_count_min"] <= len(features) <= gate["scb_feature_count_max"],
        "properties_per_feature": audit["passed"],
        "population_sum": population_sum >= gate["scb_population_sum_min"],
        "ald5_9_reported_share": summary["ald5_9_reported_share"] >= gate["scb_ald5_9_nonnull_share_min"],
        "bytes": len(payload) <= gate["scb_bytes_max"],
    }
    return summary


# --------------------------------------------------------------------------- OSM

def pbf_identity(path: Path) -> dict[str, Any]:
    processor = osmium.FileProcessor(str(path), osmium.osm.NOTHING)
    header = processor.header
    box = header.box()
    fields = {key: header.get(key, "") for key in HEADER_KEYS}
    bbox = None
    if box.valid():
        bbox = [box.bottom_left.lon, box.bottom_left.lat, box.top_right.lon, box.top_right.lat]
    return {
        "file_name": Path(path).name, "bytes": Path(path).stat().st_size,
        "sha256": sha256_file(path), "header": fields, "header_bbox_wgs84": bbox,
        "provider_verified": False,
        "provenance_note": "Local file supplied by the user without a download receipt; "
                           "the provider and release date are unverified and are not inferred from the file name.",
    }


def _category(tags: dict[str, str]) -> str | None:
    for name, key, value in POI_CATEGORIES:
        if tags.get(key) == value:
            return name
    return None


def _tags(entity) -> dict[str, str]:
    return {tag.k: tag.v for tag in entity.tags}


def extract_pois(path: Path) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Return representative points for the three categories inside the expanded BBOX.

    Pass 1 keeps only tagged candidates (C++ TagFilter); pass 2 resolves the ways of
    multipolygon relations; pass 3 resolves node locations by ID. No whole-file node
    index is built, so memory stays small.
    """
    tag_filter = osmium.filter.TagFilter(*[(key, value) for _, key, value in POI_CATEGORIES])
    nodes: list[tuple[int, float, float, dict[str, str]]] = []
    ways: dict[int, tuple[list[int], dict[str, str]]] = {}
    relations: list[tuple[int, list[tuple[str, int, str]], dict[str, str]]] = []
    stats = {"tagged_nodes": 0, "tagged_ways": 0, "tagged_relations": 0,
             "skipped_unlocated": 0, "skipped_geometry": 0, "skipped_relation_type": 0}
    for entity in osmium.FileProcessor(str(path), osmium.osm.NODE | osmium.osm.WAY | osmium.osm.RELATION).with_filter(tag_filter):
        tags = _tags(entity)
        if _category(tags) is None:
            continue
        if entity.is_node():
            stats["tagged_nodes"] += 1
            if entity.location.valid():
                nodes.append((entity.id, entity.location.lon, entity.location.lat, tags))
            else:
                stats["skipped_unlocated"] += 1
        elif entity.is_way():
            stats["tagged_ways"] += 1
            ways[entity.id] = ([ref.ref for ref in entity.nodes], tags)
        elif entity.is_relation():
            stats["tagged_relations"] += 1
            if tags.get("type") not in {"multipolygon", "site"}:
                stats["skipped_relation_type"] += 1
                continue
            relations.append((entity.id, [(m.type, m.ref, m.role) for m in entity.members], tags))
    member_way_ids = {ref for _, members, _ in relations for kind, ref, _ in members if kind == "w"}
    member_ways: dict[int, list[int]] = {}
    if member_way_ids:
        for entity in osmium.FileProcessor(str(path), osmium.osm.WAY).with_filter(osmium.filter.IdFilter(member_way_ids)):
            member_ways[entity.id] = [ref.ref for ref in entity.nodes]
    node_ids = {ref for refs, _ in ways.values() for ref in refs}
    node_ids |= {ref for refs in member_ways.values() for ref in refs}
    locations: dict[int, tuple[float, float]] = {}
    if node_ids:
        for entity in osmium.FileProcessor(str(path), osmium.osm.NODE).with_filter(osmium.filter.IdFilter(node_ids)):
            if entity.location.valid():
                locations[entity.id] = (entity.location.lon, entity.location.lat)

    candidates: list[dict[str, Any]] = []

    def add(osm_type: str, osm_id: int, point: Point, tags: dict[str, str], method: str) -> None:
        candidates.append({"osm_type": osm_type, "osm_id": osm_id, "lon": point.x, "lat": point.y,
                           "tags": tags, "method": method})

    for osm_id, lon, lat, tags in nodes:
        add("node", osm_id, Point(lon, lat), tags, "node")
    for osm_id, (refs, tags) in ways.items():
        coords = [locations.get(ref) for ref in refs]
        if not coords or any(item is None for item in coords):
            stats["skipped_unlocated"] += 1
            continue
        try:
            if len(coords) >= 4 and coords[0] == coords[-1]:
                geom = make_valid(Polygon(coords))
            elif len(coords) >= 2:
                geom = LineString(coords)
            else:
                geom = Point(coords[0])
            point = geom.representative_point()
        except Exception:
            stats["skipped_geometry"] += 1
            continue
        if point.is_empty:
            stats["skipped_geometry"] += 1
            continue
        add("way", osm_id, point, tags, "way_representative_point")
    for osm_id, members, tags in relations:
        lines = []
        for kind, ref, _role in members:
            if kind != "w" or ref not in member_ways:
                continue
            coords = [locations.get(item) for item in member_ways[ref]]
            if len(coords) >= 2 and all(item is not None for item in coords):
                lines.append(LineString(coords))
        try:
            polygons = list(polygonize(linemerge(lines))) if lines else []
            geom = make_valid(unary_union(polygons)) if polygons else None
            point = geom.representative_point() if geom is not None and not geom.is_empty else None
        except Exception:
            point = None
        if point is None or point.is_empty:
            stats["skipped_geometry"] += 1
            continue
        add("relation", osm_id, point, tags, "relation_representative_point")

    to_3006 = Transformer.from_crs("EPSG:4326", "EPSG:3006", always_xy=True)
    outer = expanded_bbox()
    pois = []
    for item in candidates:
        x, y = to_3006.transform(item["lon"], item["lat"])
        if not _inside(x, y, outer):
            continue
        item["x_3006"], item["y_3006"] = x, y
        item["category"] = _category(item["tags"])
        item["in_study_bbox"] = _inside(x, y, STUDY_BBOX_3006)
        pois.append(item)
    order = {"node": 0, "way": 1, "relation": 2}
    pois.sort(key=lambda p: (p["category"], order[p["osm_type"]], p["osm_id"]))
    stats["candidates_total_sweden_file"] = len(candidates)
    return pois, stats


def osm_summary(path: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    identity = pbf_identity(path)
    pois, stats = extract_pois(path)
    counts = {}
    for name, _, _ in POI_CATEGORIES:
        counts[name] = {
            "study_bbox": sum(1 for p in pois if p["category"] == name and p["in_study_bbox"]),
            "expanded_bbox": sum(1 for p in pois if p["category"] == name),
            "by_method": {m: sum(1 for p in pois if p["category"] == name and p["method"] == m)
                          for m in ("node", "way_representative_point", "relation_representative_point")},
        }
    summary = {**identity, "extraction_stats": stats, "counts": counts,
               "study_bbox_3006": list(STUDY_BBOX_3006), "expanded_bbox_3006": list(expanded_bbox())}
    summary["checks"] = {
        "school": counts["school"]["study_bbox"] >= GATE["osm_school_min"],
        "supermarket": counts["supermarket"]["study_bbox"] >= GATE["osm_supermarket_min"],
        "pharmacy": counts["pharmacy"]["study_bbox"] >= GATE["osm_pharmacy_min"],
    }
    return summary, pois


def m12a_nontriviality(scb_path: Path, pois: list[dict[str, Any]]) -> dict[str, Any]:
    features = json.loads(Path(scb_path).read_bytes())["features"]
    schools = [Point(p["x_3006"], p["y_3006"]) for p in pois if p["category"] == "school"]
    cells = [shape(f["geometry"]) for f in features]
    nearest = [min(cell.distance(school) for school in schools) for cell in cells] if schools else []
    rows = []
    for distance in GATE["m12a_distances_m"]:
        gaps = sum(1 for value in nearest if value > distance)
        rows.append({"distance_m": distance, "gap_cell_count": gaps, "cell_count": len(cells),
                     "nontrivial": 1 <= gaps <= len(cells) - 1})
    passed = sum(1 for row in rows if row["nontrivial"]) >= GATE["m12a_nontrivial_distances_min"]
    return {"rows": rows, "school_count_expanded_bbox": len(schools), "checks": {"nontrivial": passed}}


# --------------------------------------------------------------------------- GTFS

def _seconds(text: str) -> int:
    hours, minutes, seconds = (int(part) for part in text.strip().split(":"))
    return hours * 3600 + minutes * 60 + seconds


def _csv_rows(archive: zipfile.ZipFile, name: str) -> Iterable[dict[str, str]]:
    with archive.open(name) as raw:
        yield from csv.DictReader(io.TextIOWrapper(raw, encoding="utf-8-sig", newline=""))


def active_service_ids(archive: zipfile.ZipFile, day: date) -> set[str]:
    token = day.strftime("%Y%m%d")
    weekday = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")[day.weekday()]
    active: set[str] = set()
    names = set(archive.namelist())
    if "calendar.txt" in names:
        for row in _csv_rows(archive, "calendar.txt"):
            if row["start_date"] <= token <= row["end_date"] and row[weekday] == "1":
                active.add(row["service_id"])
    if "calendar_dates.txt" in names:
        for row in _csv_rows(archive, "calendar_dates.txt"):
            if row["date"] != token:
                continue
            if row["exception_type"] == "1":
                active.add(row["service_id"])
            elif row["exception_type"] == "2":
                active.discard(row["service_id"])
    return active


def gtfs_summary(zip_path: Path) -> dict[str, Any]:
    sha = sha256_file(zip_path)
    to_3006 = Transformer.from_crs("EPSG:4326", "EPSG:3006", always_xy=True)
    with zipfile.ZipFile(zip_path) as archive:
        stops = {}
        origin_ids: set[str] = set()
        for row in _csv_rows(archive, "stops.txt"):
            x, y = to_3006.transform(float(row["stop_lon"]), float(row["stop_lat"]))
            stops[row["stop_id"]] = _inside(x, y, STUDY_BBOX_3006)
            if row["stop_name"] == ORIGIN_STOP_NAME:
                origin_ids.add(row["stop_id"])
        for row in _csv_rows(archive, "stops.txt"):
            if row.get("parent_station") in origin_ids:
                origin_ids.add(row["stop_id"])
        services = active_service_ids(archive, SERVICE_DATE)
        trip_route = {}
        for row in _csv_rows(archive, "trips.txt"):
            if row["service_id"] in services:
                trip_route[row["trip_id"]] = row["route_id"]
        touching: set[str] = set()
        trip_bytes: dict[str, int] = {}
        trip_stops: dict[str, set[str]] = {}
        start, end = (_seconds(item) for item in ORIGIN_WINDOW)
        origin_departures = 0
        stop_time_rows = 0
        for row in _csv_rows(archive, "stop_times.txt"):
            stop_time_rows += 1
            trip = row["trip_id"]
            if trip not in trip_route:
                continue
            trip_bytes[trip] = trip_bytes.get(trip, 0) + sum(len(v) for v in row.values()) + len(row)
            trip_stops.setdefault(trip, set()).add(row["stop_id"])
            if stops.get(row["stop_id"]):
                touching.add(trip)
            if row["stop_id"] in origin_ids and row.get("pickup_type", "") in {"", "0"} and row.get("departure_time"):
                if start <= _seconds(row["departure_time"]) < end:
                    origin_departures += 1
        routes = {trip_route[trip] for trip in touching}
        subset_stop_ids = set().union(*(trip_stops[t] for t in touching)) if touching else set()
        estimate = sum(trip_bytes[t] for t in touching) + 120 * len(subset_stop_ids) + 120 * len(touching)
    summary = {
        "file_name": Path(zip_path).name, "sha256": sha, "sha256_matches_frozen": sha == GTFS_EXPECTED_SHA256,
        "service_date": SERVICE_DATE.isoformat(), "active_service_count": len(services),
        "active_trip_count": len(trip_route), "stop_time_rows_total": stop_time_rows,
        "study_bbox_stop_count": sum(1 for inside in stops.values() if inside),
        "study_trip_count": len(touching), "study_route_count": len(routes),
        "origin_stop_name": ORIGIN_STOP_NAME, "origin_stop_ids": sorted(origin_ids),
        "origin_window": list(ORIGIN_WINDOW), "origin_regular_departures": origin_departures,
        "subset_stop_count": len(subset_stop_ids),
        "subset_uncompressed_bytes_estimate": estimate,
    }
    summary["checks"] = {
        "sha256": summary["sha256_matches_frozen"],
        "study_trips": len(touching) >= GATE["gtfs_study_trips_min"],
        "routes": len(routes) >= GATE["gtfs_routes_min"],
        "origin_departures": origin_departures >= GATE["gtfs_origin_departures_min"],
        "subset_size": estimate <= GATE["gtfs_subset_uncompressed_bytes_max"],
    }
    return summary


# --------------------------------------------------------------------------- commands

SECTION_NAMES = ("scb", "osm", "m12a_nontriviality", "gtfs")


def _tool_versions() -> dict[str, str]:
    return {"osmium": metadata.version("osmium"), "shapely": metadata.version("shapely"),
            "pyproj": metadata.version("pyproj")}


def report_v1(args) -> dict[str, Any]:
    """Gate v1 report, unchanged (reproduces m12_data_precheck_v1.json)."""
    report: dict[str, Any] = {
        "schema": "m12-data-precheck-v1", "gate_source": "M12 plan v2 section 2.1 / ADR-0024",
        "gate": GATE, "study_bbox_3006": list(STUDY_BBOX_3006),
        "tool_versions": _tool_versions(),
    }
    report["scb"] = scb_summary(args.scb)
    osm, pois = osm_summary(args.pbf)
    report["osm"] = osm
    report["m12a_nontriviality"] = m12a_nontriviality(args.scb, pois)
    report["gtfs"] = gtfs_summary(args.gtfs_zip)
    report["passed"] = all(
        all(section["checks"].values())
        for section in (report["scb"], report["osm"], report["m12a_nontriviality"], report["gtfs"])
    )
    return report


def build_report_v2(sections: dict[str, dict[str, Any]], *, v1_reference: dict[str, Any]) -> dict[str, Any]:
    report: dict[str, Any] = {
        "schema": "m12-data-precheck-v2", "gate_version": "v2",
        "gate_source": "M12 plan v2.1 section 2.2 / ADR-0024 revision 2026-10-08 (user-approved)",
        "gate": GATE_V2, "supersedes": dict(v1_reference), "amendments": list(V2_AMENDMENTS),
        "study_bbox_3006": list(STUDY_BBOX_3006), "poi_margin_m": POI_MARGIN_M,
        "tool_versions": _tool_versions(),
    }
    for name in SECTION_NAMES:
        report[name] = sections[name]
    report["passed"] = all(all(sections[name]["checks"].values()) for name in SECTION_NAMES)
    return report


def cmd_precheck(args) -> int:
    if args.gate_version == "v1":
        report = report_v1(args)
    else:
        if args.v1_report is None:
            raise SystemExit("--v1-report is required for gate v2")
        v1_bytes = Path(args.v1_report).read_bytes()
        v1_reference = {"file_name": Path(args.v1_report).name, "sha256": hashlib.sha256(v1_bytes).hexdigest(),
                        "passed": json.loads(v1_bytes)["passed"]}
        osm, pois = osm_summary(args.pbf)
        sections = {"scb": scb_summary_v2(args.scb), "osm": osm,
                    "m12a_nontriviality": m12a_nontriviality(args.scb, pois), "gtfs": gtfs_summary(args.gtfs_zip)}
        report = build_report_v2(sections, v1_reference=v1_reference)
    _write_exclusive(args.output, _canonical(report))
    print(json.dumps({"passed": report["passed"], **{k: report[k]["checks"] for k in SECTION_NAMES}},
                     ensure_ascii=False, sort_keys=True))
    return 0 if report["passed"] else 2


def require_passed_precheck(report_path: Path, *, section: str, sha256: str) -> dict[str, Any]:
    """Builders run only after a passed gate-v2 report whose input hash matches."""
    report = json.loads(Path(report_path).read_bytes())
    if report.get("schema") != "m12-data-precheck-v2" or report.get("passed") is not True:
        raise SystemExit("A passed m12-data-precheck-v2 report is required before building fixtures")
    if report[section]["sha256"] != sha256:
        raise SystemExit(f"Input hash differs from the precheck report ({section})")
    return report


def _publish_dir(output_dir: Path, files: dict[str, bytes]) -> None:
    output_dir = Path(output_dir)
    if output_dir.exists():
        raise SystemExit(f"Refusing to overwrite existing fixture directory: {output_dir.name}")
    staging = output_dir.parent / f".staging-{output_dir.name}-{uuid.uuid4().hex[:10]}"
    staging.mkdir(parents=True)
    try:
        for name, payload in files.items():
            (staging / name).write_bytes(payload)
        os.replace(staging, output_dir)
    finally:
        if staging.exists():
            shutil.rmtree(staging)


def cmd_build_scb(args) -> int:
    payload = Path(args.source).read_bytes()
    receipt_bytes = Path(args.receipt).read_bytes()
    receipt = json.loads(receipt_bytes)
    summary = scb_summary_v2(args.source)
    if not all(summary["checks"].values()):
        raise SystemExit("SCB source does not satisfy precheck gate v2")
    precheck = require_passed_precheck(args.precheck_report, section="scb", sha256=summary["sha256"])
    precheck_sha = sha256_file(args.precheck_report)
    data_name = "scb_population_grid_2025_lund_20km_raw.geojson"
    manifest = {
        "schema": "m12-scb-fixture-manifest-v1", "fixture_id": "scb-population-grid-2025-lund-20km-v1",
        "provider": "Statistics Sweden (SCB)", "dataset": "stat:befolkning_1km_2025",
        "license": "CC0-1.0", "attribution": "Statistics Sweden (SCB), 1 km population grid 2025",
        "crs": "EPSG:3006", "bbox_3006": list(STUDY_BBOX_3006),
        "files": {
            data_name: {"sha256": hashlib.sha256(payload).hexdigest(), "bytes": len(payload),
                        "feature_count": summary["feature_count"], "content": "unaltered WFS response bytes"},
            "download_receipt.json": {"sha256": hashlib.sha256(receipt_bytes).hexdigest(), "bytes": len(receipt_bytes)},
        },
        "receipt_response_sha256": receipt.get("response_sha256") or receipt.get("content_sha256"),
        "precheck": {"report": Path(args.precheck_report).name, "report_sha256": precheck_sha,
                     "gate_version": precheck["gate_version"],
                     **{key: summary[key] for key in ("population_total_reported_sum", "population_total_not_reported_count",
                                                      "ald5_9_reported_count", "data_bbox_3006")}},
        "edge_cell_rule": summary["edge_cell_rule"],
        "build_command": "python scripts/build_m12_lund_data.py build-scb --source <downloaded geojson> --receipt <receipt> --precheck-report benchmarks/results/m12/precheck/m12_data_precheck_v2.json --output-dir data/fixtures/population/scb/m12_lund_v1",
        "purpose": "M12 scenario population demand and children age bands (CC0; kept separate from ODbL data).",
    }
    _publish_dir(args.output_dir, {data_name: payload, "download_receipt.json": receipt_bytes,
                                   "manifest.json": _canonical(manifest)})
    print(json.dumps({"published": Path(args.output_dir).name, "features": summary["feature_count"]}))
    return 0


def poi_geojson(pois: list[dict[str, Any]]) -> bytes:
    features = []
    for p in pois:
        poi_id = f"{p['osm_type']}/{p['osm_id']}"
        tags = p["tags"]
        features.append({
            "type": "Feature", "id": poi_id,
            "properties": {
                "poi_id": poi_id, "osm_type": p["osm_type"], "osm_id": p["osm_id"],
                "category": p["category"], "amenity": tags.get("amenity"), "shop": tags.get("shop"),
                "name": tags.get("name"), "geometry_method": p["method"],
            },
            "geometry": {"type": "Point", "coordinates": [round(p["lon"], 7), round(p["lat"], 7)]},
        })
    document = {"type": "FeatureCollection", "features": features}
    return (json.dumps(document, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def cmd_build_osm(args) -> int:
    summary, pois = osm_summary(args.pbf)
    if not all(summary["checks"].values()):
        raise SystemExit("OSM extraction does not satisfy the frozen precheck gate")
    require_passed_precheck(args.precheck_report, section="osm", sha256=summary["sha256"])
    precheck_sha = sha256_file(args.precheck_report)
    data_name = "lund_poi_points_v1.geojson"
    payload = poi_geojson(pois)
    readme = (
        "# M12 Lund OSM POI fixture (ODbL 1.0)\n\n"
        "Representative points for amenity=school, shop=supermarket and amenity=pharmacy within the M12 study\n"
        "BBOX (EPSG:3006 378000,6166000,398000,6186000) expanded by 3 km, extracted offline with the Python\n"
        "osmium package from a local OpenStreetMap PBF supplied by the user. The provider and release date of\n"
        "that PBF are unverified; its SHA-256 and header fields are recorded in manifest.json.\n\n"
        "License: Open Database License 1.0 (ODbL-1.0). Attribution: © OpenStreetMap contributors.\n"
        "This derived database is shared under the ODbL and is kept separate from CC0 data (SCB, GTFS).\n"
        "Nodes and areas for the same facility are not merged.\n"
    ).encode("utf-8")
    manifest = {
        "schema": "m12-osm-poi-fixture-manifest-v1", "fixture_id": "osm-poi-lund-m12-v1",
        "license": OSM_LICENSE, "attribution": OSM_ATTRIBUTION,
        "source_pbf": {key: summary[key] for key in ("file_name", "bytes", "sha256", "header", "header_bbox_wgs84",
                                                    "provider_verified", "provenance_note")},
        "crs": "EPSG:4326", "study_bbox_3006": list(STUDY_BBOX_3006), "expanded_bbox_3006": list(expanded_bbox()),
        "categories": [{"category": name, "tag": f"{key}={value}"} for name, key, value in POI_CATEGORIES],
        "counts": summary["counts"], "extraction_stats": summary["extraction_stats"],
        "geometry_rule": "node location; way/relation shapely representative_point; coordinates rounded to 1e-7 deg",
        "tool_versions": {"osmium": metadata.version("osmium"), "shapely": metadata.version("shapely"),
                          "pyproj": metadata.version("pyproj")},
        "files": {data_name: {"sha256": hashlib.sha256(payload).hexdigest(), "bytes": len(payload),
                              "feature_count": len(pois)},
                  "README.md": {"sha256": hashlib.sha256(readme).hexdigest(), "bytes": len(readme)}},
        "precheck": {"report": Path(args.precheck_report).name, "report_sha256": precheck_sha},
        "build_command": "python scripts/build_m12_lund_data.py build-osm --pbf data/osm/<local pbf> --precheck-report benchmarks/results/m12/precheck/m12_data_precheck_v2.json --output-dir data/fixtures/osm/m12_lund_poi_v1",
    }
    _publish_dir(args.output_dir, {data_name: payload, "README.md": readme, "manifest.json": _canonical(manifest)})
    print(json.dumps({"published": Path(args.output_dir).name, "features": len(pois)}))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    pre = sub.add_parser("precheck")
    pre.add_argument("--scb", type=Path, required=True)
    pre.add_argument("--gtfs-zip", type=Path, required=True)
    pre.add_argument("--pbf", type=Path, required=True)
    pre.add_argument("--output", type=Path, required=True)
    pre.add_argument("--gate-version", choices=("v1", "v2"), required=True)
    pre.add_argument("--v1-report", type=Path, help="published v1 report referenced by a v2 report")
    scb = sub.add_parser("build-scb")
    scb.add_argument("--source", type=Path, required=True)
    scb.add_argument("--receipt", type=Path, required=True)
    scb.add_argument("--precheck-report", type=Path, required=True)
    scb.add_argument("--output-dir", type=Path, required=True)
    osm_parser = sub.add_parser("build-osm")
    osm_parser.add_argument("--pbf", type=Path, required=True)
    osm_parser.add_argument("--precheck-report", type=Path, required=True)
    osm_parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    return {"precheck": cmd_precheck, "build-scb": cmd_build_scb, "build-osm": cmd_build_osm}[args.command](args)


if __name__ == "__main__":
    raise SystemExit(main())
