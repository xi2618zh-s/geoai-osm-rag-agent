from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import requests
from pydantic import ValidationError

from src.contracts import ChatRequest, ChatSuccessResponse, LLMDecision, SimpleChatRequest
from src.llm.ollama_client import call_ollama_json
from src.osm.extractor import extract_features_to_geojson, osmium_extract_bbox
from src.osm.geocode import clear_geocode_cache, geocode_to_bbox
from src.rag.retriever import RetrievedChunk
from src.reliability import get_or_create_clip, retry_call


def chunk(key="amenity", value="cafe"):
    return RetrievedChunk(
        0.8,
        "Evidence text",
        "https://example.test",
        "Example",
        key,
        value,
    )


class ContractTests(unittest.TestCase):
    def test_request_contracts_strip_and_forbid_unknown_fields(self):
        request = ChatRequest.model_validate({"query": "  cafes in Lund  "}, strict=True)
        self.assertEqual(request.query, "cafes in Lund")
        with self.assertRaises(ValidationError):
            ChatRequest.model_validate({"query": "   "}, strict=True)
        with self.assertRaises(ValidationError):
            SimpleChatRequest.model_validate(
                {"key": "amenity", "value": "cafe", "unknown": 1}, strict=True
            )

    def test_llm_contract_requires_all_fields_and_rejects_extras(self):
        valid = {
            "place": "Lund",
            "tag": {"key": "amenity", "value": "cafe"},
            "confidence": 0.9,
            "explanation": "Supported by evidence.",
        }
        self.assertEqual(LLMDecision.model_validate(valid, strict=True).tag.value, "cafe")
        with self.assertRaises(ValidationError):
            LLMDecision.model_validate({k: v for k, v in valid.items() if k != "confidence"}, strict=True)
        with self.assertRaises(ValidationError):
            LLMDecision.model_validate({**valid, "extra": True}, strict=True)

    def test_response_contract_requires_trace_and_stage_timings(self):
        body = {
            "message": "ok",
            "query": "cafes in Lund",
            "place": "Lund",
            "chosen_tag": "amenity=cafe",
            "count": 1,
            "geojson_url": "/output/x.geojson",
            "evidence": [],
            "llm_ok": True,
            "llm_confidence": 0.9,
            "counts_by_type": {"node": 1, "way": 0, "relation": 0},
            "decision_source": "llm",
            "clip_cache_hit": False,
            "trace_id": "trace",
            "timings_ms": {"total": 1.0},
        }
        self.assertEqual(ChatSuccessResponse.model_validate(body).status, "success")
        with self.assertRaises(ValidationError):
            ChatSuccessResponse.model_validate({k: v for k, v in body.items() if k != "trace_id"})


