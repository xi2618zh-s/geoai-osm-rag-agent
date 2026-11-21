from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import numpy as np

from src.knowledge import validate_catalog_v3_payload
from src.knowledge.build import build_index_documents_v3
from src.knowledge.indexes import parse_index_documents
from src.knowledge.serialization import canonical_jsonl_bytes
from tests.test_m03_phase2_schema import v3_fixture


class Phase2IndexContractTests(unittest.TestCase):
    def test_existing_phase1_document_release_parses_unchanged(self):
        root = Path(__file__).resolve().parents[1]
        path = root / "data/knowledge/osm/v2/releases/m03_phase1_2026-10-05_v1/index_documents.jsonl"
        documents = parse_index_documents(path.read_bytes(), expected_count=200)
        self.assertEqual(len(documents), 200)
        self.assertEqual({item.schema_version for item in documents}, {"1.0"})

    def test_v3_documents_load_with_roles_and_keep_v1_strict(self):
        docs = build_index_documents_v3(validate_catalog_v3_payload(v3_fixture()))
        loaded = parse_index_documents(canonical_jsonl_bytes(docs), expected_count=8)
        self.assertEqual(len(loaded), 8)
        self.assertEqual({doc.schema_version for doc in loaded}, {"2.0"})
        self.assertEqual({doc.tag_role.value for doc in loaded}, {"feature", "land_use", "attribute"})
        with self.assertRaises(ValueError):
            parse_index_documents(canonical_jsonl_bytes(docs), expected_count=200)

    def test_isolated_faiss_writer_accepts_manifest_count_and_keeps_384_dimensions(self):
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory(prefix="m03_phase2_faiss_") as temp:
            directory = Path(temp)
            vectors = np.eye(8, 384, dtype=np.float32)
            np.save(directory / "vectors.npy", vectors, allow_pickle=False)
            subprocess.run([
                sys.executable, str(root / "scripts/write_m03_faiss_index.py"),
                "--embeddings", str(directory / "vectors.npy"),
                "--index", str(directory / "dense.faiss"),
                "--receipt", str(directory / "receipt.json"),
                "--expected-count", "8",
            ], check=True, capture_output=True)
            receipt = json.loads((directory / "receipt.json").read_text("utf-8"))
            self.assertEqual((receipt["vector_count"], receipt["dimension"]), (8, 384))

    def test_mixed_document_versions_are_rejected(self):
        docs = build_index_documents_v3(validate_catalog_v3_payload(v3_fixture()))
        payload = canonical_jsonl_bytes(docs).decode("utf-8").splitlines()
        payload[0] = payload[0].replace('"schema_version":"2.0"', '"schema_version":"1.0"')
        with self.assertRaises(ValueError):
            parse_index_documents(("\n".join(payload) + "\n").encode(), expected_count=8)


if __name__ == "__main__":
    unittest.main()
