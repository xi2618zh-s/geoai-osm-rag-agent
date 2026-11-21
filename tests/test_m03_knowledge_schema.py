from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import unittest

from pydantic import ValidationError

from src.knowledge import (
    KnowledgeCatalogV2,
    KnowledgeValidationError,
    RawWikiSourceV1,
    validate_catalog_payload,
    validate_raw_source_payload,
)
from src.knowledge.serialization import canonical_json_bytes


ROOT = Path(__file__).resolve().parents[1]
FIXTURE_DIR = ROOT / "data" / "fixtures" / "knowledge" / "m03" / "v2"


def load_catalog_payload() -> dict:
    return json.loads((FIXTURE_DIR / "valid_catalog.json").read_text("utf-8"))


def load_sources() -> list[dict]:
    return [
        json.loads(line)
        for line in (FIXTURE_DIR / "raw_sources.jsonl").read_text("utf-8").splitlines()
        if line.strip()
    ]


class KnowledgeContractTests(unittest.TestCase):
    def test_valid_catalog_round_trip_is_strict_and_stable(self):
        payload = load_catalog_payload()
        catalog = validate_catalog_payload(payload)
        self.assertIsInstance(catalog, KnowledgeCatalogV2)
        self.assertEqual(len(catalog.entries), 2)
        self.assertEqual(catalog.entries[0].entry_id, "osm-tag:amenity=cafe")
        reparsed = KnowledgeCatalogV2.model_validate_json(
            canonical_json_bytes(catalog).decode("utf-8")
        )
        self.assertEqual(reparsed, catalog)

        invalid = deepcopy(payload)
        invalid["unexpected"] = True
        with self.assertRaises(ValidationError):
            KnowledgeCatalogV2.model_validate(invalid)

    def test_valid_raw_source_round_trip(self):
        source = validate_raw_source_payload(load_sources()[0])
        self.assertIsInstance(source, RawWikiSourceV1)
        self.assertEqual(source.source_id, "osm-wiki:amenity=cafe@100")
        with self.assertRaises(ValidationError):
            RawWikiSourceV1.model_validate({**load_sources()[0], "extra": True})

    def test_raw_validator_rejects_redirect_only_snapshot(self):
        source = load_sources()[0]
        source["text"] = "Redirect to:\nKey:wheelchair"
        source["content_sha256"] = hashlib.sha256(source["text"].encode("utf-8")).hexdigest()
        with self.assertRaises(KnowledgeValidationError) as raised:
            validate_raw_source_payload(source)
        self.assertEqual(raised.exception.code, "OSM_KB_SOURCE_SCHEMA_INVALID")

    def test_invalid_fixture_cases_have_stable_error_codes(self):
        catalog_payload = load_catalog_payload()
        raw_source = load_sources()[0]
        cases = json.loads((FIXTURE_DIR / "invalid_cases.json").read_text("utf-8"))["cases"]

        for case in cases:
            with self.subTest(case=case["id"]):
                catalog = deepcopy(catalog_payload)
                source = deepcopy(raw_source)
                target = case["target"]
                if target == "entry":
                    entry = catalog["entries"][0]
                    if "path" in case:
                        entry[case["path"]] = case["value"]
                    elif case["mutation"] == "hard_negative_self":
                        entry["hard_negatives"][0]["canonical_tag"] = entry["canonical_tag"]
                    elif case["mutation"] == "hard_negative_unknown":
                        entry["hard_negatives"][0]["canonical_tag"] = "amenity=unknown_fixture"
                elif target == "catalog":
                    if case["mutation"] == "duplicate_first_entry":
                        catalog["entries"].append(deepcopy(catalog["entries"][0]))
                    elif case["mutation"] == "copy_first_alias_to_second":
                        catalog["entries"][1]["aliases_en"].append(
                            catalog["entries"][0]["aliases_en"][0]
                        )
                elif target == "source":
                    source[case["path"]] = case["value"]

                with self.assertRaises(KnowledgeValidationError) as raised:
                    if target == "source":
                        validate_raw_source_payload(source)
                    else:
                        validate_catalog_payload(catalog)
                self.assertEqual(raised.exception.code, case["expected_code"])

    def test_primary_category_is_singular_and_related_categories_are_unique(self):
        payload = load_catalog_payload()
        payload["entries"][0]["primary_category"] = ["food", "retail"]
        with self.assertRaises(KnowledgeValidationError) as raised:
            validate_catalog_payload(payload)
        self.assertEqual(raised.exception.code, "OSM_KB_CATEGORY_INVALID")

        payload = load_catalog_payload()
        payload["entries"][0]["related_categories"] = ["retail", "retail"]
        with self.assertRaises(KnowledgeValidationError) as raised:
            validate_catalog_payload(payload)
        self.assertEqual(raised.exception.code, "OSM_KB_CATEGORY_DUPLICATE")

    def test_unknown_status_is_allowed_only_as_explicit_enum_value(self):
        payload = load_catalog_payload()
        payload["entries"][0]["status"] = "unknown"
        self.assertEqual(validate_catalog_payload(payload).entries[0].status.value, "unknown")

    def test_canonical_serialization_uses_utf8_lf_sorted_keys_and_no_absolute_paths(self):
        catalog = validate_catalog_payload(load_catalog_payload())
        first = canonical_json_bytes(catalog)
        second = canonical_json_bytes(catalog)
        self.assertEqual(first, second)
        self.assertTrue(first.endswith(b"\n"))
        self.assertNotIn(b"\r\n", first)
        self.assertIn("咖啡馆".encode("utf-8"), first)
        self.assertNotIn(str(ROOT).encode("utf-8"), first)


if __name__ == "__main__":
    unittest.main()