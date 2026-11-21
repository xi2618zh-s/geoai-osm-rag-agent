from __future__ import annotations

import unittest

from src.knowledge.evaluation import (
    Phase2QuerySetError,
    parse_query_jsonl,
    compute_phase2_cohort_metrics,
    validate_phase2_query_sets,
)
from src.knowledge.contracts import RetrievalQueryV2
from tests.test_m03_phase2_schema import v3_fixture
from src.knowledge import validate_catalog_v3_payload


class Phase2EvaluationTests(unittest.TestCase):
    def setUp(self):
        self.catalog = validate_catalog_v3_payload(v3_fixture())
        self.retained = {"amenity=cafe", "amenity=restaurant"}

    def query(self, tag, split, language, text, *, alias=False):
        entry = next(item for item in self.catalog.entries if item.canonical_tag == tag)
        return RetrievalQueryV2.model_validate({
            "schema_version": "2.0", "query_id": f"{split}:{tag}:{language}",
            "split": split, "language": language, "query_text": text,
            "target_tag": tag, "excluded_tags": [entry.hard_negatives[0].canonical_tag],
            "primary_category": entry.primary_category.value,
            "tag_role": entry.tag_role.value,
            "target_cohort": "phase1_retained" if tag in self.retained else "phase2_new",
            "challenge_type": "alias" if alias else "paraphrase",
            "construction_note": "Independent test query.", "author": "GeoAI project team",
            "construction_method": "codex_assisted_manual_review", "codex_assisted": True,
            "catalog_version": self.catalog.catalog_version,
        })

    def sets(self):
        dev = []
        public = []
        for index, entry in enumerate(self.catalog.entries):
            tag = entry.canonical_tag
            dev.extend([
                self.query(tag, "dev", "en", f"Find the English map feature number {index}."),
                self.query(tag, "dev", "zh", f"查询第 {index} 种中文地图对象。"),
            ])
            public.append(self.query(tag, "public_eval", "en" if index % 2 else "zh", f"Public scenario number {index}: choose a fitting map label."))
        return dev, public

    def test_versioned_query_parser_preserves_v1_and_rejects_mixed(self):
        dev, _ = self.sets()
        v2_bytes = ("\n".join(item.model_dump_json() for item in dev) + "\n").encode("utf-8")
        parsed = parse_query_jsonl(v2_bytes)
        self.assertEqual([item.query_id for item in parsed], [item.query_id for item in dev])
        old = dev[0].model_dump(mode="json")
        old.pop("tag_role")
        old.pop("target_cohort")
        old["schema_version"] = "1.0"
        from src.knowledge.contracts import RetrievalQueryV1
        v1 = RetrievalQueryV1.model_validate(old)
        self.assertEqual(parse_query_jsonl((v1.model_dump_json() + "\n").encode())[0].schema_version, "1.0")
        with self.assertRaises(Phase2QuerySetError):
            parse_query_jsonl((v1.model_dump_json() + "\n" + dev[0].model_dump_json()).encode())

    def test_complete_coverage_and_cohort_metrics(self):
        dev, public = self.sets()
        report = validate_phase2_query_sets(self.catalog, dev, public, self.retained, minimum_total=12)
        self.assertEqual(report["dev_count"], 8)
        self.assertEqual(report["public_eval_count"], 4)
        self.assertEqual(report["target_count"], 4)
        rankings = {item.query_id: [item.target_tag] for item in public}
        metrics = compute_phase2_cohort_metrics(public, rankings)
        self.assertEqual(metrics["phase1_retained"].query_count, 2)
        self.assertEqual(metrics["phase2_new"].query_count, 2)
        self.assertEqual(metrics["phase1_retained"].recall_at_1, 1.0)

    def test_rejects_missing_target_and_wrong_cohort(self):
        dev, public = self.sets()
        with self.assertRaises(Phase2QuerySetError):
            validate_phase2_query_sets(self.catalog, dev, public[:-1], self.retained, minimum_total=12)
        changed = public[0].model_copy(update={"target_cohort": "phase2_new"})
        with self.assertRaises(Phase2QuerySetError):
            validate_phase2_query_sets(self.catalog, dev, [changed, *public[1:]], self.retained, minimum_total=12)

    def test_rejects_public_target_alias_and_dev_public_duplicate_text(self):
        dev, public = self.sets()
        entry = self.catalog.entries[0]
        changed = public[0].model_copy(update={"query_text": f"Where is {entry.name_en}?"})
        with self.assertRaises(Phase2QuerySetError):
            validate_phase2_query_sets(self.catalog, dev, [changed, *public[1:]], self.retained, minimum_total=12)
        changed = public[0].model_copy(update={"query_text": dev[0].query_text})
        with self.assertRaises(Phase2QuerySetError):
            validate_phase2_query_sets(self.catalog, dev, [changed, *public[1:]], self.retained, minimum_total=12)


if __name__ == "__main__":
    unittest.main()