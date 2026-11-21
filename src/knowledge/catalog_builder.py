"""Offline normalization from frozen OSM Wiki snapshots and reviewed curation."""

from __future__ import annotations

import json
from pathlib import Path
import re
from typing import Any, Sequence

from .contracts import KnowledgeCatalogV2, RawWikiSourceV1, RawWikiSourceV2
from .errors import KnowledgeBuildError, KnowledgeValidationError
from .validation import validate_catalog_payload, validate_raw_source_payload, validate_raw_source_v2_payload


STATUS_RE = re.compile(r"(?:^|\n)Status:\s*\n([^\n]+)", re.IGNORECASE)
STATUS_MAP = {
    "approved": "approved",
    "de facto": "de_facto",
    "in use": "in_use",
    "deprecated": "deprecated",
    "obsolete": "obsolete",
    "proposed": "proposed",
    "rejected": "rejected",
    "unknown": "unknown",
}


def load_raw_sources(path: Path) -> list[RawWikiSourceV1 | RawWikiSourceV2]:
    sources: list[RawWikiSourceV1 | RawWikiSourceV2] = []
    for line_number, line in enumerate(Path(path).read_text("utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
            if payload.get("schema_version") == "2.0":
                sources.append(validate_raw_source_v2_payload(payload))
            else:
                sources.append(validate_raw_source_payload(payload))
        except (json.JSONDecodeError, KnowledgeValidationError) as error:
            code = getattr(error, "code", "OSM_KB_SOURCE_SCHEMA_INVALID")
            raise KnowledgeBuildError(code, f"raw source line {line_number}: {error}") from error
    if not sources:
        raise KnowledgeBuildError("OSM_KB_BUILD_INPUT_EMPTY", "raw source input is empty")
    tags = [item.canonical_tag for item in sources]
    if len(tags) != len(set(tags)):
        raise KnowledgeBuildError("OSM_KB_CANONICAL_TAG_DUPLICATE", "raw sources contain duplicate tags")
    page_fingerprints = {}
    for source in sources:
        if isinstance(source, RawWikiSourceV2):
            fingerprint = (source.canonical_url, source.content_sha256, source.license_id)
            prior = page_fingerprints.setdefault(source.source_page_id, fingerprint)
            if prior != fingerprint:
                raise KnowledgeBuildError("OSM_KB_SOURCE_PAGE_MISMATCH", "one fixed page maps to inconsistent content")
    return sorted(sources, key=lambda item: item.canonical_tag)


def _status_from_source(source: RawWikiSourceV1) -> str:
    match = STATUS_RE.search(source.text)
    if not match:
        return "unknown"
    raw_status = " ".join(match.group(1).strip().casefold().split())
    return STATUS_MAP.get(raw_status, "unknown")


def _element_and_geometry_types(tag: str) -> tuple[list[str], list[str]]:
    key, value = tag.split("=", 1)
    if key == "building" or key == "landuse":
        return ["relation", "way"], ["area"]
    if key == "highway":
        return ["node"], ["point"]
    if tag == "railway=subway_entrance" or tag == "railway=tram_stop":
        return ["node"], ["point"]
    if tag == "railway=halt":
        return ["node", "way"], ["area", "point"]
    if tag in {"railway=platform", "public_transport=platform"}:
        return ["node", "way"], ["area", "line", "point"]
    if tag in {"railway=station", "public_transport=station"}:
        return ["node", "relation", "way"], ["area", "point"]
    if tag == "aeroway=aerodrome":
        return ["node", "relation", "way"], ["area", "point"]
    if tag == "aeroway=airport":
        return ["node", "way"], ["area", "point"]
    if tag == "aeroway=terminal":
        return ["relation", "way"], ["area"]
    if key == "leisure":
        return ["node", "relation", "way"], ["area", "point"]
    return ["node", "way"], ["area", "point"]


def _related_categories(tag: str, primary: str) -> list[str]:
    related: list[str] = []
    if primary == "food" and tag.startswith("shop="):
        related.append("retail")
    if tag == "amenity=pharmacy":
        related.append("retail")
    if tag == "shop=chemist":
        related.append("healthcare")
    if tag in {"aeroway=terminal", "public_transport=station", "railway=station"}:
        related.append("building")
    if tag in {"amenity=hospital", "amenity=school", "building=school", "landuse=education"}:
        related.append("building")
    if tag == "office=government":
        related.append("building")
    return sorted(set(item for item in related if item != primary))


def _compatible_patterns(tag: str) -> list[str]:
    key, _ = tag.split("=", 1)
    patterns = ["name=*"]
    if key in {"amenity", "shop", "office", "healthcare", "leisure"}:
        patterns.append("opening_hours=*")
    if key == "shop":
        patterns.append("brand=*")
    if key == "building":
        patterns.append("building:levels=*")
    if key in {"public_transport", "railway", "highway"}:
        patterns.append("ref=*")
    return sorted(patterns)


def _validate_input_sets(candidates: dict, sources: Sequence[RawWikiSourceV1], curation: dict):
    candidate_rows = candidates.get("candidates")
    curation_rows = curation.get("entries")
    if not isinstance(candidate_rows, list) or not isinstance(curation_rows, list):
        raise KnowledgeBuildError("OSM_KB_BUILD_INPUT_INVALID", "candidate and curation entries must be lists")
    candidate_tags = [item.get("canonical_tag") for item in candidate_rows]
    source_tags = [item.canonical_tag for item in sources]
    curation_tags = [item.get("canonical_tag") for item in curation_rows]
    if len(candidate_tags) != 100 or len(set(candidate_tags)) != 100:
        raise KnowledgeBuildError("OSM_KB_RELEASE_COUNT_INVALID", "candidate set must contain 100 unique tags")
    if set(candidate_tags) != set(source_tags) or set(candidate_tags) != set(curation_tags):
        raise KnowledgeBuildError(
            "OSM_KB_BUILD_INPUT_VERSION_MISMATCH",
            "candidate, raw source, and curation tag sets must match exactly",
        )
    for item in curation_rows:
        if item.get("hard_negative_tag") not in set(candidate_tags):
            raise KnowledgeBuildError(
                "OSM_KB_HARD_NEGATIVE_TARGET_UNKNOWN",
                f"unknown hard negative for {item.get('canonical_tag')}",
            )


def build_catalog_from_payloads(
    candidates: dict[str, Any],
    sources: Sequence[RawWikiSourceV1],
    curation: dict[str, Any],
    *,
    candidate_manifest_sha256: str,
    catalog_version: str,
) -> KnowledgeCatalogV2:
    _validate_input_sets(candidates, sources, curation)
    source_by_tag = {item.canonical_tag: item for item in sources}
    curation_by_tag = {item["canonical_tag"]: item for item in curation["entries"]}
    entries = []
    for candidate in sorted(candidates["candidates"], key=lambda item: item["canonical_tag"]):
        tag = candidate["canonical_tag"]
        key, value = tag.split("=", 1)
        source = source_by_tag[tag]
        curated = curation_by_tag[tag]
        negative_tag = curated["hard_negative_tag"]
        negative = curation_by_tag[negative_tag]
        element_types, geometry_types = _element_and_geometry_types(tag)
        reason_en = (
            f"Do not use {negative['name_en']} for this feature: {curated['name_en']} means "
            f"{curated['description_en'].rstrip('.').lower()}, whereas {negative['name_en']} means "
            f"{negative['description_en'].rstrip('.').lower()}."
        )
        reason_zh = (
            f"不要把“{curated['name_zh']}”误标为“{negative['name_zh']}”：“{curated['name_zh']}”"
            f"指{curated['description_zh'].rstrip('。')}；后者指{negative['description_zh'].rstrip('。')}。"
        )
        entries.append(
            {
                "schema_version": "2.0",
                "entry_id": f"osm-tag:{tag}",
                "canonical_tag": tag,
                "key": key,
                "value": value,
                "primary_category": candidate["primary_category"],
                "related_categories": _related_categories(tag, candidate["primary_category"]),
                "name_en": curated["name_en"],
                "name_zh": curated["name_zh"],
                "aliases_en": curated["aliases_en"],
                "aliases_zh": curated["aliases_zh"],
                "description_en": curated["description_en"],
                "description_zh": curated["description_zh"],
                "element_types": element_types,
                "geometry_types": geometry_types,
                "status": _status_from_source(source),
                "compatible_tag_patterns": _compatible_patterns(tag),
                "related_tags": [negative_tag],
                "hard_negatives": [
                    {
                        "canonical_tag": negative_tag,
                        "reason_en": reason_en,
                        "reason_zh": reason_zh,
                        "source_type": "manual_semantic_review",
                    }
                ],
                "source_refs": [source.source_id],
                "curation_method": curated["construction_method"],
            }
        )
    payload = {
        "schema_version": "2.0",
        "catalog_version": catalog_version,
        "release_phase": "m03_phase1",
        "candidate_manifest_sha256": candidate_manifest_sha256,
        "license_id": "CC-BY-SA-2.0",
        "license_url": "https://creativecommons.org/licenses/by-sa/2.0/",
        "curation_notice": curation["curation_notice"],
        "entries": entries,
    }
    try:
        return validate_catalog_payload(payload)
    except KnowledgeValidationError as error:
        raise KnowledgeBuildError(error.code, str(error)) from error