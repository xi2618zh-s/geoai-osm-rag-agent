"""Flask API and static UI for the GeoAI OSM RAG demo."""

from __future__ import annotations

from pathlib import Path

from flask import Flask, jsonify, request, send_file, send_from_directory
from pydantic import ValidationError

from src.agent.contracts import (
    WorkflowErrorResponse,
    WorkflowRequest,
    WorkflowSuccessResponse,
)
from src.agent.workflow import available_workflow_tools, run_agent_workflow
from src.config import (
    FAISS_INDEX,
    FAISS_META,
    FLASK_DEBUG,
    FLASK_HOST,
    FLASK_PORT,
    OLLAMA_MODEL,
    OSM_CLIP_CACHE_ENABLED,
    OSM_PBF,
    OUTPUT_DIR,
)
from src.contracts import (
    ChatRequest,
    ChatSuccessResponse,
    ErrorResponse,
    SimpleChatRequest,
    SimpleChatSuccessResponse,
)
from src.llm.ollama_client import check_ollama_available, list_available_models
from src.observability import log_event, new_trace_id
from src.osm.extractor import find_osmium_executable
from src.pipeline import run_query, run_query_without_llm


app = Flask(__name__)


def _validation_error(trace_id: str, error: ValidationError):
    body = ErrorResponse(
        error_code="INVALID_REQUEST",
        message="Request body does not match the API contract",
        trace_id=trace_id,
        details=error.errors(include_url=False),
    )
    return jsonify(body.model_dump(exclude_none=True)), 400


def _pipeline_status(error_code: str | None) -> int:
    if error_code == "INVALID_REQUEST":
        return 400
    if error_code in {"GEOCODING_FAILED", "PLACE_NOT_FOUND", "TAG_SELECTION_FAILED"}:
        return 422
    if error_code in {"NOMINATIM_TIMEOUT", "OSMIUM_TIMEOUT"}:
        return 504
    return 502


def _workflow_status(error_code: str | None) -> int:
    if error_code in {"INVALID_REQUEST", "PLAN_SCHEMA_INVALID"}:
        return 400
    if error_code and error_code.startswith("PLAN_"):
        return 422
    if error_code and "TIMEOUT" in error_code:
        return 504
    return 502


@app.get("/")
def home():
    return jsonify(
        {
            "service": "GeoAI OSM RAG Demo",
            "endpoints": {
                "POST /chat": "Natural-language query",
                "POST /chat_simple": "Explicit place/key/value query",
                "POST /workflow": "Constrained multi-tag union/intersection workflow",
                "GET /output/<filename>": "Generated GeoJSON",
                "GET /ui": "Leaflet UI",
                "GET /status": "Dependency readiness",
            },
        }
    )


@app.get("/status")
def status():
    ollama_ok = check_ollama_available()
    models = list_available_models() if ollama_ok else []
    checks = {
        "osm_pbf": Path(OSM_PBF).is_file(),
        "faiss_index": Path(FAISS_INDEX).is_file(),
        "faiss_metadata": Path(FAISS_META).is_file(),
        "osmium_cli": find_osmium_executable() is not None,
    }
    return jsonify(
        {
            "status": "running",
            "ready_without_llm": all(checks.values()),
            "checks": checks,
            "ollama_available": ollama_ok,
            "available_models": models,
            "default_model": OLLAMA_MODEL,
            "default_model_available": any(
                name == OLLAMA_MODEL or name.startswith(f"{OLLAMA_MODEL}:")
                for name in models
            ),
            "clip_cache_enabled": OSM_CLIP_CACHE_ENABLED,
            "workflow_tools": [item["name"] for item in available_workflow_tools()],
        }
    )


@app.post("/chat")
def chat():
    trace_id = request.headers.get("X-Trace-ID") or new_trace_id()
    try:
        payload = ChatRequest.model_validate(request.get_json(silent=True) or {}, strict=True)
    except ValidationError as error:
        log_event("request_rejected", trace_id=trace_id, endpoint="/chat")
        return _validation_error(trace_id, error)

    try:
        result = run_query(
            query=payload.query,
            model=payload.model,
            trace_id=trace_id,
        )
    except Exception as error:
        log_event(
            "request_crashed",
            trace_id=trace_id,
            endpoint="/chat",
            exception_type=type(error).__name__,
        )
        body = ErrorResponse(
            error_code="INTERNAL_ERROR",
            message="Unexpected server error",
            trace_id=trace_id,
        )
        return jsonify(body.model_dump(exclude_none=True)), 500

    if not result.get("success"):
        body = ErrorResponse(
            error_code=result.get("error_code", "PIPELINE_FAILED"),
            message=result.get("error", "Unknown error"),
            query=payload.query,
            trace_id=result.get("trace_id", trace_id),
            timings_ms=result.get("timings_ms", {}),
        )
        return jsonify(body.model_dump(exclude_none=True)), _pipeline_status(
            result.get("error_code")
        )

    message = (
        f"Place: {result['place']}\n"
        f"Chosen tag: {result['chosen_tag']}\n"
        f"Extracted features: {result['count']}\n"
        f"LLM used: {result['llm_ok']}"
    )
    if result.get("llm_explanation"):
        message += f"\nLLM explanation: {result['llm_explanation']}"
    body = ChatSuccessResponse(
        message=message,
        query=payload.query,
        place=result["place"],
        chosen_tag=result["chosen_tag"],
        count=result["count"],
        geojson_url=f"/output/{result['geojson_filename']}",
        evidence=result.get("evidence", []),
        llm_ok=result["llm_ok"],
        llm_confidence=result.get("llm_confidence", 0),
        counts_by_type=result.get("counts_by_type", {}),
        decision_source=result.get("decision_source", "unknown"),
        clip_cache_hit=result.get("clip_cache_hit", False),
        trace_id=result.get("trace_id", trace_id),
        timings_ms=result.get("timings_ms", {}),
    )
    return jsonify(body.model_dump())


