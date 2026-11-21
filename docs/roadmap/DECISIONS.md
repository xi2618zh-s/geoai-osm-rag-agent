# Architecture decision index

Every decision that affects public contracts, data, evaluation criteria or cross-module collaboration must be recorded as an ADR. Statuses are `Proposed`, `Accepted`, `Superseded` or `Deprecated`.

| ADR | Title | Status | Date | Scope |
|---|---|---|---|---|
| [ADR-0001](../adr/ADR-0001-baseline-governance.md) | Repository control files as the single cross-session source of truth | Accepted | 2026-10-05 | M00–M14 |
| [ADR-0002](../adr/ADR-0002-geodsl-v1-contract.md) | GeoDSL v1 strict semantic contracts and a one-way adapter from the old Workflow | Accepted | 2026-10-05 | M01, M02, M07, M11 |
| [ADR-0003](../adr/ADR-0003-local-dataset-registry.md) | DatasetRef with opaque URIs, controlled copies and an explicit lifecycle | Accepted | 2026-10-05 | M02, M04–M11 |
| [ADR-0004](../adr/ADR-0004-versioned-benchmark-calibration.md) | Benchmark with strict contracts, deterministic scoring and non-overwritable run directories | Accepted | 2026-10-05 | M11, M04–M10, M12–M14 |
| [ADR-0005](../adr/ADR-0005-datasetref-analysis-tool-registry.md) | A new DatasetRef analysis tool registry, with Shapely 2 for topology validation | Accepted | 2026-10-05 | M04, M07–M13 |
| [ADR-0006](../adr/ADR-0006-m04b-spatial-analysis-semantics.md) | M04b explicit pyproj CRS policy, make_valid repair and derived DatasetRefs | Accepted | 2026-10-05 | M02, M04, M07–M13 |
| [ADR-0007](../adr/ADR-0007-scb-population-grid-adapter.md) | M05 fixed SCB population grid adapter and conservative fully-contained-cell statistics | Accepted | 2026-10-05 | M02, M04, M05, M10–M14 |
| [ADR-0008](../adr/ADR-0008-osm-knowledge-v2-phase1.md) | M03 Phase 1 strict bilingual knowledge contracts, pinned OSM Wiki sources and versioned hybrid indexes | Accepted | 2026-10-05 | M03, M07, M11 |
| [ADR-0009](../adr/ADR-0009-gtfs-sverige2-m06a.md) | M06a pinned official GTFS Sverige 2 archive and bounded local ingestion | Accepted | 2026-10-05 | M02, M06, M11/M12 |
| [ADR-0010](../adr/ADR-0010-m06b-stop-service-coverage.md) | M06b service-day stop frequencies and conservative buffer coverage | Accepted | 2026-10-05 | M02, M04–M06, M11/M12 |
| [ADR-0011](../adr/ADR-0011-osm-knowledge-v3-phase2.md) | M03 Phase 2 backward-compatible schema v3 with roles/categories | Accepted | 2026-10-06 | M03, M07, M11 |
| [ADR-0012](../adr/ADR-0012-m03-phase2-key-page-source-exception.md) | Pinned key-page sources and a value-level `unknown` exception for four tags | Accepted | 2026-10-06 | M03 |
| [ADR-0013](../adr/ADR-0013-m07-constrained-planning-preview.md) | M07 constrained planning and preview | Accepted | 2026-10-06 | M07–M11 |
| [ADR-0014](../adr/ADR-0014-m08-bounded-validation-replanning.md) | M08 bounded validation and replanning | Accepted | 2026-10-06 | M08–M11 |
| [ADR-0015](../adr/ADR-0015-m09-bounded-session-reuse.md) | M09 bounded sessions and checked reuse | Accepted | 2026-10-06 | M07–M10 |
| [ADR-0016](../adr/ADR-0016-m10-evidence-backed-result-presentation.md) | M10 result presentation from checked evidence | Accepted | 2026-10-06 | M10, M11–M14 |
| [ADR-0017](../adr/ADR-0017-m11b-versioned-spatial-benchmark.md) | M11b versioned tasks, a private split and real geometric scoring | Accepted | 2026-10-07 | M11b, M11c |
| [ADR-0018](../adr/ADR-0018-m07-grammar-m072-prefilter-productions.md) | M07 grammar m07.2 additive pre-filter productions | Accepted | 2026-10-07 | M07, M11c |
| [ADR-0019](../adr/ADR-0019-m11c-baseline-comparison-protocol.md) | M11c baseline comparison protocol v1 | Accepted | 2026-10-07 | M11c |
| [ADR-0020](../adr/ADR-0020-m11c-v2-model-frontend-sealed-private.md) | M11c v2 local model front end and a sealed private v2 | Accepted | 2026-10-07 | M11c |
| [ADR-0021](../adr/ADR-0021-m12-scenario-layer.md) | A separate M12 scenario layer (restricted command grammar, typed scenario plans and evidence reports) | Accepted | 2026-10-08 | M12 |
| [ADR-0022](../adr/ADR-0022-m12b-rank-candidates.md) | m12b deterministic multi-criteria ranking tool `rank_candidates` v1 | Accepted (not implemented) | 2026-10-08 | M12 |
| [ADR-0023](../adr/ADR-0023-m12c-single-ride-reach.md) | m12c single-ride in-vehicle time budget plus fixed walking radius tool | Accepted (not implemented) | 2026-10-08 | M06, M12 |
| [ADR-0024](../adr/ADR-0024-m12-lund-scenario-data.md) | M12 Lund scenario data, licence separation and a data pre-check gate | Accepted | 2026-10-08 | M05, M06, M12 |

