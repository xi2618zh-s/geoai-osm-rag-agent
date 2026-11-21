"""Run immutable dense/BM25/hybrid M03 retrieval evaluation."""

from __future__ import annotations

import argparse
from dataclasses import asdict
import hashlib
import json
import math
from pathlib import Path
import shutil
import statistics
import subprocess
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import numpy as np

from src.knowledge.build import publish_directory_atomically
from src.knowledge.contracts import RetrievalQueryV2
from src.knowledge.evaluation import (
    compute_retrieval_metrics,
    compute_phase2_cohort_metrics,
    parse_query_jsonl,
    write_public_evaluation_receipt,
)
from src.knowledge.serialization import canonical_json_bytes, canonical_jsonl_bytes, sha256_hex


def percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, math.ceil(fraction * len(ordered)) - 1))
    return ordered[index]


def macro(groups: dict[str, dict[str, float]]) -> dict[str, float]:
    metrics = ("recall_at_1", "recall_at_5", "mrr", "target_top1_accuracy", "hard_negative_accuracy")
    return {
        name: sum(float(group[name]) for group in groups.values()) / len(groups)
        for name in metrics
    } if groups else {name: 0.0 for name in metrics}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--queries", type=Path, required=True)
    parser.add_argument("--split", choices=("dev", "public_eval"), required=True)
    parser.add_argument("--index-dir", type=Path, required=True)
    parser.add_argument("--hybrid-config", type=Path, required=True)
    parser.add_argument("--model-dir", type=Path, required=True)
    parser.add_argument("--model-receipt", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--experiment-id", required=True)
    parser.add_argument("--run-timestamp", required=True)
    parser.add_argument("--public-receipt", type=Path)
    args = parser.parse_args()

    output = args.output_dir.resolve()
    if output.exists():
        raise SystemExit("OSM_KB_EVAL_RESULT_EXISTS: refusing to overwrite immutable result")
    if args.split == "public_eval":
        if args.public_receipt is None:
            raise SystemExit("OSM_KB_PUBLIC_EVAL_RECEIPT_REQUIRED")
        if args.public_receipt.exists():
            raise SystemExit("OSM_KB_PUBLIC_EVAL_ALREADY_RUN")

    query_bytes = args.queries.read_bytes()
    queries = parse_query_jsonl(query_bytes)
    phase2 = isinstance(queries[0], RetrievalQueryV2)
    if phase2:
        target_count = len({item.target_tag for item in queries})
        if (target_count < 200 or len(queries) != target_count * (2 if args.split == "dev" else 1)
            or {item.target_cohort for item in queries} != {"phase1_retained", "phase2_new"}):
            raise ValueError("OSM_KB_PHASE2_EVAL_QUERY_COUNT_INVALID")
    else:
        expected = 200 if args.split == "dev" else 100
        if len(queries) != expected:
            raise ValueError("OSM_KB_EVAL_QUERY_COUNT_INVALID")
    if {item.split.value for item in queries} != {args.split}:
        raise ValueError("OSM_KB_EVAL_SPLIT_INVALID")
    config_bytes = args.hybrid_config.read_bytes()
    config = json.loads(config_bytes.decode("utf-8"))
    if args.split == "public_eval" and config.get("status") != "frozen":
        raise ValueError("OSM_KB_PUBLIC_EVAL_CONFIG_NOT_FROZEN")

    output.parent.mkdir(parents=True, exist_ok=True)
    staging = output.parent / f".staging-{output.name}-{uuid.uuid4().hex[:10]}"
    staging.mkdir()
    try:
        embeddings_path = staging / "query_embeddings.npy"
        encoder_receipt_path = staging / "query_embedding_receipt.json"
        encoder_process_started = time.perf_counter()
        subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts" / "encode_m03_queries.py"),
                "--queries",
                str(args.queries.resolve()),
                "--model-dir",
                str(args.model_dir.resolve()),
                "--model-receipt",
                str(args.model_receipt.resolve()),
                "--output",
                str(embeddings_path),
                "--receipt",
                str(encoder_receipt_path),
            ],
            check=True,
        )
        encoder_process_cold_ms = (time.perf_counter() - encoder_process_started) * 1000.0
        encoder_receipt = json.loads(encoder_receipt_path.read_text("utf-8"))
        vectors = np.load(embeddings_path, allow_pickle=False)
        if vectors.shape != (len(queries), 384):
            raise ValueError("OSM_KB_EMBEDDING_DIMENSION_INVALID")

        from src.knowledge.retrieval import M03IndexBundle

        load_started = time.perf_counter()
        bundle = M03IndexBundle(args.index_dir)
        index_load_cold_ms = (time.perf_counter() - load_started) * 1000.0
        if phase2 and bundle.unique_tag_count != target_count:
            raise ValueError("OSM_KB_PHASE2_INDEX_TARGET_COUNT_MISMATCH")
        if config.get("documents_sha256") != bundle.documents_sha256:
            raise ValueError("OSM_KB_INDEX_INPUT_VERSION_MISMATCH")

        ranking_depth = bundle.unique_tag_count if phase2 else 100
        methods = ("dense", "bm25", "hybrid")
        rankings_by_method: dict[str, dict[str, list[str]]] = {name: {} for name in methods}
        latency_by_method: dict[str, list[float]] = {name: [] for name in methods}
        for query_index, query in enumerate(queries):
            vector = vectors[query_index]
            for method in methods:
                started = time.perf_counter()
                if method == "dense":
                    ranking = bundle.dense_ranking(vector, k=ranking_depth)
                elif method == "bm25":
                    ranking = bundle.bm25_ranking(query.query_text, k=ranking_depth)
                else:
                    ranking = bundle.hybrid_ranking(
                        query.query_text, vector, k=ranking_depth, config=config
                    )
                latency_by_method[method].append((time.perf_counter() - started) * 1000.0)
                rankings_by_method[method][query.query_id] = ranking

        generated_files: dict[str, bytes] = {}
        summaries = {}
        for method in methods:
            rankings = rankings_by_method[method]
            metrics = compute_retrieval_metrics(queries, rankings)
            metric_payload = asdict(metrics)
            metric_payload["macro_by_language"] = macro(metrics.by_language)
            metric_payload["macro_by_category"] = macro(metrics.by_category)
            if phase2:
                metric_payload["by_cohort"] = {
                    cohort: {**asdict(result),
                             "macro_by_language": macro(result.by_language),
                             "macro_by_category": macro(result.by_category)}
                    for cohort, result in compute_phase2_cohort_metrics(queries, rankings).items()
                }
            summaries[method] = metric_payload
            predictions = []
            errors = []
            for query in queries:
                ranking = rankings[query.query_id]
                target_rank = ranking.index(query.target_tag) + 1 if query.target_tag in ranking else None
                excluded_ranks = {
                    tag: (ranking.index(tag) + 1 if tag in ranking else None)
                    for tag in query.excluded_tags
                }
                row = {
                    "query_id": query.query_id,
                    "target_tag": query.target_tag,
                    "excluded_tags": query.excluded_tags,
                    "target_rank": target_rank,
                    "excluded_ranks": excluded_ranks,
                    "top_10": ranking[:10],
                }
                predictions.append(row)
                hard_ok = target_rank is not None and all(
                    rank is None or target_rank < rank for rank in excluded_ranks.values()
                )
                if target_rank != 1 or not hard_ok:
                    errors.append(
                        {
                            **row,
                            "query_text": query.query_text,
                            "language": query.language.value,
                            "primary_category": query.primary_category.value,
                            "challenge_type": query.challenge_type,
                            "failure_categories": [
                                label
                                for label, active in (
                                    ("top1_miss", target_rank != 1),
                                    ("top5_miss", target_rank is None or target_rank > 5),
                                    ("hard_negative_outrank", not hard_ok),
                                )
                                if active
                            ],
                        }
                    )
            generated_files[f"{method}_metrics.json"] = canonical_json_bytes(metric_payload)
            generated_files[f"{method}_predictions.jsonl"] = canonical_jsonl_bytes(predictions)
            generated_files[f"{method}_errors.jsonl"] = canonical_jsonl_bytes(errors)

        latency = {
            "schema_version": "1.0",
            "encoder_process_cold_ms": encoder_process_cold_ms,
            "encoder": encoder_receipt["latency_ms"],
            "index_bundle_load_cold_ms": index_load_cold_ms,
            "retrieval": {
                method: {
                    "first_query_cold_ms": values[0],
                    "warm_median_ms": statistics.median(values[1:]) if len(values) > 1 else values[0],
                    "warm_p95_ms": percentile(values[1:] or values, 0.95),
                    "warm_mean_ms": sum(values[1:] or values) / len(values[1:] or values),
                }
                for method, values in latency_by_method.items()
            },
        }
        generated_files["latency.json"] = canonical_json_bytes(latency)
        generated_files["summary.json"] = canonical_json_bytes(summaries)
        for name, content in generated_files.items():
            (staging / name).write_bytes(content)

        index_manifest_bytes = (args.index_dir / "index_manifest.json").read_bytes()
        file_records = []
        for path in sorted(item for item in staging.iterdir() if item.is_file()):
            content = path.read_bytes()
            file_records.append(
                {"path": path.name, "sha256": sha256_hex(content), "size_bytes": len(content)}
            )
        experiment_manifest = {
            "schema_version": "1.0",
            "experiment_id": args.experiment_id,
            "split": args.split,
            "is_hidden_test": False,
            "run_timestamp": args.run_timestamp,
            "query_sha256": sha256_hex(query_bytes),
            "query_count": len(queries),
            "query_schema_version": queries[0].schema_version,
            "index_manifest_sha256": sha256_hex(index_manifest_bytes),
            "documents_sha256": bundle.documents_sha256,
            "hybrid_config_sha256": sha256_hex(config_bytes),
            "hybrid_config": config,
            "model_id": encoder_receipt["model_id"],
            "model_revision": encoder_receipt["model_revision"],
            "public_eval_policy": (
                "First immutable run after config freeze; future changes require a new feedback-affected experiment version."
                if args.split == "public_eval"
                else "Development split; may be used for configuration selection."
            ),
            "files": file_records,
        }
        manifest_bytes = canonical_json_bytes(experiment_manifest)
        (staging / "experiment_manifest.json").write_bytes(manifest_bytes)
        expected_files = {item.name for item in staging.iterdir() if item.is_file()}
        publish_directory_atomically(staging, output, expected_files=expected_files)
        if args.split == "public_eval":
            write_public_evaluation_receipt(
                args.public_receipt,
                {
                    "schema_version": "1.0",
                    "experiment_id": args.experiment_id,
                    "run_timestamp": args.run_timestamp,
                    "query_sha256": sha256_hex(query_bytes),
                    "hybrid_config_sha256": sha256_hex(config_bytes),
                    "experiment_manifest_sha256": sha256_hex(manifest_bytes),
                    "result_directory_name": output.name,
                    "feedback_affected": False,
                },
            )
        print(json.dumps(summaries, ensure_ascii=False, sort_keys=True, indent=2))
        return 0
    except Exception:
        if staging.exists():
            shutil.rmtree(staging)
        raise


if __name__ == "__main__":
    raise SystemExit(main())