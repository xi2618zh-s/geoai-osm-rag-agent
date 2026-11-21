"""Strict v1 contracts for M04b deterministic spatial analysis tools."""

from __future__ import annotations

from typing import Literal, Optional, TypeAlias, Union

from pydantic import (
    Field,
    StrictBool,
    StrictFloat,
    StrictInt,
    StrictStr,
    field_validator,
    model_validator,
)

from src.datasets import DatasetRef
from src.geodsl.contracts import FIELD_PATTERN

from .contracts import AnalysisToolContract, AnalysisToolMetadata


FilterScalar: TypeAlias = Union[StrictStr, StrictInt, StrictFloat, StrictBool]
FilterValue: TypeAlias = Union[FilterScalar, list[FilterScalar], None]


class DerivedToolInput(AnalysisToolContract):
    schema_version: Literal["1.0"] = "1.0"
    output_ttl_seconds: int = Field(default=86400, ge=1, le=604800)


class AttributeFilterCondition(AnalysisToolContract):
    field: StrictStr = Field(pattern=FIELD_PATTERN)
    operator: Literal[
        "eq",
        "ne",
        "gt",
        "gte",
        "lt",
        "lte",
        "in",
        "not_in",
        "contains",
        "is_null",
    ]
    value: FilterValue

    @model_validator(mode="after")
    def validate_operator_value(self):
        if self.operator == "is_null":
            if self.value is not None:
                raise ValueError("is_null requires a null value")
            return self
        if self.value is None:
            raise ValueError(f"{self.operator} requires a value")
        if self.operator in {"in", "not_in"}:
            if not isinstance(self.value, list) or not self.value:
                raise ValueError(f"{self.operator} requires a non-empty list")
        elif isinstance(self.value, list):
            raise ValueError(f"{self.operator} requires a scalar value")
        if self.operator == "contains" and not isinstance(self.value, str):
            raise ValueError("contains requires a string value")
        return self


class FilterAttributesInput(DerivedToolInput):
    dataset: DatasetRef
    conditions: list[AttributeFilterCondition] = Field(default_factory=list, max_length=64)
    logic: Literal["all", "any"] = "all"


class FilterAttributesOutput(AnalysisToolContract):
    schema_version: Literal["1.0"] = "1.0"
    dataset: DatasetRef
    input_feature_count: int = Field(ge=0)
    matched_feature_count: int = Field(ge=0)
    metadata: AnalysisToolMetadata


class ReprojectDatasetInput(DerivedToolInput):
    dataset: DatasetRef
    target_crs: StrictStr = Field(pattern=r"^EPSG:[1-9][0-9]{2,5}$")


class ReprojectDatasetOutput(AnalysisToolContract):
    schema_version: Literal["1.0"] = "1.0"
    dataset: DatasetRef
    source_crs: StrictStr
    target_crs: StrictStr
    transformed_geometry_count: int = Field(ge=0)
    metadata: AnalysisToolMetadata


class RepairGeometryInput(DerivedToolInput):
    dataset: DatasetRef


class RepairGeometryOutput(AnalysisToolContract):
    schema_version: Literal["1.0"] = "1.0"
    dataset: DatasetRef
    feature_count: int = Field(ge=0)
    repaired_geometry_count: int = Field(ge=0)
    geometry_type_change_count: int = Field(ge=0)
    dimension_reduction_count: int = Field(ge=0)
    geometry_collection_count: int = Field(ge=0)
    metadata: AnalysisToolMetadata


class BufferFeaturesInput(DerivedToolInput):
    dataset: DatasetRef
    distance_m: float = Field(gt=0)
    quad_segs: int = Field(default=8, ge=1, le=64)


class BufferFeaturesOutput(AnalysisToolContract):
    schema_version: Literal["1.0"] = "1.0"
    dataset: DatasetRef
    buffered_feature_count: int = Field(ge=0)
    distance_m: float = Field(gt=0)
    metadata: AnalysisToolMetadata


