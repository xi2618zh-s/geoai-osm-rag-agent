"""Independent reference oracle for M12a school coverage gaps.

Deliberately independent of the system under test: it reads the committed fixture
files with ``json`` and computes geometry with shapely/pyproj only. It must never
import ``src.tools``, ``src.population`` or ``src.scenarios`` (a test enforces this).
Same author as the system; independent implementation, not independent labelling.

Semantics (frozen in docs/roadmap/M12A_EVALUATION_FREEZE.md):
- A cell is every SCB feature of the fixture (cells intersecting or touching the study
  BBOX, unclipped); grid_id = ``rutid_inspire``.
- Population values: non-negative int; null or a suppression token is "not reported".
- Schools: fixture points with ``amenity == "school"``, transformed EPSG:4326 -> 3006
  with pyproj ``always_xy``.
- Distance: shapely distance from the cell polygon to the nearest school point (metres);
  nearest ties broken by poi_id.
- Gap: distance > D and the requested band value is reported and >= N.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pyproj import Transformer
from shapely.geometry import Point, shape

SUPPRESSION_TOKENS = {"", "-", ":", "..", "..."}
BAND_SOURCE_FIELDS = {"0-4": "ald0_4", "5-9": "ald5_9", "10-14": "ald10_14", "15-19": "ald15_19"}


def _reported(value: Any) -> int | None:
    if value is None or (isinstance(value, str) and value.strip() in SUPPRESSION_TOKENS):
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"unexpected population value {value!r}")
    return value


def load_cells(scb_path: Path) -> list[dict[str, Any]]:
    document = json.loads(Path(scb_path).read_bytes())
    cells = []
    for feature in document["features"]:
        props = feature["properties"]
        cells.append({
            "grid_id": props["rutid_inspire"],
            "geometry": shape(feature["geometry"]),
            "population_total": _reported(props.get("beftotalt")),
            "bands": {band: _reported(props.get(field)) for band, field in BAND_SOURCE_FIELDS.items()},
        })
    return cells


def load_schools(poi_path: Path) -> list[dict[str, Any]]:
    transformer = Transformer.from_crs("EPSG:4326", "EPSG:3006", always_xy=True)
    document = json.loads(Path(poi_path).read_bytes())
    schools = []
    for feature in document["features"]:
        props = feature["properties"]
        if props.get("amenity") != "school":
            continue
        lon, lat = feature["geometry"]["coordinates"]
        x, y = transformer.transform(lon, lat)
        schools.append({"poi_id": props["poi_id"], "name": props.get("name"), "point": Point(x, y)})
    return schools


def nearest_table(cells: list[dict[str, Any]], schools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for cell in cells:
        best = min(((cell["geometry"].distance(s["point"]), s["poi_id"], s) for s in schools),
                   key=lambda item: (item[0], item[1]))
        rows.append({**cell, "distance_m": best[0], "nearest_poi_id": best[1], "nearest_name": best[2]["name"]})
    return rows


def coverage_gaps(rows: list[dict[str, Any]], *, distance_m: float, band: str, threshold: int) -> dict[str, Any]:
    gaps = [r for r in rows if r["distance_m"] > distance_m
            and r["bands"][band] is not None and r["bands"][band] >= threshold]
    total_children = sum(r["bands"][band] for r in rows if r["bands"][band] is not None)
    gap_children = sum(r["bands"][band] for r in gaps)
    margin = min(abs(r["distance_m"] - distance_m) for r in rows)
    return {
        "gap_grid_ids": sorted(r["grid_id"] for r in gaps),
        "gap_cell_count": len(gaps),
        "gap_children": gap_children,
        "total_children": total_children,
        "gap_children_share": (gap_children / total_children) if total_children else None,
        "grid_cell_count": len(rows),
        "band_not_reported_cells": sum(1 for r in rows if r["bands"][band] is None),
        "max_gap_distance_m": max((r["distance_m"] for r in gaps), default=None),
        "min_distance_margin_m": margin,
        "gaps": [{"grid_id": r["grid_id"], "children": r["bands"][band], "population_total": r["population_total"],
                  "distance_m": r["distance_m"], "nearest_poi_id": r["nearest_poi_id"]}
                 for r in sorted(gaps, key=lambda item: item["grid_id"])],
    }


def reference(scb_path: Path, poi_path: Path, *, distance_m: float, band: str, threshold: int) -> dict[str, Any]:
    cells = load_cells(scb_path)
    schools = load_schools(poi_path)
    result = coverage_gaps(nearest_table(cells, schools), distance_m=distance_m, band=band, threshold=threshold)
    result["school_count"] = len(schools)
    return result
