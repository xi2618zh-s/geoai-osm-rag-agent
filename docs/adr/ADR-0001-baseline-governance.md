# ADR-0001: Repository control files as the single cross-session source of truth

- Status: Accepted
- Date: 2026-10-05
- Decision owner/Session: M00 baseline governance
- Affected modules: M00–M14

## Context

The upgrade plan spans many independent sessions. Chat context, the external environment and mutable data are not stable enough to hand work over between them. The code already carries P0–P5 interfaces and results, and later modules will add GeoDSL, DatasetRef, GIS tools and an evaluation system. Relying on conversational memory alone makes it easy to mistake a planned capability for an implemented one.

## Decision

`docs/roadmap/` is the single cross-session source of truth: status, interfaces, data, tools, evaluation protocols and handoff records must be updated whenever a module finishes. Code and tests are the behavioural facts; if a document conflicts with code that passes its tests, record the discrepancy first and then correct the document. Planned interfaces that are not yet implemented must be explicitly marked as not to be relied on.

## Alternatives

- Rely only on chat summaries: cheap, but neither versioned nor reviewable. Rejected.
- Use an external project-management service: collaborative, but introduces connectors that are not installed and cannot guarantee offline handoff. Not adopted in M00.
- Keep everything in one long document: prone to merge conflicts and blurs the line between interfaces, data and status. Rejected.

## Consequences

- Positive: a new session can restore context from the repository alone; the boundary between plan and implementation is reviewable.
- Cost: several related indexes must be maintained at the end of every module.
- Testing and observability: every handoff must include commands and their actual results; benchmark numbers must link to machine-readable files.

## Migration and rollback

Existing code and APIs are unchanged; there is no runtime migration. If the governance approach changes later, add a new ADR and keep these files as history.
