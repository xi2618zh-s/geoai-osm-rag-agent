"""Run reproducible retrieval, LLM-decision, and sampled E2E benchmarks."""

from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import statistics
import time
from typing import Any, Iterable, Optional, Sequence

# Benchmarks should not become dependent on Hugging Face availability after the
# embedding model has been installed. Set GEOAI_BENCHMARK_ALLOW_MODEL_DOWNLOAD=1
# only when intentionally populating an empty model cache.
if os.getenv("GEOAI_BENCHMARK_ALLOW_MODEL_DOWNLOAD", "0") != "1":
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")

from src.config import OLLAMA_MODEL, RAG_MIN_SCORE
from src.pipeline import run_query
from src.query.llm_parser import llm_parse_query, validate_llm_response
from src.rag.hybrid_retriever import build_retriever
from src.rag.retriever import RetrievedChunk, pick_tag_from_chunks


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATASET = ROOT / "benchmarks" / "data" / "query_benchmark_v1.jsonl"
DEFAULT_OUTPUT_JSON = ROOT / "benchmarks" / "results" / "p1_baseline.json"
DEFAULT_OUTPUT_MD = ROOT / "benchmarks" / "results" / "p1_baseline.md"
DEFAULT_E2E_IDS = (
    "amenity_cafe_canonical",
    "leisure_park_canonical",
    "highway_bus_stop_canonical",
)


@dataclass(frozen=True)
class BenchmarkCase:
    id: str
    query: str
    expected_place: str
    key: str
    value: str
    category: str
    difficulty: str

    @property
    def expected_tag(self) -> tuple[str, str]:
        return self.key, self.value


def load_cases(path: Path) -> list[BenchmarkCase]:
    cases: list[BenchmarkCase] = []
    ids: set[str] = set()
    with Path(path).open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                case = BenchmarkCase(**json.loads(line))
            except (TypeError, json.JSONDecodeError) as error:
                raise ValueError(f"Invalid benchmark row at line {line_number}: {error}") from error
            if case.id in ids:
                raise ValueError(f"Duplicate benchmark id: {case.id}")
            if not all((case.query.strip(), case.expected_place.strip(), case.key.strip(), case.value.strip())):
                raise ValueError(f"Benchmark case contains an empty required field: {case.id}")
            ids.add(case.id)
            cases.append(case)
    if not cases:
        raise ValueError(f"Benchmark dataset is empty: {path}")
    return cases


def hit_tag(hit: RetrievedChunk) -> Optional[tuple[str, str]]:
    if hit.key and hit.value:
        return str(hit.key).strip(), str(hit.value).strip()
    return None


def reciprocal_rank(hits: Sequence[RetrievedChunk], expected: tuple[str, str]) -> float:
    for rank, hit in enumerate(hits, 1):
        if hit_tag(hit) == expected:
            return 1.0 / rank
    return 0.0


def top1_prediction(hits: Sequence[RetrievedChunk]) -> Optional[tuple[str, str]]:
    for hit in hits:
        tag = hit_tag(hit)
        if tag is not None:
            return tag
    return None


def weighted_prediction(
    hits: Sequence[RetrievedChunk], k: int, min_score: float
) -> Optional[tuple[str, str]]:
    try:
        return pick_tag_from_chunks(list(hits[:k]), min_score=min_score)
    except ValueError:
        return None


