"""Create a local, ignored M11b holdout and a payload-free public hash receipt."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import random
import secrets
import tempfile

from shapely.affinity import scale, translate
from shapely.geometry import mapping, shape

from scripts.build_m11b_dataset import ROOT, FIXTURES, TOOLS, _json_bytes, _reference, _sha
from src.datasets import LocalDatasetRegistry
from src.evaluation.m11b import M11bTask, load_m11b_tasks, M11bPrediction, score_m11b
from src.tools import (AttributeFilterCondition, BufferFeaturesInput, FeaturesWithinInput,
                       FilterAttributesInput, NearestFeaturesInput, SpatialJoinInput)

PRIVATE_DIR = ROOT / "benchmarks/data/test/private"
PUBLIC_RECEIPT = ROOT / "benchmarks/data/test/m11b_private_receipt_v1.json"
FILES = ("sources.geojson", "targets.geojson", "regions.geojson",
         "m11b_test_tasks_v1.jsonl", "m11b_private_manifest_v1.json")


def _source_bytes(seed: int) -> dict[str, bytes]:
    rng = random.Random(seed)
    factor = rng.uniform(1.25, 3.25)
    dx, dy = rng.uniform(1000, 9000), rng.uniform(-9000, -1000)
    source_map = {"sources.geojson": "projected_sources.geojson",
                  "targets.geojson": "projected_targets.geojson",
                  "regions.geojson": "projected_regions.geojson"}
    result = {}
    for name, origin in source_map.items():
        doc = deepcopy(json.loads((FIXTURES / origin).read_text("utf-8")))
        for feature in doc["features"]:
            geometry = shape(feature["geometry"])
            feature["geometry"] = mapping(translate(scale(geometry, xfact=factor,
                                             yfact=factor, origin=(0, 0)), dx, dy))
            feature["id"] = f"h{rng.getrandbits(48):012x}"
        result[name] = _json_bytes(doc)
    return result


def _build_content(seed: int):
    sources = _source_bytes(seed)
    rows = []
    rng = random.Random(seed ^ 0xB11B)
    with tempfile.TemporaryDirectory(prefix="m11b_hidden_build_") as temp:
        temp_path = Path(temp)
        registry = LocalDatasetRegistry(temp_path / "registry")
        refs = {}
        for name, data in sources.items():
            source_path = temp_path / name
            source_path.write_bytes(data)
            refs[name] = registry.register_file(source_path, kind="vector",
                         crs="EPSG:3857", source="m11b-private-synthetic",
                         source_version="m11b-private-test-v1")
        source, target, regions = refs["sources.geojson"], refs["targets.geojson"], refs["regions.geojson"]
        def invoke(name, contract):
            return TOOLS.invoke(name, contract, datasets=registry)
        def add(op, query, result, tools, names):
            number = len(rows) + 1
            expectation = _reference(registry, result)
            expectation["tool_sequence"] = tools
            rows.append(M11bTask(task_id=f"m11b_test_{number:03d}", split="test",
                 task_type=op, category="private_" + op, difficulty="medium",
                 capability="supported", query=query, random_seed=seed % 2147483647 + number,
                 data_version="m11b-private-test-v1", source_fixtures=names,
                 expectation=expectation))
        for index in range(8):
            category = ("clinic", "school")[index // 4]
            threshold = (0, 5, 10, 20)[index % 4]
            out = invoke("filter_attributes", FilterAttributesInput(dataset=source,
                conditions=[AttributeFilterCondition(field="category", operator="eq", value=category),
                            AttributeFilterCondition(field="score", operator="gte", value=threshold)]))
            add("search", f"Locate {category} sources in private zone {rng.getrandbits(32):08x} with score >= {threshold}",
                out, ["filter_attributes"], ["sources.geojson"])
        for index in range(8):
            distance = float(index + 2.5)
            out = invoke("buffer_features", BufferFeaturesInput(dataset=source,
                distance_m=distance, quad_segs=8))
            add("within_distance", f"Private zone {rng.getrandbits(32):08x}: fixed source buffer {distance:g} metres",
                out, ["buffer_features"], ["sources.geojson"])
        for index in range(8):
            threshold = index
            k = index % 3 + 1
            filtered = invoke("filter_attributes", FilterAttributesInput(dataset=source,
                conditions=[AttributeFilterCondition(field="score", operator="gte", value=threshold)]))
            out = invoke("nearest_features", NearestFeaturesInput(dataset=filtered.dataset,
                target_dataset=target, k=k))
            add("nearest", f"Private zone {rng.getrandbits(32):08x}: {k} nearest targets, source score >= {threshold}",
                out, ["filter_attributes", "nearest_features"], ["sources.geojson", "targets.geojson"])
        for index in range(8):
            threshold = index
            filtered = invoke("filter_attributes", FilterAttributesInput(dataset=source,
                conditions=[AttributeFilterCondition(field="score", operator="gte", value=threshold)]))
            out = invoke("features_within", FeaturesWithinInput(dataset=filtered.dataset,
                container_dataset=regions))
            add("within", f"Private zone {rng.getrandbits(32):08x}: sources score >= {threshold} strictly within regions",
                out, ["filter_attributes", "features_within"], ["sources.geojson", "regions.geojson"])
        for index in range(8):
            predicate = ("intersects", "contains")[index // 4]
            threshold = index % 4
            filtered = invoke("filter_attributes", FilterAttributesInput(dataset=source,
                conditions=[AttributeFilterCondition(field="score", operator="gte", value=threshold)]))
            out = invoke("spatial_join", SpatialJoinInput(dataset=regions,
                right_dataset=filtered.dataset, predicate=predicate))
            add("spatial_join", f"Private zone {rng.getrandbits(32):08x}: region source {predicate} join, score >= {threshold}",
                out, ["filter_attributes", "spatial_join"], ["regions.geojson", "sources.geojson"])
    task_data = b"".join(_json_bytes(task.model_dump(mode="json")) for task in rows)
    private_manifest = {
        "schema_version": "1.0", "data_version": "m11b-private-test-v1",
        "seed": seed, "task_count": 40, "split": "test",
        "source": "Project-authored synthetic geometry transformed from M04b contract fixtures",
        "license": "CC0-1.0", "source_sha256": {name: _sha(data) for name, data in sources.items()},
        "tasks_sha256": _sha(task_data), "builder": "python -m scripts.build_m11b_hidden",
    }
    manifest_data = _json_bytes(private_manifest)
    receipt = {
        "schema_version": "1.0", "data_version": "m11b-private-test-v1",
        "task_count": 40, "split": "test", "license": "CC0-1.0",
        "tasks_sha256": _sha(task_data), "private_manifest_sha256": _sha(manifest_data),
        "source": "Project-authored synthetic private holdout; coordinates and IDs transformed from M04b contract structure",
        "content_location": "ignored local benchmarks/data/test/private; never commit payload",
        "hidden_queries_published": False,
    }
    return sources, task_data, manifest_data, _json_bytes(receipt)


def build_private(private_dir: Path = PRIVATE_DIR, receipt_path: Path = PUBLIC_RECEIPT,
                  *, seed: int | None = None):
    private_dir, receipt_path = Path(private_dir), Path(receipt_path)
    if private_dir.exists() or receipt_path.exists():
        raise FileExistsError("M11b private release already exists")
    if seed is None:
        seed = secrets.randbits(63)
    sources, tasks, manifest, receipt = _build_content(seed)
    private_dir.mkdir(parents=True)
    for name, content in sources.items():
        with (private_dir / name).open("xb") as stream:
            stream.write(content)
    with (private_dir / "m11b_test_tasks_v1.jsonl").open("xb") as stream:
        stream.write(tasks)
    with (private_dir / "m11b_private_manifest_v1.json").open("xb") as stream:
        stream.write(manifest)
    with receipt_path.open("xb") as stream:
        stream.write(receipt)
    return receipt_path


def audit_private(private_dir: Path = PRIVATE_DIR, receipt_path: Path = PUBLIC_RECEIPT) -> bool:
    private_dir, receipt_path = Path(private_dir), Path(receipt_path)
    if {p.name for p in private_dir.iterdir() if p.is_file()} != set(FILES):
        raise ValueError("private holdout file inventory mismatch")
    manifest = json.loads((private_dir / "m11b_private_manifest_v1.json").read_text("utf-8"))
    sources, tasks, private_manifest, receipt = _build_content(manifest["seed"])
    for name, data in sources.items():
        if (private_dir / name).read_bytes() != data:
            raise ValueError("private source differs from seed rebuild")
    if (private_dir / "m11b_test_tasks_v1.jsonl").read_bytes() != tasks:
        raise ValueError("private task differs from seed rebuild")
    if (private_dir / "m11b_private_manifest_v1.json").read_bytes() != private_manifest:
        raise ValueError("private manifest differs from seed rebuild")
    if receipt_path.read_bytes() != receipt:
        raise ValueError("public private-holdout receipt differs from seed rebuild")
    hidden = load_m11b_tasks(private_dir / "m11b_test_tasks_v1.jsonl")
    public = load_m11b_tasks(ROOT / "benchmarks/data/dev/m11b_tasks_v1.jsonl")
    if {t.query.casefold() for t in hidden} & {t.query.casefold() for t in public}:
        raise ValueError("dev/test query overlap")
    for task in hidden:
        e = task.expectation
        prediction = M11bPrediction(task_id=task.task_id, status=e.status,
            operation=task.task_type, exact_fields=e.exact_fields, steps=e.required_steps,
            tool_sequence=e.tool_sequence, feature_ids=e.feature_ids,
            numeric_values={k: v.value for k, v in e.numeric.items()},
            geometry=e.geometry, crs=e.crs)
        if not score_m11b(task, prediction).passed:
            raise ValueError("hidden reference grader mismatch")
    return True


if __name__ == "__main__":
    path = build_private()
    print(f"Published private M11b holdout hash receipt: {path}")