## Process for a new decision

1. Copy `docs/adr/ADR_TEMPLATE.md` to a new file with the next number.
2. Describe the context, decision, alternatives, consequences and rollback/migration.
3. Register it in this index; when interfaces are involved, update `INTERFACES.md` at the same time.
4. A `Proposed` decision must never be treated as an implemented capability by later modules.

## M03 Phase 1 decisions implemented (2026-10-05)

- ADR-0008's public contracts, pinned official Wiki `oldid`s/CC BY-SA 2.0 data licence, 100-tag quotas, pinned multilingual MiniLM revision, CJK BM25, independent `public_eval` and the boundary between Phase 1 and whole-module status are all implemented.
- The final `publication_manifest` is the Phase 1 completion evidence; its SHA-256 is `1312a3456169a9e432321d2223672a77111e745f5e818c35cab17599e1c1b19b`. Full M03 DONE still requires 200+ tags and renewed coverage of sources/licences/bilingual content/hard negatives/indexes/evaluation.
- Any later change to the public schema, model revision, index format, sources, licence or `public_eval` configuration needs a new ADR/version and must not overwrite the pinned Phase 1 results.

## M06a decisions implemented (2026-10-05)

- ADR-0009 pins the official Trafiklab/Samtrafiken GTFS Sverige 2 historical archive, CC0-1.0, the 2025-10-04 version, the source SHA-256, explicit network download/offline subset building, bounded GTFS validation and the `network/gtfs` registration semantics of DatasetRef v1.
- No new dependencies, no change to the public DatasetRef schema, and no overwriting of old GeoJSON/CSV, M03 Phase 1 or historical benchmarks. The full national archive exceeds the current bounded registration limit; the M06b window/coverage semantics are frozen by ADR-0010, and any later streaming/slicing approach needs its own ADR.

## M06b decisions implemented (2026-10-05)

- ADR-0010 fixes half-open time windows counted from the start of the service day, regular-boarding frequencies, metric EPSG:3006 stop buffers, a conservative fully-contained-cell population lower bound and unique POIs over the buffer union, comparison with the first window, strict contracts and a separate 12-tool registry. The first version of M06 is DONE within this boundary; real transfer routing, isochrones, area weighting and national streaming are not part of this stage.
- No new Python/system dependencies, no new network data sources, and no changes to the public DatasetRef v1 schema, the old Workflow/M04/M05 registries, the M03 Phase 1 publication or historical benchmarks. When M06b was completed, M03 as a whole/Gate B were still IN_PROGRESS; both are DONE now that Phase 2 has passed.

## M03 Phase 2 v3 knowledge contracts and sources implemented

- The user approved extending Phase 2 from the old eight categories to a backward-compatible schema v3 with `accessibility` and `land_use`, and approved the per-tag classification/role draft for all 205 tags; see ADR-0011. No old v2/Phase 1 asset may be rewritten.
- After fetching official tag pages at pinned `oldid`s, four redirect-only stubs failed source review; raw v1 keeps the rejection evidence. Under ADR-0012 the user approved pinned key pages with a value-level `unknown` status for those four tags only. Raw v2 has been published with 205 mappings/203 pages, and the catalog, indexes and public evaluation are published too. Indexes were verified to rebuild 7/7 byte for byte in the original threading environment, and 615 distinct, separated queries meet the planned count; M03 as a whole and Gate B are `DONE`. The limitation on external generalization of catalog-informed queries is retained.

