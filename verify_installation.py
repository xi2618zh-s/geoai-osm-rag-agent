"""Read-only installation checks, plus optional network and E2E probes."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

import osmium

from src.config import FAISS_INDEX, FAISS_META, OLLAMA_MODEL, OSM_PBF, OUTPUT_DIR
from src.llm.ollama_client import (
    call_ollama_json,
    check_ollama_available,
    list_available_models,
)
from src.osm.geocode import geocode_to_bbox
from src.osm.extractor import find_osmium_executable
from src.path_compat import native_readable_path
from src.pipeline import run_query, run_query_without_llm
from src.rag.hybrid_retriever import build_retriever


def report(name: str, ok: bool, detail="") -> bool:
    marker = "PASS" if ok else "FAIL"
    print(f"[{marker}] {name}{': ' + str(detail) if detail else ''}")
    return ok


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--network", action="store_true")
    parser.add_argument("--ollama", action="store_true")
    parser.add_argument("--e2e-simple", action="store_true")
    parser.add_argument("--e2e-query", default="")
    args = parser.parse_args()

    results = [
        report("country PBF", Path(OSM_PBF).is_file(), OSM_PBF),
        report("FAISS index", Path(FAISS_INDEX).is_file(), FAISS_INDEX),
        report("FAISS metadata", Path(FAISS_META).is_file(), FAISS_META),
        report("osmium CLI", find_osmium_executable() is not None),
    ]

    try:
        hits = build_retriever(local_files_only=not args.network).retrieve(
            "Find all cafes", k=3
        )
        results.append(report("FAISS retrieval", bool(hits), [(h.key, h.value) for h in hits]))
    except Exception as error:
        results.append(report("FAISS retrieval", False, error))

    sample_pbf = Path(OUTPUT_DIR) / "sub_lund.osm.pbf"
    if sample_pbf.is_file():
        try:
            with native_readable_path(sample_pbf) as readable:
                first = next(iter(osmium.FileProcessor(str(readable))))
            results.append(report("pyosmium Unicode-path compatibility", first is not None))
        except Exception as error:
            results.append(report("pyosmium Unicode-path compatibility", False, error))

    if args.network:
        try:
            results.append(report("Nominatim", bool(geocode_to_bbox("Lund", sleep_s=0))))
        except Exception as error:
            results.append(report("Nominatim", False, error))

    if args.ollama:
        available = check_ollama_available()
        models = list_available_models() if available else []
        model_ok = any(
            name == OLLAMA_MODEL or name.startswith(f"{OLLAMA_MODEL}:") for name in models
        )
        results.append(report("Ollama service", available, models))
        results.append(report(f"Ollama model {OLLAMA_MODEL}", model_ok))
        if available and model_ok:
            probe = call_ollama_json(
                model=OLLAMA_MODEL,
                system="Return only valid JSON.",
                user='Return {"ok": true}.',
                timeout_s=180,
            )
            results.append(report("Ollama inference", probe.ok, probe.raw[:500]))

    if args.e2e_simple:
        result = run_query_without_llm("cafes", "Lund", "amenity", "cafe")
        results.append(report("simple E2E", result.get("success", False), result))

    if args.e2e_query:
        result = run_query(args.e2e_query)
        results.append(report("natural-language E2E", result.get("success", False), result))

    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
