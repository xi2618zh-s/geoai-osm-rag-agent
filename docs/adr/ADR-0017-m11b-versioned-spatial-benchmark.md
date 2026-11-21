# ADR-0017: M11b versioned tasks, a private split and real geometric scoring

- Status: Accepted
- Date: 2026-10-07
- Affects: M11b, later M11c; M11a v1.0 and the public behaviour of M04/M07–M10 are unchanged.

## Context

M11a's 24 visible tasks only calibrate the contracts and a lightweight bbox score; they cannot evaluate real M04 tool results. M11b has to extend the tasks, store real references and isolate hidden questions, without passing off calibration predictions as agent performance.

## Decision

1. Add a separate benchmark contract/scorer v2.0 without rewriting M11a v1.0 data, API, runner or historical results. Public dev v1 has 160 tasks and the local private test v1 has 40, 200 in total. The public set was frozen and published exclusively first; the hidden set was generated after a separate supplementary freeze.
2. References and tool order for the 144 public executable tasks and the 40 private tasks are produced by actually running the existing M04 `AnalysisToolRegistry.invoke`. Public tasks use the project-authored CC0 m04b-v1 fixture; the private split uses independently transformed synthetic geometry and random Feature IDs that never enter development fixtures. The public set also has 8 unsupported rank tasks and 8 missing-place clarification tasks. Private tasks, source data, seed and answers live in a git-ignored directory; only a hash receipt without questions is public. The complete private data can be audited only in the original workspace; in a fresh clone without the private content, module tests explicitly skip the private rebuild instead of claiming it was verified.
3. v2.0 scoring fixes status/reason, operation, required fields, semantic steps, the complete M04 tool sequence, Feature ID precision/recall/F1, numeric tolerance, explicit CRS, real geometric IoU/topological equivalence and, where applicable, nDCG. Two empty sets are equivalent; `partial` is not equivalent to `success`; invalid/missing geometry, mismatched CRS and missing fields all fail. In this version `rank` is a deterministic rejection and no ranking performance is scored.
4. The scoring entry point reads only structured tasks and predictions and does not call the tools a prediction lists. The M04 tool registry and the six M07 tool permissions are unchanged. Output directories are created exclusively and record input/configuration hashes, git state, versions, model/prompt identity and metrics; private test reports are aggregate only and never contain questions, answers, the seed or per-task scores. Generation and audits are offline, with no new dependencies.
5. M11b's reference self-check of 200/200 only shows that the data and scorer can be rebuilt; it is not a system success rate. Only M11c locks the configuration, runs the three baselines (fixed workflow, dynamic agent, validator) at least three times each and produces the final comparison. Gate D still needs all of M10–M13.

## Consequences and limitations

- Although the private set is separated from development queries and locations, its geometric structure still comes from the same small synthetic M04 contract fixture; it is not an independent external geographic source or expert annotation. The private seed is in an ignored directory and must be stored securely to rebuild on another machine. The public receipt alone cannot recover the hidden content.
- M03 Phase 2 public queries remain a catalog-informed internal evaluation. Fixed GTFS stop buffers are not real transit isochrones.
- Any later change to v2.0 fields/thresholds needs a new version and migration layer that keeps the existing tasks, private receipt and historical results without overwriting them.

- After the 160 public dev tasks were published, an exclusively created sidecar and receipt with M04 tool-call evidence were added; the original tasks/manifest were not overwritten. The hidden-query leakage audit was extended to exact-text scans of the public benchmark, agent source code, OSM knowledge assets and development fixtures.
