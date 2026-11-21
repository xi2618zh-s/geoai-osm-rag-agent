# M11c pre-freeze public-dev verification

Not the formal M11c comparison: one run per agent system on public dev only; no private data read; offline deterministic systems with 0 model calls.

| Group (task_type/benchmark capability) | Tasks | dynamic_agent strict | dynamic_agent excl. required_steps | validator_replan strict | validator_replan excl. required_steps |
|---|---|---|---|---|---|
| aggregate_by_region/supported | 24 | 0 | 24 | 24 | 24 |
| nearest/supported | 24 | 0 | 24 | 24 | 24 |
| rank/unsupported | 8 | 8 | 8 | 8 | 8 |
| search/clarification | 8 | 8 | 8 | 8 | 8 |
| search/supported | 24 | 0 | 24 | 24 | 24 |
| spatial_join/supported | 24 | 0 | 24 | 24 | 24 |
| within/supported | 24 | 0 | 24 | 24 | 24 |
| within_distance/supported | 24 | 0 | 0 | 0 | 0 |

P4 fixed workflow front-end codes (not applicable to M04 GeoJSON tasks): {"PLAN_OPERATION_UNSUPPORTED": 96, "PLAN_PLACE_MISSING": 64}