## M07 decisions (2026-10-06)

- ADR-0013 adopted: a separate M07 plan-preview/tool-binding contract on top of the old Workflow and GeoDSL v1, without changing old public contracts or execution paths. A model may only propose strict JSON; task interpretation, the agent tool allow-list and the static gate are handled by deterministic code.
- `rank` is explicitly rejected because there is no registered deterministic ranking tool, and it is not disguised as aggregation/filtering. M07 does not execute tools and does not implement M08 result validation/replanning or M09 session state.
- Evaluation v1 was frozen in advance and its non-overwritable results are kept; after a coverage audit, v2 was added for the eight plannable task types and is explicitly a post-implementation supplementary regression. Gate C still IN_PROGRESS.

## M08 decisions (2026-10-06)

- ADR-0014 freezes separate M08 v1.0 validation, explicit repair, one retry with identical parameters, one replan overall and a cap of 24 tool calls. Replanning keeps the complete original request and business parameters, and every new plan passes the M07 static gate again; failures return `partial`, a classification and limitations, without changing computed values.
- M08 changes no old contracts or registries and adds no dependencies, network access or real model calls; M09 session state has not started, and Gate C is still IN_PROGRESS.

## M09 decisions (2026-10-06)

- ADR-0015 fixes bounded in-process sessions, versioned state, four kinds of edits, explicit new-city data, step caches checked by M08 and no promotion of `partial` results. The M07 restricted grammar gains exclusion conditions in a backward-compatible way; M08 only gains an optional cache entry point, without changing old contract structures or tool permissions.
- With M07/M08/M09 complete, Gate C can be marked DONE; M10 presentation, M11b/c and later scenario integration were not yet implemented. No new dependencies, real models or network evaluation.

## M10 decisions (2026-10-06)

- ADR-0016 fixes a separate AnalysisResult v1.0, evidence rebuilt from current DatasetRefs, numeric placeholders in templates, checked two-sided comparisons and self-contained SVG presentation. `GET /analysis-ui` serves only the published snapshot; old interfaces, public tools and dependencies are unchanged.
- Maps/tables have preview caps while downloads contain the complete verified result; projected-coordinate GeoJSON is marked with `geoai_crs`, and the RFC 7946 adaptation requirement is disclosed. On the 12 frozen M10 cases all four metrics score 12/12; M10 DONE, Gate D still IN_PROGRESS.

## M11b decisions (2026-10-07)

- ADR-0017 adds a separate benchmark v2.0 and leaves M11a v1.0 unchanged: 160 public dev + 40 locally ignored private test tasks, 200 in total. Real M04 calls produce the reference results; geometry is scored by area IoU in an explicit CRS or by non-area topological/Hausdorff equivalence, and Feature IDs, numbers, fields and tool order are scored separately.
- The private seed/questions/answers/source geometry live only in a git-ignored directory; only a hash receipt is public, and private reports are aggregate only. No new dependencies, agent tool permissions or overwriting of historical results. The M11b self-check does not represent agent performance; the three M11c baselines and repeated runs were still pending; Gate D stays IN_PROGRESS.

## M11c decisions (2026-10-07, in progress at the time)

- ADR-0018: the user agreed in principle to append m07.2 threshold/multi-condition/pre-filter productions after m07.1 as a prerequisite improvement for M11c; old commands and the M07–M10 receipts are byte-identical, the tool allow-list and GeoDSL v1 are unchanged, and single-dataset buffers are unsupported and counted in coverage. This ADR does not constitute M11c DONE.
- The comparison protocol and fixed-workflow options are in `docs/roadmap/M11C_PROTOCOL_DRAFT.md` (a draft, not frozen); no formal comparison or private run before the user confirms.
- ADR-0019: the shared offline front end m11c-frontend-v1 and seven configurations (formal FW-A, dynamic_agent, validator_replan; dev ablations FW-B, A1–A3); SystemInput is only task_id + query; strict pass is the primary metric and "strict pass excluding required_steps" the diagnostic secondary metric; A4 fault injection is left to M13. m11c-protocol-v1 is frozen before running, and private results are published only in aggregate.
- Formal result m11c_comparison_v1: public dev strict 8/16/136; private 0/40 (the front end parsed none of the private queries), private common subset 0, so under the frozen criteria M11c stays IN_PROGRESS; the next direction is the user's decision, and the v1 results are not overwritten.

