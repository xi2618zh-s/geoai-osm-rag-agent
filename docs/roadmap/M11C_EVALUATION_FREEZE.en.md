# M11c three-baseline comparison protocol freeze (before formal runs, m11c-protocol-v1)

> **English translation.** The authoritative frozen document is the Chinese original [`M11C_EVALUATION_FREEZE.md`](M11C_EVALUATION_FREEZE.md). Its SHA-256 is recorded in the frozen M11c reports and checked by code, so the original is kept byte for byte as evaluation evidence. This translation is provided for readers and is not part of the frozen evidence; if the two differ, the original applies.

- Date: 2026-10-07; published before any formal run and any private-set run. The user confirmed: FW-A is the third formal configuration; FW-B is only a public dev ablation, frozen before any scores were seen; strict pass is the primary metric and "strict pass excluding required_steps" the diagnostic secondary metric; A4 fault injection is not included (left to M13).
- Machine-readable freeze: `benchmarks/results/m11c/freeze/m11c_freeze_manifest_v1.json`, SHA-256 `8c512e4b545d58029017989669688e25a4890f92c1bd06bcef358ae96584a15c`; public dev capability matrix `m11c_dev_capability_matrix_v1.jsonl`, SHA-256 `66afad17b310ee49513f660229b061dcfead22f3c6b015721d8b77ca2a6140c5`; source fingerprint (path + SHA-256 summary of all `.py` files in `src` and the two runners) `sha256:96049292e2e1803eb5c6eb1a99baccb91bea35a1c6e76bb5b7fe2161b1891cb7`. Before a formal run the runner recomputes all three and refuses to run on any mismatch, which requires publishing a new protocol version.
- Supersedes `M11C_PROTOCOL_DRAFT.md` (whose "six fixed templates" wording is wrong).

## 1. Data, scoring and identity

- M11b v2.0 public dev 160 (SHA-256 `6c7f2ab5d09581642918ccda6f0f7cba90cbcb25ab9b7427d6c68596dc2d94fb`) and local private test 40 (`e0f9d97fc68f22f1c75e3e861b5d8b8370530ae4a634cae21d72a6f8aa2121fd`; the private manifest `1c640e0a…e75f` matches the public receipt). The m11b-v2.0 scorer and historical results are unchanged; for each configuration and run, `benchmarks.run_m11b_scoring` additionally generates an official report that must agree item by item with in-memory scoring.
- A system receives only `SystemInput(task_id, query)` and the split's complete data catalogue: for dev, all 7 m04b-v1 files (including the distractors empty, geographic_points and invalid_bow_tie); for test, the three private files regions/sources/targets, with the CRS declared by the evaluator as EPSG:3857 (only the EPSG token was extracted from the hidden builder). task_type, category, capability, expectation and source_fixtures never enter the systems.
- Everything is offline and deterministic: `model_id` and `prompt_version` are empty, 0 LLM calls, cost 0 USD; results must not be called online LLM results. Versions: system m11c-baselines-v1, front end m11c-frontend-v1, M07 grammar m07.2 (ADR-0018).

## 2. Configurations

| ID | Role | Composition |
|---|---|---|
| fixed_workflow (FW-A) | formal | The P4 fixed-pipeline architecture ported onto the same M04 data: shared front end → fixed `filter_attributes` → result encoding; search only; no M07 static gate, no clarification turn (a missing place and the like are recorded as error), no result validation/retry/replanning. |
| dynamic_agent | formal | Shared front end → M07 `plan_request` (m07.2) → direct plan execution; no result validation/retry/replanning. |
| validator_replan | formal | Shared front end → M07 → M08 `execute_validated_plan`: `allow_empty=true`, `max_features=10000`, no CRS/geometry repair, one replan re-grounded through `plan_request`. |
| template_workflow (FW-B) | dev ablation | Shared front end → one of five fixed templates, with no M07 static gate/clarification/validation/replanning; not a port of the original P4. |
| validator_no_frontend (A1) | dev ablation | Front end off: the raw query and the full catalogue go straight to M07+M08. |
| validator_strict_empty (A2) | dev ablation | Same as validator_replan, but `allow_empty=false`. |
| validator_no_replan (A3) | dev ablation | Same as validator_replan, but no replanning callback is provided. |

The five FW-B templates (each first runs `filter_attributes` on the role in brackets, then the operation): (1) search: filter(role); (2) nearest: filter(source) → `nearest_features(k)`; (3) within: filter(source) → `features_within(container)`; (4) spatial_join: filter(right) → `spatial_join(left, predicate)`; (5) aggregate_by_region: filter(values) → `aggregate_by_region(region_id_field, values_region_id_field, metric)`. Check against the frozen matrix: 24 executable public dev tasks per template, 120 in total.

