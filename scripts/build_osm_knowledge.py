"""Build and atomically publish an immutable M03 Phase 1 catalog release offline."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import sys
import uuid

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.knowledge.build import build_index_documents, publish_directory_atomically
from src.knowledge.catalog_builder import build_catalog_from_payloads, load_raw_sources
from src.knowledge.manifest import validate_manifest_files
from src.knowledge.serialization import canonical_json_bytes, canonical_jsonl_bytes
from src.knowledge.tokenizer import CJK_TOKENIZER_VERSION


def sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def verified_json(path: Path, sidecar: Path) -> tuple[dict, bytes, str]:
    content = path.read_bytes()
    actual = sha256(content)
    expected = sidecar.read_text("utf-8").split()[0]
    if actual != expected:
        raise ValueError(f"OSM_KB_MANIFEST_HASH_MISMATCH: {path.name}")
    return json.loads(content.decode("utf-8")), content, actual


def relative_input(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT.resolve()).as_posix()
    except ValueError as error:
        raise ValueError("OSM_KB_BUILD_INPUT_INVALID: inputs must be inside the repository") from error


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidates", type=Path, required=True)
    parser.add_argument("--candidate-sha256", type=Path, required=True)
    parser.add_argument("--raw-dir", type=Path, required=True)
    parser.add_argument("--curation", type=Path, required=True)
    parser.add_argument("--curation-sha256", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--catalog-version", required=True)
    parser.add_argument("--build-timestamp", required=True)
    args = parser.parse_args()

    output = args.output_dir.resolve()
    if output.exists():
        raise SystemExit("OSM_KB_RELEASE_EXISTS: refusing to overwrite immutable release")
    output.parent.mkdir(parents=True, exist_ok=True)
    staging = output.parent / f".staging-{output.name}-{uuid.uuid4().hex[:10]}"
    staging.mkdir()

    try:
        candidate_path = args.candidates.resolve()
        curation_path = args.curation.resolve()
        raw_dir = args.raw_dir.resolve()
        raw_path = raw_dir / "raw_sources.jsonl"
        raw_manifest_path = raw_dir / "manifest.json"
        candidates, candidate_content, candidate_hash = verified_json(
            candidate_path, args.candidate_sha256.resolve()
        )
        curation, curation_content, curation_hash = verified_json(
            curation_path, args.curation_sha256.resolve()
        )
        validate_manifest_files(raw_manifest_path)
        raw_manifest_content = raw_manifest_path.read_bytes()
        raw_manifest = json.loads(raw_manifest_content.decode("utf-8"))
        if raw_manifest.get("candidate_manifest_sha256") != candidate_hash:
            raise ValueError("OSM_KB_BUILD_INPUT_VERSION_MISMATCH: raw snapshot candidate hash differs")
        if raw_manifest.get("source_count") != 100:
            raise ValueError("OSM_KB_RELEASE_COUNT_INVALID: raw snapshot must contain 100 sources")

        sources = load_raw_sources(raw_path)
        catalog = build_catalog_from_payloads(
            candidates,
            sources,
            curation,
            candidate_manifest_sha256=candidate_hash,
            catalog_version=args.catalog_version,
        )
        documents = build_index_documents(catalog)
        if len(catalog.entries) != 100 or len(documents) != 200:
            raise ValueError("OSM_KB_RELEASE_COUNT_INVALID: release must contain 100 entries and 200 documents")

        catalog_content = canonical_json_bytes(catalog)
        documents_content = canonical_jsonl_bytes(documents)
        attribution_content = (raw_dir / "ATTRIBUTION.md").read_bytes()
        (staging / "catalog.json").write_bytes(catalog_content)
        (staging / "index_documents.jsonl").write_bytes(documents_content)
        (staging / "ATTRIBUTION.md").write_bytes(attribution_content)

        category_counts: dict[str, int] = {}
        status_counts: dict[str, int] = {}
        for entry in catalog.entries:
            category_counts[entry.primary_category.value] = category_counts.get(entry.primary_category.value, 0) + 1
            status_counts[entry.status.value] = status_counts.get(entry.status.value, 0) + 1
        input_records = [
            {
                "path": relative_input(candidate_path),
                "role": "frozen_candidates",
                "sha256": candidate_hash,
                "size_bytes": len(candidate_content),
            },
            {
                "path": relative_input(curation_path),
                "role": "bilingual_curation",
                "sha256": curation_hash,
                "size_bytes": len(curation_content),
            },
            {
                "path": relative_input(raw_manifest_path),
                "role": "raw_manifest",
                "sha256": sha256(raw_manifest_content),
                "size_bytes": len(raw_manifest_content),
            },
            {
                "path": relative_input(raw_path),
                "role": "raw_sources",
                "sha256": sha256(raw_path.read_bytes()),
                "size_bytes": raw_path.stat().st_size,
            },
        ]
        output_records = []
        for filename, role, content in (
            ("ATTRIBUTION.md", "license_attribution", attribution_content),
            ("catalog.json", "knowledge_catalog", catalog_content),
            ("index_documents.jsonl", "shared_retrieval_documents", documents_content),
        ):
            output_records.append(
                {
                    "path": filename,
                    "role": role,
                    "sha256": sha256(content),
                    "size_bytes": len(content),
                }
            )
        manifest = {
            "schema_version": "1.0",
            "release_id": output.name,
            "release_phase": "m03_phase1",
            "m03_module_status": "IN_PROGRESS",
            "phase_status": "DONE_PENDING_INDEX_AND_EVALUATION",
            "catalog_version": args.catalog_version,
            "build_timestamp": args.build_timestamp,
            "candidate_manifest_sha256": candidate_hash,
            "entry_count": len(catalog.entries),
            "unique_canonical_tag_count": len({item.canonical_tag for item in catalog.entries}),
            "index_document_count": len(documents),
            "documents_per_tag": 2,
            "languages": ["en", "zh"],
            "category_counts": dict(sorted(category_counts.items())),
            "status_counts": dict(sorted(status_counts.items())),
            "catalog_schema_version": "2.0",
            "raw_source_schema_version": "1.0",
            "index_document_schema_version": "1.0",
            "tokenizer_version": CJK_TOKENIZER_VERSION,
            "license_id": "CC-BY-SA-2.0",
            "inputs": input_records,
            "files": output_records,
        }
        (staging / "build_manifest.json").write_bytes(canonical_json_bytes(manifest))
        publish_directory_atomically(
            staging,
            output,
            expected_files={
                "ATTRIBUTION.md",
                "catalog.json",
                "index_documents.jsonl",
                "build_manifest.json",
            },
        )
        print(json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2))
        return 0
    except Exception:
        if staging.exists():
            shutil.rmtree(staging)
        raise


if __name__ == "__main__":
    raise SystemExit(main())