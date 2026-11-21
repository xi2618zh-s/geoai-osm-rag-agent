# GeoAI P1 Benchmark

## Dataset

`data/query_benchmark_v1.jsonl` contains 63 project-authored queries over all 21 labels in the current OSM Wiki knowledge base:

- 21 canonical queries;
- 21 natural-language paraphrases;
- 21 hard contrastive queries.

Each row contains a stable id, query, expected place, expected OSM key/value, category, and difficulty. This is a balanced internal regression benchmark, not a production-traffic or external human-judged dataset.

## Run

From the repository root:

```powershell
# Offline retrieval and weighted-fallback ablation.
python -m benchmarks.run_benchmark

# Add one canonical local-LLM decision query per label.
python -m benchmarks.run_benchmark --llm-limit 21

# Run three representative end-to-end queries into separate result files.
python -m benchmarks.run_benchmark `
  --output-json benchmarks/results/p1_e2e.json `
  --output-md benchmarks/results/p1_e2e.md `
  --e2e-ids default
```

The runner defaults to the locally cached embedding model so an installed benchmark does not silently depend on Hugging Face availability. Set `GEOAI_BENCHMARK_ALLOW_MODEL_DOWNLOAD=1` only when intentionally populating an empty cache.

## Metrics

- Retrieval Recall@1/3/5/10 and MRR;
- top-1 tag accuracy;
- similarity-weighted tag accuracy/coverage for top-k 1/3/5/10 and thresholds 0/0.15/0.25;
- JSON validity, strict schema validity, grounded tag rate, place/tag accuracy and fallback trigger rate for the selected LLM sample;
- E2E success, chosen-tag accuracy, LLM success and feature counts;
- mean/P50/P95 latency for retrieval, LLM and E2E stages.

## P1 baseline conclusion

The dense retriever has high candidate recall on the current internal set (Recall@5 95.2%, MRR 0.8854), but the original weighted aggregation is not an accuracy improvement: k=5 reaches 77.8%, compared with 82.5% for top-1. Hard contrastive queries are the main source of degradation. The canonical Qwen sample reaches 95.2% tag accuracy with 100% JSON/schema/grounding validity, while CPU latency remains high. Three representative E2E cases pass, but the sample is deliberately small to control Nominatim usage and local CPU runtime.

Do not generalize these numbers beyond the versioned dataset. Query wording and ground truth should receive human review before results are published or compared.

## M11a benchmark framework calibration

```powershell
python -m benchmarks.run_m11a_benchmark
```

This fully offline command validates 24 visible synthetic dev tasks against a
versioned calibration prediction file and writes both JSON and Markdown into a
new `benchmarks/results/runs/<UTC timestamp>-<config hash>/` directory. It scores
outcome/reason, operation, exact fields, required semantic steps, Feature ID
precision/recall/F1, numeric tolerance, bbox IoU, and ranking nDCG. The report
also includes score mean/standard deviation/P50/P95, latency quantiles, tool/LLM
call counts, estimated cost, input hashes, code revision, and failure categories.

The bundled predictions intentionally contain one or more examples of all eight
failure categories and therefore pass 15/24 tasks. This is a scorer calibration,
not a dynamic-Agent, Validator, hidden-test, or production benchmark. Supply a
future system's prediction JSONL with `--predictions`; do not overwrite or edit
an existing run directory.

## P2 reliability regression

```powershell
python -m benchmarks.run_benchmark `
  --llm-limit 0 `
  --output-json benchmarks/results/p2_regression.json `
  --output-md benchmarks/results/p2_regression.md

python -m benchmarks.run_p2_validation
```

`p2_regression.*` confirms that reliability changes preserve the 63-query P1 retrieval metrics. `p2_runtime_validation.*` runs the same real natural-language query twice and records trace IDs, dependency attempts, per-stage timings, decision source, feature counts, and clip-cache state. Cache numbers are a two-run local diagnostic, not a throughput benchmark.

## P3 hybrid retrieval

