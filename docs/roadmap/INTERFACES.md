# Stable public interfaces

> Records the public boundaries that actually exist in the current code. Planned interfaces must not be described as "available" before they are implemented and accepted.

## Versioning and compatibility principles

- Current application interface baseline: P5 delivery / P4 workflow, recorded 2026-10-05.
- Pydantic contracts default to `extra="forbid"`; unknown fields must be rejected.
- The conversion boundary between GeoDSL v1 and the old `WorkflowPlan` is stable; later changes must stay version-compatible and must not change old behaviour.
- Breaking changes require an ADR, a migration layer and regression tests first.

## HTTP interface

| Method and path | Request contract | Success contract | Notes |
|---|---|---|---|
| `GET /status` | none | JSON status object | Reports availability of local dependencies and configuration. |
| `POST /chat` | `src.contracts.ChatRequest` | `ChatSuccessResponse` | Single-tag RAG + local LLM; failures are `ErrorResponse`. |
| `POST /chat_simple` | `SimpleChatRequest` | `SimpleChatSuccessResponse` | Explicit place and single tag, no LLM call. |
| `POST /workflow` | `src.agent.contracts.WorkflowRequest` | `WorkflowSuccessResponse` | Restricted natural-language OR, or explicit union/intersection. |
| `GET /output/<filename>` | path parameter | GeoJSON file | Serves only result files inside the output directory. |
| `GET /ui` | none | HTML | The current Leaflet demo interface. |

## Python interface

| Location | Interface | Current responsibility |
|---|---|---|
| `src/pipeline.py` | `run_query()` | End-to-end single-tag natural-language execution. |
| `src/pipeline.py` | `run_query_without_llm()` | Deterministic single-tag debugging entry point. |
| `src/agent/planner.py` | `compile_natural_language_plan()` | Compiles explicit English OR queries into a `WorkflowPlan`. |
| `src/agent/planner.py` | `build_workflow_plan()` | Builds the fixed workflow from a validated explicit intent. |
| `src/agent/workflow.py` | `run_agent_workflow()` | Executes plans in order, records steps, bounded retries. |
| `src/agent/tools.py` | `ToolRegistry.invoke()` | Allow-list, parameter-key validation, output-key validation and retries. |
| `src/rag/hybrid_retriever.py` | `HybridRetriever.retrieve()` | Tag-level hybrid retrieval with dense + BM25. |
| `src/osm/geocode.py` | `geocode_to_bbox()` | Nominatim geocoding. |
| `src/osm/extractor.py` | `extract_features_to_geojson()` | Extracts nodes/ways/relations from the clipped PBF. |

## Current workflow contract v1.0

- `WorkflowIntent`: `place`, `operation` (`union`/`intersection`), 2–5 unique OSM tags, `source`.
- `WorkflowStep`: a valid `step_id`, an allow-listed tool name, dependencies only on steps that already appeared, parameters, and a `none/retry_once` recovery policy.
- `WorkflowPlan`: fixed version `1.0`, at most 16 steps; duplicate steps and forward dependencies are forbidden.
- `StepRecord`: `pending/running/succeeded/failed/skipped`, attempt count, recovery flag, duration, summary and a stable error.

## GeoDSL contract v1.0

Implementation: `src/geodsl/contracts.py`; public exports: `src/geodsl/__init__.py`.

- Core models: `GeoTaskIntent`, `PlaceReference`, `DatasetRequirement`, `AttributeFilter`, `SpatialPredicate`, `MetricDefinition`, `SortDefinition`, `OutputRequirement`, `ClarificationRequest`, `GeoAnalysisStep`, `GeoAnalysisPlan`.
- Supported operations: `search`, `within_distance`, `nearest`, `within`, `spatial_join`, `aggregate_by_region`, `rank`.
- All models use Pydantic strict mode and `extra="forbid"`.
- The schema version of `GeoTaskIntent`, `ClarificationRequest` and `GeoAnalysisPlan` is fixed at `1.0`.
- Place, data requirements, analysis and output CRS all use explicit `EPSG:<code>`; CRSs are never guessed silently.
- Distance input is `{value, unit}` with `m/km/mi/ft`, converted to standard metres through a read-only `meters` property; serialization keeps the original value and unit.
- Plan validation: step dependencies may only point to earlier steps, input data must already exist, and output data roles must not overwrite existing roles.
- `src.geodsl.adapters.workflow_plan_to_geodsl()` converts the old `WorkflowPlan` one way into the GeoDSL `search` subset; union maps to `filter_logic="any"` and intersection to `"all"`.
- The adapter does not modify old plans and does not change the existing `/workflow` HTTP or execution behaviour.

## Dataset registry contract v1.0

Implementation: `src/datasets/`; public exports: `src/datasets/__init__.py`.

- `DatasetRef` fields: `id`, `kind`, `format`, `storage_uri`, `schema_version`, `crs`, `bbox`, `row_count`, `source`, `source_version`, `created_at`, `content_hash`, `size_bytes`, `fields`, `lineage`, `lifecycle`, `expires_at`.
- The dataset ID is `ds_<32 hex>` and is only an opaque identifier; it must never be interpreted as a file path.
- `storage_uri` is fixed to `registry://datasets/<id>/data.<format>`; arbitrary local paths are neither exposed nor accepted.
- The `DatasetRegistry` Protocol defines registration, reading, metadata queries, controlled path resolution, release and expiry cleanup.
- `register_file(..., lineage=None)` supports optional derived lineage; without it the original `register` lineage is produced, so old calls behave the same.
- `LocalDatasetRegistry` copies the source file into its own root at registration and publishes atomically after staging validation; deleting the source file later does not affect the registered data.
- Every read verifies file existence, byte size and SHA-256 by default; missing or tampered metadata, URIs or content fall into stable error categories.
- The current local implementation registers only `vector/geojson` and `table/csv`; the contract reserves `raster/geotiff` and `network/gtfs`, but local registration returns an explicit unsupported-format error for them.
- Temporary data must declare a TTL of 1–604800 seconds explicitly and can be released manually with `release()` or cleaned up by `cleanup_expired()`; persistent data cannot be deleted through this release interface.

## Benchmark contract v1.0 (M11a)

Implementation: `src/evaluation/`; offline entry point: `python -m benchmarks.run_m11a_benchmark`.

- `BenchmarkTask` fixes the task ID, `dev/test` split, difficulty, category, GeoDSL operation, random seed, data version and `TaskExpectation`.
- `BenchmarkPrediction` records result status, operation, semantic steps, exact fields, feature IDs, numbers, ranking, bbox, reason code, duration, tool/LLM calls and estimated cost.
- `StepRecord` and `BenchmarkTrace` provide a versioned, timezone-aware execution-trace contract with consecutive step numbers; the task/status of the trace and the final prediction must agree.
- The `TaskScorer` Protocol allows composed scorers; M11a has built-in outcome, operation, exact-field, required-step LCS, Feature ID P/R/F1, numeric absolute/relative tolerance, bbox IoU and ranking nDCG scorers.
- `BenchmarkRunReport` records per-task component scores, failure categories, and summaries of mean, standard deviation, P50/P95, call volume and cost.
- Run metadata records the code commit/dirty state, random seed, data version, model, prompt, input hashes and configuration hash; a run directory with the same name is never overwritten.
- M11a `required_steps` are semantic step labels, not M04 registered tool names; real tool-sequence scoring is connected only after the M04 contracts are stable.
- The current 24 public dev tasks and the `fixture_calibration_v1` predictions only calibrate the framework; they are not dynamic-agent or hidden-set results.

