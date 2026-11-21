# Tool catalogue

> This catalogue lists only tools that are actually registered and pass their tests. The old workflow uses `src.agent.tools.DEFAULT_TOOL_REGISTRY`; M04a read-only quality tools use `src.tools.QUALITY_TOOL_REGISTRY`; the combined M04a+M04b tools use `src.tools.ANALYSIS_TOOL_REGISTRY`; M05 population adaptation uses `src.tools.POPULATION_TOOL_REGISTRY`; M06b stop coverage uses `src.tools.ACCESSIBILITY_TOOL_REGISTRY`. Planned tools must not be listed as available before they are implemented, tested and registered.

## Current allow-listed tools

| Tool | Purpose | Required parameters | Output keys | Retries | Main boundaries |
|---|---|---|---|---|---|
| `geocode_place` | Resolve a place to a Nominatim bbox | `place`, `trace_id` | `bbox` | none | External network dependency; subject to the geocode cache and timeout policy. |
| `clip_osm` | Clip the national PBF to a bbox | `bbox`, `temp_dir` | `sub_pbf`, `clip_cache_hit` | `TimeoutError` at most 2 times | Needs the local PBF and osmium; cached by source-file fingerprint. |
| `extract_tag` | Extract one OSM tag from the sub-PBF | `sub_pbf`, `tag`, `temp_dir`, `step_id` | `features`, `tag` | none | Supports node/way/area relation; not a general spatial query. |
| `combine_features` | Union/intersection and deduplication by OSM object identity | `operation`, `filters`, `feature_sets` | `combined`, `counts_by_filter`, `counts_by_type` | none | Intersection is object-identity intersection, not geometric topological intersection. |
| `write_geojson` | Write results and provenance metadata | `combined`, `counts_by_filter`, `operation`, `filters`, `place`, `query` | `geojson_path`, `geojson_filename`, `count`, `counts_by_type` | none | Writes to the configured output directory; file names carry a random suffix. |

## Registry guarantees

- Unregistered tools return `TOOL_NOT_REGISTERED`.
- Missing or extra parameter keys return `TOOL_INPUT_INVALID`.
- Missing declared output keys return `TOOL_OUTPUT_INVALID`.
- Handler exceptions are wrapped as `TOOL_<NAME>_FAILED`; only declared exceptions may be retried, up to the tool's limit.
- Inputs and outputs of these tools are in-process objects/paths; analysis tools added after M02 should use `DatasetRef` instead, without breaking the existing v1 workflow.

## M04a DatasetRef quality tools

| Tool | Registry | Input contract | Output contract | Supported kinds | Main boundaries |
|---|---|---|---|---|---|
| `inspect_dataset_schema` | `QUALITY_TOOL_REGISTRY` | `InspectDatasetSchemaInput.v1` | `InspectDatasetSchemaOutput.v1` | vector/table/raster/network metadata | Reads only verified DatasetRefs; the current backend actually supports only GeoJSON/CSV. |
| `validate_geometry` | `QUALITY_TOOL_REGISTRY` | `ValidateGeometryInput.v1` | `ValidateGeometryOutput.v1` | vector | Shapely 2 topology checks; the number of issues is capped; never repairs, drops or rewrites. |

### Analysis registry guarantees

- Unregistered tools, strict input, stale DatasetRefs, data access, kind, input limits, execution, input tampering, strict output and output limits each have a stable `ANALYSIS_TOOL_*` error code.
- All inputs are resolved through opaque DatasetRefs; user paths are never accepted.
- After the handler returns, input content hashes and metadata are re-validated to prevent violations of `mutates_input=false`.
- Every output contains lineage, a parameter hash, statistics and warnings; default feature/input/output limits are in `INTERFACES.md`.
- M07 uses the six M04 tools for plan previews only after a double check against a separate agent allow-list and their registration status; M07 does not execute registry tools.

## M04b DatasetRef spatial analysis tools

