"""M04b deterministic GeoJSON spatial analysis tools."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import tempfile
from typing import Any, Iterable

from pyproj import CRS, Transformer
from pyproj.exceptions import CRSError, ProjError
from shapely import get_dimensions, make_valid
from shapely.geometry import mapping, shape
from shapely.ops import transform as transform_geometry

from src.datasets import DatasetLineage, DatasetRef, DatasetRegistry

from .contracts import AnalysisToolMetadata, ToolLimits, ToolSpec
from .quality import (
    DEFAULT_MAX_INPUT_BYTES,
    DEFAULT_MAX_INPUT_FEATURES,
    DEFAULT_MAX_OUTPUT_BYTES,
    build_quality_tool_registry,
)
from .registry import AnalysisToolError, AnalysisToolRegistry, ToolRegistration
from .spatial_contracts import (
    AggregateByRegionInput,
    AggregateByRegionOutput,
    AggregationMetric,
    AttributeFilterCondition,
    BufferFeaturesInput,
    BufferFeaturesOutput,
    FeaturesWithinInput,
    FeaturesWithinOutput,
    FilterAttributesInput,
    FilterAttributesOutput,
    NearestFeaturesInput,
    NearestFeaturesOutput,
    RepairGeometryInput,
    RepairGeometryOutput,
    ReprojectDatasetInput,
    ReprojectDatasetOutput,
    SpatialJoinInput,
    SpatialJoinOutput,
)


def _parameters_hash(parameters: dict[str, Any]) -> str:
    payload = json.dumps(
        parameters,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(payload).hexdigest()}"


def _lineage(
    operation: str,
    parents: list[DatasetRef],
    parameters: dict[str, Any],
) -> DatasetLineage:
    return DatasetLineage(
        operation=operation,
        parent_ids=[item.id for item in parents],
        timestamp=datetime.now(timezone.utc),
        parameters_hash=_parameters_hash(parameters),
    )


def _metadata(
    lineage: DatasetLineage,
    statistics: dict[str, int | float],
    warnings: list[str],
) -> AnalysisToolMetadata:
    return AnalysisToolMetadata(
        lineage=lineage,
        statistics=statistics,
        warnings=list(dict.fromkeys(warnings)),
    )


def _register_output(
    datasets: DatasetRegistry,
    *,
    operation: str,
    parents: list[DatasetRef],
    parameters: dict[str, Any],
    features: list[dict[str, Any]],
    crs: str,
    ttl_seconds: int,
) -> tuple[DatasetRef, DatasetLineage]:
    lineage = _lineage(operation, parents, parameters)
    document = {"type": "FeatureCollection", "features": features}
    try:
        payload = json.dumps(
            document,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as error:
        raise AnalysisToolError(
            "ANALYSIS_TOOL_GEOMETRY_INVALID",
            f"{operation} produced non-serializable GeoJSON: {error}",
        ) from error
    with tempfile.TemporaryDirectory(prefix=f"{operation}_") as directory:
        path = Path(directory) / "result.geojson"
        path.write_text(payload, encoding="utf-8")
        ref = datasets.register_file(
            path,
            kind="vector",
            crs=crs,
            source=f"analysis:{operation}",
            source_version="1.0",
            temporary=True,
            ttl_seconds=ttl_seconds,
            lineage=[lineage],
        )
    return ref, lineage


def _parse_crs(value: str) -> CRS:
    try:
        return CRS.from_user_input(value)
    except (CRSError, ProjError, ValueError) as error:
        raise AnalysisToolError(
            "ANALYSIS_TOOL_CRS_INVALID", f"Invalid CRS {value}: {error}"
        ) from error


def _require_equivalent_crs(left: DatasetRef, right: DatasetRef) -> CRS:
    left_crs = _parse_crs(left.crs)
    right_crs = _parse_crs(right.crs)
    if not left_crs.equals(right_crs):
        raise AnalysisToolError(
            "ANALYSIS_TOOL_CRS_MISMATCH",
            f"Dataset CRS values are not equivalent: {left.crs} vs {right.crs}",
        )
    return left_crs


def _distance_unit_factor(crs_value: str) -> tuple[CRS, float]:
    crs = _parse_crs(crs_value)
    if crs.is_geographic:
        raise AnalysisToolError(
            "ANALYSIS_TOOL_GEOGRAPHIC_DISTANCE_UNSUPPORTED",
            f"Meter-based distance is not supported in geographic CRS {crs_value}",
        )
    axes = list(crs.axis_info)
    horizontal_directions = {"east", "west", "north", "south"}
    if (
        not crs.is_projected
        or len(axes) != 2
        or any(axis.direction.lower() not in horizontal_directions for axis in axes)
    ):
        raise AnalysisToolError(
            "ANALYSIS_TOOL_CRS_UNIT_UNSUPPORTED",
            f"CRS must be a two-dimensional projected CRS: {crs_value}",
        )
    factors: list[float] = []
    for axis in axes:
        factor = axis.unit_conversion_factor
        if factor is None:
            raise AnalysisToolError(
                "ANALYSIS_TOOL_CRS_UNIT_UNSUPPORTED",
                f"CRS axis has no conversion-to-metre factor: {crs_value}",
            )
        numeric = float(factor)
        if not math.isfinite(numeric) or numeric <= 0:
            raise AnalysisToolError(
                "ANALYSIS_TOOL_CRS_UNIT_UNSUPPORTED",
                f"CRS axis conversion factor is invalid: {crs_value}",
            )
        factors.append(numeric)
    if not math.isclose(factors[0], factors[1], rel_tol=1e-12, abs_tol=0.0):
        raise AnalysisToolError(
            "ANALYSIS_TOOL_CRS_UNIT_UNSUPPORTED",
            f"CRS horizontal axes use inconsistent units: {crs_value}",
        )
    return crs, factors[0]


def _feature_id(feature: dict[str, Any], index: int) -> str:
    candidate = feature.get("id")
    properties = feature.get("properties")
    if candidate is None and isinstance(properties, dict):
        candidate = properties.get("id")
    return str(candidate) if candidate is not None else f"feature-{index}"


def _valid_geometries(records: Iterable[dict[str, Any]]) -> list[Any]:
    geometries: list[Any] = []
    for index, feature in enumerate(records):
        try:
            geometry = shape(feature["geometry"])
        except Exception as error:
            raise AnalysisToolError(
                "ANALYSIS_TOOL_GEOMETRY_INVALID",
                f"Feature {_feature_id(feature, index)} geometry cannot be parsed: {error}",
            ) from error
        if geometry.is_empty or not geometry.is_valid:
            raise AnalysisToolError(
                "ANALYSIS_TOOL_GEOMETRY_INVALID",
                f"Feature {_feature_id(feature, index)} geometry is empty or invalid",
            )
        geometries.append(geometry)
    return geometries


def _all_finite(value: Any) -> bool:
    if isinstance(value, bool):
        return False
    if isinstance(value, (int, float)):
        return math.isfinite(float(value))
    if isinstance(value, (list, tuple)):
        return all(_all_finite(item) for item in value)
    return False


def _mapped_geometry(geometry: Any) -> dict[str, Any]:
    result = mapping(geometry)
    if result["type"] == "GeometryCollection":
        if not all(
            _all_finite(item.get("coordinates"))
            if item.get("type") != "GeometryCollection"
            else True
            for item in result.get("geometries", [])
        ):
            raise AnalysisToolError(
                "ANALYSIS_TOOL_GEOMETRY_INVALID",
                "GeometryCollection contains non-finite coordinates",
            )
    elif not _all_finite(result.get("coordinates")):
        raise AnalysisToolError(
            "ANALYSIS_TOOL_GEOMETRY_INVALID",
            "Geometry contains non-finite coordinates",
        )
    return result


def _field_names(ref: DatasetRef) -> set[str]:
    return {item.name for item in ref.fields}


def _require_fields(ref: DatasetRef, fields: Iterable[str]) -> None:
    missing = sorted(set(fields).difference(_field_names(ref)))
    if missing:
        raise AnalysisToolError(
            "ANALYSIS_TOOL_FIELD_MISSING",
            f"Dataset {ref.id} is missing fields: {missing}",
        )


def _is_number(value: Any) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
    )


def _validate_filter_types(contract: FilterAttributesInput) -> None:
    declared = {item.name: item.type for item in contract.dataset.fields}
    for condition in contract.conditions:
        field_type = declared[condition.field]
        if condition.operator in {"gt", "gte", "lt", "lte"}:
            value = condition.value
            compatible = (
                field_type in {"integer", "number"} and _is_number(value)
            ) or (field_type == "string" and isinstance(value, str))
            if not compatible:
                raise AnalysisToolError(
                    "ANALYSIS_TOOL_FIELD_TYPE_INVALID",
                    f"{condition.operator} operands are incompatible for {condition.field}",
                )
        if condition.operator == "contains" and field_type not in {"string", "mixed"}:
            raise AnalysisToolError(
                "ANALYSIS_TOOL_FIELD_TYPE_INVALID",
                f"contains requires a string field: {condition.field}",
            )


def _condition_matches(properties: dict[str, Any], condition: AttributeFilterCondition) -> bool:
    value = properties.get(condition.field)
    expected = condition.value
    if condition.operator == "is_null":
        return value is None
    if condition.operator == "eq":
        return value == expected
    if condition.operator == "ne":
        return value != expected
    if condition.operator == "in":
        return value in expected
    if condition.operator == "not_in":
        return value not in expected
    if condition.operator == "contains":
        if value is None:
            return False
        if not isinstance(value, str):
            raise AnalysisToolError(
                "ANALYSIS_TOOL_FIELD_TYPE_INVALID",
                f"contains encountered a non-string value in {condition.field}",
            )
        return expected in value
    if value is None:
        return False
    if condition.operator == "gt":
        return value > expected
    if condition.operator == "gte":
        return value >= expected
    if condition.operator == "lt":
        return value < expected
    if condition.operator == "lte":
        return value <= expected
    raise AssertionError(f"Unhandled filter operator: {condition.operator}")


def filter_attributes(
    contract: FilterAttributesInput, datasets: DatasetRegistry
) -> FilterAttributesOutput:
    loaded = datasets.read(contract.dataset.id)
    _require_fields(loaded.ref, [item.field for item in contract.conditions])
    _validate_filter_types(contract)
    selected: list[dict[str, Any]] = []
    for feature in loaded.records:
        matches = [
            _condition_matches(feature["properties"], condition)
            for condition in contract.conditions
        ]
        keep = True if not matches else (
            all(matches) if contract.logic == "all" else any(matches)
        )
        if keep:
            selected.append(deepcopy(feature))
    parameters = {
        "conditions": [item.model_dump(mode="json") for item in contract.conditions],
        "logic": contract.logic,
        "output_ttl_seconds": contract.output_ttl_seconds,
    }
    output, lineage = _register_output(
        datasets,
        operation="filter_attributes",
        parents=[loaded.ref],
        parameters=parameters,
        features=selected,
        crs=loaded.ref.crs,
        ttl_seconds=contract.output_ttl_seconds,
    )
    warnings = ["dataset_empty"] if not loaded.records else []
    if loaded.records and not selected:
        warnings.append("result_empty")
    stats = {
        "input_feature_count": len(loaded.records),
        "matched_feature_count": len(selected),
    }
    return FilterAttributesOutput(
        dataset=output,
        input_feature_count=len(loaded.records),
        matched_feature_count=len(selected),
        metadata=_metadata(lineage, stats, warnings),
    )


def reproject_dataset(
    contract: ReprojectDatasetInput, datasets: DatasetRegistry
) -> ReprojectDatasetOutput:
    loaded = datasets.read(contract.dataset.id)
    source_crs = _parse_crs(loaded.ref.crs)
    target_crs = _parse_crs(contract.target_crs)
    equivalent = source_crs.equals(target_crs)
    transformer = None if equivalent else Transformer.from_crs(
        source_crs, target_crs, always_xy=True
    )
    output_features: list[dict[str, Any]] = []
    for index, feature in enumerate(loaded.records):
        try:
            geometry = shape(feature["geometry"])
            if geometry.is_empty:
                raise ValueError("geometry is empty")
            if transformer is not None:
                def project(x, y, z=None):
                    tx, ty = transformer.transform(x, y, errcheck=True)
                    if not _all_finite(tx) or not _all_finite(ty):
                        raise ValueError("transformation returned non-finite coordinates")
                    if z is None:
                        return tx, ty
                    if not _all_finite(z):
                        raise ValueError("input Z coordinate is non-finite")
                    return tx, ty, z

                geometry = transform_geometry(project, geometry)
            mapped = _mapped_geometry(geometry)
        except AnalysisToolError:
            raise
        except Exception as error:
            raise AnalysisToolError(
                "ANALYSIS_TOOL_CRS_INVALID",
                f"Could not transform feature {_feature_id(feature, index)}: {error}",
            ) from error
        transformed = deepcopy(feature)
        transformed["geometry"] = mapped
        output_features.append(transformed)
    parameters = {
        "target_crs": contract.target_crs,
        "always_xy": True,
        "errcheck": True,
        "output_ttl_seconds": contract.output_ttl_seconds,
    }
    output, lineage = _register_output(
        datasets,
        operation="reproject_dataset",
        parents=[loaded.ref],
        parameters=parameters,
        features=output_features,
        crs=contract.target_crs,
        ttl_seconds=contract.output_ttl_seconds,
    )
    warnings = []
    if not loaded.records:
        warnings.append("dataset_empty")
    if equivalent:
        warnings.append("source_and_target_crs_equivalent")
    transformed_count = 0 if equivalent else len(loaded.records)
    stats = {
        "feature_count": len(loaded.records),
        "transformed_geometry_count": transformed_count,
    }
    return ReprojectDatasetOutput(
        dataset=output,
        source_crs=loaded.ref.crs,
        target_crs=contract.target_crs,
        transformed_geometry_count=transformed_count,
        metadata=_metadata(lineage, stats, warnings),
    )


def repair_geometry(
    contract: RepairGeometryInput, datasets: DatasetRegistry
) -> RepairGeometryOutput:
    loaded = datasets.read(contract.dataset.id)
    output_features: list[dict[str, Any]] = []
    repaired_count = 0
    type_change_count = 0
    dimension_reduction_count = 0
    collection_count = 0
    for index, feature in enumerate(loaded.records):
        try:
            original = shape(feature["geometry"])
        except Exception as error:
            raise AnalysisToolError(
                "ANALYSIS_TOOL_GEOMETRY_REPAIR_FAILED",
                f"Could not parse feature {_feature_id(feature, index)}: {error}",
            ) from error
        if original.is_empty:
            raise AnalysisToolError(
                "ANALYSIS_TOOL_GEOMETRY_REPAIR_FAILED",
                f"Feature {_feature_id(feature, index)} is empty",
            )
        repaired = original
        if not original.is_valid:
            try:
                repaired = make_valid(original)
            except Exception as error:
                raise AnalysisToolError(
                    "ANALYSIS_TOOL_GEOMETRY_REPAIR_FAILED",
                    f"make_valid failed for {_feature_id(feature, index)}: {error}",
                ) from error
            repaired_count += 1
            if repaired.is_empty or not repaired.is_valid:
                raise AnalysisToolError(
                    "ANALYSIS_TOOL_GEOMETRY_REPAIR_FAILED",
                    f"Feature {_feature_id(feature, index)} remained empty or invalid",
                )
        if repaired.geom_type != original.geom_type:
            type_change_count += 1
        if int(get_dimensions(repaired)) < int(get_dimensions(original)):
            dimension_reduction_count += 1
        if repaired.geom_type == "GeometryCollection":
            collection_count += 1
        try:
            mapped = deepcopy(feature["geometry"]) if original.is_valid else _mapped_geometry(repaired)
            json.dumps(mapped, allow_nan=False)
        except Exception as error:
            raise AnalysisToolError(
                "ANALYSIS_TOOL_GEOMETRY_REPAIR_FAILED",
                f"Could not serialize repaired feature {_feature_id(feature, index)}: {error}",
            ) from error
        output_feature = deepcopy(feature)
        output_feature["geometry"] = mapped
        output_features.append(output_feature)
    parameters = {"method": "make_valid", "output_ttl_seconds": contract.output_ttl_seconds}
    output, lineage = _register_output(
        datasets,
        operation="repair_geometry",
        parents=[loaded.ref],
        parameters=parameters,
        features=output_features,
        crs=loaded.ref.crs,
        ttl_seconds=contract.output_ttl_seconds,
    )
    warnings = []
    if not loaded.records:
        warnings.append("dataset_empty")
    if type_change_count:
        warnings.append("geometry_type_changed")
    if dimension_reduction_count:
        warnings.append("geometry_dimension_reduced")
    if collection_count:
        warnings.append("geometry_collection_produced")
    stats = {
        "feature_count": len(loaded.records),
        "repaired_geometry_count": repaired_count,
        "geometry_type_change_count": type_change_count,
        "dimension_reduction_count": dimension_reduction_count,
        "geometry_collection_count": collection_count,
    }
    return RepairGeometryOutput(
        dataset=output,
        feature_count=len(loaded.records),
        repaired_geometry_count=repaired_count,
        geometry_type_change_count=type_change_count,
        dimension_reduction_count=dimension_reduction_count,
        geometry_collection_count=collection_count,
        metadata=_metadata(lineage, stats, warnings),
    )


def buffer_features(
    contract: BufferFeaturesInput, datasets: DatasetRegistry
) -> BufferFeaturesOutput:
    loaded = datasets.read(contract.dataset.id)
    _crs, unit_factor = _distance_unit_factor(loaded.ref.crs)
    geometries = _valid_geometries(loaded.records)
    native_distance = contract.distance_m / unit_factor
    output_features: list[dict[str, Any]] = []
    for index, (feature, geometry) in enumerate(zip(loaded.records, geometries)):
        buffered = geometry.buffer(native_distance, quad_segs=contract.quad_segs)
        if buffered.is_empty or not buffered.is_valid:
            raise AnalysisToolError(
                "ANALYSIS_TOOL_GEOMETRY_INVALID",
                f"Buffer result is empty or invalid for {_feature_id(feature, index)}",
            )
        output_feature = deepcopy(feature)
        output_feature["geometry"] = _mapped_geometry(buffered)
        output_features.append(output_feature)
    parameters = {
        "distance_m": contract.distance_m,
        "quad_segs": contract.quad_segs,
        "output_ttl_seconds": contract.output_ttl_seconds,
    }
    output, lineage = _register_output(
        datasets,
        operation="buffer_features",
        parents=[loaded.ref],
        parameters=parameters,
        features=output_features,
        crs=loaded.ref.crs,
        ttl_seconds=contract.output_ttl_seconds,
    )
    warnings = ["dataset_empty"] if not loaded.records else []
    stats = {
        "input_feature_count": len(loaded.records),
        "buffered_feature_count": len(output_features),
        "distance_m": contract.distance_m,
    }
    return BufferFeaturesOutput(
        dataset=output,
        buffered_feature_count=len(output_features),
        distance_m=contract.distance_m,
        metadata=_metadata(lineage, stats, warnings),
    )


def _pair_properties(
    left_feature: dict[str, Any],
    left_index: int,
    right_feature: dict[str, Any],
    right_index: int,
    *,
    left_id_key: str,
    right_id_key: str,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    properties: dict[str, Any] = {
        left_id_key: _feature_id(left_feature, left_index),
        right_id_key: _feature_id(right_feature, right_index),
    }
    if extra:
        properties.update(extra)
    for prefix, feature in (("left_", left_feature), ("right_", right_feature)):
        for key, value in feature["properties"].items():
            output_key = f"{prefix}{key}"
            if output_key in properties:
                raise AnalysisToolError(
                    "ANALYSIS_TOOL_FIELD_TYPE_INVALID",
                    f"Namespaced output field collides with reserved field: {output_key}",
                )
            properties[output_key] = value
    return properties


def nearest_features(
    contract: NearestFeaturesInput, datasets: DatasetRegistry
) -> NearestFeaturesOutput:
    source = datasets.read(contract.dataset.id)
    target = datasets.read(contract.target_dataset.id)
    _require_equivalent_crs(source.ref, target.ref)
    _crs, unit_factor = _distance_unit_factor(source.ref.crs)
    source_geometries = _valid_geometries(source.records)
    target_geometries = _valid_geometries(target.records)
    output_features: list[dict[str, Any]] = []
    for source_index, (source_feature, source_geometry) in enumerate(
        zip(source.records, source_geometries)
    ):
        candidates = []
        for target_index, (target_feature, target_geometry) in enumerate(
            zip(target.records, target_geometries)
        ):
            distance_m = float(source_geometry.distance(target_geometry)) * unit_factor
            if not math.isfinite(distance_m):
                raise AnalysisToolError(
                    "ANALYSIS_TOOL_GEOMETRY_INVALID", "Nearest distance is non-finite"
                )
            candidates.append(
                (distance_m, _feature_id(target_feature, target_index), target_index, target_feature)
            )
        candidates.sort(key=lambda item: (item[0], item[1], item[2]))
        for distance_m, _target_id, target_index, target_feature in candidates[: contract.k]:
            properties = _pair_properties(
                source_feature,
                source_index,
                target_feature,
                target_index,
                left_id_key="source_feature_id",
                right_id_key="target_feature_id",
                extra={"distance_m": distance_m},
            )
            output_features.append(
                {
                    "type": "Feature",
                    "id": f"nearest-{source_index}-{target_index}",
                    "properties": properties,
                    "geometry": deepcopy(source_feature["geometry"]),
                }
            )
    parameters = {"k": contract.k, "output_ttl_seconds": contract.output_ttl_seconds}
    output, lineage = _register_output(
        datasets,
        operation="nearest_features",
        parents=[source.ref, target.ref],
        parameters=parameters,
        features=output_features,
        crs=source.ref.crs,
        ttl_seconds=contract.output_ttl_seconds,
    )
    warnings = []
    if not source.records:
        warnings.append("source_dataset_empty")
    if not target.records:
        warnings.append("target_dataset_empty")
    stats = {
        "source_feature_count": len(source.records),
        "target_feature_count": len(target.records),
        "matched_pair_count": len(output_features),
    }
    return NearestFeaturesOutput(
        dataset=output,
        source_feature_count=len(source.records),
        target_feature_count=len(target.records),
        matched_pair_count=len(output_features),
        metadata=_metadata(lineage, stats, warnings),
    )


def features_within(
    contract: FeaturesWithinInput, datasets: DatasetRegistry
) -> FeaturesWithinOutput:
    source = datasets.read(contract.dataset.id)
    containers = datasets.read(contract.container_dataset.id)
    _require_equivalent_crs(source.ref, containers.ref)
    source_geometries = _valid_geometries(source.records)
    container_geometries = _valid_geometries(containers.records)
    output_features = [
        deepcopy(feature)
        for feature, geometry in zip(source.records, source_geometries)
        if any(geometry.within(container) for container in container_geometries)
    ]
    parameters = {"predicate": "within", "output_ttl_seconds": contract.output_ttl_seconds}
    output, lineage = _register_output(
        datasets,
        operation="features_within",
        parents=[source.ref, containers.ref],
        parameters=parameters,
        features=output_features,
        crs=source.ref.crs,
        ttl_seconds=contract.output_ttl_seconds,
    )
    warnings = []
    if not source.records:
        warnings.append("source_dataset_empty")
    if not containers.records:
        warnings.append("container_dataset_empty")
    if source.records and not output_features:
        warnings.append("result_empty")
    stats = {
        "input_feature_count": len(source.records),
        "container_feature_count": len(containers.records),
        "matched_feature_count": len(output_features),
    }
    return FeaturesWithinOutput(
        dataset=output,
        input_feature_count=len(source.records),
        container_feature_count=len(containers.records),
        matched_feature_count=len(output_features),
        metadata=_metadata(lineage, stats, warnings),
    )


def spatial_join(
    contract: SpatialJoinInput, datasets: DatasetRegistry
) -> SpatialJoinOutput:
    left = datasets.read(contract.dataset.id)
    right = datasets.read(contract.right_dataset.id)
    _require_equivalent_crs(left.ref, right.ref)
    left_geometries = _valid_geometries(left.records)
    right_geometries = _valid_geometries(right.records)
    output_features: list[dict[str, Any]] = []
    for left_index, (left_feature, left_geometry) in enumerate(
        zip(left.records, left_geometries)
    ):
        for right_index, (right_feature, right_geometry) in enumerate(
            zip(right.records, right_geometries)
        ):
            if not getattr(left_geometry, contract.predicate)(right_geometry):
                continue
            output_features.append(
                {
                    "type": "Feature",
                    "id": f"join-{left_index}-{right_index}",
                    "properties": _pair_properties(
                        left_feature,
                        left_index,
                        right_feature,
                        right_index,
                        left_id_key="left_feature_id",
                        right_id_key="right_feature_id",
                    ),
                    "geometry": deepcopy(left_feature["geometry"]),
                }
            )
    parameters = {
        "predicate": contract.predicate,
        "join_type": "inner",
        "output_ttl_seconds": contract.output_ttl_seconds,
    }
    output, lineage = _register_output(
        datasets,
        operation="spatial_join",
        parents=[left.ref, right.ref],
        parameters=parameters,
        features=output_features,
        crs=left.ref.crs,
        ttl_seconds=contract.output_ttl_seconds,
    )
    warnings = []
    if not left.records:
        warnings.append("left_dataset_empty")
    if not right.records:
        warnings.append("right_dataset_empty")
    if left.records and right.records and not output_features:
        warnings.append("result_empty")
    stats = {
        "left_feature_count": len(left.records),
        "right_feature_count": len(right.records),
        "matched_pair_count": len(output_features),
    }
    return SpatialJoinOutput(
        dataset=output,
        left_feature_count=len(left.records),
        right_feature_count=len(right.records),
        matched_pair_count=len(output_features),
        predicate=contract.predicate,
        metadata=_metadata(lineage, stats, warnings),
    )


def _group_key(value: Any, *, field: str) -> tuple[str, str]:
    if value is None or isinstance(value, (bool, list, dict)):
        raise AnalysisToolError(
            "ANALYSIS_TOOL_FIELD_TYPE_INVALID",
            f"Region key {field} must be a non-null string or finite number",
        )
    if isinstance(value, float) and not math.isfinite(value):
        raise AnalysisToolError(
            "ANALYSIS_TOOL_FIELD_TYPE_INVALID", f"Region key {field} must be finite"
        )
    if not isinstance(value, (str, int, float)):
        raise AnalysisToolError(
            "ANALYSIS_TOOL_FIELD_TYPE_INVALID", f"Region key {field} has an unsupported type"
        )
    return type(value).__name__, json.dumps(value, ensure_ascii=False, sort_keys=True)


def _aggregate_metric(metric: AggregationMetric, rows: list[dict[str, Any]]) -> Any:
    if metric.operation == "count":
        return len(rows)
    values = [row.get(metric.field) for row in rows if row.get(metric.field) is not None]
    if any(not _is_number(value) for value in values):
        raise AnalysisToolError(
            "ANALYSIS_TOOL_FIELD_TYPE_INVALID",
            f"{metric.operation} requires numeric non-boolean values in {metric.field}",
        )
    if not values:
        return None
    if metric.operation == "sum":
        return sum(values)
    if metric.operation == "mean":
        return sum(values) / len(values)
    if metric.operation == "min":
        return min(values)
    if metric.operation == "max":
        return max(values)
    raise AssertionError(f"Unhandled metric: {metric.operation}")


def aggregate_by_region(
    contract: AggregateByRegionInput, datasets: DatasetRegistry
) -> AggregateByRegionOutput:
    regions = datasets.read(contract.dataset.id)
    values = datasets.read(contract.values_dataset.id)
    _require_equivalent_crs(regions.ref, values.ref)
    output_fields = [item.output_field for item in contract.metrics]
    if regions.records:
        _require_fields(regions.ref, [contract.region_id_field])
        collisions = sorted(set(output_fields).intersection(_field_names(regions.ref)))
        if collisions:
            raise AnalysisToolError(
                "ANALYSIS_TOOL_FIELD_TYPE_INVALID",
                f"Generated aggregation fields collide with region fields: {collisions}",
            )
    if values.records:
        required_value_fields = [contract.values_region_id_field]
        required_value_fields.extend(
            item.field for item in contract.metrics if item.field is not None
        )
        _require_fields(values.ref, required_value_fields)
        for metric in contract.metrics:
            if metric.field is None:
                continue
            declared = next(item.type for item in values.ref.fields if item.name == metric.field)
            if declared not in {"integer", "number", "null"}:
                raise AnalysisToolError(
                    "ANALYSIS_TOOL_FIELD_TYPE_INVALID",
                    f"{metric.operation} requires a numeric field: {metric.field}",
                )
    region_keys: dict[tuple[str, str], int] = {}
    for index, feature in enumerate(regions.records):
        key = _group_key(
            feature["properties"].get(contract.region_id_field),
            field=contract.region_id_field,
        )
        if key in region_keys:
            raise AnalysisToolError(
                "ANALYSIS_TOOL_FIELD_TYPE_INVALID",
                f"Region key values must be unique: {contract.region_id_field}",
            )
        region_keys[key] = index
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = {
        key: [] for key in region_keys
    }
    unmatched = 0
    for feature in values.records:
        raw_key = feature["properties"].get(contract.values_region_id_field)
        try:
            key = _group_key(raw_key, field=contract.values_region_id_field)
        except AnalysisToolError:
            unmatched += 1
            continue
        if key not in grouped:
            unmatched += 1
            continue
        grouped[key].append(feature["properties"])
    output_features: list[dict[str, Any]] = []
    matched = 0
    for feature in regions.records:
        key = _group_key(
            feature["properties"].get(contract.region_id_field),
            field=contract.region_id_field,
        )
        rows = grouped[key]
        matched += len(rows)
        output_feature = deepcopy(feature)
        for metric in contract.metrics:
            output_feature["properties"][metric.output_field] = _aggregate_metric(metric, rows)
        output_features.append(output_feature)
    parameters = {
        "region_id_field": contract.region_id_field,
        "values_region_id_field": contract.values_region_id_field,
        "metrics": [item.model_dump(mode="json") for item in contract.metrics],
        "output_ttl_seconds": contract.output_ttl_seconds,
    }
    output, lineage = _register_output(
        datasets,
        operation="aggregate_by_region",
        parents=[regions.ref, values.ref],
        parameters=parameters,
        features=output_features,
        crs=regions.ref.crs,
        ttl_seconds=contract.output_ttl_seconds,
    )
    warnings = []
    if not regions.records:
        warnings.append("region_dataset_empty")
    if not values.records:
        warnings.append("values_dataset_empty")
    if unmatched:
        warnings.append("unmatched_value_rows")
    stats = {
        "region_count": len(regions.records),
        "value_row_count": len(values.records),
        "matched_value_row_count": matched,
        "unmatched_value_row_count": unmatched,
    }
    return AggregateByRegionOutput(
        dataset=output,
        region_count=len(regions.records),
        value_row_count=len(values.records),
        matched_value_row_count=matched,
        unmatched_value_row_count=unmatched,
        output_fields=output_fields,
        metadata=_metadata(lineage, stats, warnings),
    )


def build_analysis_tool_registry(
    *,
    max_input_features: int = DEFAULT_MAX_INPUT_FEATURES,
    max_input_bytes: int = DEFAULT_MAX_INPUT_BYTES,
    max_output_bytes: int = DEFAULT_MAX_OUTPUT_BYTES,
) -> AnalysisToolRegistry:
    limits = ToolLimits(
        max_input_features=max_input_features,
        max_input_bytes=max_input_bytes,
        max_output_bytes=max_output_bytes,
    )
    quality = build_quality_tool_registry(
        max_input_features=max_input_features,
        max_input_bytes=max_input_bytes,
        max_output_bytes=max_output_bytes,
    )
    registry = AnalysisToolRegistry()
    for spec in quality.describe():
        registry.register(quality.get(spec.name))
    definitions = [
        ("filter_attributes", "Filter vector features by strict attribute conditions", FilterAttributesInput, FilterAttributesOutput, filter_attributes),
        ("reproject_dataset", "Reproject GeoJSON coordinates with pyproj and explicit axis order", ReprojectDatasetInput, ReprojectDatasetOutput, reproject_dataset),
        ("repair_geometry", "Explicitly repair invalid geometries with Shapely make_valid", RepairGeometryInput, RepairGeometryOutput, repair_geometry),
        ("buffer_features", "Buffer valid features by a meter distance in a projected CRS", BufferFeaturesInput, BufferFeaturesOutput, buffer_features),
        ("nearest_features", "Return stable per-source nearest target matches with meter distances", NearestFeaturesInput, NearestFeaturesOutput, nearest_features),
        ("features_within", "Keep unique left features strictly within any container geometry", FeaturesWithinInput, FeaturesWithinOutput, features_within),
        ("spatial_join", "Inner spatial join with namespaced attributes and left geometry", SpatialJoinInput, SpatialJoinOutput, spatial_join),
        ("aggregate_by_region", "Aggregate joined value rows by explicit region keys while preserving regions", AggregateByRegionInput, AggregateByRegionOutput, aggregate_by_region),
    ]
    for name, description, input_model, output_model, handler in definitions:
        registry.register(
            ToolRegistration(
                spec=ToolSpec(
                    name=name,
                    description=description,
                    input_contract=f"{input_model.__name__}.v1",
                    output_contract=f"{output_model.__name__}.v1",
                    supported_dataset_kinds=["vector"],
                    produces_dataset=True,
                    mutates_input=False,
                    deterministic=True,
                    limits=limits,
                ),
                input_model=input_model,
                output_model=output_model,
                handler=handler,
            )
        )
    return registry


ANALYSIS_TOOL_REGISTRY = build_analysis_tool_registry()