## DatasetRef analysis tool contract v1.0 (M04a)

Implementation: `src/tools/`; default quality registry: `QUALITY_TOOL_REGISTRY`.

- `ToolSpec` declares the tool name/version, input/output contracts, supported data kinds, whether it produces data, determinism, immutability and `ToolLimits`.
- `AnalysisToolRegistry.invoke()` accepts only allow-listed tools; before the call it checks strict input, the current DatasetRef state, kind, object count and input bytes, and after the call it checks that inputs are unchanged, the strict output and output bytes.
- New tools accept only `DatasetRef`s, never arbitrary local paths or large in-process GeoDataFrames.
- `AnalysisToolMetadata` contains `DatasetLineage`, statistics and warnings; parameters are recorded as the SHA-256 of canonical JSON.
- `inspect_dataset_schema` supports metadata checks for the vector/table/raster/network kinds in the contract; the current local registry still has only GeoJSON/CSV backends.
- `validate_geometry` supports only vector/GeoJSON and checks topology with Shapely 2; it returns valid/invalid/empty geometry counts, geometry type counts and 0–1000 issues at most, and never repairs the input.
- Default limits: 10,000 features, 50,000,000 input bytes, 5,000,000 output bytes.
- `src.agent.tools.ToolSpec/ToolRegistry` keeps serving the old workflow; it is an explicit compatibility isolation layer from `src.tools.ToolSpec/AnalysisToolRegistry`, and M04a did not migrate it.

## M04b core spatial analysis contract v1.0

Implementation: `src/tools/spatial_contracts.py`, `src/tools/spatial.py`; combined registry: `ANALYSIS_TOOL_REGISTRY`.

- Eight new strict, versioned tool contracts: `filter_attributes`, `reproject_dataset`, `repair_geometry`, `buffer_features`, `nearest_features`, `features_within`, `spatial_join`, `aggregate_by_region`.
- Every data-producing tool returns a new, registered temporary DatasetRef with a default TTL of 86400 seconds. The DatasetRef and the output metadata record the same operation, all direct parent IDs, the UTC time and the SHA-256 of the canonical parameters.
- `AnalysisToolRegistry` checks the current state/kind of every input DatasetRef, accumulates object counts and input bytes, and verifies immutability of each input after execution. Derived outputs must use a new ID, be verifiable, carry all direct-parent lineage, and respect both the result contract and the file byte limit.
- CRSs are parsed and compared for equivalence with pyproj; two-input tools reject non-equivalent CRSs. Reprojection is fixed to `always_xy=True` and `errcheck=True` and rejects non-finite results.
- buffer/nearest accept only a confirmable two-dimensional projected CRS; both horizontal axes must have the same finite conversion factor to metres. Longitude/latitude CRSs are rejected explicitly; linear projected units such as feet are first converted from metres to the native unit, and nearest then outputs metres.
- `repair_geometry` calls Shapely `make_valid` only on invalid geometries; it never drops features or extracts GeometryCollection subtypes. If a repair fails, is empty, is still invalid or cannot be serialized, the whole tool fails.
- nearest returns at most K targets per source feature, one row per matched pair, sorted stably by distance and target ID/index, using the source geometry and outputting namespaced attributes from both sides.
- within uses strict Shapely `within`: boundaries do not match, and each left feature is output at most once. Spatial join is always an inner join supporting only `intersects/within/contains`, using the left geometry and `left_`/`right_` attribute namespaces.
- aggregate uses the original region dataset, an already joined values dataset and an explicit region key; it supports count/sum/mean/min/max, keeps regions with zero matches, ignores missing/null numbers, rejects booleans and other non-numeric values, and outputs deterministic `agg_*` fields.
- The eight tools are not connected to an LLM yet; M04c verified the deterministic four-step composition, and M07 dynamic selection was not yet implemented.

## M04c composition compatibility evidence

- M04c adds no public interfaces, contracts, error codes, dependencies or composition executor; existing callers keep calling `AnalysisToolRegistry.invoke()` one tool at a time.
- The fixed chain `reproject_dataset → buffer_features → spatial_join → aggregate_by_region` was accepted against stored reference GeoJSON, parsed intermediate values and explicit spatial tolerances.
- Acceptance covers new DatasetRefs at every stage, direct-parent lineage, 24-hour TTL, input immutability, no partial output from failing stages, cumulative resource limits and content-level consistency across two runs.

## M05 SCB population grid adapter contract v1.0

Implementation: `src/population/`, `src/tools/population.py`, `src/tools/population_contracts.py`; combined registry: `POPULATION_TOOL_REGISTRY`.

- `ScbPopulationGridRequest.v1` pins SCB WFS 1.1.0, `stat:befolkning_1km_2025`, EPSG:3006, GeoJSON, `sortBy=objectid` and an explicit attribute list; callers provide only a finite EPSG:3006 BBOX with positive area.
- `download_scb_population_grid()` is a separate network boundary returning bytes and a `PopulationDownloadReceipt.v1`; the receipt contains the canonical URL, schema URL, UTC time, HTTP metadata, feature count, reference date and two SHA-256 values. Default offline tests use an injected transport and never go online.
- The `load_population_dataset` input is `LoadPopulationDatasetInput.v1`: a registered vector DatasetRef, `provider=scb`, `dataset_year=2025` and an output TTL of 1–604800 seconds; URLs, paths and GeoDataFrames are not accepted.
- The CRS declared in the input GeoJSON, the DatasetRef CRS and the target CRS must all equal EPSG:3006 according to pyproj; a mismatch fails explicitly, with no automatic reprojection.
- The output is `LoadPopulationDatasetOutput.v1`, containing a new DatasetRef, source/layer/year, field-mapping version `scb-population-grid-2025-v1`, adapter `1.0`, feature/status counts and AnalysisToolMetadata.
- Standard fields: `grid_id`, `source_grid_id_scb`, `population_reference_date`, `population_total`, `population_status`, `population_male`, `population_female` and `population_age_0_4` through `population_age_100_plus`.
- 0 stays the integer 0; null, missing or supported suppression tokens are normalized to null; the status is only `reported|missing_or_suppressed`. Sex/age breakdowns that do not equal the total only produce a disclosure-control warning.
- The output is a new temporary DatasetRef with a default TTL of 86400 seconds; lineage records the direct parent ID, operation, UTC time and a parameter hash covering the layer/mapping/adapter versions; the input is unchanged.
- `POPULATION_TOOL_REGISTRY` contains the 10 M04 tools and the 1 M05 tool. The existing `ANALYSIS_TOOL_REGISTRY` still has 10 tools and `QUALITY_TOOL_REGISTRY` 2; the old workflow registry is unchanged.
- `run_fully_contained_grid_population()` is a deterministic reference composition, not a registered tool: it outputs the number of fully contained cells, the number of intersecting but excluded cells, the summed population and a mandatory conservative-lower-bound warning.

