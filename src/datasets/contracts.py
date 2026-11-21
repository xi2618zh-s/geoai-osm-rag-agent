"""Strict, backend-neutral dataset reference contracts."""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from pydantic import ConfigDict, Field, StrictStr, field_validator, model_validator

from src.contracts import StrictContract


DATASET_ID_PATTERN = r"^ds_[a-f0-9]{32}$"
CRS_PATTERN = r"^EPSG:[1-9][0-9]{2,5}$"


class DatasetContract(StrictContract):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, strict=True)


class DatasetField(DatasetContract):
    name: StrictStr = Field(min_length=1, max_length=256)
    type: Literal["null", "boolean", "integer", "number", "string", "array", "object", "mixed"]
    nullable: bool = False


class DatasetLineage(DatasetContract):
    operation: StrictStr = Field(min_length=1, max_length=128)
    parent_ids: list[StrictStr] = Field(default_factory=list, max_length=32)
    timestamp: datetime
    parameters_hash: Optional[StrictStr] = Field(
        default=None, pattern=r"^sha256:[a-f0-9]{64}$"
    )

    @field_validator("parent_ids")
    @classmethod
    def validate_parent_ids(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("lineage parent_ids must be unique")
        import re

        if any(re.fullmatch(DATASET_ID_PATTERN, item) is None for item in value):
            raise ValueError("lineage parent_ids must be dataset IDs")
        return value

    @field_validator("timestamp")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("timestamp must be timezone-aware")
        return value


class DatasetRef(DatasetContract):
    schema_version: Literal["1.0"] = "1.0"
    id: StrictStr = Field(pattern=DATASET_ID_PATTERN)
    kind: Literal["vector", "table", "raster", "network"]
    format: Literal["geojson", "csv", "geotiff", "gtfs"]
    storage_uri: StrictStr = Field(min_length=1, max_length=512)
    crs: StrictStr = Field(pattern=CRS_PATTERN)
    bbox: Optional[list[float]] = Field(default=None, min_length=4, max_length=4)
    row_count: int = Field(ge=0)
    source: StrictStr = Field(min_length=1, max_length=256)
    source_version: StrictStr = Field(min_length=1, max_length=128)
    created_at: datetime
    content_hash: StrictStr = Field(pattern=r"^sha256:[a-f0-9]{64}$")
    size_bytes: int = Field(ge=0)
    fields: list[DatasetField] = Field(default_factory=list, max_length=512)
    lineage: list[DatasetLineage] = Field(min_length=1, max_length=64)
    lifecycle: Literal["persistent", "temporary"]
    expires_at: Optional[datetime] = None

    @field_validator("created_at", "expires_at")
    @classmethod
    def require_timezone(cls, value: Optional[datetime]):
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("dataset timestamps must be timezone-aware")
        return value

    @field_validator("bbox")
    @classmethod
    def validate_bbox(cls, value: Optional[list[float]]):
        if value is not None and not (value[0] <= value[2] and value[1] <= value[3]):
            raise ValueError("bbox must be [min_x, min_y, max_x, max_y]")
        return value

    @field_validator("fields")
    @classmethod
    def require_unique_fields(cls, value: list[DatasetField]) -> list[DatasetField]:
        names = [item.name for item in value]
        if len(names) != len(set(names)):
            raise ValueError("dataset field names must be unique")
        return value

    @model_validator(mode="after")
    def validate_storage_and_lifecycle(self):
        expected = f"registry://datasets/{self.id}/data.{self.format}"
        if self.storage_uri != expected:
            raise ValueError("storage_uri must be the canonical registry URI")
        compatible = {
            ("vector", "geojson"),
            ("table", "csv"),
            ("raster", "geotiff"),
            ("network", "gtfs"),
        }
        if (self.kind, self.format) not in compatible:
            raise ValueError("dataset kind and format are incompatible")
        if self.kind == "table" and self.bbox is not None:
            raise ValueError("table datasets must not declare a spatial bbox")
        if self.lifecycle == "temporary":
            if self.expires_at is None or self.expires_at <= self.created_at:
                raise ValueError("temporary datasets require a future expires_at")
        elif self.expires_at is not None:
            raise ValueError("persistent datasets must not expire")
        return self
