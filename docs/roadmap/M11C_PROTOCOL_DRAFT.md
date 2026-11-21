# M11c comparison protocol draft and fixed-workflow options (DRAFT, not frozen)

- Superseded by `M11C_EVALUATION_FREEZE.md` (m11c-protocol-v1). The "six fixed templates" wording in this draft is wrong; the frozen version has five templates (without single-dataset buffer). What follows is kept as a historical draft.
- Date: 2026-10-07. Status: only after the user confirms the fixed-workflow option and the reporting criteria is a separate, non-overwritable `M11C_EVALUATION_FREEZE.md` published and formal running started. This draft produces no formal scores.
- Completed prerequisites: ADR-0018 (M07 grammar m07.2); public dev pre-freeze validation `benchmarks/results/m11c/prefreeze/20261006T203100Z-dev-prefreeze/` (capability matrix + one minimal run per agent; no private data read).

## 1. Pre-freeze validation findings (public dev only, single run)

| Public dev group | Count | Front end/M07 decision | Validator/replan strict | Dynamic agent strict | Dynamic agent excluding required_steps |
|---|---|---|---|---|---|
| search | 24 | plan: filter, matches the M04 reference sequence | 24 | 0 | 24 |
| nearest / within / spatial_join / aggregate | 96 | plan: filter→operation, matches the reference sequence | 96 | 0 | 96 |
| within_distance (single-dataset buffer) | 24 | front end rejects with unsupported_buffer | 0 | 0 | 0 |
| rank | 8 | front end rejects with unsupported_rank | 8 | 8 | 8 |
| missing place | 8 | clarification missing_place | 8 | 8 | 8 |
| Total | 160 | executable 120 | 136 | 16 | 136 |

- (1) Binding: a system receives only task_id + query and the split's complete data catalogue (for dev, all 7 m04b-v1 files, including the distractors empty/geographic_points/invalid_bow_tie). Roles are matched by singular/plural forms of query nouns against catalogue roles (the last segment of the file name). The analysis scope (the M07 place) is bound to the catalogue scope only when the query names at least one catalogue dataset. Aggregation join keys and measure fields are inferred from a unique schema match, otherwise left to M07 clarification. The 8 missing-place queries return `missing_place` whether given the full catalogue or an empty one.
- (2) rank: the front end recognizes the rank action first, and because ANALYSIS_TOOL_REGISTRY has no ranking tool it returns `unsupported_rank` before place binding (consistent with the M07 rank policy), 8/8.
- (3) Execution: 120/120 planned tool sequences match the M04 references; the validator outputs pass every component 120/120. The dynamic agent's outputs are identical, but because it does not validate results it produces no `validate_result` semantic step, so M11b v2.0 strict scoring fails on required_steps.
- Old P4: the front end gives 96 PLAN_OPERATION_UNSUPPORTED and 64 PLAN_PLACE_MISSING; its data path extracts OSM PBF tags and does not read M04 GeoJSON DatasetRefs, so it is marked not applicable and not used as the third configuration.

## 2. Fixed-workflow options (awaiting user confirmation)

