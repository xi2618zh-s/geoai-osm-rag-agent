# ADR-0022: m12b deterministic multi-criteria ranking tool `rank_candidates` v1

- Status: Accepted (user decision D1-A, 2026-10-08); not yet implemented (the m12b session freezes the details and tests first, then implements)
- Date: 2026-10-08
- Decision owner/Session: the user; Claude Code M12 session (implemented in m12b)
- Affected modules: M12 (scenario registry); M07/M11 unchanged

## Context

Retail site selection needs multi-criteria ranking. M04/M07 explicitly do not support rank, and in M11 rank tasks are scored as unsupported under a frozen protocol.

## Decision

1. Add a deterministic tool `rank_candidates` v1, registered only in `SCENARIO_TOOL_REGISTRY`; it is not added to M04 `ANALYSIS_TOOL_REGISTRY` or M07 `PUBLIC_TOOLS`. Rank stays unsupported in M07/M11, and the frozen M11 protocol, results and audits are unaffected.
2. Input: a registered vector DatasetRef, a unique candidate ID field, 1–8 criteria `{field, direction: max|min, weight>0}`, `top_k` (1–100), `normalization="min_max"` (the only v1 value) and `null_policy="exclude"` (the only v1 value). Weights must be explicit and sum to 1 (tolerance 1e-9); otherwise the static gate rejects the plan and triggers a clarification. The front end generates equal weights only when the user explicitly answers `equal weights`.
3. Semantics: each criterion is min–max normalized over the set of non-null eligible candidates (max direction `(v−min)/(max−min)`, min direction `(max−v)/(max−min)`); a constant criterion normalizes to 0 with a warning; `score = Σ w·norm`; the sort key is `(-score, candidate ID)`; score differences ≤ 1e-12 produce a tie warning. Candidates with null values are excluded and their IDs listed in metadata.
4. Output: a new temporary DatasetRef (all eligible candidates in rank order, with added `rank`, `score`, `norm_<field>` and `in_top_k`); metadata records the criteria, weights, per-criterion min/max, excluded IDs and warnings; inputs are unchanged and lineage and parameter hashes are complete. Field conflicts, non-numeric values, duplicate IDs, invalid weights and similar cases have stable error codes (frozen in m12b).
5. Evaluation: an independent oracle computes the reference ranks; scoring covers exact top-K order, Kendall τ over the full order, nDCG@K and a score tolerance.

## Alternatives

- Add a rank production to M07: changes a public contract and conflicts with the frozen `unsupported_rank` expectation in M11. Not chosen.
- Sort only in the report layer: not an auditable tool step. Not chosen.

## Consequences

- Positive: explainable, re-checkable multi-criteria ranking.
- Cost/risk: min–max normalization depends on the candidate set, so scores are not directly comparable across runs; weights are subjective, and reports must disclose them.

## Migration and rollback

A new tool; removing its registration rolls it back without affecting existing registries.
