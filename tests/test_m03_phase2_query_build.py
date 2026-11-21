from __future__ import annotations

import json
from pathlib import Path
import unittest

from src.knowledge import validate_catalog_v3_payload
from src.knowledge.evaluation import validate_phase2_query_sets
from src.knowledge.query_builder_v3 import build_query_sets_v3

ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / "data/knowledge/osm/v3/releases/m03_phase2_2026-10-06_v1/catalog.json"
CANDIDATES = ROOT / "data/knowledge/osm/v3/candidates/m03_phase2_candidates_v1.json"


class Phase2QueryBuildTests(unittest.TestCase):
    def test_615_queries_cover_every_tag_and_new_attribute_semantics(self):
        catalog = validate_catalog_v3_payload(json.loads(CATALOG.read_text("utf-8")))
        candidates = json.loads(CANDIDATES.read_text("utf-8"))["candidates"]
        retained = {item["canonical_tag"] for item in candidates if item["retained_from_phase1"]}
        dev, public = build_query_sets_v3(catalog, retained)
        report = validate_phase2_query_sets(catalog, dev, public, retained)
        self.assertEqual((report["dev_count"], report["public_eval_count"]), (410, 205))
        self.assertEqual((report["phase1_retained_target_count"], report["phase2_new_target_count"]), (100, 105))
        self.assertEqual(len({item.query_text for item in [*dev, *public]}), 615)
        self.assertEqual({item.language.value for item in public}, {"en", "zh"})
        self.assertTrue(any(item.target_tag == "access=private" and item.tag_role.value == "attribute" for item in public))
        self.assertTrue(any(item.target_tag == "wheelchair=limited" and item.tag_role.value == "attribute" for item in public))


if __name__ == "__main__":
    unittest.main()
