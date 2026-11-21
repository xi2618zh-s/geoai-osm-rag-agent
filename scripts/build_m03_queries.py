"""Build the immutable M03 300-query dev/public_eval data release offline."""

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

from src.knowledge import validate_catalog_payload
from src.knowledge.build import publish_directory_atomically
from src.knowledge.evaluation import audit_query_leakage
from src.knowledge.query_builder import build_query_sets
from src.knowledge.serialization import canonical_json_bytes, canonical_jsonl_bytes, sha256_hex


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--build-timestamp", required=True)
    args = parser.parse_args()
    catalog_path = args.catalog.resolve()
    catalog_bytes = catalog_path.read_bytes()
    catalog = validate_catalog_payload(json.loads(catalog_bytes.decode("utf-8")))
    output = args.output_dir.resolve()
    if output.exists():
        raise SystemExit("OSM_KB_EVAL_DATA_EXISTS: refusing to overwrite immutable query release")
    output.parent.mkdir(parents=True, exist_ok=True)
    staging = output.parent / f".staging-{output.name}-{uuid.uuid4().hex[:10]}"
    staging.mkdir()
    try:
        dev, public_eval = build_query_sets(catalog)
        if len(dev) != 200 or len(public_eval) != 100:
            raise ValueError("OSM_KB_EVAL_QUERY_COUNT_INVALID")
        audit_query_leakage(catalog, dev)
        audit_query_leakage(catalog, public_eval)
        dev_bytes = canonical_jsonl_bytes(dev)
        public_bytes = canonical_jsonl_bytes(public_eval)
        readme = (
            "# M03 retrieval evaluation data v1\n\n"
            "This directory contains a public development set (200 queries) and a public evaluation set (100 challenge queries). "
            "The public_eval split is not a hidden test set and must be run only once after configuration freeze for an experiment version.\n\n"
            "Each of the 100 tags has one English and one Chinese dev query. Each tag has one public_eval challenge; exactly 25 use a catalog alias, and the other 75 contain neither the target name nor its aliases. "
            "All queries record authoring method and Codex assistance.\n"
        ).encode("utf-8")
        (staging / "dev.jsonl").write_bytes(dev_bytes)
        (staging / "public_eval.jsonl").write_bytes(public_bytes)
        (staging / "README.md").write_bytes(readme)

        def distribution(queries, field):
            result = {}
            for query in queries:
                value = getattr(query, field)
                key = value.value if hasattr(value, "value") else value
                result[key] = result.get(key, 0) + 1
            return dict(sorted(result.items()))

        files = []
        for name, content in (
            ("README.md", readme),
            ("dev.jsonl", dev_bytes),
            ("public_eval.jsonl", public_bytes),
        ):
            files.append({"path": name, "sha256": sha256_hex(content), "size_bytes": len(content)})
        manifest = {
            "schema_version": "1.0",
            "query_release_id": output.name,
            "build_timestamp": args.build_timestamp,
            "catalog_version": catalog.catalog_version,
            "catalog_sha256": sha256_hex(catalog_bytes),
            "query_schema_version": "1.0",
            "dev": {
                "query_count": len(dev),
                "target_count": len({item.target_tag for item in dev}),
                "language_counts": distribution(dev, "language"),
                "category_counts": distribution(dev, "primary_category"),
                "challenge_counts": distribution(dev, "challenge_type"),
            },
            "public_eval": {
                "query_count": len(public_eval),
                "target_count": len({item.target_tag for item in public_eval}),
                "is_hidden_test": False,
                "run_policy": "Run once only after configuration freeze; later changes require a new feedback-affected experiment version.",
                "language_counts": distribution(public_eval, "language"),
                "category_counts": distribution(public_eval, "primary_category"),
                "challenge_counts": distribution(public_eval, "challenge_type"),
                "alias_challenge_count": sum(item.challenge_type == "alias" for item in public_eval),
                "non_alias_target_term_count": 0,
            },
            "leakage_audit": {
                "dev_query_text_copied_to_catalog": 0,
                "public_eval_query_text_copied_to_catalog": 0,
            },
            "files": files,
        }
        (staging / "query_manifest.json").write_bytes(canonical_json_bytes(manifest))
        publish_directory_atomically(
            staging,
            output,
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