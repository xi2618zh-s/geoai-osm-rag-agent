"""Publish an immutable 205-tag M03 Phase 2 v3 catalog and bilingual documents."""

from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import shutil
import sys
import uuid

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.knowledge import validate_catalog_payload
from src.knowledge.build import build_index_documents_v3, publish_directory_atomically
from src.knowledge.catalog_builder import load_raw_sources
from src.knowledge.manifest import validate_manifest_files
from src.knowledge.phase2_catalog_builder import build_phase2_catalog, parse_new_curation_rows
from src.knowledge.serialization import canonical_json_bytes, canonical_jsonl_bytes, sha256_hex
from src.knowledge.tokenizer import CJK_TOKENIZER_VERSION

PHASE1_CATALOG_SHA256 = "c624eef08aacbc7d1c5b87e357884039399680b23ba89b8833b0c360191e29d0"


def checked_file(path: Path, sidecar: Path) -> tuple[bytes, str]:
    data = path.read_bytes()
    digest = sha256_hex(data)
    if digest != sidecar.read_text("utf-8").split()[0]:
        raise ValueError("OSM_KB_MANIFEST_HASH_MISMATCH: " + path.name)
    return data, digest


def repo_record(path: Path, role: str) -> dict:
    relative = path.resolve().relative_to(ROOT.resolve()).as_posix()
    content = path.read_bytes()
    return {"path": relative, "role": role, "sha256": sha256_hex(content), "size_bytes": len(content)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidates", type=Path, required=True)
    parser.add_argument("--candidate-sha256", type=Path, required=True)
    parser.add_argument("--source-map", type=Path, required=True)
    parser.add_argument("--source-map-sha256", type=Path, required=True)
    parser.add_argument("--raw-dir", type=Path, required=True)
    parser.add_argument("--phase1-catalog", type=Path, required=True)
    parser.add_argument("--curation", type=Path, required=True)
    parser.add_argument("--curation-sha256", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--catalog-version", required=True)
    parser.add_argument("--build-timestamp", required=True)
    args = parser.parse_args()
    output = args.output_dir.resolve()
    if output.exists():
        raise SystemExit("OSM_KB_RELEASE_EXISTS: refusing to overwrite Phase 2 catalog")
    candidate_bytes, candidate_hash = checked_file(args.candidates, args.candidate_sha256)
    source_map_bytes, source_map_hash = checked_file(args.source_map, args.source_map_sha256)
    curation_bytes, curation_hash = checked_file(args.curation, args.curation_sha256)
    candidates = json.loads(candidate_bytes)
    source_map = json.loads(source_map_bytes)
    raw_dir = args.raw_dir.resolve()
    validate_manifest_files(raw_dir / "manifest.json")
    raw_manifest = json.loads((raw_dir / "manifest.json").read_text("utf-8"))
    if (source_map["candidate_manifest_sha256"] != candidate_hash
        or raw_manifest["candidate_manifest_sha256"] != candidate_hash
        or raw_manifest["source_map_sha256"] != source_map_hash
        or raw_manifest["tag_mapping_count"] != 205
        or raw_manifest["unique_page_count"] != 203):
        raise ValueError("OSM_KB_PHASE2_SOURCE_MANIFEST_MISMATCH")
    sources = load_raw_sources(raw_dir / "raw_sources.jsonl")
    old_bytes = args.phase1_catalog.read_bytes()
    if sha256_hex(old_bytes) != PHASE1_CATALOG_SHA256:
        raise ValueError("OSM_KB_PHASE1_CATALOG_HASH_MISMATCH")
    phase1 = validate_catalog_payload(json.loads(old_bytes))
    catalog = build_phase2_catalog(
        candidates, sources, phase1, parse_new_curation_rows(curation_bytes.decode("utf-8")),
        candidate_sha256=candidate_hash, catalog_version=args.catalog_version,
    )
    documents = build_index_documents_v3(catalog)
    if len(documents) != 410:
        raise ValueError("OSM_KB_PHASE2_DOCUMENT_COUNT_INVALID")
    catalog_bytes = canonical_json_bytes(catalog)
    document_bytes = canonical_jsonl_bytes(documents)
    attribution = (raw_dir / "ATTRIBUTION.md").read_bytes()
    file_contents = {
        "catalog.json": catalog_bytes,
        "index_documents.jsonl": document_bytes,
        "ATTRIBUTION.md": attribution,
    }
    manifest = {
        "schema_version": "2.0", "release_id": output.name,
        "release_phase": "m03_phase2", "m03_module_status": "IN_PROGRESS",
        "phase_status": "DONE_PENDING_INDEX_AND_EVALUATION",
        "catalog_version": catalog.catalog_version,
        "build_timestamp": args.build_timestamp,
        "candidate_manifest_sha256": candidate_hash,
        "source_map_sha256": source_map_hash,
        "raw_manifest_sha256": sha256_hex((raw_dir / "manifest.json").read_bytes()),
        "entry_count": len(catalog.entries),
        "unique_canonical_tag_count": len({item.canonical_tag for item in catalog.entries}),
        "source_page_count": len({item.source_page_id for item in sources}),
        "index_document_count": len(documents), "documents_per_tag": 2,
        "languages": ["en", "zh"],
        "category_counts": dict(sorted(Counter(item.primary_category.value for item in catalog.entries).items())),
        "role_counts": dict(sorted(Counter(item.tag_role.value for item in catalog.entries).items())),
        "status_counts": dict(sorted(Counter(item.status.value for item in catalog.entries).items())),
        "catalog_schema_version": "3.0", "raw_source_schema_version": "2.0",
        "index_document_schema_version": "2.0", "tokenizer_version": CJK_TOKENIZER_VERSION,
        "license_id": "CC-BY-SA-2.0",
        "inputs": [
            repo_record(args.candidates, "frozen_candidates"),
            repo_record(args.source_map, "frozen_source_map"),
            repo_record(args.curation, "frozen_bilingual_curation"),
            repo_record(raw_dir / "manifest.json", "raw_manifest"),
            repo_record(raw_dir / "raw_sources.jsonl", "raw_sources"),
            repo_record(args.phase1_catalog, "phase1_retained_catalog"),
        ],
        "files": [{"path": name, "sha256": sha256_hex(content), "size_bytes": len(content)}
                  for name, content in sorted(file_contents.items())],
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    stage = output.parent / f".staging-{output.name}-{uuid.uuid4().hex[:10]}"
    stage.mkdir()
    try:
        for name, content in file_contents.items():
            (stage / name).write_bytes(content)
        (stage / "build_manifest.json").write_bytes(canonical_json_bytes(manifest))
        publish_directory_atomically(stage, output, expected_files=set(file_contents) | {"build_manifest.json"})
    except Exception:
        if stage.exists():
            shutil.rmtree(stage)
        raise
    print(json.dumps({"release": str(output), "catalog_sha256": sha256_hex(catalog_bytes),
        "documents_sha256": sha256_hex(document_bytes), "entry_count": len(catalog.entries),
        "document_count": len(documents), "curation_sha256": curation_hash}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
