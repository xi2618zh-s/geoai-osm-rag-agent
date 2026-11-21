from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import unittest

from src.knowledge import validate_catalog_payload
from src.knowledge.catalog_builder import load_raw_sources
from src.knowledge.phase2_catalog_builder import build_phase2_catalog, parse_new_curation_rows

ROOT = Path(__file__).resolve().parents[1]
CANDIDATE = ROOT / "data/knowledge/osm/v3/candidates/m03_phase2_candidates_v1.json"
RAW = ROOT / "data/knowledge/osm/v3/raw/m03_phase2_2026-10-06_v2/raw_sources.jsonl"
OLD = ROOT / "data/knowledge/osm/v2/releases/m03_phase1_2026-10-05_v1/catalog.json"
ROWS = ROOT / "data/knowledge/osm/v3/curation/m03_phase2_new_tags_draft.tsv"


class Phase2CatalogBuildTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.candidates = json.loads(CANDIDATE.read_text("utf-8"))
        cls.sources = load_raw_sources(RAW)
        cls.old = validate_catalog_payload(json.loads(OLD.read_text("utf-8")))
        cls.rows = parse_new_curation_rows(ROWS.read_text("utf-8"))

    def build(self, candidates=None, rows=None):
        return build_phase2_catalog(
            self.candidates if candidates is None else candidates,
            self.sources, self.old, self.rows if rows is None else rows,
            candidate_sha256=hashlib.sha256(CANDIDATE.read_bytes()).hexdigest(),
            catalog_version="m03.phase2.v3.test",
        )

    def test_full_205_catalog_preserves_old_100_and_distinguishes_four_unknown_values(self):
        catalog = self.build()
        self.assertEqual(len(catalog.entries), 205)
        self.assertEqual(len({x.canonical_tag for x in catalog.entries}), 205)
        self.assertEqual(sum(x.status.value == "unknown" for x in catalog.entries), 4)
        self.assertEqual({x.canonical_tag for x in catalog.entries if x.status.value == "unknown"},
            {"kerb=lowered", "wheelchair=yes", "wheelchair=limited", "wheelchair=no"})
        self.assertEqual(next(x for x in catalog.entries if x.canonical_tag == "landuse=education").primary_category.value, "land_use")
        self.assertEqual(next(x for x in catalog.entries if x.canonical_tag == "access=private").applicability.semantic_kind.value, "legal_access")
        self.assertEqual(hashlib.sha256(OLD.read_bytes()).hexdigest(),
            "c624eef08aacbc7d1c5b87e357884039399680b23ba89b8833b0c360191e29d0")

    def test_missing_curation_and_category_drift_fail(self):
        with self.assertRaises(Exception):
            self.build(rows=self.rows[:-1])
        changed = deepcopy(self.candidates)
        changed["candidates"][0]["primary_category"] = "building"
        with self.assertRaises(Exception):
            self.build(candidates=changed)


if __name__ == "__main__":
    unittest.main()
