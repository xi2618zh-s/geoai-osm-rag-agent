# ADR-0023: m12c tool for a single-ride in-vehicle time budget plus a fixed walking radius

- Status: Accepted (user decision D3-B, 2026-10-08); not yet implemented (the m12c session freezes the details and tests first, then implements)
- Date: 2026-10-08
- Decision owner/Session: the user; Claude Code M12 session (implemented in m12c)
- Affected modules: M06 (a simplified second-stage reachability estimate), M12

## Context

The transit-accessibility scenario wants to compare what is reachable within 15/30/45 minutes. M06b only has fixed stop buffers within service-day time windows, which are explicitly not isochrones, and the implementation plan forbids building a full transit routing engine in the same session.

## Decision

1. Add a bounded tool `analyze_transit_single_ride_reach` v1, registered only in `SCENARIO_TOOL_REGISTRY`.
2. The semantics are always described as a "single-ride, in-vehicle time budget plus a fixed walking radius": from an explicit set of origin stops, board regularly within a service-day departure window, ride the **same trip** without transfers, alight regularly at the arrival stop, and count the stop as reachable when (arrival time − origin departure time) ≤ the budget. Each stop records its shortest in-vehicle time, and reachable stops get a fixed-walking-radius Euclidean buffer in EPSG:3006. Waiting time, walking time and transfers are **not** included; the result is not called an isochrone or "total travel time".
3. Front end: when the user asks vaguely how much is reachable "in N minutes in total" or about total travel time, it clarifies first (`total_time_unsupported`, explaining that only an in-vehicle time budget plus a fixed walking radius is supported); it also clarifies when an origin name matches zero or several stops.
4. Output: reachable stops per budget, shortest in-vehicle time, route count and buffer DatasetRefs; population is given both as a fully-contained-cell lower bound (inside the tool, following M06b semantics) and as an intersecting-cell upper bound computed by the scenario chain; there is no area weighting. If there are no departures on the service day, a valid zero result is returned and scenario validation classifies it as `no_service`; the date is never changed silently.
5. Times follow the M06a/M06b rules of seconds counted from the start of the service day, including 24+ hours; the time zone must equal the feed's agency timezone.

## Alternatives

- Use only M06b multi-window coverage (D3-A): does not answer the budget question. Not chosen.
- Full transfer routing/isochrones: out of scope. Not chosen.

## Consequences

- Positive: answers the 15/30/45-minute question under clearly stated limits.
- Cost/risk: systematically underestimates real reachability (no transfers, no walking network), and reports must disclose this.

## Migration and rollback

A new tool; removing its registration rolls it back.
