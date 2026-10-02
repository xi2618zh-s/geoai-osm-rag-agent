"""Core RAG -> LLM -> geocode -> OSM -> GeoJSON pipeline."""

from __future__ import annotations

from collections import Counter
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, Optional
import re
import tempfile
import unicodedata
import uuid

import requests

from src.config import (
    DEFAULT_PLACE,
    OLLAMA_MODEL,
    OSM_CLIP_CACHE_DIR,
    OSM_CLIP_CACHE_ENABLED,
    OSM_PBF,
    OSMIUM_EXTRACT_STRATEGY,
    OUTPUT_DIR,
    RAG_TOP_K,
)
from src.observability import QueryTrace, log_event
from src.osm.extractor import extract_features_to_geojson, osmium_extract_bbox
from src.osm.geocode import geocode_to_bbox
from src.query.llm_parser import llm_parse_query, validate_llm_response
from src.rag.hybrid_retriever import build_retriever
from src.rag.retriever import pick_tag_from_chunks
from src.reliability import get_or_create_clip


ERROR_INVALID_REQUEST = "INVALID_REQUEST"
ERROR_RAG_RETRIEVAL = "RAG_RETRIEVAL_FAILED"
ERROR_TAG_SELECTION = "TAG_SELECTION_FAILED"
ERROR_GEOCODING = "GEOCODING_FAILED"
ERROR_EXTRACTION = "OSM_EXTRACTION_FAILED"


def _geocode_error_code(error: Exception) -> str:
    if isinstance(error, requests.Timeout):
        return "NOMINATIM_TIMEOUT"
    if isinstance(error, requests.ConnectionError):
        return "NOMINATIM_UNAVAILABLE"
    if isinstance(error, ValueError):
        return "PLACE_NOT_FOUND"
    return ERROR_GEOCODING


def _extraction_error_code(error: Exception) -> str:
    if isinstance(error, TimeoutError):
        return "OSMIUM_TIMEOUT"
    if isinstance(error, FileNotFoundError):
        return "OSM_DATA_MISSING"
    return ERROR_EXTRACTION


def safe_slug(text: str) -> str:
    text = unicodedata.normalize("NFKD", str(text).lower())
    text = text.encode("ascii", "ignore").decode("ascii")
    text = re.sub(r"[^a-z0-9]+", "_", text).strip("_")
    return text or "unknown"


def simple_place_heuristic(query: str) -> Optional[str]:
    """Extract a terminal place phrase when the LLM is unavailable."""

    query = query.strip()
    patterns = [
        r"\b(?:in|at|near|around|within|from)\s+([\w\u00c0-\u024f .'-]{2,80})[?.!]*$",
        r"(?:在|位于|靠近)\s*([\w\u3400-\u9fff· -]{2,40}?)(?:市|区|县|省)?[，。?!]*$",
    ]
    for pattern in patterns:
        match = re.search(pattern, query, re.IGNORECASE)
        if match:
            place = match.group(1).strip(" .,!?")
            return place or None
    return None


@lru_cache(maxsize=1)
def _retriever():
    return build_retriever()


def _new_geojson_path(query: str, place: str, key: str, value: str) -> Path:
    label = safe_slug(f"{place}_{key}_{value}_{query}")[:80]
    return OUTPUT_DIR / f"result_{label}_{uuid.uuid4().hex[:10]}.geojson"


def _extract(query: str, place: str, key: str, value: str, bbox) -> Dict[str, Any]:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    geojson_path = _new_geojson_path(query, place, key, value)
    clip_cache_hit = False

    if OSM_CLIP_CACHE_ENABLED:
        sub_pbf, clip_cache_hit = get_or_create_clip(
            Path(OSM_PBF),
            bbox,
            strategy=OSMIUM_EXTRACT_STRATEGY,
            cache_dir=OSM_CLIP_CACHE_DIR,
            enabled=True,
            creator=lambda output: osmium_extract_bbox(Path(OSM_PBF), output, bbox),
        )
        features = extract_features_to_geojson(sub_pbf, key, value, geojson_path)
    else:
        with tempfile.TemporaryDirectory(prefix="geoai_clip_", dir=OUTPUT_DIR) as clip_dir:
            sub_pbf = Path(clip_dir) / "subset.osm.pbf"
            osmium_extract_bbox(Path(OSM_PBF), sub_pbf, bbox)
            features = extract_features_to_geojson(sub_pbf, key, value, geojson_path)

    counts = Counter(item.osm_type for item in features)
    return {
        "count": len(features),
        "counts_by_type": {
            "node": counts.get("node", 0),
            "way": counts.get("way", 0),
            "relation": counts.get("relation", 0),
        },
        "geojson_path": str(geojson_path),
        "geojson_filename": geojson_path.name,
        "clip_cache_hit": clip_cache_hit,
    }


def _base(trace: QueryTrace, query: str) -> dict[str, Any]:
    return {"query": query, "trace_id": trace.trace_id}


def _failure(
    trace: QueryTrace,
    query: str,
    code: str,
    message: str,
    **fields,
) -> Dict[str, Any]:
    timings = trace.snapshot()
    log_event(
        "query_failed",
        trace_id=trace.trace_id,
        error_code=code,
        total_ms=timings["total"],
    )
    return {
        "success": False,
        **_base(trace, query),
        **fields,
        "error_code": code,
        "error": message,
        "timings_ms": timings,
    }


