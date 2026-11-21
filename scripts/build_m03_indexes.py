"""Build immutable dense, BM25, and hybrid-support indexes for M03 offline."""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
from pathlib import Path
import shutil
import subprocess
import sys
import uuid

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sentence_transformers import SentenceTransformer
import numpy as np

from src.knowledge.build import publish_directory_atomically
from src.knowledge.contracts import IndexDocumentV1, IndexDocumentV2
from src.knowledge.indexes import build_bm25_index_payload, parse_index_documents
from src.knowledge.model_provenance import verify_model_snapshot
from src.knowledge.serialization import canonical_json_bytes, sha256_hex
from src.knowledge.tokenizer import CJK_TOKENIZER_VERSION


def load_documents(path: Path, *, expected_count: int = 200) -> list[IndexDocumentV1 | IndexDocumentV2]:
    return parse_index_documents(path.read_bytes(), expected_count=expected_count)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release-dir", type=Path, required=True)
    parser.add_argument("--model-dir", type=Path, required=True)
    parser.add_argument("--model-receipt", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--build-timestamp", required=True)
    args = parser.parse_args()

    release_dir = args.release_dir.resolve()
    documents_path = release_dir / "index_documents.jsonl"
    release_manifest_path = release_dir / "build_manifest.json"
    receipt_path = args.model_receipt.resolve()
    model_dir = args.model_dir.resolve()
    output = args.output_dir.resolve()
    if output.exists():
        raise SystemExit("OSM_KB_INDEX_EXISTS: refusing to overwrite immutable index")
    output.parent.mkdir(parents=True, exist_ok=True)
    staging = output.parent / f".staging-{output.name}-{uuid.uuid4().hex[:10]}"
    staging.mkdir()

    try:
        release_manifest_bytes = release_manifest_path.read_bytes()
        release_manifest = json.loads(release_manifest_bytes.decode("utf-8"))
        documents_bytes = documents_path.read_bytes()
        expected_document_hash = next(
            item["sha256"]
            for item in release_manifest["files"]
            if item["path"] == "index_documents.jsonl"
        )
        if sha256_hex(documents_bytes) != expected_document_hash:
            raise ValueError("OSM_KB_MANIFEST_HASH_MISMATCH: index_documents.jsonl")
        expected_count = release_manifest["index_document_count"]
        phase2 = release_manifest.get("catalog_schema_version") == "3.0"
        if phase2:
            if expected_count < 400 or expected_count != 2 * release_manifest["unique_canonical_tag_count"]:
                raise ValueError("OSM_KB_PHASE2_INDEX_COUNT_MISMATCH")
        elif expected_count != 200:
            raise ValueError("OSM_KB_INDEX_COUNT_MISMATCH")
        documents = load_documents(documents_path, expected_count=expected_count)
        if {item.schema_version for item in documents} != ({"2.0"} if phase2 else {"1.0"}):
            raise ValueError("OSM_KB_INDEX_DOCUMENT_SCHEMA_MISMATCH")

        receipt_bytes = receipt_path.read_bytes()
        receipt = json.loads(receipt_bytes.decode("utf-8"))
        model = SentenceTransformer(str(model_dir), device="cpu", local_files_only=True)
        print("Loaded pinned embedding model", flush=True)
        verification = verify_model_snapshot(model_dir, receipt)
        print(f"Verified {verification.checked_files} pinned model files", flush=True)
        texts = [item.text for item in documents]
        embeddings = model.encode(
            texts,
            batch_size=4,
            show_progress_bar=True,
            normalize_embeddings=True,
            convert_to_numpy=True,
        ).astype(np.float32)
        print(f"Encoded {len(texts)} documents", flush=True)
        if embeddings.shape != (expected_count, 384):
            raise ValueError(f"OSM_KB_EMBEDDING_DIMENSION_INVALID: {embeddings.shape}")
        norms = np.linalg.norm(embeddings, axis=1)
        if not np.allclose(norms, np.ones_like(norms), atol=1e-5):
            raise ValueError("OSM_KB_EMBEDDING_NORMALIZATION_INVALID")
        embeddings_path = staging / ".embeddings.npy"
        np.save(embeddings_path, embeddings, allow_pickle=False)
        del embeddings
        subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts" / "write_m03_faiss_index.py"),
                "--embeddings",
                str(embeddings_path),
                "--index",
                str(staging / "dense.faiss"),
                "--receipt",
                str(staging / "faiss_build_receipt.json"),
                "--expected-count",
                str(expected_count),
            ],
            check=True,
        )
        embeddings_path.unlink()
        faiss_receipt = json.loads(
            (staging / "faiss_build_receipt.json").read_text("utf-8")
        )
        if faiss_receipt["vector_count"] != expected_count or faiss_receipt["dimension"] != 384:
            raise ValueError("OSM_KB_INDEX_COUNT_MISMATCH: dense index count/dimension mismatch")

        (staging / "documents.jsonl").write_bytes(documents_bytes)
        metadata = [
            {
                "page_content": item.text,
                "url": "",
                "title": item.title,
                "key": item.canonical_tag.split("=", 1)[0],
                "value": item.canonical_tag.split("=", 1)[1],
                "document_id": item.document_id,
                "entry_id": item.entry_id,
                "canonical_tag": item.canonical_tag,
                "language": item.language.value,
                "primary_category": item.primary_category.value,
                **({
                    "tag_role": item.tag_role.value,
                    "applicability": item.applicability.model_dump(mode="json") if item.applicability else None,
                } if isinstance(item, IndexDocumentV2) else {}),
                "source_refs": item.source_refs,
                "catalog_version": item.catalog_version,
                "embedding_model": receipt["model_id"],
                "embedding_revision": receipt["revision"],
                "normalize_embeddings": True,
            }
            for item in documents
        ]
        (staging / "dense.metadata.json").write_bytes(canonical_json_bytes(metadata))
        bm25_payload = build_bm25_index_payload(documents)
        (staging / "bm25.index.json").write_bytes(canonical_json_bytes(bm25_payload))
        hybrid_config = {
            "schema_version": "1.0",
            "config_id": "m03-phase2-hybrid-dev-candidate-v1" if phase2 else "m03-phase1-hybrid-dev-candidate-v1",
            "status": "dev_candidate",
            "fusion": "weighted_reciprocal_rank",
            "dense_weight": 1.0,
            "bm25_weight": 2.0,
            "rrf_k": 60,
            "candidate_tags_per_retriever": len(documents) // 2 if phase2 else 100,
            "documents_sha256": sha256_hex(documents_bytes),
            "dense_index_type": "IndexFlatIP",
            "bm25_tokenizer_version": CJK_TOKENIZER_VERSION,
        }
        (staging / "hybrid.config.json").write_bytes(canonical_json_bytes(hybrid_config))

        output_files = []
        for name in (
            "bm25.index.json",
            "dense.faiss",
            "dense.metadata.json",
            "documents.jsonl",
            "faiss_build_receipt.json",
            "hybrid.config.json",
        ):
            content = (staging / name).read_bytes()
            output_files.append(
                {"path": name, "sha256": sha256_hex(content), "size_bytes": len(content)}
            )
        manifest = {
            "schema_version": "1.0",
            "index_id": output.name,
            "build_timestamp": args.build_timestamp,
            "catalog_version": release_manifest["catalog_version"],
            "release_manifest_sha256": sha256_hex(release_manifest_bytes),
            "documents_sha256": sha256_hex(documents_bytes),
            "document_count": len(documents),
            "unique_canonical_tag_count": len({item.canonical_tag for item in documents}),
            "languages": ["en", "zh"],
            "dense": {
                "model_id": receipt["model_id"],
                "model_revision": receipt["revision"],
                "model_receipt_sha256": sha256_hex(receipt_bytes),
                "verified_model_files": verification.checked_files,
                "sentence_transformers_version": importlib.metadata.version("sentence-transformers"),
                "faiss_version": faiss_receipt["faiss_version"],
                "numpy_version": importlib.metadata.version("numpy"),
                "embedding_dimension": 384,
                "normalize_embeddings": True,
                "index_type": "IndexFlatIP",
                "vector_count": faiss_receipt["vector_count"],
            },
            "bm25": {
                "schema_version": bm25_payload["schema_version"],
                "tokenizer_version": CJK_TOKENIZER_VERSION,
                "k1": bm25_payload["k1"],
                "b": bm25_payload["b"],
                "document_count": bm25_payload["document_count"],
            },
            "hybrid": hybrid_config,
            "files": output_files,
        }
        (staging / "index_manifest.json").write_bytes(canonical_json_bytes(manifest))
        publish_directory_atomically(
            staging,
            output,
            expected_files={
                "bm25.index.json",
                "dense.faiss",
                "dense.metadata.json",
                "documents.jsonl",
            "faiss_build_receipt.json",
                "hybrid.config.json",
                "index_manifest.json",
            },
        )
        print(json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2))
        return 0
    except Exception:
        if staging.exists():
            shutil.rmtree(staging)
        raise


if __name__ == "__main__":
    raise SystemExit(main())