| Tool | Input contract | Output contract | Deterministic semantics | Main boundaries |
|---|---|---|---|---|
| `filter_attributes` | `FilterAttributesInput.v1` | `FilterAttributesOutput.v1` | all/any attribute conditions with eq/ne/comparisons/in/contains/is_null | Missing fields or incompatible types fail explicitly; input unchanged. |
| `reproject_dataset` | `ReprojectDatasetInput.v1` | `ReprojectDatasetOutput.v1` | Explicit EPSG transformation with pyproj, `always_xy` and `errcheck` | Invalid CRS/non-finite coordinates fail; CRS is never guessed. |
| `repair_geometry` | `RepairGeometryInput.v1` | `RepairGeometryOutput.v1` | Shapely `make_valid` on invalid geometries only | Never drops features; does not extract from GeometryCollections; any failure fails the whole call. |
| `buffer_features` | `BufferFeaturesInput.v1` | `BufferFeaturesOutput.v1` | Converts a metric distance to the native unit of a 2D projected CRS, then buffers | Geographic CRSs, non-2D or unreliable-unit CRSs and invalid geometries are rejected. |
| `nearest_features` | `NearestFeaturesInput.v1` | `NearestFeaturesOutput.v1` | At most K targets per source, stable ordering, one row per pair, distance in metres | Both input CRSs must be equivalent; when K exceeds the number of targets the actual number is returned. |
| `features_within` | `FeaturesWithinInput.v1` | `FeaturesWithinOutput.v1` | Strict within; boundaries do not match; left features are never duplicated | Both input CRSs must be equivalent; any invalid geometry fails. |
| `spatial_join` | `SpatialJoinInput.v1` | `SpatialJoinOutput.v1` | Inner join; intersects/within/contains; left geometry; ordered by left and right index | Attributes use `left_`/`right_` namespaces; conflicts fail explicitly. |
| `aggregate_by_region` | `AggregateByRegionInput.v1` | `AggregateByRegionOutput.v1` | Explicit region key; count/sum/mean/min/max; all regions kept | Zero matches give count=0 and null for the others; non-numeric/boolean values and field conflicts are rejected. |

### M04b derived-result guarantees

- Each tool's `ToolSpec` declares `produces_dataset=true`, `mutates_input=false` and default limits of 10,000 objects / 50,000,000 input bytes / 5,000,000 output bytes.
- Multi-input tools apply the object and input-byte limits cumulatively; the output byte limit applies to both the output contract and the actual GeoJSON file.
- Outputs are newly registered, verifiable DatasetRefs with a default 24-hour TTL; lineage contains all direct parent IDs, the operation, time and a parameter hash.
- `ANALYSIS_TOOL_REGISTRY` registers the two M04a quality tools and the eight M04b spatial tools; they are still not exposed to an LLM.

## M04c fixed-composition acceptance

- `reproject_dataset → buffer_features → spatial_join → aggregate_by_region` was run in sequence through `ANALYSIS_TOOL_REGISTRY`, without adding a composition executor or a public tool.
- The fixed reference verifies EPSG:4326→3857 coordinates, the area of a 75 m `quad_segs=8` buffer, stable join pairs and the final per-region count/sum/mean; final geometries are compared to stored GeoJSON with Hausdorff/area tolerances.
- Empty input propagates to a zero aggregation; buffering in metres directly on longitude/latitude, invalid geometries and cumulative resource overruns each fail at the corresponding stage without partial output.
- Two independent runs produce different IDs for the four derived DatasetRefs, but identical content hashes and file bytes at every stage; inputs stay unchanged, and derived lineage/TTL are verified.

## M05 SCB population adapter tool

| Tool | Registry | Input contract | Output contract | Deterministic semantics | Main boundaries |
|---|---|---|---|---|---|
| `load_population_dataset` | `POPULATION_TOOL_REGISTRY` | `LoadPopulationDatasetInput.v1` | `LoadPopulationDatasetOutput.v1` | Validates and standardizes a registered SCB 2025 population grid; keeps core, sex and five-year age fields | Accepts only DatasetRefs and EPSG:3006; no network access and no automatic reprojection; new temporary DatasetRef, direct-parent lineage, input unchanged. |

### M05 download and reference-composition boundaries

- `scripts/fetch_scb_population_grid.py` is a separate, explicit network entry point, not a registered analysis tool; it pins the WFS version/layer/CRS/attributes/ordering, writes a receipt and refuses to overwrite by default.
- Stable download-layer errors are `POPULATION_ADAPTER_DOWNLOAD_FAILED` and `POPULATION_ADAPTER_RESPONSE_INVALID`; the tool layer adds `ANALYSIS_TOOL_POPULATION_SCHEMA_INVALID` and `ANALYSIS_TOOL_POPULATION_VALUE_INVALID`.
- `run_fully_contained_grid_population()` deterministically combines buffer, contains/intersects joins and aggregation, reporting fully contained cells, excluded partially intersecting cells and the summed population. It is not an LLM-selectable tool, and the metric is explicitly a conservative lower bound rather than an area-weighted population.
- `POPULATION_TOOL_REGISTRY` has 11 tools; `ANALYSIS_TOOL_REGISTRY` keeps the 10 M04 tools and `QUALITY_TOOL_REGISTRY` keeps 2.

