# ADR-0010: M06b uses service-day stop frequencies and conservative buffer coverage

- Status: Accepted
- Date: 2026-10-05
- Decision owner/Session: M06b stop service and coverage analysis
- Affected modules: M02, M04, M05, M06, later M11/M12

## Context

M06a delivered a pinned Swedish GTFS source, bounded ZIP parsing and the `network/gtfs` DatasetRef. The next step needs deterministic tools that directly support a "stop coverage and service frequency" business case, but there is no reliable full transfer-routing network or isochrone input yet. M05 already provides standard EPSG:3006 population-grid fields and conservative fully-contained-cell semantics, and M04 provides the Shapely/pyproj spatial foundation and the new tool registry.

## Decision

1. Create strict, versioned `AnalyzeGtfsStopCoverageInput/Output.v1` and register a single `analyze_gtfs_stop_coverage` tool in a separate `ACCESSIBILITY_TOOL_REGISTRY`. That registry combines the 10 M04 tools, the 1 M05 tool and this tool; `ANALYSIS_TOOL_REGISTRY`, `POPULATION_TOOL_REGISTRY` and the old `src.agent.tools.ToolRegistry` are unchanged.
2. The input states explicitly: a GTFS DatasetRef, a `service_date`, a time zone equal to the feed's `agency_timezone`, 1–8 non-overlapping windows ordered by start, a buffer distance in metres, and optionally an M05 standardized population GeoJSON and a Point GeoJSON with `poi_id`. A time window is a half-open interval `[start,end)` in GTFS time counted from the start of the service day; values of 24 hours and more are allowed, but the end must be below 48:00:00. It is not elapsed UTC time. Trips after midnight (24:xx) still belong to the original `service_date`.
3. Effective frequency counts stop events of trips with an active service that allow regular boarding (`pickup_type=0` or empty) and have a `departure_time`. `pickup_type=1` (no boarding), `2` (phone ahead) and `3` (arrange with driver) are not counted as regular service; 2/3 produce a warning. For each stop the tool records event count, distinct trips and route IDs; for each route, event count, distinct trips and stop IDs. This is scheduled stop service frequency; it does not derive complete rideable OD journeys.
4. Metric EPSG:3006 buffers are built only for stops with service in the window. The output GeoJSON has one feature per stop per window, ordered by window input order and then `stop_id`. Population and POIs are aggregated over the geometric union of all buffers in that window, so overlapping stops are not double-counted. Population sums only non-negative, reported `population_total` of fully contained grid cells; partially intersecting cells are excluded and reported separately, and null cells are counted with a warning, so the result is a conservative lower bound of known values. POIs are Points with a unique `poi_id` and are counted once when they fall inside or on the boundary of the union.
5. Multi-window comparison uses the first window of the request as the baseline and reports differences in trip events, active stops, routes, the population lower bound and the POI count. Without optional population/POI inputs the corresponding metrics are null; with inputs but no coverage they are zero. When there is no service, a valid empty GeoJSON DatasetRef and zero statistics are still produced.
6. The GTFS input must be `network/gtfs` in EPSG:4326; population and POIs must be `vector/geojson`, and both the DatasetRef and the GeoJSON declaration must be equivalent to EPSG:3006. The output is a brand-new temporary `vector/geojson` DatasetRef with all direct parent IDs, a parameter SHA-256, a content hash and a TTL; inputs are unchanged. At most 5000 stop buffers are output, within the registry's default input/output byte limits.
7. Results always state that GTFS completeness is unverified and that buffers are not isochrones. A population snapshot date that differs from the service day, unknown population values and the absence of service all produce explicit warnings. The synthetic fixture only verifies the maths and contracts; it supports no conclusions about real Lund service levels.

## Alternatives

- Full timetable transfer routing/isochrones: needs a walking network and transfer and delay semantics, and the data are insufficient; not built in M06b.
- Sum population and POIs per stop buffer: overlaps would be double-counted; the per-window union is used instead.
- Area-weight partially covered population cells: there is no assumption about population distribution within a cell, which invites false precision; the M05 fully-contained-cell lower bound is kept.
- Buffer directly in metres on longitude/latitude: wrong units; stops are projected to EPSG:3006 with pyproj `always_xy`.

## Consequences

- Positive: stop service and coverage can be recomputed for fixed dates/windows; trips after midnight and suspended dates are testable; M05 population outputs can be used directly; no network access or new dependencies.
- Cost/risk: a buffer is only Euclidean proximity, not reachability by actual walking or travel time; a GTFS subset cannot represent the complete service supply. Service-day time is the timetable clock and is not interpreted as real elapsed seconds across DST changes. The population snapshot and the service date may differ.
- Acceptance: morning/night results on a synthetic CC0 fixture, compatibility with the real M06a subset, M05 standard population input, conditional boarding, empty results, CRS/schema failures, offline blocking, repeated content hashes and the full regression.

## Migration and rollback

The new contracts, tool and fixtures sit alongside the old assets; the old HTTP/Workflow, the M03 publication, the M04/M05 registries and data need no migration. Rolling back M06b only requires removing `ACCESSIBILITY_TOOL_REGISTRY` and the new files, without touching DatasetRef v1 or the M06a parser.
