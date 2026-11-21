# ADR-0008: M03 Phase 1 uses strict bilingual knowledge contracts, pinned OSM Wiki sources and versioned hybrid indexes

- Status: Accepted
- Date: 2026-10-05
- Decision owner/Session: approved by the user; M03 OSM knowledge expansion
- Affected modules: M03, the existing RAG retrieval compatibility boundary, future M07/M11
- Scope: this ADR approves M03 Phase 1 only; M03 as a whole stays IN_PROGRESS until it reaches 200+ tags

## Context

The existing knowledge assets contain only 21 unique OSM tags and 144 English Wiki chunks. The raw data, catalog and FAISS index can be reproduced with the current cleaning/chunking logic, but they lack strict public contracts, Chinese fields, applicable object types, per-entry source versions and licences, hard negatives, input-manifest hashes, atomic publication and an independent bilingual evaluation. The historical 63 P1 queries and 21 P3 challenge queries were written by the project author, and the challenge set was used for tuning, so neither can serve as an independent M03 test.

## Decision

1. Phase 1 is fixed at 100 unique canonical `key=value` tags: the existing 21 are kept and 79 are added. Each has exactly one `primary_category` and may have several `related_categories`. Category quotas: food and drink 14, retail 16, healthcare 12, education 10, transport 18, public services 12, leisure 10, buildings 8.
2. The candidate list is frozen and its SHA-256 recorded before fetching. The only content source is official OSM Wiki tag pages; Taginfo, Wikidata and third-party material are not used. Missing pages, unexpected redirects, unknown sources or unconfirmable status are rejected. `aeroway=airport` is kept only for compatibility.
3. Network fetching stores the original request URL, canonical URL, `oldid`, history link, UTC fetch time, ETag, Last-Modified, Content-Type, body SHA-256 and a separate licence-notice check. Only text is processed; media is neither fetched nor redistributed.
4. The OSM Wiki raw snapshot, the derived catalog and project-curated fields are published together as CC BY-SA 2.0 knowledge data with a separate attribution/licence notice; this does not change or imply a licence for the software code. If a page declares a separate licence, automatic ingestion must fail and wait for manual licence review.
5. Add four strict, versioned, `extra=forbid` contracts: raw source, normalized knowledge entry, index document and retrieval evaluation query. Stable IDs, the canonical tag, ordering, UTF-8/LF, Unicode NFKC, canonical JSON serialization and the SHA-256 algorithm are part of the contract.
6. The canonical tag must be exactly one `key=value`. Deduplication is based only on the canonical tag. English and Chinese names, aliases and descriptions are checked for language, empty values, duplicates within an entry, cross-tag conflicts and canonical conflicts; Phase 1 allows no alias-conflict exemptions.
7. Every knowledge record has 1–3 asymmetric hard negatives, recording the wrong tag, the English and Chinese confusion reasons and the source (`wiki_hint|manual_semantic_review`). The target must be another tag in the same catalog; self-references, duplicates and random unrelated candidates are not allowed. Hard-negative content never enters the target entry's positive index text.
8. Raw data, the normalized catalog, index documents, evaluation queries and the index release are stored in separate directories. Version directories are immutable, and staging and target directories must be on the same file system. Publication is atomic and happens only when there are 100/100 entries, a manifest, 200 documents, a matching FAISS vector count and all hashes agree. Overwriting is refused by default, and a failure cleans up only this run's staging.
9. Each tag produces one English and one Chinese `IndexDocumentV1`, 200 documents in total. Dense, BM25 and hybrid retrieval read the same deterministically sorted JSONL.
10. The dense model is fixed to `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2`, with a pinned Hugging Face commit, sentence-transformers version, normalization settings, 384 dimensions, a hash of key model configuration and the tokenizer version. Model weights are not committed; an offline rebuild must hit the already downloaded pinned revision and must never fetch "latest".
11. FAISS keeps using `IndexFlatIP` on normalized vectors in binary format; the old model and the v1 index are not modified. The new release uses its own directory and a strict build manifest.
12. BM25 uses a deterministic tokenizer with no third-party dependencies: Latin text is casefolded and lightly stemmed as before; each run of consecutive Han characters produces Unicode code-point unigrams and adjacent bigrams; mixed queries keep both Latin and CJK tokens. The tokenizer version is written to the manifest.
13. The Phase 1 evaluation is fixed at 300 queries: 200 visible dev queries (one English and one Chinese per tag) and 100 `public_eval` queries (one challenge per tag). Nothing is written to a future hidden test directory, and this set is not called a hidden test.
14. `public_eval` is run formally once after the configuration is frozen and writes a non-overwritable receipt. Any tuning after seeing the results must create a new experiment version marked as influenced by `public_eval` feedback. 25 alias challenges may use catalog aliases; the other 75 must not restate the target entry's name or alias.
15. Reports include dense/BM25/hybrid Recall@1, Recall@5, MRR, whether the target is top-1, hard-negative accuracy, per-category/per-language macro metrics and cold/warm-start latency. Hard-negative accuracy means the target tag ranks above all of the query's excluded tags.
16. After Phase 1, record "M03 Phase 1 DONE", but M03 as a whole and the M03 part of Gate B stay IN_PROGRESS. Once 200+ tags are reached, sources, licences, bilingual content, hard negatives, indexes and evaluation must be completed again before M03 can be considered DONE.

