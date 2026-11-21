"""Build, check and seal the M11c private v2 holdout in a custodian workspace.

The workspace lives outside the repository (GEOAI_M11C_PRIVATE_V2_DIR or
--workspace). It holds catalogue.json, the custodian's GeoJSON files and
task_specs.json. References come from real M04 tool calls with the M11b v2.0
encoding. Output to the console and the public receipt is limited to counts,
category distribution, check results and hashes: no names, queries or answers.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import tempfile
from typing import Any

from pyproj import CRS

from scripts.build_m11b_dataset import _reference
from src.agent.baselines import CatalogueFile, DatasetCatalogue
from src.datasets import LocalDatasetRegistry
from src.evaluation.m11b import M11bPrediction, load_m11b_tasks, score_m11b
from src.evaluation.m11c import (
    PRIVATE_V2_FAMILIES, PRIVATE_V2_VERSION, M11cPrivateTask, _role, load_m11c_private_v2_tasks,
)
from src.tools import (
    AggregateByRegionInput, AggregationMetric, AttributeFilterCondition, BufferFeaturesInput,
    FeaturesWithinInput, FilterAttributesInput, NearestFeaturesInput, SpatialJoinInput,
    build_analysis_tool_registry,
)

ROOT = Path(__file__).resolve().parents[1]
BUILDER_VERSION = "m11c-private-v2-builder-1"
RECEIPT = ROOT / "benchmarks/data/m11c/m11c_private_v2_receipt.json"
TASKS_NAME = "m11c_private_tasks_v2.jsonl"
MANIFEST_NAME = "m11c_private_manifest_v2.json"
SEAL_NAME = "SEALED.json"
PUBLIC_DEV = ROOT / "benchmarks/data/dev/m11b_tasks_v1.jsonl"
PRIVATE_V1 = ROOT / "benchmarks/data/test/private/m11b_test_tasks_v1.jsonl"
OPERATORS = {"eq", "ne", "gt", "gte", "lt", "lte"}
FILTER_ROLE = {"search": "dataset", "nearest": "source", "within": "source",
               "spatial_join": "right", "aggregate_by_region": "values"}
FILE_PARAMS = {"search": ("dataset",), "buffer": ("dataset",), "nearest": ("source", "target"),
               "within": ("source", "container"), "spatial_join": ("left", "right"),
               "aggregate_by_region": ("regions", "values"), "rank": (), "missing_place": ()}
TASK_TYPE = {"buffer": "within_distance", "missing_place": "search"}
TOOLS = build_analysis_tool_registry()


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _json_bytes(data: Any) -> bytes:
    return (json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                       allow_nan=False) + "\n").encode("utf-8")


def _norm(text: str) -> str:
    return " ".join(text.casefold().split())


def workspace_path(path: str | Path | None = None) -> Path:
    raw = path if path is not None else os.environ.get("GEOAI_M11C_PRIVATE_V2_DIR")
    if not raw:
        raise ValueError("private v2 workspace not given (GEOAI_M11C_PRIVATE_V2_DIR or --workspace)")
    resolved = Path(raw).resolve()
    if resolved == ROOT or ROOT in resolved.parents:
        raise ValueError("private v2 workspace must be outside the repository")
    return resolved


def _fail(message: str):
    raise ValueError(message)


def _conditions(task_id: str, params: dict) -> list[AttributeFilterCondition]:
    raw = params.get("conditions")
    if not isinstance(raw, list) or not raw:
        _fail(f"{task_id}: filtered family requires at least one condition")
    out = []
    for item in raw:
        if not isinstance(item, dict) or set(item) != {"field", "operator", "value"} \
                or item["operator"] not in OPERATORS or isinstance(item["value"], (list, dict, type(None))):
            _fail(f"{task_id}: invalid condition structure")
        out.append(AttributeFilterCondition(**item))
    return out


def _compile(workspace: Path, per_family: int) -> tuple[bytes, dict[str, Any], dict[str, Any]]:
    catalogue_raw = (workspace / "catalogue.json").read_bytes()
    specs_raw = (workspace / "task_specs.json").read_bytes()
    catalogue = json.loads(catalogue_raw)
    specs = json.loads(specs_raw)
    files = catalogue.get("files")
    if not isinstance(files, dict) or not files:
        _fail("catalogue.json must declare files")
    roles = Counter(_role(name) for name in files)
    if any(count > 1 for count in roles.values()):
        _fail("catalogue role names (last underscore segment of file stem) must be unique")
    file_info = {}
    for name, item in sorted(files.items()):
        path = workspace / name
        if Path(name).name != name or not path.is_file() or path.suffix != ".geojson":
            _fail("catalogue file missing or not a plain .geojson name")
        try:
            crs = CRS.from_user_input(item["crs"])
        except Exception:
            _fail("catalogue declares an invalid CRS")
        file_info[name] = {"sha256": _sha(path.read_bytes()), "crs": item["crs"],
                           "role": _role(name), "projected": crs.is_projected}
    tasks_in = specs.get("tasks")
    if not isinstance(tasks_in, list):
        _fail("task_specs.json must contain a tasks list")
    expected_ids = [f"m11b_test_{200 + i:03d}" for i in range(1, per_family * len(PRIVATE_V2_FAMILIES) + 1)]
    if sorted(t.get("task_id") for t in tasks_in) != expected_ids:
        _fail("task IDs must be exactly m11b_test_201.. in the frozen count")
    families = Counter(t.get("family") for t in tasks_in)
    if set(families) != set(PRIVATE_V2_FAMILIES) or set(families.values()) != {per_family}:
        _fail("family distribution differs from the frozen protocol")
    queries = [_norm(str(t.get("query", ""))) for t in tasks_in]
    if any(not q for q in queries) or len(set(queries)) != len(queries):
        _fail("queries must be non-empty and unique")
    public = {_norm(t.query) for t in load_m11b_tasks(PUBLIC_DEV)}
    previous = {_norm(t.query) for t in load_m11b_tasks(PRIVATE_V1)} if PRIVATE_V1.is_file() else set()
    public_overlap, previous_overlap = len(public & set(queries)), len(previous & set(queries))
    if public_overlap or previous_overlap:
        _fail("query overlaps a public dev or private v1 query")
    rows, self_pass, non_empty = [], 0, 0
    with tempfile.TemporaryDirectory(prefix="m11c_private_v2_build_") as temp:
        registry = LocalDatasetRegistry(Path(temp) / "datasets")
        refs = {name: registry.register_file(workspace / name, kind="vector", crs=info["crs"],
                                             source="m11c-private-v2", source_version=PRIVATE_V2_VERSION)
                for name, info in file_info.items()}

        def invoke(name, contract):
            return TOOLS.invoke(name, contract, datasets=registry)

        for spec in sorted(tasks_in, key=lambda item: item["task_id"]):
            task_id, family, params = spec["task_id"], spec["family"], spec.get("params", {})
            if not isinstance(params, dict):
                _fail(f"{task_id}: params must be an object")
            used = [params.get(key) for key in FILE_PARAMS[family]]
            if any(name not in refs for name in used):
                _fail(f"{task_id}: params reference an undeclared catalogue file")
            if family in ("buffer", "nearest") and not all(file_info[n]["projected"] for n in used):
                _fail(f"{task_id}: buffer/nearest need projected CRS")
            base = {"task_id": task_id, "split": "test", "data_version": PRIVATE_V2_VERSION,
                    "task_type": TASK_TYPE.get(family, family), "category": f"private_v2_{family}",
                    "query": spec["query"], "random_seed": 0}
            if family == "rank":
                rows.append(M11cPrivateTask(**base, difficulty="hard", capability="unsupported",
                    expectation={"status": "error", "reason_code": "unsupported_rank",
                                 "required_steps": ["reject_unsupported"]}))
                continue
            if family == "missing_place":
                rows.append(M11cPrivateTask(**base, difficulty="simple", capability="clarification",
                    expectation={"status": "clarification", "reason_code": "missing_place",
                                 "required_steps": ["clarify"]}))
                continue
            try:
                if family == "buffer":
                    distance = params.get("distance_m")
                    if not isinstance(distance, (int, float)) or distance <= 0:
                        _fail(f"{task_id}: buffer needs positive distance_m")
                    result = invoke("buffer_features", BufferFeaturesInput(
                        dataset=refs[params["dataset"]], distance_m=float(distance), quad_segs=8))
                    names = ["buffer_features"]
                else:
                    conditions = _conditions(task_id, params)
                    filtered = invoke("filter_attributes", FilterAttributesInput(
                        dataset=refs[params[FILTER_ROLE[family]]], conditions=conditions))
                    names = ["filter_attributes"]
                    if family == "search":
                        result = filtered
                    elif family == "nearest":
                        result = invoke("nearest_features", NearestFeaturesInput(
                            dataset=filtered.dataset, target_dataset=refs[params["target"]], k=params["k"]))
                    elif family == "within":
                        result = invoke("features_within", FeaturesWithinInput(
                            dataset=filtered.dataset, container_dataset=refs[params["container"]]))
                    elif family == "spatial_join":
                        result = invoke("spatial_join", SpatialJoinInput(
                            dataset=refs[params["left"]], right_dataset=filtered.dataset,
                            predicate=params["predicate"]))
                    else:
                        result = invoke("aggregate_by_region", AggregateByRegionInput(
                            dataset=refs[params["regions"]], values_dataset=filtered.dataset,
                            region_id_field=params["region_id_field"],
                            values_region_id_field=params["values_region_id_field"],
                            metrics=[AggregationMetric(operation=params["operation"], field=params.get("field"))]))
                    if family != "search":
                        names.append({"nearest": "nearest_features", "within": "features_within",
                                      "spatial_join": "spatial_join",
                                      "aggregate_by_region": "aggregate_by_region"}[family])
            except ValueError:
                raise
            except Exception as error:
                _fail(f"{task_id}: M04 execution failed ({type(error).__name__})")
            expected = _reference(registry, result)
            expected["tool_sequence"] = names
            non_empty += int(bool(expected["feature_ids"]))
            rows.append(M11cPrivateTask(**base, difficulty="simple" if len(names) == 1 else "medium",
                                        capability="supported", source_fixtures=sorted(set(used)),
                                        expectation=expected))
    for task in rows:
        e = task.expectation
        prediction = M11bPrediction(
            task_id=task.task_id, status=e.status,
            operation=task.task_type if task.capability == "supported" else None,
            reason_code=e.reason_code, exact_fields=e.exact_fields, steps=e.required_steps,
            tool_sequence=e.tool_sequence, feature_ids=e.feature_ids,
            numeric_values={k: v.value for k, v in e.numeric.items()}, geometry=e.geometry, crs=e.crs)
        self_pass += int(score_m11b(task, prediction).passed)
    if self_pass != len(rows):
        _fail("reference self-check failed")
    task_bytes = b"".join(_json_bytes(task.model_dump(mode="json")) for task in rows)
    checks = {"task_count": len(rows), "family_distribution_ok": True, "unique_ids_queries": True,
              "public_dev_query_overlap": public_overlap, "private_v1_query_overlap": previous_overlap,
              "private_v1_available_for_overlap_check": PRIVATE_V1.is_file(),
              "reference_self_check": f"{self_pass}/{len(rows)}",
              "supported_non_empty_results": non_empty,
              "supported_tasks": sum(t.capability == "supported" for t in rows)}
    manifest = {"schema_version": "1.0", "data_version": PRIVATE_V2_VERSION, "builder_version": BUILDER_VERSION,
                "task_count": len(rows), "family_counts": dict(sorted(families.items())),
                "files": file_info, "catalogue_sha256": _sha(catalogue_raw), "specs_sha256": _sha(specs_raw),
                "tasks_sha256": _sha(task_bytes), "checks": checks,
                "license": catalogue.get("license"), "author": catalogue.get("author")}
    summary = {"data_version": PRIVATE_V2_VERSION, "builder_version": BUILDER_VERSION,
               "task_count": len(rows), "family_counts": dict(sorted(families.items())), "checks": checks,
               "tasks_sha256": manifest["tasks_sha256"], "specs_sha256": manifest["specs_sha256"],
               "catalogue_sha256": manifest["catalogue_sha256"],
               "data_files_sha256": sorted(info["sha256"] for info in file_info.values())}
    return task_bytes, manifest, summary


def build_package(workspace=None, *, per_family: int = 6) -> dict[str, Any]:
    workspace = workspace_path(workspace)
    if (workspace / SEAL_NAME).exists():
        raise FileExistsError("private v2 package is sealed; rebuilding is not allowed")
    task_bytes, manifest, summary = _compile(workspace, per_family)
    manifest_bytes = _json_bytes(manifest)
    (workspace / TASKS_NAME).write_bytes(task_bytes)
    (workspace / MANIFEST_NAME).write_bytes(manifest_bytes)
    return {**summary, "manifest_sha256": _sha(manifest_bytes)}


def seal(workspace=None, *, receipt_path: Path = RECEIPT, per_family: int = 6) -> dict[str, Any]:
    workspace = workspace_path(workspace)
    if (workspace / SEAL_NAME).exists() or Path(receipt_path).exists():
        raise FileExistsError("private v2 package or receipt already sealed")
    task_bytes, manifest, summary = _compile(workspace, per_family)
    manifest_bytes = _json_bytes(manifest)
    if (workspace / TASKS_NAME).read_bytes() != task_bytes or (workspace / MANIFEST_NAME).read_bytes() != manifest_bytes:
        raise ValueError("built package is stale; run build after the last edit, then seal")
    receipt = {**summary, "schema_version": "1.0", "manifest_sha256": _sha(manifest_bytes),
               "sealed_at": datetime.now(timezone.utc).isoformat(),
               "storage": "outside repository; workspace path supplied at run time",
               "content_published": False}
    Path(receipt_path).parent.mkdir(parents=True, exist_ok=True)
    receipt_bytes = (json.dumps(receipt, sort_keys=True, indent=2) + "\n").encode("utf-8")
    with Path(receipt_path).open("xb") as stream:
        stream.write(receipt_bytes)
    with (workspace / SEAL_NAME).open("x", encoding="utf-8") as stream:
        json.dump({"receipt_sha256": _sha(receipt_bytes), "sealed_at": receipt["sealed_at"]}, stream)
    return receipt


def verify_sealed(workspace=None, receipt_path: Path = RECEIPT):
    """Return (tasks, catalogue) only if every sealed hash still matches."""
    workspace = workspace_path(workspace)
    receipt_bytes = Path(receipt_path).read_bytes()
    receipt = json.loads(receipt_bytes)
    seal_info = json.loads((workspace / SEAL_NAME).read_text("utf-8"))
    manifest_bytes = (workspace / MANIFEST_NAME).read_bytes()
    manifest = json.loads(manifest_bytes)
    ok = (seal_info["receipt_sha256"] == _sha(receipt_bytes)
          and _sha(manifest_bytes) == receipt["manifest_sha256"]
          and _sha((workspace / TASKS_NAME).read_bytes()) == receipt["tasks_sha256"]
          and _sha((workspace / "task_specs.json").read_bytes()) == receipt["specs_sha256"]
          and _sha((workspace / "catalogue.json").read_bytes()) == receipt["catalogue_sha256"]
          and all(_sha((workspace / name).read_bytes()) == info["sha256"]
                  for name, info in manifest["files"].items()))
    if not ok:
        raise ValueError("sealed private v2 package does not match its receipt")
    files = tuple(CatalogueFile(role=info["role"], path=workspace / name, crs=info["crs"],
                                source="m11c-private-v2", source_version=PRIVATE_V2_VERSION,
                                sha256=info["sha256"]) for name, info in sorted(manifest["files"].items()))
    catalogue = DatasetCatalogue(name=PRIVATE_V2_VERSION, scope="Synthetic private catalogue", files=files)
    return load_m11c_private_v2_tasks(workspace / TASKS_NAME), catalogue


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("build", "seal", "verify"))
    parser.add_argument("--workspace")
    args = parser.parse_args()
    if args.command == "build":
        result = build_package(args.workspace)
    elif args.command == "seal":
        result = seal(args.workspace)
    else:
        tasks, catalogue = verify_sealed(args.workspace)
        result = {"verified": True, "task_count": len(tasks), "catalogue_files": len(catalogue.files)}
    print(json.dumps(result, sort_keys=True, indent=2))


if __name__ == "__main__":
    main()
