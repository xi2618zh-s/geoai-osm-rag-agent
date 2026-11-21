from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from src.knowledge import validate_raw_source_payload
from src.knowledge.validation import validate_raw_source_v2_payload
from src.knowledge.catalog_builder import load_raw_sources
from src.knowledge.serialization import canonical_jsonl_bytes

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data/fixtures/knowledge/m03/v2/raw_sources.jsonl"


def key_source():
    source = json.loads(RAW.read_text("utf-8").splitlines()[0])
    excerpt = "wheelchair\n=\nyes\nWheelchairs have full unrestricted access."
    source.update(
        schema_version="2.0",
        source_id="osm-wiki:wheelchair=yes@555",
        canonical_tag="wheelchair=yes",
        request_url="https://wiki.openstreetmap.org/wiki/Key:wheelchair",
        canonical_url="https://wiki.openstreetmap.org/w/index.php?title=Key%3Awheelchair&oldid=555",
        revision="555",
        history_url="https://wiki.openstreetmap.org/w/index.php?title=Key%3Awheelchair&action=history",
        title="Key:wheelchair",
        text="Key:wheelchair\nStatus:\napproved\n" + excerpt,
        source_page_title="Key:wheelchair",
        source_page_kind="key_exception",
        source_page_id="osm-wiki-page:Key:wheelchair@555",
        value_evidence_excerpt=excerpt,
        value_evidence_sha256=hashlib.sha256(excerpt.encode()).hexdigest(),
        value_status_review="key_page_no_independent_value_status",
        reviewed_value_status="unknown",
        value_status_review_note="Key 页未给取值独立状态；取值级 status=unknown。",
    )
    source["content_sha256"] = hashlib.sha256(source["text"].encode()).hexdigest()
    return source


class Phase2RawSourceTests(unittest.TestCase):
    def test_v1_rejects_key_page_but_v2_explicit_exception_accepts(self):
        source = key_source()
        old = {key: value for key, value in source.items() if key not in {
            "source_page_title", "source_page_kind", "source_page_id",
            "value_evidence_excerpt", "value_evidence_sha256", "value_status_review",
            "reviewed_value_status", "value_status_review_note",
        }}
        old["schema_version"] = "1.0"
        with self.assertRaises(Exception):
            validate_raw_source_payload(old)
        parsed = validate_raw_source_v2_payload(source)
        self.assertEqual(parsed.source_page_title, "Key:wheelchair")
        self.assertEqual(parsed.value_status_review, "key_page_no_independent_value_status")

    def test_key_exception_is_limited_to_four_tags_and_exact_evidence(self):
        source = key_source()
        source["canonical_tag"] = "amenity=cafe"
        source["source_id"] = "osm-wiki:amenity=cafe@555"
        with self.assertRaises(Exception):
            validate_raw_source_v2_payload(source)
        source = key_source()
        source["value_evidence_excerpt"] = "not in fixed page"
        source["value_evidence_sha256"] = hashlib.sha256(b"not in fixed page").hexdigest()
        with self.assertRaises(Exception):
            validate_raw_source_v2_payload(source)

    def test_v2_loader_preserves_two_tag_mappings_to_one_key_page(self):
        yes = key_source()
        limited = key_source()
        text = yes["text"] + "\nwheelchair\n=\nlimited\nWheelchairs have partial access."
        digest = hashlib.sha256(text.encode()).hexdigest()
        for source in (yes, limited):
            source["text"] = text
            source["content_sha256"] = digest
        limited["canonical_tag"] = "wheelchair=limited"
        limited["source_id"] = "osm-wiki:wheelchair=limited@555"
        limited["value_evidence_excerpt"] = "wheelchair\n=\nlimited\nWheelchairs have partial access."
        limited["value_evidence_sha256"] = hashlib.sha256(limited["value_evidence_excerpt"].encode()).hexdigest()
        with tempfile.TemporaryDirectory(prefix="m03_key_sources_") as temp:
            path = Path(temp) / "raw_sources.jsonl"
            path.write_bytes(canonical_jsonl_bytes([yes, limited]))
            loaded = load_raw_sources(path)
        self.assertEqual(len(loaded), 2)
        self.assertEqual(len({item.source_page_id for item in loaded}), 1)

    def test_tag_source_v2_keeps_exact_page_identity(self):
        source = json.loads(RAW.read_text("utf-8").splitlines()[0])
        source.update(
            schema_version="2.0", source_page_title="Tag:amenity=cafe",
            source_page_kind="tag", source_page_id="osm-wiki-page:Tag:amenity=cafe@100",
            value_evidence_excerpt=None, value_evidence_sha256=None,
            value_status_review="tag_page_status", reviewed_value_status="approved",
            value_status_review_note="Tag page status reviewed on fixed oldid.",
        )
        parsed = validate_raw_source_v2_payload(source)
        self.assertEqual(parsed.canonical_tag, "amenity=cafe")
        source["source_page_title"] = "Key:amenity"
        with self.assertRaises(Exception):
            validate_raw_source_v2_payload(source)


if __name__ == "__main__":
    unittest.main()
