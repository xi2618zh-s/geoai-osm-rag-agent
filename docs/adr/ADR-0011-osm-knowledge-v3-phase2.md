# ADR-0011: M03 Phase 2 uses a backward-compatible knowledge schema v3

- Status: Accepted (contracts and the Phase 2 release are complete)
- Date: 2026-10-06
- Decision owner/Session: the user approved the M03 Phase 2 classification draft and the v3 design
- Affected modules: M03; tag-retrieval consumers in later M05/M06/M11/M12

## Context

The eight-category knowledge schema v2 of Phase 1 has sealed 100 tags. M05/M06 and the flagship scenarios also need land-use and access-condition tags. Classifying `landuse=*` as a building, or treating `access=private` as wheelchair accessibility, would create wrong semantics. The user decided to add two categories, `accessibility` and `land_use`, and approved the category and role draft for all 205 tags one by one.

## Decision

1. Publish `KnowledgeCatalogV3/KnowledgeEntryV3` (`schema_version=3.0`, `release_phase=m03_phase2`), `IndexDocumentV2` and `RetrievalQueryV2` alongside the existing ones. Strict parsing of the v2 catalog and v1 documents/queries, and every Phase 1 publication, stay unchanged; new assets go only into separate, non-overwritable version directories.
2. Primary categories gain `accessibility` and `land_use`. A separate `tag_role=feature|land_use|attribute` distinguishes standalone objects, land use and additional attributes. In v3, `landuse=education` belongs to `land_use` and is not written back to v2. Dedicated buildings may be classified under scenario categories such as hospitals, hotels or stations, but a building type alone does not prove that the service is being provided.
3. An attribute must record `semantic_kind`, `applies_to_keys` and an English and Chinese combination note. `access=private` means a legal/managerial access restriction, `foot/bicycle=designated` means a mode permission, and `sidewalk=both` means a sidewalk exists; none of these is positive evidence of wheelchair accessibility. Wheelchair and kerb semantics must be expressed by their own explicit tags and cannot be inferred from other attributes.
4. Tag sources still follow the approved rules: official OSM Wiki tag pages at pinned `oldid`s, per-page status/licence review, CC BY-SA 2.0 data licence and separate attribution. The software repository licence does not change with the knowledge data. The model revision, 384-dimension normalized `IndexFlatIP`, deterministic CJK BM25 tokenizer and three-way index format are unchanged.
5. Phase 2 queries must cover every final tag: one English and one Chinese dev query per tag, and one `public_eval` query per tag isolated from dev, at least 300 in total. Dense, BM25 and hybrid each report overall, per-language/per-category macro, and original-100/new-tag cohort results. The public evaluation is not a hidden test; after freezing it is run formally only once, and its results and receipt cannot be overwritten.

## Source review results

The four redirect-only tag pages in the first raw v1 were rejected, and the rejection evidence is retained. The user then approved a strictly limited exception for exactly these four tags, using official key pages at fixed `oldid`s with a value-level `unknown` status; see [ADR-0012](ADR-0012-m03-phase2-key-page-source-exception.md). The formal raw v2 covers 205 tags from 203 distinct pages (201 tag pages, 2 key pages) and does not write back into the Phase 1/v2 source contracts. Key-page value evidence, content hashes and licence review are recorded entry by entry in ADR-0012 and raw v2.

## Consequences and acceptance

Release gates: strict v2/v3 compatibility tests, rejection of redirect-only sources, bilingual documents and role metadata, the complete tag count and per-file hashes, independent query/leakage audits, repeated builds and public-evaluation discipline. All gates have now passed: 205 tags, 615 separated queries, 7/7 index files rebuilt byte for byte in the original build environment, 235/235 offline regression tests and a single public receipt. M03 as a whole and Gate B are `DONE`; the public queries are not independently labelled by outsiders. No new dependencies are introduced, old publications are untouched, and M07/M08/M10/M11b/M11c/M12 are not started here.