## Stable error families

- Request/API: `INVALID_REQUEST`, `NOT_FOUND`, `INTERNAL_ERROR`.
- Planning: `PLAN_QUERY_EMPTY`, `PLAN_PLACE_MISSING`, `PLAN_OPERATION_AMBIGUOUS`, `PLAN_OPERATION_UNSUPPORTED`, `PLAN_FILTERS_INVALID`, `PLAN_TOO_MANY_FILTERS`, `PLAN_TAG_NOT_FOUND`, `PLAN_SCHEMA_INVALID`, `PLAN_REFERENCE_INVALID`.
- Tools: `TOOL_NOT_REGISTERED`, `TOOL_INPUT_INVALID`, `TOOL_OUTPUT_INVALID`, `TOOL_<NAME>_FAILED`.
- DatasetRef analysis tools: `ANALYSIS_TOOL_NOT_REGISTERED`, `ANALYSIS_TOOL_INPUT_INVALID`, `ANALYSIS_TOOL_DATASET_ACCESS_FAILED`, `ANALYSIS_TOOL_DATASET_REF_STALE`, `ANALYSIS_TOOL_DATASET_KIND_UNSUPPORTED`, `ANALYSIS_TOOL_LIMIT_EXCEEDED`, `ANALYSIS_TOOL_EXECUTION_FAILED`, `ANALYSIS_TOOL_INPUT_MUTATED`, `ANALYSIS_TOOL_OUTPUT_INVALID`, `ANALYSIS_TOOL_OUTPUT_LIMIT_EXCEEDED`.
- M04b specific analysis errors: `ANALYSIS_TOOL_CRS_INVALID`, `ANALYSIS_TOOL_CRS_MISMATCH`, `ANALYSIS_TOOL_GEOGRAPHIC_DISTANCE_UNSUPPORTED`, `ANALYSIS_TOOL_CRS_UNIT_UNSUPPORTED`, `ANALYSIS_TOOL_FIELD_MISSING`, `ANALYSIS_TOOL_FIELD_TYPE_INVALID`, `ANALYSIS_TOOL_GEOMETRY_REPAIR_FAILED`, `ANALYSIS_TOOL_GEOMETRY_INVALID`.
- M05 download errors: `POPULATION_ADAPTER_DOWNLOAD_FAILED`, `POPULATION_ADAPTER_RESPONSE_INVALID`.
- M05 tool errors: `ANALYSIS_TOOL_POPULATION_SCHEMA_INVALID`, `ANALYSIS_TOOL_POPULATION_VALUE_INVALID`; CRS, geometry and resource limits keep reusing the existing analysis errors.
- Dependencies/pipeline: `RAG_RETRIEVAL_FAILED`, `LLM_PARSER_FAILED`, `NOMINATIM_TIMEOUT`, `NOMINATIM_UNAVAILABLE`, `PLACE_NOT_FOUND`, `OSM_DATA_MISSING`, `OSMIUM_TIMEOUT`, `OSM_EXTRACTION_FAILED`.
- Data registration: `DATASET_ID_INVALID`, `DATASET_SOURCE_MISSING`, `DATASET_FORMAT_UNSUPPORTED`, `DATASET_FORMAT_INVALID`, `DATASET_KIND_FORMAT_MISMATCH`, `DATASET_CRS_MISSING`, `DATASET_CRS_INVALID`, `DATASET_TTL_INVALID`, `DATASET_NOT_FOUND`, `DATASET_METADATA_INVALID`, `DATASET_STORAGE_VIOLATION`, `DATASET_CONTENT_MISSING`, `DATASET_CONTENT_HASH_MISMATCH`, `DATASET_NOT_TEMPORARY`, `DATASET_TIME_INVALID`, `DATASET_ID_COLLISION`.

## Not implemented yet; do not depend on these

- DeSO/PxWeb administrative joins, area-weighted population estimates and full GTFS transfer routing/isochrones. M06a ingestion and M06b stop frequency/buffer coverage are implemented.
- A local GeoTIFF registration and reading backend is not implemented; M06a implemented bounded local GTFS registration and stop reading.
- M07 plan execution is implemented by M08; M09 session state and M10 result presentation are implemented. Production cross-process session persistence and authentication are still not implemented.
- M10 AnalysisResult v1.0 is implemented, with the contract below; production paginated downloads and a general result-execution HTTP endpoint are not implemented.
- At the time of writing, M11b/M11c's formal real M04 trace adaptation, 150–200 tasks, hidden test and the fixed workflow/dynamic agent/validator comparison were not implemented (they are now, see the M11b and M11c sections below).

## M03 Phase 1 OSM knowledge contracts and offline retrieval (v2)

- Implementation: `src/knowledge/`; ADR-0008 freezes the public semantics. The four boundaries are `RawWikiSourceV1`, `KnowledgeCatalogV2/KnowledgeEntryV2`, `IndexDocumentV1` and `RetrievalQueryV1`, all strict, `extra=forbid`, versioned Pydantic contracts.
- `canonical_tag` is strictly `key=value`; `entry_id=osm-tag:<canonical_tag>`, `source_id=osm-wiki:<canonical_tag>@<oldid>`, `document_id=<entry_id>#<en|zh>`. Each entry has exactly one `primary_category` and may have several `related_categories`; source, licence, status, object/geometry types, compatible/related tags, English and Chinese names/aliases/summaries, and hard negatives with bilingual reasons are all fields.
- Catalog v2 is sorted by `canonical_tag`; sources, aliases, related categories/tags, hard negatives and documents are sorted by stable canonical keys. After NFKC, whitespace/case normalization and strict deduplication: UTF-8/LF, JSON object keys sorted, no extra whitespace, one LF per JSONL line; all file contents are hashed with SHA-256. Full definitions are in ADR-0008 and `src/knowledge/serialization.py`.
- Raw sources must be HTTPS URLs of official Wiki pages at pinned `oldid`s, recording request/canonical/history URLs, HTTP fetch information, body hash and per-page licence exceptions; a separate licence declaration blocks automatic ingestion. Knowledge-entry status allows a strictly enumerated `unknown`, but frozen candidates need manual review. Hard negatives may be asymmetric and must point to another tag in the same catalog.
- Two `IndexDocumentV1`s per tag (en, zh) are the only shared input of the three retrieval paths; `M03IndexBundle` loads and checks FAISS/BM25/metadata/document hashes and counts. The CJK tokenizer `m03-cjk-unigram-bigram-v1` produces deterministic Han code-point unigrams/bigrams, while Latin text keeps casefolding/light stemming; hybrid is a tag-level weighted RRF.
- The offline public retrieval entry point is `benchmarks/run_m03_retrieval.py`, which writes version directories and refuses to overwrite. `public_eval` requires a `status=frozen` configuration and a separate `O_EXCL` receipt; error analysis and language/category macro metrics are written to the result directory. This capability is not connected to the old HTTP/Workflow, the M07 planner or `src.agent.tools.ToolSpec/ToolRegistry`.
- Stable M03 error family: `OSM_KB_CANONICAL_TAG_INVALID/DUPLICATE`, `OSM_KB_ALIAS_DUPLICATE/CROSS_TAG_CONFLICT/CANONICAL_CONFLICT`, `OSM_KB_LANGUAGE_MISMATCH`, `OSM_KB_REQUIRED_TEXT_EMPTY`, `OSM_KB_SOURCE_URL_INVALID/SOURCE_VERSION_MISSING/SOURCE_SCHEMA_INVALID/SOURCE_CONTENT_HASH_MISMATCH`, `OSM_KB_LICENSE_MISSING/LICENSE_EXCEPTION`, `OSM_KB_OBJECT_TYPES_EMPTY`, `OSM_KB_STATUS_INVALID`, `OSM_KB_HARD_NEGATIVE_INVALID/SELF_REFERENCE/TARGET_UNKNOWN`, `OSM_KB_MANIFEST_HASH_MISMATCH`, `OSM_KB_INDEX_COUNT_MISMATCH/INDEX_METADATA_MISMATCH`, `OSM_KB_BUILD_INPUT_INVALID/BUILD_INPUT_VERSION_MISMATCH/RELEASE_COUNT_INVALID`, `OSM_KB_EVAL_QUERY_COUNT_INVALID`, `OSM_KB_PUBLIC_EVAL_ALREADY_RUN/PUBLIC_EVAL_CONFIG_NOT_FROZEN`, `OSM_KB_PUBLICATION_GATE_FAILED`.
- Network/transport, HTTP response, page/licence schema, normalization, build, index and evaluation failures each keep their own reason code; old interface error codes and old registry behaviour are unchanged.

