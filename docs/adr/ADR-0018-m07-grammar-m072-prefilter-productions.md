# ADR-0018: M07 grammar m07.2 additive productions (thresholds, multiple conditions and pre-filters)

- Status: Accepted (on 2026-10-07 the user agreed in principle to a "versioned, additive M07 grammar" as a prerequisite improvement for M11c; this ADR does not approve M11c DONE)
- Date: 2026-10-07
- Decision owner/Session: the user; M11c session (Claude Code, cross-model handover)
- Affected modules: M07 (planning grammar), M08 (accepts the new plans automatically through `plan_request`), M11c (baseline comparison); GeoDSL v1, the M04 tool allow-list and M09/M10 behaviour are unchanged.

## Context

All 144 public supported tasks of M11b v2.0 require threshold/multi-condition attribute filtering as a separate `filter_attributes` step (for example `score gte 5`, `value at least 10`) or a single-dataset buffer. The frozen m07.1 grammar has only a single `FIELD = VALUE` search, spatial operations have no pre-filter, and `within_distance` has buffer + spatial_join two-dataset semantics; M08 re-derives the plan through `plan_request` and requires exact equality. A public dev probe showed that, submitted as is, M07 returns `missing_place` for 136 tasks (including 8 rank tasks) and `M07_UNSUPPORTED_TASK` for the 24 buffer tasks; even after lossless rewriting, the dynamic agent and the validator can plan 0 of the 144 supported tasks. This is a real interface mismatch between the benchmark and the planner, and it must not be bypassed with reference answers or the tasks' capability/expectation fields.

## Decision

1. Append five m07.2 productions after all m07.1 productions. They are reached only by inputs that match no m07.1 production, so the plans of all old commands stay byte-identical:
   - `search ROLE where COND (and COND)* in PLACE` → conditional_search (operation search): one `filter_attributes` step, logic `all`.
   - `find K nearest TARGET to SOURCE where CONDS in PLACE` → filtered_nearest: filter(SOURCE) → `nearest_features`.
   - `find SOURCE where CONDS within CONTAINER in PLACE` → filtered_within: filter(SOURCE) → `features_within`.
   - `join LEFT with RIGHT where CONDS on PRED in PLACE` → filtered_spatial_join: filter(RIGHT) → `spatial_join`.
   - `aggregate OP [FIELD] by RID from VALUES where CONDS into REGIONS using VRID in PLACE` → filtered_aggregate_by_region: filter(VALUES) → `aggregate_by_region`.
2. `COND := FIELD OP VALUE`, with OP ∈ {=, !=, >, >=, <, <=} mapped to eq/ne/gt/gte/lt/lte. VALUE may only be a number (integer/decimal) or an identifier; at most 8 conditions, forming an exact conjunction, and every field must be declared in the request. The GeoDSL intent records conditions as AttributeFilter; new plans have `source_version` `m07.2`, while old productions keep `m07.1`.
3. The tool allow-list, budgets, dependency closure, dangerous-text scan and CRS rules all reuse the m07.1 static gate; filtered_nearest requires a projected CRS just like nearest. `rank` still returns `M07_UNSUPPORTED_TASK`.
4. No single-dataset buffer production is added: GeoDSL v1 `within_distance` requires two different datasets, and upgrading GeoDSL is outside M11c's scope (user decision). The 24 public and 8 private single-dataset buffer tasks in M11b are listed as unsupported in M11c and counted in full-set coverage.

## Alternatives

- Leave M07 unchanged: fully frozen, but the dynamic agent/validator could execute 0 M11b supported tasks and the comparison would shrink to 16 rejection/clarification tasks. Kept as an option for the user; not adopted.
- Let the adapter drop conditions it cannot express: would silently change user constraints and produce wrong answers. Rejected.
- Extend plans privately in M11c code to bypass the M07/M08 static gate: M08 could not execute them and the single source of truth would be broken. Rejected.
- Upgrade GeoDSL to support single-dataset buffers at the same time: a Gate A public-contract change, which the user decided not to make in this session.

## Consequences

- Positive: the dynamic agent and the validator can attempt the five M11b supported task types; in public dev validation, 120/120 new plans have tool sequences that match the M04 references (results in `benchmarks/results/m11c/prefreeze/`).
- Cost/risk: the added restricted English sentence patterns are still not open-domain language understanding; M10 uses the generic analysis template for the new task classes. Condition values are limited to numbers/identifiers.
- Testing and observability: 15 tests in `tests/test_m07_grammar_extension.py` (including that the frozen v1/v2 cases are still m07.1 with unchanged tools and business parameters, and that new plans executed through M08 match an independent direct M04 call chain feature by feature); the expected first run was red on 10/15. The original 126 M07 tests and the M07 v1/v2, M08, M09 and M10 receipts are unchanged under byte-level audit.

## Migration and rollback

No data migration; old commands and old receipts are unchanged. Rolling back means deleting the five appended productions and their `_build` branches, which naturally restores m07.1 behaviour; published M11c results must be kept and labelled with the grammar version they depend on, m07.2.