## Planned but not implemented

DeSO/PxWeb, area-weighted population, full GTFS transfer routing/isochrones and result-presentation tools are not available; M06b stop frequency and buffer coverage are available; M04/M05 tools are not yet exposed to dynamic LLM selection.

## M03 offline knowledge-base entry points (not runtime-registered tools)

| Entry point | Input/output | Boundaries |
|---|---|---|
| `scripts/fetch_osm_wiki_knowledge.py` | Frozen candidates → raw data from official tag pages at pinned `oldid`s + attribution/manifest | The only M03 network page-fetching entry point; official Wiki allow-list only; network, HTTP, licence and schema errors are layered; nothing is published on failure. |
| `scripts/cache_m03_embedding_model.py` | Pinned model ID/revision → model cache outside the repository + file-hash receipt | The only explicit network entry point for model downloads; offline builds require `local_files_only` and per-file verification. |
| `scripts/build_osm_knowledge.py` | Candidates/raw/curation → strict catalog + 200 documents | Offline by default, pinned input SHAs, atomic version directories and refusal to overwrite. |
| `scripts/build_m03_indexes.py` and `write_m03_faiss_index.py` | The same 200 documents + pinned local model → FAISS/BM25/hybrid configuration | Dense 384-dimension normalized `IndexFlatIP`; CJK tokenizer; FAISS written in a separate process; a failure only cleans up this run's staging. |
| `scripts/build_m03_queries.py` and `benchmarks/run_m03_retrieval.py` | Frozen catalog → 200 dev/100 public_eval; three-way ablation, error examples, receipt | Queries are separate from knowledge; `public_eval` runs once after the configuration is frozen, and results cannot be overwritten. |
| `scripts/audit_m03_reproducibility.py` and `publish_m03_phase1.py` | Pinned inputs → byte-level repeated builds; sealing of the complete publication | The publication gate checks 100/200/200 and every key manifest/receipt hash. |

These are explicitly executed build and evaluation commands. They are not added to `src.agent.tools.DEFAULT_TOOL_REGISTRY`, the M04/M05 AnalysisToolRegistry or the M07 planner. The old HTTP/Workflow tool behaviour is unchanged.

## M06a GTFS offline data entry points (not runtime-registered tools)

| Entry point | Input/output | Boundaries |
|---|---|---|
| `scripts/fetch_gtfs_sverige2.py` | Pinned official historical URL → ZIP outside the repository and a download receipt | The only explicit M06a network entry point; pinned length, ETag, SHA-256 and Content-Type; same-disk staging, no overwriting; offline tests mock the network. |
| `scripts/build_gtfs_sverige2_fixture.py` | Verified complete ZIP/receipt → small Lund ZIP, README, manifest | Fully offline; 5 selected trips with their complete stop_times and reference chains; stable ordering/ZIP and per-file hashes; no overwriting. |
| `src.gtfs.ingest` | Bounded GTFS ZIP → validated GtfsFeed, service-date queries | Parses/filters service only; not an M06b statistics or transfer-routing tool. |
| `LocalDatasetRegistry.register_file(network/gtfs)` | Existing bounded GTFS ZIP → standard DatasetRef v1 | Stop count, fields, bbox, content hash; full tables are parsed by the dedicated parser after `resolve_path`. |

No GTFS analysis tool is registered in `src.agent.tools.DEFAULT_TOOL_REGISTRY`, AnalysisToolRegistry or the planner. Frequency, coverage and spatial aggregation tools are introduced only in M06b.

## M06b DatasetRef stop service and coverage tool

| Tool | Registry | Input/output contract | Deterministic semantics | Main boundaries |
|---|---|---|---|---|
| `analyze_gtfs_stop_coverage` | `ACCESSIBILITY_TOOL_REGISTRY` | `AnalyzeGtfsStopCoverageInput/Output.v1` | GTFS `service_date` + ordered time windows; regular-boarding stop-event/route frequencies; EPSG:3006 stop buffers; fully-contained-cell population lower bound and unique POIs over the union; differences relative to the first window; new temporary vector DatasetRef | Offline, no routing/isochrones; warning when the population snapshot date differs; GTFS completeness unknown; at most 5000 buffers, default tool resource limits. |

`ACCESSIBILITY_TOOL_REGISTRY` has 12 tools (10 from M04, 1 from M05, 1 from M06b); the existing `ANALYSIS_TOOL_REGISTRY` (10) and `POPULATION_TOOL_REGISTRY` (11) keep their behaviour, and the old workflow registry is unchanged. `scripts/run_m06b_reference.py` only runs fixed synthetic cases that can be re-checked offline; it is not an estimate of real Swedish transit supply and is not registered as an agent-selectable tool.

