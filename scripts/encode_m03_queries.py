"""Encode M03 evaluation queries with the pinned model in a FAISS-free process."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sentence_transformers import SentenceTransformer
import numpy as np

from src.knowledge.evaluation import parse_query_jsonl
from src.knowledge.model_provenance import verify_model_snapshot
from src.knowledge.serialization import canonical_json_bytes, sha256_hex


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--queries", type=Path, required=True)
    parser.add_argument("--model-dir", type=Path, required=True)
    parser.add_argument("--model-receipt", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists() or args.receipt.exists():
        raise SystemExit("OSM_KB_QUERY_EMBEDDING_EXISTS: refusing to overwrite")
    query_bytes = args.queries.read_bytes()
    queries = parse_query_jsonl(query_bytes)
    if not queries:
        raise ValueError("OSM_KB_EVAL_QUERY_COUNT_INVALID: empty query input")
    receipt_bytes = args.model_receipt.read_bytes()
    model_receipt = json.loads(receipt_bytes.decode("utf-8"))

    started = time.perf_counter()
    model = SentenceTransformer(str(args.model_dir.resolve()), device="cpu", local_files_only=True)
    model_load_ms = (time.perf_counter() - started) * 1000.0
    print("Loaded pinned query encoder", flush=True)
    verify_started = time.perf_counter()
    verified = verify_model_snapshot(args.model_dir, model_receipt)
    model_verify_ms = (time.perf_counter() - verify_started) * 1000.0

    first_started = time.perf_counter()
    first = model.encode(
        [queries[0].query_text],
        batch_size=1,
        show_progress_bar=False,
        normalize_embeddings=True,
        convert_to_numpy=True,
    ).astype(np.float32)
    first_query_encode_ms = (time.perf_counter() - first_started) * 1000.0
    print("Encoded first query", flush=True)
    warm_started = time.perf_counter()
    if len(queries) > 1:
        rest = model.encode(
            [item.query_text for item in queries[1:]],
            batch_size=4,
            show_progress_bar=False,
            normalize_embeddings=True,
            convert_to_numpy=True,
        ).astype(np.float32)
        vectors = np.vstack([first, rest]).astype(np.float32)
    else:
        vectors = first
    warm_batch_ms = (time.perf_counter() - warm_started) * 1000.0
    print(f"Encoded {len(queries)} evaluation queries", flush=True)
    if vectors.shape != (len(queries), 384):
        raise ValueError(f"OSM_KB_EMBEDDING_DIMENSION_INVALID: {vectors.shape}")
    norms = np.linalg.norm(vectors, axis=1)
    if not np.allclose(norms, np.ones_like(norms), atol=1e-5):
        raise ValueError("OSM_KB_EMBEDDING_NORMALIZATION_INVALID")

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("xb") as handle:
        np.save(handle, vectors, allow_pickle=False)
    output_bytes = args.output.read_bytes()
    payload = {
        "schema_version": "1.0",
        "query_sha256": sha256_hex(query_bytes),
        "query_count": len(queries),
        "query_ids_sha256": sha256_hex(
            canonical_json_bytes([item.query_id for item in queries])
        ),
        "model_id": model_receipt["model_id"],
        "model_revision": model_receipt["revision"],
        "model_receipt_sha256": sha256_hex(receipt_bytes),
        "verified_model_files": verified.checked_files,
        "embedding_dimension": 384,
        "normalize_embeddings": True,
        "embedding_file_sha256": sha256_hex(output_bytes),
        "embedding_file_size_bytes": len(output_bytes),
        "latency_ms": {
            "model_load_cold": model_load_ms,
            "model_verification_cold": model_verify_ms,
            "first_query_encode_cold": first_query_encode_ms,
            "remaining_queries_batch_total": warm_batch_ms,
            "remaining_queries_warm_mean": warm_batch_ms / max(1, len(queries) - 1),
        },
    }
    descriptor = os.open(args.receipt, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(canonical_json_bytes(payload))
    print(json.dumps(payload, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())