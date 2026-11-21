from __future__ import annotations

import hashlib
import json
from pathlib import Path
from unittest.mock import patch
import unittest

from src.knowledge.build import build_index_documents
from src.knowledge.catalog_builder import build_catalog_from_payloads, load_raw_sources


ROOT = Path(__file__).resolve().parents[1]
V2 = ROOT / "data" / "knowledge" / "osm" / "v2"
CANDIDATES = V2 / "candidates" / "m03_phase1_candidates_v1.json"
CURATION = V2 / "curation" / "m03_phase1_curation_v2.json"
RAW = V2 / "raw" / "m03_phase1_2026-10-05_v1" / "raw_sources.jsonl"
LEGACY = ROOT / "data" / "knowledge" / "tag_catalog_v1.json"


class Phase1CatalogIntegrationTests(unittest.TestCase):
    def test_offline_build_produces_100_entries_200_documents_and_keeps_v1_tags(self):
        candidate_bytes = CANDIDATES.read_bytes()
        candidates = json.loads(candidate_bytes)
        curation = json.loads(CURATION.read_text("utf-8"))
        sources = load_raw_sources(RAW)
        with patch("requests.get", side_effect=AssertionError("offline build attempted network")):
            catalog = build_catalog_from_payloads(
                candidates,
                sources,
                curation,
                candidate_manifest_sha256=hashlib.sha256(candidate_bytes).hexdigest(),
                catalog_version="m03-phase1-2026-10-05.v1",
            )
        tags = {entry.canonical_tag for entry in catalog.entries}
        legacy_tags = {
            f"{entry['key']}={entry['value']}"
            for entry in json.loads(LEGACY.read_text("utf-8"))["entries"]
        }
        self.assertEqual(len(catalog.entries), 100)
        self.assertEqual(len(tags), 100)
        self.assertTrue(legacy_tags.issubset(tags))
        self.assertEqual(len(build_index_documents(catalog)), 200)
        self.assertEqual(sum(entry.status.value == "in_use" for entry in catalog.entries), 2)
        self.assertEqual(sum(entry.status.value == "obsolete" for entry in catalog.entries), 1)
        self.assertEqual(
            next(entry for entry in catalog.entries if entry.canonical_tag == "aeroway=airport").status.value,
            "obsolete",
        )

    def test_curation_v2_is_frozen_and_all_confusion_targets_are_allowlisted(self):
        expected_hash = CURATION.with_suffix(".sha256").read_text("utf-8").split()[0]
        self.assertEqual(hashlib.sha256(CURATION.read_bytes()).hexdigest(), expected_hash)
        candidates = json.loads(CANDIDATES.read_text("utf-8"))
        known = {item["canonical_tag"] for item in candidates["candidates"]}
        curation = json.loads(CURATION.read_text("utf-8"))
        self.assertEqual(len(curation["entries"]), 100)
        self.assertEqual(
            {item["canonical_tag"] for item in curation["entries"]},
            known,
        )
        self.assertTrue(all(item["hard_negative_tag"] in known for item in curation["entries"]))
        self.assertTrue(all(item["hard_negative_tag"] != item["canonical_tag"] for item in curation["entries"]))


if __name__ == "__main__":
    unittest.main()