## M03 Phase 2 offline knowledge-build boundaries (historical implementation record)

- M03 Phase 2 only extends the knowledge contracts and the fetch/offline index/evaluation commands; it registers no new agent or GIS ToolSpec and does not change the M04/M05/M06 tool registries. The pinned model and BM25 tokenizer are carried over from Phase 1; the isolated FAISS writer can accept the document count from a release manifest, with the Phase 1 default still 200.
- The four redirect-only raw pages were rejected by the fetcher and the raw validator; the approved key-page exception went into a separate raw v2, and the Phase 2 indexes are published. M07 had not started at that stage; M07 is now complete, and full transit transfer routing is still not implemented.

## M03 Phase 2 offline publication entry points (2026-10-06)

| Entry point | Input/output | Boundaries |
|---|---|---|
| `scripts/fetch_m03_phase2_sources.py` | Frozen 205-source mapping → raw v2 with source/licence/per-value evidence | Requests only the two key pages at the `oldid`s approved by the user, reusing the 201 reviewed tag pages; refuses overwriting and redirect-only pages. |
| `scripts/build_m03_phase2_catalog.py` | Candidates, source map, raw v2, Phase 1 catalog, new curation TSV → 205-entry v3 catalog, 410 bilingual documents | All input hashes pinned; the old v2 directory is never written. |
| `scripts/build_m03_indexes.py` | 410 v3 documents + pinned local model → dense/BM25/hybrid | 384-dimension normalized `IndexFlatIP` and tokenizer v1; the first repeated build in the default threading environment matched 7/7 file hashes; across threading environments one vector row differs in its last digit, see the separate audit evidence. |
| `scripts/build_m03_phase2_queries.py` | v3 catalog + frozen candidates → dev 410/public_eval 205 | 615 unique texts, coverage of every tag and cohort, source notes and leakage prevention; result directories cannot be overwritten. |
| `benchmarks/run_m03_retrieval.py` | Frozen queries/indexes/configuration → three-way metrics, predictions, errors and a public receipt | `public_eval` only once; the configuration `benchmarks/config/m03_phase2_public_eval_v1.json` is frozen; not a hidden set. |

These are offline knowledge-pipeline commands; they are not registered in the agent/GIS tool tables, and they did not start M07 or transit transfer routing.

## M07 agent public tool scope

- Public and required to stay registered: `filter_attributes`, `buffer_features`, `nearest_features`, `features_within`, `spatial_join`, `aggregate_by_region`.
- Not public: `repair_geometry`, `reproject_dataset`, `inspect_dataset_schema`, `validate_geometry`, population and GTFS tools, and the path/in-process tools of the old workflow. When CRSs differ, M07 rejects explicitly and never inserts a reprojection silently.
- ToolSpec v1.0 is unchanged; M07's PlannedToolStep v1.0 stores role references and checked scalar parameters, never arbitrary DatasetRefs/paths. Each step declares at most one retry, and M07 actually generates 0; tool execution waits for a separate execution boundary.

## M08 runtime tool permissions

- Plan steps still allow only the six public M07 tools: `filter_attributes`, `buffer_features`, `nearest_features`, `features_within`, `spatial_join`, `aggregate_by_region`, and at execution time they must exist in M04 `ANALYSIS_TOOL_REGISTRY`. M08 does not widen the agent-selectable tools or change ToolSpec.
- `validate_geometry` is called internally by the deterministic executor in every step's input/output checks; `reproject_dataset` and `repair_geometry` are called internally by the executor only when a ValidationPolicy explicitly authorizes them. None of them is exposed to the model. Substitute sources can only be DatasetRefs already in the policy and verified by the DatasetRegistry; there is currently no semantically equivalent public substitute analysis tool, so no tool switch is faked.
- The total tool-call cap is 24; a timeout with identical parameters is retried at most once, repairs and substitutions happen once per role, and replanning once overall. The old workflow, population and GTFS registries are unchanged.

## M09 session execution and tool reuse

- No new agent-selectable tools: the six public M04 tools of M07 and their registration gate are unchanged. M09's restricted exclusion conditions use the existing `ne` operator of `filter_attributes`; spatial-join fields map to M04's `left_` output prefix.
- The cache stores only intermediate DatasetRefs from M08 success runs. A step fingerprint consists of the tool name, deterministic parameters, input roles, input IDs and content hashes; at execution, M08 re-checks the registry's input contract and the current output constraints. A reuse does not count as an analysis-tool call (`StepExecution attempts=0`), while quality-check tools still run. When a data source or step parameter changes, the affected downstream steps are recomputed.
- M09 does not expose `repair_geometry`, `reproject_dataset`, population, GTFS or old-workflow path tools to the model; there are no real transit isochrones.