def percentile(values: Sequence[float], percent: float) -> Optional[float]:
    if not values:
        return None
    ordered = sorted(float(value) for value in values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * percent / 100.0
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = position - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction


def rate(flags: Iterable[bool]) -> Optional[float]:
    values = list(flags)
    return sum(bool(value) for value in values) / len(values) if values else None


def tag_from_data(data: Any) -> Optional[tuple[str, str]]:
    if not isinstance(data, dict) or not isinstance(data.get("tag"), dict):
        return None
    key = data["tag"].get("key")
    value = data["tag"].get("value")
    if isinstance(key, str) and key.strip() and isinstance(value, str) and value.strip():
        return key.strip(), value.strip()
    return None


def strict_schema_valid(data: Any) -> bool:
    if not validate_llm_response(data):
        return False
    if not all(field in data for field in ("place", "tag", "confidence", "explanation")):
        return False
    return isinstance(data.get("explanation"), str)


def metric_block(flags: list[bool], latencies: list[float]) -> dict[str, Any]:
    return {
        "n": len(flags),
        "rate": rate(flags),
        "latency_ms": {
            "mean": statistics.fmean(latencies) if latencies else None,
            "p50": percentile(latencies, 50),
            "p95": percentile(latencies, 95),
        },
    }


def evaluate_retrieval(
    cases: Sequence[BenchmarkCase], retriever: FaissRetriever, max_k: int = 10
) -> tuple[dict[str, Any], dict[str, list[RetrievedChunk]]]:
    hits_by_id: dict[str, list[RetrievedChunk]] = {}
    rows: list[dict[str, Any]] = []
    latencies: list[float] = []
    recall_ks = (1, 3, 5, 10)
    grid_ks = (1, 3, 5, 10)
    thresholds = (0.0, 0.15, 0.25)

    for case in cases:
        started = time.perf_counter()
        hits = retriever.retrieve(case.query, k=max_k)
        latency_ms = (time.perf_counter() - started) * 1000.0
        latencies.append(latency_ms)
        hits_by_id[case.id] = hits
        rows.append(
            {
                **asdict(case),
                "expected_tag": f"{case.key}={case.value}",
                "latency_ms": latency_ms,
                "reciprocal_rank": reciprocal_rank(hits, case.expected_tag),
                "top1_prediction": _tag_text(top1_prediction(hits)),
                "hits": [
                    {
                        "rank": rank,
                        "tag": _tag_text(hit_tag(hit)),
                        "score": hit.score,
                        "url": hit.url,
                    }
                    for rank, hit in enumerate(hits, 1)
                ],
            }
        )

    recall = {
        str(k): rate(
            any(hit_tag(hit) == case.expected_tag for hit in hits_by_id[case.id][:k])
            for case in cases
        )
        for k in recall_ks
    }
    weighted_grid = []
    for k in grid_ks:
        for threshold in thresholds:
            predictions = [
                weighted_prediction(hits_by_id[case.id], k=k, min_score=threshold)
                for case in cases
            ]
            weighted_grid.append(
                {
                    "k": k,
                    "min_score": threshold,
                    "accuracy": rate(
                        prediction == case.expected_tag
                        for prediction, case in zip(predictions, cases)
                    ),
                    "coverage": rate(prediction is not None for prediction in predictions),
                }
            )

    category_metrics: dict[str, Any] = {}
    for category in sorted({case.category for case in cases}):
        selected = [case for case in cases if case.category == category]
        category_metrics[category] = {
            "n": len(selected),
            "recall_at_5": rate(
                any(hit_tag(hit) == case.expected_tag for hit in hits_by_id[case.id][:5])
                for case in selected
            ),
            "weighted_accuracy_k5_t015": rate(
                weighted_prediction(hits_by_id[case.id], 5, RAG_MIN_SCORE) == case.expected_tag
                for case in selected
            ),
        }

    summary = {
        "n": len(cases),
        "labels": len({case.expected_tag for case in cases}),
        "category_counts": dict(Counter(case.category for case in cases)),
        "difficulty_counts": dict(Counter(case.difficulty for case in cases)),
        "recall_at": recall,
        "mrr": statistics.fmean(row["reciprocal_rank"] for row in rows),
        "top1_tag_accuracy": rate(
            top1_prediction(hits_by_id[case.id]) == case.expected_tag for case in cases
        ),
        "weighted_grid": weighted_grid,
        "category_metrics": category_metrics,
        "latency_ms": {
            "mean": statistics.fmean(latencies),
            "p50": percentile(latencies, 50),
            "p95": percentile(latencies, 95),
        },
        "rows": rows,
    }
    return summary, hits_by_id


def evaluate_llm(
    cases: Sequence[BenchmarkCase],
    hits_by_id: dict[str, list[RetrievedChunk]],
    model: str,
    limit: int,
) -> dict[str, Any]:
    selected = list(cases if limit <= 0 else cases[:limit])
    rows: list[dict[str, Any]] = []
    latencies: list[float] = []

    for index, case in enumerate(selected, 1):
        chunks = hits_by_id[case.id][:5]
        print(f"[LLM {index}/{len(selected)}] {case.id}: {case.query}", flush=True)
        started = time.perf_counter()
        response = llm_parse_query(case.query, chunks, model=model)
        latency_ms = (time.perf_counter() - started) * 1000.0
        latencies.append(latency_ms)
        data = response.get("data", {})
        json_valid = bool(response.get("ok")) and isinstance(data, dict)
        schema_valid = json_valid and strict_schema_valid(data)
        grounded = schema_valid and validate_llm_response(data, chunks)
        llm_tag = tag_from_data(data)
        fallback_tag = weighted_prediction(chunks, 5, RAG_MIN_SCORE)
        final_tag = llm_tag if grounded else fallback_tag
        place = data.get("place") if isinstance(data, dict) else None
        rows.append(
            {
                **asdict(case),
                "expected_tag": f"{case.key}={case.value}",
                "latency_ms": latency_ms,
                "json_valid": json_valid,
                "schema_valid": schema_valid,
                "grounded": grounded,
                "place_correct": isinstance(place, str)
                and place.casefold().strip() == case.expected_place.casefold(),
                "llm_prediction": _tag_text(llm_tag),
                "llm_tag_correct": llm_tag == case.expected_tag,
                "fallback_triggered": not grounded,
                "fallback_prediction": _tag_text(fallback_tag),
                "final_prediction": _tag_text(final_tag),
                "final_tag_correct": final_tag == case.expected_tag,
                "raw": str(response.get("raw", ""))[:2000],
            }
        )

    return {
        "model": model,
        "n": len(rows),
        "json_validity_rate": rate(row["json_valid"] for row in rows),
        "strict_schema_rate": rate(row["schema_valid"] for row in rows),
        "grounded_tag_rate": rate(row["grounded"] for row in rows),
        "place_accuracy": rate(row["place_correct"] for row in rows),
        "llm_tag_accuracy": rate(row["llm_tag_correct"] for row in rows),
        "fallback_trigger_rate": rate(row["fallback_triggered"] for row in rows),
        "final_decision_accuracy": rate(row["final_tag_correct"] for row in rows),
        "latency_ms": {
            "mean": statistics.fmean(latencies) if latencies else None,
            "p50": percentile(latencies, 50),
            "p95": percentile(latencies, 95),
        },
        "rows": rows,
    }


def evaluate_e2e(
    cases: Sequence[BenchmarkCase], case_ids: Sequence[str], model: str
) -> dict[str, Any]:
    case_map = {case.id: case for case in cases}
    missing = [case_id for case_id in case_ids if case_id not in case_map]
    if missing:
        raise ValueError(f"Unknown E2E benchmark ids: {missing}")
    rows: list[dict[str, Any]] = []
    latencies: list[float] = []
    for index, case_id in enumerate(case_ids, 1):
        case = case_map[case_id]
        print(f"[E2E {index}/{len(case_ids)}] {case.id}: {case.query}", flush=True)
        started = time.perf_counter()
        result = run_query(case.query, model=model)
        latency_ms = (time.perf_counter() - started) * 1000.0
        latencies.append(latency_ms)
        rows.append(
            {
                **asdict(case),
                "expected_tag": f"{case.key}={case.value}",
                "latency_ms": latency_ms,
                "success": bool(result.get("success")),
                "tag_correct": result.get("chosen_tag") == f"{case.key}={case.value}",
                "llm_ok": bool(result.get("llm_ok")),
                "count": result.get("count"),
                "counts_by_type": result.get("counts_by_type"),
                "geojson_filename": result.get("geojson_filename"),
                "error": result.get("error"),
            }
        )
    return {
        "n": len(rows),
        "success_rate": rate(row["success"] for row in rows),
        "tag_accuracy": rate(row["tag_correct"] for row in rows),
        "llm_success_rate": rate(row["llm_ok"] for row in rows),
        "latency_ms": {
            "mean": statistics.fmean(latencies) if latencies else None,
            "p50": percentile(latencies, 50),
            "p95": percentile(latencies, 95),
        },
        "rows": rows,
    }


def _tag_text(tag: Optional[tuple[str, str]]) -> Optional[str]:
    return f"{tag[0]}={tag[1]}" if tag else None


def _pct(value: Optional[float]) -> str:
    return "n/a" if value is None else f"{value * 100:.1f}%"


def _ms(value: Optional[float]) -> str:
    return "n/a" if value is None else f"{value:.1f} ms"


def render_markdown(result: dict[str, Any]) -> str:
    retrieval = result["retrieval"]
    lines = [
        f"# {result.get('report_title', 'Retrieval Benchmark')}",
        "",
        f"Generated: {result['generated_at']}",
        f"Dataset: `{result['dataset']}`",
        f"Cases: {retrieval['n']} across {retrieval['labels']} labels",
        f"Retriever: `{result.get('retriever_mode', 'dense')}`",
        "",
        "## Retrieval",
        "",
        "| Metric | Result |",
        "|---|---:|",
        f"| Recall@1 | {_pct(retrieval['recall_at']['1'])} |",
        f"| Recall@3 | {_pct(retrieval['recall_at']['3'])} |",
        f"| Recall@5 | {_pct(retrieval['recall_at']['5'])} |",
        f"| Recall@10 | {_pct(retrieval['recall_at']['10'])} |",
        f"| MRR | {retrieval['mrr']:.4f} |",
        f"| Top-1 chunk tag accuracy | {_pct(retrieval['top1_tag_accuracy'])} |",
        f"| Retrieval latency P50 | {_ms(retrieval['latency_ms']['p50'])} |",
        f"| Retrieval latency P95 | {_ms(retrieval['latency_ms']['p95'])} |",
        "",
        "### Weighted fallback ablation",
        "",
        "| top-k | min score | accuracy | coverage |",
        "|---:|---:|---:|---:|",
    ]
    for row in retrieval["weighted_grid"]:
        lines.append(
            f"| {row['k']} | {row['min_score']:.2f} | {_pct(row['accuracy'])} | {_pct(row['coverage'])} |"
        )

    lines.extend(["", "### Results by query category", "", "| Category | N | Recall@5 | Weighted accuracy (k=5, threshold=0.15) |", "|---|---:|---:|---:|"])
    for category, metrics in retrieval["category_metrics"].items():
        lines.append(
            f"| {category} | {metrics['n']} | {_pct(metrics['recall_at_5'])} | {_pct(metrics['weighted_accuracy_k5_t015'])} |"
        )

    llm = result.get("llm")
    if llm:
        lines.extend(
            [
                "",
                "## LLM decision sample",
                "",
                f"Model: `{llm['model']}`; N={llm['n']}",
                "",
                "| Metric | Result |",
                "|---|---:|",
                f"| JSON validity | {_pct(llm['json_validity_rate'])} |",
                f"| Strict schema validity | {_pct(llm['strict_schema_rate'])} |",
                f"| Grounded tag rate | {_pct(llm['grounded_tag_rate'])} |",
                f"| Place accuracy | {_pct(llm['place_accuracy'])} |",
                f"| Raw LLM tag accuracy | {_pct(llm['llm_tag_accuracy'])} |",
                f"| Fallback trigger rate | {_pct(llm['fallback_trigger_rate'])} |",
                f"| Final decision accuracy | {_pct(llm['final_decision_accuracy'])} |",
                f"| LLM latency P50 | {_ms(llm['latency_ms']['p50'])} |",
                f"| LLM latency P95 | {_ms(llm['latency_ms']['p95'])} |",
            ]
        )

    e2e = result.get("e2e")
    if e2e:
        lines.extend(
            [
                "",
                "## Sampled end-to-end execution",
                "",
                f"N={e2e['n']}",
                "",
                "| Metric | Result |",
                "|---|---:|",
                f"| E2E success | {_pct(e2e['success_rate'])} |",
                f"| Chosen-tag accuracy | {_pct(e2e['tag_accuracy'])} |",
                f"| LLM success | {_pct(e2e['llm_success_rate'])} |",
                f"| E2E latency P50 | {_ms(e2e['latency_ms']['p50'])} |",
                f"| E2E latency P95 | {_ms(e2e['latency_ms']['p95'])} |",
                "",
                "| Case | Success | Tag correct | LLM OK | Features |",
                "|---|---:|---:|---:|---:|",
            ]
        )
        for row in e2e["rows"]:
            lines.append(
                f"| {row['id']} | {row['success']} | {row['tag_correct']} | {row['llm_ok']} | {row['count']} |"
            )

    retrieval_failures = [
        row for row in retrieval["rows"] if row["reciprocal_rank"] == 0.0
    ]
    lines.extend(["", "## Error analysis", ""])
    if retrieval_failures:
        lines.append("Expected tag absent from top-10 chunks:")
        lines.append("")
        for row in retrieval_failures:
            lines.append(
                f"- `{row['id']}` expected `{row['expected_tag']}`, top-1 was `{row['top1_prediction']}`."
            )
    else:
        lines.append("Every expected tag appeared within the top-10 retrieved chunks.")

    lines.extend(
        [
            "",
            "## Limitations",
            "",
            "- The dataset is a balanced, project-authored benchmark over the current 21-tag knowledge base, not an external human-judged production corpus.",
            "- LLM evaluation uses one canonical query per tag by default; E2E evaluation is deliberately sampled to respect Nominatim and control local CPU runtime.",
            "- Accuracy values describe this versioned dataset and must not be presented as universal production performance.",
            "- Query wording and ground truth should receive human review before results are published or compared.",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--output-json", type=Path, default=DEFAULT_OUTPUT_JSON)
    parser.add_argument("--output-md", type=Path, default=DEFAULT_OUTPUT_MD)
    parser.add_argument("--model", default=OLLAMA_MODEL)
    parser.add_argument(
        "--retriever-mode",
        choices=("dense", "bm25", "hybrid"),
        default="dense",
        help="Retrieval implementation to evaluate; dense preserves the P1 baseline.",
    )
    parser.add_argument("--report-title", default="")
    parser.add_argument("--llm-limit", type=int, default=0, help="0 skips LLM evaluation")
    parser.add_argument(
        "--e2e-ids",
        default="",
        help="Comma-separated case ids. Use 'default' for three representative cases.",
    )
    args = parser.parse_args()

    cases = load_cases(args.dataset)
    print(f"[Benchmark] Loaded {len(cases)} cases", flush=True)
    retriever = build_retriever(args.retriever_mode, local_files_only=True)
    retrieval, hits_by_id = evaluate_retrieval(cases, retriever)
    result: dict[str, Any] = {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "dataset": str(args.dataset.relative_to(ROOT) if args.dataset.is_relative_to(ROOT) else args.dataset),
        "retriever_mode": args.retriever_mode,
        "report_title": args.report_title
        or ("P1 Benchmark Baseline" if args.retriever_mode == "dense" else "P3 Hybrid Search Benchmark"),
        "retrieval": retrieval,
    }

    if args.llm_limit:
        result["llm"] = evaluate_llm(cases, hits_by_id, args.model, args.llm_limit)

    if args.e2e_ids:
        ids = DEFAULT_E2E_IDS if args.e2e_ids == "default" else tuple(
            item.strip() for item in args.e2e_ids.split(",") if item.strip()
        )
        result["e2e"] = evaluate_e2e(cases, ids, args.model)

    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_md.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    args.output_md.write_text(render_markdown(result), encoding="utf-8")
    print(f"[Benchmark] Wrote {args.output_json}", flush=True)
    print(f"[Benchmark] Wrote {args.output_md}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
