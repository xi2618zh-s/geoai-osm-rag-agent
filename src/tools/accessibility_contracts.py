"""Strict v1 contracts for deterministic GTFS stop-service accessibility."""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import Field, StrictStr, field_validator, model_validator

from src.datasets import DatasetRef
from src.gtfs.ingest import parse_gtfs_time

from .contracts import AnalysisToolContract, AnalysisToolMetadata


MAX_SERVICE_SECONDS = 48 * 3600


class GtfsTimeWindow(AnalysisToolContract):
    schema_version: Literal["1.0"] = "1.0"
    window_id: StrictStr = Field(pattern=r"^[a-z][a-z0-9_]{0,31}$")
    start_time: StrictStr
    end_time: StrictStr

    @model_validator(mode="after")
    def require_valid_half_open_window(self):
        start = parse_gtfs_time(self.start_time)
        end = parse_gtfs_time(self.end_time)
        if start < 0 or end >= MAX_SERVICE_SECONDS or end <= start:
            raise ValueError("window must satisfy 0 <= start < end < 48:00:00")
        return self


class AnalyzeGtfsStopCoverageInput(AnalysisToolContract):
    schema_version: Literal["1.0"] = "1.0"
    dataset: DatasetRef
    population_dataset: DatasetRef | None = None
    poi_dataset: DatasetRef | None = None
    service_date: date
    timezone: StrictStr = Field(min_length=1, max_length=64)
    windows: list[GtfsTimeWindow] = Field(min_length=1, max_length=8)
    buffer_distance_m: float = Field(gt=0, le=5000)
    quad_segs: int = Field(default=8, ge=1, le=32)
    output_ttl_seconds: int = Field(default=86400, ge=1, le=604800)

    @field_validator("service_date")
    @classmethod
    def require_date_only(cls, value: date) -> date:
        if type(value) is not date:
            raise ValueError("service_date must be a date")
        return value

    @model_validator(mode="after")
    def require_distinct_inputs_and_ordered_windows(self):
        ids = [
            item.id for item in
            (self.dataset, self.population_dataset, self.poi_dataset)
            if item is not None
        ]
        if len(ids) != len(set(ids)):
            raise ValueError("GTFS, population and POI datasets must be distinct")
        seen: set[str] = set()
        previous_end = -1
        for window in self.windows:
            if window.window_id in seen:
                raise ValueError("window_id values must be unique")
            seen.add(window.window_id)
            start = parse_gtfs_time(window.start_time)
            if start < previous_end:
                raise ValueError("windows must be ordered and non-overlapping")
            previous_end = parse_gtfs_time(window.end_time)
        return self


class GtfsStopFrequency(AnalysisToolContract):
    stop_id: StrictStr
    stop_name: StrictStr
    departure_count: int = Field(ge=0)
    trip_count: int = Field(ge=0)
    route_ids: list[StrictStr]


class GtfsRouteFrequency(AnalysisToolContract):
    route_id: StrictStr
    route_name: StrictStr
    departure_count: int = Field(ge=0)
    trip_count: int = Field(ge=0)
    stop_ids: list[StrictStr]


class GtfsWindowSummary(AnalysisToolContract):
    window_id: StrictStr
    start_time: StrictStr
    end_time: StrictStr
    departure_count: int = Field(ge=0)
    trip_count: int = Field(ge=0)
    route_count: int = Field(ge=0)
    active_stop_count: int = Field(ge=0)
    stop_frequencies: list[GtfsStopFrequency]
    route_frequencies: list[GtfsRouteFrequency]
    fully_covered_grid_ids: list[StrictStr]
    partially_intersecting_grid_ids: list[StrictStr]
    covered_population_lower_bound: int | None = Field(default=None, ge=0)
    population_unknown_grid_count: int | None = Field(default=None, ge=0)
    covered_poi_ids: list[StrictStr]
    covered_poi_count: int | None = Field(default=None, ge=0)


class GtfsWindowComparison(AnalysisToolContract):
    base_window_id: StrictStr
    compared_window_id: StrictStr
    delta_departures: int
    delta_active_stops: int
    delta_routes: int
    delta_population_lower_bound: int | None
    delta_poi_count: int | None


class AnalyzeGtfsStopCoverageOutput(AnalysisToolContract):
    schema_version: Literal["1.0"] = "1.0"
    dataset: DatasetRef
    service_date: date
    timezone: StrictStr
    buffer_crs: Literal["EPSG:3006"] = "EPSG:3006"
    buffer_distance_m: float = Field(gt=0)
    windows: list[GtfsWindowSummary]
    comparisons: list[GtfsWindowComparison]
    metadata: AnalysisToolMetadata
