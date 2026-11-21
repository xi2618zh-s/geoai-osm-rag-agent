"""Strict GeoDSL v1 contracts.

The models describe semantic intent and deterministic plan structure. They do
not execute tools or carry large datasets. Every spatial reference records a
CRS, and every distance is supplied with a unit before being normalized.
"""

from __future__ import annotations

from typing import ClassVar, Literal, Optional, TypeAlias, Union

from pydantic import ConfigDict, Field, StrictBool, StrictFloat, StrictInt, StrictStr, field_validator, model_validator

from src.contracts import StrictContract


GeoOperation: TypeAlias = Literal[
    "search",
    "within_distance",
    "nearest",
    "within",
    "spatial_join",
    "aggregate_by_region",
    "rank",
]
DatasetKind: TypeAlias = Literal["vector", "table", "raster", "network"]
FilterScalar: TypeAlias = Union[StrictStr, StrictInt, StrictFloat, StrictBool]
CRS_PATTERN = r"^EPSG:[1-9][0-9]{2,5}$"
NAME_PATTERN = r"^[a-z][a-z0-9_]{0,63}$"
FIELD_PATTERN = r"^[A-Za-z_][A-Za-z0-9_.:-]{0,127}$"


class GeoDSLContract(StrictContract):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, strict=True)


class Distance(GeoDSLContract):
    """A positive distance with an explicit input unit."""

    value: float = Field(gt=0)
    unit: Literal["m", "km", "mi", "ft"]

    _METERS_PER_UNIT: ClassVar[dict[str, float]] = {
        "m": 1.0,
        "km": 1000.0,
        "mi": 1609.344,
        "ft": 0.3048,
    }

    @property
    def meters(self) -> float:
        return float(self.value) * self._METERS_PER_UNIT[self.unit]


