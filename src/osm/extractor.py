"""OSM clipping and GeoJSON extraction for nodes, ways, and areas."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple
import json
import shutil
import subprocess
import sys

import osmium

from src.config import OSMIUM_EXTRACT_STRATEGY, OSMIUM_TIMEOUT_S
from src.path_compat import native_readable_path


@dataclass
class OSMFeature:
    osm_type: str
    osm_id: int
    name: Optional[str]
    tags: Dict[str, str]
    geometry: Dict


# Backward-compatible public name used by older callers.
OSMPoint = OSMFeature


def find_osmium_executable() -> Optional[str]:
    """Locate osmium on PATH or in the active Conda environment."""

    candidates = [
        Path(sys.prefix) / "Library" / "bin" / "osmium.exe",
        Path(sys.prefix) / "bin" / "osmium",
    ]
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    discovered = shutil.which("osmium")
    if discovered:
        # On Windows shutil.which may return an upper-case `.EXE`. osmium-tool
        # 1.18 treats that argv[0] spelling as a subcommand, so prefer the
        # canonical lower-case Conda path above whenever it exists.
        return discovered
    return None


def osmium_extract_bbox(
    input_pbf: Path,
    output_pbf: Path,
    bbox: Tuple[float, float, float, float],
    *,
    timeout_s: int = OSMIUM_TIMEOUT_S,
) -> None:
    """Clip a PBF with a configurable, low-memory-by-default strategy."""

    input_pbf = Path(input_pbf)
    output_pbf = Path(output_pbf)
    if not input_pbf.is_file():
        raise FileNotFoundError(f"Input PBF file not found: {input_pbf}")

    executable = find_osmium_executable()
    if not executable:
        raise RuntimeError(
            "osmium-tool is not available on PATH. Activate the Conda environment "
            "or install it with: conda install -c conda-forge osmium-tool"
        )

    output_pbf.parent.mkdir(parents=True, exist_ok=True)
    bbox_str = ",".join(str(float(x)) for x in bbox)
    command = [
        executable,
        "extract",
        "--bbox",
        bbox_str,
        "--strategy",
        OSMIUM_EXTRACT_STRATEGY,
        str(input_pbf),
        "-o",
        str(output_pbf),
        "-O",
        "--set-bounds",
    ]

    print(f"[osmium] Extracting bbox {bbox_str} -> {output_pbf}")
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout_s,
        )
    except subprocess.TimeoutExpired as error:
        raise TimeoutError(
            f"osmium extract timed out after {timeout_s}s"
        ) from error
    if result.returncode != 0:
        details = (result.stderr or result.stdout or "unknown error").strip()
        raise RuntimeError(f"osmium extract failed ({result.returncode}): {details}")
    if not output_pbf.is_file():
        raise RuntimeError(f"osmium completed but output was not created: {output_pbf}")


def _matches(obj, key: str, value: str) -> bool:
    return obj.tags.get(key) == value


def _feature(obj, osm_type: str, osm_id: int, geometry: Dict) -> OSMFeature:
    return OSMFeature(
        osm_type=osm_type,
        osm_id=int(osm_id),
        name=obj.tags.get("name"),
        tags=dict(obj.tags),
        geometry=geometry,
    )


def _write_geojson(features: List[OSMFeature], out_geojson: Path, stats: Dict) -> None:
    collection = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {
                    "osm_type": item.osm_type,
                    "osm_id": item.osm_id,
                    "name": item.name,
                    "tags": item.tags,
                },
                "geometry": item.geometry,
            }
            for item in features
        ],
        "metadata": {"extraction": stats},
    }
    out_geojson = Path(out_geojson)
    out_geojson.parent.mkdir(parents=True, exist_ok=True)
    with out_geojson.open("w", encoding="utf-8") as stream:
        json.dump(collection, stream, ensure_ascii=False, indent=2)


def extract_features_to_geojson(
    input_pbf: Path,
    key: str,
    value: str,
    out_geojson: Path,
    *,
    include_nodes: bool = True,
    include_ways: bool = True,
    include_relations: bool = True,
) -> List[OSMFeature]:
    """Extract matching OSM nodes, linear ways, and polygonal areas.

    Closed area ways and multipolygon relations are emitted by pyosmium's area
    assembler. Non-area relations have no single canonical GeoJSON geometry and
    are skipped with an explicit counter in the output metadata.
    """

    factory = osmium.geom.GeoJSONFactory()
    features: List[OSMFeature] = []
    geometry_failures = 0
    matching_relation_ids = set()
    area_relation_ids = set()
    area_way_ids = set()

    with native_readable_path(Path(input_pbf)) as readable_pbf:
        processor = (
            osmium.FileProcessor(str(readable_pbf)).with_locations().with_areas()
        )
        for obj in processor:
            object_type = type(obj).__name__
            if object_type == "Node":
                if not include_nodes or not _matches(obj, key, value):
                    continue
                if not obj.location.valid():
                    geometry_failures += 1
                    continue
                geometry = {
                    "type": "Point",
                    "coordinates": [float(obj.lon), float(obj.lat)],
                }
                features.append(_feature(obj, "node", obj.id, geometry))

            elif object_type == "Way":
                if not include_ways or not _matches(obj, key, value):
                    continue
                try:
                    geometry = json.loads(factory.create_linestring(obj))
                    features.append(_feature(obj, "way", obj.id, geometry))
                except Exception:
                    geometry_failures += 1

            elif object_type == "Area":
                if not _matches(obj, key, value):
                    continue
                from_way = bool(obj.from_way())
                if (from_way and not include_ways) or (
                    not from_way and not include_relations
                ):
                    continue
                try:
                    geometry = json.loads(factory.create_multipolygon(obj))
                    osm_type = "way" if from_way else "relation"
                    original_id = int(obj.orig_id())
                    features.append(_feature(obj, osm_type, original_id, geometry))
                    if from_way:
                        area_way_ids.add(original_id)
                    else:
                        area_relation_ids.add(original_id)
                except Exception:
                    geometry_failures += 1

            elif object_type == "Relation":
                if (
                    include_relations
                    and _matches(obj, key, value)
                    and int(obj.id) not in area_relation_ids
                ):
                    matching_relation_ids.add(int(obj.id))

    # FileProcessor yields ways before assembled areas. Remove the temporary
    # LineString representation when the same closed way was assembled as a
    # polygon, keeping one feature per original OSM object.
    features = [
        item
        for item in features
        if not (
            item.osm_type == "way"
            and item.osm_id in area_way_ids
            and item.geometry.get("type") == "LineString"
        )
    ]

    counts = Counter(item.osm_type for item in features)
    stats = {
        "tag": f"{key}={value}",
        "total": len(features),
        "nodes": counts.get("node", 0),
        "ways": counts.get("way", 0),
        "relations": counts.get("relation", 0),
        "geometry_failures": geometry_failures,
        "skipped_non_area_relations": len(
            matching_relation_ids.difference(area_relation_ids)
        ),
    }
    _write_geojson(features, Path(out_geojson), stats)
    print(f"[extractor] Saved {len(features)} features to {out_geojson}: {stats}")
    return features


def extract_nodes_to_geojson(
    input_pbf: Path, key: str, value: str, out_geojson: Path
) -> List[OSMFeature]:
    """Backward-compatible node-only extraction."""

    return extract_features_to_geojson(
        input_pbf,
        key,
        value,
        out_geojson,
        include_nodes=True,
        include_ways=False,
        include_relations=False,
    )


def extract_ways_to_geojson(
    input_pbf: Path, key: str, value: str, out_geojson: Path
) -> List[OSMFeature]:
    """Extract linear and polygonal ways."""

    return extract_features_to_geojson(
        input_pbf,
        key,
        value,
        out_geojson,
        include_nodes=False,
        include_ways=True,
        include_relations=False,
    )
