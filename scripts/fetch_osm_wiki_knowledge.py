"""Fetch one immutable M03 raw snapshot from a frozen official OSM Wiki allow-list."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.knowledge.build import AtomicPublishError, publish_directory_atomically
from src.knowledge.fetcher import OsmWikiFetchError, fetch_candidate_snapshot
from src.knowledge.catalog_builder import load_raw_sources
from src.knowledge.manifest import validate_manifest_files
from src.knowledge.serialization import canonical_json_bytes, canonical_jsonl_bytes


def sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def load_frozen_candidates(path: Path, sidecar: Path) -> tuple[dict, str]:
    content = path.read_bytes()
    actual = sha256(content)
    expected = sidecar.read_text("utf-8").split()[0]
    if actual != expected:
        raise ValueError("OSM_KB_CANDIDATE_HASH_MISMATCH: candidate allow-list hash changed")
    payload = json.loads(content.decode("utf-8"))
    candidates = payload.get("candidates")
    if not isinstance(candidates, list) or len(candidates) != payload.get("expected_unique_tags"):
        raise ValueError("OSM_KB_CANDIDATE_SCHEMA_INVALID: count does not match frozen manifest")
    tags = [item.get("canonical_tag") for item in candidates]
    if len(tags) != len(set(tags)) or tags != sorted(tags):
        raise ValueError("OSM_KB_CANDIDATE_SCHEMA_INVALID: tags must be unique and sorted")
    return payload, actual


def attribution_text(sources) -> str:
    lines = [
        "# M03 OSM Wiki text attribution",
        "",
        "This notice applies to the text-only M03 OSM knowledge data, not to the software code in this repository.",
        "",
        "Source text: OpenStreetMap Wiki contributors, CC BY-SA 2.0.",
        "License: https://creativecommons.org/licenses/by-sa/2.0/",
        "Wiki license policy: https://wiki.openstreetmap.org/w/index.php?title=Wiki_content_license&oldid=3006732",
        "Modifications: navigation and scripts removed; concise bilingual names, summaries, aliases, and confusion notes are project curation and are distributed with this knowledge data under CC BY-SA 2.0.",
        "No images, screenshots, maps, or other media are included.",
        "",
        "## Fixed source revisions",
        "",
    ]
    for source in sources:
        lines.append(
            f"- `{source.canonical_tag}`: {source.canonical_url} — OpenStreetMap Wiki contributors; revision {source.revision}; CC BY-SA 2.0; text extracted and cleaned."
        )
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidates", type=Path, required=True)
    parser.add_argument("--candidate-sha256", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--reuse-raw-dir", type=Path, default=None, help="Validated immutable prior raw snapshot to reuse for retained tags")
    parser.add_argument("--sleep-seconds", type=float, default=0.25)
    args = parser.parse_args()

    output = args.output_dir.resolve()
    if output.exists():
        raise SystemExit("OSM_KB_RELEASE_EXISTS: refusing to overwrite immutable raw snapshot")
    output.parent.mkdir(parents=True, exist_ok=True)
    staging = output.parent / f".staging-{output.name}-{uuid.uuid4().hex[:10]}"
    if staging.exists():
        raise SystemExit("OSM_KB_STAGING_EXISTS: generated staging path already exists")
    staging.mkdir()

    try:
        candidate_payload, candidate_hash = load_frozen_candidates(
            args.candidates.resolve(), args.candidate_sha256.resolve()
        )
        sources = []
        candidates = candidate_payload["candidates"]
        reused = {}
        reused_hash = None
        if args.reuse_raw_dir is not None:
            prior = args.reuse_raw_dir.resolve()
            validate_manifest_files(prior / "manifest.json")
            prior_raw = prior / "raw_sources.jsonl"
            reused_hash = sha256(prior_raw.read_bytes())
            reused = {item.canonical_tag: item for item in load_raw_sources(prior_raw)}
            retained = {
                item["canonical_tag"] for item in candidates
                if item.get("retained_from_phase1") is True
            }
            if set(reused) != retained:
                raise ValueError("OSM_KB_BUILD_INPUT_VERSION_MISMATCH: reused raw tags differ from retained candidates")
        fetched_count = 0
        for index, candidate in enumerate(candidates, 1):
            tag = candidate["canonical_tag"]
            source = reused.get(tag)
            if source is None:
                try:
                    source = fetch_candidate_snapshot(candidate)
                except OsmWikiFetchError as error:
                    raise RuntimeError(
                        f"{error.code}: [{index}/{len(candidates)}] {tag}: {error}"
                    ) from error
                fetched_count += 1
            sources.append(source)
            origin = "reused" if tag in reused else "fetched"
            print(
                f"[{index}/{len(candidates)}] {tag} oldid={source.revision} {origin}",
                flush=True,
            )
            if origin == "fetched" and index != len(candidates) and args.sleep_seconds > 0:
                time.sleep(args.sleep_seconds)

        expected = candidate_payload["expected_unique_tags"]
        if len(sources) != expected or len({item.canonical_tag for item in sources}) != expected:
            raise RuntimeError(f"OSM_KB_RELEASE_COUNT_INVALID: raw snapshot is not {expected}/{expected} unique tags")

        raw_content = canonical_jsonl_bytes(sources)
        attribution = attribution_text(sources).encode("utf-8")
        (staging / "raw_sources.jsonl").write_bytes(raw_content)
        (staging / "ATTRIBUTION.md").write_bytes(attribution)
        records = [
            {
                "canonical_tag": source.canonical_tag,
                "source_id": source.source_id,
                "request_url": source.request_url,
                "canonical_url": source.canonical_url,
                "revision": source.revision,
                "history_url": source.history_url,
                "retrieved_at": source.retrieved_at,
                "etag": source.etag,
                "last_modified": source.last_modified,
                "content_type": source.content_type,
                "content_sha256": source.content_sha256,
                "page_has_separate_license": source.page_has_separate_license,
                "license_id": source.license_id,
            }
            for source in sorted(sources, key=lambda item: item.canonical_tag)
        ]
        manifest = {
            "schema_version": "1.0",
            "snapshot_id": output.name,
            "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "candidate_set_id": candidate_payload["candidate_set_id"],
            "candidate_manifest_sha256": candidate_hash,
            "source_policy": candidate_payload["source_policy"],
            "license_id": "CC-BY-SA-2.0",
            "license_policy_url": "https://wiki.openstreetmap.org/w/index.php?title=Wiki_content_license&oldid=3006732",
            "text_only": True,
            "source_count": len(sources),
            "reused_source_count": len(reused),
            "new_fetch_count": fetched_count,
            "reused_raw_sha256": reused_hash,
            "unique_canonical_tag_count": len({item.canonical_tag for item in sources}),
            "source_records": records,
            "files": [
                {
                    "path": "ATTRIBUTION.md",
                    "sha256": sha256(attribution),
                    "size_bytes": len(attribution),
                },
                {
                    "path": "raw_sources.jsonl",
                    "sha256": sha256(raw_content),
                    "size_bytes": len(raw_content),
                },
            ],
        }
        (staging / "manifest.json").write_bytes(canonical_json_bytes(manifest))
        publish_directory_atomically(
            staging,
            output,
            expected_files={"raw_sources.jsonl", "manifest.json", "ATTRIBUTION.md"},
        )
        print(f"Published immutable raw snapshot: {output}", flush=True)
        return 0
    except Exception:
        if staging.exists():
            shutil.rmtree(staging)
        raise


if __name__ == "__main__":
    raise SystemExit(main())