"""Publish an immutable, leakage-checked Phase 2 bilingual query release."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import sys
import uuid

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.knowledge import validate_catalog_v3_payload
from src.knowledge.build import publish_directory_atomically
from src.knowledge.evaluation import validate_phase2_query_sets
from src.knowledge.query_builder_v3 import build_query_sets_v3
from src.knowledge.serialization import canonical_json_bytes, canonical_jsonl_bytes, sha256_hex


def distribution(queries, field):
    counts = {}
    for query in queries:
        value = getattr(query, field)
        key = value.value if hasattr(value, "value") else value
        counts[key] = counts.get(key, 0) + 1
    return dict(sorted(counts.items()))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--candidates", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--build-timestamp", required=True)
    args = parser.parse_args()
    output = args.output_dir.resolve()
    if output.exists():
        raise SystemExit("M03_PHASE2_QUERY_RELEASE_EXISTS: refusing to overwrite")
    catalog_bytes = args.catalog.read_bytes()
    candidate_bytes = args.candidates.read_bytes()
    catalog = validate_catalog_v3_payload(json.loads(catalog_bytes.decode("utf-8")))
    candidate_payload = json.loads(candidate_bytes.decode("utf-8"))
    retained = {
        row["canonical_tag"] for row in candidate_payload["candidates"]
        if row["retained_from_phase1"]
    }
    dev, public = build_query_sets_v3(catalog, retained)
    audit = validate_phase2_query_sets(catalog, dev, public, retained)
    if audit != {
        "dev_count": 410, "public_eval_count": 205, "target_count": 205,
        "phase1_retained_target_count": 100, "phase2_new_target_count": 105,
    }:
        raise ValueError("M03_PHASE2_QUERY_RELEASE_COUNTS_INVALID")

    output.parent.mkdir(parents=True, exist_ok=True)
    staging = output.parent / f".staging-{output.name}-{uuid.uuid4().hex[:10]}"
    staging.mkdir()
    try:
        readme = (
            "# M03 Phase 2 retrieval query release\n\n"
            "This public release contains 410 development queries (English and Chinese for every tag) "
            "and 205 public_eval challenge queries (one per tag). The total is 615 distinct queries. "
            "Both cohorts, retained 100 and new 105, are covered. public_eval is public, not hidden, "
            "and may be used once per frozen experiment after configuration freeze.\n\n"
            "Queries are project-authored and Codex-assisted. Name and alias prompts use catalog "
            "names or aliases. Scenario challenges are catalog-informed paraphrases, with reviewed "
            "exceptions for awkward automatic name removal. They are separate query records and "
            "have no exact full-text overlap with the catalog or each other; this does not establish "
            "independent external annotation. Results should be interpreted with that limitation.\n"
        ).encode("utf-8")
        payloads = {
            "README.md": readme,
            "dev.jsonl": canonical_jsonl_bytes(dev),
            "public_eval.jsonl": canonical_jsonl_bytes(public),
        }
        for name, content in payloads.items():
            (staging / name).write_bytes(content)
        files = [
            {"path": name, "sha256": sha256_hex(content), "size_bytes": len(content)}
            for name, content in sorted(payloads.items())
        ]
        manifest = {
            "schema_version": "2.0",
            "query_release_id": output.name,
            "build_timestamp": args.build_timestamp,
            "catalog_version": catalog.catalog_version,
            "catalog_sha256": sha256_hex(catalog_bytes),
            "candidate_sha256": sha256_hex(candidate_bytes),
            "query_schema_version": "2.0",
            "source_note": "Project-authored, Codex-assisted; scenario queries are catalog-informed.",
            "audit": audit,
            "dev": {
                "query_count": len(dev),
                "target_count": len({q.target_tag for q in dev}),
                "language_counts": distribution(dev, "language"),
                "category_counts": distribution(dev, "primary_category"),
                "role_counts": distribution(dev, "tag_role"),
                "cohort_counts": distribution(dev, "target_cohort"),
                "challenge_counts": distribution(dev, "challenge_type"),
            },
            "public_eval": {
                "query_count": len(public),
                "target_count": len({q.target_tag for q in public}),
                "is_hidden_test": False,
                "run_policy": "One run after configuration freeze; any later changes require a new feedback-affected experiment.",
                "language_counts": distribution(public, "language"),
                "category_counts": distribution(public, "primary_category"),
                "role_counts": distribution(public, "tag_role"),
                "cohort_counts": distribution(public, "target_cohort"),
                "challenge_counts": distribution(public, "challenge_type"),
            },
            "leakage_audit": {
                "duplicate_query_texts": 0,
                "cross_split_text_overlap": 0,
                "exact_catalog_full_text_copies": 0,
                "non_alias_target_term_leaks": 0,
            },
            "files": files,
        }
        (staging / "query_manifest.json").write_bytes(canonical_json_bytes(manifest))
        publish_directory_atomically(
            staging, output,
            expected_files={"README.md", "dev.jsonl", "public_eval.jsonl", "query_manifest.json"},
        )
        print(json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2))
        return 0
    except Exception:
        if staging.exists():
            shutil.rmtree(staging)
        raise


if __name__ == "__main__":
    raise SystemExit(main())
