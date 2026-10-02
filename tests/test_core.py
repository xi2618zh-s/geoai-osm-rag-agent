from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import faiss
import numpy as np

from src.llm.ollama_client import _parse_json_from_content
from src.path_compat import read_faiss_index, write_faiss_index
from src.pipeline import safe_slug, simple_place_heuristic
from src.query.llm_parser import validate_llm_response
from src.rag.retriever import RetrievedChunk, pick_tag_from_chunks


def chunk(score=0.5, key="amenity", value="cafe"):
    return RetrievedChunk(score, "content", "https://example.test", "title", key, value)


class CoreBehaviorTests(unittest.TestCase):
    def test_json_parser_accepts_clean_and_wrapped_json(self):
        self.assertTrue(_parse_json_from_content('{"x": 1}').ok)
        self.assertTrue(_parse_json_from_content('```json\n{"x": 2}\n```').ok)

    def test_validator_requires_grounded_tag_and_valid_types(self):
        evidence = [chunk()]
        valid = {
            "place": "Lund",
            "tag": {"key": "amenity", "value": "cafe"},
            "confidence": 0.9,
            "explanation": "The evidence names this tag.",
        }
        self.assertTrue(validate_llm_response(valid, evidence))

        invented = {**valid, "tag": {"key": "invented", "value": "value"}}
        self.assertFalse(validate_llm_response(invented, evidence))
        self.assertFalse(validate_llm_response({**valid, "confidence": 9}, evidence))
        self.assertFalse(validate_llm_response({**valid, "place": 123}, evidence))

    def test_weighted_fallback_uses_threshold(self):
        hits = [chunk(0.4), chunk(0.3), chunk(0.65, "shop", "supermarket")]
        self.assertEqual(pick_tag_from_chunks(hits), ("amenity", "cafe"))
        with self.assertRaises(ValueError):
            pick_tag_from_chunks([chunk(-0.1)], min_score=0.15)

    def test_place_and_slug_support_unicode(self):
        self.assertEqual(simple_place_heuristic("Find cafes in Malmö"), "Malmö")
        self.assertEqual(safe_slug("Malmö Centrum"), "malmo_centrum")

    def test_faiss_round_trip_in_unicode_directory(self):
        with tempfile.TemporaryDirectory(prefix="geoai_test_") as temp_dir:
            path = Path(temp_dir) / "中文索引" / "faiss_index"
            index = faiss.IndexFlatIP(3)
            index.add(np.array([[1.0, 0.0, 0.0]], dtype=np.float32))
            write_faiss_index(index, path)
            loaded = read_faiss_index(path)
            self.assertEqual(loaded.ntotal, 1)
            self.assertEqual(loaded.d, 3)


class FlaskContractTests(unittest.TestCase):
    def test_chat_returns_unique_geojson_url(self):
        import app_min

        result = {
            "success": True,
            "place": "Lund",
            "chosen_tag": "amenity=cafe",
            "count": 2,
            "counts_by_type": {"node": 1, "way": 1, "relation": 0},
            "geojson_filename": "result_test.geojson",
            "evidence": [],
            "llm_ok": False,
            "llm_confidence": 0,
        }
        with patch.object(app_min, "run_query", return_value=result):
            with app_min.app.test_client() as client:
                response = client.post("/chat", json={"query": "cafes in Lund"})
        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertEqual(body["geojson_url"], "/output/result_test.geojson")
        self.assertEqual(body["counts_by_type"]["way"], 1)


if __name__ == "__main__":
    unittest.main()
