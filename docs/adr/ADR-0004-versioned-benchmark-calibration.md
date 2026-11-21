# ADR-0004: The benchmark uses strict contracts, deterministic scoring and non-overwritable run directories

- Status: Accepted
- Date: 2026-10-05
- Scope: M11, M04–M10, M12–M14

## Context

M11a starts before the dynamic agent, the validator and most GIS tools exist. Calling replayed results on public fixtures "system performance" at this point would create false evidence; designing the evaluation only after every module is finished would leave interfaces, traces and results without a stable target. The evaluation also has to support several scoring criteria at once: exact fields, step order, feature sets, numeric tolerance, spatial approximation and ranking.

## Decision

1. `src/evaluation/` provides version `1.0` strict contracts for tasks, predictions, traces, steps, task scores and run reports; unknown fields are always rejected.
2. M11a commits only a visible `dev` calibration set. Future hidden queries never enter the repository, prompts, aliases or development fixtures.
3. The total score combines deterministic scorers only. If an LLM judge is added later it must be reported separately and must never override deterministic spatial metrics.
4. In M11a, required steps are semantic step labels and do not impersonate tool names that M04 has not registered yet; assertions on real tool sequences are added only after the M04 interfaces are stable.
5. Every run writes an independent `<UTC timestamp>-<config hash>` directory; an existing directory is an error, and historical results are never overwritten in place.
6. The built-in calibration predictions deliberately contain known errors to exercise the eight failure categories. Their 15/24 pass rate only shows that the scorer separates success from failure; it is not agent performance.
7. Reports record the code commit, working-tree state, data and prediction hashes, configuration hash, random seed, and model and prompt versions; when no model or prompt is used this is recorded explicitly as empty.

## Alternatives

- Build the evaluation framework only after M07/M08: rejected, because tools and the agent would lack a stable acceptance target.
- Generate all-correct predictions from the expected values: rejected, because it cannot show that failure classification and partial scores work.
- Keep a single `latest.json`: rejected, because it overwrites history and makes reproduction and comparison impossible.
- Freeze concrete M04 tool names now: rejected, because the tool contracts are not implemented yet and this would create a false dependency.

## Consequences

- Later modules can plug in through the same prediction/trace contracts without changing the public dev task format.
- M11b still has to add real data, geometric-equivalence scoring and 150–200 tasks; only M11c compares the fixed workflow, the dynamic agent and the validator.
- `benchmarks/results/runs/` is ignored by default so that local run history is not mistaken for a frozen baseline; results meant for publication must be audited by hand and then versioned separately.

## Rollback and migration

A breaking contract change needs a new schema/scorer version plus a migration layer that reads the old version. Published run directories and hashes must never be rewritten.
