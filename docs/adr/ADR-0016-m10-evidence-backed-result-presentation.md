# ADR-0016: M10 generates result presentations from checked evidence

- Status: Accepted
- Date: 2026-10-06
- Decision owner/Session: M10 result presentation and business reporting session
- Affected modules: M10, later M11b/M11c and M12–M14
- Versions: new AnalysisResult v1.0; GeoDSL, DatasetRef, ToolSpec and the M07–M09 public contracts are unchanged.

## Context

M08 provides success/partial traces and current DatasetRefs, and M09 provides verified comparison counts, but there was no unified presentation contract linking maps, tables, charts and business wording to the actual computed results. Letting a model write reports directly produces untraceable numbers, and a static HTML page that reads old references could show stale data. The old HTTP/Workflow and the agent's tool permissions must stay compatible.

## Decision

1. Create a strict, `extra=forbid` AnalysisResult v1.0. It is built only from a VerifiedRun that passed the M07 static gate and has M08 `status=success`; steps and input/output DatasetRefs are checked item by item against their current content. For preprocessed inputs, the declared source is traced through at most 32 current parent DatasetRefs. After a successful replan, only the final successful steps count as current result evidence, and the recovery events are kept in warnings. A two-sided comparison requires two independent successes with the same CRS.
2. Metrics store their unit, evidence type and a DatasetRef or checked plan-parameter reference. Report templates may only use placeholders that reference declared metrics, never literal numbers. Before display, `validate_analysis_result` rebuilds the whole canonical result from the current registry and compares it for strict equality, rejecting rewritten values, downloads, layers or sources.
3. Search, fixed-distance coverage and verified count comparison use fixed templates; other supported analysis types use a generic template. Ranking still needs a future deterministic ranking tool, so no ranking claims are made. Fixed-distance buffers are explicitly not travel-time isochrones.
4. The page uses local, self-contained SVG to show multiple layers, a legend, feature pop-ups, metric charts, a result table and sources/steps/limitations. Data fields are written to DOM `textContent`, and embedded JSON is escaped at the script boundary. Maps show at most 250 features per layer and tables at most 50 rows, with truncation disclosed. Downloads are GeoJSON/CSV data URIs of the complete verified output, capped at 20 MB per file; CSV formula prefixes are escaped, and SHA-256/byte counts are recorded.
5. GeoJSON exported in projected coordinates carries a private `geoai_crs` field, and the report reminds RFC 7946 consumers to reproject to EPSG:4326 first. The new `GET /analysis-ui` serves only the published v2 synthetic example; it offers no new execution and reads no user paths. Demo v1/v2 and the frozen evaluation receipt are published exclusively and audited byte for byte.

## Alternatives

- Let a model generate the numbers and HTML directly: flexible styling, but numbers, sources and security boundaries cannot be verified deterministically. Rejected.
- Online base maps and browser-side remote data loading: richer map backgrounds, but they break default offline acceptance and reproducibility. Not adopted for now.
- Extend the old `/workflow` response structure directly: would affect published clients and old tests; a separate v1.0 contract and an additional read-only route are used instead.

## Consequences

- Positive: result numbers, layers, downloads and steps can be re-checked against current DatasetRefs and successful traces; the offline browser page needs no manual editing.
- Cost/risk: the page is an SVG illustration of small synthetic data without an online base map; large data would need new pagination or server-side downloads; in-process data-source identity is declared by the provider and not independently authenticated; projected GeoJSON does not follow RFC 7946's default coordinate semantics directly.
- Testing and observability: on the 12 cases frozen before development, all four thresholds score 12/12; module tests 24/24, full offline suite 437/437; the M07–M10 result audits and the M03 protected-hash checks pass. No new dependencies, real models or external network calls.

## Migration and rollback

Old routes and all old contracts remain available; there is no data migration. If the new presentation entry point misbehaves, stop using `/analysis-ui` and keep the structured M08 success results; the non-overwritable v1/v2 snapshots and evaluation receipts remain for review. Future changes to AnalysisResult, export CRS or the download mechanism need a new version and ADR and must not rewrite current results.