## M06a GTFS Schedule ingestion contract v1

- The source is the pinned Trafiklab/Samtrafiken GTFS Sverige 2 historical archive of 2025-10-04 (CC0-1.0); network downloads happen only through `scripts/fetch_gtfs_sverige2.py` and offline builds only through `scripts/build_gtfs_sverige2_fixture.py`, both refusing to overwrite by default.
- `src.gtfs.ingest.load_gtfs_feed(path)` returns a `GtfsFeed` (a frozen dataclass shell whose internal record dictionaries are not deeply immutable) containing the agency timezone, feed version, agencies, stops, routes, trips, stop_times, calendar, calendar_dates and the WGS84 bbox; when needed, the controlled ZIP is re-parsed through `registry.resolve_path(ref.id)`. `GtfsFeed` is an internal read-only M06a structure and does not replace the public DatasetRef v1.
- `parse_gtfs_time(HH:MM:SS)` returns seconds from the start of the service day and may exceed 86400; `gtfs_local_datetime(service_date, time, timezone)` keeps the original service-date semantics and returns a timezone-aware time. `active_service_ids` and `active_trip_ids` merge the weekly calendar with calendar_dates (1 = add, 2 = remove); without valid service they return an empty tuple.
- `LocalDatasetRegistry` adds `network/gtfs` registration for `.zip/.gtfs`; it accepts only EPSG:4326, copies into same-disk staging, and validates required files, schema, unique keys, foreign keys, coordinates, calendars and times before publishing. The output is still a strict DatasetRef v1 with `format=gtfs`, canonical URI `data.gtfs`, `row_count` = number of stops, `fields`/`read()` describing stop rows, and a bbox from all stop coordinates. A failure leaves no partial dataset.
- Bounded limits: 20,000,000 bytes compressed, 50,000,000 bytes uncompressed in total, 30,000,000 bytes per member, at most 50 members; path traversal, encryption and non-text members are rejected. The full national archive cannot be registered directly at present.
- Stable parsing errors: `GTFS_ARCHIVE_INVALID`, `GTFS_LIMIT_EXCEEDED`, `GTFS_REQUIRED_FILE_MISSING`, `GTFS_SCHEMA_INVALID`, `GTFS_FIELD_MISSING`, `GTFS_DUPLICATE_ID`, `GTFS_FOREIGN_KEY_MISSING`, `GTFS_COORDINATE_INVALID`, `GTFS_TIME_INVALID`, `GTFS_DATE_INVALID`, `GTFS_TIMEZONE_INVALID`. The download boundary also uses `GTFS_SOURCE_EXISTS`, `GTFS_HTTP_RESPONSE_INVALID`, `GTFS_DOWNLOAD_FAILED`, `GTFS_SOURCE_HASH_MISMATCH`. The registry keeps GTFS parsing error codes and does not downgrade them to `DATASET_METADATA_INVALID`.
- This stage registers no new AnalysisToolRegistry/old workflow tools; M06b frequencies, buffers, population/POI aggregation and routing were not implemented yet. The old DatasetRef GeoJSON/CSV, HTTP/Workflow and M04/M05 behaviour is unchanged. ADR-0009 freezes these boundaries.

## M06b stop frequency and buffer coverage contract v1

- A separate `ACCESSIBILITY_TOOL_REGISTRY` = the 10 M04 tools + the 1 M05 tool + `analyze_gtfs_stop_coverage`; the old `ANALYSIS_TOOL_REGISTRY`, `POPULATION_TOOL_REGISTRY` and `src.agent.tools.DEFAULT_TOOL_REGISTRY` are unchanged, and the M07 dynamic planner is not connected.
- Strict Pydantic v1: `GtfsTimeWindow`, `AnalyzeGtfsStopCoverageInput/Output`, `GtfsStopFrequency`, `GtfsRouteFrequency`, `GtfsWindowSummary`, `GtfsWindowComparison`, `extra=forbid`. The input must be a registered `network/gtfs` DatasetRef, an explicit `service_date` and a time zone equal to the feed's agency timezone, with 1–8 non-overlapping half-open time windows ordered by start; times are GTFS `HH:MM:SS` from the start of the service day (24+ allowed, end below 48:00:00), not real elapsed UTC time.
- Regular service frequency counts the departure events of active trips in stop_times with `pickup_type=0`/empty; 1/2/3 are excluded, and 2/3 produce a warning. Stops and routes each record departure event counts, unique trips and the related routes/stops. Next-day 24:xx still belongs to the original `service_date`; without service, zero statistics and an empty output DatasetRef are returned.
- GTFS EPSG:4326 stops are projected to EPSG:3006 with pyproj `always_xy`, and only stops with service in the window get metric buffers. The result GeoJSON has one feature per stop per window, ordered by requested window and then `stop_id`; buffers are not walking or public-transport isochrones.
- Optional population/POI inputs must be `vector/geojson` in EPSG:3006 with the same CRS declared in the file. Population needs unique `grid_id`, non-negative int/null `population_total`, an ISO `population_reference_date` and valid Polygon/MultiPolygon; POIs need a unique `poi_id` and a valid Point. Deduplication is per window over the buffer union: the population of fully contained cells is a conservative lower bound of known values, partially intersecting cells are excluded and listed separately, and null cells are listed with a warning; a POI inside or on the boundary of the union is counted once. Without the optional data the metrics are null; with data but zero coverage they are 0.
- Multi-window comparison uses the first window as the baseline and outputs differences in departures, active stops, routes, population lower bound and POIs. The output is a new temporary EPSG:3006 `vector/geojson` DatasetRef v1 whose lineage records all input parent IDs, the parameter SHA-256 and the TTL; the same inputs give stable output file bytes/hashes. At most 5000 buffer features, still subject to the tool registry's input/output limits.
- New stable errors: `ANALYSIS_TOOL_GTFS_TIMEZONE_MISMATCH`, `ANALYSIS_TOOL_GTFS_PICKUP_INVALID`, `ANALYSIS_TOOL_GTFS_POPULATION_INVALID`, `ANALYSIS_TOOL_GTFS_POI_INVALID`; `ANALYSIS_TOOL_CRS_MISMATCH`, `ANALYSIS_TOOL_GEOMETRY_INVALID`, `ANALYSIS_TOOL_LIMIT_EXCEEDED` and others are reused. Mandatory warnings: feed completeness is unverified and buffers are not isochrones; a mismatched population snapshot time, unknown values, excluded conditional boarding and no service are added as applicable. ADR-0010 defines the semantics.

