"""Seal M03 Phase 1 only after all immutable inputs pass the final gate."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
import sys
import uuid

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.knowledge.build import publish_directory_atomically
from src.knowledge.contracts import IndexDocumentV1, RetrievalQueryV1
from src.knowledge.manifest import validate_manifest_files
from src.knowledge.retrieval import M03IndexBundle
from src.knowledge.serialization import canonical_json_bytes
from src.knowledge.validation import validate_catalog_payload

VERSION = "m03_phase1_2026-10-05_v1"
RAW = ROOT / "data/knowledge/osm/v2/raw" / VERSION
CATALOG = ROOT / "data/knowledge/osm/v2/releases" / VERSION
INDEX = ROOT / "faiss_index/m03_phase1_v2_e8f8c211"
QUERIES = ROOT / "benchmarks/data/m03/v1"
DEV = ROOT / "benchmarks/results/m03/dev/m03_phase1_dev_v1"
PUBLIC = ROOT / "benchmarks/results/m03/public_eval/m03_phase1_public_eval_v1"
PUBLIC_RECEIPT = ROOT / "benchmarks/results/m03/public_eval_receipts/m03_phase1_public_eval_v1.json"
CONFIG = ROOT / "benchmarks/config/m03_phase1_public_eval_v1.json"
OUTPUT = ROOT / "data/knowledge/osm/v2/publications" / VERSION


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def record(path: Path) -> dict:
    return {
        "path": path.relative_to(ROOT).as_posix(),
        "sha256": digest(path),
        "size_bytes": path.stat().st_size,
    }


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError("OSM_KB_PUBLICATION_GATE_FAILED: " + message)


def main() -> int:
    if OUTPUT.exists():
        raise SystemExit("OSM_KB_PUBLICATION_EXISTS: refusing to overwrite sealed release")
    for directory, manifest_name in (
        (RAW, "manifest.json"),
        (CATALOG, "build_manifest.json"),
        (INDEX, "index_manifest.json"),
        (QUERIES, "query_manifest.json"),
    ):
        validate_manifest_files(directory / manifest_name)

    raw_manifest = json.loads((RAW / "manifest.json").read_text("utf-8"))
    catalog_manifest = json.loads((CATALOG / "build_manifest.json").read_text("utf-8"))
    index_manifest = json.loads((INDEX / "index_manifest.json").read_text("utf-8"))
    query_manifest = json.loads((QUERIES / "query_manifest.json").read_text("utf-8"))
    catalog = validate_catalog_payload(json.loads((CATALOG / "catalog.json").read_text("utf-8")))
    documents = [
        IndexDocumentV1.model_validate_json(line)
        for line in (CATALOG / "index_documents.jsonl").read_text("utf-8").splitlines()
        if line.strip()
    ]
    raw_tags = {
        json.loads(line)["canonical_tag"]
        for line in (RAW / "raw_sources.jsonl").read_text("utf-8").splitlines()
        if line.strip()
    }
    catalog_tags = {entry.canonical_tag for entry in catalog.entries}
    document_tags = {doc.canonical_tag for doc in documents}
    require(len(raw_tags) == len(catalog_tags) == len(document_tags) == 100, "tag counts")
    require(raw_tags == catalog_tags == document_tags, "tag sets differ")
    require(len(documents) == 200, "document count")
    require(
        {doc.language.value for doc in documents} == {"en", "zh"}
        and all(sum(doc.canonical_tag == tag for doc in documents) == 2 for tag in catalog_tags),
        "two bilingual documents per tag",
    )
    require(raw_manifest["source_count"] == 100, "raw manifest count")
    require(catalog_manifest["entry_count"] == 100 and catalog_manifest["index_document_count"] == 200, "catalog manifest counts")
    require(index_manifest["document_count"] == 200 and index_manifest["unique_canonical_tag_count"] == 100, "index manifest counts")
    require(
        digest(CATALOG / "index_documents.jsonl") == digest(INDEX / "documents.jsonl")
        == index_manifest["documents_sha256"],
        "shared index input hash",
    )
    bundle = M03IndexBundle(INDEX)
    require(bundle.dense.ntotal == 200 and bundle.bm25_payload["document_count"] == 200, "physical index counts")

    dev_queries = [RetrievalQueryV1.model_validate_json(line) for line in (QUERIES / "dev.jsonl").read_text("utf-8").splitlines() if line.strip()]
    public_queries = [RetrievalQueryV1.model_validate_json(line) for line in (QUERIES / "public_eval.jsonl").read_text("utf-8").splitlines() if line.strip()]
    require(len(dev_queries) == 200 and len(public_queries) == 100, "evaluation query counts")
    require(query_manifest["dev"]["query_count"] == 200 and query_manifest["public_eval"]["query_count"] == 100, "query manifest counts")
    dev_manifest = json.loads((DEV / "experiment_manifest.json").read_text("utf-8"))
    public_manifest = json.loads((PUBLIC / "experiment_manifest.json").read_text("utf-8"))
    receipt = json.loads(PUBLIC_RECEIPT.read_text("utf-8"))
    require(receipt["experiment_manifest_sha256"] == digest(PUBLIC / "experiment_manifest.json"), "public receipt hash")
    require(receipt["hybrid_config_sha256"] == digest(CONFIG), "frozen config hash")
    require(public_manifest["hybrid_config_sha256"] == digest(CONFIG), "public result config hash")
    require(dev_manifest["query_sha256"] == digest(QUERIES / "dev.jsonl"), "dev query hash")
    require(public_manifest["query_sha256"] == digest(QUERIES / "public_eval.jsonl"), "public query hash")
    require(not public_manifest["is_hidden_test"] and not receipt["feedback_affected"], "public evaluation status")

    output_manifest = {
        "schema_version": "1.0",
        "publication_id": VERSION,
        "published_at": "2026-10-05T00:00:00Z",
        "phase_status": "DONE",
        "m03_module_status": "IN_PROGRESS",
        "catalog_entry_count": 100,
        "unique_canonical_tag_count": 100,
        "index_document_count": 200,
        "dense_vector_count": 200,
        "bm25_document_count": 200,
        "dev_query_count": 200,
        "public_eval_query_count": 100,
        "inputs": [
            record(RAW / "manifest.json"),
            record(RAW / "raw_sources.jsonl"),
            record(CATALOG / "build_manifest.json"),
            record(CATALOG / "catalog.json"),
            record(CATALOG / "index_documents.jsonl"),
            record(INDEX / "index_manifest.json"),
            record(INDEX / "dense.faiss"),
            record(INDEX / "bm25.index.json"),
            record(QUERIES / "query_manifest.json"),
            record(DEV / "experiment_manifest.json"),
            record(PUBLIC / "experiment_manifest.json"),
            record(PUBLIC_RECEIPT),
            record(CONFIG),
        ],
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    staging = OUTPUT.parent / f".staging-{VERSION}-{uuid.uuid4().hex[:10]}"
    staging.mkdir()
    try:
        (staging / "publication_manifest.json").write_bytes(canonical_json_bytes(output_manifest))
        publish_directory_atomically(staging, OUTPUT, expected_files={"publication_manifest.json"})
    except Exception:
        if staging.exists():
            shutil.rmtree(staging)
        raise
    print(json.dumps(output_manifest, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
