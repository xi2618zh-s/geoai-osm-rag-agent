# ADR-0005: A new DatasetRef analysis tool registry, with Shapely 2 for topology validation

- Status: Accepted
- Date: 2026-10-05
- Decision owner/Session: M04a tool contracts and quality
- Affected modules: M04, M07–M13

## Context

The existing `src.agent.tools.ToolRegistry` serves the old OSM export workflow: its inputs and outputs include in-process objects and file paths, and its `ToolSpec` is a dataclass. The upcoming M04 tools must exchange only `DatasetRef`s, enforce strict Pydantic contracts, apply resource limits and verify that inputs are not modified in place. Upgrading the old registry directly would change the frozen Workflow v1 behaviour.

The geometry-quality tool also has to detect topology problems such as self-intersection. When M04a started, the environment had no Shapely, GeoPandas, pyproj or Fiona, and pure-Python structural checks cannot reliably decide topological validity.

## Decision

1. Keep `src.agent.tools` as the compatibility layer for the old workflow, without changing its contracts or default registry.
2. Add `src.tools`, whose `ToolSpec` v1, inputs, outputs, metadata and errors are all strict Pydantic contracts.
3. The new registry accepts only allow-listed tools. Before a call it validates the input schema, the current DatasetRef metadata, kind, object count and input bytes; after the call it validates input integrity, the output schema, the output DatasetRef and output bytes.
4. Every M04 tool declares `mutates_input=false`; after the handler returns, the registry re-checks content hashes and metadata and reports violations as `ANALYSIS_TOOL_INPUT_MUTATED`.
5. M04a registers only `inspect_dataset_schema` and `validate_geometry`; neither produces a new DatasetRef, and neither is exposed to dynamic LLM selection.
6. `validate_geometry` uses `shapely>=2,<3` (version 2.1.2 in the acceptance environment). It reports valid, invalid and empty geometries, type counts and a capped list of issues, and never repairs silently.
7. All tool outputs share lineage based on `DatasetLineage`: operation, parent IDs, a timezone-aware timestamp, a parameter hash, statistics and warnings.
8. Default limits are 10,000 objects, 50,000,000 input bytes and 5,000,000 output bytes; later tools may build their own registry configuration but may not bypass the contracts.

## Alternatives

- Upgrade the old `src.agent.tools.ToolSpec` in place: rejected; it widens the regression scope and breaks the Workflow v1 compatibility boundary.
- Check GeoJSON structure only: rejected; it cannot detect common topology errors such as self-intersection.
- Introduce GeoPandas/Fiona/pyproj immediately: rejected; M04a only needs topology validation, and dependencies such as reprojection are decided explicitly in M04b.
- Repair invalid geometries automatically: rejected; quality checks must not change data silently, and `repair_geometry` is a later, explicit tool.

## Consequences

- Positive: later analysis tools share one serializable, limited and traceable execution boundary; old features are unaffected.
- Cost: for a while there are two `ToolSpec` classes with the same name in different namespaces; callers must use `src.tools.ToolSpec` for the new DatasetRef tools.
- Security: tools cannot receive arbitrary paths, stale references and tampered inputs are rejected, and both the issue list and the output size are capped.

## Migration and rollback

M04b/M07 must target only the new `src.tools` registry. The old workflow may keep using `src.agent.tools` until a migration is planned separately. Rolling back M04a means deleting `src/tools/` and the Shapely dependency, without affecting the old API.
