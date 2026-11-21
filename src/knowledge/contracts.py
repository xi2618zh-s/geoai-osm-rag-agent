"""Strict, versioned contracts for the M03 OSM knowledge base."""

from __future__ import annotations

from enum import Enum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, StrictStr, model_validator


SHA256_PATTERN = r"^[0-9a-f]{64}$"
CANONICAL_TAG_PATTERN = r"^[a-z0-9_]+=[a-z0-9_:-]+$"


class StrictKnowledgeModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class PrimaryCategory(str, Enum):
    FOOD = "food"
    RETAIL = "retail"
    HEALTHCARE = "healthcare"
    EDUCATION = "education"
    TRANSPORT = "transport"
    PUBLIC_SERVICE = "public_service"
    LEISURE = "leisure"
    BUILDING = "building"


class OSMElementType(str, Enum):
    NODE = "node"
    WAY = "way"
    RELATION = "relation"


class GeometryType(str, Enum):
    POINT = "point"
    LINE = "line"
    AREA = "area"


class TagStatus(str, Enum):
    APPROVED = "approved"
    DE_FACTO = "de_facto"
    IN_USE = "in_use"
    DEPRECATED = "deprecated"
    OBSOLETE = "obsolete"
    PROPOSED = "proposed"
    REJECTED = "rejected"
    UNKNOWN = "unknown"


class HardNegativeSource(str, Enum):
    WIKI_HINT = "wiki_hint"
    MANUAL_SEMANTIC_REVIEW = "manual_semantic_review"


class Language(str, Enum):
    EN = "en"
    ZH = "zh"


class EvaluationSplit(str, Enum):
    DEV = "dev"
    PUBLIC_EVAL = "public_eval"


PrimaryCategoryValue = Annotated[PrimaryCategory, Field(strict=False)]
OSMElementTypeValue = Annotated[OSMElementType, Field(strict=False)]
GeometryTypeValue = Annotated[GeometryType, Field(strict=False)]
TagStatusValue = Annotated[TagStatus, Field(strict=False)]
HardNegativeSourceValue = Annotated[HardNegativeSource, Field(strict=False)]
LanguageValue = Annotated[Language, Field(strict=False)]
EvaluationSplitValue = Annotated[EvaluationSplit, Field(strict=False)]


class HardNegativeV1(StrictKnowledgeModel):
    canonical_tag: StrictStr = Field(pattern=CANONICAL_TAG_PATTERN)
    reason_en: StrictStr
    reason_zh: StrictStr
    source_type: HardNegativeSourceValue


class RawWikiSourceV1(StrictKnowledgeModel):
    schema_version: Literal["1.0"]
    source_id: StrictStr
    canonical_tag: StrictStr = Field(pattern=CANONICAL_TAG_PATTERN)
    request_url: StrictStr
    canonical_url: StrictStr
    revision: StrictStr
    history_url: StrictStr
    retrieved_at: StrictStr
    etag: StrictStr | None
    last_modified: StrictStr | None
    content_type: StrictStr
    title: StrictStr
    text: StrictStr
    content_sha256: StrictStr = Field(pattern=SHA256_PATTERN)
    license_id: StrictStr
    license_url: StrictStr
    contributors: StrictStr
    modification_note: StrictStr
    page_has_separate_license: StrictBool


class RawWikiSourceV2(RawWikiSourceV1):
    schema_version: Literal["2.0"]
    source_page_title: StrictStr
    source_page_kind: Literal["tag", "key_exception"]
    source_page_id: StrictStr
    value_evidence_excerpt: StrictStr | None
    value_evidence_sha256: StrictStr | None
    value_status_review: Literal["tag_page_status", "key_page_no_independent_value_status"]
    reviewed_value_status: TagStatusValue
    value_status_review_note: StrictStr


class KnowledgeEntryV2(StrictKnowledgeModel):
    schema_version: Literal["2.0"]
    entry_id: StrictStr
    canonical_tag: StrictStr = Field(pattern=CANONICAL_TAG_PATTERN)
    key: StrictStr
    value: StrictStr
    primary_category: PrimaryCategoryValue
    related_categories: list[PrimaryCategoryValue]
    name_en: StrictStr
    name_zh: StrictStr
    aliases_en: list[StrictStr]
    aliases_zh: list[StrictStr]
    description_en: StrictStr
    description_zh: StrictStr
    element_types: list[OSMElementTypeValue]
    geometry_types: list[GeometryTypeValue]
    status: TagStatusValue
    compatible_tag_patterns: list[StrictStr]
    related_tags: list[StrictStr]
    hard_negatives: list[HardNegativeV1]
    source_refs: list[StrictStr]
    curation_method: Literal["codex_assisted_project_review", "manual_project_review"]