class NearestFeaturesInput(DerivedToolInput):
    dataset: DatasetRef
    target_dataset: DatasetRef
    k: int = Field(ge=1, le=1000)

    @model_validator(mode="after")
    def require_distinct_datasets(self):
        if self.dataset.id == self.target_dataset.id:
            raise ValueError("nearest requires two different datasets")
        return self


class NearestFeaturesOutput(AnalysisToolContract):
    schema_version: Literal["1.0"] = "1.0"
    dataset: DatasetRef
    source_feature_count: int = Field(ge=0)
    target_feature_count: int = Field(ge=0)
    matched_pair_count: int = Field(ge=0)
    metadata: AnalysisToolMetadata


class FeaturesWithinInput(DerivedToolInput):
    dataset: DatasetRef
    container_dataset: DatasetRef

    @model_validator(mode="after")
    def require_distinct_datasets(self):
        if self.dataset.id == self.container_dataset.id:
            raise ValueError("features_within requires two different datasets")
        return self


class FeaturesWithinOutput(AnalysisToolContract):
    schema_version: Literal["1.0"] = "1.0"
    dataset: DatasetRef
    input_feature_count: int = Field(ge=0)
    container_feature_count: int = Field(ge=0)
    matched_feature_count: int = Field(ge=0)
    metadata: AnalysisToolMetadata


class SpatialJoinInput(DerivedToolInput):
    dataset: DatasetRef
    right_dataset: DatasetRef
    predicate: Literal["intersects", "within", "contains"]

    @model_validator(mode="after")
    def require_distinct_datasets(self):
        if self.dataset.id == self.right_dataset.id:
            raise ValueError("spatial_join requires two different datasets")
        return self


class SpatialJoinOutput(AnalysisToolContract):
    schema_version: Literal["1.0"] = "1.0"
    dataset: DatasetRef
    left_feature_count: int = Field(ge=0)
    right_feature_count: int = Field(ge=0)
    matched_pair_count: int = Field(ge=0)
    predicate: Literal["intersects", "within", "contains"]
    metadata: AnalysisToolMetadata


class AggregationMetric(AnalysisToolContract):
    operation: Literal["count", "sum", "mean", "min", "max"]
    field: Optional[StrictStr] = Field(default=None, pattern=FIELD_PATTERN)

    @model_validator(mode="after")
    def validate_field(self):
        if self.operation == "count" and self.field is not None:
            raise ValueError("count does not accept a field")
        if self.operation != "count" and self.field is None:
            raise ValueError(f"{self.operation} requires a field")
        return self

    @property
    def output_field(self) -> str:
        return (
            "agg_count"
            if self.operation == "count"
            else f"agg_{self.operation}_{self.field}"
        )


class AggregateByRegionInput(DerivedToolInput):
    dataset: DatasetRef
    values_dataset: DatasetRef
    region_id_field: StrictStr = Field(pattern=FIELD_PATTERN)
    values_region_id_field: StrictStr = Field(pattern=FIELD_PATTERN)
    metrics: list[AggregationMetric] = Field(min_length=1, max_length=32)

    @model_validator(mode="after")
    def validate_datasets_and_metrics(self):
        if self.dataset.id == self.values_dataset.id:
            raise ValueError("aggregate_by_region requires two different datasets")
        output_fields = [item.output_field for item in self.metrics]
        if len(output_fields) != len(set(output_fields)):
            raise ValueError("aggregation metrics generate duplicate output fields")
        return self


class AggregateByRegionOutput(AnalysisToolContract):
    schema_version: Literal["1.0"] = "1.0"
    dataset: DatasetRef
    region_count: int = Field(ge=0)
    value_row_count: int = Field(ge=0)
    matched_value_row_count: int = Field(ge=0)
    unmatched_value_row_count: int = Field(ge=0)
    output_fields: list[StrictStr] = Field(min_length=1, max_length=32)
    metadata: AnalysisToolMetadata
