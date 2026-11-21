"""M04a read-only dataset schema and geometry quality tools."""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import hashlib
import json

from shapely.geometry import shape
from shapely.validation import explain_validity

from src.datasets import DatasetLineage, DatasetRegistry

from .contracts import (
    AnalysisToolMetadata,
    GeometryIssue,
    InspectDatasetSchemaInput,
    InspectDatasetSchemaOutput,
    ToolLimits,
    ToolSpec,
    ValidateGeometryInput,
    ValidateGeometryOutput,
)
from .registry import AnalysisToolRegistry, ToolRegistration


DEFAULT_MAX_INPUT_FEATURES = 10_000
DEFAULT_MAX_INPUT_BYTES = 50_000_000
DEFAULT_MAX_OUTPUT_BYTES = 5_000_000


def _parameters_hash(parameters: dict) -> str:
    payload = json.dumps(
        parameters, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(payload).hexdigest()}"


def _metadata(
    operation: str,
    dataset_id: str,
    parameters: dict,
    statistics: dict,
    warnings: list[str],
) -> AnalysisToolMetadata:
    return AnalysisToolMetadata(
        lineage=DatasetLineage(
            operation=operation,
            parent_ids=[dataset_id],
            timestamp=datetime.now(timezone.utc),
            parameters_hash=_parameters_hash(parameters),
        ),
        statistics=statistics,
        warnings=warnings,
    )


def inspect_dataset_schema(
    contract: InspectDatasetSchemaInput, datasets: DatasetRegistry
) -> InspectDatasetSchemaOutput:
    ref = datasets.get(contract.dataset.id, verify=True)
    warnings = ["dataset_empty"] if ref.row_count == 0 else []
    return InspectDatasetSchemaOutput(
        dataset=ref,
        kind=ref.kind,
        format=ref.format,
        crs=ref.crs,
        bbox=ref.bbox,
        row_count=ref.row_count,
        fields=ref.fields,
        metadata=_metadata(
            "inspect_dataset_schema",
            ref.id,
            {},
            {"row_count": ref.row_count, "field_count": len(ref.fields)},
            warnings,
        ),
    )


def _feature_id(feature: dict, index: int) -> str:
    candidate = feature.get("id")
    if candidate is None and isinstance(feature.get("properties"), dict):
        candidate = feature["properties"].get("id")
    return str(candidate) if candidate is not None else f"feature-{index}"


def validate_geometry(
    contract: ValidateGeometryInput, datasets: DatasetRegistry
) -> ValidateGeometryOutput:
    loaded = datasets.read(contract.dataset.id)
    type_counts: Counter[str] = Counter()
    issues: list[GeometryIssue] = []
    valid_count = 0
    invalid_count = 0
    empty_count = 0
    total_issue_count = 0

    for index, feature in enumerate(loaded.records):
        raw_geometry = feature.get("geometry")
        geometry_type = (
            str(raw_geometry.get("type")) if isinstance(raw_geometry, dict) else None
        )
        reason = None
        try:
            geometry = shape(raw_geometry)
            geometry_type = geometry.geom_type
            type_counts[geometry_type] += 1
            if geometry.is_empty:
                empty_count += 1
                reason = "Empty geometry"
            elif not geometry.is_valid:
                invalid_count += 1
                reason = explain_validity(geometry)
            else:
                valid_count += 1
        except Exception as error:
            invalid_count += 1
            reason = f"Geometry parse failed: {error}"
            if geometry_type:
                type_counts[geometry_type] += 1
        if reason is not None:
            total_issue_count += 1
            if len(issues) < contract.max_issues:
                issues.append(
                    GeometryIssue(
                        feature_index=index,
                        feature_id=_feature_id(feature, index),
                        geometry_type=geometry_type,
                        reason=reason,
                    )
                )

    warnings = []
    if not loaded.records:
        warnings.append("dataset_empty")
    if total_issue_count > len(issues):
        warnings.append("issues_truncated")
    stats = {
        "feature_count": len(loaded.records),
        "valid_geometry_count": valid_count,
        "invalid_geometry_count": invalid_count,
        "empty_geometry_count": empty_count,
        "reported_issue_count": len(issues),
    }
    return ValidateGeometryOutput(
        dataset=loaded.ref,
        is_valid=invalid_count == 0 and empty_count == 0,
        valid_geometry_count=valid_count,
        invalid_geometry_count=invalid_count,
        empty_geometry_count=empty_count,
        geometry_type_counts=dict(sorted(type_counts.items())),
        issues=issues,
        metadata=_metadata(
            "validate_geometry",
            loaded.ref.id,
            {"max_issues": contract.max_issues},
            stats,
            warnings,
        ),
    )


def build_quality_tool_registry(
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
    registry = AnalysisToolRegistry()
    registry.register(
        ToolRegistration(
            spec=ToolSpec(
                name="inspect_dataset_schema",
                description="Inspect verified DatasetRef metadata without reading or mutating source data",
                input_contract="InspectDatasetSchemaInput.v1",
                output_contract="InspectDatasetSchemaOutput.v1",
                supported_dataset_kinds=["vector", "table", "raster", "network"],
                produces_dataset=False,
                mutates_input=False,
                deterministic=True,
                limits=limits,
            ),
            input_model=InspectDatasetSchemaInput,
            output_model=InspectDatasetSchemaOutput,
            handler=inspect_dataset_schema,
        )
    )
    registry.register(
        ToolRegistration(
            spec=ToolSpec(
                name="validate_geometry",
                description="Validate GeoJSON geometry topology with Shapely without modifying input",
                input_contract="ValidateGeometryInput.v1",
                output_contract="ValidateGeometryOutput.v1",
                supported_dataset_kinds=["vector"],
                produces_dataset=False,
                mutates_input=False,
                deterministic=True,
                limits=limits,
            ),
            input_model=ValidateGeometryInput,
            output_model=ValidateGeometryOutput,
            handler=validate_geometry,
        )
    )
    return registry


QUALITY_TOOL_REGISTRY = build_quality_tool_registry()
