# ADR-0015: M09 bounded sessions and checked incremental reuse

- Status: Accepted
- Date: 2026-10-06
- Affected modules: M09, the M07 restricted grammar, the optional M08 cache entry point and later M10
- Versions: new SessionSnapshot, SessionUpdate, CountComparison v1.0; existing M07/M08/GeoDSL/DatasetRef/ToolSpec schema versions are unchanged.

## Context

M07 produces static plans and M08 executes and validates them. Edits across turns must keep the intent and sources without copying whole datasets or passing off stale intermediate results as new computations. Simply rerunning every tool cannot show upstream reuse, while trusting a cache directly would bypass the M08 result gate.

## Decision

1. Use an in-process SessionManager with at most 32 active sessions by default, configurable between 1 and 256. A random opaque token is the access credential and a version number provides optimistic concurrency; after an explicit close the session can no longer be accessed. State keeps only the request/normalized plan, bound DatasetRefs, step/result references, display and distance-unit preferences and one count comparison; it never stores whole chats or data rows.
2. Multi-turn edits use restricted short commands: change the distance, exclude one field value, switch city, compare with another city, change the output format. A change of place/source requires the caller to supply a complete new M07PlanRequest and new role DatasetRefs explicitly, with source identifiers matching the references. A comparison only computes the count difference between two M08 success results; the baseline analysis result is not changed.
3. To support exclusions, the M07 `within_distance` restricted grammar gets a backward-compatible extension that builds a third `filter_attributes(ne)` step; target-side attributes from M04 `spatial_join` use the `left_` prefix. The old 80/90 frozen evaluations and old contract versions are unchanged.
4. Step artefacts from M08 success runs are cached under a key of tool, parameters, input roles and input DatasetRef IDs/content hashes. Before a cache hit, M08's optional `cached_outputs` entry point still checks the public tool and the registered input contract and re-validates the current DatasetRef, CRS, geometry, counts and final constraints; a stale cache entry is recomputed instead. `StepExecution attempts=0` means verified reuse; the original non-cached call path is unchanged.
5. An edit that ends in an M08 `partial` is not promoted to the current session; the full `attempt_outcome` is returned so the caller can see the errors and limitations. A format-only change edits only the display preference; it does not generate an M10 report or claim a new output file.

## Compatibility, limitations and risks

- The old HTTP API, GeoDSL, DatasetRef, ToolSpec and M04/M05/M06 registries are unchanged; no new dependencies. The original M07 restricted commands and M08 non-cached behaviour were re-checked byte for byte against the frozen evaluations.
- This is in-process state that does not survive a restart and is not shared across machines. There is no user authentication, HTTP session endpoint, open-domain edit understanding or time-based expiry. Any caller holding the token can access the session; a production multi-user service would need new authentication and persistence designs.
- Synthetic city names and small data only demonstrate state and source-invalidation semantics, not real city coverage. GTFS stop buffers are not transit isochrones; M03 Phase 2 queries remain catalog-informed.

## Evidence

- 16 cases frozen in advance; the four metrics score 16/16, 4/4, 4/4 and 4/4; module tests 27/27.
- The M07 v1/v2 and M08 v1 receipts were re-checked byte for byte and are unchanged; the M09 receipt passes its byte-level check. Full offline suite 413/413, compilation and existing reproducibility audits pass (single-threaded numeric environment).