Recommended FW-A: m11c-fixed-workflow-v1, a port of the P4 architecture onto the same M04 data and tools.
- It shares m11c-frontend-v1, the same data catalogue, the same M04 registry and the same result encoder with the agents; the only difference is the architecture.
- The same fixed linear steps and single search capability as P4: load catalogue → `filter_attributes(role, conditions)` → encode result. No M07 static gate, no clarification turn, no result validation, no retry/replanning; conditions go straight to M04 FilterAttributesInput (M04's own contract checks still apply).
- Non-search interpretations return `status=error, reason=unsupported_<operation>` (corresponding to P4's PLAN_OPERATION_UNSUPPORTED; mapped the same way as for the agents, so rank gets `unsupported_rank`). A missing place returns `status=error, reason=missing_place` (P4 has no clarification, corresponding to PLAN_PLACE_MISSING), which fails strict scoring because the status does not match.
- Target coverage (only a target before running): 24 executable on dev (search), 8 on private (if private search phrasing can be parsed). Three-way common executable subset = 24 public + 8 private search tasks.
- Alternative FW-B: six fixed templates (a fixed filter→operation chain chosen by the front end's operation), likewise without M07 static gate/clarification/validation. The common subset grows to 120 public + 32 private, but on clean data it differs from the dynamic agent almost only in clarification and the static gate; it is not a port of the "existing fixed workflow".

## 3. Comparison rules proposed for freezing

- Data and scoring: M11b v2.0 public dev 160 (SHA-256 `6c7f2ab5…94fb`) and private test 40 (`e0f9d97f…21fd`); scorer m11b-v2.0 unchanged. The private catalogue CRS is declared by the evaluator as EPSG:3857.
- Configuration identity: system m11c-baselines-v1, front end m11c-frontend-v1, grammar m07.2, M08 policy `allow_empty=true`/`max_features=10000`/no CRS or geometry repair/one re-grounded replan. Everything offline: `model_id` and `prompt_version` are empty, 0 LLM calls, 0 cost; results must not be called online LLM results.
- Runs: each of the three configurations 3 times × (dev 160 + private 40); seeds 11071/11072/11073 only decide task execution order; a new DatasetRegistry per task. The private set runs only after this protocol is frozen, with no tuning afterwards; any change needs a new version, a rerun of both splits and keeping the old results.
- Scope: (1) full set of 200: every task counts, and unsupported/rejected/failed all count as failures, with coverage and unsupported reported separately (including the 24 public and 8 private single-dataset buffer tasks); (2) the three-way common executable subset (per the frozen matrix) is the main like-for-like comparison; (3) the two-agent common executable subset; (4) the rejection/clarification decision subset reported separately.
- Metrics: the primary metric is the official strict pass; a pre-registered secondary metric, "result accuracy excluding required_steps", separates the validation-step requirement from spatial-result correctness and does not replace the strict score. Also reported: component pass rates, score mean/standard deviation/P50/P95, latency mean/standard deviation/P50/P95, tool calls, LLM calls, cost, failure categories; mean/standard deviation across the 3 runs and per-task agreement (status, pass, failure category, Feature ID set hash).
- Definition of `tool_sequence`: the order of analysis tools in the round that produced the final returned result; retries and `validate_geometry` checks count toward `tool_calls` but are not written into the sequence again.
- Ablations (dev only): A1 front end off (raw query passed directly to M07+M08); A2 validator with strict empty-result policy `allow_empty=false`; A3 validator without replanning. Optional A4: seeded injection of transient timeouts to compare recovery by the dynamic agent and the validator (overlaps with M13 fault injection; to be confirmed whether to include it).
- Reports: JSON/Markdown created exclusively under `benchmarks/results/m11c/<run_id>/`, recording the git commit, dirty flag and working-tree source fingerprint (path + SHA-256 summary of `src` and the relevant benchmarks/scripts), data/catalogue/configuration hashes, seeds, versions, environment and thread variables. Dev gets per-task results; private gets aggregates only, without queries, Feature IDs, per-task scores or seeds.

## 4. Proposed DONE criteria (not lowering the original standard)

M11c is marked DONE only when all of the following hold: the protocol and capability matrix are frozen before the formal runs with hashes recorded; each of the three configurations completes dev and private evaluation at least 3 times with exclusive outputs; the three configurations have a non-empty common executable subset with a like-for-like comparison; full-set coverage/unsupported/failures over all 200 are reported completely (buffer included); ablations, stability, error analysis, latency/calls/cost and fingerprints are complete; the original 448 tests are all kept, and the M11b/M07–M10 audits and protected hashes are unchanged. There is no pass-rate threshold (M11c is a measurement module); if the user accepts neither option in section 2 or the common-subset criterion, M11c stays IN_PROGRESS.
