from __future__ import annotations

import hashlib
import json
from pathlib import Path
import unittest

from src.knowledge.retrieval import M03IndexBundle, weighted_rrf
from src.path_compat import read_faiss_index


ROOT = Path(__file__).resolve().parents[1]
INDEX_DIR = ROOT / "faiss_index" / "m03_phase1_v2_e8f8c211"
RELEASE_DOCS = ROOT / "data" / "knowledge" / "osm" / "v2" / "releases" / "m03_phase1_2026-10-05_v1" / "index_documents.jsonl"


class M03RetrieverTests(unittest.TestCase):
    def test_index_bundle_hashes_counts_and_shared_document_identity(self):
        bundle = M03IndexBundle(INDEX_DIR)
        self.assertEqual(bundle.document_count, 200)
        self.assertEqual(bundle.unique_tag_count, 100)
        self.assertEqual(bundle.documents_sha256, hashlib.sha256(RELEASE_DOCS.read_bytes()).hexdigest())
        self.assertEqual((INDEX_DIR / "documents.jsonl").read_bytes(), RELEASE_DOCS.read_bytes())
        index = read_faiss_index(INDEX_DIR / "dense.faiss")
        self.assertEqual(index.ntotal, 200)
        self.assertEqual(index.d, 384)

    def test_bm25_bundle_retrieves_chinese(self):
        bundle = M03IndexBundle(INDEX_DIR)
        ranking = bundle.bm25_ranking("想找可以喝咖啡和吃简餐的地方", k=5)
        self.assertIn("amenity=cafe", ranking[:3])

    def test_weighted_rrf_is_deterministic_and_uses_both_rankings(self):
        dense = ["amenity=cafe", "amenity=restaurant", "amenity=bar"]
        bm25 = ["amenity=restaurant", "amenity=cafe", "amenity=pub"]
        first = weighted_rrf(dense, bm25, dense_weight=1.0, bm25_weight=2.0, rrf_k=60)
        second = weighted_rrf(dense, bm25, dense_weight=1.0, bm25_weight=2.0, rrf_k=60)
        self.assertEqual(first, second)
        self.assertEqual(first[0][0], "amenity=restaurant")
        self.assertEqual({tag for tag, _score in first}, set(dense) | set(bm25))


if __name__ == "__main__":
    unittest.main()