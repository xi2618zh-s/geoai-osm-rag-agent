"""Offline M03 index bundle and tag-level dense/BM25/hybrid ranking."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Sequence

import numpy as np

from .indexes import Bm25IndexV1
from .tokenizer import CJK_TOKENIZER_VERSION
from src.path_compat import read_faiss_index


def weighted_rrf(
    dense_ranking: Sequence[str],
    bm25_ranking: Sequence[str],
    *,
    dense_weight: float,
    bm25_weight: float,
    rrf_k: int,
) -> list[tuple[str, float]]:
    if dense_weight < 0 or bm25_weight < 0 or dense_weight + bm25_weight <= 0:
        raise ValueError("hybrid weights must be non-negative with a positive sum")
    if rrf_k <= 0:
        raise ValueError("rrf_k must be positive")
    scores: dict[str, float] = {}
    for rank, tag in enumerate(dense_ranking, 1):
        scores[tag] = scores.get(tag, 0.0) + dense_weight / (rrf_k + rank)
    for rank, tag in enumerate(bm25_ranking, 1):
        scores[tag] = scores.get(tag, 0.0) + bm25_weight / (rrf_k + rank)
    return sorted(scores.items(), key=lambda item: (-item[1], item[0]))


class M03IndexBundle:
    def __init__(self, index_dir: Path):
        self.index_dir = Path(index_dir)
        self.manifest = json.loads((self.index_dir / "index_manifest.json").read_text("utf-8"))
        for record in self.manifest["files"]:
            path = self.index_dir / record["path"]
            content = path.read_bytes()
            if len(content) != record["size_bytes"]:
                raise ValueError(f"OSM_KB_INDEX_METADATA_MISMATCH: size {record['path']}")
            if hashlib.sha256(content).hexdigest() != record["sha256"]:
                raise ValueError(f"OSM_KB_INDEX_METADATA_MISMATCH: sha256 {record['path']}")
        self.documents_bytes = (self.index_dir / "documents.jsonl").read_bytes()
        self.documents_sha256 = hashlib.sha256(self.documents_bytes).hexdigest()
        if self.documents_sha256 != self.manifest["documents_sha256"]:
            raise ValueError("OSM_KB_INDEX_METADATA_MISMATCH: documents hash")
        self.metadata = json.loads((self.index_dir / "dense.metadata.json").read_text("utf-8"))
        self.bm25_payload = json.loads((self.index_dir / "bm25.index.json").read_text("utf-8"))
        self.bm25 = Bm25IndexV1(self.bm25_payload)
        self.hybrid_config = json.loads((self.index_dir / "hybrid.config.json").read_text("utf-8"))
        self.dense = read_faiss_index(self.index_dir / "dense.faiss")
        self.document_count = int(self.manifest["document_count"])
        self.unique_tag_count = int(self.manifest["unique_canonical_tag_count"])
        if self.document_count != len(self.metadata) or self.dense.ntotal != self.document_count:
            raise ValueError("OSM_KB_INDEX_COUNT_MISMATCH")
        if self.dense.d != 384 or self.manifest["dense"]["embedding_dimension"] != 384:
            raise ValueError("OSM_KB_EMBEDDING_DIMENSION_INVALID")
        if self.bm25_payload["document_ids"] != [item["document_id"] for item in self.metadata]:
            raise ValueError("OSM_KB_INDEX_METADATA_MISMATCH: document order")
        if self.bm25_payload["tokenizer_version"] != CJK_TOKENIZER_VERSION:
            raise ValueError("OSM_KB_INDEX_METADATA_MISMATCH: tokenizer")

    def dense_scored_ranking(self, vector: np.ndarray, *, k: int = 100) -> list[tuple[str, float]]:
        query = np.asarray(vector, dtype=np.float32).reshape(1, -1)
        if query.shape != (1, 384):
            raise ValueError("dense query vector must have dimension 384")
        distances, indices = self.dense.search(query, self.document_count)
        best: dict[str, float] = {}
        for score, index in zip(distances[0], indices[0]):
            if index < 0:
                continue
            tag = self.metadata[int(index)]["canonical_tag"]
            best[tag] = max(best.get(tag, float("-inf")), float(score))
        return sorted(best.items(), key=lambda item: (-item[1], item[0]))[:k]

    def dense_ranking(self, vector: np.ndarray, *, k: int = 100) -> list[str]:
        return [tag for tag, _score in self.dense_scored_ranking(vector, k=k)]

    def bm25_scored_ranking(self, query: str, *, k: int = 100) -> list[tuple[str, float]]:
        return self.bm25.search_tags(query, k=k)

    def bm25_ranking(self, query: str, *, k: int = 100) -> list[str]:
        return [tag for tag, _score in self.bm25_scored_ranking(query, k=k)]

    def hybrid_scored_ranking(
        self,
        query: str,
        vector: np.ndarray,
        *,
        k: int = 100,
        config: dict | None = None,
    ) -> list[tuple[str, float]]:
        settings = config or self.hybrid_config
        dense = self.dense_ranking(vector, k=self.unique_tag_count)
        bm25 = self.bm25_ranking(query, k=self.unique_tag_count)
        return weighted_rrf(
            dense,
            bm25,
            dense_weight=float(settings["dense_weight"]),
            bm25_weight=float(settings["bm25_weight"]),
            rrf_k=int(settings["rrf_k"]),
        )[:k]

    def hybrid_ranking(
        self,
        query: str,
        vector: np.ndarray,
        *,
        k: int = 100,
        config: dict | None = None,
    ) -> list[str]:
        return [tag for tag, _score in self.hybrid_scored_ranking(query, vector, k=k, config=config)]