def run_query(
    query: str,
    model: str = OLLAMA_MODEL,
    *,
    trace_id: str | None = None,
) -> Dict[str, Any]:
    trace = QueryTrace(trace_id)
    query = str(query).strip()
    log_event(
        "query_started",
        trace_id=trace.trace_id,
        mode="natural_language",
        query_length=len(query),
        model=model,
    )
    if not query:
        return _failure(trace, query, ERROR_INVALID_REQUEST, "Query is empty")

    try:
        with trace.stage("rag_retrieval"):
            chunks = _retriever().retrieve(query, k=RAG_TOP_K)
    except Exception as error:
        return _failure(
            trace, query, ERROR_RAG_RETRIEVAL, f"RAG retrieval failed: {error}"
        )

    try:
        with trace.stage("llm_parse"):
            llm_res = llm_parse_query(
                query=query,
                chunks=chunks,
                model=model,
                trace_id=trace.trace_id,
            )
    except Exception as error:
        llm_res = {
            "ok": False,
            "data": {},
            "raw": f"Unexpected LLM parser error: {error}",
            "error_code": "LLM_PARSER_FAILED",
            "attempts": 1,
        }
    llm_data = llm_res.get("data", {})
    llm_ok = bool(llm_res.get("ok")) and validate_llm_response(llm_data, chunks)

    with trace.stage("decision"):
        place = llm_data.get("place") if llm_ok else None
        if not place:
            place = simple_place_heuristic(query) or DEFAULT_PLACE

        key = value = None
        if llm_ok:
            key = llm_data["tag"]["key"].strip()
            value = llm_data["tag"]["value"].strip()
        if not key or not value:
            try:
                key, value = pick_tag_from_chunks(chunks)
            except ValueError as error:
                return _failure(
                    trace,
                    query,
                    ERROR_TAG_SELECTION,
                    f"Could not determine OSM tag: {error}",
                    place=place,
                )

    try:
        with trace.stage("geocode"):
            bbox = geocode_to_bbox(place, trace_id=trace.trace_id)
    except Exception as error:
        return _failure(
            trace,
            query,
            _geocode_error_code(error),
            f"Geocoding failed for '{place}': {error}",
            place=place,
            chosen_tag=f"{key}={value}",
        )

    try:
        with trace.stage("osm_extract"):
            extracted = _extract(query, place, key, value, bbox)
    except Exception as error:
        return _failure(
            trace,
            query,
            _extraction_error_code(error),
            f"OSM/GeoJSON extraction failed: {error}",
            place=place,
            chosen_tag=f"{key}={value}",
            bbox=bbox,
        )

    evidence = [
        {
            "score": round(chunk.score, 4),
            "key": chunk.key,
            "value": chunk.value,
            "url": chunk.url,
            "snippet": chunk.page_content[:220].replace("\n", " "),
            "retrieval_method": chunk.retrieval_method,
            "dense_score": (
                round(chunk.dense_score, 4)
                if chunk.dense_score is not None
                else None
            ),
            "lexical_score": (
                round(chunk.lexical_score, 4)
                if chunk.lexical_score is not None
                else None
            ),
        }
        for chunk in chunks
    ]
    timings = trace.snapshot()
    result = {
        "success": True,
        **_base(trace, query),
        "place": place,
        "chosen_tag": f"{key}={value}",
        "bbox": bbox,
        **extracted,
        "evidence": evidence,
        "llm_ok": llm_ok,
        "llm_raw": llm_res.get("raw", ""),
        "llm_error_code": None if llm_ok else llm_res.get("error_code"),
        "llm_attempts": llm_res.get("attempts", 1),
        "llm_explanation": llm_data.get("explanation", "") if llm_ok else "",
        "llm_confidence": llm_data.get("confidence", 0) if llm_ok else 0,
        "decision_source": "llm" if llm_ok else "weighted_fallback",
        "timings_ms": timings,
    }
    log_event(
        "query_completed",
        trace_id=trace.trace_id,
        total_ms=timings["total"],
        count=result["count"],
        decision_source=result["decision_source"],
        clip_cache_hit=result["clip_cache_hit"],
    )
    return result


def run_query_without_llm(
    query: str,
    place: str,
    key: str,
    value: str,
    *,
    trace_id: str | None = None,
) -> Dict[str, Any]:
    trace = QueryTrace(trace_id)
    query = str(query or "").strip()
    place = str(place or "").strip()
    key = str(key or "").strip()
    value = str(value or "").strip()
    log_event(
        "query_started",
        trace_id=trace.trace_id,
        mode="explicit_tag",
        query_length=len(query),
    )
    if not place or not key or not value:
        return _failure(
            trace,
            query,
            ERROR_INVALID_REQUEST,
            "place, key, and value must all be non-empty strings",
        )

    try:
        with trace.stage("geocode"):
            bbox = geocode_to_bbox(place, trace_id=trace.trace_id)
    except Exception as error:
        return _failure(
            trace,
            query,
            _geocode_error_code(error),
            f"Geocoding failed for '{place}': {error}",
            place=place,
            chosen_tag=f"{key}={value}",
        )
    try:
        with trace.stage("osm_extract"):
            extracted = _extract(query, place, key, value, bbox)
    except Exception as error:
        return _failure(
            trace,
            query,
            _extraction_error_code(error),
            f"OSM/GeoJSON extraction failed: {error}",
            place=place,
            chosen_tag=f"{key}={value}",
            bbox=bbox,
        )

    timings = trace.snapshot()
    result = {
        "success": True,
        **_base(trace, query),
        "place": place,
        "chosen_tag": f"{key}={value}",
        "bbox": bbox,
        **extracted,
        "llm_ok": False,
        "decision_source": "explicit_tag",
        "timings_ms": timings,
    }
    log_event(
        "query_completed",
        trace_id=trace.trace_id,
        total_ms=timings["total"],
        count=result["count"],
        decision_source="explicit_tag",
        clip_cache_hit=result["clip_cache_hit"],
    )
    return result
