"""Strict contracts for the SCB population adapter and reference case."""

from __future__ import annotations

from datetime import datetime
import math
from typing import Literal, Optional

from pydantic import Field, StrictFloat, StrictStr, field_validator

from src.contracts import StrictContract
from src.datasets import DatasetRef
from src.geodsl.contracts import FIELD_PATTERN


SCB_PROVIDER = "scb"
SCB_DATASET_YEAR = 2025
SCB_LAYER = "stat:befolkning_1km_2025"
SCB_CRS = "EPSG:3006"
SCB_WFS_VERSION = "1.1.0"
SCB_SORT_BY = "objectid"
SCB_WFS_BASE_URL = "https://geodata.scb.se/geoserver/stat/wfs"
SCB_SCHEMA_URL = (
    f"{SCB_WFS_BASE_URL}?service=WFS&version={SCB_WFS_VERSION}"
    f"&request=DescribeFeatureType&typeName=stat%3Abefolkning_1km_2025"
)
SCB_PROPERTY_NAMES = (
    "objectid",
    "rutid_inspire",
    "rutid_scb",
    "rutstorl",
    "beftotalt",
    "man",
    "kvinna",
    "ald0_4",
    "ald5_9",
    "ald10_14",
    "ald15_19",
    "ald20_24",
    "ald25_29",
    "ald30_34",
    "ald35_39",
    "ald40_44",
    "ald45_49",
    "ald50_54",
    "ald55_59",
    "ald60_64",
    "ald65_69",
    "ald70_74",
    "ald75_79",
    "ald80_84",
    "ald85_89",
    "ald90_94",
    "ald95_99",
    "ald100w",
    "referenstid",
    "sp_geometry",
)
CONSERVATIVE_POPULATION_WARNING = (
    "fully_contained_grid_population_is_a_conservative_lower_bound_"
    "not_an_area_weighted_or_true_buffer_population_estimate"
)


class PopulationContract(StrictContract):
    pass


class ScbPopulationGridRequest(PopulationContract):
    schema_version: Literal["1.0"] = "1.0"
    provider: Literal["scb"] = "scb"
    dataset_year: Literal[2025] = 2025
    bbox: list[StrictFloat] = Field(min_length=4, max_length=4)
    wfs_version: Literal["1.1.0"] = "1.1.0"
    type_name: Literal["stat:befolkning_1km_2025"] = "stat:befolkning_1km_2025"
    srs_name: Literal["EPSG:3006"] = "EPSG:3006"
    output_format: Literal["application/json"] = "application/json"
    sort_by: Literal["objectid"] = "objectid"

    @field_validator("bbox")
    @classmethod
    def validate_bbox(cls, value: list[float]) -> list[float]:
        if not all(math.isfinite(item) for item in value):
            raise ValueError("bbox coordinates must be finite")
        if value[0] >= value[2] or value[1] >= value[3]:
            raise ValueError("bbox must have positive width and height")
        return value


class PopulationDownloadReceipt(PopulationContract):
    schema_version: Literal["1.0"] = "1.0"
    provider: Literal["scb"] = "scb"
    layer: Literal["stat:befolkning_1km_2025"] = "stat:befolkning_1km_2025"
    dataset_year: Literal[2025] = 2025
    crs: Literal["EPSG:3006"] = "EPSG:3006"
    license: Literal["CC0-1.0"] = "CC0-1.0"
    canonical_url: StrictStr
    schema_url: StrictStr
    retrieved_at: datetime
    status_code: Literal[200] = 200
    content_type: StrictStr
    last_modified: Optional[StrictStr] = None
    etag: Optional[StrictStr] = None
    feature_count: int = Field(ge=0)
    reference_dates: list[StrictStr]
    content_sha256: StrictStr = Field(pattern=r"^[a-f0-9]{64}$")
    schema_sha256: StrictStr = Field(pattern=r"^[a-f0-9]{64}$")

    @field_validator("retrieved_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("retrieved_at must be timezone-aware")
        return value


class FullyContainedGridPopulationInput(PopulationContract):
    schema_version: Literal["1.0"] = "1.0"
    poi_dataset: DatasetRef
    population_dataset: DatasetRef
    poi_id_field: StrictStr = Field(pattern=FIELD_PATTERN)
    buffer_distance_m: float = Field(gt=0)
    quad_segs: int = Field(default=8, ge=1, le=64)
    output_ttl_seconds: int = Field(default=86400, ge=1, le=604800)


class FullyContainedGridPopulationOutput(PopulationContract):
    schema_version: Literal["1.0"] = "1.0"
    method: Literal["fully_contained_grid_population"] = (
        "fully_contained_grid_population"
    )
    dataset: DatasetRef
    fully_contained_grid_ids: list[StrictStr]
    fully_contained_grid_count: int = Field(ge=0)
    partially_intersecting_excluded_grid_count: int = Field(ge=0)
    fully_contained_grid_population: Optional[int] = Field(default=None, ge=0)
    warning: Literal[
        "fully_contained_grid_population_is_a_conservative_lower_bound_not_an_area_weighted_or_true_buffer_population_estimate"
    ] = CONSERVATIVE_POPULATION_WARNING
