"""Deterministic GTFS stop-service frequency and conservative spatial coverage."""

from __future__ import annotations

from datetime import date, datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import tempfile
from typing import Any

from pyproj import CRS, Transformer
from pyproj.exceptions import CRSError, ProjError
from shapely import union_all
from shapely.geometry import Point, mapping, shape

from src.datasets import DatasetLineage, DatasetRef, DatasetRegistry
from src.gtfs.ingest import GtfsValidationError, active_trip_ids, load_gtfs_feed, parse_gtfs_time

from .accessibility_contracts import (
    AnalyzeGtfsStopCoverageInput,
    AnalyzeGtfsStopCoverageOutput,
    GtfsRouteFrequency,
    GtfsStopFrequency,
    GtfsWindowComparison,
    GtfsWindowSummary,
)
from .contracts import AnalysisToolMetadata
from .registry import AnalysisToolError


OUTPUT_CRS = "EPSG:3006"
MAX_BUFFER_FEATURES = 5000


def _error(code: str, message: str):
    raise AnalysisToolError(code, message)


def _parameters_hash(parameters: dict[str, Any]) -> str:
    payload = json.dumps(
        parameters, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(payload).hexdigest()}"


def _crs(value: str) -> CRS:
    try:
        return CRS.from_user_input(value)
    except (CRSError, ProjError, ValueError) as error:
        raise AnalysisToolError("ANALYSIS_TOOL_CRS_INVALID", f"Invalid CRS: {value}") from error


def _load_projected_features(
    datasets: DatasetRegistry, ref: DatasetRef, *, role: str
) -> tuple[dict[str, Any], ...]:
    if ref.kind != "vector" or ref.format != "geojson":
        _error("ANALYSIS_TOOL_DATASET_KIND_UNSUPPORTED", f"{role} must be vector/geojson")
    if not _crs(ref.crs).equals(_crs(OUTPUT_CRS)):
        _error("ANALYSIS_TOOL_CRS_MISMATCH", f"{role} must use {OUTPUT_CRS}")
    path = datasets.resolve_path(ref.id)
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
        declared = document["crs"]["properties"]["name"]
    except (OSError, UnicodeError, ValueError, KeyError, TypeError) as error:
        raise AnalysisToolError(
            "ANALYSIS_TOOL_CRS_INVALID", f"{role} requires an explicit GeoJSON CRS"
        ) from error
    if not isinstance(declared, str) or not _crs(declared).equals(_crs(OUTPUT_CRS)):
        _error("ANALYSIS_TOOL_CRS_MISMATCH", f"{role} GeoJSON CRS must match {OUTPUT_CRS}")
    return datasets.read(ref.id).records


def _population_items(
    datasets: DatasetRegistry, ref: DatasetRef | None
) -> tuple[tuple[str, Any, int | None, date], ...] | None:
    if ref is None:
        return None
    features = _load_projected_features(datasets, ref, role="population")
    results = []
    seen: set[str] = set()
    for feature in features:
        properties = feature.get("properties")
        if not isinstance(properties, dict):
            _error("ANALYSIS_TOOL_GTFS_POPULATION_INVALID", "population properties must be an object")
        grid_id = properties.get("grid_id")
        value = properties.get("population_total")
        reference = properties.get("population_reference_date")
        if not isinstance(grid_id, str) or not grid_id.strip() or grid_id in seen:
            _error("ANALYSIS_TOOL_GTFS_POPULATION_INVALID", "population grid_id missing or duplicated")
        seen.add(grid_id)
        if value is not None and (type(value) is not int or value < 0):
            _error("ANALYSIS_TOOL_GTFS_POPULATION_INVALID", "population_total must be nonnegative integer or null")
        try:
            reference_date = date.fromisoformat(reference)
        except (ValueError, TypeError) as error:
            raise AnalysisToolError(
                "ANALYSIS_TOOL_GTFS_POPULATION_INVALID", "population reference date must be ISO date"
            ) from error
        try:
            geometry = shape(feature.get("geometry"))
        except Exception as error:
            raise AnalysisToolError(
                "ANALYSIS_TOOL_GEOMETRY_INVALID", "population geometry cannot be parsed"
            ) from error
        if geometry.geom_type not in {"Polygon", "MultiPolygon"} or geometry.is_empty or not geometry.is_valid:
            _error("ANALYSIS_TOOL_GEOMETRY_INVALID", "population geometry must be valid Polygon/MultiPolygon")
        results.append((grid_id, geometry, value, reference_date))
    return tuple(sorted(results, key=lambda item: item[0]))