class KnowledgeCatalogV2(StrictKnowledgeModel):
    schema_version: Literal["2.0"]
    catalog_version: StrictStr
    release_phase: Literal["m03_phase1"]
    candidate_manifest_sha256: StrictStr = Field(pattern=SHA256_PATTERN)
    license_id: Literal["CC-BY-SA-2.0"]
    license_url: StrictStr
    curation_notice: StrictStr
    entries: list[KnowledgeEntryV2]


class PrimaryCategoryV3(str, Enum):
    FOOD = "food"
    RETAIL = "retail"
    HEALTHCARE = "healthcare"
    EDUCATION = "education"
    TRANSPORT = "transport"
    PUBLIC_SERVICE = "public_service"
    LEISURE = "leisure"
    BUILDING = "building"
    ACCESSIBILITY = "accessibility"
    LAND_USE = "land_use"


class TagRole(str, Enum):
    FEATURE = "feature"
    LAND_USE = "land_use"
    ATTRIBUTE = "attribute"


class AttributeSemanticKind(str, Enum):
    LEGAL_ACCESS = "legal_access"
    MODE_ACCESS = "mode_access"
    WHEELCHAIR_ACCESS = "wheelchair_access"
    SIDEWALK_PRESENCE = "sidewalk_presence"
    KERB_PROFILE = "kerb_profile"


PrimaryCategoryV3Value = Annotated[PrimaryCategoryV3, Field(strict=False)]
TagRoleValue = Annotated[TagRole, Field(strict=False)]
AttributeSemanticKindValue = Annotated[AttributeSemanticKind, Field(strict=False)]


class AttributeApplicabilityV1(StrictKnowledgeModel):
    semantic_kind: AttributeSemanticKindValue
    applies_to_keys: list[StrictStr]
    combination_note_en: StrictStr
    combination_note_zh: StrictStr


class KnowledgeEntryV3(KnowledgeEntryV2):
    schema_version: Literal["3.0"]
    primary_category: PrimaryCategoryV3Value
    related_categories: list[PrimaryCategoryV3Value]
    tag_role: TagRoleValue
    applicability: AttributeApplicabilityV1 | None


class KnowledgeCatalogV3(KnowledgeCatalogV2):
    schema_version: Literal["3.0"]
    release_phase: Literal["m03_phase2"]
    entries: list[KnowledgeEntryV3]


class IndexDocumentV1(StrictKnowledgeModel):
    schema_version: Literal["1.0"]
    document_id: StrictStr
    entry_id: StrictStr
    canonical_tag: StrictStr = Field(pattern=CANONICAL_TAG_PATTERN)
    language: LanguageValue
    primary_category: PrimaryCategoryValue
    title: StrictStr
    text: StrictStr
    source_refs: list[StrictStr]
    catalog_version: StrictStr
    catalog_license_id: StrictStr


class IndexDocumentV2(IndexDocumentV1):
    schema_version: Literal["2.0"]
    primary_category: PrimaryCategoryV3Value
    tag_role: TagRoleValue
    applicability: AttributeApplicabilityV1 | None


class RetrievalQueryV1(StrictKnowledgeModel):
    schema_version: Literal["1.0"]
    query_id: StrictStr
    split: EvaluationSplitValue
    language: LanguageValue
    query_text: StrictStr
    target_tag: StrictStr = Field(pattern=CANONICAL_TAG_PATTERN)
    excluded_tags: list[StrictStr]
    primary_category: PrimaryCategoryValue
    challenge_type: StrictStr
    construction_note: StrictStr
    author: StrictStr
    construction_method: Literal[
        "codex_assisted_manual_review", "manual_project_review", "catalog_alias_challenge"
    ]
    codex_assisted: StrictBool
    catalog_version: StrictStr

    @model_validator(mode="after")
    def hard_negative_is_not_target(self):
        if self.target_tag in self.excluded_tags:
            raise ValueError("target_tag must not appear in excluded_tags")
        return self


class RetrievalQueryV2(RetrievalQueryV1):
    schema_version: Literal["2.0"]
    primary_category: PrimaryCategoryV3Value
    tag_role: TagRoleValue
    target_cohort: Literal["phase1_retained", "phase2_new"]


class FileManifestEntryV1(StrictKnowledgeModel):
    path: StrictStr
    sha256: StrictStr = Field(pattern=SHA256_PATTERN)
    size_bytes: StrictInt = Field(ge=0)