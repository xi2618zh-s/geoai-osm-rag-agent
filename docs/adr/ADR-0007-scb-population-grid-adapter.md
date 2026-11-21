# ADR-0007: M05 uses a fixed SCB population grid adapter and conservative fully-contained-cell statistics

- Status: Accepted
- Date: 2026-10-05
- Decision owner/Session: confirmed by the user; M05 population/admin data
- Affected modules: M02, M04, M05, M10–M14

## Context

M05 needs a population source with a clear licence that can be fetched reproducibly without GeoPandas/Fiona/GDAL, while keeping the DatasetRef, explicit-CRS, immutable-input and offline-fixture boundaries. SCB (Statistics Sweden) publishes an annual 1 km registered-population grid with total population, sex and five-year age groups under CC0, but its statistical disclosure control means the total may not equal the sum of its breakdowns. The WFS service is a network boundary and must not leak into offline analysis tools or default tests.

## Decision

1. The only source in the first version is the SCB WFS 1.1.0 layer `stat:befolkning_1km_2025`: data year 2025, CRS EPSG:3006, licence CC0-1.0. DeSO + PxWeb are left as a later extension.
2. Network access happens only through `scripts/fetch_scb_population_grid.py` and the download layer in `src.population`. Requests pin the HTTPS host, WFS version, layer, EPSG:3006, an explicit BBOX, GeoJSON output, `sortBy=objectid` and the attribute list. Public analysis tools accept no URLs or paths, and offline tests never touch the network.
3. The download receipt records the canonical URL, schema URL/hash, UTC time, HTTP status, Content-Type, ETag/Last-Modified (when present), feature count, reference date, response hash, source and licence. Network/HTTP errors use `POPULATION_ADAPTER_DOWNLOAD_FAILED`; content, ordering, CRS or schema errors use `POPULATION_ADAPTER_RESPONSE_INVALID`. Neither wraps the other.
4. `load_population_dataset` is a strict v1 DatasetRef→DatasetRef tool with fixed `provider=scb` and `dataset_year=2025`. It validates an already registered raw GeoJSON and never downloads data.
5. The CRS declared in the input GeoJSON, the DatasetRef CRS and EPSG:3006 must be equivalent according to `pyproj.CRS`; there is no automatic reprojection. On a mismatch the caller must first call `reproject_dataset` explicitly.
6. The core mapping is fixed: `rutid_inspire→grid_id`, `rutid_scb→source_grid_id_scb`, `referenstid→population_reference_date`, `beftotalt→population_total`, plus a generated `population_status`. Standardized optional fields for men, women and `ald0_4` through `ald100w` are kept as well.
7. A source value of 0 stays the integer 0; null, missing fields and supported suppression tokens are normalized to null. Grid cells that were not returned must never be inferred as 0. The status is only `reported` or `missing_or_suppressed`, without guessing a specific reason.
8. The total is not required to equal the sex or age breakdowns. A mismatch is recorded in warnings as a feature of SCB disclosure control, not treated as corruption. Negative, boolean or non-integer population values, invalid dates, missing core fields and duplicate grid IDs fail explicitly.
9. The output is a newly registered, verifiable temporary DatasetRef with a default TTL of 86400 seconds. Its lineage contains the raw parent ID, operation, time and a parameter hash; the parameters cover the layer, data year, field-mapping version and adapter version. The raw DatasetRef is not modified.
10. M05 adds `POPULATION_TOOL_REGISTRY`, containing the ten frozen M04 tools plus `load_population_dataset`. The existing `ANALYSIS_TOOL_REGISTRY`, `QUALITY_TOOL_REGISTRY` and `src.agent.tools` are unchanged; M05 tools are not exposed to an LLM.
11. The tool layer adds `ANALYSIS_TOOL_POPULATION_SCHEMA_INVALID` and `ANALYSIS_TOOL_POPULATION_VALUE_INVALID` and reuses M04's CRS, geometry and resource-limit errors; an explicit `AnalysisToolError` is never wrapped as a generic execution failure.
12. The fixed reference method is named `fully_contained_grid_population`: it sums only grid cells fully contained in a POI buffer and reports the number of fully contained cells, the number of intersecting but excluded cells, the population total and a mandatory warning. It is a conservative lower bound, not an area-weighted or true buffer-population estimate.

## Alternatives

- GeoPandas/Fiona/GDAL: rejected; GeoJSON, Shapely and pyproj are enough for the first version, and extra dependencies would widen the runtime and implicit I/O behaviour.
- DeSO + PxWeb: deferred; it needs a second data interface, code joins, and coordination between administrative-area and population-table versions, which is outside the first M05 version.
- Network access inside tools, or accepting URLs/local paths: rejected; violates the DatasetRef, safe-path and offline-reproducibility boundaries.
- Automatic reprojection: rejected; it would hide a CRS transformation that affects the statistics.
- Treat null/unreturned cells as 0, or force breakdowns to sum to the total: rejected; conflicts with SCB's disclosure-control semantics.
- Call the full population of every intersecting cell the "buffer population": rejected; it systematically overestimates and hides the boundary semantics. If area weighting is implemented later it must have its own name and contract.

## Consequences

- Positive: source, licence, version, request, schema, hashes, field semantics, CRS and lineage are all traceable; runtime analysis stays offline, DatasetRef-only and deterministic.
- Cost: the first version supports a single year/layer/grid size; there are no administrative joins, area weighting, time series or large-scale spatial index.
- Risk: the upstream WFS schema or service behaviour may change; the download layer then fails explicitly and requires a new mapping version, fixture, ADR or migration note instead of silently accepting the drift.

## Migration and rollback

Old M04 registries and callers need no migration. Deterministic code that needs M05 uses `POPULATION_TOOL_REGISTRY` explicitly; future sources/years need a new mapping version or contract instead of reinterpreting v1. Rolling back M05 means deleting `src.population`, the population contracts/registry, scripts and fixtures; M02/M04 behaviour is unaffected.