## M03 Phase 2 v3 knowledge contracts (in progress at the time)

- `KnowledgeCatalogV3/KnowledgeEntryV3` use `3.0` and `m03_phase2`; the primary categories add `accessibility` and `land_use` to the old eight, and `tag_role=feature|land_use|attribute` is new. An attribute must have an `AttributeApplicabilityV1` with `semantic_kind`, `applies_to_keys` and English and Chinese combination notes; `access=private` is fixed as `legal_access` and cannot be interpreted as `wheelchair_access`.
- `IndexDocumentV2` and `RetrievalQueryV2` use `2.0`; the latter records `tag_role` and the `phase1_retained|phase2_new` cohort. Strict parsing of the old `KnowledgeCatalogV2`, `IndexDocumentV1` and `RetrievalQueryV1` is not relaxed.
- Raw Wiki text is now rejected for redirect-only pages both at the fetcher and at raw validation; the rejected Phase 2 raw v1 cannot be used as catalog input. `parse_index_documents` checks the same version, ordered unique documents and two (en/zh) per tag; the Phase 2 offline index build checks at least 400 documents against the release manifest.
- `validate_phase2_query_sets` requires one English and one Chinese dev query for every tag, one public_eval query per tag, no duplicated text across splits and no leakage of non-alias target words, and computes metrics per target cohort. These contracts were used for the separately published Phase 2 catalog, indexes and queries. See ADR-0011/0012.

## M03 Phase 2 published interfaces and evaluation boundaries (2026-10-06)

- The v3 catalog's `release_phase=m03_phase2`, ten primary categories and `tag_role=feature|land_use|attribute` are implemented; attributes have `semantic_kind/applies_to_keys` and English and Chinese combination notes. `access=private` is a legal access restriction, `foot/bicycle=designated` is a mode permission and `sidewalk=both` means a sidewalk exists; none of them proves wheelchair accessibility. Parsing and assets of the old v2 catalog and v1 documents/queries stay strictly compatible.
- `RawWikiSourceV2` allows only the four approved key-page source exceptions and records the real page title/identity, pinned `oldid`, and hashes of the body and per-value excerpts; the value status is `unknown`, noting that the key page gives no separate status for the value. The other 201 tags keep official tag-page sources. The v1 source contract still accepts only tag pages.
- The new offline entry points `scripts/build_m03_phase2_catalog.py` and `scripts/build_m03_phase2_queries.py`, together with the existing version-aware index/evaluation entry points, have published the 205-entry catalog, 410 `IndexDocumentV2` and 615 `RetrievalQueryV2`; results and receipts are separate and refuse overwriting. All index metadata include roles and applicability, and document/model hashes are cross-checked with the release manifest.
- Repeated builds of the catalog/queries match 4/4 and 4/4 files; after reproducing the original default threading environment, all 7 index files have identical SHA-256. A single-threaded cross-environment rebuild showed a floating-point difference in the last digit of one vector row, but the dense top-5 document order of all 615 frozen queries is identical; see the separate audit receipt. Scenario questions are rewritten from catalog semantics; although they pass exact-text leakage checks, they should not be interpreted as independent external annotation. M03 as a whole and Gate B are DONE.

## M07 constrained plan-preview contract v1.0

- `src.agent.dynamic_planner` adds `M07PlanRequest`, `DatasetDescriptor`, `PlannedToolStep` and `PlanningResult`; all top-level M07 contracts are version 1.0, strict and forbid unknown fields. The old `GeoTaskIntent`, `GeoAnalysisPlan`, `ClarificationRequest` and old `WorkflowPlan` are unchanged.
- `M07PlanRequest` requires a query, a declared role catalogue and explicit place/analysis/output CRS; `source` is a controlled identifier without paths. The output contains `task_class`, confidence, evidence, business parameters, the GeoDSL intent/plan, tool bindings and a user preview.
- `plan_request` classifies restricted English commands and parses parameters; `resume_clarification` checks the original query and completes it; `plan_with_model` accepts an injected JSON proposer; `validate_model_plan_json` gives structural errors a single repair attempt and rejects all semantic errors deterministically. There is no default real model call.
- The static gate checks that public tools are still registered, scalar parameter types/business values, input roles and vector kinds, CRS, dependency closure/forward references, output roles, 8 steps, 12 cost units and at most 1 retry per step. Plans generated by M07 declare 0 retries; no tools are executed.
- `POST /plan-preview` returns 200 for a plan/clarification, 400 for request schema errors and 422 for unsupported tasks or invalid model proposals; `GET /plan-ui` is a separate preview page. The old `/chat`, `/workflow` and `/ui` routes are unchanged.
- Stable M07 error codes: `M07_UNSUPPORTED_TASK`, `M07_DATASET_UNKNOWN`, `M07_DATASET_KIND_UNSUPPORTED`, `M07_FIELD_UNKNOWN`, `M07_CRS_INCOMPATIBLE`, `M07_PARAMETER_INVALID`, `M07_PARAMETER_CONFLICT`, `M07_DEPENDENCY_INVALID`, `M07_TOOL_FORBIDDEN`, `M07_BUDGET_EXCEEDED`, `M07_MODEL_OUTPUT_INVALID`, `M07_MODEL_CALL_FAILED`, `M07_CLARIFICATION_INVALID`.
- A confidence of 1.0 only means an exact match of the explicit grammar, not a model probability. Rank, real transit isochrones and non-public tools are explicitly unsupported; there was no new plan executor at this stage.

## M08 validator and bounded replanning contract v1.0

