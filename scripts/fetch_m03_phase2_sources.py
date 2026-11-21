"""Publish Phase 2 raw v2: 205 tag mappings from 201 Tag and 2 fixed Key pages."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
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
from src.knowledge.catalog_builder import load_raw_sources
from src.knowledge.fetcher import _default_get, _fixed_parse_api_url, _parse_fixed_api_response, _header
from src.knowledge.manifest import validate_manifest_files
from src.knowledge.serialization import canonical_json_bytes, canonical_jsonl_bytes, sha256_hex
from src.knowledge.validation import validate_raw_source_payload, validate_raw_source_v2_payload


def verified_map(path: Path, sidecar: Path) -> tuple[dict, str]:
    content = path.read_bytes()
    digest = sha256_hex(content)
    if sidecar.read_text("utf-8").split()[0] != digest:
        raise ValueError("OSM_KB_SOURCE_MAP_HASH_MISMATCH")
    payload = json.loads(content)
    mappings = payload["mappings"]
    tags = [item["canonical_tag"] for item in mappings]
    if (len(tags) != 205 or tags != sorted(tags) or len(set(tags)) != 205
        or len({item["source_page_id"] for item in mappings}) != 203
        or sum(item["source_page_kind"] == "key_exception" for item in mappings) != 4):
        raise ValueError("OSM_KB_SOURCE_MAP_COUNT_INVALID")
    return payload, digest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-map", type=Path, required=True)
    parser.add_argument("--source-map-sha256", type=Path, required=True)
    parser.add_argument("--candidate-file", type=Path, required=True)
    parser.add_argument("--reuse-raw-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    output = args.output_dir.resolve()
    if output.exists():
        raise SystemExit("OSM_KB_RELEASE_EXISTS: refusing to overwrite Phase 2 raw snapshot")
    source_map, source_map_hash = verified_map(args.source_map, args.source_map_sha256)
    candidate_hash = sha256_hex(args.candidate_file.read_bytes())
    if source_map["candidate_manifest_sha256"] != candidate_hash:
        raise ValueError("OSM_KB_SOURCE_MAP_CANDIDATE_MISMATCH")
    prior = args.reuse_raw_dir.resolve()
    validate_manifest_files(prior / "manifest.json")
    prior_bytes = (prior / "raw_sources.jsonl").read_bytes()
    prior_sources = {item["canonical_tag"]: item for item in (
        json.loads(line) for line in prior_bytes.decode("utf-8").splitlines() if line.strip()
    )}
    if len(prior_sources) != 205:
        raise ValueError("OSM_KB_REUSED_RAW_COUNT_INVALID")
    key_pages = {}
    for title, oldid in (("Key:wheelchair", "3062598"), ("Key:kerb", "3074064")):
        mappings = [item for item in source_map["mappings"] if item["source_page_title"] == title]
        if not mappings or {item["oldid"] for item in mappings} != {oldid}:
            raise ValueError("OSM_KB_KEY_OLDID_NOT_APPROVED")
        response = _default_get(_fixed_parse_api_url(int(oldid)))
        display, text, separate_license = _parse_fixed_api_response(response, title, int(oldid))
        if separate_license:
            raise ValueError("OSM_KB_LICENSE_EXCEPTION: fixed Key page")
        digest = sha256_hex(text.encode("utf-8"))
        if {item["expected_content_sha256"] for item in mappings} != {digest}:
            raise ValueError("OSM_KB_FIXED_KEY_CONTENT_CHANGED")
        key_pages[title] = (text, display, response, digest)

    sources = []
    for mapping in source_map["mappings"]:
        tag = mapping["canonical_tag"]
        title = mapping["source_page_title"]
        if mapping["source_page_kind"] == "tag":
            old = validate_raw_source_payload(prior_sources[tag])
            if (old.revision != mapping["oldid"] or old.content_sha256 != mapping["expected_content_sha256"]
                or old.canonical_url != mapping["canonical_url"]):
                raise ValueError("OSM_KB_REUSED_TAG_SOURCE_MISMATCH: " + tag)
            payload = old.model_dump(mode="json")
            payload["modification_note"] += " Reused and mapped to Phase 2 raw v2."
        else:
            text, display, response, digest = key_pages[title]
            excerpt = mapping["value_evidence_excerpt"]
            if (not isinstance(excerpt, str) or excerpt not in text
                or sha256_hex(excerpt.encode("utf-8")) != mapping["value_evidence_sha256"]):
                raise ValueError("OSM_KB_KEY_VALUE_EVIDENCE_MISMATCH: " + tag)
            payload = {
                "schema_version": "2.0", "source_id": f"osm-wiki:{tag}@{mapping['oldid']}",
                "canonical_tag": tag, "request_url": mapping["request_url"],
                "canonical_url": mapping["canonical_url"], "revision": mapping["oldid"],
                "history_url": "https://wiki.openstreetmap.org/w/index.php?title=" + title.replace(":", "%3A") + "&action=history",
                "retrieved_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                "etag": _header(response.headers, "ETag"),
                "last_modified": _header(response.headers, "Last-Modified"),
                "content_type": _header(response.headers, "Content-Type") or "application/json",
                "title": display or title, "text": text, "content_sha256": digest,
                "license_id": "CC-BY-SA-2.0",
                "license_url": "https://wiki.openstreetmap.org/wiki/Wiki_content_license",
                "contributors": "OpenStreetMap Wiki contributors",
                "modification_note": "Text extracted from a fixed-revision official Key page; this tag maps to an exact value passage, not a Tag page.",
                "page_has_separate_license": False,
            }
        payload.update({
            "schema_version": "2.0", "source_page_title": title,
            "source_page_kind": mapping["source_page_kind"],
            "source_page_id": mapping["source_page_id"],
            "value_evidence_excerpt": mapping["value_evidence_excerpt"],
            "value_evidence_sha256": mapping["value_evidence_sha256"],
            "value_status_review": "key_page_no_independent_value_status" if mapping["source_page_kind"] == "key_exception" else "tag_page_status",
            "reviewed_value_status": mapping["reviewed_value_status"],
            "value_status_review_note": (
                "Key 页未给取值独立状态；取值级 status=unknown，未继承 Key 页状态。"
                if mapping["source_page_kind"] == "key_exception" else
                "Status reviewed on the fixed Tag page; no Key-page inference."
            ),
        })
        sources.append(validate_raw_source_v2_payload(payload))
    if len(sources) != 205 or len({item.source_page_id for item in sources}) != 203:
        raise ValueError("OSM_KB_SOURCE_PAGE_COUNT_INVALID")
    raw = canonical_jsonl_bytes(sources)
    grouped = defaultdict(list)
    for source in sources:
        grouped[source.source_page_id].append(source)
    attribution_lines = [
        "# M03 Phase 2 OSM Wiki text attribution", "",
        "This notice applies to the text-only knowledge data, not to the software repository.",
        "Source text: OpenStreetMap Wiki contributors, CC BY-SA 2.0.",
        "License: https://creativecommons.org/licenses/by-sa/2.0/", "",
        "## Fixed pages and canonical tag mappings", "",
    ]
    for page_id, items in sorted(grouped.items()):
        attribution_lines.append(
            f"- `{page_id}`: {items[0].canonical_url}; tags: "
            + ", ".join(f"`{item.canonical_tag}`" for item in items)
            + "; OpenStreetMap Wiki contributors, CC BY-SA 2.0."
        )
    attribution = ("\n".join(attribution_lines) + "\n").encode("utf-8")
    records = [{
        "canonical_tag": source.canonical_tag,
        "source_page_id": source.source_page_id,
        "source_page_kind": source.source_page_kind,
        "canonical_url": source.canonical_url,
        "content_sha256": source.content_sha256,
        "value_evidence_sha256": source.value_evidence_sha256,
        "reviewed_value_status": source.reviewed_value_status.value,
        "value_status_review_note": source.value_status_review_note,
        "license_id": source.license_id,
        "page_has_separate_license": source.page_has_separate_license,
    } for source in sources]
    manifest = {
        "schema_version": "2.0", "snapshot_id": output.name,
        "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "candidate_manifest_sha256": candidate_hash,
        "source_map_sha256": source_map_hash,
        "reused_raw_sha256": sha256_hex(prior_bytes),
        "tag_mapping_count": len(sources), "unique_page_count": len(grouped),
        "tag_page_count": sum(items[0].source_page_kind == "tag" for items in grouped.values()),
        "key_exception_page_count": sum(items[0].source_page_kind == "key_exception" for items in grouped.values()),
        "status_counts": dict(sorted(Counter(source.reviewed_value_status.value for source in sources).items())),
        "license_id": "CC-BY-SA-2.0", "text_only": True,
        "source_records": records,
        "files": [
            {"path": "ATTRIBUTION.md", "sha256": sha256_hex(attribution), "size_bytes": len(attribution)},
            {"path": "raw_sources.jsonl", "sha256": sha256_hex(raw), "size_bytes": len(raw)},
        ],
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    stage = output.parent / f".staging-{output.name}-{uuid.uuid4().hex[:10]}"
    stage.mkdir()
    try:
        (stage / "raw_sources.jsonl").write_bytes(raw)
        (stage / "ATTRIBUTION.md").write_bytes(attribution)
        (stage / "manifest.json").write_bytes(canonical_json_bytes(manifest))
        publish_directory_atomically(stage, output, expected_files={"raw_sources.jsonl", "ATTRIBUTION.md", "manifest.json"})
    except Exception:
        if stage.exists():
            shutil.rmtree(stage)
        raise
    print(json.dumps({"output": str(output), "tag_mappings": len(sources), "pages": len(grouped),
        "raw_sha256": sha256_hex(raw), "manifest_sha256": sha256_hex((output / "manifest.json").read_bytes())},
        ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
