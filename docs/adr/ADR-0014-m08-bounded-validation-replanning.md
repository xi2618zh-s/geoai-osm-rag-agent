# ADR-0014: M08 independent validation and bounded replanning

- Status: Accepted
- Date: 2026-10-06
- Affected modules: M08, later M09/M10
- Versions: ValidationPolicy, ValidationFinding, RecoveryEvent, StepExecution, ValidationOutcome v1.0; the old DatasetRef, M07 plans and M04 ToolSpec are unchanged.

## Context

M07 only generates and statically validates plans, and M04 returns deterministic DatasetRefs. After a plan runs, the actual results still need checking, recovery from transient faults must be controlled, and model proposals must never drive unbounded loops or turn wrong results into a success.

## Decision

1. Add a separate `execute_validated_plan`. Before execution the plan passes the M07 static gate again, and only the six public registered tools are bound to checked DatasetRefs. The dataset registry validates the current reference, content hash and readability; both inputs and outputs are checked for vector kind, CRS, fields, geometry, counts and the empty-result policy, and finally the user's count constraint and strict count conclusion are checked.
2. A timeout with identical parameters is retried at most once. CRS/geometry repair and substitute DatasetRefs are only allowed when an explicit ValidationPolicy permits them; repairs call existing M04 tools and do not widen the set of tools visible to the model. When there is no equivalent public substitute analysis tool, no tool switch is invented.
3. Injected replanning happens at most once; every field and business parameter of the original M07PlanRequest must stay the same, and the new plan passes the M07 static gate again. Each run makes at most 24 tool calls; retries, repairs, substitutions and replanning all write structured recovery records. Failures return `partial`, a stable error code and explicit limitations; original computed values are never changed.
4. M08 v1.0 checks textual counts only through the strict `Found N features.` template; M10 free-text reports and M09 multi-turn state are not part of this stage.
5. The evaluation's public 16-case fault matrix, denominators and thresholds were frozen before the code was written; the receipt is created exclusively and recomputed byte for byte. All experiments are offline, and neither M03 phase asset is overwritten.

## Compatibility and limitations

- The old GeoDSL, Workflow, DatasetRef, ToolSpec, M07 `/plan-preview`, M04/M05/M06 registries and existing HTTP routes are unchanged; no new dependencies.
- Role source identifiers are still declared by the caller; M08 validates data content and metadata but does not authenticate sources independently. There are no default real-model calls, external services, real transit isochrones or proof of production deployment.
- Substitute tools, free-text conclusions, session state or wider permissions would need new contracts and evaluation.

## Evidence

- M08 module tests 25/25; on the frozen 16 cases: clean first pass 4/4, fault recovery 4/4, classification 12/12, guarded rejection 8/8; the byte-level receipt audit passes.
- Full offline suite 386/386, compilation and existing reproducibility audits pass; the M07 starting baseline was 361/361, and the original 235 M03 tests are all kept.
