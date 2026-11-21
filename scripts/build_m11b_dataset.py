"""Build and byte-audit the public M11b dev reference set with real M04 tools."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile

from shapely.geometry import GeometryCollection, mapping, shape
from shapely.ops import unary_union

from src.datasets import DatasetRef, LocalDatasetRegistry
from src.evaluation.m11b import M11bTask
from src.tools import (
    AggregateByRegionInput, AggregationMetric, AttributeFilterCondition,
    BufferFeaturesInput, FeaturesWithinInput, FilterAttributesInput,
    NearestFeaturesInput, SpatialJoinInput, build_analysis_tool_registry,
)

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "data/fixtures/tools/m04b/v1"
SEED = 1107
VERSION = "m11b-public-dev-v1"
TOOLS = build_analysis_tool_registry()


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _json_bytes(data: object) -> bytes:
    return (json.dumps(data, ensure_ascii=False, sort_keys=True,
                       separators=(",", ":"), allow_nan=False) + "\n").encode("utf-8")


def _source_registry(registry: LocalDatasetRegistry, manifest: dict):
    refs = {}
    for name, item in sorted(manifest["fixtures"].items()):
        raw = (FIXTURES / name).read_bytes()
        if _sha(raw) != item["sha256"]:
            raise ValueError(f"M04 fixture hash mismatch: {name}")
        refs[name] = registry.register_file(FIXTURES / name, kind="vector",
                                            crs=item["crs"], source="m04b-fixture",
                                            source_version="m04b-v1")
    return refs


def _reference(registry, result):
    records = list(registry.read(result.dataset.id).records)
    geometries = [shape(item["geometry"]) for item in records]
    combined = unary_union(geometries) if geometries else GeometryCollection()
    ids = [str(item["id"]) for item in records]
    if len(ids) != len(set(ids)):
        raise ValueError("M04 output has duplicate Feature IDs")
    numeric = {"count": {"value": float(len(records)), "absolute_tolerance": 0.0,
                         "relative_tolerance": 0.0}}
    if combined.area > 0:
        numeric["area_m2"] = {"value": float(combined.area),
                              "absolute_tolerance": 1e-6, "relative_tolerance": 1e-6}
    distances = [item.get("properties", {}).get("distance_m") for item in records]
    distances = [float(value) for value in distances if value is not None]
    if distances:
        numeric["distance_m"] = {"value": min(distances),
                                  "absolute_tolerance": 1e-6, "relative_tolerance": 1e-6}
    return {
        "status": "success", "exact_fields": {"crs": result.dataset.crs,
            "row_count": len(records)}, "required_steps": ["load_fixture", "execute_m04",
            "validate_result"], "feature_ids": ids, "numeric": numeric,
        "geometry": mapping(combined), "crs": result.dataset.crs,
    }


def _tasks_bytes(trace_sink: list[dict] | None = None):
    fixture_manifest = json.loads((FIXTURES / "manifest.json").read_text("utf-8"))
    rows = []
    calls: list[dict] = []
    with tempfile.TemporaryDirectory(prefix="m11b_reference_") as temp:
        registry = LocalDatasetRegistry(Path(temp) / "datasets")
        refs = _source_registry(registry, fixture_manifest)
        source = refs["projected_sources.geojson"]
        target = refs["projected_targets.geojson"]
        regions = refs["projected_regions.geojson"]
        values = refs["aggregate_values.geojson"]
        def invoke(name, contract):
            result = TOOLS.invoke(name, contract, datasets=registry)
            if trace_sink is not None:
                inputs = [getattr(contract, field).content_hash
                          for field in contract.__class__.model_fields
                          if isinstance(getattr(contract, field), DatasetRef)]
                calls.append({"name": name, "tool_version": TOOLS.get(name).spec.version,
                              "input_hashes": inputs,
                              "parameters_hash": result.dataset.lineage[-1].parameters_hash,
                              "output_hash": result.dataset.content_hash,
                              "output_count": result.dataset.row_count})
            return result
        def add(op, category, query, index, result, names, fixtures):
            number = len(rows) + 1
            expected = _reference(registry, result)
            expected["tool_sequence"] = names
            task = M11bTask(task_id=f"m11b_dev_{number:03d}", task_type=op,
                category=category, difficulty=("simple" if len(names) == 1 else "medium"),
                capability="supported", query=query, random_seed=SEED + number,
                source_fixtures=fixtures, expectation=expected)
            rows.append(task)
            if trace_sink is not None:
                recent = calls[-len(names):]
                if [item["name"] for item in recent] != names:
                    raise ValueError("M04 actual call trace mismatch")
                trace_sink.append({"task_id": task.task_id, "calls": recent})
        for index in range(24):
            category = ("clinic", "school")[index // 12]
            comparator = ("gte", "lte")[index % 12 // 6]
            threshold = (0, 5, 10, 15, 20, 25)[index % 6]
            result = invoke("filter_attributes", FilterAttributesInput(dataset=source,
                conditions=[AttributeFilterCondition(field="category", operator="eq", value=category),
                            AttributeFilterCondition(field="score", operator=comparator, value=threshold)]))
            add("search", "attribute_search",
                f"Search synthetic sources: category {category}, score {comparator} {threshold}",
                index, result, ["filter_attributes"], ["projected_sources.geojson"])
        for index in range(24):
            distance = float(index + 1)
            result = invoke("buffer_features", BufferFeaturesInput(dataset=source,
                distance_m=distance, quad_segs=8))
            add("within_distance", "fixed_distance_buffer",
                f"Make a fixed {distance:g} metre buffer of synthetic sources",
                index, result, ["buffer_features"], ["projected_sources.geojson"])
        for index in range(24):
            threshold = index // 3
            k = index % 3 + 1
            filtered = invoke("filter_attributes", FilterAttributesInput(dataset=source,
                conditions=[AttributeFilterCondition(field="score", operator="gte", value=threshold)]))
            result = invoke("nearest_features", NearestFeaturesInput(dataset=filtered.dataset,
                target_dataset=target, k=k))
            add("nearest", "nearest_pairs",
                f"Find {k} nearest synthetic targets for sources with score at least {threshold}",
                index, result, ["filter_attributes", "nearest_features"],
                ["projected_sources.geojson", "projected_targets.geojson"])
        for index in range(24):
            threshold = index
            filtered = invoke("filter_attributes", FilterAttributesInput(dataset=source,
                conditions=[AttributeFilterCondition(field="score", operator="gte", value=threshold)]))
            result = invoke("features_within", FeaturesWithinInput(dataset=filtered.dataset,
                container_dataset=regions))
            add("within", "strict_within",
                f"Find synthetic sources with score at least {threshold} strictly within regions",
                index, result, ["filter_attributes", "features_within"],
                ["projected_sources.geojson", "projected_regions.geojson"])
        for index in range(24):
            predicate = ("intersects", "within", "contains")[index // 8]
            threshold = index % 8
            filtered = invoke("filter_attributes", FilterAttributesInput(dataset=source,
                conditions=[AttributeFilterCondition(field="score", operator="gte", value=threshold)]))
            result = invoke("spatial_join", SpatialJoinInput(dataset=regions,
                right_dataset=filtered.dataset, predicate=predicate))
            add("spatial_join", "region_source_join",
                f"Join synthetic regions to sources with score at least {threshold} by {predicate}",
                index, result, ["filter_attributes", "spatial_join"],
                ["projected_regions.geojson", "projected_sources.geojson"])
        for index in range(24):
            operation = ("count", "sum", "mean", "min")[index // 6]
            threshold = (0, 5, 10, 15, 20, 25)[index % 6]
            filtered = invoke("filter_attributes", FilterAttributesInput(dataset=values,
                conditions=[AttributeFilterCondition(field="value", operator="gte", value=threshold)]))
            metric = AggregationMetric(operation=operation,
                field=None if operation == "count" else "value")
            result = invoke("aggregate_by_region", AggregateByRegionInput(
                dataset=regions, values_dataset=filtered.dataset,
                region_id_field="region_id", values_region_id_field="joined_region_id",
                metrics=[metric]))
            add("aggregate_by_region", "region_aggregate",
                f"Aggregate synthetic region values by {operation} after value at least {threshold}",
                index, result, ["filter_attributes", "aggregate_by_region"],
                ["projected_regions.geojson", "aggregate_values.geojson"])
    for index in range(8):
        number = len(rows) + 1
        rows.append(M11bTask(task_id=f"m11b_dev_{number:03d}", task_type="rank",
            category="unsupported_rank", difficulty="hard", capability="unsupported",
            query=f"Rank the synthetic regions by unsupported criterion {index + 1}",
            random_seed=SEED + number,
            expectation={"status": "error", "reason_code": "unsupported_rank",
                         "required_steps": ["reject_unsupported"]}))
    for index in range(8):
        number = len(rows) + 1
        rows.append(M11bTask(task_id=f"m11b_dev_{number:03d}", task_type="search",
            category="missing_place", difficulty="simple", capability="clarification",
            query=f"Find synthetic category {index + 1} without specifying a place",
            random_seed=SEED + number,
            expectation={"status": "clarification", "reason_code": "missing_place",
                         "required_steps": ["clarify"]}))
    if trace_sink is not None:
        trace_sink.extend({"task_id": item.task_id, "calls": []} for item in rows[144:])
    if len(rows) != 160:
        raise AssertionError(len(rows))
    task_data = b"".join(_json_bytes(row.model_dump(mode="json")) for row in rows)
    spec_versions = {item.name: item.version for item in TOOLS.describe()
                     if item.name in {name for row in rows for name in row.expectation.tool_sequence}}
    manifest = {
        "schema_version": "1.0", "data_version": VERSION, "seed": SEED,
        "task_count": len(rows), "supported_count": 144, "split": "dev",
        "source": "Project-authored synthetic M04b fixture and deterministic M04 tool outputs",
        "license": "CC0-1.0", "builder": "python -m scripts.build_m11b_dataset",
        "fixture_manifest_sha256": _sha((FIXTURES / "manifest.json").read_bytes()),
        "fixture_sha256": {name: value["sha256"] for name, value in sorted(fixture_manifest["fixtures"].items())},
        "tool_versions": spec_versions, "tasks_sha256": _sha(task_data),
        "hidden_test_data_present": False,
    }
    return task_data, _json_bytes(manifest)


def build_dataset(output_dir: Path):
    output_dir = Path(output_dir)
    task_path = output_dir / "m11b_tasks_v1.jsonl"
    manifest_path = output_dir / "m11b_manifest_v1.json"
    if task_path.exists() or manifest_path.exists():
        raise FileExistsError("M11b release already exists")
    task_data, manifest_data = _tasks_bytes()
    output_dir.mkdir(parents=True, exist_ok=True)
    with task_path.open("xb") as stream:
        stream.write(task_data)
    with manifest_path.open("xb") as stream:
        stream.write(manifest_data)
    return task_path, manifest_path


def audit_dataset(task_path: Path, manifest_path: Path) -> bool:
    task_data, manifest_data = _tasks_bytes()
    if Path(task_path).read_bytes() != task_data or Path(manifest_path).read_bytes() != manifest_data:
        raise ValueError("M11b release differs from deterministic M04 rebuild")
    return True


if __name__ == "__main__":
    tasks, manifest = build_dataset(ROOT / "benchmarks/data/dev")
    print(f"Published {tasks} and {manifest}")