```powershell
# Compare 15 dense, BM25, tag-dedup and hybrid configurations on P1.
python -m benchmarks.run_p3_experiment

# Run the same configuration grid on the 21-query validation/challenge set.
python -m benchmarks.run_p3_experiment `
  --dataset benchmarks/data/query_benchmark_p3_challenge.jsonl `
  --output-json benchmarks/results/p3_challenge_experiment.json `
  --output-md benchmarks/results/p3_challenge_experiment.md

# Unified final-config regression. Dense remains the runner default for P1 reproducibility.
python -m benchmarks.run_benchmark `
  --retriever-mode hybrid `
  --output-json benchmarks/results/p3_hybrid_baseline.json `
  --output-md benchmarks/results/p3_hybrid_baseline.md
```

On the 63-query internal set, the final hybrid configuration reaches Recall@1/5/10 100%, MRR 1.0 and top-1 tag accuracy 100%, versus dense Recall@1 82.5%, Recall@5 95.2%, MRR 0.8854 and top-1 82.5%. In the same experiment, retrieval P50/P95 increases from 14.8/18.1 ms to 22.1/28.5 ms.

The 21-query challenge set also reaches 100% with the final configuration, but it informed the final 1:2 dense-to-lexical weight choice and is therefore a validation set, not a blind holdout. Both query sets, the aliases and the catalog are project-authored.

`p3_llm.*` deliberately separates retrieval from downstream decision quality: on 21 canonical queries Qwen final decision accuracy is 85.7%, despite 100% retrieval. `p3_e2e.*` records three successful real OSM executions. The result files retain the failure cases needed to audit that gap.

## P4 constrained workflow

```powershell
# Offline plan compilation only.
python -m benchmarks.run_p4_workflow

# Add one real multi-tag Nominatim/OSM execution.
python -m benchmarks.run_p4_workflow `
  --e2e-query "Find cafes or restaurants in Lund"
