# ADR-0009: M06a uses a pinned GTFS Sverige 2 historical archive and bounded local ingestion

- Status: Accepted
- Date: 2026-10-05
- Decision owner/Session: M06a GTFS ingestion
- Affected modules: M02, M06, later M11/M12

## Context

M02's `DatasetRef` v1 already reserves `network/gtfs`, but the local registry previously implemented only GeoJSON/CSV. M06 needs real Swedish timetable data and inputs that can be re-checked offline, without depending on a changing "latest" feed, an API key or unauthorized third-party mirrors. The full national archive is about 47.7 MB compressed, with `stop_times.txt` at about 391 MB uncompressed, which is unsuitable as a repository fixture or as an unbounded runtime input at this stage.

## Decision

1. The only official source is GTFS Sverige 2 from Trafiklab/Samtrafiken, licensed CC0-1.0. The chosen public historical archive is `https://data.samtrafiken.se/trafiklab/gtfs-sverige-2/2025/10/sweden-20251004.zip`, source version `gtfs-sverige-2-20251004`, internal `feed_version=2025-10-04`, with pinned length, ETag and SHA-256. The official description and archive locations are at `https://www.trafiklab.se/api/gtfs-datasets/gtfs-sverige-2/`.
2. `scripts/fetch_gtfs_sverige2.py` is the only explicit network boundary. It requests only the fixed URL above, rejects unexpected redirects, Content-Type, length, ETag, SHA-256 and invalid ZIPs, downloads into same-disk staging, publishes atomically on success and refuses to overwrite by default. The original 47.7 MB ZIP stays outside the repository and is not committed.
3. `scripts/build_gtfs_sverige2_fixture.py` is fully offline. From the verified archive it selects the complete `stop_times` chains of five real trips and keeps the agency/route/stop/service rows they reference, plus calendar, calendar_dates and feed_info. Field values are unchanged; row ordering, UTF-8/LF CSV, fixed ZIP metadata and a canonical manifest guarantee byte-level rebuilds. Only the small Lund fixture is committed, with licence, source, time, source version and per-file SHA-256.
4. `src.gtfs.ingest` validates a fixed, stop-based GTFS Schedule: required files, CSV headers and widths, unique IDs, agency/route/trip/stop/service foreign keys, stop coordinates, time zone, service dates, calendar and calendar_dates exceptions, and monotonic `stop_sequence` and times. `HH:MM:SS` may exceed 24 hours and still belongs to the original service date; when there is no valid service an empty tuple is returned instead of inventing service.
5. `LocalDatasetRegistry` adds `.zip`/`.gtfs` input support for `network/gtfs` under the existing v1 `DatasetRef` contract. Registration first copies into staging and validates completely; on success the URI is `registry://datasets/<id>/data.gtfs`, the CRS must be `EPSG:4326`, `row_count` and `read()` describe stops, and the bbox comes from the stops. Later tools that need the full timetable resolve the ZIP through the controlled `resolve_path()`. Existing vector/geojson and table/csv semantics are unchanged.
6. At runtime M06a accepts only bounded feeds: at most 20 MB compressed, at most 50 MB uncompressed in total and at most 30 MB per member. The full national archive cannot be registered directly. If M06b needs a larger area it must make an explicit slicing/streaming decision instead of silently relaxing the limits. M06a provides no transfer routing or stop-coverage statistics.

## Alternatives

- Trafiklab's latest feed: changes over time and needs an API key, so offline acceptance cannot be pinned. Not adopted.
- Third-party mirrors, Taginfo and similar: their sources and licences are outside this scope. Not adopted.
- Commit the national ZIP or read it without bounds: repository and memory costs are too high; a reproducible small subset from a pinned source is used instead.
- Add a third-party GTFS parsing dependency: the required files and relations can be handled strictly with the standard library at this stage. No new dependency.

## Consequences

- Positive: source, licence and version are traceable; network and offline runs are isolated; the old DatasetRef contract and HTTP/Workflow behaviour are unchanged; the small sample covers trips past midnight, date exceptions and days without service.
- Cost/risk: only bounded, fixed stop-based feeds are supported at this stage. Runtime streaming queries over the national archive, frequency/buffer/population aggregation, full routing and real operational ambiguity around DST boundaries are not handled. `read()` returns stops rather than all GTFS tables, so callers must use the dedicated parser.
- Testing and observability: 14 offline M06a tests, a full regression of 191/191, and byte-identical repeated builds of the three fixture files; a failure never publishes a partial DatasetRef.

## Migration and rollback

Old GeoJSON/CSV data, M03 Phase 1, historical benchmarks and M05 fixtures are neither migrated nor overwritten. The new format is isolated in the `network/gtfs` branch; removing that branch and `src/gtfs` rolls back M06a without changing old data formats or the public `DatasetRef` v1.
