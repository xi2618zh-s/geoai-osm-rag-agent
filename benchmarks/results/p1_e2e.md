# P1 Benchmark Baseline

Generated: 2026-08-04T13:12:33.965821+00:00
Dataset: `benchmarks\data\query_benchmark_v1.jsonl`
Cases: 63 across 21 labels

## Retrieval

| Metric | Result |
|---|---:|
| Recall@1 | 82.5% |
| Recall@3 | 95.2% |
| Recall@5 | 95.2% |
| Recall@10 | 96.8% |
| MRR | 0.8854 |
| Top-1 chunk tag accuracy | 82.5% |
| Retrieval latency P50 | 17.4 ms |
| Retrieval latency P95 | 28.5 ms |

### Weighted fallback ablation

| top-k | min score | accuracy | coverage |
|---:|---:|---:|---:|
| 1 | 0.00 | 82.5% | 100.0% |
| 1 | 0.15 | 82.5% | 100.0% |
| 1 | 0.25 | 82.5% | 100.0% |
| 3 | 0.00 | 79.4% | 100.0% |
| 3 | 0.15 | 79.4% | 100.0% |
| 3 | 0.25 | 79.4% | 100.0% |
| 5 | 0.00 | 77.8% | 100.0% |
| 5 | 0.15 | 77.8% | 100.0% |
| 5 | 0.25 | 77.8% | 100.0% |
| 10 | 0.00 | 74.6% | 100.0% |
| 10 | 0.15 | 74.6% | 100.0% |
| 10 | 0.25 | 74.6% | 100.0% |

### Results by query category

| Category | N | Recall@5 | Weighted accuracy (k=5, threshold=0.15) |
|---|---:|---:|---:|
| canonical | 21 | 95.2% | 90.5% |
| hard | 21 | 95.2% | 57.1% |
| paraphrase | 21 | 95.2% | 85.7% |

## Sampled end-to-end execution

N=3

| Metric | Result |
|---|---:|
| E2E success | 100.0% |
| Chosen-tag accuracy | 100.0% |
| LLM success | 100.0% |
| E2E latency P50 | 105548.5 ms |
| E2E latency P95 | 106748.1 ms |

| Case | Success | Tag correct | LLM OK | Features |
|---|---:|---:|---:|---:|
| amenity_cafe_canonical | True | True | True | 50 |
| leisure_park_canonical | True | True | True | 170 |
| highway_bus_stop_canonical | True | True | True | 1204 |

## Error analysis

Expected tag absent from top-10 chunks:

- `aeroway_airport_canonical` expected `aeroway=airport`, top-1 was `amenity=cafe`.
- `aeroway_airport_paraphrase` expected `aeroway=airport`, top-1 was `aeroway=aerodrome`.

## Limitations

- The dataset is a balanced, project-authored benchmark over the current 21-tag knowledge base, not an external human-judged production corpus.
- LLM evaluation uses one canonical query per tag by default; E2E evaluation is deliberately sampled to respect Nominatim and control local CPU runtime.
- Accuracy values describe this versioned dataset and must not be presented as universal production performance.
- Query wording and ground truth should receive human review before results are published or compared.
