# P3 Search Experiment

Generated: 2026-08-04T21:02:53.641515+00:00
Dataset: `benchmarks\data\query_benchmark_p3_challenge.jsonl`

| Configuration | R@1 | R@5 | R@10 | MRR | Top-1 | Fallback k5 | P50 | P95 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| dense_chunk_p1 | 76.2% | 95.2% | 100.0% | 0.8378 | 76.2% | 76.2% | 14.4 ms | 20.0 ms |
| bm25_tag | 95.2% | 100.0% | 100.0% | 0.9683 | 95.2% | 95.2% | 5.0 ms | 9.8 ms |
| dense_tag_dedup | 76.2% | 100.0% | 100.0% | 0.8651 | 76.2% | 76.2% | 18.5 ms | 21.9 ms |
| hybrid_rrf_1_0.25 | 71.4% | 100.0% | 100.0% | 0.8413 | 71.4% | 71.4% | 20.0 ms | 23.5 ms |
| hybrid_rrf_1_0.5 | 71.4% | 100.0% | 100.0% | 0.8492 | 71.4% | 71.4% | 16.3 ms | 22.2 ms |
| hybrid_rrf_1_1 | 85.7% | 100.0% | 100.0% | 0.9286 | 85.7% | 85.7% | 17.2 ms | 23.6 ms |
| hybrid_rrf_1_2 | 100.0% | 100.0% | 100.0% | 1.0000 | 100.0% | 100.0% | 16.7 ms | 22.0 ms |
| hybrid_rrf_0.5_1 | 95.2% | 100.0% | 100.0% | 0.9762 | 95.2% | 95.2% | 17.1 ms | 50.4 ms |
| hybrid_rrf_0.25_1 | 95.2% | 100.0% | 100.0% | 0.9762 | 95.2% | 95.2% | 16.3 ms | 21.0 ms |
| hybrid_rrf_1_1_match_0.05 | 90.5% | 100.0% | 100.0% | 0.9524 | 90.5% | 90.5% | 15.4 ms | 17.9 ms |
| hybrid_rrf_1_1_match_0.15 | 90.5% | 100.0% | 100.0% | 0.9524 | 90.5% | 90.5% | 15.5 ms | 18.6 ms |
| hybrid_rrf_0.5_1_match_0.15 | 95.2% | 100.0% | 100.0% | 0.9762 | 95.2% | 95.2% | 16.5 ms | 18.0 ms |
| hybrid_rrf_1_1_match_0.15_neg_0.25 | 90.5% | 100.0% | 100.0% | 0.9524 | 90.5% | 90.5% | 16.9 ms | 20.0 ms |
| hybrid_rrf_1_2_match_0.15_neg_0.25 | 100.0% | 100.0% | 100.0% | 1.0000 | 100.0% | 100.0% | 15.7 ms | 18.9 ms |
| hybrid_rrf_0.5_1_match_0.15_neg_0.25 | 95.2% | 100.0% | 100.0% | 0.9762 | 95.2% | 95.2% | 15.9 ms | 20.6 ms |

Selected: `hybrid_rrf_1_2_match_0.15_neg_0.25`

Selection rule: maximize top-1 accuracy, then MRR, Recall@5, and finally lower P95 latency. The dense P1 baseline remains in the same report.

## Limitations

- The 63-query dataset and tag catalog are both project-authored; aliases are derived from the local OSM Wiki snapshot, so this is an internal regression/ablation rather than an independent external evaluation.
- Configuration selection on this dataset can overfit it. P3 claims must retain the internal-benchmark qualifier.
- Cross-encoder reranking is not included in this experiment; the current corpus is only 21 labels/144 dense chunks, and the experiment first tests whether lower-cost hybrid fusion is sufficient.
