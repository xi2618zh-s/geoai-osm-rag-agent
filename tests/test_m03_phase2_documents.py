from __future__ import annotations

import unittest

from src.knowledge import validate_catalog_v3_payload
from src.knowledge.build import build_index_documents_v3
from src.knowledge.contracts import IndexDocumentV1, IndexDocumentV2
from src.knowledge.serialization import canonical_jsonl_bytes
from tests.test_m03_phase2_schema import v3_fixture


class Phase2DocumentTests(unittest.TestCase):
    def test_two_bilingual_documents_per_tag_with_role_and_applicability(self):
        catalog = validate_catalog_v3_payload(v3_fixture())
        docs = build_index_documents_v3(catalog)
        self.assertEqual(len(docs), 8)
        self.assertEqual(len({x.document_id for x in docs}), 8)
        self.assertEqual([x.document_id for x in docs], sorted(x.document_id for x in docs))
        by_tag = {}
        for doc in docs:
            by_tag.setdefault(doc.canonical_tag, []).append(doc)
            self.assertEqual(doc.schema_version, "2.0")
            self.assertEqual(doc.catalog_version, catalog.catalog_version)
        self.assertEqual(set(by_tag), {x.canonical_tag for x in catalog.entries})
        self.assertTrue(all({x.language.value for x in pair} == {"en", "zh"} for pair in by_tag.values()))
        attribute = by_tag["wheelchair=yes"][0]
        self.assertEqual(attribute.tag_role.value, "attribute")
        self.assertEqual(attribute.applicability.semantic_kind.value, "wheelchair_access")
        self.assertIn("Combine with an existing facility", next(x.text for x in by_tag["wheelchair=yes"] if x.language.value == "en"))
        self.assertEqual({x.tag_role.value for x in by_tag["landuse=residential"]}, {"land_use"})

    def test_document_v2_serialization_is_deterministic_and_v1_stays_strict(self):
        catalog = validate_catalog_v3_payload(v3_fixture())
        first = canonical_jsonl_bytes(build_index_documents_v3(catalog))
        second = canonical_jsonl_bytes(build_index_documents_v3(catalog))
        self.assertEqual(first, second)
        self.assertTrue(first.endswith(b"\n"))
        attribute = next(x for x in build_index_documents_v3(catalog) if x.canonical_tag == "wheelchair=yes")
        self.assertIsInstance(attribute, IndexDocumentV2)
        with self.assertRaises(Exception):
            IndexDocumentV1.model_validate(attribute.model_dump(mode="json"))

    def test_hard_negative_reason_is_not_positive_document_text(self):
        catalog = validate_catalog_v3_payload(v3_fixture())
        docs = build_index_documents_v3(catalog)
        for doc in docs:
            self.assertNotIn("A restaurant primarily serves", doc.text)
            self.assertNotIn("Wheelchair access is an attribute", doc.text)


if __name__ == "__main__":
    unittest.main()
