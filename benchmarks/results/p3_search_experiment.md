# P3 Search Experiment

Generated: 2026-08-04T19:10:03.176181+00:00
Dataset: `benchmarks\data\query_benchmark_v1.jsonl`

| Configuration | R@1 | R@5 | R@10 | MRR | Top-1 | Fallback k5 | P50 | P95 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| dense_chunk_p1 | 82.5% | 95.2% | 96.8% | 0.8854 | 82.5% | 77.8% | 14.8 ms | 18.1 ms |
| bm25_tag | 98.4% | 100.0% | 100.0% | 0.9921 | 98.4% | 98.4% | 6.3 ms | 10.2 ms |
| dense_tag_dedup | 82.5% | 96.8% | 98.4% | 0.8918 | 82.5% | 82.5% | 19.9 ms | 24.4 ms |
| hybrid_rrf_1_0.25 | 85.7% | 96.8% | 98.4% | 0.9120 | 85.7% | 85.7% | 18.8 ms | 23.5 ms |
| hybrid_rrf_1_0.5 | 87.3% | 96.8% | 100.0% | 0.9224 | 87.3% | 87.3% | 18.6 ms | 23.2 ms |
| hybrid_rrf_1_1 | 93.7% | 98.4% | 100.0% | 0.9577 | 93.7% | 93.7% | 24.1 ms | 31.8 ms |
| hybrid_rrf_1_2 | 95.2% | 100.0% | 100.0% | 0.9709 | 95.2% | 95.2% | 21.1 ms | 29.4 ms |
| hybrid_rrf_0.5_1 | 95.2% | 100.0% | 100.0% | 0.9709 | 95.2% | 95.2% | 19.4 ms | 26.4 ms |
| hybrid_rrf_0.25_1 | 95.2% | 100.0% | 100.0% | 0.9722 | 95.2% | 95.2% | 20.3 ms | 26.3 ms |
| hybrid_rrf_1_1_match_0.05 | 100.0% | 100.0% | 100.0% | 1.0000 | 100.0% | 100.0% | 22.2 ms | 29.8 ms |
| hybrid_rrf_1_1_match_0.15 | 100.0% | 100.0% | 100.0% | 1.0000 | 100.0% | 100.0% | 20.4 ms | 24.9 ms |
| hybrid_rrf_0.5_1_match_0.15 | 100.0% | 100.0% | 100.0% | 1.0000 | 100.0% | 100.0% | 18.9 ms | 24.9 ms |
| hybrid_rrf_1_1_match_0.15_neg_0.25 | 100.0% | 100.0% | 100.0% | 1.0000 | 100.0% | 100.0% | 20.3 ms | 23.8 ms |
| hybrid_rrf_1_2_match_0.15_neg_0.25 | 100.0% | 100.0% | 100.0% | 1.0000 | 100.0% | 100.0% | 22.1 ms | 28.5 ms |
| hybrid_rrf_0.5_1_match_0.15_neg_0.25 | 100.0% | 100.0% | 100.0% | 1.0000 | 100.0% | 100.0% | 20.8 ms | 26.5 ms |

Selected: `hybrid_rrf_1_1_match_0.15_neg_0.25`

Selection rule: maximize top-1 accuracy, then MRR, Recall@5, and finally lower P95 latency. The dense P1 baseline remains in the same report.

## Limitations

- The 63-query dataset and tag catalog are both project-authored; aliases are derived from the local OSM Wiki snapshot, so this is an internal regression/ablation rather than an independent external evaluation.
- Configuration selection on this dataset can overfit it. P3 claims must retain the internal-benchmark qualifier.
- Cross-encoder reranking is not included in this experiment; the current corpus is only 21 labels/144 dense chunks, and the experiment first tests whether lower-cost hybrid fusion is sufficient.
