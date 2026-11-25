# GeoAI: from place queries to inspectable spatial analysis

A place query asks where schools are. A service-coverage question also needs population data, coordinate systems, distance semantics and evidence for the displayed conclusion. GeoAI makes those responsibilities visible: a question becomes a bounded plan, deterministic tools execute it, and the report links numbers to checked outputs.

The Lund example uses public SCB population and extracted OSM school snapshots. At 1,000 m and ten children aged 5–9, the saved calculation finds eleven cells containing 178 children. This is a straight-line coverage screen, not evidence about school capacity or street-network access. The [demo](docs/DEMO.md) and [original record](benchmarks/results/m12/m12a/formal_v1/scenario_report_m12a_A1.json) show its limits.

The previous case study records restoration of an inherited prototype. Subsequent artifacts document hybrid lexical/dense retrieval, grounded structured model decisions, deterministic OSM extraction, typed dataset/tool contracts, population/transit adapters, bounded plans, validation and reports. Historical engineering identifiers remain in technical records; this case describes the resulting system.

Documented decisions explain its shape. Hybrid retrieval was compared against a measured baseline: see [methodology](docs/BENCHMARKS.md). Interpretation stays separate from GIS execution so an ungrounded model output cannot select arbitrary tools or files: see [ADRs](docs/adr/). Report validation rebuilds metrics from execution so plausible sentences cannot substitute for evidence: see [scenario code](src/scenarios/m12a.py).

The interface work adds a thin adapter around these existing components. The public site displays an already saved analysis; the local form sends editable parameters into the real scenario. Preview executes no GIS tools. Runs have independent persistent files, errors hide earlier results, and validation failure blocks success reports. The adapters do not look up reference answers. The [verification record](docs/VERIFICATION.md) describes the acceptance checks.

Evaluation needs a precise reading. The [school protocol](benchmarks/results/m12/m12a/formal_v1/report.md) reports 13/13 authored public cases; a separate oracle implementation is by the same author. The [planner comparison](benchmarks/results/m11c_v2/M11C_V2_FINAL_REPORT.md) reports 136/160 public and 34/48 private strict passes, including required validation steps. Removing that criterion makes dynamic and validation configurations equal; formal runs triggered no replanning. Separate fault tests establish bounded recovery. Complex spatial relations and incomplete aggregation scoring limit conclusions.

Code and data have separate licences described in the README. A public real-time backend is a future scope; this delivery executes locally on included Lund data.
