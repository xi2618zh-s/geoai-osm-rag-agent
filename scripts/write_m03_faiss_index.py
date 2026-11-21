"""Isolated FAISS writer for M03 to avoid PyTorch/FAISS OpenMP co-loading on Windows."""

from __future__ import annotations

import argparse
import importlib.metadata
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import faiss
import numpy as np

from src.knowledge.serialization import canonical_json_bytes, sha256_hex


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--embeddings", type=Path, required=True)
    parser.add_argument("--index", type=Path, required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--expected-count", type=int, default=200)
    args = parser.parse_args()
    embeddings = np.load(args.embeddings, allow_pickle=False)
    if args.expected_count <= 0 or embeddings.shape != (args.expected_count, 384) or embeddings.dtype != np.float32:
        raise ValueError(f"OSM_KB_EMBEDDING_DIMENSION_INVALID: {embeddings.shape} {embeddings.dtype}")
    norms = np.linalg.norm(embeddings, axis=1)
    if not np.allclose(norms, np.ones_like(norms), atol=1e-5):
        raise ValueError("OSM_KB_EMBEDDING_NORMALIZATION_INVALID")
    index = faiss.IndexFlatIP(384)
    index.add(embeddings)
    serialized = faiss.serialize_index(index).tobytes()
    args.index.write_bytes(serialized)
    receipt = {
        "schema_version": "1.0",
        "process_isolation_reason": "Avoid PyTorch and FAISS OpenMP runtime co-loading on Windows.",
        "index_type": "IndexFlatIP",
        "dimension": int(index.d),
        "vector_count": int(index.ntotal),
        "faiss_version": importlib.metadata.version("faiss-cpu"),
        "index_sha256": sha256_hex(serialized),
        "index_size_bytes": len(serialized),
    }
    args.receipt.write_bytes(canonical_json_bytes(receipt))
    print(json.dumps(receipt, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())