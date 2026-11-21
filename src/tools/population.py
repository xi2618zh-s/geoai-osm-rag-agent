"""SCB raw population-grid validation and stable field normalization."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import tempfile
from typing import Any

from pyproj import CRS
from pyproj.exceptions import CRSError, ProjError
from shapely.geometry import shape

from src.datasets import DatasetLineage, DatasetRef, DatasetRegistry

from .contracts import AnalysisToolMetadata
from .population_contracts import (
    LoadPopulationDatasetInput,
    LoadPopulationDatasetOutput,
)
from .registry import AnalysisToolError


SCB_LAYER = "stat:befolkning_1km_2025"
SCB_CRS = "EPSG:3006"
FIELD_MAPPING_VERSION = "scb-population-grid-2025-v1"
ADAPTER_VERSION = "1.0"
SUPPRESSION_TOKENS = {"", "-", ":", "..", "..."}
OPTIONAL_POPULATION_FIELDS = {
    "man": "population_male",
    "kvinna": "population_female",
    "ald0_4": "population_age_0_4",
    "ald5_9": "population_age_5_9",
    "ald10_14": "population_age_10_14",
    "ald15_19": "population_age_15_19",
    "ald20_24": "population_age_20_24",
    "ald25_29": "population_age_25_29",
    "ald30_34": "population_age_30_34",
    "ald35_39": "population_age_35_39",
    "ald40_44": "population_age_40_44",
    "ald45_49": "population_age_45_49",
    "ald50_54": "population_age_50_54",
    "ald55_59": "population_age_55_59",
    "ald60_64": "population_age_60_64",
    "ald65_69": "population_age_65_69",
    "ald70_74": "population_age_70_74",
    "ald75_79": "population_age_75_79",
    "ald80_84": "population_age_80_84",
    "ald85_89": "population_age_85_89",
    "ald90_94": "population_age_90_94",
    "ald95_99": "population_age_95_99",
    "ald100w": "population_age_100_plus",
}


def _parameters_hash(parameters: dict[str, Any]) -> str:
    payload = json.dumps(
        parameters,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(payload).hexdigest()}"


def _register_output(
    datasets: DatasetRegistry,
    *,
    parent: DatasetRef,
    parameters: dict[str, Any],
    features: list[dict[str, Any]],
    ttl_seconds: int,
) -> tuple[DatasetRef, DatasetLineage]:
    lineage = DatasetLineage(
        operation="load_population_dataset",
        parent_ids=[parent.id],
        timestamp=datetime.now(timezone.utc),
        parameters_hash=_parameters_hash(parameters),
    )
    document = {
        "type": "FeatureCollection",
        "crs": {"type": "name", "properties": {"name": SCB_CRS}},
        "features": features,
    }
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
            "ANALYSIS_TOOL_POPULATION_VALUE_INVALID",
            f"Normalized population output is not serializable: {error}",
        ) from error
    with tempfile.TemporaryDirectory(prefix="load_population_dataset_") as directory:
        path = Path(directory) / "result.geojson"
        path.write_text(payload, encoding="utf-8")
        output = datasets.register_file(
            path,
            kind="vector",
            crs=SCB_CRS,
            source="Statistics Sweden (SCB)",
            source_version="befolkning_1km_2025",
            temporary=True,
            ttl_seconds=ttl_seconds,
            lineage=[lineage],
        )
    return output, lineage


def _parse_crs(value: str) -> CRS:
    try:
        return CRS.from_user_input(value)
    except (CRSError, ProjError, ValueError) as error:
        raise AnalysisToolError(
            "ANALYSIS_TOOL_CRS_INVALID", f"Invalid CRS {value}: {error}"
        ) from error


def _require_expected_crs(document: dict[str, Any], dataset: DatasetRef) -> None:
    expected = _parse_crs(SCB_CRS)
    dataset_crs = _parse_crs(dataset.crs)
    if not dataset_crs.equals(expected):
        raise AnalysisToolError(
            "ANALYSIS_TOOL_CRS_MISMATCH",
            f"SCB population input must use {SCB_CRS}; explicitly call reproject_dataset first",
        )
    try:
        declared_name = document["crs"]["properties"]["name"]
    except (KeyError, TypeError) as error:
        raise AnalysisToolError(
            "ANALYSIS_TOOL_POPULATION_SCHEMA_INVALID",
            "Raw SCB GeoJSON must declare its CRS",
        ) from error
    if not isinstance(declared_name, str) or not declared_name.strip():
        raise AnalysisToolError(
            "ANALYSIS_TOOL_POPULATION_SCHEMA_INVALID",
            "Raw SCB GeoJSON CRS declaration must be a string",
        )
    declared = _parse_crs(declared_name)
    if not declared.equals(expected) or not declared.equals(dataset_crs):
        raise AnalysisToolError(
            "ANALYSIS_TOOL_CRS_MISMATCH",
            "GeoJSON CRS, DatasetRef CRS and EPSG:3006 must be equivalent",
        )


def _require_nonempty_string(properties: dict[str, Any], field: str) -> str:
    if field not in properties:
        raise AnalysisToolError(
            "ANALYSIS_TOOL_POPULATION_SCHEMA_INVALID",
            f"SCB population feature is missing required field: {field}",
        )
    value = properties[field]
    if not isinstance(value, str) or not value.strip():
        raise AnalysisToolError(
            "ANALYSIS_TOOL_POPULATION_SCHEMA_INVALID",
            f"SCB population field must be a non-empty string: {field}",
        )
    return value.strip()


def _population_value(properties: dict[str, Any], field: str) -> int | None:
    value = properties.get(field)
    if value is None or (isinstance(value, str) and value.strip() in SUPPRESSION_TOKENS):
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise AnalysisToolError(
            "ANALYSIS_TOOL_POPULATION_VALUE_INVALID",
            f"SCB population field {field} must be a non-negative integer or null",
        )
    return value


def _reference_date(properties: dict[str, Any], dataset_year: int) -> str:
    raw = _require_nonempty_string(properties, "referenstid")
    try:
        parsed = datetime.strptime(raw, "%Y%m%d").date()
    except ValueError as error:
        raise AnalysisToolError(
            "ANALYSIS_TOOL_POPULATION_VALUE_INVALID",
            f"SCB referenstid must be a valid YYYYMMDD date: {raw}",
        ) from error
    if parsed.year != dataset_year:
        raise AnalysisToolError(
            "ANALYSIS_TOOL_POPULATION_VALUE_INVALID",
            f"SCB referenstid year {parsed.year} does not match dataset_year {dataset_year}",
        )
    return parsed.isoformat()


def _validate_structure(feature: dict[str, Any], index: int) -> dict[str, Any]:
    properties = feature.get("properties")
    if not isinstance(properties, dict):
        raise AnalysisToolError(
            "ANALYSIS_TOOL_POPULATION_SCHEMA_INVALID",
            f"SCB feature {index} properties must be an object",
        )
    object_id = properties.get("objectid")
    if isinstance(object_id, bool) or not isinstance(object_id, int):
        raise AnalysisToolError(
            "ANALYSIS_TOOL_POPULATION_SCHEMA_INVALID",
            f"SCB feature {index} objectid must be an integer",
        )
    grid_size = properties.get("rutstorl")
    if isinstance(grid_size, bool) or not isinstance(grid_size, int) or grid_size != 1000:
        raise AnalysisToolError(
            "ANALYSIS_TOOL_POPULATION_SCHEMA_INVALID",
            f"SCB feature {index} rutstorl must be integer 1000",
        )
    try:
        geometry = shape(feature.get("geometry"))
    except Exception as error:
        raise AnalysisToolError(
            "ANALYSIS_TOOL_GEOMETRY_INVALID",
            f"SCB feature {index} geometry cannot be parsed: {error}",
        ) from error
    if geometry.geom_type not in {"Polygon", "MultiPolygon"} or geometry.is_empty or not geometry.is_valid:
        raise AnalysisToolError(
            "ANALYSIS_TOOL_GEOMETRY_INVALID",
            f"SCB feature {index} must have a valid non-empty Polygon or MultiPolygon",
        )
    return properties


def load_population_dataset(
    contract: LoadPopulationDatasetInput,
    datasets: DatasetRegistry,
) -> LoadPopulationDatasetOutput:
    loaded = datasets.read(contract.dataset.id)
    path = datasets.resolve_path(contract.dataset.id)
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise AnalysisToolError(
            "ANALYSIS_TOOL_POPULATION_SCHEMA_INVALID",
            f"Raw SCB GeoJSON is unreadable: {error}",
        ) from error
    if not isinstance(document, dict) or document.get("type") != "FeatureCollection":
        raise AnalysisToolError(
            "ANALYSIS_TOOL_POPULATION_SCHEMA_INVALID",
            "Raw SCB input must be a GeoJSON FeatureCollection",
        )
    _require_expected_crs(document, loaded.ref)

    output_features: list[dict[str, Any]] = []
    grid_ids: set[str] = set()
    reported_count = 0
    missing_count = 0
    components_mismatch = False
    optional_missing = False
    for index, feature in enumerate(loaded.records):
        properties = _validate_structure(feature, index)
        grid_id = _require_nonempty_string(properties, "rutid_inspire")
        source_grid_id = _require_nonempty_string(properties, "rutid_scb")
        if grid_id in grid_ids:
            raise AnalysisToolError(
                "ANALYSIS_TOOL_POPULATION_SCHEMA_INVALID",
                f"SCB grid_id values must be unique: {grid_id}",
            )
        grid_ids.add(grid_id)
        reference_date = _reference_date(properties, contract.dataset_year)
        total = _population_value(properties, "beftotalt")
        if total is None:
            status = "missing_or_suppressed"
            missing_count += 1
        else:
            status = "reported"
            reported_count += 1
        normalized_components: dict[str, int | None] = {}
        for source_field, output_field in OPTIONAL_POPULATION_FIELDS.items():
            if source_field not in properties:
                optional_missing = True
            normalized_components[output_field] = _population_value(
                properties, source_field
            )
        male = normalized_components["population_male"]
        female = normalized_components["population_female"]
        age_values = [
            value
            for key, value in normalized_components.items()
            if key.startswith("population_age_")
        ]
        if total is not None:
            if male is not None and female is not None and male + female != total:
                components_mismatch = True
            if all(value is not None for value in age_values) and sum(age_values) != total:
                components_mismatch = True
        output_properties: dict[str, Any] = {
            "grid_id": grid_id,
            "source_grid_id_scb": source_grid_id,
            "population_reference_date": reference_date,
            "population_total": total,
            "population_status": status,
        }
        output_properties.update(normalized_components)
        output_features.append(
            {
                "type": "Feature",
                "id": grid_id,
                "properties": output_properties,
                "geometry": deepcopy(feature["geometry"]),
            }
        )

    parameters = {
        "provider": contract.provider,
        "layer": SCB_LAYER,
        "dataset_year": contract.dataset_year,
        "field_mapping_version": FIELD_MAPPING_VERSION,
        "adapter_version": ADAPTER_VERSION,
        "output_ttl_seconds": contract.output_ttl_seconds,
    }
    output, lineage = _register_output(
        datasets,
        parent=loaded.ref,
        parameters=parameters,
        features=output_features,
        ttl_seconds=contract.output_ttl_seconds,
    )
    warnings = ["population_values_are_disclosure_controlled"]
    if not loaded.records:
        warnings.append("population_dataset_empty")
    if missing_count:
        warnings.append("population_values_missing_or_suppressed")
    if optional_missing:
        warnings.append("population_optional_fields_missing")
    if components_mismatch:
        warnings.append("population_components_may_not_sum_to_total")
    metadata = AnalysisToolMetadata(
        lineage=lineage,
        statistics={
            "input_feature_count": len(loaded.records),
            "normalized_feature_count": len(output_features),
            "reported_count": reported_count,
            "missing_or_suppressed_count": missing_count,
        },
        warnings=warnings,
    )
    return LoadPopulationDatasetOutput(
        dataset=output,
        feature_count=len(output_features),
        reported_count=reported_count,
        missing_or_suppressed_count=missing_count,
        metadata=metadata,
    )
