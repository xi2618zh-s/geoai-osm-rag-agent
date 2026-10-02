# Portfolio case study: evidence-grounded geospatial agent

## Problem

OpenStreetMap is structurally precise but difficult for non-specialists: a user
asks for “schools” or “airport terminals”, while execution requires correct
`key=value` semantics, spatial bounds and geometry handling. A language model can
interpret intent but should not be trusted to invent tags, coordinates or files.

## Engineering approach

I separated semantic decision from deterministic execution. A domain RAG layer
ranks documented OSM tags; a local model returns strict structured output; schema
and evidence grounding reject invalid choices. Nominatim, osmium and pyosmium then
run only validated intents against real PBF data.

The inherited prototype was restored before optimization. P1 established a
quantitative baseline. P2 added contracts, trace/timing, retry and cache. P3
introduced BM25+dense hybrid ranking. P4 added constrained multi-tag plans,
allow-listed tools, step state, failure recovery and provenance GeoJSON. P5 makes
the result reviewable through a clean README, official data/checksum flow,
activation-free Windows entrypoints, offline CI and an evidence-aligned demo.

## Hard decisions

- Kept Qwen 2.5 3B as the verified default on the target CPU; Mistral remains optional.
- Rejected cross-chunk weighted voting after it underperformed top-1.
- Added hybrid retrieval only after a baseline and 15-configuration ablation.
- Did not add a cross-encoder after current internal retrieval errors reached zero.
- Rejected arbitrary LLM tool calling in favor of a testable allow-list and DAG.
- Rejected an unverified Docker artifact; delivered the path validated on Windows.

## Evidence

- Dense baseline: internal Recall@1 82.5%, MRR 0.8854.
- Hybrid: internal Recall@1 100%, MRR 1.0, with about 7–10 ms added retrieval latency.
- Qwen downstream final decision: 85.7% on 21 internal queries.
- P4 planning: 12/12 internal exact plans.
- Real P4 HTTP workflow: 173 café/restaurant objects and 6/6 successful steps.
- Offline regression grew from 10 tests at P1 to 43 tests before P5 delivery tests.

## What this demonstrates

RAG/search evaluation, structured LLM output, failure-safe agent tooling, Python
backend engineering, geospatial data processing, experiment design, Windows
compatibility, reproducibility and honest technical communication.

## Boundaries

The knowledge base and benchmarks are small and project-authored. The service is
local-first, not production hardened. There is no autonomous planner, multi-agent
system, long-term memory, public SLA or external accuracy validation.