- `src.agent.validation` adds `ValidationPolicy`, `ValidationFinding`, `RecoveryEvent`, `StepExecution` and `ValidationOutcome`, strict Pydantic v1.0 forbidding unknown fields. The old GeoDSL, DatasetRef, ToolSpec, M07 plan contracts and old HTTP routes stay as they are; M08 is a separate Python execution entry point, with no session state or new HTTP execution endpoint yet.
- `execute_validated_plan(request, plan, bindings, datasets, *, policy, tools=ANALYSIS_TOOL_REGISTRY, replan=None, conclusion=None)` first re-runs M07 static validation and then binds declared roles to current, readable DatasetRefs whose SHA verification passes. Only the six M07 agent public tools may be plan steps; actual calls use the M04 registry's strict input models. Model text, arbitrary code, SQL and local paths can never be executed directly.
- Inputs and every step's output are checked for current DatasetRef state, vector kind, expected CRS, fields, geometry, the explicit empty-result policy and maximum feature count; the final check covers count bounds, required fields and the strict `Found N features.` count conclusion. A textual conclusion outside this template returns the stable `M08_CONCLUSION_UNVERIFIABLE`; M10 free-text reports are not supported.
- A transient timeout is retried at most once with identical parameters; only an explicit policy allows calls to M04 `reproject_dataset`/`repair_geometry` for repairs or the use of a declared substitute DatasetRef. Repair tools are not exposed to the M07 model. Each run allows at most one injected replan; the original M07PlanRequest must be identical and the business parameters unchanged, and the new plan passes the static gate again; at most 24 tool calls in total. Failures return `partial`, a stable error code, references to verified results and a statement of limitations.
- Main error categories: `M08_PLAN_INVALID`, `M08_DATASET_UNAVAILABLE`, `M08_OUTPUT_UNREADABLE`, `M08_CRS_MISMATCH`, `M08_SCHEMA_MISMATCH`, `M08_GEOMETRY_INVALID`, `M08_EMPTY_UNEXPECTED`, `M08_LIMIT_EXCEEDED`, `M08_TOOL_TIMEOUT`, `M08_TOOL_UNAVAILABLE`, `M08_TOOL_FAILED`, `M08_CONSTRAINT_UNMET`, `M08_CONCLUSION_UNVERIFIABLE`, `M08_CONCLUSION_MISMATCH`, `M08_BUDGET_EXCEEDED`, `M08_REPLAN_INVALID`. Bound source labels come from the M07 declaration; M08 verifies kind/CRS/fields/current content hash but does not vouch for their external source identity.

## M09 bounded session and incremental edit contract v1.0

- `src.agent.session` adds `SessionManager`, `SessionSnapshot`, `SessionUpdate`, `CountComparison` and `SessionError`, strict Pydantic v1.0 rejecting unknown fields. At most 32 in-process sessions (explicitly configurable within 1–256), a 24-byte random opaque token, optimistic concurrency by version and explicit close; no full chat history or data rows are stored, and no new HTTP endpoint is provided. `context_summary` contains only the task class, place, business parameters, role names, verified counts and display/distance-unit preferences.
- `start` accepts a statically valid M07 plan and explicit DatasetRef bindings; only an M08 success can create a session. A snapshot stores the current request/normalized intent and plan, version, bindings, M08 step/result references, output format and distance unit. `get` returns a deep copy; expired or released DatasetRefs are rejected or trigger recomputation when reused.
- `apply(session_id, expected_version, turn, *, new_request=None, new_bindings=None)` supports, in restricted form, `change distance to N m|km`, `exclude FIELD = VALUE`, `switch city to CITY`, `compare with CITY` and `change output format to summary|geojson`. Unknown/conflicting edits, version conflicts, missing explicit new sources and non-existent fields all fail with stable errors. switch/compare must provide a complete new-source request and new DatasetRefs explicitly; a comparison only computes the count difference between two M08-verified results and does not change the current analysis baseline.
- The structure of the original M07 v1.0 contracts is unchanged; the restricted `within_distance` grammar adds `excluding FIELD = VALUE` in a backward-compatible way, producing a third downstream `filter_attributes` step, and M04 `spatial_join` output fields use the `left_` prefix. M08 `execute_validated_plan` gains an optional `cached_outputs` parameter: after checking tool registration and the input contract, each step looks up a key of tool, parameters and input DatasetRef ID/content hash; a cache hit still has to pass validation of the current DatasetRef, CRS, geometry, empty result, count and final constraints. `StepExecution status=succeeded` with `attempts=0` means verified reuse; the old non-cached semantics are unchanged.
- A change of output format only updates the display preference; it does not generate M10 maps/reports or change the GeoDSL output contract. A distance change recomputes the buffer and downstream steps, reusing the original data source; adding an exclusion reuses the two unchanged upstream steps and runs the new filter; a city/source change does not use old caches; a `partial` edit is not promoted to the current session, and `SessionUpdate` attaches `attempt_outcome`, a stable error code and limitations.

## M10 AnalysisResult v1.0 and presentation entry points

- `src.agent.result_presentation` adds strict, `extra=forbid` `VerifiedRun`, `AnalysisResult`, `ResultMetric`, `SourceEvidence`, `StepEvidence`, `MapLayer`, `ResultTable`, `MetricChart` and `DownloadArtifact`; `AnalysisResult.schema_version=1.0`. `build_analysis_result(primary, registry, comparison=None)` accepts only runs whose M07 static gate is still valid and whose M08 `status=success`. After a successful replan, evidence comes from the final successful step trace; `partial`, stale/inconsistent DatasetRefs, mismatched source roles (including checked lineage through at most 32 current parent DatasetRefs) and comparisons with different CRSs are rejected.
- `validate_analysis_result` rebuilds the canonical result from the current DatasetRegistry and the verified runs, and any change in metrics, text, layers, sources, steps or downloads raises `M10_EVIDENCE_MISMATCH`. Report templates forbid literal numbers and unknown metric placeholders; `rendered_report` fills in values only from metrics with evidence. Results contain the conclusion, units, tables/comparisons, at least two layers, charts, source versions/hashes, steps, warnings/assumptions/limitations, and complete GeoJSON/CSV data URIs.
- Maps preview at most 250 features per layer and tables at most 50 rows; downloads contain the complete verified result, at most 20 MB per file. CSV escapes formula prefixes; HTML uses `textContent` and JSON-safe embedding. Projected-coordinate GeoJSON carries the private `geoai_crs` marker; RFC 7946 consumers must convert to EPSG:4326 first. No base-map network requests.
- `src.agent.result_html.render_analysis_html` produces self-contained SVG with multiple layers/legend/pop-ups/charts/report. `GET /analysis-ui` only shows the published offline v2 example snapshot; it is not a new endpoint for executing user tasks. The old `/chat`, `/workflow`, `/ui`, `/plan-preview`, `/plan-ui` and the GeoDSL/DatasetRef/ToolSpec/M07–M09 contracts are unchanged.
- Stable presentation errors: `M10_RESULT_UNVERIFIED`, `M10_PLAN_INVALID`, `M10_PLAN_TRACE_MISMATCH`, `M10_DATASET_STALE`, `M10_SOURCE_MISMATCH`, `M10_SOURCE_UNSAFE`, `M10_CRS_MISMATCH`, `M10_EXPORT_LIMIT`, `M10_EVIDENCE_MISMATCH`. M10 only presents results; it does not widen agent tool permissions or call real models or external services.

