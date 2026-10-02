"""Compare dense, BM25, and tag-level hybrid retrieval on the P1 dataset."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

from benchmarks.run_benchmark import evaluate_retrieval, load_cases
from src.rag.hybrid_retriever import BM25Retriever, HybridRetriever
from src.rag.retriever import FaissRetriever


ROOT = Path(__file__).resolve().parents[1]
DATASET = ROOT / "benchmarks" / "data" / "query_benchmark_v1.jsonl"
DEFAULT_JSON = ROOT / "benchmarks" / "results" / "p3_search_experiment.json"
DEFAULT_MD = ROOT / "benchmarks" / "results" / "p3_search_experiment.md"


def pct(value: float) -> str:
    return f"{value * 100:.1f}%"


def compact(summary: dict[str, Any]) -> dict[str, Any]:
    weighted = next(
        row
        for row in summary["weighted_grid"]
        if row["k"] == 5 and row["min_score"] == 0.15
    )
    failures = [
        {
            "id": row["id"],
            "expected_tag": row["expected_tag"],
            "top1_prediction": row["top1_prediction"],
            "reciprocal_rank": row["reciprocal_rank"],
        }
        for row in summary["rows"]
        if row["reciprocal_rank"] == 0.0
    ]
    return {
        "n": summary["n"],
        "recall_at": summary["recall_at"],
        "mrr": summary["mrr"],
        "top1_tag_accuracy": summary["top1_tag_accuracy"],
        "fallback_accuracy_k5_t015": weighted["accuracy"],
        "latency_ms": summary["latency_ms"],
        "category_metrics": summary["category_metrics"],
        "top10_misses": failures,
        "rows": summary["rows"],
    }


def render(report: dict[str, Any]) -> str:
    lines = [
        "# P3 Search Experiment",
        "",
        f"Generated: {report['generated_at']}",
        f"Dataset: `{report['dataset']}`",
        "",
        "| Configuration | R@1 | R@5 | R@10 | MRR | Top-1 | Fallback k5 | P50 | P95 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for name, result in report["configurations"].items():
        latency = result["latency_ms"]
        lines.append(
            f"| {name} | {pct(result['recall_at']['1'])} | "
            f"{pct(result['recall_at']['5'])} | {pct(result['recall_at']['10'])} | "
            f"{result['mrr']:.4f} | {pct(result['top1_tag_accuracy'])} | "
            f"{pct(result['fallback_accuracy_k5_t015'])} | "
            f"{latency['p50']:.1f} ms | {latency['p95']:.1f} ms |"
        )
    lines.extend(
        [
            "",
            f"Selected: `{report['selected_configuration']}`",
            "",
            "Selection rule: maximize top-1 accuracy, then MRR, Recall@5, and finally lower P95 latency. The dense P1 baseline remains in the same report.",
            "",
            "## Limitations",
            "",
            "- The 63-query dataset and tag catalog are both project-authored; aliases are derived from the local OSM Wiki snapshot, so this is an internal regression/ablation rather than an independent external evaluation.",
            "- Configuration selection on this dataset can overfit it. P3 claims must retain the internal-benchmark qualifier.",
            "- Cross-encoder reranking is not included in this experiment; the current corpus is only 21 labels/144 dense chunks, and the experiment first tests whether lower-cost hybrid fusion is sufficient.",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, default=DATASET)
    parser.add_argument("--output-json", type=Path, default=DEFAULT_JSON)
    parser.add_argument("--output-md", type=Path, default=DEFAULT_MD)
    args = parser.parse_args()

    dataset_path = args.dataset.resolve()
    cases = load_cases(dataset_path)
    dense = FaissRetriever(local_files_only=True)
    configurations = {
        "dense_chunk_p1": dense,
        "bm25_tag": BM25Retriever(dense.meta),
        "dense_tag_dedup": HybridRetriever(
            dense,
            dense_weight=1.0,
            lexical_weight=0.0,
            tag_match_weight=0.0,
            negative_weight=0.0,
        ),
        "hybrid_rrf_1_0.25": HybridRetriever(
            dense, dense_weight=1.0, lexical_weight=0.25, tag_match_weight=0.0
        ),
        "hybrid_rrf_1_0.5": HybridRetriever(
            dense, dense_weight=1.0, lexical_weight=0.5, tag_match_weight=0.0
        ),
        "hybrid_rrf_1_1": HybridRetriever(
            dense, dense_weight=1.0, lexical_weight=1.0, tag_match_weight=0.0
        ),
        "hybrid_rrf_1_2": HybridRetriever(
            dense, dense_weight=1.0, lexical_weight=2.0, tag_match_weight=0.0
        ),
        "hybrid_rrf_0.5_1": HybridRetriever(
            dense, dense_weight=0.5, lexical_weight=1.0, tag_match_weight=0.0
        ),
        "hybrid_rrf_0.25_1": HybridRetriever(
            dense, dense_weight=0.25, lexical_weight=1.0, tag_match_weight=0.0
        ),
        "hybrid_rrf_1_1_match_0.05": HybridRetriever(
            dense, dense_weight=1.0, lexical_weight=1.0, tag_match_weight=0.05
        ),
        "hybrid_rrf_1_1_match_0.15": HybridRetriever(
            dense, dense_weight=1.0, lexical_weight=1.0, tag_match_weight=0.15
        ),
        "hybrid_rrf_0.5_1_match_0.15": HybridRetriever(
            dense, dense_weight=0.5, lexical_weight=1.0, tag_match_weight=0.15
        ),
        "hybrid_rrf_1_1_match_0.15_neg_0.25": HybridRetriever(
            dense,
            dense_weight=1.0,
            lexical_weight=1.0,
            tag_match_weight=0.15,
            negative_weight=0.25,
        ),
        "hybrid_rrf_1_2_match_0.15_neg_0.25": HybridRetriever(
            dense,
            dense_weight=1.0,
            lexical_weight=2.0,
            tag_match_weight=0.15,
            negative_weight=0.25,
        ),
        "hybrid_rrf_0.5_1_match_0.15_neg_0.25": HybridRetriever(
            dense,
            dense_weight=0.5,
            lexical_weight=1.0,
            tag_match_weight=0.15,
            negative_weight=0.25,
        ),
    }
    results: dict[str, Any] = {}
    for name, retriever in configurations.items():
        print(f"[P3] Evaluating {name}", flush=True)
        summary, _hits = evaluate_retrieval(cases, retriever)
        results[name] = compact(summary)
    selected = max(
        results,
        key=lambda name: (
            results[name]["top1_tag_accuracy"],
            results[name]["mrr"],
            results[name]["recall_at"]["5"],
            -results[name]["latency_ms"]["p95"],
        ),
    )
    report = {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "dataset": str(
            dataset_path.relative_to(ROOT)
            if dataset_path.is_relative_to(ROOT)
            else dataset_path
        ),
        "selection_rule": "top1_accuracy,mrr,recall_at_5,-p95_latency",
        "selected_configuration": selected,
        "configurations": results,
    }
    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_md.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    args.output_md.write_text(render(report), encoding="utf-8")
    print(f"[P3] Selected {selected}", flush=True)
    print(f"[P3] Wrote {args.output_json}", flush=True)
    print(f"[P3] Wrote {args.output_md}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
