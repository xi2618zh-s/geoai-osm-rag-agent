# P4 Constrained Workflow Benchmark

Generated: 2026-08-04T21:55:13.416604+00:00
Dataset: `benchmarks/data/workflow_benchmark_v1.jsonl`

## Plan compilation

| Metric | Result |
|---|---:|
| Cases | 12 |
| Plan validity | 100.0% |
| Operation accuracy | 100.0% |
| Place accuracy | 100.0% |
| Filter-set accuracy | 100.0% |
| Exact-plan accuracy | 100.0% |
| Planning latency P50 | 43.9 ms |
| Planning latency P95 | 57.7 ms |

## Per-case results

| Case | Valid | Exact | Predicted filters |
|---|---:|---:|---|
| workflow_food_lund | True | True | amenity=cafe, amenity=restaurant |
| workflow_aviation_lund | True | True | aeroway=aerodrome, aeroway=terminal |
| workflow_school_lund | True | True | amenity=school, building=school |
| workflow_rail_uppsala | True | True | railway=halt, railway=station |
| workflow_shops_lund | True | True | shop=convenience, shop=supermarket |
| workflow_leisure_lund | True | True | leisure=park, leisure=swimming_pool |
| workflow_services_malmo | True | True | amenity=hospital, amenity=police |
| workflow_road_lund | True | True | amenity=parking, highway=traffic_signals |
| workflow_culture_lund | True | True | amenity=cinema, amenity=library |
| workflow_buildings_lund | True | True | building=residential, building=school |
| workflow_legacy_airport_vasteras | True | True | aeroway=aerodrome, aeroway=airport |
| workflow_transport_uppsala | True | True | highway=bus_stop, railway=station |

## Real multi-tag E2E

| Metric | Result |
|---|---:|
| Success | True |
| Operation | union |
| Chosen tags | amenity=cafe, amenity=restaurant |
| Features | 173 |
| Step success | 100.0% |
| Steps | 6 |
| Recovered steps | 0 |
| Clip cache hit | True |
| E2E latency | 7324.5 ms |

## Scope and limitations

- Natural-language P4 planning deliberately supports explicit English OR queries only.
- Free-form AND is rejected because ordinary conjunction is ambiguous; structured plans can request object-level intersection.
- The 12 cases and tag catalog are project-authored, not an external production benchmark.
- This is a constrained deterministic planner and allow-listed tool workflow, not autonomous planning or multi-agent execution.
