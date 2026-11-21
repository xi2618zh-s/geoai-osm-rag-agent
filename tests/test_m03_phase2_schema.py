from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import unittest

from src.knowledge import validate_catalog_payload, validate_catalog_v3_payload
from src.knowledge.contracts import KnowledgeCatalogV2

ROOT = Path(__file__).resolve().parents[1]
V2_FIXTURE = ROOT / "data/fixtures/knowledge/m03/v2/valid_catalog.json"


def v3_fixture() -> dict:
    legacy = json.loads(V2_FIXTURE.read_text("utf-8"))
    cafe, restaurant = deepcopy(legacy["entries"])
    for item in (cafe, restaurant):
        item["schema_version"] = "3.0"
        item["tag_role"] = "feature"
        item["applicability"] = None
    wheelchair = deepcopy(cafe)
    wheelchair.update(
        entry_id="osm-tag:wheelchair=yes",
        canonical_tag="wheelchair=yes",
        key="wheelchair",
        value="yes",
        primary_category="accessibility",
        related_categories=[],
        name_en="Wheelchair accessible",
        name_zh="轮椅可进入",
        aliases_en=["wheelchair usable"],
        aliases_zh=["轮椅可用"],
        description_en="A wheelchair access attribute attached to another mapped feature.",
        description_zh="依附于其他地图对象的轮椅可进入属性。",
        compatible_tag_patterns=["amenity=*", "public_transport=*"],
        related_tags=["amenity=cafe"],
        hard_negatives=[{
            "canonical_tag": "amenity=cafe",
            "reason_en": "Wheelchair access is an attribute, while a cafe is a facility.",
            "reason_zh": "轮椅通行是附加属性，而咖啡馆是设施主标签。",
            "source_type": "manual_semantic_review",
        }],
        source_refs=["osm-wiki:wheelchair=yes@300"],
        tag_role="attribute",
        applicability={
            "semantic_kind": "wheelchair_access",
            "applies_to_keys": ["amenity", "public_transport"],
            "combination_note_en": "Combine with an existing facility or platform; do not infer an accessible route.",
            "combination_note_zh": "与已有设施或站台组合，不推断整条路径无障碍。",
        },
    )
    land = deepcopy(restaurant)
    land.update(
        entry_id="osm-tag:landuse=residential",
        canonical_tag="landuse=residential",
        key="landuse",
        value="residential",
        primary_category="land_use",
        related_categories=[],
        name_en="Residential land use",
        name_zh="居住用地",
        aliases_en=["housing land area"],
        aliases_zh=["住宅用地区"],
        description_en="An area primarily used for residential purposes.",
        description_zh="主要用于居住用途的面状区域。",
        compatible_tag_patterns=["building=*"],
        related_tags=["amenity=restaurant"],
        hard_negatives=[{
            "canonical_tag": "amenity=restaurant",
            "reason_en": "Residential land use is an area, while a restaurant is a facility.",
            "reason_zh": "居住用地是区域类型，而餐厅是独立设施。",
            "source_type": "manual_semantic_review",
        }],
        source_refs=["osm-wiki:landuse=residential@400"],
        tag_role="land_use",
        applicability=None,
    )
    return {
        "schema_version": "3.0",
        "catalog_version": "fixture.m03.phase2.v3",
        "release_phase": "m03_phase2",
        "candidate_manifest_sha256": "b" * 64,
        "license_id": "CC-BY-SA-2.0",
        "license_url": "https://creativecommons.org/licenses/by-sa/2.0/",
        "curation_notice": "Artificial test fixture only.",
        "entries": [cafe, restaurant, wheelchair, land],
    }


class Phase2SchemaTests(unittest.TestCase):
    def test_v2_catalog_stays_parseable_and_rejects_phase2(self):
        legacy = json.loads(V2_FIXTURE.read_text("utf-8"))
        self.assertEqual(len(validate_catalog_payload(legacy).entries), 2)
        changed = deepcopy(legacy)
        changed["release_phase"] = "m03_phase2"
        with self.assertRaises(Exception):
            KnowledgeCatalogV2.model_validate(changed)

    def test_v3_accepts_feature_land_use_and_attribute(self):
        catalog = validate_catalog_v3_payload(v3_fixture())
        self.assertEqual(catalog.schema_version, "3.0")
        self.assertEqual({x.tag_role.value for x in catalog.entries}, {"feature", "land_use", "attribute"})
        self.assertEqual({x.primary_category.value for x in catalog.entries if x.tag_role.value == "land_use"}, {"land_use"})
        self.assertEqual(catalog.entries[-1].applicability.semantic_kind.value, "wheelchair_access")

    def test_attribute_requires_applicability(self):
        payload = v3_fixture()
        payload["entries"][-2]["applicability"] = None
        with self.assertRaises(Exception):
            validate_catalog_v3_payload(payload)

    def test_legal_access_is_not_wheelchair_access(self):
        payload = v3_fixture()
        attribute = payload["entries"][-2]
        attribute.update(
            entry_id="osm-tag:access=private",
            canonical_tag="access=private",
            key="access",
            value="private",
            source_refs=["osm-wiki:access=private@300"],
        )
        with self.assertRaises(Exception):
            validate_catalog_v3_payload(payload)

    def test_v3_rejects_unapproved_fields(self):
        payload = v3_fixture()
        payload["entries"][0]["unapproved"] = True
        with self.assertRaises(Exception):
            validate_catalog_v3_payload(payload)


if __name__ == "__main__":
    unittest.main()