class PlaceReference(GeoDSLContract):
    name: StrictStr = Field(min_length=1, max_length=300)
    crs: StrictStr = Field(pattern=CRS_PATTERN)
    country_code: Optional[StrictStr] = Field(default=None, pattern=r"^[A-Z]{2}$")
    bbox: Optional[list[float]] = Field(default=None, min_length=4, max_length=4)

    @field_validator("name")
    @classmethod
    def reject_blank_name(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("place name must not be blank")
        return value.strip()

    @field_validator("bbox")
    @classmethod
    def validate_bbox_order(cls, value: Optional[list[float]]):
        if value is not None and not (value[0] < value[2] and value[1] < value[3]):
            raise ValueError("bbox must be [min_x, min_y, max_x, max_y]")
        return value


class DatasetRequirement(GeoDSLContract):
    role: StrictStr = Field(pattern=NAME_PATTERN)
    kind: DatasetKind
    source: StrictStr = Field(min_length=1, max_length=128)
    crs: StrictStr = Field(pattern=CRS_PATTERN)
    required_fields: list[StrictStr] = Field(default_factory=list, max_length=128)

    @field_validator("required_fields")
    @classmethod
    def require_unique_fields(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("required_fields must be unique")
        return value


class AttributeFilter(GeoDSLContract):
    dataset_role: StrictStr = Field(pattern=NAME_PATTERN)
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
    value: Union[FilterScalar, list[FilterScalar], None]

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


class SpatialPredicate(GeoDSLContract):
    relation: Literal["within_distance", "nearest", "within", "intersects", "contains"]
    left_dataset: StrictStr = Field(pattern=NAME_PATTERN)
    right_dataset: StrictStr = Field(pattern=NAME_PATTERN)
    distance: Optional[Distance] = None
    max_results: Optional[int] = Field(default=None, ge=1, le=1000)

    @model_validator(mode="after")
    def validate_relation_parameters(self):
        if self.left_dataset == self.right_dataset:
            raise ValueError("spatial predicate requires two different datasets")
        if self.relation == "within_distance":
            if self.distance is None:
                raise ValueError("within_distance requires distance")
            if self.max_results is not None:
                raise ValueError("within_distance does not accept max_results")
        elif self.relation == "nearest":
            if self.max_results is None:
                raise ValueError("nearest requires max_results")
        elif self.distance is not None or self.max_results is not None:
            raise ValueError(f"{self.relation} does not accept distance or max_results")
        return self


class MetricDefinition(GeoDSLContract):
    name: StrictStr = Field(pattern=NAME_PATTERN)
    operation: Literal["count", "sum", "mean", "min", "max", "area", "distance"]
    dataset_role: StrictStr = Field(pattern=NAME_PATTERN)
    field: Optional[StrictStr] = Field(default=None, pattern=FIELD_PATTERN)
    unit: Optional[Literal["m", "km", "mi", "ft", "m2", "km2"]] = None

    @model_validator(mode="after")
    def validate_metric_parameters(self):
        if self.operation in {"sum", "mean", "min", "max"} and self.field is None:
            raise ValueError(f"{self.operation} requires field")
        if self.operation == "count" and (self.field is not None or self.unit is not None):
            raise ValueError("count does not accept field or unit")
        if self.operation == "area":
            if self.field is not None or self.unit not in {"m2", "km2"}:
                raise ValueError("area requires an area unit and no field")
        if self.operation == "distance":
            if self.field is not None or self.unit not in {"m", "km", "mi", "ft"}:
                raise ValueError("distance requires a distance unit and no field")
        if self.operation in {"sum", "mean", "min", "max"} and self.unit in {"m2", "km2"}:
            raise ValueError("numeric aggregation cannot use an area unit")
        return self


class SortDefinition(GeoDSLContract):
    key: StrictStr = Field(pattern=FIELD_PATTERN)
    direction: Literal["asc", "desc"]
    nulls: Literal["first", "last"]


class OutputRequirement(GeoDSLContract):
    formats: list[Literal["map", "table", "chart", "geojson", "csv", "summary"]] = Field(
        min_length=1, max_length=6
    )
    crs: StrictStr = Field(pattern=CRS_PATTERN)
    include_fields: list[StrictStr] = Field(default_factory=list, max_length=128)
    limit: Optional[int] = Field(default=None, ge=1, le=10000)

    @field_validator("formats", "include_fields")
    @classmethod
    def require_unique_values(cls, value: list):
        if len(value) != len(set(value)):
            raise ValueError("list values must be unique")
        return value


class ClarificationRequest(GeoDSLContract):
    schema_version: Literal["1.0"] = "1.0"
    original_query: StrictStr = Field(min_length=1, max_length=2000)
    reason_code: Literal[
        "missing_place",
        "missing_distance",
        "ambiguous_target",
        "conflicting_constraints",
        "unsupported_operation",
    ]
    question: StrictStr = Field(min_length=1, max_length=1000)
    missing_fields: list[StrictStr] = Field(min_length=1, max_length=16)
    choices: list[StrictStr] = Field(default_factory=list, max_length=10)

    @field_validator("missing_fields", "choices")
    @classmethod
    def require_unique_items(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("clarification items must be unique")
        return value


class GeoTaskIntent(GeoDSLContract):
    schema_version: Literal["1.0"] = "1.0"
    task_type: GeoOperation
    query: StrictStr = Field(min_length=1, max_length=2000)
    place: PlaceReference
    analysis_crs: StrictStr = Field(pattern=CRS_PATTERN)
    datasets: list[DatasetRequirement] = Field(min_length=1, max_length=16)
    attribute_filters: list[AttributeFilter] = Field(default_factory=list, max_length=64)
    spatial_predicates: list[SpatialPredicate] = Field(default_factory=list, max_length=16)
    metrics: list[MetricDefinition] = Field(default_factory=list, max_length=32)
    sort: list[SortDefinition] = Field(default_factory=list, max_length=16)
    output: OutputRequirement

    @field_validator("query")
    @classmethod
    def reject_blank_query(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("query must not be blank")
        return value.strip()

    @model_validator(mode="after")
    def validate_intent_semantics(self):
        roles = [item.role for item in self.datasets]
        if len(roles) != len(set(roles)):
            raise ValueError("dataset roles must be unique")
        role_set = set(roles)
        referenced_roles = {item.dataset_role for item in self.attribute_filters}
        referenced_roles.update(item.dataset_role for item in self.metrics)
        for predicate in self.spatial_predicates:
            referenced_roles.update({predicate.left_dataset, predicate.right_dataset})
        unknown = referenced_roles.difference(role_set)
        if unknown:
            raise ValueError(f"intent references unknown dataset roles: {sorted(unknown)}")
        metric_names = [item.name for item in self.metrics]
        if len(metric_names) != len(set(metric_names)):
            raise ValueError("metric names must be unique")

        required_relation = {
            "within_distance": "within_distance",
            "nearest": "nearest",
            "within": "within",
        }.get(self.task_type)
        relations = {item.relation for item in self.spatial_predicates}
        if required_relation and required_relation not in relations:
            raise ValueError(f"{self.task_type} requires a {required_relation} predicate")
        if self.task_type == "search" and self.spatial_predicates:
            raise ValueError("search does not accept spatial predicates")
        if self.task_type == "spatial_join" and not self.spatial_predicates:
            raise ValueError("spatial_join requires a spatial predicate")
        if self.task_type == "aggregate_by_region" and not self.metrics:
            raise ValueError("aggregate_by_region requires metrics")
        if self.task_type == "rank" and not self.sort:
            raise ValueError("rank requires sort definitions")
        return self


class GeoAnalysisStep(GeoDSLContract):
    step_id: StrictStr = Field(pattern=NAME_PATTERN)
    operation: GeoOperation
    depends_on: list[StrictStr] = Field(default_factory=list, max_length=16)
    input_datasets: list[StrictStr] = Field(min_length=1, max_length=16)
    output_dataset: StrictStr = Field(pattern=NAME_PATTERN)
    filter_logic: Literal["all", "any"] = "all"
    attribute_filters: list[AttributeFilter] = Field(default_factory=list, max_length=64)
    spatial_predicate: Optional[SpatialPredicate] = None
    metrics: list[MetricDefinition] = Field(default_factory=list, max_length=32)
    sort: list[SortDefinition] = Field(default_factory=list, max_length=16)
    limit: Optional[int] = Field(default=None, ge=1, le=10000)

    @field_validator("depends_on", "input_datasets")
    @classmethod
    def require_unique_references(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("step references must be unique")
        return value

    @model_validator(mode="after")
    def validate_step_semantics(self):
        if self.step_id in self.depends_on:
            raise ValueError("step cannot depend on itself")
        if self.output_dataset in self.input_datasets:
            raise ValueError("output dataset must differ from input datasets")
        input_set = set(self.input_datasets)
        referenced_roles = {item.dataset_role for item in self.attribute_filters}
        referenced_roles.update(item.dataset_role for item in self.metrics)
        if self.spatial_predicate:
            referenced_roles.update(
                {self.spatial_predicate.left_dataset, self.spatial_predicate.right_dataset}
            )
        unknown = referenced_roles.difference(input_set)
        if unknown:
            raise ValueError(f"step references non-input datasets: {sorted(unknown)}")

        expected_relation = {
            "within_distance": "within_distance",
            "nearest": "nearest",
            "within": "within",
        }.get(self.operation)
        if expected_relation:
            if self.spatial_predicate is None or self.spatial_predicate.relation != expected_relation:
                raise ValueError(f"{self.operation} requires a {expected_relation} predicate")
        elif self.operation == "spatial_join":
            if self.spatial_predicate is None:
                raise ValueError("spatial_join requires a spatial predicate")
        elif self.spatial_predicate is not None:
            raise ValueError(f"{self.operation} does not accept a spatial predicate")
        if self.operation == "aggregate_by_region" and not self.metrics:
            raise ValueError("aggregate_by_region requires metrics")
        if self.operation == "rank" and not self.sort:
            raise ValueError("rank requires sort definitions")
        return self


class GeoAnalysisPlan(GeoDSLContract):
    schema_version: Literal["1.0"] = "1.0"
    plan_id: StrictStr = Field(pattern=NAME_PATTERN)
    source: Literal["user", "planner", "legacy_workflow"]
    source_version: StrictStr = Field(min_length=1, max_length=32)
    intent: GeoTaskIntent
    steps: list[GeoAnalysisStep] = Field(min_length=1, max_length=16)

    @model_validator(mode="after")
    def validate_step_graph_and_lineage(self):
        available_datasets = {item.role for item in self.intent.datasets}
        seen_steps: set[str] = set()
        for step in self.steps:
            if step.step_id in seen_steps:
                raise ValueError(f"duplicate step_id: {step.step_id}")
            missing_dependencies = set(step.depends_on).difference(seen_steps)
            if missing_dependencies:
                raise ValueError(
                    f"step {step.step_id} has unknown or future dependencies: "
                    f"{sorted(missing_dependencies)}"
                )
            missing_inputs = set(step.input_datasets).difference(available_datasets)
            if missing_inputs:
                raise ValueError(
                    f"step {step.step_id} has unavailable input datasets: "
                    f"{sorted(missing_inputs)}"
                )
            if step.output_dataset in available_datasets:
                raise ValueError(f"step output dataset already exists: {step.output_dataset}")
            seen_steps.add(step.step_id)
            available_datasets.add(step.output_dataset)
        return self