@app.post("/chat_simple")
def chat_simple():
    trace_id = request.headers.get("X-Trace-ID") or new_trace_id()
    try:
        payload = SimpleChatRequest.model_validate(
            request.get_json(silent=True) or {}, strict=True
        )
    except ValidationError as error:
        log_event("request_rejected", trace_id=trace_id, endpoint="/chat_simple")
        return _validation_error(trace_id, error)

    try:
        result = run_query_without_llm(
            query=payload.query,
            place=payload.place,
            key=payload.key,
            value=payload.value,
            trace_id=trace_id,
        )
    except Exception as error:
        log_event(
            "request_crashed",
            trace_id=trace_id,
            endpoint="/chat_simple",
            exception_type=type(error).__name__,
        )
        body = ErrorResponse(
            error_code="INTERNAL_ERROR",
            message="Unexpected server error",
            trace_id=trace_id,
        )
        return jsonify(body.model_dump(exclude_none=True)), 500

    if not result.get("success"):
        body = ErrorResponse(
            error_code=result.get("error_code", "PIPELINE_FAILED"),
            message=result.get("error", "Unknown error"),
            trace_id=result.get("trace_id", trace_id),
            timings_ms=result.get("timings_ms", {}),
        )
        return jsonify(body.model_dump(exclude_none=True)), _pipeline_status(
            result.get("error_code")
        )

    body = SimpleChatSuccessResponse(
        message=(
            f"Place: {payload.place}\nTag: {payload.key}={payload.value}\n"
            f"Count: {result['count']}"
        ),
        geojson_url=f"/output/{result['geojson_filename']}",
        count=result["count"],
        counts_by_type=result.get("counts_by_type", {}),
        decision_source=result.get("decision_source", "explicit_tag"),
        clip_cache_hit=result.get("clip_cache_hit", False),
        trace_id=result.get("trace_id", trace_id),
        timings_ms=result.get("timings_ms", {}),
    )
    return jsonify(body.model_dump())


@app.post("/workflow")
def workflow():
    trace_id = request.headers.get("X-Trace-ID") or new_trace_id()
    try:
        payload = WorkflowRequest.model_validate(
            request.get_json(silent=True) or {}, strict=True
        )
    except ValidationError as error:
        log_event("request_rejected", trace_id=trace_id, endpoint="/workflow")
        return _validation_error(trace_id, error)

    try:
        if payload.mode == "explicit_plan":
            result = run_agent_workflow(
                payload.query,
                place=payload.place,
                operation=payload.operation,
                filters=payload.filters,
                trace_id=trace_id,
            )
        else:
            result = run_agent_workflow(payload.query, trace_id=trace_id)
    except Exception as error:
        log_event(
            "request_crashed",
            trace_id=trace_id,
            endpoint="/workflow",
            exception_type=type(error).__name__,
        )
        body = WorkflowErrorResponse(
            error_code="INTERNAL_ERROR",
            message="Unexpected workflow server error",
            workflow_id="unavailable",
            trace_id=trace_id,
            query=payload.query,
        )
        return jsonify(body.model_dump(exclude_none=True)), 500

    if not result.get("success"):
        body = WorkflowErrorResponse(
            error_code=result.get("error_code", "WORKFLOW_FAILED"),
            message=result.get("error", "Unknown workflow error"),
            workflow_id=result.get("workflow_id", "unavailable"),
            trace_id=result.get("trace_id", trace_id),
            query=payload.query,
            plan=result.get("plan"),
            step_records=result.get("step_records", []),
            timings_ms=result.get("timings_ms", {}),
        )
        return jsonify(body.model_dump(exclude_none=True)), _workflow_status(
            result.get("error_code")
        )

    body = WorkflowSuccessResponse(
        workflow_id=result["workflow_id"],
        trace_id=result["trace_id"],
        query=result["query"],
        place=result["place"],
        operation=result["operation"],
        chosen_tags=result["chosen_tags"],
        count=result["count"],
        counts_by_type=result["counts_by_type"],
        matched_counts_by_tag=result["matched_counts_by_tag"],
        geojson_url=f"/output/{result['geojson_filename']}",
        plan=result["plan"],
        step_records=result["step_records"],
        recovery_count=result["recovery_count"],
        clip_cache_hit=result["clip_cache_hit"],
        timings_ms=result["timings_ms"],
    )
    return jsonify(body.model_dump())


@app.get("/output/<path:filename>")
def output_files(filename):
    return send_from_directory(str(OUTPUT_DIR), filename)


@app.get("/ui")
def ui():
    return send_file(Path(__file__).with_name("chat.html"))


@app.errorhandler(404)
def not_found(_error):
    return jsonify(
        {"status": "error", "error_code": "NOT_FOUND", "message": "Not found"}
    ), 404


if __name__ == "__main__":
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    app.run(host=FLASK_HOST, port=FLASK_PORT, debug=FLASK_DEBUG)
