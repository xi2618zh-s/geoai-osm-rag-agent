from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from src.knowledge import validate_catalog_payload
from src.knowledge.build import build_index_documents
from src.knowledge.indexes import Bm25IndexV1, build_bm25_index_payload
from src.knowledge.model_provenance import ModelProvenanceError, verify_model_snapshot
from src.knowledge.serialization import canonical_json_bytes


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "data" / "fixtures" / "knowledge" / "m03" / "v2" / "valid_catalog.json"


def documents():
    catalog = validate_catalog_payload(json.loads(FIXTURE.read_text("utf-8")))
    return build_index_documents(catalog)


class BilingualIndexTests(unittest.TestCase):
    def test_bm25_payload_is_deterministic_and_uses_all_shared_documents(self):
        docs = documents()
        first = build_bm25_index_payload(docs)
        second = build_bm25_index_payload(docs)
        self.assertEqual(canonical_json_bytes(first), canonical_json_bytes(second))
        self.assertEqual(first["document_count"], len(docs))
        self.assertEqual(first["document_ids"], [item.document_id for item in docs])
        self.assertEqual(first["tokenizer_version"], "m03-cjk-unigram-bigram-v1")

    def test_bm25_retrieves_chinese_latin_and_mixed_queries(self):
        index = Bm25IndexV1(build_bm25_index_payload(documents()))
        self.assertEqual(index.search_tags("咖啡馆", k=1)[0][0], "amenity=cafe")
        self.assertEqual(index.search_tags("restaurant full meals", k=1)[0][0], "amenity=restaurant")
        self.assertEqual(index.search_tags("coffee 咖啡", k=1)[0][0], "amenity=cafe")

    def test_bm25_rejects_empty_document_input(self):
        with self.assertRaises(ValueError):
            build_bm25_index_payload([])

    def test_model_snapshot_receipt_hashes_are_enforced_without_paths_in_receipt(self):
        with tempfile.TemporaryDirectory(prefix="m03_model_") as temp:
            root = Path(temp)
            content = b"config"
            (root / "config.json").write_bytes(content)
            receipt = {
                "model_id": "fixture/model",
                "revision": "a" * 40,
                "embedding_dimension": 384,
                "normalize_embeddings": True,
                "files": [{
                    "path": "config.json",
                    "sha256": hashlib.sha256(content).hexdigest(),
                    "size_bytes": len(content),
                }],
            }
            result = verify_model_snapshot(root, receipt)
            self.assertEqual(result.checked_files, 1)
            (root / "config.json").write_bytes(b"tampered")
            with self.assertRaises(ModelProvenanceError) as raised:
                verify_model_snapshot(root, receipt)
            self.assertEqual(raised.exception.code, "OSM_KB_MODEL_HASH_MISMATCH")


if __name__ == "__main__":
    unittest.main()