# P3 Hybrid + Qwen Decision Benchmark

Generated: 2026-08-04T20:22:12.418294+00:00
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
| Retrieval latency P50 | 21.2 ms |
| Retrieval latency P95 | 42.0 ms |

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

## LLM decision sample

Model: `qwen2.5:3b`; N=21

| Metric | Result |
|---|---:|
| JSON validity | 100.0% |
| Strict schema validity | 85.7% |
| Grounded tag rate | 85.7% |
| Place accuracy | 100.0% |
| Raw LLM tag accuracy | 71.4% |
| Fallback trigger rate | 14.3% |
| Final decision accuracy | 85.7% |
| LLM latency P50 | 58372.6 ms |
| LLM latency P95 | 64050.0 ms |

## Error analysis

Every expected tag appeared within the top-10 retrieved chunks.

## Limitations

- The dataset is a balanced, project-authored benchmark over the current 21-tag knowledge base, not an external human-judged production corpus.
- LLM evaluation uses one canonical query per tag by default; E2E evaluation is deliberately sampled to respect Nominatim and control local CPU runtime.
- Accuracy values describe this versioned dataset and must not be presented as universal production performance.
- Query wording and ground truth should receive human review before results are published or compared.