## M11b benchmark v2.0 contract

- `src.evaluation.m11b` adds `M11bTask/M11bExpectation/M11bPrediction/M11bScore`, `load_m11b_tasks` and `score_m11b`. v2.0 runs in parallel with M11a v1.0; unknown fields are rejected, and consistency of dev/test IDs, split and data version is checked. M11a's 24 tasks, API and reports are unchanged.
- References for supported tasks are produced by actually executing the existing M04 AnalysisToolRegistry; the reference answer contains required semantic steps, the complete M04 tool sequence, Feature IDs, count/area/distance, explicit CRS and real geometry. Publicly supported: search, within_distance, nearest, within, spatial_join, aggregate_by_region; rank returns `unsupported_rank`, without implying a ranking tool.
- Scoring never executes tools, paths or code proposed by predictions; tool sequences may contain only existing M04 names and must match the reference exactly. The M07 agent's six-tool public allow-list, ToolSpec, GeoDSL, DatasetRef, HTTP and the old workflow are all unchanged.
- CLI: `python -m benchmarks.run_m11b_scoring --tasks <v2 JSONL> --predictions <v2 JSONL> --system-id <id>`. Outputs `report.json`/`report.md` in an exclusive directory; public dev has per-task diagnostics, while private test is aggregate only and never writes seeds/queries/answers. A complete official release is identified by path and hash, and subsets are explicitly labelled as not a complete release. M11b provides no fixed workflow/dynamic agent/validator baseline executors.

- Real-call evidence supplement: `benchmarks/data/dev/m11b_tool_traces_v1.jsonl` and a separate receipt record, for the 144 supported tasks, the M04 tool names, versions, input/parameter/output hashes and row counts, rebuildable byte for byte; the published tasks and original manifest are not modified.

## M07 grammar m07.2 additive productions (ADR-0018, 2026-10-07)

- `plan_request` appends, after all m07.1 productions: `search ROLE where COND (and COND)* in PLACE` (conditional_search), `find K nearest TARGET to SOURCE where CONDS in PLACE` (filtered_nearest), `find SOURCE where CONDS within CONTAINER in PLACE` (filtered_within), `join LEFT with RIGHT where CONDS on PRED in PLACE` (filtered_spatial_join, filtering RIGHT) and `aggregate OP [FIELD] by RID from VALUES where CONDS into REGIONS using VRID in PLACE` (filtered_aggregate_by_region). COND is `FIELD OP VALUE` with OP ∈ =, !=, >, >=, <, <= (eq/ne/gt/gte/lt/lte); VALUE is only a number or an identifier; at most 8 conditions, and fields must be declared in the request.
- New plans have `GeoAnalysisPlan.source_version=m07.2`, and their business parameters include `conditions` (and `filter_role`); old commands are still m07.1 with byte-identical output. PlanningResult, M07PlanRequest, the tool allow-list, budgets, the static gate and the M08 interface are unchanged; M08 accepts the new plans automatically through `plan_request`. There is no production for single-dataset buffers; rank is still `M07_UNSUPPORTED_TASK`.

## M11c baseline systems and comparison protocol (ADR-0019, m11c-protocol-v1)

- `src.agent.benchmark_frontend.interpret(query, catalogue, scope)` → `FrontendDecision` v1.0 (m11c-frontend-v1): status request/clarification/unsupported, operation, reason_code, M07 command and M07PlanRequest, plus structured roles/conditions/parameters of the same interpretation. It reads only the query, the role→DatasetRef catalogue and the scope label; stable reasons are `missing_place`, `unknown_dataset`, `unsupported_rank`, `unsupported_buffer`, `unparsed_query`, `unexpressible_condition`, `ambiguous_join_key`.
- `src.agent.baselines`: `SystemInput` v1.0 contains only task_id and query; `DatasetCatalogue/CatalogueFile`, `register_catalogue` (SHA-256 checked); `format_success` encodes results per M11b v2.0; systems `FixedWorkflowSystem` (fixed_workflow), `DynamicAgentSystem`, `ValidatorReplanSystem` (configurable policy/allow_replan/system_id), `TemplateWorkflowSystem` (template_workflow) and `RawQueryValidatorSystem` (validator_no_frontend). Each system's `route(query, refs, catalogue)` executes no tools, and `run(SystemInput, catalogue)` returns an M11bPrediction and a trace; rejection reasons are unified as `unsupported_<operation>` or native codes, and unexpected exceptions are recorded as `system_exception` failures.
- `src.evaluation.m11c`: `dev_catalogue`, `private_catalogue` (EPSG:3857 declared by the evaluator), `system_input`, `capability_matrix`, `formal_systems`, `ablation_systems`, `source_fingerprint`, `run_system`, `secondary_pass`, `summarize`, `across_runs`, `stability`. CLI: `python -m benchmarks.run_m11c_baselines prefreeze|freeze|run`; `run` checks the frozen manifest, matrix and source fingerprint and then writes the report exclusively. Audit: `python -m scripts.audit_m11c_reproducibility`.
- No new HTTP routes, agent tools or dependencies; the M11b v2.0 contract and scorer are unchanged.

## M11c v2 model front end and evaluation harness (ADR-0020, m11c-protocol-v2)

- `src.agent.model_frontend` (m11c-frontend-v2): `PromptTemplate/load_prompt` (SHA-256 computed after normalizing line endings to LF); `profile_catalogue(DatasetCatalogue)` → `CatalogueProfile` (roles, geometry types, row counts, field names/types, value lists for text fields with ≤12 values, plus all values kept internally for grounding); `frame_schema(profile)` (frozen structure, with role/field enumerations generated from the declared catalogue); `render_prompt`; `SemanticFrame/FrameCondition`; `parse_frame` → ok | non_json | schema_invalid; `ModelFrontend(prompt, transport, endpoint, clock)`: `identity()`, `verify_model()` (`/api/version` must be 0.13.5 and the `/api/tags` digest must equal the frozen value, otherwise `ModelUnavailable`), `call(query, profile)` → `LogicalCall` (≤2 attempts; `Attempt` records the outcome, raw response, tokens and time; `record()/from_record()` for replay); `ground_frame(frame, refs, profile, scope, query)` → `FrontendDecision` (frontend_version m11c-frontend-v2; new reason codes `model_frontend_failed`, `unsupported_operation`, `unknown_field`, `ambiguous_metric_field`); `mention_positions`, `infer_join_keys`. The default transport is urllib directly to 127.0.0.1 (bypassing system proxies); offline tests inject a mocked transport and never call the real model.
- `src.agent.benchmark_frontend`: adds `render_request` (M07 command rendering shared by v1 `interpret` and v2); `FrontendDecision.frontend_version` allows m11c-frontend-v2; `interpret` behaves byte-identically to v1 (re-checked against the v1 capability matrix and audit).
- `src.agent.baselines`: `_System.frontend` is an optionally injected decision source (`callable(query, refs, catalogue)` with `identity()`); with the default `None`, `config()` equals the m11c-protocol-v1 manifest; when injected, `config()` adds frontend_version, model_id, model_digest, prompt_version and prompt_sha256. The template system's `route` and the agent's `_decide` go through the same injection point.
- `src.evaluation.m11c_v2`: `PROTOCOL_VERSION` m11c-protocol-v2, `SEEDS`, `GATE`, `MODEL_FREE_IDS`; `frontend_round` (one logical call per task per run), `replay_round` (rebuilt from recorded raw responses, no model call), `round_records`, `round_stats`, `decisions_sha256`, `RoundDecisionSource`, `IdentitySource`, `model_systems`, `evaluate_split` (latency adds the per-task front-end wall-clock time, `llm_calls` = number of HTTP attempts; A1 uses no model), `combined_matrix` (counted as executable only if executable in every run), `gate_check`, `receipt_entries`, `frontend_receipt`.
- CLI: `python -m benchmarks.run_m11c_v2 dev-iterate [--probe file] | freeze-stage2 | run-dev | run-private --workspace <workspace outside the repository> | replay-private | scan-leaks`; audit `python -m scripts.audit_m11c_v2_reproducibility`. The private workspace is passed only via `--workspace` or `GEOAI_M11C_PRIVATE_V2_DIR`, and the code contains no local absolute paths.
- No new dependencies, HTTP routes or agent tool permissions; the M11b v2.0 contract and scorer are unchanged.
- Leak location: `python -m scripts.locate_m11c_v2_leak_hits --workspace <workspace outside the repository>` (must be run by the author/evaluator role or the owner; it reads the sealed package and outputs only the hit category, file, masked JSON path or a Markdown section identical to the dev report, key/value, and whether the text is identical to the public dev report, never private words).

