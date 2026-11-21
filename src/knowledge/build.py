"""Deterministic offline document construction and atomic publication."""

from __future__ import annotations

from pathlib import Path
import shutil

from .contracts import IndexDocumentV1, IndexDocumentV2, KnowledgeCatalogV2, KnowledgeCatalogV3, Language, TagRole


class AtomicPublishError(RuntimeError):
    pass


def validate_release_size(count: int, *, minimum: int, maximum: int) -> None:
    if not isinstance(count, int) or isinstance(count, bool) or count < minimum or count > maximum:
        raise ValueError(f"release entry count must be between {minimum} and {maximum}")


def _document_text(entry, language: Language) -> tuple[str, str]:
    if language is Language.EN:
        title = entry.name_en
        aliases = entry.aliases_en
        description = entry.description_en
        reasons = [item.reason_en for item in entry.hard_negatives]
    else:
        title = entry.name_zh
        aliases = entry.aliases_zh
        description = entry.description_zh
        reasons = [item.reason_zh for item in entry.hard_negatives]
    sections = [
        entry.canonical_tag,
        title,
        " ; ".join(aliases),
        description,
        "related: " + " ; ".join(entry.related_tags),
        "confusions: " + " ; ".join(reasons),
    ]
    return title, "\n".join(part for part in sections if part and not part.endswith(": "))


def build_index_documents(catalog: KnowledgeCatalogV2) -> list[IndexDocumentV1]:
    documents: list[IndexDocumentV1] = []
    for entry in sorted(catalog.entries, key=lambda item: item.canonical_tag):
        for language in (Language.EN, Language.ZH):
            title, text = _document_text(entry, language)
            documents.append(
                IndexDocumentV1(
                    schema_version="1.0",
                    document_id=f"{entry.entry_id}#{language.value}",
                    entry_id=entry.entry_id,
                    canonical_tag=entry.canonical_tag,
                    language=language,
                    primary_category=entry.primary_category,
                    title=title,
                    text=text,
                    source_refs=sorted(entry.source_refs),
                    catalog_version=catalog.catalog_version,
                    catalog_license_id=catalog.license_id,
                )
            )
    return sorted(documents, key=lambda item: item.document_id)


def build_index_documents_v3(catalog: KnowledgeCatalogV3) -> list[IndexDocumentV2]:
    """Build role-aware Phase 2 documents without indexing hard-negative reasons."""
    documents: list[IndexDocumentV2] = []
    for entry in sorted(catalog.entries, key=lambda item: item.canonical_tag):
        for language in (Language.EN, Language.ZH):
            if language is Language.EN:
                title = entry.name_en
                aliases = entry.aliases_en
                description = entry.description_en
                combination = (
                    entry.applicability.combination_note_en
                    if entry.applicability is not None else None
                )
            else:
                title = entry.name_zh
                aliases = entry.aliases_zh
                description = entry.description_zh
                combination = (
                    entry.applicability.combination_note_zh
                    if entry.applicability is not None else None
                )
            parts = [
                entry.canonical_tag,
                title,
                " ; ".join(aliases),
                description,
                "role: " + entry.tag_role.value,
            ]
            if entry.tag_role is TagRole.ATTRIBUTE and combination:
                parts.append(combination)
            documents.append(
                IndexDocumentV2(
                    schema_version="2.0",
                    document_id=f"{entry.entry_id}#{language.value}",
                    entry_id=entry.entry_id,
                    canonical_tag=entry.canonical_tag,
                    language=language,
                    primary_category=entry.primary_category,
                    title=title,
                    text="\n".join(parts),
                    source_refs=sorted(entry.source_refs),
                    catalog_version=catalog.catalog_version,
                    catalog_license_id=catalog.license_id,
                    tag_role=entry.tag_role,
                    applicability=entry.applicability,
                )
            )
    return sorted(documents, key=lambda item: item.document_id)


def publish_directory_atomically(
    staging: Path,
    target: Path,
    *,
    expected_files: set[str],
) -> None:
    staging = Path(staging).resolve()
    target = Path(target).resolve()
    if staging.parent != target.parent:
        raise AtomicPublishError("staging and target must share a parent filesystem directory")
    try:
        if target.exists():
            raise AtomicPublishError("refusing to overwrite an existing immutable release")
        if not staging.is_dir():
            raise AtomicPublishError("staging directory does not exist")
        actual = {
            path.relative_to(staging).as_posix()
            for path in staging.rglob("*")
            if path.is_file()
        }
        missing = set(expected_files) - actual
        if missing:
            raise AtomicPublishError(f"staging is incomplete: {sorted(missing)}")
        staging.replace(target)
    except Exception as error:
        if staging.exists() and staging.is_dir():
            shutil.rmtree(staging)
        if isinstance(error, AtomicPublishError):
            raise
        raise AtomicPublishError(str(error)) from error