def _poi_items(
    datasets: DatasetRegistry, ref: DatasetRef | None
) -> tuple[tuple[str, Any], ...] | None:
    if ref is None:
        return None
    features = _load_projected_features(datasets, ref, role="POI")
    results = []
    seen: set[str] = set()
    for feature in features:
        properties = feature.get("properties")
        poi_id = properties.get("poi_id") if isinstance(properties, dict) else None
        if not isinstance(poi_id, str) or not poi_id.strip() or poi_id in seen:
            _error("ANALYSIS_TOOL_GTFS_POI_INVALID", "poi_id missing or duplicated")
        seen.add(poi_id)
        try:
            geometry = shape(feature.get("geometry"))
        except Exception as error:
            raise AnalysisToolError(
                "ANALYSIS_TOOL_GEOMETRY_INVALID", "POI geometry cannot be parsed"
            ) from error
        if geometry.geom_type != "Point" or geometry.is_empty or not geometry.is_valid:
            _error("ANALYSIS_TOOL_GEOMETRY_INVALID", "POI geometry must be a valid Point")
        results.append((poi_id, geometry))
    return tuple(sorted(results, key=lambda item: item[0]))


def _register_buffer_dataset(
    datasets: DatasetRegistry,
    *,
    parents: list[DatasetRef],
    parameters: dict[str, Any],
    features: list[dict[str, Any]],
    ttl_seconds: int,
) -> tuple[DatasetRef, DatasetLineage]:
    lineage = DatasetLineage(
        operation="analyze_gtfs_stop_coverage",
        parent_ids=[item.id for item in parents],
        timestamp=datetime.now(timezone.utc),
        parameters_hash=_parameters_hash(parameters),
    )
    payload = json.dumps(
        {"type": "FeatureCollection",
         "crs": {"type": "name", "properties": {"name": OUTPUT_CRS}},
         "features": features},
        ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False,
    )
    with tempfile.TemporaryDirectory(prefix="analyze_gtfs_stop_coverage_") as directory:
        path = Path(directory) / "result.geojson"
        path.write_text(payload, encoding="utf-8")
        ref = datasets.register_file(
            path, kind="vector", crs=OUTPUT_CRS,
            source="analysis:analyze_gtfs_stop_coverage", source_version="1.0",
            temporary=True, ttl_seconds=ttl_seconds, lineage=[lineage],
        )
    return ref, lineage


