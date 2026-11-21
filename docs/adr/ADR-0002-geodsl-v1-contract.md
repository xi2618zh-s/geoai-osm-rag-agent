# ADR-0002: GeoDSL v1 uses strict semantic contracts and a one-way adapter from the old Workflow

- Status: Accepted
- Date: 2026-10-05
- Decision owner/Session: M01 GeoDSL contracts
- Affected modules: M01, M02, M07, M11

## Context

The old `WorkflowPlan` only expresses a fixed union/intersection export over 2–5 OSM tags. It cannot reliably carry distances, spatial relations, aggregation, ranking or multi-source data needs. M01 must establish the semantic contract that later modules share, without breaking the P4 API and execution engine that already pass their tests.

## Decision

1. GeoDSL v1 supports only the seven operations named in the plan: `search`, `within_distance`, `nearest`, `within`, `spatial_join`, `aggregate_by_region`, `rank`.
2. All GeoDSL models use Pydantic strict mode, forbid unknown fields, and pin `schema_version="1.0"` on the top-level intent, clarification and plan.
3. A v1 task must state the place, analysis CRS, data requirements and output CRS explicitly; missing parameters that affect the result are never filled with defaults.
4. Distances must carry a `m/km/mi/ft` unit. The contract keeps the input value and unit and offers a deterministic metric normalization through a `meters` property.
5. `DatasetRequirement` only describes a semantic need and does not reference stored data; the real `DatasetRef` is left to M02.
6. Add a one-way `workflow_plan_to_geodsl()`: the old union/OR maps to an `any` filter and intersection maps to `all`. The old contracts, HTTP API and execution path stay unchanged.
7. M01 delivers domain contracts and static validation only; it does not implement a dynamic planner or execute new plans.

## Alternatives

- Extend `WorkflowPlan` directly: would couple the fixed OSM workflow to general spatial semantics and introduce breaking changes. Rejected.
- Store step parameters in an arbitrary `dict`: flexible in the short term, but missing distances, wrong CRS and illegal dependencies could not be found statically. Rejected.
- Store only normalized metres: loses the user's original unit and weakens explanation and multi-turn edits. Rejected.
- Replace the `/workflow` execution chain at the same time: outside M01's scope and widens the regression surface. Rejected.

## Consequences

- Positive: M02/M04/M07/M11 share one semantic vocabulary; errors surface before execution; old features do not regress.
- Cost: adding operations or allowing tasks without a place requires a new schema version or a compatible extension.
- Testing and observability: 65 fixtures, JSON round-trips, JSON Schema, all seven operations and old-plan conversion are covered by offline tests.

## Migration and rollback

There is no runtime migration. The old `WorkflowPlan` remains the execution fact for P4. Deleting `src/geodsl/` rolls back M01 without affecting the old API. Any future two-way conversion or execution integration needs its own ADR.