## M12 scenario-layer contract v1.0 (ADR-0021; m12a implemented, 2026-10-08)

- New package `src/scenarios/`, all strict, `extra=forbid`, `schema_version 1.0`: `ScenarioCatalogue`/`CatalogueEntry` (role→DatasetRef, semantic type `scb_population_grid_raw|poi_points`, declared CRS, required fields, licence/attribution/`provider_verified`/time notes, optional `CrsReconfirmation`), `CrsEvidence`, `ScenarioClarification` (a new contract, reason_code ∈ `missing_parameters|distance_out_of_range|age_band_unsupported|threshold_out_of_range|place_not_covered`; the closed enumeration of GeoDSL `ClarificationRequest` is neither reused nor modified), `ScenarioIntent`, `FrontendResult` (plan|clarification|unsupported), `ScenarioStep`/`ScenarioPlan` (≤12 steps, a per-step `empty_policy`, preview reasons), `StepRecord`, `ScenarioOutcome` (success|partial; `tool_calls` ≤64 including internal `validate_geometry`; `invoked_tools` excludes internal checks; `replan_count` ≤1; reuses M08 `ValidationFinding`/`RecoveryEvent`).
- `SCENARIO_TOOL_REGISTRY` = the 12 tools of `ACCESSIBILITY_TOOL_REGISTRY` (m12a adds no tools); `PLANNABLE_TOOLS` are the 8 tools that may enter scenario plans (no quality/repair tools; the GTFS tool will get its mapping in m12b). Existing registries and M07 `PUBLIC_TOOLS` are unchanged.
- `validate_plan(plan, catalogue)`: allow-listed and registered, sequential step IDs, available roles, no reuse of outputs, dependencies equal to input lineage, exact parameter-key sets that pass each tool's strict input model (with placeholder DatasetRefs); failures raise `SCENARIO_PLAN_INVALID`.
- `check_binding`/`crs_plausible`: current DatasetRef and hash, kind, CRS equal to the declaration, coordinates within the declared CRS's `area_of_use` (±1%) and intersecting the study area, required fields. `reconfirm_source_crs(catalogue, role, CrsEvidence, registry)`: the evidence must be a versioned manifest under `data/fixtures/` whose file sha256 equals the content hash and whose `crs` is valid and plausible after conversion → a new DatasetRef (lineage `crs_reconfirmed`) and an updated catalogue declaration.
- `execute_scenario_plan(plan, catalogue, registry, *, policy, replanner=None, tools=SCENARIO_TOOL_REGISTRY, max_tool_calls=64)`: the policy's `allow_crs_repair`/`allow_geometry_repair` must be False (otherwise `SCENARIO_POLICY_INVALID`); binding checks complete before any tool call; a wrong CRS is never reprojected; verified substitute snapshot + one replan; a timeout is retried once with identical parameters; every step's output is checked for currency/CRS/geometry/empty result/limits.
- `src.scenarios.m12a`: `interpret_m12a`/`resume_m12a` (restricted command grammar m12a-grammar-v1), `plan_m12a` (m12a-plan-v1), `build_m12a_report`/`validate_m12a_report`. `src.scenarios.report`: `ScenarioReport` v1.0 (evidence types `dataset_row_count`, `dataset_field_sum`, `dataset_field_extreme`, `dataset_field_null_count`, `tool_output_value`, `plan_parameter`, `difference`, `ratio`; the validation context for `tool_output_value` is to be implemented in m12b/m12c), `verify_metric_evidence`, `rendered_report`, `FORBIDDEN_PHRASES`; `src.scenarios.html.render_scenario_html` (self-contained SVG).
- Stable error codes: `SCENARIO_PLAN_INVALID`, `SCENARIO_POLICY_INVALID`, `SCENARIO_DATASET_UNAVAILABLE`, `SCENARIO_SCHEMA_MISMATCH`, `SCENARIO_CRS_MISMATCH`, `SCENARIO_CRS_IMPLAUSIBLE`, `SCENARIO_CRS_EVIDENCE_INVALID`, `SCENARIO_ROLE_UNKNOWN`, `SCENARIO_REPLAN_INVALID`, `SCENARIO_TOOL_FAILED`, `SCENARIO_TOOL_TIMEOUT`, `SCENARIO_BUDGET_EXCEEDED`, `SCENARIO_OUTPUT_UNREADABLE`, `SCENARIO_LIMIT_EXCEEDED`, `SCENARIO_GEOMETRY_INVALID`, `SCENARIO_EMPTY_UNEXPECTED`, `SCENARIO_CONSTRAINT_UNMET`, `SCENARIO_CLARIFICATION_INVALID`, `SCENARIO_RESULT_UNVERIFIED`, `SCENARIO_REPORT_EVIDENCE_MISMATCH`, `SCENARIO_SOURCE_UNSAFE`, `SCENARIO_EXPORT_LIMIT`.
- Evaluation and data entry points: `src.evaluation.m12a` (`build_catalogue`/`run_case`/`score_case`/`evaluate`/`deterministic_receipt`); `python -m benchmarks.run_m12a_scenario run|receipt`; `python -m scripts.audit_m12a_reproducibility`; `python scripts/freeze_m12a.py verify`; `python scripts/build_m12_lund_data.py precheck --gate-version v1|v2 | build-scb | build-osm`; `python scripts/build_m12a_references.py --check`. No new HTTP routes, Python dependencies or agent tool permissions; M07–M11 contracts and behaviour are unchanged.
