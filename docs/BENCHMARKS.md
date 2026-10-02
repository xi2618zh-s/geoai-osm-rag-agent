# Benchmark evidence summary

All datasets below are project-authored. They support regression and engineering
decisions; they are not external production traffic or blind academic benchmarks.

## P1: establish the dense baseline

- 63 queries, 21 OSM labels, three expression styles per label.
- Dense Recall@1 82.5%, Recall@5 95.2%, MRR 0.8854.
- Cross-chunk weighted voting at k=5 fell to 77.8%, below top-1 82.5%.
- Result: reject the claim that the old weighted fallback improved accuracy.

## P2: reliability without metric regression

- Strict Pydantic contracts, trace/timing, stable errors, bounded retry and cache.
- 26 offline tests at the P2 milestone.
- 63-query retrieval metrics preserved.
- A real 120-second cold Ollama timeout recovered once; the measured cold path
  motivated a 180-second default rather than hiding the failure.

## P3: hybrid retrieval

| Configuration | Recall@1 | Recall@5 | MRR | P50 | P95 |
|---|---:|---:|---:|---:|---:|
| Dense baseline | 82.5% | 95.2% | 0.8854 | 14.8 ms | 18.1 ms |
| Final hybrid | 100% | 100% | 1.0 | 22.1 ms | 28.5 ms |

The 21-query Qwen decision evaluation finished at 85.7% despite retrieval 100%.
This is retained because it demonstrates a real downstream semantic-selection
gap and prevents a misleading “system accuracy 100%” claim.

## P4: constrained workflow

- 12 OR-plan cases: validity, operation, place, filter-set and exact-plan 100%.
- Planning P50/P95: 43.9/57.7 ms.
- Real Lund café/restaurant union: 50 + 123 → 173 objects.
- 6/6 workflow steps succeeded; clip cache hit; in-process total 7.32 s.
- Real HTTP `/workflow`: the same 173 objects; request total 9.05 s.
- Fault injection proves one retry recovery and downstream skip after final failure.

## Reproduce

```bat
scripts\test_offline.cmd
conda run -n geoai_project_env python -m benchmarks.run_benchmark --retriever-mode hybrid
conda run -n geoai_project_env python -m benchmarks.run_p4_workflow
```

Commands with `--llm` or `--e2e-query` require the external model/service/data
described in `README.md`. Machine-readable evidence is in `benchmarks/results/`.