## Canonical serialization and stable IDs

- Text is first normalized with Unicode NFKC; control characters are forbidden; strings are trimmed, and internal whitespace in aliases is collapsed.
- Catalog entries are sorted by canonical tag; related categories, aliases, relations and hard negatives are sorted by their own canonical keys.
- Canonical JSON uses UTF-8, LF, `ensure_ascii=false`, lexicographically sorted object keys and no non-semantic whitespace; JSONL has one canonical object per line, each ending with a single LF.
- The entry ID is `osm-tag:<canonical_tag>`; the source ID is `osm-wiki:<canonical_tag>@<oldid>`; the index document ID is `<entry_id>#<en|zh>`; query IDs use a fixed release prefix, the canonical tag slug, the language and the query type.
- Manifest and content hashes are lowercase hexadecimal SHA-256 of the canonical bytes and must not contain absolute paths.

## Licence and attribution

Every source keeps at least the pinned page `oldid`, the page title, "OpenStreetMap Wiki contributors", the CC BY-SA 2.0 licence link and a note on the project's modifications/curation. Page media never enters the raw or derived assets. If the official licence page, the page footer or the page body shows a separate licence exception, automatic fetching stops with a stable licence error.

## Error layers

- Network/transport: `OSM_KB_FETCH_FAILED`
- HTTP, redirects or Content-Type: `OSM_KB_HTTP_RESPONSE_INVALID`
- Page/oldid/licence structure: `OSM_KB_SOURCE_SCHEMA_INVALID`, `OSM_KB_LICENSE_EXCEPTION`
- Knowledge contract and semantics: `OSM_KB_SCHEMA_INVALID` plus specific canonical, alias, language, relation and hard-negative errors
- Manifest/index/build: `OSM_KB_MANIFEST_HASH_MISMATCH`, `OSM_KB_INDEX_METADATA_INVALID`, `OSM_KB_BUILD_FAILED`
- Evaluation boundary: `OSM_KB_EVAL_LEAKAGE_DETECTED`, `OSM_KB_PUBLIC_EVAL_ALREADY_RUN`

Network, HTTP, schema, licence, build and evaluation errors must never be downgraded into one another.

## Alternatives

- Keep the English `all-MiniLM-L6-v2`: rejected as the v2 default, because it cannot provide a credible baseline for English/Chinese dense experiments.
- Upgrade only the dense model and keep the current Chinese BM25 tokenization: rejected, because consecutive Chinese text would become a single token and hybrid Chinese experiments would be uninterpretable.
- Fill in frequencies or fields from Taginfo/Wikidata: not approved for Phase 1, to keep the source and licence surface small.
- Overwrite the v1 raw data, catalog, index or historical results in place: rejected; breaks rollback and the evidence chain.
- Call `public_eval` a hidden set: rejected; it is a public evaluation, visible in the repository and run once after the configuration is frozen.

## Consequences, migration and rollback

M03 v2 is implemented in parallel version directories; v1 assets, old indexes, old benchmarks and runtime interfaces are kept. The retrieval default may switch only after the v2 build, evaluation and the compatibility regression on the old 21 tags pass. Rolling back means restoring the v1 configuration; published v2 releases and evaluation receipts must never be deleted or rewritten.

## ADR-0008 implementation and acceptance notes

- 2026-10-05: the final Phase 1 publication manifest passed the gates for 100 raw / 100 catalog / 200 index documents / 200 FAISS and BM25 entries, 300 queries and per-file hashes; M03 as a whole stays IN_PROGRESS.
- The official HTML requests returned 403, so fetching used the same-domain MediaWiki `action=parse` API with pinned `oldid`s; the approved source was not widened. Pages showed the status `in_use`, so the frozen enumeration includes that value.
- On Windows, model encoding and FAISS writing run in separate processes to avoid a PyTorch/FAISS OpenMP co-loading crash; the model, FAISS format and configuration remain as decided in this ADR.
- The build manifest uses the fixed epoch 2026-10-05T00:00:00Z for byte-level reproducibility; it is not the actual execution time. The final sealed field `published_at` reuses that epoch; later versions should rename it `build_epoch` and store the real publication time separately.
- `public_eval` was run exactly once; the frozen configuration and the non-overwritable receipt are retained. Detailed metrics, error types and the entry point for the next phase are in the M03 Phase 1 handoff.
