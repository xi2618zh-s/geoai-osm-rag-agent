"""Deterministic Phase 2 v3 catalog assembly from reviewed bilingual rows."""

from __future__ import annotations

from collections import Counter
from typing import Sequence

from .catalog_builder import _compatible_patterns, _related_categories
from .contracts import KnowledgeCatalogV2, KnowledgeCatalogV3, RawWikiSourceV2
from .validation import validate_catalog_v3_payload


_FIELDS = (
    "canonical_tag", "name_en", "name_zh", "alias_en", "alias_zh",
    "description_en", "description_zh", "hard_negative_tag",
)
_KEY_EXCEPTIONS = {"kerb=lowered", "wheelchair=yes", "wheelchair=limited", "wheelchair=no"}
_APPLIES_TO = {
    "access": ["amenity", "building", "highway", "public_transport", "railway"],
    "bicycle": ["highway", "route"],
    "foot": ["highway", "route"],
    "kerb": ["barrier", "highway", "public_transport"],
    "sidewalk": ["highway"],
    "wheelchair": ["amenity", "building", "healthcare", "highway", "public_transport", "railway", "shop", "tourism"],
}
_ATTRIBUTE_NOTES_EN = {
    "access=private": "Combine with a road, place or entrance to record legal access restriction; never infer wheelchair accessibility.",
    "bicycle=designated": "Combine with a route or way to record signed cycling access; it does not establish wheelchair access.",
    "foot=designated": "Combine with a route or way to record designated walking access; it does not establish wheelchair access.",
    "kerb=lowered": "Combine with a crossing, kerb or stop to describe a lowered edge; do not infer an accessible end-to-end route.",
    "sidewalk=both": "Combine with a road to indicate sidewalks on both sides; this does not establish wheelchair access.",
    "wheelchair=limited": "Combine with an existing object to record limited wheelchair access; the specific accessible extent cannot be inferred from this tag alone.",
    "wheelchair=no": "Combine with an existing object to record lack of unrestricted wheelchair access; do not confuse physical access with legal restriction.",
    "wheelchair=yes": "Combine with an existing object to record wheelchair usability; do not infer access along the whole route to it.",
}


def parse_new_curation_rows(text: str) -> list[dict[str, str]]:
    rows = []
    for number, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        fields = [part.strip() for part in line.split("|")]
        if len(fields) != len(_FIELDS) or any(not part for part in fields):
            raise ValueError(f"M03_PHASE2_CURATION_ROW_INVALID: line {number}")
        rows.append(dict(zip(_FIELDS, fields)))
    tags = [row["canonical_tag"] for row in rows]
    if len(tags) != len(set(tags)):
        raise ValueError("M03_PHASE2_CURATION_TAG_DUPLICATE")
    return rows


def _geometry(tag: str, role: str) -> tuple[list[str], list[str]]:
    key = tag.split("=", 1)[0]
    if role == "attribute":
        return ["node", "relation", "way"], ["area", "line", "point"]
    if role == "land_use" or key == "building":
        return ["relation", "way"], ["area"]
    if tag == "highway=crossing" or tag == "public_transport=stop_position":
        return ["node"], ["point"]
    if key == "highway" or (key == "railway" and tag in {
        "railway=rail", "railway=light_rail", "railway=subway", "railway=tram"
    }):
        return ["way"], ["line"]
    if tag == "route=bus":
        return ["relation"], ["line"]
    if tag in {"leisure=swimming_area", "leisure=dog_park"}:
        return ["relation", "way"], ["area"]
    if tag == "leisure=track":
        return ["relation", "way"], ["area", "line"]
    return ["node", "relation", "way"], ["area", "point"]


