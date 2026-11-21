# ADR-0021: A separate M12 scenario layer (restricted command grammar, typed scenario plans and evidence reports)

- Status: Accepted (conditionally approved by the user on 2026-10-08; m12a implements the catalogue/front end/plan/execution/report, ScenarioSession is implemented in m12b)
- Date: 2026-10-08
- Decision owner/Session: the user; Claude Code M12 session
- Affected modules: M12 (new `src/scenarios/`); read-only reuse of M04–M11

## Context

M12 requires each flagship scenario to have at least five tool steps, multiple data sources, multi-turn edits and fault recovery. The current M07 public allow-list has only six M04 tools, 1–3 steps per plan and no rank support; M08 executes only M07 plans (re-derivation, six-tool payloads, a 24-call cap); M10 accepts only a single M07/M08 run. The M11c v1/v2 audits rerun the systems with the current code, so the behaviour of M07–M10 on existing inputs must not change.

## Decision

1. Add a separate package `src/scenarios/`. All public contracts are strict, `extra="forbid"`, `schema_version="1.0"`: `ScenarioCatalogue`/`CatalogueEntry`, `ScenarioIntent`, `ScenarioStep`, `ScenarioPlan`, `StepRecord`, `ScenarioOutcome`, `ScenarioReport` (with its metric/layer/table/download/source/licence sub-contracts); m12b adds `ScenarioSession`. The M08 `ValidationPolicy`/`RecoveryEvent`/`ValidationFinding` contracts are reused without modification. Clarification uses a new `ScenarioClarification` v1.0 contract: GeoDSL `ClarificationRequest.reason_code` is a closed five-value enumeration (missing_place, missing_distance, ambiguous_target, conflicting_constraints, unsupported_operation) that cannot express scenario-slot reasons, and changing it would be a public-contract change, so it is neither reused nor modified (corrected before the freeze on 2026-10-08).
2. The front end is a **restricted command grammar** per scenario (deterministic, English, no LLM), and documentation and UI use exactly that name, without claiming open-ended natural-language understanding. Missing, conflicting or out-of-range slots return `ScenarioClarification`; `resume` accepts only answers in the prescribed format; business parameters are never filled silently.
3. `SCENARIO_TOOL_REGISTRY` = the 12 tools of `ACCESSIBILITY_TOOL_REGISTRY` + scenario tools approved by ADR (ADR-0022, ADR-0023). `ANALYSIS_TOOL_REGISTRY`, `POPULATION_TOOL_REGISTRY`, `ACCESSIBILITY_TOOL_REGISTRY` and M07 `PUBLIC_TOOLS` are unchanged.
4. Static gate: tools must be on the scenario allow-list and registered; parameters are validated by each tool's strict input model; roles exist, dependencies are closed, there are no forward references and output roles are not reused; at most 12 steps; every step declares an `empty_policy` (allow|reject).
5. The executor follows M08 semantics: currency/content hash, kind, CRS, required fields, `validate_geometry` and feature caps of the input and every step's output DatasetRef, plus a per-step empty-result policy; a timeout with identical parameters is retried once; verified substitute snapshots declared in the policy; at most 1 replan overall (business parameters unchanged, the new plan passes the static gate again); a cap of 64 tool calls (including validation calls).
6. **CRS plausibility and reprojection rules**: before execution, each input's DatasetRef bbox must lie within the area of use of its declared CRS (pyproj `area_of_use` after conversion, with a tolerance) and intersect the catalogue's study area; otherwise `SCENARIO_CRS_IMPLAUSIBLE` is returned, rejecting the run without calling analysis tools or reprojecting. The planner inserts an explicit `reproject_dataset` step only when the source CRS has been confirmed by a trusted record (the preview states the reason). There are only two recoveries from a wrong declaration: (a) `reconfirm_source_crs`: if a versioned manifest records a file SHA-256 that matches the DatasetRef content hash and declares the source CRS, the data are re-registered with that CRS as a new DatasetRef (lineage `crs_reconfirmed`, recording the parent ID and the evidence hash), otherwise `SCENARIO_CRS_EVIDENCE_INVALID`; (b) switch to a verified snapshot declared in the policy that passes the same checks, and replan once. The scenario executor never calls a reprojection "repair" on wrongly declared data.
7. `ScenarioReport` follows the M10 rules: report/title templates forbid literal numbers and use only placeholders for registered metrics; metric evidence types are `dataset_row_count`, `dataset_field_sum`, `dataset_field_extreme`, `dataset_field_null_count`, `tool_output_value`, `plan_parameter`, `difference`, `ratio`; `validate_scenario_report` rebuilds the canonical report and any deviation raises `SCENARIO_REPORT_EVIDENCE_MISMATCH`; HTML uses self-contained SVG, `textContent` writes and CSV formula escaping; licences and attributions are listed separately.
8. No new HTTP routes; M07–M11 contracts, behaviour and frozen results are unchanged.

## Alternatives

- Extend the M07 grammar and public allow-list to carry multi-step scenarios: changes public contracts and could break the M11c audits. Not chosen.
- Split scenarios into several chained M07 sub-plans: intermediate DatasetRefs would have to pass between sub-plans through new roles, and M05/M06b/ranking tools still could not be used. Not chosen.

## Consequences

- Positive: scenario chains of 6–9 steps across sources, fully auditable; zero behaviour change in existing modules.
- Cost/risk: partly parallel implementation to M08 (consistency is enforced through same-named contracts and tests); the whole-tree source fingerprint changes (the M11c audits do not check the fingerprint, and the formal runs are complete).
- Testing and observability: scenario-layer module tests, frozen-case evaluation and byte-level audits; the full offline regression and existing audits re-checked serially.

## Migration and rollback

A purely additive module; deleting `src/scenarios/` with its tests and results rolls it back without affecting existing interfaces or data.
