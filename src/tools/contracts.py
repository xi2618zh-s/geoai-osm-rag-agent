"""Strict v1 contracts for DatasetRef-based deterministic analysis tools."""

from __future__ import annotations

from typing import Literal, Optional, TypeAlias, Union

from pydantic import ConfigDict, Field, StrictFloat, StrictInt, StrictStr, field_validator

from src.contracts import StrictContract
from src.datasets import DatasetField, DatasetLineage, DatasetRef
from src.geodsl.contracts import DatasetKind


StatisticValue: TypeAlias = Union[StrictInt, StrictFloat]
TOOL_NAME_PATTERN = r"^[a-z][a-z0-9_]{0,63}$"
CONTRACT_NAME_PATTERN = r"^[A-Za-z][A-Za-z0-9_.-]{2,127}$"


class AnalysisToolContract(StrictContract):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, strict=True)


class ToolLimits(AnalysisToolContract):
    max_input_features: int = Field(ge=0, le=10_000_000)
    max_input_bytes: int = Field(ge=1, le=10_000_000_000)
    max_output_bytes: int = Field(ge=1, le=1_000_000_000)


class ToolSpec(AnalysisToolContract):
    schema_version: Literal["1.0"] = "1.0"
    name: StrictStr = Field(pattern=TOOL_NAME_PATTERN)
    version: Literal["1.0"] = "1.0"
    description: StrictStr = Field(min_length=1, max_length=500)
    input_contract: StrictStr = Field(pattern=CONTRACT_NAME_PATTERN)
    output_contract: StrictStr = Field(pattern=CONTRACT_NAME_PATTERN)
    supported_dataset_kinds: list[DatasetKind] = Field(min_length=1, max_length=4)
    produces_dataset: bool
    mutates_input: Literal[False] = False
    deterministic: bool
    limits: ToolLimits

    @field_validator("supported_dataset_kinds")
    @classmethod
    def require_unique_kinds(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("supported_dataset_kinds must be unique")
        return value


class AnalysisToolMetadata(AnalysisToolContract):
    lineage: DatasetLineage
    statistics: dict[StrictStr, StatisticValue] = Field(default_factory=dict, max_length=128)
    warnings: list[StrictStr] = Field(default_factory=list, max_length=128)

    @field_validator("warnings")
    @classmethod
    def require_unique_warnings(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("warnings must be unique")
        return value


class InspectDatasetSchemaInput(AnalysisToolContract):
    schema_version: Literal["1.0"] = "1.0"
    dataset: DatasetRef


class InspectDatasetSchemaOutput(AnalysisToolContract):
    schema_version: Literal["1.0"] = "1.0"
    dataset: DatasetRef
    kind: Literal["vector", "table", "raster", "network"]
    format: Literal["geojson", "csv", "geotiff", "gtfs"]
    crs: StrictStr
    bbox: Optional[list[float]]
    row_count: int = Field(ge=0)
    fields: list[DatasetField]
    metadata: AnalysisToolMetadata


class ValidateGeometryInput(AnalysisToolContract):
    schema_version: Literal["1.0"] = "1.0"
    dataset: DatasetRef
    max_issues: int = Field(default=100, ge=0, le=1000)


class GeometryIssue(AnalysisToolContract):
    feature_index: int = Field(ge=0)
    feature_id: Optional[StrictStr] = Field(default=None, max_length=256)
    geometry_type: Optional[StrictStr] = Field(default=None, max_length=64)
    reason: StrictStr = Field(min_length=1, max_length=1000)


class ValidateGeometryOutput(AnalysisToolContract):
    schema_version: Literal["1.0"] = "1.0"
    dataset: DatasetRef
    geometry_engine: Literal["shapely-2"] = "shapely-2"
    is_valid: bool
    valid_geometry_count: int = Field(ge=0)
    invalid_geometry_count: int = Field(ge=0)
    empty_geometry_count: int = Field(ge=0)
    geometry_type_counts: dict[StrictStr, int]
    issues: list[GeometryIssue] = Field(max_length=1000)
    metadata: AnalysisToolMetadata
