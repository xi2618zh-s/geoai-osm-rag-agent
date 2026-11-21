"""M03 OSM knowledge contracts and deterministic offline build helpers."""

from .contracts import (
    HardNegativeV1,
    IndexDocumentV1,
    KnowledgeCatalogV2,
    KnowledgeCatalogV3,
    KnowledgeEntryV3,
    AttributeApplicabilityV1,
    TagRole,
    KnowledgeEntryV2,
    RawWikiSourceV1,
    RawWikiSourceV2,
    RetrievalQueryV1,
    RetrievalQueryV2,
)
from .errors import KnowledgeBuildError, KnowledgeValidationError
from .validation import validate_catalog_payload, validate_catalog_v3_payload, validate_raw_source_payload, validate_raw_source_v2_payload

__all__ = [
    "HardNegativeV1",
    "IndexDocumentV1",
    "KnowledgeBuildError",
    "KnowledgeCatalogV2",
    "KnowledgeCatalogV3",
    "KnowledgeEntryV3",
    "AttributeApplicabilityV1",
    "TagRole",
    "KnowledgeEntryV2",
    "KnowledgeValidationError",
    "RawWikiSourceV1",
    "RawWikiSourceV2",
    "RetrievalQueryV1",
    "RetrievalQueryV2",
    "validate_catalog_payload",
    "validate_catalog_v3_payload",
    "validate_raw_source_payload",
    "validate_raw_source_v2_payload",
]