Single-dataset buffer is not among them: the shared front end rejects buffer before binding (m07.2 has no production for it, GeoDSL v1 `within_distance` needs two datasets, and the user decided M11c would not upgrade GeoDSL). FW-B must have the same operation set as the agents so that the gap between FW-A and FW-B can be attributed to the search-only limitation; adding a buffer template would make 144 public tasks executable and mix in a GeoDSL contract difference. The 24 public and 8 private buffer tasks are counted in full-set coverage and failures, not removed.

## 3. Frozen capability matrix (public dev, no tools executed)

| Configuration | Executable | Notes |
|---|---|---|
| fixed_workflow | 24 | search only |
| template_workflow | 120 | five templates |
| dynamic_agent / validator_replan / validator_strict_empty / validator_no_replan | 120 | M07 status=plan |
| validator_no_frontend | 0 | raw query: 136 missing_place, 24 unsupported |

The three-way common executable subset (fixed_workflow ∧ dynamic_agent ∧ validator_replan) is the 24 public search tasks; the private subset is computed by the same rule during the formal run and only its size is published (target 8 search tasks, with no guarantee that their phrasing can be parsed).

## 4. Runs and metrics

- Seeds 11071/11072/11073 only decide task execution order; each seed runs every configuration in turn; each task gets a new DatasetRegistry. Formal configurations run on dev 160 + private 40, ablations only on dev 160; 3 times each. The private set runs only after this freeze, with no tuning afterwards; any change requires a new version and a rerun of both splits, keeping the old results.
- Primary metric: the official M11b v2.0 strict pass. Secondary metric "strict pass excluding required_steps" (diagnostic, not the official M11b pass rate): for the same prediction it ignores only the required_steps component of the pass decision, and all other components (status/reason, operation, fields, tool sequence, Feature IDs, numbers, CRS, geometry) must still pass; it must not be used to claim that non-validating systems completed end-to-end validation.
- Scopes and denominators: full (all tasks of each split; dev 160, test 40, 200 in total); common_executable; agent_executable; decision (benchmark expectation is not success); supported_unexecutable_by_formal (such as buffer). Each reports the task denominator, strict/secondary metrics, per-component passes/applicable counts, coverage, score mean/standard deviation/P50/P95, latency mean/standard deviation/P50/P95, tool calls, LLM calls, cost and status/reason/failure categories; across the 3 runs it reports the mean and sample standard deviation, with the agreement rate of per-task result signatures as stability.
- `tool_sequence`: the order of analysis tools in the round that produced the final returned result; retries and `validate_geometry` only count toward `tool_calls`. Latency includes catalogue loading, routing, execution, validation and result encoding.
- Reports: created exclusively under `benchmarks/results/m11c/formal/<run_id>/`: report.json, report.md, dev_task_results.jsonl, dev_predictions, m11b_reports, deterministic_receipt.json. Dev gets per-task results; private gets aggregates only, without queries, Feature IDs, per-task scores or seeds. `scripts.audit_m11c_reproducibility` reruns with the frozen configuration and compares the deterministic receipt (excluding times) byte for byte.
- Must be disclosed: clean data cannot demonstrate the recovery benefit of retries/replanning; if A3 equals the full validator, this is explained as the split having triggered no replanning; rank is unsupported and no ranking capability is claimed; the private set is structurally related to the public M04b fixture and the front-end recognizers come from public dev phrasing, so private results only reflect template-level generalization; M03 Phase 2 public queries are a catalog-informed internal evaluation; fixed GTFS stop buffers are not transit isochrones.

## 5. DONE criteria (not lowering the original standard)

M11c is marked DONE only when all of the following hold: this protocol and matrix are frozen before the formal runs and checked by the runner; the three formal configurations each complete dev and private evaluation 3 times and the four dev ablations 3 times each, all with exclusive outputs; the three-way common executable subset is non-empty with a like-for-like comparison; full coverage/unsupported/failures over all 200 are reported completely (buffer included); ablations, stability, error analysis, latency/calls/cost and code/working-tree fingerprints are complete; the original 448 tests are all kept, the M11b and M07–M10 audits and protected hashes are unchanged, and the M11c receipt is reproducible byte for byte. There is no pass-rate threshold; if the private common subset is empty or any condition fails, M11c stays IN_PROGRESS and a change of criteria is submitted for the user to decide.
