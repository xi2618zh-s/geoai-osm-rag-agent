# ADR-0019: M11c three baselines, a shared front end and comparison protocol m11c-protocol-v1

- Status: Accepted (on 2026-10-07 the user confirmed FW-A/FW-B, the reporting criteria and the ablation scope)
- Date: 2026-10-07
- Decision owner/Session: the user; M11c session (Claude Code, cross-model handover)
- Affected modules: M11c; depends on M04, M07 (m07.2, ADR-0018), M08 and M11b v2.0. The M07–M10 public contracts, the agent tool allow-list, the M11b scorer and historical results are unchanged.

## Context

The query phrasing of the M11b benchmark matches neither the M07 restricted grammar nor the data path of the P4 fixed workflow: P4 only extracts OSM PBF tags and cannot read M04 GeoJSON. No model API is available, so the systems must be offline and deterministic, and they must never see task types, capability labels or reference answers. The comparison must use the same data and tools, report the common executable subset under the same criteria, and also report full-set coverage.

## Decision

1. The shared offline front end m11c-frontend-v1 (`src/agent/benchmark_frontend.py`) reads only the query, the dataset catalogue (role → DatasetRef) and scope labels; its recognizers were written from public dev phrasing only. It outputs an M07 command plus a structured form of the same interpretation. `rank` is rejected before binding because there is no registered ranking tool; single-dataset buffers are rejected because no planning production exists; when no catalogue dataset is named, it returns `missing_place`.
2. The system input contract SystemInput contains only `task_id` and `query`; each task gets a new DatasetRegistry loaded with the split's full catalogue. Three formal configurations: FW-A `fixed_workflow` (a port of the P4 architecture, search-only, no static gate/clarification/validation), `dynamic_agent` (M07 + direct execution) and `validator_replan` (M07 + M08, `allow_empty=true`, one re-grounded replan). Dev ablations: FW-B five templates, A1 front end off, A2 strict empty results, A3 no replanning. Fault injection (M13) is not included.
3. All systems share the M11b v2.0 result encoding (union geometry, count, `area_m2` when the union area > 0, `distance_m` as the minimum); `tool_sequence` records the tools of the round that produced the returned result. The primary metric is the official strict pass; the diagnostic secondary metric, "strict pass excluding required_steps", ignores only the required_steps component.
4. Before formal runs, the dev capability matrix and the manifest (system configurations, seeds, data/catalogue hashes, source fingerprint) are frozen exclusively, and the runner refuses to start on any mismatch. The three seeds only decide execution order; formal configurations run on dev + private, ablations only on dev, 3 times each; private results are published only in aggregate; result directories are created exclusively, and the deterministic receipt can be reproduced byte for byte by the audit.

## Alternatives

- Use P4 directly as the third configuration: its data path does not apply to M04 GeoJSON, so 0 tasks would be executable and the comparison empty; it is reported honestly as not applicable instead.
- Use FW-B as the formal third configuration: stronger, but not a port of the original fixed workflow; kept as an ablation to quantify the search-only limitation.
- Give each system its own front end: parsing differences would be confounded with architectural differences; a shared front end is used instead, and only the A1 ablation removes it.
- Use task fields such as `task_type` or `source_fixtures` to help binding: would bypass the real interface mismatch. Rejected.

## Consequences

- Positive: the three configurations are compared on the same M04 data, tools and encoder; coverage differences and validation differences can be separated (FW-A/FW-B/agent/validator and A1–A3).
- Cost/risk: the front end is rule-based, so private results only reflect template-level generalization; strict scoring requires `validate_result`, so configurations without validation necessarily fail on strict, which is why the diagnostic secondary metric is reported alongside; clean data cannot demonstrate recovery benefits.
- Testing and observability: `tests/test_m11c_baselines.py`, `test_m11c_workflows.py`, `test_m11c_runner.py`; `scripts.audit_m11c_reproducibility`; the frozen manifest/matrix hashes are in `M11C_EVALUATION_FREEZE.md`.

## Migration and rollback

All new code and results are additive; old contracts and scorers are unchanged. Changing any system, front end, grammar or protocol requires publishing a new protocol version, freezing again and rerunning both splits, while keeping the m11c-protocol-v1 results and receipt.
