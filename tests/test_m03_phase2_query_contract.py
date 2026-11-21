from __future__ import annotations

import unittest

from src.knowledge.contracts import RetrievalQueryV1, RetrievalQueryV2


class Phase2QueryContractTests(unittest.TestCase):
    def payload(self):
        return {
            "schema_version": "2.0",
            "query_id": "m03:phase2:public_eval:wheelchair=yes:zh",
            "split": "public_eval",
            "language": "zh",
            "query_text": "一处设施标注了轮椅可以进入，应选择哪个附加属性标签？",
            "target_tag": "wheelchair=yes",
            "excluded_tags": ["access=private"],
            "primary_category": "accessibility",
            "tag_role": "attribute",
            "target_cohort": "phase2_new",
            "challenge_type": "hard_negative",
            "construction_note": "Artificial query for schema verification.",
            "author": "GeoAI project team",
            "construction_method": "manual_project_review",
            "codex_assisted": False,
            "catalog_version": "fixture.m03.phase2.v3",
        }

    def test_v2_query_supports_new_categories_role_and_cohort(self):
        query = RetrievalQueryV2.model_validate(self.payload())
        self.assertEqual(query.primary_category.value, "accessibility")
        self.assertEqual(query.tag_role.value, "attribute")
        self.assertEqual(query.target_cohort, "phase2_new")

    def test_v1_query_remains_strict(self):
        with self.assertRaises(Exception):
            RetrievalQueryV1.model_validate(self.payload())

    def test_query_v2_rejects_unexpected_cohort_and_fields(self):
        payload = self.payload()
        payload["target_cohort"] = "hidden"
        with self.assertRaises(Exception):
            RetrievalQueryV2.model_validate(payload)
        payload = self.payload()
        payload["unexpected"] = True
        with self.assertRaises(Exception):
            RetrievalQueryV2.model_validate(payload)


if __name__ == "__main__":
    unittest.main()