def analyze_gtfs_stop_coverage(
    contract: AnalyzeGtfsStopCoverageInput,
    datasets: DatasetRegistry,
) -> AnalyzeGtfsStopCoverageOutput:
    """Count boardable stop departures, then buffer active stops and aggregate once."""
    gtfs_ref = contract.dataset
    if gtfs_ref.kind != "network" or gtfs_ref.format != "gtfs":
        _error("ANALYSIS_TOOL_DATASET_KIND_UNSUPPORTED", "dataset must be network/gtfs")
    if gtfs_ref.crs != "EPSG:4326":
        _error("ANALYSIS_TOOL_CRS_MISMATCH", "GTFS stops require EPSG:4326")
    try:
        feed = load_gtfs_feed(datasets.resolve_path(gtfs_ref.id))
    except GtfsValidationError as error:
        raise AnalysisToolError(error.code, str(error)) from error
    if contract.timezone != feed.agency_timezone:
        _error("ANALYSIS_TOOL_GTFS_TIMEZONE_MISMATCH", "requested timezone must equal feed agency timezone")

    population = _population_items(datasets, contract.population_dataset)
    pois = _poi_items(datasets, contract.poi_dataset)
    routes = {row["route_id"]: row for row in feed.routes}
    stops = {row["stop_id"]: row for row in feed.stops}
    active_ids = set(active_trip_ids(feed, contract.service_date))
    trip_route = {row["trip_id"]: row["route_id"] for row in feed.trips if row["trip_id"] in active_ids}
    try:
        transformer = Transformer.from_crs("EPSG:4326", OUTPUT_CRS, always_xy=True)
    except (CRSError, ProjError) as error:
        raise AnalysisToolError("ANALYSIS_TOOL_CRS_INVALID", "GTFS coordinate transform unavailable") from error
    coordinates: dict[str, tuple[float, float]] = {}
    for stop_id, row in stops.items():
        try:
            x, y = transformer.transform(float(row["stop_lon"]), float(row["stop_lat"]), errcheck=True)
        except (ValueError, ProjError) as error:
            raise AnalysisToolError("ANALYSIS_TOOL_CRS_INVALID", "GTFS stop transform failed") from error
        if not (math.isfinite(x) and math.isfinite(y)):
            _error("ANALYSIS_TOOL_CRS_INVALID", "GTFS stop transform is non-finite")
        coordinates[stop_id] = (x, y)

    events: list[tuple[int, str, str, str]] = []
    conditional_pickup_excluded = False
    for row in feed.stop_times:
        trip_id = row["trip_id"]
        if trip_id not in active_ids or not row["departure_time"]:
            continue
        pickup_type = row.get("pickup_type", "0").strip() or "0"
        if pickup_type not in {"0", "1", "2", "3"}:
            _error("ANALYSIS_TOOL_GTFS_PICKUP_INVALID", "invalid stop_times.pickup_type")
        if pickup_type != "0":
            conditional_pickup_excluded = conditional_pickup_excluded or pickup_type in {"2", "3"}
            continue
        events.append((
            parse_gtfs_time(row["departure_time"]),
            row["stop_id"],
            trip_route[trip_id],
            trip_id,
        ))

    features: list[dict[str, Any]] = []
    summaries: list[GtfsWindowSummary] = []
    warnings = ["gtfs_feed_completeness_not_verified", "spatial_buffer_not_transit_isochrone"]
    if conditional_pickup_excluded:
        warnings.append("conditional_pickup_excluded_from_regular_frequency")
    if population is not None:
        warnings.append("population_complete_grid_lower_bound")
        if any(item[3] != contract.service_date for item in population):
            warnings.append("population_reference_date_differs_from_service_date")
    for window in contract.windows:
        start, end = parse_gtfs_time(window.start_time), parse_gtfs_time(window.end_time)
        selected = [
            event for event in events if start <= event[0] < end
        ]
        selected.sort(key=lambda item: (item[0], item[1], item[2], item[3]))
        stop_events: dict[str, list[tuple[int, str, str, str]]] = {}
        route_events: dict[str, list[tuple[int, str, str, str]]] = {}
        for event in selected:
            stop_events.setdefault(event[1], []).append(event)
            route_events.setdefault(event[2], []).append(event)
        if len(features) + len(stop_events) > MAX_BUFFER_FEATURES:
            _error("ANALYSIS_TOOL_LIMIT_EXCEEDED", "GTFS output buffer count exceeds limit")
        buffers = {}
        stop_frequencies = []
        for stop_id in sorted(stop_events):
            rows = stop_events[stop_id]
            route_ids = sorted({item[2] for item in rows})
            stop_frequencies.append(GtfsStopFrequency(
                stop_id=stop_id,
                stop_name=stops[stop_id]["stop_name"],
                departure_count=len(rows),
                trip_count=len({item[3] for item in rows}),
                route_ids=route_ids,
            ))
            x, y = coordinates[stop_id]
            buffer = Point(x, y).buffer(contract.buffer_distance_m, quad_segs=contract.quad_segs)
            buffers[stop_id] = buffer
            features.append({
                "type": "Feature",
                "id": f"{window.window_id}:{stop_id}",
                "geometry": mapping(buffer),
                "properties": {
                    "window_id": window.window_id,
                    "stop_id": stop_id,
                    "stop_name": stops[stop_id]["stop_name"],
                    "departure_count": len(rows),
                    "trip_count": len({item[3] for item in rows}),
                    "route_ids": route_ids,
                },
            })
        route_frequencies = [
            GtfsRouteFrequency(
                route_id=route_id,
                route_name=routes[route_id].get("route_short_name", "").strip()
                or routes[route_id].get("route_long_name", "").strip()
                or route_id,
                departure_count=len(rows),
                trip_count=len({item[3] for item in rows}),
                stop_ids=sorted({item[1] for item in rows}),
            )
            for route_id, rows in sorted(route_events.items())
        ]
        coverage = union_all([buffers[key] for key in sorted(buffers)]) if buffers else None
        full_grid_ids: list[str] = []
        partial_grid_ids: list[str] = []
        population_total: int | None = None
        population_unknown: int | None = None
        if population is not None:
            population_total = 0
            population_unknown = 0
            for grid_id, polygon, value, _reference_date in population:
                if coverage is not None and coverage.covers(polygon):
                    full_grid_ids.append(grid_id)
                    if value is None:
                        population_unknown += 1
                    else:
                        population_total += value
                elif coverage is not None and coverage.intersects(polygon):
                    partial_grid_ids.append(grid_id)
            if population_unknown:
                warnings.append("population_covered_grid_value_unknown")
        poi_ids: list[str] = []
        poi_count: int | None = None
        if pois is not None:
            poi_ids = [
                poi_id for poi_id, point in pois
                if coverage is not None and coverage.covers(point)
            ]
            poi_count = len(poi_ids)
        if not selected:
            warnings.append("no_scheduled_departures_in_window")
        summaries.append(GtfsWindowSummary(
            window_id=window.window_id,
            start_time=window.start_time,
            end_time=window.end_time,
            departure_count=len(selected),
            trip_count=len({item[3] for item in selected}),
            route_count=len(route_events),
            active_stop_count=len(stop_events),
            stop_frequencies=stop_frequencies,
            route_frequencies=route_frequencies,
            fully_covered_grid_ids=full_grid_ids,
            partially_intersecting_grid_ids=partial_grid_ids,
            covered_population_lower_bound=population_total,
            population_unknown_grid_count=population_unknown,
            covered_poi_ids=poi_ids,
            covered_poi_count=poi_count,
        ))

    base = summaries[0]
    comparisons = [
        GtfsWindowComparison(
            base_window_id=base.window_id,
            compared_window_id=item.window_id,
            delta_departures=item.departure_count - base.departure_count,
            delta_active_stops=item.active_stop_count - base.active_stop_count,
            delta_routes=item.route_count - base.route_count,
            delta_population_lower_bound=(
                item.covered_population_lower_bound - base.covered_population_lower_bound
                if item.covered_population_lower_bound is not None
                and base.covered_population_lower_bound is not None else None
            ),
            delta_poi_count=(
                item.covered_poi_count - base.covered_poi_count
                if item.covered_poi_count is not None and base.covered_poi_count is not None else None
            ),
        )
        for item in summaries[1:]
    ]
    parents = [
        item for item in
        (gtfs_ref, contract.population_dataset, contract.poi_dataset)
        if item is not None
    ]
    parameters = {
        "service_date": contract.service_date.isoformat(),
        "timezone": contract.timezone,
        "windows": [item.model_dump(mode="json") for item in contract.windows],
        "buffer_distance_m": contract.buffer_distance_m,
        "quad_segs": contract.quad_segs,
        "output_ttl_seconds": contract.output_ttl_seconds,
        "analysis_version": "1.0",
    }
    output_ref, lineage = _register_buffer_dataset(
        datasets, parents=parents, parameters=parameters, features=features,
        ttl_seconds=contract.output_ttl_seconds,
    )
    return AnalyzeGtfsStopCoverageOutput(
        dataset=output_ref,
        service_date=contract.service_date,
        timezone=contract.timezone,
        buffer_distance_m=contract.buffer_distance_m,
        windows=summaries,
        comparisons=comparisons,
        metadata=AnalysisToolMetadata(
            lineage=lineage,
            statistics={
                "input_gtfs_stop_count": len(feed.stops),
                "active_trip_count": len(active_ids),
                "window_count": len(summaries),
                "output_buffer_feature_count": len(features),
            },
            warnings=list(dict.fromkeys(warnings)),
        ),
    )
