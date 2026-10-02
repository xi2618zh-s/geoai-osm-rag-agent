# P3 Hybrid Challenge Validation

Generated: 2026-08-04T21:03:02.077691+00:00
Dataset: `benchmarks\data\query_benchmark_p3_challenge.jsonl`
Cases: 21 across 21 labels
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
| Retrieval latency P50 | 19.5 ms |
| Retrieval latency P95 | 26.2 ms |

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
| validation_challenge | 21 | 100.0% | 100.0% |

## Error analysis

Every expected tag appeared within the top-10 retrieved chunks.

## Limitations

- The dataset is a balanced, project-authored benchmark over the current 21-tag knowledge base, not an external human-judged production corpus.
- LLM evaluation uses one canonical query per tag by default; E2E evaluation is deliberately sampled to respect Nominatim and control local CPU runtime.
- Accuracy values describe this versioned dataset and must not be presented as universal production performance.
- Query wording and ground truth should receive human review before results are published or compared.