def build_phase2_catalog(
    candidates: dict,
    sources: Sequence[RawWikiSourceV2],
    phase1: KnowledgeCatalogV2,
    new_rows: Sequence[dict[str, str]],
    *,
    candidate_sha256: str,
    catalog_version: str,
) -> KnowledgeCatalogV3:
    rows = candidates.get("candidates")
    if not isinstance(rows, list) or len(rows) != 205:
        raise ValueError("M03_PHASE2_CANDIDATE_COUNT_INVALID")
    candidate_by_tag = {row["canonical_tag"]: row for row in rows}
    old_by_tag = {entry.canonical_tag: entry for entry in phase1.entries}
    new_by_tag = {row["canonical_tag"]: row for row in new_rows}
    source_by_tag = {source.canonical_tag: source for source in sources}
    if (len(candidate_by_tag) != 205 or len(source_by_tag) != 205
        or len(new_by_tag) != 105 or set(candidate_by_tag) != set(source_by_tag)
        or set(new_by_tag) != set(candidate_by_tag) - set(old_by_tag)
        or {tag for tag, row in candidate_by_tag.items() if row.get("retained_from_phase1")} != set(old_by_tag)):
        raise ValueError("M03_PHASE2_INPUT_SET_MISMATCH")
    if len({source.source_page_id for source in sources}) != 203:
        raise ValueError("M03_PHASE2_SOURCE_PAGE_COUNT_INVALID")
    if {source.canonical_tag for source in sources if source.source_page_kind == "key_exception"} != _KEY_EXCEPTIONS:
        raise ValueError("M03_PHASE2_KEY_EXCEPTION_SCOPE_INVALID")
    if any(source.reviewed_value_status.value == "unknown" for source in sources if source.canonical_tag not in _KEY_EXCEPTIONS):
        raise ValueError("M03_PHASE2_UNREVIEWED_TAG_STATUS")
    entries = []
    for tag in sorted(candidate_by_tag):
        candidate = candidate_by_tag[tag]
        source = source_by_tag[tag]
        if source.canonical_tag != tag:
            raise ValueError("M03_PHASE2_SOURCE_TAG_MISMATCH")
        if tag in old_by_tag:
            entry = old_by_tag[tag].model_dump(mode="json")
            entry["schema_version"] = "3.0"
            entry["primary_category"] = candidate["primary_category"]
            entry["related_categories"] = sorted(set(
                item for item in entry["related_categories"] if item != entry["primary_category"]
            ))
        else:
            curated = new_by_tag[tag]
            negative_tag = curated["hard_negative_tag"]
            if negative_tag not in candidate_by_tag or negative_tag == tag:
                raise ValueError("M03_PHASE2_HARD_NEGATIVE_INVALID")
            negative = new_by_tag.get(negative_tag)
            if negative is None:
                old = old_by_tag[negative_tag]
                negative_name_en, negative_name_zh = old.name_en, old.name_zh
                negative_description_en, negative_description_zh = old.description_en, old.description_zh
            else:
                negative_name_en, negative_name_zh = negative["name_en"], negative["name_zh"]
                negative_description_en, negative_description_zh = negative["description_en"], negative["description_zh"]
            role = candidate["tag_role"]
            key, value = tag.split("=", 1)
            element_types, geometry_types = _geometry(tag, role)
            related = _related_categories(tag, candidate["primary_category"])
            if key == "building" and candidate["primary_category"] != "building":
                related = sorted(set(related + ["building"]))
            entry = {
                "schema_version": "3.0", "entry_id": f"osm-tag:{tag}", "canonical_tag": tag,
                "key": key, "value": value,
                "primary_category": candidate["primary_category"], "related_categories": related,
                "name_en": curated["name_en"], "name_zh": curated["name_zh"],
                "aliases_en": [curated["alias_en"]], "aliases_zh": [curated["alias_zh"]],
                "description_en": curated["description_en"], "description_zh": curated["description_zh"],
                "element_types": element_types, "geometry_types": geometry_types,
                "status": source.reviewed_value_status.value,
                "compatible_tag_patterns": _compatible_patterns(tag),
                "related_tags": [negative_tag],
                "hard_negatives": [{
                    "canonical_tag": negative_tag,
                    "reason_en": f"Do not confuse {curated['name_en']} with {negative_name_en}: {curated['description_en']} In contrast, {negative_description_en}",
                    "reason_zh": f"不要把“{curated['name_zh']}”误作“{negative_name_zh}”：{curated['description_zh']} 后者是：{negative_description_zh}",
                    "source_type": "manual_semantic_review",
                }],
                "source_refs": [source.source_id],
                "curation_method": "codex_assisted_project_review",
            }
        role = candidate["tag_role"]
        entry["tag_role"] = role
        entry["status"] = source.reviewed_value_status.value
        entry["source_refs"] = [source.source_id]
        if role == "attribute":
            key = tag.split("=", 1)[0]
            entry["applicability"] = {
                "semantic_kind": candidate["semantic_kind"],
                "applies_to_keys": _APPLIES_TO[key],
                "combination_note_en": _ATTRIBUTE_NOTES_EN[tag],
                "combination_note_zh": candidate["applicability_and_combination"],
            }
            entry["compatible_tag_patterns"] = [item + "=*" for item in _APPLIES_TO[key]]
        else:
            entry["applicability"] = None
        entries.append(entry)
    payload = {
        "schema_version": "3.0", "catalog_version": catalog_version,
        "release_phase": "m03_phase2", "candidate_manifest_sha256": candidate_sha256,
        "license_id": "CC-BY-SA-2.0",
        "license_url": "https://creativecommons.org/licenses/by-sa/2.0/",
        "curation_notice": "Official OSM Wiki fixed-source text and Codex-assisted project-authored bilingual curation; 100 Phase 1 entries retained with v3 role/category mapping; four Key-page value statuses explicitly unknown. Knowledge data CC BY-SA 2.0; software license unchanged.",
        "entries": entries,
    }
    return validate_catalog_v3_payload(payload)