```

`data/workflow_benchmark_v1.jsonl` contains 12 project-authored English OR queries. The runner reports plan validity, operation/place/filter-set/exact-plan accuracy and planning P50/P95. Optional E2E output records chosen tags, per-tag/type counts, step success, recovery, clip cache state, total latency, trace/workflow IDs and step records.

The final 12-case run reaches 100% plan validity and exact-plan accuracy with 43.9/57.7 ms planning P50/P95. The real Lund query produces 173 unioned objects from 50 cafés and 123 restaurants, with 6/6 successful steps and 7.32 s in-process latency. This is a small internal benchmark, not a blind external or production result.

## M03 Phase 2 public retrieval evaluation

The immutable query release is `benchmarks/data/m03/phase2_v3_2026-10-06_v1/`: 410 bilingual development queries and 205 public evaluation queries, one public target per each of the 205 canonical tags. The public set is visible, not hidden. Its one formal run used `benchmarks/config/m03_phase2_public_eval_v1.json` after the development run and wrote `benchmarks/results/m03/public_eval/m03_phase2_public_eval_v1/` plus an exclusive receipt in `benchmarks/results/m03/public_eval_receipts/`. Do not rerun or overwrite that experiment.

The runner emits separate dense, BM25 and hybrid metrics, language/category macro results, retained-100/new-105 cohorts, predictions, errors, and latency. See `docs/roadmap/EVALUATION_PROTOCOL.md` for exact numbers and commands. Scenario questions are catalog-informed rather than externally annotated; the high lexical result is not a generalization claim. The original default-thread build reproduces all seven index files byte for byte. A single-thread cross-environment build has a small floating-point difference in one vector, without changing top-five ranks on any frozen query. M03 and Gate B are complete under the project plan; external generalization remains untested.


## M07 offline planning evaluation

- Public v1/v2 cases: benchmarks/data/dev/m07_plan_cases_v1.jsonl and m07_plan_cases_v2.jsonl.
- Run: conda run -n geoai_project_env python -m benchmarks.run_m07_planning. The default v2 result path is exclusive-create; choose a new --output path for reruns.
- The 80 supported v2 cases measure strict plan legality, exact tool sequence and exact business parameters; 10 rank cases must be rejected. Results are visible synthetic development evidence, not hidden or real-model performance.
## M08 offline fault evaluation

Run `conda run -n geoai_project_env python -m scripts.audit_m08_reproducibility` to recompute the immutable v1 receipt byte for byte. The public synthetic matrix has 16 cases: clean first pass 4/4, recovered faults 4/4, classified injected faults 12/12, guarded rejection 8/8. The original result is exclusive-create at `benchmarks/results/m08/dev/m08_fault_eval_v1.json`; do not overwrite it. Fixtures and failures are small and mocked, without real model, network or production evidence. M03 Phase 2 retrieval queries remain catalog-informed.
## M09 offline multi-turn evaluation

The frozen public matrix is `benchmarks/data/dev/m09_turn_cases_v1.jsonl` (16 cases: distance, exclusion, city source switch and verified count comparison). The exclusive v1 receipt is `benchmarks/results/m09/dev/m09_turn_eval_v1.json`. Recompute it byte for byte with `conda run -n geoai_project_env python -m scripts.audit_m09_reproducibility`. All four metrics are 100% on visible synthetic fixtures; this is a contract and reuse check, not real-city, hidden-set, live-model or production evidence. M03 Phase 2 retrieval queries remain catalog-informed.

## M10 offline result presentation evaluation

The pre-code frozen public matrix is `benchmarks/data/dev/m10_result_cases_v1.jsonl` (12 synthetic cases: search, fixed-distance coverage, verified count comparison). The exclusive receipt is `benchmarks/results/m10/dev/m10_result_eval_v1.json`; four evidence and presentation metrics score 12/12. Recompute the receipt and both immutable demo snapshots byte for byte with `conda run -n geoai_project_env python -m scripts.audit_m10_reproducibility`. The active standalone demo is `docs/demo/m10_analysis_result_v2.html` and is also served at GET /analysis-ui. The data are small visible fixtures, without a live model, hidden test, real city coverage or travel-time isochrone. M03 Phase 2 queries remain catalog-informed.
## M11b v2.0 public dev and private holdout

- Public development tasks: benchmarks/data/dev/m11b_tasks_v1.jsonl (160); manifest: m11b_manifest_v1.json. The independent M11a 24-task calibration and its known-wrong predictions remain unchanged.
- The private 40-task test payload, synthetic source geometry and seed live only in Git-ignored benchmarks/data/test/private/. The tracked m11b_private_receipt_v1.json contains hashes and counts, never queries or answers. Transfer the private package securely when reproducing it elsewhere. Do not put its content in prompts, aliases or development fixtures.
- Rebuild/audit in the original workspace: conda run -n geoai_project_env python -m scripts.audit_m11b_reproducibility. This verifies real M04 reference outputs, byte identity and leakage boundaries. The 200 reference self-checks are scorer calibration, not system performance.
- Score supplied v2.0 predictions: conda run -n geoai_project_env python -m benchmarks.run_m11b_scoring --tasks benchmarks/data/dev/m11b_tasks_v1.jsonl --predictions <path> --system-id <id>. JSON and Markdown are written to a new immutable run directory. Private test reports are aggregate only. M11c will supply and compare fixed Workflow, dynamic Agent and Validator runs; no such comparison is part of M11b.

## M11b trace provenance

The public m11b_tool_traces_v1.jsonl records actual M04 allow-listed calls
for each supported task: tool/version, input content hashes, parameter hash,
output content hash and output row count. Its exclusive receipt is
m11b_tool_traces_receipt_v1.json. The task/manifest files were not rewritten
when this audit evidence was added. The M11b audit rebuilds all of them byte
for byte and scans private query strings against public benchmarks, Agent
source, knowledge assets and development fixtures. Byte identity holds on the
reference platform (Windows); on other platforms GEOS can differ in the last
floating-point digit, so the offline tests there check the rebuild structurally
with floats equal within a relative 1e-9.

## M11c v1 baseline comparison (offline, deterministic)

- Protocol m11c-protocol-v1 is frozen in docs/roadmap/M11C_EVALUATION_FREEZE.md (ADR-0018, ADR-0019; English translation in M11C_EVALUATION_FREEZE.en.md). Systems see only task_id + query and the split's full dataset catalogue; no language model is called (0 LLM calls, 0 cost).
- Configurations: formal fixed_workflow (FW-A, port of the P4 fixed pipeline onto M04 data, search only), dynamic_agent (M07 m07.2 + direct execution), validator_replan (M07 + M08). Public-dev ablations: template_workflow (FW-B, five fixed templates), validator_no_frontend (A1), validator_strict_empty (A2), validator_no_replan (A3). Three seeded runs each.
- Commands: conda run -n geoai_project_env python -m benchmarks.run_m11c_baselines prefreeze|freeze|run, then python -m scripts.audit_m11c_reproducibility. run refuses to start if the frozen manifest, capability matrix or source fingerprint changed, and writes a new exclusive directory under benchmarks/results/m11c/formal/.
- Published result m11c_comparison_v1: public dev strict pass FW-A 8/160, dynamic 16/160, validator 136/160; diagnostic "strict pass excluding required_steps" 32, 136, 136. Common executable subset (24 dev search tasks): strict 0, 0, 24; diagnostic 24 each. Private test: 0/40 for all three because the dev-derived front-end parsed none of the hidden queries; private common subset is empty, so v1 alone did not meet the M11c completion criterion (v2 below did). Private results are aggregate only.
- The old P4 OSM-PBF workflow is not applicable to M04 GeoJSON tasks and is not one of the configurations. Single-dataset buffer tasks (24 dev, 8 private) are unsupported by every configuration and counted as failures; rank is unsupported.

## M11c v2: local model front-end and sealed private v2 (m11c-protocol-v2)

- Protocol: docs/roadmap/M11C_V2_EVALUATION_FREEZE.md (stage 1), docs/roadmap/M11C_V2_STAGE2_FREEZE.md (stage 2, manifest under benchmarks/results/m11c_v2/freeze/), ADR-0020; English translations sit next to each freeze document as `*.en.md`. The same seven systems as v1 receive decisions from a local qwen2.5:3b semantic-frame front-end (Ollama 0.13.5, CPU only, temperature 0) with deterministic grounding; one model call per task per run is shared by all model-using configurations.
- Commands: python -m benchmarks.run_m11c_v2 dev-iterate | freeze-stage2 | run-dev | run-private --workspace <custodian workspace> | replay-private | scan-leaks, then python -m scripts.audit_m11c_v2_reproducibility (replays recorded raw responses; no model call). Offline tests use a fake transport and never call the model.
- Public dev (7 configs x 3 runs): gate passed in every run (validator strict 136/160, rank 8/8, missing place 8/8, 0/160 model-call failures); counts identical to v1 (FW-A 8, dynamic 16, validator 136). 120/160 is only the entry gate.
- Sealed private v2 (48 tasks, 3 formal configs x 3 runs, aggregate only): strict FW-A 6, dynamic 12, validator 34; three-way common executable subset 5 (strict 0/0/5). Summary: benchmarks/results/m11c_v2/M11C_V2_FINAL_REPORT.md.

## M12a school coverage gaps scenario (m12a-protocol-v1)

- Protocol: docs/roadmap/M12A_EVALUATION_FREEZE.md (English translation in M12A_EVALUATION_FREEZE.en.md) and the freeze manifest benchmarks/results/m12/m12a/freeze/m12a_freeze_manifest_v1.json (ADR-0021, ADR-0024). The front end is a restricted command grammar (fixed English templates with explicit slots), not open-ended natural-language input; no language model is called.
- Data: SCB 2025 1 km grid (CC0, 453 cells returned for the Lund study rectangle including edge cells) and OpenStreetMap school/supermarket/pharmacy points extracted from a local PBF (ODbL, provider unverified). Population sums are over the returned cells and are not the exact population of the rectangle.
- Commands: python scripts/freeze_m12a.py verify; python -m benchmarks.run_m12a_scenario run (exclusive outputs under benchmarks/results/m12/m12a/formal_v1/ and docs/demo/m12a_school_coverage_gaps_v1.html); python -m scripts.audit_m12a_reproducibility.
- Result: 13/13 frozen public-dev cases passed with identical deterministic receipts across two runs (receipt bf46afba...1fda4a). References come from an independent oracle written by the same author (independent implementation, not independent labelling); this is not a hidden test set.