## M11c v2 decisions (2026-10-07)

- Options 3 + 4 (ADR-0020): the v1 results are unchanged; a local qwen2.5:3b semantic-frame front end + an independently written private v2 sealed by the user; a three-stage freeze (rules → prompt/code → dev gate).
- Revisions before sealing (user decision): the number of tasks is unchanged; 5 existing tasks became legal empty sets (one each for search, nearest, within and spatial_join, with the within/spatial_join ones produced by spatial relations) plus one count-zero-match aggregate; an extra missing parameter in a missing-place task was fixed. M11b v2.0 does not score per-region aggregate values: the scorer stays frozen and this is disclosed as a limitation.
- Front-end design: the model outputs only a semantic frame, with role/field enumerations taken from the catalogue; deterministic grounding handles lexical evidence (otherwise `missing_place`), rank/buffer rejection, the filtered side, adding conditions from mentioned values, turning pairing verbs into joins, taking the first-mentioned subject of a relation, join keys (value overlap first) and M07 rendering. All evidence comes from public dev and dev-derived probes only.
- Gate and private evaluation: stage 3 passed all three times (validator 136/160 and so on); an evaluator subagent ran `run-private` once. Three hits from the leak scan were confirmed, through an out-of-session de-identified location step authorized by the user, to be template text identical to the dev report; the raw hits are kept.
- Verdict: the three-way common executable subset of private v2 is 5 (non-empty) and all other frozen DONE conditions are met → M11c DONE, M11 DONE; Gate D still IN_PROGRESS (M12, M13 not implemented). Improving the spatial_join weakness on private v2 would need a new protocol version and a new holdout.

## M12 decisions (2026-10-08, conditionally approved by the user)

- D1-A → ADR-0022: `rank_candidates` is registered only in the scenario registry, not in M07; rank remains unsupported in M07/M11.
- D2-B (limited approval) → ADR-0024: only two downloads are allowed, the extended SCB WFS BBOX and the pinned GTFS archive, stored on drive D outside the repository; the data pre-check gate must pass first; the local PBF is read-only, with its full SHA-256, file-header timestamp and "provider unverified" recorded, and only Python osmium is used; OSM (ODbL) and CC0 data are kept in separate directories with separate licences and attributions.
- D3-B → ADR-0023: only called a "single-ride in-vehicle time budget plus a fixed walking radius", excluding waiting/walking time and transfers, and never called an isochrone; vague "how many minutes in total" questions are clarified first.
- D4: no local model; the front end is honestly called a "restricted command grammar". D5-B: m12a, m12b and m12c run in three sessions, each with its own handoff.
- ADR-0021: a separate scenario layer; a wrong CRS declaration is rejected first (`SCENARIO_CRS_IMPLAUSIBLE`), and explicit reprojection is allowed only after the source CRS is reconfirmed by a trusted manifest and re-registered, or after switching to a verified snapshot; `reproject_dataset` is never called directly on a wrong declaration. Cases, reference answers and thresholds are frozen after the data pre-check and the independent oracle, and before scenario-specific implementation.
- Pre-check gate v2 (approved by the user, ADR-0024 revision record): the SCB upper bound goes from 400 to 484 (the original bound was a geometric estimation error); attributes are checked per feature, and `sp_geometry` is checked as the top-level geometry; the raw 453-cell response is kept; a population total must not be called the exact population of the 20×20 km area; the m12a distance threshold cap is tightened to 2000 m because of edge cells.
- Correction before the freeze (ADR-0021): scenario clarification uses the new `ScenarioClarification` contract and neither reuses nor modifies the closed enumeration of GeoDSL `ClarificationRequest`; nearest distances above 2000 m are upper bounds, and m12a neither reports nor scores `max_gap_distance_m`.
- m12a DONE (2026-10-08): 13/13 frozen cases exact in every component, receipt `bf46afba…1fda4a` identical across two runs, audit passed; m12b and m12c start in new sessions per D5-B.
