# ADR-0012: Key-page source exception for four M03 Phase 2 tags

- Status: Accepted
- Date: 2026-10-06
- Decision owner/Session: the user explicitly approved official key pages at fixed `oldid`s and the value-level `unknown` status rule
- Affected modules: M03 Phase 2 knowledge sources only; Phase 1/v2 source contracts and publications are unchanged

## Context

Among the 205 frozen candidate tags, the official tag pages for `kerb=lowered`, `wheelchair=limited`, `wheelchair=no` and `wheelchair=yes` contain, at their pinned `oldid`s, only redirect text pointing to the key page. They cannot serve as evidence for the semantics or status of the specific values. The original raw v1 keeps their `REJECTED_FOR_CATALOG_BUILD` review records, and they take no part in the catalog. The user decided to use the official key pages as the source for these four tags only, rather than dropping key accessibility semantics.

## Decision and scope

1. Only `wheelchair=yes/limited/no` map to [Key:wheelchair oldid 3062598](https://wiki.openstreetmap.org/w/index.php?title=Key%3Awheelchair&oldid=3062598), and `kerb=lowered` maps to [Key:kerb oldid 3074064](https://wiki.openstreetmap.org/w/index.php?title=Key%3Akerb&oldid=3074064). The other 201 tags keep using their own official tag pages at pinned `oldid`s, and the key-page exception cannot be widened.
2. Add `RawWikiSourceV2`: each source mapping explicitly records the actual `source_page_title/kind/id`, the pinned `oldid`, the SHA-256 of the full page body, per-value excerpts with their SHA-256, `reviewed_value_status` and `value_status_review_note`. All four state: "The key page gives no separate status for this value; value-level status=unknown, the key-page status is not inherited." `source_id` still identifies the mapping uniquely by canonical tag + `oldid`; the page identity is recorded separately in `source_page_id` and is not disguised as a tag page.
3. The `approved`/`de facto` status in a key page's infobox applies to the key as a whole and is never copied to the four values. `unknown` is allowed only for these four; the 201 tag sources keep the status reviewed on their own pages. `wheelchair=limited` only says that accessibility is limited and cannot by itself establish the exact extent; `kerb=lowered` describes a lowered kerb and cannot imply that an entire route is accessible.
4. The original `RawWikiSourceV1`, the Phase 1 fetcher and its assets keep parsing tag pages strictly. The Phase 2 v2 validator accepts the exact key page/`oldid`/value paragraph only for the four canonical tags above; formal fetching requests only those two pages at their pinned `oldid`s and checks each page for licence exceptions. Any mismatch in body hash, value excerpt, `oldid`, source identity or licence immediately blocks publication, with no automatic switch to another version or substitute tag.
5. The text data and derived knowledge carry separate CC BY-SA 2.0 attribution; this does not affect the software repository licence, and no page images or media are stored.

## Frozen mapping and review results

- New source mapping: `data/knowledge/osm/v3/candidates/m03_phase2_source_map_v2.json`, SHA-256 `26f50e2548dd9b28597bb44772b741339424f7e32b67ee45cfed21b49bad94d8`; 205 canonical tags map to 203 distinct pages (201 tag pages, 2 key pages). The SHA-256 of the original candidate list v1, `374e76afb208264dada25d218a13f82035a4973aba11bf27b9af6137f886eff4`, is unchanged.
- Key:wheelchair body SHA-256 `3edf5fd554b677a361b495477237fb811d098ccdcc5fff497f56d0a128a8807e`; excerpt SHA-256 for yes/limited/no: `7210ac9624263d2482b7c86abc1882728882d16827915cf462d063969a6972c0`, `5afff21817590287c150b52ab3cdfb3bd1e9dc7f9125b338cb7365acfd7adf10`, `819dbdef490e6b74fc367342a825d6a7ec9d3c9281dd1ae5bad14e80b3cfeedc`.
- Key:kerb body SHA-256 `38cd13ff1e15679906969a2fbaf4103fe9580de2e7fcaa33559a56ccbff9b89a`; `lowered` excerpt SHA-256 `671ca808f45506b68c0a9351496809edca22fde301123abae2b0a99c9f28e64d`.
- Formal raw v2 release directory `data/knowledge/osm/v3/raw/m03_phase2_2026-10-06_v2/`, raw SHA-256 `51efa58c3d8333dc9a9cd6b852cc142ab067db2a6991633c89258c4c44a25ab5`, manifest SHA-256 `cdde24fe44a82b09a1c8472f8cbd74b54b57b3448e39a092c8a1fa112b09deea`. 205 mappings/203 pages; statuses approved 61, de_facto 128, in_use 11, obsolete 1, unknown 4; separate licence exceptions 0; the per-file manifest verifies 2/2. Data-licence attribution is in `ATTRIBUTION.md` in the same directory.

## Acceptance and risk

Still to be verified at the time: v2/v3 compatibility, a one-to-one match between catalog entries and raw sources, no spread of the four `unknown` statuses, bilingual curation and semantic hard negatives, and all indexes/queries/evaluation and repeated builds. A pinned Wiki version only proves what the documentation said about a value at that version; `unknown` means no separate value status was found, not an inference that the value is approved, obsolete or accessible in reality. The 205 tags, bilingual knowledge and indexes, 615 queries, three-way evaluation and repeated build in the original build environment have since passed; the M03 module is `DONE`.
