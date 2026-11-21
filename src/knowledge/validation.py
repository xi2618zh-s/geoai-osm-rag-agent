"""Semantic validation and normalization for M03 knowledge contracts."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime
import hashlib
import re
import unicodedata
from urllib.parse import parse_qs, unquote, urlparse
from typing import Any

from pydantic import ValidationError

from .contracts import KnowledgeCatalogV2, KnowledgeCatalogV3, PrimaryCategory, PrimaryCategoryV3, RawWikiSourceV1, RawWikiSourceV2, TagStatus
from .errors import KnowledgeValidationError


CANONICAL_TAG_RE = re.compile(r"^[a-z0-9_:-]+=[a-z0-9_:-]+$")
HAN_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")
LATIN_RE = re.compile(r"[A-Za-z]")
CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")
OFFICIAL_WIKI_HOST = "wiki.openstreetmap.org"


def _fail(code: str, message: str):
    raise KnowledgeValidationError(code, message)


def normalize_text(value: str) -> str:
    text = unicodedata.normalize("NFKC", str(value))
    if CONTROL_RE.search(text):
        _fail("OSM_KB_REQUIRED_TEXT_EMPTY", "control characters are not allowed")
    return " ".join(text.strip().split())


def alias_identity(value: str) -> str:
    return normalize_text(value).casefold()


def normalize_canonical_tag(value: Any) -> str:
    if not isinstance(value, str):
        _fail("OSM_KB_CANONICAL_TAG_INVALID", "canonical_tag must be a string")
    tag = unicodedata.normalize("NFKC", value).strip()
    if not CANONICAL_TAG_RE.fullmatch(tag) or tag.count("=") != 1:
        _fail("OSM_KB_CANONICAL_TAG_INVALID", f"invalid canonical OSM tag: {value!r}")
    return tag


def _required_text(entry: dict, field: str) -> str:
    value = entry.get(field)
    if not isinstance(value, str) or not normalize_text(value):
        _fail("OSM_KB_REQUIRED_TEXT_EMPTY", f"{field} must be non-empty")
    return normalize_text(value)


def _validate_language(value: str, language: str, field: str):
    if language == "en" and (not LATIN_RE.search(value) or HAN_RE.search(value)):
        _fail("OSM_KB_LANGUAGE_MISMATCH", f"{field} must contain concise English text")
    if language == "zh" and not HAN_RE.search(value):
        _fail("OSM_KB_LANGUAGE_MISMATCH", f"{field} must contain Chinese text")


def _validate_https_url(value: Any, field: str, *, official_wiki: bool = False):
    if not isinstance(value, str):
        _fail("OSM_KB_SOURCE_URL_INVALID", f"{field} must be an HTTPS URL")
    parsed = urlparse(value)
    if parsed.scheme != "https" or not parsed.netloc:
        _fail("OSM_KB_SOURCE_URL_INVALID", f"{field} must be an HTTPS URL")
    if official_wiki and parsed.hostname != OFFICIAL_WIKI_HOST:
        _fail("OSM_KB_SOURCE_URL_INVALID", f"{field} must use the official OSM Wiki host")


def validate_raw_source_payload(payload: dict[str, Any], *, allow_key_page: bool = False) -> RawWikiSourceV1:
    data = deepcopy(payload)
    for field in ("request_url", "canonical_url", "history_url"):
        _validate_https_url(data.get(field), field, official_wiki=True)
    _validate_https_url(data.get("license_url"), "license_url")

    revision = data.get("revision")
    if not isinstance(revision, str) or not revision.strip() or not revision.isdigit():
        _fail("OSM_KB_SOURCE_VERSION_MISSING", "revision/oldid must be a non-empty decimal string")
    if not isinstance(data.get("license_id"), str) or not data["license_id"].strip():
        _fail("OSM_KB_LICENSE_MISSING", "source license is required")
    if data.get("page_has_separate_license") is True:
        _fail("OSM_KB_LICENSE_EXCEPTION", "page-level license exception requires manual review")

    query = parse_qs(urlparse(str(data.get("canonical_url", ""))).query)
    if query.get("oldid") != [revision]:
        _fail("OSM_KB_SOURCE_VERSION_MISSING", "canonical_url oldid must match revision")
    if not allow_key_page:
        expected_title = f"Tag:{data.get('canonical_tag')}"
        canonical_title = query.get("title", [None])[0]
        request_title = unquote(urlparse(str(data.get("request_url", ""))).path).split("/")[-1]
        if canonical_title != expected_title or request_title != expected_title:
            _fail("OSM_KB_SOURCE_SCHEMA_INVALID", "v1 source must be the exact Tag page")
    if "action=history" not in str(data.get("history_url", "")):
        _fail("OSM_KB_SOURCE_URL_INVALID", "history_url must link to page history")

    retrieved = data.get("retrieved_at")
    try:
        if not isinstance(retrieved, str) or not retrieved.endswith("Z"):
            raise ValueError
        datetime.fromisoformat(retrieved[:-1] + "+00:00")
    except ValueError:
        _fail("OSM_KB_SOURCE_SCHEMA_INVALID", "retrieved_at must be an ISO-8601 UTC timestamp")

    text = data.get("text")
    if not isinstance(text, str) or not text.strip():
        _fail("OSM_KB_SOURCE_SCHEMA_INVALID", "source text must be non-empty")
    if re.match(r"^\s*(?:Redirect to:|#REDIRECT\b)", text, flags=re.IGNORECASE):
        _fail("OSM_KB_SOURCE_SCHEMA_INVALID", "redirect-only page has no Tag content")
    expected_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
    if data.get("content_sha256") != expected_hash:
        _fail("OSM_KB_SOURCE_CONTENT_HASH_MISMATCH", "content_sha256 does not match source text")

    try:
        source = RawWikiSourceV1.model_validate(data)
    except ValidationError as error:
        _fail("OSM_KB_SOURCE_SCHEMA_INVALID", str(error))
    expected_id = f"osm-wiki:{source.canonical_tag}@{source.revision}"
    if source.source_id != expected_id:
        _fail("OSM_KB_SOURCE_SCHEMA_INVALID", "source_id does not match canonical tag and revision")
    return source


_KEY_PAGE_EXCEPTION_TITLES = {
    "kerb=lowered": "Key:kerb",
    "wheelchair=yes": "Key:wheelchair",
    "wheelchair=limited": "Key:wheelchair",
    "wheelchair=no": "Key:wheelchair",
}


def validate_raw_source_v2_payload(payload: dict[str, Any]) -> RawWikiSourceV2:
    """Validate explicit page identity and exact value evidence for Phase 2 sources."""
    if not isinstance(payload, dict):
        _fail("OSM_KB_SOURCE_SCHEMA_INVALID", "v2 source must be an object")
    data = deepcopy(payload)
    v1_fields = set(RawWikiSourceV1.model_fields)
    base = {key: value for key, value in data.items() if key in v1_fields}
    base["schema_version"] = "1.0"
    validate_raw_source_payload(base, allow_key_page=True)
    tag = normalize_canonical_tag(data.get("canonical_tag"))
    kind = data.get("source_page_kind")
    title = data.get("source_page_title")
    expected_title = f"Tag:{tag}" if kind == "tag" else _KEY_PAGE_EXCEPTION_TITLES.get(tag)
    if not isinstance(title, str) or title != expected_title:
        _fail("OSM_KB_SOURCE_SCHEMA_INVALID", "source page title does not match approved mapping")
    canonical_title = parse_qs(urlparse(data["canonical_url"]).query).get("title", [None])[0]
    request_title = unquote(urlparse(data["request_url"]).path).split("/")[-1]
    if canonical_title != title or request_title != title:
        _fail("OSM_KB_SOURCE_SCHEMA_INVALID", "source URL does not name the actual page")
    if data.get("source_page_id") != f"osm-wiki-page:{title}@{data['revision']}":
        _fail("OSM_KB_SOURCE_SCHEMA_INVALID", "source page id mismatch")
    review_note = data.get("value_status_review_note")
    if not isinstance(review_note, str) or not review_note.strip():
        _fail("OSM_KB_SOURCE_SCHEMA_INVALID", "value status review note is required")
    excerpt = data.get("value_evidence_excerpt")
    excerpt_hash = data.get("value_evidence_sha256")
    if kind == "tag":
        if (excerpt is not None or excerpt_hash is not None
            or data.get("value_status_review") != "tag_page_status"
            or data.get("reviewed_value_status") == "unknown"):
            _fail("OSM_KB_SOURCE_SCHEMA_INVALID", "Tag page must use Tag status review")
    elif kind == "key_exception":
        if (not isinstance(excerpt, str) or not excerpt.strip()
            or excerpt not in data["text"]
            or hashlib.sha256(excerpt.encode("utf-8")).hexdigest() != excerpt_hash
            or data.get("value_status_review") != "key_page_no_independent_value_status"
            or data.get("reviewed_value_status") != "unknown"
            or "Key 页未给取值独立状态" not in review_note):
            _fail("OSM_KB_SOURCE_SCHEMA_INVALID", "Key exception needs exact value evidence and explicit unknown status")
        value = tag.split("=", 1)[1]
        if not re.search(r"(?:^|\n)" + re.escape(value) + r"(?:\n|$)", excerpt):
            _fail("OSM_KB_SOURCE_SCHEMA_INVALID", "value evidence does not identify target value")
    else:
        _fail("OSM_KB_SOURCE_SCHEMA_INVALID", "unsupported source page kind")
    try:
        return RawWikiSourceV2.model_validate(data)
    except ValidationError as error:
        _fail("OSM_KB_SOURCE_SCHEMA_INVALID", str(error))


def _normalize_string_list(values: Any, field: str) -> list[str]:
    if not isinstance(values, list):
        _fail("OSM_KB_SCHEMA_INVALID", f"{field} must be a list")
    normalized = []
    for value in values:
        if not isinstance(value, str) or not normalize_text(value):
            _fail("OSM_KB_REQUIRED_TEXT_EMPTY", f"{field} contains an empty value")
        normalized.append(normalize_text(value))
    return sorted(normalized, key=lambda item: (item.casefold(), item))


def _validate_catalog_payload(payload: dict[str, Any], *, category_enum, catalog_model):
    data = deepcopy(payload)
    entries = data.get("entries")
    if not isinstance(entries, list):
        _fail("OSM_KB_SCHEMA_INVALID", "entries must be a list")

    canonical_tags: list[str] = []
    for entry in entries:
        if not isinstance(entry, dict):
            _fail("OSM_KB_SCHEMA_INVALID", "each entry must be an object")
        tag = normalize_canonical_tag(entry.get("canonical_tag"))
        canonical_tags.append(tag)
        key, value = tag.split("=", 1)
        if entry.get("key") != key or entry.get("value") != value:
            _fail("OSM_KB_CANONICAL_TAG_INVALID", "key/value must exactly match canonical_tag")
        if entry.get("entry_id") != f"osm-tag:{tag}":
            _fail("OSM_KB_SCHEMA_INVALID", "entry_id must derive from canonical_tag")

        primary = entry.get("primary_category")
        if not isinstance(primary, str) or primary not in {item.value for item in category_enum}:
            _fail("OSM_KB_CATEGORY_INVALID", "primary_category must be exactly one allowed category")
        related = entry.get("related_categories")
        if not isinstance(related, list):
            _fail("OSM_KB_CATEGORY_INVALID", "related_categories must be a list")
        if any(not isinstance(item, str) or item not in {x.value for x in category_enum} for item in related):
            _fail("OSM_KB_CATEGORY_INVALID", "related_categories contains an invalid category")
        if len(related) != len(set(related)):
            _fail("OSM_KB_CATEGORY_DUPLICATE", "related_categories must be unique")
        if primary in related:
            _fail("OSM_KB_CATEGORY_DUPLICATE", "primary_category must not be repeated as related")
        entry["related_categories"] = sorted(related)

        status = entry.get("status")
        if not isinstance(status, str) or status not in {item.value for item in TagStatus}:
            _fail("OSM_KB_STATUS_INVALID", "status must use the frozen enum")
        element_types = entry.get("element_types")
        if not isinstance(element_types, list) or not element_types:
            _fail("OSM_KB_OBJECT_TYPES_EMPTY", "at least one supported OSM element type is required")

        for field, language in (
            ("name_en", "en"),
            ("description_en", "en"),
            ("name_zh", "zh"),
            ("description_zh", "zh"),
        ):
            entry[field] = _required_text(entry, field)
            _validate_language(entry[field], language, field)

        for field, language in (("aliases_en", "en"), ("aliases_zh", "zh")):
            aliases = _normalize_string_list(entry.get(field), field)
            identities = [alias_identity(item) for item in aliases]
            if len(identities) != len(set(identities)):
                _fail("OSM_KB_ALIAS_DUPLICATE", f"{field} contains normalized duplicates")
            for alias in aliases:
                _validate_language(alias, language, field)
                if CANONICAL_TAG_RE.fullmatch(alias_identity(alias)):
                    _fail("OSM_KB_ALIAS_CANONICAL_CONFLICT", "alias must not be a canonical tag")
            entry[field] = aliases

        for field in ("compatible_tag_patterns", "related_tags", "source_refs"):
            entry[field] = _normalize_string_list(entry.get(field), field)
        if not entry["source_refs"]:
            _fail("OSM_KB_SOURCE_SCHEMA_INVALID", "entry must reference at least one raw source")

        for field in ("element_types", "geometry_types"):
            values = entry.get(field)
            if not isinstance(values, list):
                _fail("OSM_KB_SCHEMA_INVALID", f"{field} must be a list")
            if len(values) != len(set(values)):
                _fail("OSM_KB_SCHEMA_INVALID", f"{field} must be unique")
            entry[field] = sorted(values)

        hard_negatives = entry.get("hard_negatives")
        if not isinstance(hard_negatives, list) or not 1 <= len(hard_negatives) <= 3:
            _fail("OSM_KB_HARD_NEGATIVE_INVALID", "each entry requires one to three hard negatives")
        seen_hard = set()
        for negative in hard_negatives:
            if not isinstance(negative, dict):
                _fail("OSM_KB_HARD_NEGATIVE_INVALID", "hard negative must be an object")
            negative_tag = normalize_canonical_tag(negative.get("canonical_tag"))
            if negative_tag == tag:
                _fail("OSM_KB_HARD_NEGATIVE_SELF_REFERENCE", "hard negative cannot reference itself")
            if negative_tag in seen_hard:
                _fail("OSM_KB_HARD_NEGATIVE_INVALID", "hard negative targets must be unique")
            seen_hard.add(negative_tag)
            for field, language in (("reason_en", "en"), ("reason_zh", "zh")):
                text = _required_text(negative, field)
                _validate_language(text, language, field)
                negative[field] = text
        entry["hard_negatives"] = sorted(hard_negatives, key=lambda item: item["canonical_tag"])

    if len(canonical_tags) != len(set(canonical_tags)):
        _fail("OSM_KB_CANONICAL_TAG_DUPLICATE", "canonical_tag values must be unique")
    known_tags = set(canonical_tags)
    for entry in entries:
        for negative in entry["hard_negatives"]:
            if negative["canonical_tag"] not in known_tags:
                _fail("OSM_KB_HARD_NEGATIVE_TARGET_UNKNOWN", "hard negative must reference a catalog tag")

    aliases_by_identity: dict[str, str] = {}
    for entry in entries:
        for alias in entry["aliases_en"] + entry["aliases_zh"]:
            identity = alias_identity(alias)
            owner = aliases_by_identity.get(identity)
            if owner is not None and owner != entry["canonical_tag"]:
                _fail("OSM_KB_ALIAS_CROSS_TAG_CONFLICT", f"alias {alias!r} belongs to multiple tags")
            aliases_by_identity[identity] = entry["canonical_tag"]

    data["entries"] = sorted(entries, key=lambda item: item["canonical_tag"])
    try:
        return catalog_model.model_validate(data)
    except ValidationError as error:
        _fail("OSM_KB_SCHEMA_INVALID", str(error))

def validate_catalog_payload(payload: dict[str, Any]) -> KnowledgeCatalogV2:
    """Validate the original Phase 1 v2 schema without broadening its enum."""
    return _validate_catalog_payload(
        payload, category_enum=PrimaryCategory, catalog_model=KnowledgeCatalogV2
    )


_ATTRIBUTE_SEMANTIC_KIND_BY_KEY = {
    "access": "legal_access",
    "foot": "mode_access",
    "bicycle": "mode_access",
    "wheelchair": "wheelchair_access",
    "sidewalk": "sidewalk_presence",
    "kerb": "kerb_profile",
}


def validate_catalog_v3_payload(payload: dict[str, Any]) -> KnowledgeCatalogV3:
    """Validate the Phase 2 role/applicability extension and inherited v2 rules."""
    if not isinstance(payload, dict) or not isinstance(payload.get("entries"), list):
        _fail("OSM_KB_SCHEMA_INVALID", "v3 catalog entries must be a list")
    for entry in payload["entries"]:
        if not isinstance(entry, dict):
            _fail("OSM_KB_SCHEMA_INVALID", "v3 entry must be an object")
        tag = normalize_canonical_tag(entry.get("canonical_tag"))
        key = tag.split("=", 1)[0]
        role = entry.get("tag_role")
        category = entry.get("primary_category")
        applicability = entry.get("applicability")
        if role not in {"feature", "land_use", "attribute"}:
            _fail("OSM_KB_TAG_ROLE_INVALID", f"invalid tag role for {tag}")
        if role == "land_use":
            if key != "landuse" or category != "land_use" or applicability is not None:
                _fail("OSM_KB_TAG_ROLE_INVALID", f"land_use role/category mismatch for {tag}")
        elif role == "attribute":
            if key not in _ATTRIBUTE_SEMANTIC_KIND_BY_KEY or category != "accessibility":
                _fail("OSM_KB_TAG_ROLE_INVALID", f"attribute role/category mismatch for {tag}")
            if not isinstance(applicability, dict):
                _fail("OSM_KB_ATTRIBUTE_APPLICABILITY_MISSING", f"attribute applicability missing for {tag}")
            if applicability.get("semantic_kind") != _ATTRIBUTE_SEMANTIC_KIND_BY_KEY[key]:
                _fail("OSM_KB_ATTRIBUTE_SEMANTICS_INVALID", f"semantic kind mismatch for {tag}")
            keys = applicability.get("applies_to_keys")
            if (not isinstance(keys, list) or not keys
                or any(not isinstance(item, str) or not re.fullmatch(r"[a-z0-9_:-]+", item) for item in keys)
                or len(keys) != len(set(keys))):
                _fail("OSM_KB_ATTRIBUTE_APPLICABILITY_INVALID", f"invalid applies_to_keys for {tag}")
            for field, language in (("combination_note_en", "en"), ("combination_note_zh", "zh")):
                text = _required_text(applicability, field)
                _validate_language(text, language, field)
        else:
            if category in {"accessibility", "land_use"} or applicability is not None:
                _fail("OSM_KB_TAG_ROLE_INVALID", f"feature role/category mismatch for {tag}")
    return _validate_catalog_payload(
        payload, category_enum=PrimaryCategoryV3, catalog_model=KnowledgeCatalogV3
    )
