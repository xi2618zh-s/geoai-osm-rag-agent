from __future__ import annotations

import json
from pathlib import Path
import unittest

from src.knowledge import validate_catalog_payload
from src.knowledge.evaluation import audit_query_leakage
from src.knowledge.query_builder import build_query_sets, target_terms_in_query


ROOT = Path(__file__).resolve().parents[1]
CATALOG_PATH = ROOT / "data" / "knowledge" / "osm" / "v2" / "releases" / "m03_phase1_2026-10-05_v1" / "catalog.json"


class M03BenchmarkDataTests(unittest.TestCase):
    def setUp(self):
        self.catalog = validate_catalog_payload(json.loads(CATALOG_PATH.read_text("utf-8")))
        self.dev, self.public_eval = build_query_sets(self.catalog)

    def test_query_counts_splits_languages_and_target_coverage(self):
        self.assertEqual(len(self.dev), 200)
        self.assertEqual(len(self.public_eval), 100)
        self.assertEqual(len({item.query_id for item in self.dev + self.public_eval}), 300)
        for entry in self.catalog.entries:
            dev = [item for item in self.dev if item.target_tag == entry.canonical_tag]
            public = [item for item in self.public_eval if item.target_tag == entry.canonical_tag]
            self.assertEqual({item.language.value for item in dev}, {"en", "zh"})
            self.assertEqual(len(public), 1)
        self.assertEqual({item.split.value for item in self.dev}, {"dev"})
        self.assertEqual({item.split.value for item in self.public_eval}, {"public_eval"})

    def test_public_eval_has_25_alias_challenges_and_75_without_target_terms(self):
        alias_queries = [item for item in self.public_eval if item.challenge_type == "alias"]
        self.assertEqual(len(alias_queries), 25)
        by_tag = {entry.canonical_tag: entry for entry in self.catalog.entries}
        for query in self.public_eval:
            entry = by_tag[query.target_tag]
            hits = target_terms_in_query(entry, query.query_text)
            if query.challenge_type == "alias":
                self.assertTrue(hits)
                self.assertEqual(query.construction_method, "catalog_alias_challenge")
            else:
                self.assertEqual(hits, [])

    def test_every_public_category_has_both_languages_and_hard_negatives(self):
        categories = {entry.primary_category.value for entry in self.catalog.entries}
        for category in categories:
            languages = {
                item.language.value
                for item in self.public_eval
                if item.primary_category.value == category
            }
            self.assertEqual(languages, {"en", "zh"})
        self.assertTrue(all(item.excluded_tags for item in self.dev + self.public_eval))
        self.assertTrue(all(item.construction_note and item.author for item in self.dev + self.public_eval))

    def test_query_text_is_not_copied_into_catalog_fields(self):
        self.assertEqual(audit_query_leakage(self.catalog, self.dev).leak_count, 0)
        self.assertEqual(audit_query_leakage(self.catalog, self.public_eval).leak_count, 0)


if __name__ == "__main__":
    unittest.main()