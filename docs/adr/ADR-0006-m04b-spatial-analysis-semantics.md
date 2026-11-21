# ADR-0006: M04b uses an explicit pyproj CRS policy, make_valid repair and derived DatasetRefs

- Status: Accepted
- Date: 2026-10-05
- Decision owner/Session: confirmed by the user; M04b spatial analysis tools
- Affected modules: M02, M04, M07–M13

## Context

M04a established a strict analysis tool registry that exchanges only DatasetRefs, but it supports only read-only schema and geometry validation. M04b's reprojection, metric distances, two-dataset spatial relations and derived outputs require frozen semantics for CRS handling, geometry repair, attribute merging, lineage and resource limits. Shapely does not provide CRS transformations, and M02's `register_file()` could not yet accept lineage from derived operations.

## Decision

1. Add `pyproj>=3.6,<4` (pyproj 3.7.1 in the acceptance environment); do not introduce GeoPandas or Fiona. Shapely stays at `>=2,<3` (2.1.2 in the acceptance environment).
2. `reproject_dataset` uses `Transformer.from_crs(..., always_xy=True)` with `errcheck=True`; CRS parsing and equivalence are decided by `pyproj.CRS`, and non-finite coordinates are rejected before and after transformation.
3. Two-dataset tools require equivalent CRSs; a mismatch returns `ANALYSIS_TOOL_CRS_MISMATCH` and is never reprojected automatically.
4. `buffer_features` and `nearest_features` reject geographic CRSs and accept only a confirmable two-dimensional projected CRS. Both horizontal axes must have the same finite, positive `unit_conversion_factor`; metric inputs are converted to the CRS's native unit, and nearest outputs are always in metres.
5. `repair_geometry` calls Shapely `make_valid` only on invalid geometries. Valid geometries are kept unchanged; features are never dropped and GeometryCollection subtypes are never extracted. The whole call fails if a repair yields an empty, still-invalid or unserializable geometry. Type changes, dimension reduction and GeometryCollections are reported in statistics and warnings.
6. Add eight strict v1 tools: `filter_attributes`, `reproject_dataset`, `repair_geometry`, `buffer_features`, `nearest_features`, `features_within`, `spatial_join`, `aggregate_by_region`. Every derived result is registered as a new temporary DatasetRef with a default TTL of 24 hours.
7. `nearest` returns at most K targets per source feature, one row per matched pair, sorted by `(distance_m, target_feature_id/index)`, and outputs the source geometry, namespaced attributes from both sides and the distance in metres. `within` uses strict Shapely `within`: boundaries do not count and left features are never duplicated.
8. Spatial join is always an inner join supporting only `intersects/within/contains`; each matched pair produces one row, sorted by left and right input index, with the left geometry and `left_`/`right_` attribute namespaces. Name conflicts fail explicitly.
9. Aggregation follows the GeoDSL step boundary: it takes the original region DatasetRef, an already joined values DatasetRef and an explicit region key, and never runs a new spatial predicate implicitly. It supports `count/sum/mean/min/max` and keeps regions with zero matches; count is 0 and the other statistics are null when there are no valid numbers. Booleans are not treated as numbers. Deterministic `agg_*` fields are generated and conflicts are rejected.
10. `DatasetRegistry.register_file()` gains an optional, backward-compatible `lineage` parameter; without it the original register lineage is still produced. Derived tools record the operation, all direct parent IDs, a UTC time and the SHA-256 of the canonical parameters.
11. `AnalysisToolRegistry` validates every input DatasetRef, applies object/byte limits cumulatively and verifies after execution that each input is unchanged. Read-only tools still require the output reference to equal the primary input; derived tools require a new, registered and verifiable DatasetRef, complete direct-parent lineage and output file/contract byte limits. On failure, any temporary output already created is released.
12. New stable error codes: `ANALYSIS_TOOL_CRS_INVALID`, `ANALYSIS_TOOL_CRS_MISMATCH`, `ANALYSIS_TOOL_GEOGRAPHIC_DISTANCE_UNSUPPORTED`, `ANALYSIS_TOOL_CRS_UNIT_UNSUPPORTED`, `ANALYSIS_TOOL_FIELD_MISSING`, `ANALYSIS_TOOL_FIELD_TYPE_INVALID`, `ANALYSIS_TOOL_GEOMETRY_REPAIR_FAILED`, `ANALYSIS_TOOL_GEOMETRY_INVALID`. An explicit `AnalysisToolError` is never wrapped as a generic execution error.

## Alternatives

- Introduce GeoPandas/Fiona: rejected; the registry only supports GeoJSON today, Shapely + pyproj already cover M04b, and fewer dependencies mean less implicit behaviour.
- Approximate metric buffers/distances in a longitude/latitude CRS: rejected; results vary with latitude and violate the quality boundary.
- Reproject automatically when CRSs differ: rejected; it hides a transformation that affects results, so `reproject_dataset` must be called explicitly.
- Use `buffer(0)` or extract polygons from GeometryCollections: rejected; it can silently change or lose parts of a geometry.
- Return derived GeoJSON paths or GeoDataFrames directly: rejected; violates the DatasetRef, safe-path and traceable-lineage boundaries.

## Consequences

- Positive: the eight tools have deterministic semantics, explicit CRS/units, stable errors, immutable inputs, derived lineage, TTLs and resource limits; old M02/M04a behaviour stays compatible.
- Cost: the current implementation is a deterministic per-feature algorithm for small GeoJSON files without a spatial index; the default 10,000-object limit can still allow expensive double loops.
- Risk: pyproj's EPSG database is a runtime-environment dependency; complex GeometryCollections and large-data performance need separate evaluation in later modules.

## Migration and rollback

Existing `register_file()` calls need no change; behaviour without the optional `lineage` is unchanged. The old `QUALITY_TOOL_REGISTRY` and `src.agent.tools` keep their interfaces. Rolling back M04b means deleting the spatial contracts/implementation and the pyproj dependency and reverting the registry's optional parameter and multi-input derivation guards; the two M04a quality tools still work on their own.
