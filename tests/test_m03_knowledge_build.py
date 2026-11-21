from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from src.knowledge import validate_catalog_payload
from src.knowledge.build import (
    AtomicPublishError,
    build_index_documents,
    publish_directory_atomically,
    validate_release_size,
)
from src.knowledge.manifest import validate_manifest_files
from src.knowledge.serialization import canonical_jsonl_bytes
from src.knowledge.tokenizer import CJK_TOKENIZER_VERSION, tokenize_bilingual


ROOT = Path(__file__).resolve().parents[1]
FIXTURE_DIR = ROOT / "data" / "fixtures" / "knowledge" / "m03" / "v2"


def catalog():
    return validate_catalog_payload(
        json.loads((FIXTURE_DIR / "valid_catalog.json").read_text("utf-8"))
    )


class KnowledgeBuildTests(unittest.TestCase):
    def test_fixture_manifest_hashes_match(self):
        result = validate_manifest_files(FIXTURE_DIR / "manifest.json")
        self.assertEqual(result.checked_files, 3)
        self.assertTrue(result.valid)

    def test_index_documents_are_two_per_tag_sorted_and_repeatable(self):
        documents = build_index_documents(catalog())
        self.assertEqual(len(documents), 4)
        self.assertEqual(
            [doc.document_id for doc in documents],
            sorted(doc.document_id for doc in documents),
        )
        self.assertEqual({doc.language.value for doc in documents}, {"en", "zh"})
        self.assertEqual(canonical_jsonl_bytes(documents), canonical_jsonl_bytes(build_index_documents(catalog())))
        self.assertTrue(canonical_jsonl_bytes(documents).endswith(b"\n"))

    def test_cjk_tokenizer_is_deterministic_for_latin_han_and_mixed_text(self):
        self.assertEqual(CJK_TOKENIZER_VERSION, "m03-cjk-unigram-bigram-v1")
        self.assertEqual(tokenize_bilingual("Coffee shops"), ["coffee", "shop"])
        self.assertEqual(tokenize_bilingual("咖啡馆"), ["咖", "啡", "馆", "咖啡", "啡馆"])
        self.assertEqual(
            tokenize_bilingual("Coffee shops 咖啡馆"),
            ["coffee", "shop", "咖", "啡", "馆", "咖啡", "啡馆"],
        )
        self.assertEqual(tokenize_bilingual("ＡＢＣ 咖啡"), ["abc", "咖", "啡", "咖啡"])

    def test_release_size_rejects_empty_below_boundary_and_above_limit(self):
        for count in (0, 99, 501):
            with self.subTest(count=count):
                with self.assertRaises(ValueError):
                    validate_release_size(count, minimum=100, maximum=500)
        validate_release_size(100, minimum=100, maximum=500)
        validate_release_size(500, minimum=100, maximum=500)

    def test_failed_atomic_publish_leaves_existing_target_unchanged(self):
        with tempfile.TemporaryDirectory(prefix="m03_publish_") as temp:
            root = Path(temp)
            target = root / "release"
            target.mkdir()
            (target / "sentinel.txt").write_text("v1", encoding="utf-8")
            staging = root / ".staging-build"
            staging.mkdir()
            (staging / "catalog.json").write_text("partial", encoding="utf-8")

            with self.assertRaises(AtomicPublishError):
                publish_directory_atomically(staging, target, expected_files={"missing.json"})

            self.assertEqual((target / "sentinel.txt").read_text("utf-8"), "v1")
            self.assertFalse((target / "catalog.json").exists())

    def test_manifest_validator_rejects_tampering(self):
        with tempfile.TemporaryDirectory(prefix="m03_manifest_") as temp:
            root = Path(temp)
            payload = b"content\n"
            (root / "data.txt").write_bytes(payload)
            manifest = {
                "schema_version": "1.0",
                "files": [{
                    "path": "data.txt",
                    "sha256": hashlib.sha256(payload).hexdigest(),
                    "size_bytes": len(payload),
                }],
            }
            (root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            self.assertTrue(validate_manifest_files(root / "manifest.json").valid)
            (root / "data.txt").write_bytes(b"changed\n")
            with self.assertRaises(ValueError):
                validate_manifest_files(root / "manifest.json")

class CandidateFreezeTests(unittest.TestCase):
    def test_phase1_candidate_allowlist_is_frozen_unique_and_quota_exact(self):
        path = ROOT / "data" / "knowledge" / "osm" / "v2" / "candidates" / "m03_phase1_candidates_v1.json"
        sidecar = path.with_suffix(".sha256")
        payload = json.loads(path.read_text("utf-8"))
        tags = [item["canonical_tag"] for item in payload["candidates"]]
        self.assertEqual(payload["expected_unique_tags"], 100)
        self.assertEqual(len(tags), 100)
        self.assertEqual(len(set(tags)), 100)
        self.assertEqual(tags, sorted(tags))
        self.assertEqual(sum(bool(item["retained_from_v1"]) for item in payload["candidates"]), 21)
        counts = {}
        for item in payload["candidates"]:
            counts[item["primary_category"]] = counts.get(item["primary_category"], 0) + 1
        self.assertEqual(counts, payload["category_quotas"])
        expected_hash = sidecar.read_text("utf-8").split()[0]
        self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), expected_hash)
        airport = next(item for item in payload["candidates"] if item["canonical_tag"] == "aeroway=airport")
        self.assertTrue(airport["compatibility_only"])

if __name__ == "__main__":
    unittest.main()