## M10 presentation-layer tool boundaries

- M10 registers no new ToolSpec and does not widen the six public M04 agent tools of M07. It consumes only M07 plans, M08 success validation traces and the current DatasetRegistry; reports/maps/charts do not replace the results of analysis-tool execution.
- `src.agent.result_presentation` and `result_html` are pure presentation and evidence-validation entry points; `/analysis-ui` only serves a fixed, non-overwritable offline example. `partial` results, stale DatasetRefs, forged metrics/downloads and unchecked sources are all rejected. GeoJSON/CSV are auditable data URIs, and arbitrary local paths are never read. Stop buffers must not be labelled as real transit isochrones.

## M11b scorer and tool permissions

- M11b adds offline evaluation build/scoring code but no AnalysisToolRegistry or agent ToolRegistry entries. Reference tasks call only the existing M04 allow-list; tool names in predictions are only read and scored, never executed.
- Dev references use the six tools `filter_attributes`, `buffer_features`, `nearest_features`, `features_within`, `spatial_join`, `aggregate_by_region`. M04 repair/reproject and other contracts and the six M07 agent public permissions are unchanged; rank still has no deterministic tool.
- Tool sequences are scored exactly by full name and order; semantic steps are scored separately as an ordered subsequence. Private test data live only in an ignored directory; system traces are connected only in M11c, and M11b adds no runtime permissions.

- M11b also publishes a manifest and receipt of real per-step M04 call hashes without random DatasetRef IDs/timestamps; each step records the tool version, input/parameter/output hashes and row count, and can be rebuilt from the existing tools.

## M11c baseline tool boundaries

- No new AnalysisToolRegistry or agent ToolRegistry entries. `dynamic_agent` and `validator_*` execute only plans that passed the M07 static gate (the six public tools); the validator additionally calls `validate_geometry` per M08 (counted in `tool_calls`, not written to `tool_sequence`). FW-A/FW-B call only the five existing M04 tools `filter_attributes`, `nearest_features`, `features_within`, `spatial_join`, `aggregate_by_region`, without the M07 static gate.
- All configurations use the same M04 registry, the same split data catalogue and the same result encoder; systems never receive task types, capability labels or reference answers. M07 m07.2 only adds planning sentence patterns and does not widen tool permissions. Rank has no deterministic tool; single-dataset buffers are executed in no M11c configuration.

## M11c v2 tool and model boundaries

- No new M04 analysis tools or agent tools. The local qwen2.5:3b only outputs a semantic frame; it calls no tools and produces no spatial results. Tool permissions, the M07 static gate and M08 validation of the seven configurations are the same as in v1. The model is called only from `benchmarks.run_m11c_v2` through Ollama on 127.0.0.1, with the frozen configuration in `docs/roadmap/M11C_V2_STAGE2_FREEZE.md`; offline tests use only a mocked transport.

## M12 scenario registry and tool boundaries (ADR-0021; m12a, 2026-10-08)

- `SCENARIO_TOOL_REGISTRY` currently equals the 12 tools of `ACCESSIBILITY_TOOL_REGISTRY`; m12a adds no analysis tools. Tools usable in scenario plans (`PLANNABLE_TOOLS`): `load_population_dataset`, `reproject_dataset`, `filter_attributes`, `buffer_features`, `nearest_features`, `features_within`, `spatial_join`, `aggregate_by_region`. `validate_geometry` is called only internally by the scenario executor (counted toward the 64-call cap, not in `invoked_tools`); `repair_geometry` and `inspect_dataset_schema` do not enter scenario plans; `analyze_gtfs_stop_coverage` will be connected once m12b adds its payload mapping. `rank_candidates` (ADR-0022) and `analyze_transit_single_ride_reach` (ADR-0023) are not implemented yet.
- `reproject_dataset` is inserted explicitly by the planner, and explained in the preview, only after the catalogue-declared source CRS passes the coordinate plausibility check (reconfirmed by a trusted manifest where needed); the scenario executor never calls reprojection or repair on wrongly declared data.
- The six M07 agent public tools, the ANALYSIS (10)/POPULATION (11)/ACCESSIBILITY (12) registries and the old workflow registry are unchanged. `scripts/build_m12_lund_data.py` (POI extraction with Python osmium, SCB fixture publication, pre-check gate) is an offline data-build script, not a runtime tool.
