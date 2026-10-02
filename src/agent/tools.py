"""Allow-listed tool registry and deterministic geospatial tool handlers."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import json
from pathlib import Path
import re
import time
from typing import Any, Callable
import unicodedata
import uuid

from src.config import (
    OSM_CLIP_CACHE_DIR,
    OSM_CLIP_CACHE_ENABLED,
    OSM_PBF,
    OSMIUM_EXTRACT_STRATEGY,
    OUTPUT_DIR,
)
from src.observability import log_event
from src.osm.extractor import OSMFeature, extract_features_to_geojson, osmium_extract_bbox
from src.osm.geocode import geocode_to_bbox
from src.reliability import get_or_create_clip


@dataclass
class ToolPayload:
    values: dict[str, Any]
    summary: dict[str, Any]


ToolHandler = Callable[[dict[str, Any]], ToolPayload]


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    required_args: frozenset[str]
    optional_args: frozenset[str]
    output_keys: tuple[str, ...]
    handler: ToolHandler
    retryable_exceptions: tuple[type[BaseException], ...] = ()
    max_attempts: int = 1


class ToolRegistryError(RuntimeError):
    def __init__(self, code: str, message: str, *, attempts: int = 0):
        super().__init__(message)
        self.code = code
        self.attempts = attempts


class ToolRegistry:
    def __init__(self):
        self._tools: dict[str, ToolSpec] = {}

    def register(self, spec: ToolSpec) -> None:
        if spec.name in self._tools:
            raise ValueError(f"Tool already registered: {spec.name}")
        if not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", spec.name):
            raise ValueError(f"Invalid tool name: {spec.name}")
        self._tools[spec.name] = spec

    def get(self, name: str) -> ToolSpec:
        try:
            return self._tools[name]
        except KeyError as error:
            raise ToolRegistryError(
                "TOOL_NOT_REGISTERED", f"Tool is not allow-listed: {name}"
            ) from error

    def describe(self) -> list[dict[str, Any]]:
        return [
            {
                "name": spec.name,
                "description": spec.description,
                "required_args": sorted(spec.required_args),
                "optional_args": sorted(spec.optional_args),
                "output_keys": list(spec.output_keys),
                "max_attempts": spec.max_attempts,
            }
            for spec in self._tools.values()
        ]

    def invoke(
        self,
        name: str,
        arguments: dict[str, Any],
        *,
        allow_retry: bool,
        trace_id: str,
        step_id: str,
    ) -> tuple[ToolPayload, int, bool]:
        spec = self.get(name)
        keys = set(arguments)
        missing = spec.required_args.difference(keys)
        unexpected = keys.difference(spec.required_args | spec.optional_args)
        if missing or unexpected:
            raise ToolRegistryError(
                "TOOL_INPUT_INVALID",
                f"Invalid arguments for {name}; missing={sorted(missing)}, "
                f"unexpected={sorted(unexpected)}",
            )
        attempts = 0
        limit = spec.max_attempts if allow_retry else 1
        while True:
            attempts += 1
            try:
                payload = spec.handler(arguments)
                if not isinstance(payload, ToolPayload):
                    raise TypeError(f"Tool {name} did not return ToolPayload")
                absent = set(spec.output_keys).difference(payload.values)
                if absent:
                    raise ToolRegistryError(
                        "TOOL_OUTPUT_INVALID",
                        f"Tool {name} omitted output keys: {sorted(absent)}",
                        attempts=attempts,
                    )
                return payload, attempts, attempts > 1
            except ToolRegistryError:
                raise
            except Exception as error:
                can_retry = (
                    attempts < limit
                    and spec.retryable_exceptions
                    and isinstance(error, spec.retryable_exceptions)
                )
                if not can_retry:
                    raise ToolRegistryError(
                        f"TOOL_{name.upper()}_FAILED",
                        f"{name} failed: {error}",
                        attempts=attempts,
                    ) from error
                log_event(
                    "tool_retry",
                    trace_id=trace_id,
                    step_id=step_id,
                    tool=name,
                    attempt=attempts,
                    exception_type=type(error).__name__,
                )
                time.sleep(0.05 * attempts)


def _slug(text: str) -> str:
    text = unicodedata.normalize("NFKD", str(text).lower())
    text = text.encode("ascii", "ignore").decode("ascii")
    text = re.sub(r"[^a-z0-9]+", "_", text).strip("_")
    return text or "workflow"


def _geocode_tool(arguments: dict[str, Any]) -> ToolPayload:
    bbox = geocode_to_bbox(
        str(arguments["place"]), trace_id=str(arguments["trace_id"])
    )
    return ToolPayload(
        values={"bbox": tuple(float(item) for item in bbox)},
        summary={"bbox": [round(float(item), 7) for item in bbox]},
    )


def _clip_tool(arguments: dict[str, Any]) -> ToolPayload:
    bbox = tuple(float(item) for item in arguments["bbox"])
    if OSM_CLIP_CACHE_ENABLED:
        sub_pbf, cache_hit = get_or_create_clip(
            Path(OSM_PBF),
            bbox,
            strategy=OSMIUM_EXTRACT_STRATEGY,
            cache_dir=OSM_CLIP_CACHE_DIR,
            enabled=True,
            creator=lambda output: osmium_extract_bbox(Path(OSM_PBF), output, bbox),
        )
    else:
        sub_pbf = Path(arguments["temp_dir"]) / "workflow_subset.osm.pbf"
        osmium_extract_bbox(Path(OSM_PBF), sub_pbf, bbox)
        cache_hit = False
    return ToolPayload(
        values={"sub_pbf": Path(sub_pbf), "clip_cache_hit": bool(cache_hit)},
        summary={
            "clip_cache_hit": bool(cache_hit),
            "clip_filename": Path(sub_pbf).name,
        },
    )


def _extract_tool(arguments: dict[str, Any]) -> ToolPayload:
    tag = arguments["tag"]
    key = str(tag["key"]).strip()
    value = str(tag["value"]).strip()
    intermediate = Path(arguments["temp_dir"]) / f"{arguments['step_id']}.geojson"
    features = extract_features_to_geojson(
        Path(arguments["sub_pbf"]), key, value, intermediate
    )
    tag_text = f"{key}={value}"
    counts = Counter(item.osm_type for item in features)
    return ToolPayload(
        values={"features": features, "tag": tag_text},
        summary={
            "tag": tag_text,
            "count": len(features),
            "counts_by_type": {
                "node": counts.get("node", 0),
                "way": counts.get("way", 0),
                "relation": counts.get("relation", 0),
            },
        },
    )


def _combine_tool(arguments: dict[str, Any]) -> ToolPayload:
    filters = [f"{item['key']}={item['value']}" for item in arguments["filters"]]
    feature_sets: list[list[OSMFeature]] = arguments["feature_sets"]
    if len(filters) != len(feature_sets):
        raise ValueError("filters and feature_sets must have equal length")
    indexed: dict[tuple[str, int], dict[str, Any]] = {}
    counts_by_filter: dict[str, int] = {}
    for tag, features in zip(filters, feature_sets):
        counts_by_filter[tag] = len(features)
        for feature in features:
            identity = (feature.osm_type, feature.osm_id)
            item = indexed.setdefault(
                identity, {"feature": feature, "matched_filters": set()}
            )
            item["matched_filters"].add(tag)
    if arguments["operation"] == "intersection":
        selected = [
            item for item in indexed.values() if len(item["matched_filters"]) == len(filters)
        ]
    else:
        selected = list(indexed.values())
    selected.sort(
        key=lambda item: (item["feature"].osm_type, item["feature"].osm_id)
    )
    combined = [
        {
            "feature": item["feature"],
            "matched_filters": sorted(item["matched_filters"]),
        }
        for item in selected
    ]
    counts = Counter(item["feature"].osm_type for item in combined)
    return ToolPayload(
        values={
            "combined": combined,
            "counts_by_filter": counts_by_filter,
            "counts_by_type": {
                "node": counts.get("node", 0),
                "way": counts.get("way", 0),
                "relation": counts.get("relation", 0),
            },
        },
        summary={
            "operation": arguments["operation"],
            "input_counts": counts_by_filter,
            "deduplicated_count": len(combined),
        },
    )


def _write_tool(arguments: dict[str, Any]) -> ToolPayload:
    filters = [f"{item['key']}={item['value']}" for item in arguments["filters"]]
    combined = arguments["combined"]
    counts = Counter(item["feature"].osm_type for item in combined)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    label = _slug(
        f"{arguments['place']}_{arguments['operation']}_{'_'.join(filters)}_"
        f"{arguments['query']}"
    )[:100]
    path = OUTPUT_DIR / f"workflow_{label}_{uuid.uuid4().hex[:10]}.geojson"
    collection = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {
                    "osm_type": item["feature"].osm_type,
                    "osm_id": item["feature"].osm_id,
                    "name": item["feature"].name,
                    "tags": item["feature"].tags,
                    "matched_filters": item["matched_filters"],
                },
                "geometry": item["feature"].geometry,
            }
            for item in combined
        ],
        "metadata": {
            "workflow": {
                "operation": arguments["operation"],
                "filters": filters,
                "counts_by_filter": arguments["counts_by_filter"],
                "deduplicated_total": len(combined),
            }
        },
    }
    with path.open("w", encoding="utf-8") as stream:
        json.dump(collection, stream, ensure_ascii=False, indent=2)
    return ToolPayload(
        values={
            "geojson_path": str(path),
            "geojson_filename": path.name,
            "count": len(combined),
            "counts_by_type": {
                "node": counts.get("node", 0),
                "way": counts.get("way", 0),
                "relation": counts.get("relation", 0),
            },
        },
        summary={"geojson_filename": path.name, "count": len(combined)},
    )


def build_default_registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(
        ToolSpec(
            name="geocode_place",
            description="Resolve a place to a Nominatim bounding box",
            required_args=frozenset({"place", "trace_id"}),
            optional_args=frozenset(),
            output_keys=("bbox",),
            handler=_geocode_tool,
        )
    )
    registry.register(
        ToolSpec(
            name="clip_osm",
            description="Clip the country PBF once for the workflow bounding box",
            required_args=frozenset({"bbox", "temp_dir"}),
            optional_args=frozenset(),
            output_keys=("sub_pbf", "clip_cache_hit"),
            handler=_clip_tool,
            retryable_exceptions=(TimeoutError,),
            max_attempts=2,
        )
    )
    registry.register(
        ToolSpec(
            name="extract_tag",
            description="Extract node, way, and relation features for one OSM tag",
            required_args=frozenset({"sub_pbf", "tag", "temp_dir", "step_id"}),
            optional_args=frozenset(),
            output_keys=("features", "tag"),
            handler=_extract_tool,
        )
    )
    registry.register(
        ToolSpec(
            name="combine_features",
            description="Apply union or intersection semantics and deduplicate OSM objects",
            required_args=frozenset({"operation", "filters", "feature_sets"}),
            optional_args=frozenset(),
            output_keys=("combined", "counts_by_filter", "counts_by_type"),
            handler=_combine_tool,
        )
    )
    registry.register(
        ToolSpec(
            name="write_geojson",
            description="Write the combined workflow result and provenance metadata",
            required_args=frozenset(
                {
                    "combined",
                    "counts_by_filter",
                    "operation",
                    "filters",
                    "place",
                    "query",
                }
            ),
            optional_args=frozenset(),
            output_keys=("geojson_path", "geojson_filename", "count", "counts_by_type"),
            handler=_write_tool,
        )
    )
    return registry


DEFAULT_TOOL_REGISTRY = build_default_registry()
