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