class RetryAndCacheTests(unittest.TestCase):
    def test_retry_call_recovers_from_transient_failure(self):
        operation = Mock(side_effect=[requests.Timeout("slow"), "ok"])
        with patch("src.reliability.time.sleep") as sleep:
            result, attempts = retry_call(
                operation,
                retries=2,
                backoff_s=0.25,
                should_retry=lambda error: isinstance(error, requests.Timeout),
            )
        self.assertEqual((result, attempts), ("ok", 2))
        sleep.assert_called_once_with(0.25)

    def test_retry_call_does_not_retry_permanent_failure(self):
        operation = Mock(side_effect=ValueError("bad input"))
        with self.assertRaises(ValueError):
            retry_call(
                operation,
                retries=3,
                backoff_s=0,
                should_retry=lambda error: isinstance(error, requests.Timeout),
            )
        self.assertEqual(operation.call_count, 1)

    def test_ollama_retries_timeout_then_returns_json(self):
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {"message": {"content": '{"ok": true}'}}
        with patch(
            "src.llm.ollama_client.requests.post",
            side_effect=[requests.Timeout("slow"), response],
        ), patch("src.reliability.time.sleep"):
            result = call_ollama_json(
                "model",
                "system",
                "user",
                timeout_s=1,
                retries=1,
                backoff_s=0,
                trace_id="test-trace",
            )
        self.assertTrue(result.ok)
        self.assertEqual(result.attempts, 2)

    def test_geocode_cache_avoids_duplicate_network_call(self):
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = [
            {
                "lat": "55.7",
                "lon": "13.2",
                "boundingbox": ["55.6", "55.8", "13.1", "13.3"],
            }
        ]
        clear_geocode_cache()
        with patch("src.osm.geocode.requests.get", return_value=response) as get:
            first = geocode_to_bbox("Lund", sleep_s=0, retries=0, trace_id="a")
            second = geocode_to_bbox(" lund ", sleep_s=0, retries=0, trace_id="b")
        self.assertEqual(first, (13.1, 55.6, 13.3, 55.8))
        self.assertEqual(second, first)
        self.assertEqual(get.call_count, 1)

    def test_nominatim_retries_transient_timeout(self):
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = [
            {"lat": "55.7", "lon": "13.2", "boundingbox": ["55.6", "55.8", "13.1", "13.3"]}
        ]
        clear_geocode_cache()
        with patch(
            "src.osm.geocode.requests.get",
            side_effect=[requests.Timeout("slow"), response],
        ) as get, patch("src.reliability.time.sleep"):
            bbox = geocode_to_bbox(
                "Retry City",
                sleep_s=0,
                retries=1,
                backoff_s=0,
                trace_id="geo-retry",
            )
        self.assertEqual(bbox, (13.1, 55.6, 13.3, 55.8))
        self.assertEqual(get.call_count, 2)

    def test_clip_cache_is_reused_and_source_change_invalidates_key(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "country.osm.pbf"
            source.write_bytes(b"source-v1")
            creator = Mock(side_effect=lambda output: output.write_bytes(b"clip"))
            args = dict(
                source_pbf=source,
                bbox=(1, 2, 3, 4),
                strategy="simple",
                cache_dir=root / "cache",
                enabled=True,
                creator=creator,
            )
            first, first_hit = get_or_create_clip(**args)
            second, second_hit = get_or_create_clip(**args)
            source.write_bytes(b"source-v2-is-different")
            third, third_hit = get_or_create_clip(**args)
        self.assertFalse(first_hit)
        self.assertTrue(second_hit)
        self.assertFalse(third_hit)
        self.assertEqual(first, second)
        self.assertNotEqual(first, third)
        self.assertEqual(creator.call_count, 2)


class ErrorPathTests(unittest.TestCase):
    def test_osmium_timeout_is_classified_as_timeout(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.osm.pbf"
            source.write_bytes(b"x")
            with patch("src.osm.extractor.find_osmium_executable", return_value="osmium"), patch(
                "src.osm.extractor.subprocess.run",
                side_effect=subprocess.TimeoutExpired("osmium", 1),
            ):
                with self.assertRaisesRegex(TimeoutError, "timed out after 1s"):
                    osmium_extract_bbox(
                        source,
                        Path(directory) / "out.osm.pbf",
                        (1, 2, 3, 4),
                        timeout_s=1,
                    )

    def test_pipeline_failure_has_trace_error_code_and_timing(self):
        from src import pipeline

        retriever = Mock()
        retriever.retrieve.side_effect = RuntimeError("index unavailable")
        with patch.object(pipeline, "_retriever", return_value=retriever):
            result = pipeline.run_query("cafes in Lund", trace_id="trace-123")
        self.assertFalse(result["success"])
        self.assertEqual(result["trace_id"], "trace-123")
        self.assertEqual(result["error_code"], "RAG_RETRIEVAL_FAILED")
        self.assertIn("rag_retrieval", result["timings_ms"])
        self.assertIn("total", result["timings_ms"])

    def test_pipeline_success_reports_stage_timings_and_decision_source(self):
        from src import pipeline

        retriever = Mock()
        retriever.retrieve.return_value = [chunk()]
        decision = {
            "place": "Lund",
            "tag": {"key": "amenity", "value": "cafe"},
            "confidence": 0.9,
            "explanation": "Supported by evidence.",
        }
        extracted = {
            "count": 2,
            "counts_by_type": {"node": 1, "way": 1, "relation": 0},
            "geojson_path": "x.geojson",
            "geojson_filename": "x.geojson",
            "clip_cache_hit": True,
        }
        with patch.object(pipeline, "_retriever", return_value=retriever), patch.object(
            pipeline,
            "llm_parse_query",
            return_value={"ok": True, "data": decision, "raw": "{}", "attempts": 1},
        ), patch.object(pipeline, "geocode_to_bbox", return_value=(1, 2, 3, 4)), patch.object(
            pipeline, "_extract", return_value=extracted
        ):
            result = pipeline.run_query("cafes in Lund", trace_id="trace-ok")
        self.assertTrue(result["success"])
        self.assertEqual(result["decision_source"], "llm")
        self.assertEqual(result["trace_id"], "trace-ok")
        self.assertTrue(result["clip_cache_hit"])
        for stage in ("rag_retrieval", "llm_parse", "decision", "geocode", "osm_extract", "total"):
            self.assertIn(stage, result["timings_ms"])

    def test_unexpected_llm_parser_error_uses_grounded_fallback(self):
        from src import pipeline

        retriever = Mock()
        retriever.retrieve.return_value = [chunk()]
        extracted = {
            "count": 1,
            "counts_by_type": {"node": 1, "way": 0, "relation": 0},
            "geojson_path": "x.geojson",
            "geojson_filename": "x.geojson",
            "clip_cache_hit": False,
        }
        with patch.object(pipeline, "_retriever", return_value=retriever), patch.object(
            pipeline, "llm_parse_query", side_effect=RuntimeError("parser crashed")
        ), patch.object(pipeline, "geocode_to_bbox", return_value=(1, 2, 3, 4)), patch.object(
            pipeline, "_extract", return_value=extracted
        ):
            result = pipeline.run_query("cafes in Lund", trace_id="trace-fallback")
        self.assertTrue(result["success"])
        self.assertEqual(result["chosen_tag"], "amenity=cafe")
        self.assertEqual(result["decision_source"], "weighted_fallback")
        self.assertEqual(result["llm_error_code"], "LLM_PARSER_FAILED")


class GeometryFixtureTests(unittest.TestCase):
    OSM_XML = """<?xml version="1.0" encoding="UTF-8"?>
<osm version="0.6" generator="geoai-test">
  <node id="1" lat="55.7000" lon="13.2000"><tag k="tourism" v="attraction"/></node>
  <node id="2" lat="55.7000" lon="13.2100"/><node id="3" lat="55.7000" lon="13.2200"/>
  <node id="4" lat="55.7100" lon="13.2200"/><node id="5" lat="55.7100" lon="13.2100"/>
  <way id="10"><nd ref="2"/><nd ref="3"/><nd ref="4"/><nd ref="5"/><nd ref="2"/>
    <tag k="tourism" v="attraction"/><tag k="area" v="yes"/></way>
  <node id="6" lat="55.7200" lon="13.2100"/><node id="7" lat="55.7200" lon="13.2200"/>
  <node id="8" lat="55.7300" lon="13.2200"/><node id="9" lat="55.7300" lon="13.2100"/>
  <way id="20"><nd ref="6"/><nd ref="7"/><nd ref="8"/><nd ref="9"/><nd ref="6"/></way>
  <relation id="30"><member type="way" ref="20" role="outer"/>
    <tag k="type" v="multipolygon"/><tag k="tourism" v="attraction"/></relation>
</osm>"""

    def test_node_way_and_relation_are_emitted_as_geojson(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "fixture.osm"
            output = root / "fixture.geojson"
            source.write_text(self.OSM_XML, encoding="utf-8")
            features = extract_features_to_geojson(
                source, "tourism", "attraction", output
            )
            document = json.loads(output.read_text(encoding="utf-8"))
        self.assertEqual({item.osm_type for item in features}, {"node", "way", "relation"})
        self.assertEqual(document["type"], "FeatureCollection")
        self.assertEqual(document["metadata"]["extraction"]["geometry_failures"], 0)
        self.assertEqual(document["metadata"]["extraction"]["skipped_non_area_relations"], 0)


class FlaskP2ContractTests(unittest.TestCase):
    def test_invalid_request_returns_400_with_trace_and_details(self):
        import app_min

        with app_min.app.test_client() as client:
            response = client.post(
                "/chat",
                json={"query": " ", "unexpected": True},
                headers={"X-Trace-ID": "client-trace"},
            )
        body = response.get_json()
        self.assertEqual(response.status_code, 400)
        self.assertEqual(body["error_code"], "INVALID_REQUEST")
        self.assertEqual(body["trace_id"], "client-trace")
        self.assertTrue(body["details"])

    def test_pipeline_error_code_maps_to_stable_http_status(self):
        import app_min

        result = {
            "success": False,
            "error_code": "PLACE_NOT_FOUND",
            "error": "place not found",
            "trace_id": "trace-geo",
            "timings_ms": {"total": 1.2},
        }
        with patch.object(app_min, "run_query", return_value=result):
            with app_min.app.test_client() as client:
                response = client.post("/chat", json={"query": "cafes in nowhere"})
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.get_json()["error_code"], "PLACE_NOT_FOUND")


if __name__ == "__main__":
    unittest.main()
