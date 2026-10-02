# P3 Hybrid E2E Regression

Generated: 2026-08-04T20:45:35.154063+00:00
Dataset: `benchmarks\data\query_benchmark_v1.jsonl`
Cases: 63 across 21 labels
Retriever: `hybrid`

## Retrieval

| Metric | Result |
|---|---:|
| Recall@1 | 100.0% |
| Recall@3 | 100.0% |
| Recall@5 | 100.0% |
| Recall@10 | 100.0% |
| MRR | 1.0000 |
| Top-1 chunk tag accuracy | 100.0% |
| Retrieval latency P50 | 18.4 ms |
| Retrieval latency P95 | 23.8 ms |

### Weighted fallback ablation

| top-k | min score | accuracy | coverage |
|---:|---:|---:|---:|
| 1 | 0.00 | 100.0% | 100.0% |
| 1 | 0.15 | 100.0% | 100.0% |
| 1 | 0.25 | 100.0% | 100.0% |
| 3 | 0.00 | 100.0% | 100.0% |
| 3 | 0.15 | 100.0% | 100.0% |
| 3 | 0.25 | 100.0% | 100.0% |
| 5 | 0.00 | 100.0% | 100.0% |
| 5 | 0.15 | 100.0% | 100.0% |
| 5 | 0.25 | 100.0% | 100.0% |
| 10 | 0.00 | 100.0% | 100.0% |
| 10 | 0.15 | 100.0% | 100.0% |
| 10 | 0.25 | 100.0% | 100.0% |

### Results by query category

| Category | N | Recall@5 | Weighted accuracy (k=5, threshold=0.15) |
|---|---:|---:|---:|
| canonical | 21 | 100.0% | 100.0% |
| hard | 21 | 100.0% | 100.0% |
| paraphrase | 21 | 100.0% | 100.0% |

## Sampled end-to-end execution

N=3

| Metric | Result |
|---|---:|
| E2E success | 100.0% |
| Chosen-tag accuracy | 100.0% |
| LLM success | 100.0% |
| E2E latency P50 | 65677.0 ms |
| E2E latency P95 | 75736.0 ms |

| Case | Success | Tag correct | LLM OK | Features |
|---|---:|---:|---:|---:|
| amenity_cafe_canonical | True | True | True | 50 |
| leisure_park_canonical | True | True | True | 170 |
| highway_bus_stop_canonical | True | True | True | 1204 |

## Error analysis

Every expected tag appeared within the top-10 retrieved chunks.

## Limitations

- The dataset is a balanced, project-authored benchmark over the current 21-tag knowledge base, not an external human-judged production corpus.
- LLM evaluation uses one canonical query per tag by default; E2E evaluation is deliberately sampled to respect Nominatim and control local CPU runtime.
- Accuracy values describe this versioned dataset and must not be presented as universal production performance.
- Query wording and ground truth should receive human review before results are published or compared.
