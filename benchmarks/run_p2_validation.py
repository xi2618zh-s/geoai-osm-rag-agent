"""Run two identical real queries to validate P2 tracing and cache behavior."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import sys

from src.config import OLLAMA_MODEL
from src.osm.geocode import clear_geocode_cache
from src.pipeline import run_query


ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "benchmarks" / "results"


def compact(result: dict) -> dict:
    keys = (
        "success",
        "query",
        "place",
        "chosen_tag",
        "count",
        "counts_by_type",
        "llm_ok",
        "llm_error_code",
        "llm_attempts",
        "decision_source",
        "clip_cache_hit",
        "trace_id",
        "timings_ms",
        "error_code",
        "error",
    )
    return {key: result.get(key) for key in keys if key in result}


def main() -> int:
    query = "Find all cafes in Lund"
    clear_geocode_cache()
    first = compact(run_query(query, model=OLLAMA_MODEL))
    second = compact(run_query(query, model=OLLAMA_MODEL))
    checks = {
        "both_success": bool(first.get("success") and second.get("success")),
        "both_llm_grounded": bool(first.get("llm_ok") and second.get("llm_ok")),
        "both_tags_correct": first.get("chosen_tag") == second.get("chosen_tag") == "amenity=cafe",
        "trace_ids_unique": bool(first.get("trace_id") and second.get("trace_id") and first["trace_id"] != second["trace_id"]),
        "all_stage_timings_present": all(
            stage in run.get("timings_ms", {})
            for run in (first, second)
            for stage in ("rag_retrieval", "llm_parse", "decision", "geocode", "osm_extract", "total")
        ),
        "second_clip_cache_hit": second.get("clip_cache_hit") is True,
    }
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "model": OLLAMA_MODEL,
        "query": query,
        "checks": checks,
        "first_run": first,
        "second_run": second,
    }
    RESULTS.mkdir(parents=True, exist_ok=True)
    json_path = RESULTS / "p2_runtime_validation.json"
    md_path = RESULTS / "p2_runtime_validation.md"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = [
        "# P2 Runtime Validation",
        "",
        f"Generated: {report['generated_at']}",
        f"Model: `{OLLAMA_MODEL}`",
        f"Query: `{query}`",
        "",
        "## Checks",
        "",
        "| Check | Result |",
        "|---|---:|",
    ]
    lines.extend(f"| {key} | {'PASS' if value else 'FAIL'} |" for key, value in checks.items())
    lines.extend(["", "## Runs", "", "```json", json.dumps({"first_run": first, "second_run": second}, ensure_ascii=False, indent=2), "```", ""])
    md_path.write_text("\n".join(lines), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if all(checks.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
