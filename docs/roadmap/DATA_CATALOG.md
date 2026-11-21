# Data catalogue

> Every data entry must record its source, licence, version, spatial/temporal extent, schema, verification method and offline fixture. M00 registers only data that actually exists; later data must never exist only on a local disk or in a conversation.

## Current data assets

| ID | Type | Location | Version/size | Source and licence | Commit/test policy |
|---|---|---|---|---|---|
| `osm-sweden-pbf` | OSM PBF | default `data/osm/sweden-latest.osm.pbf` | `latest` URL, mutable; the download receipt records the checksum | Geofabrik / OSM ODbL 1.0; see `data/osm/source_manifest.json` | The large file is excluded by `.gitignore`; offline unit tests use mocks/small hand-made objects. |
| `osm-wiki-raw-v1` | JSONL text | `data/wiki_raw/wiki_raw.jsonl` | 21 lines; SHA-256 `8e64e0083d147f0471d0ebe0a072df127910d96229fa9799c12aedf6edc8a36b` | OSM Wiki page snapshots; the source URL is kept per entry | Committed; used to rebuild the index. |
| `osm-tag-catalog-v1` | JSON | `data/knowledge/tag_catalog_v1.json` | `2026-08-05.v1`; 21 unique tags | Generated in structured form from the local OSM Wiki snapshot | Committed; kept compatible until the M03 extension. |
| `faiss-index-v1` | Dense index + metadata | `faiss_index/` | metadata schema v1 | Built from the Wiki raw data; model configuration in the metadata | Committed; evaluate Git LFS if the binary keeps growing. |
| `benchmark-query-v1` | JSONL | `benchmarks/data/query_benchmark_v1.jsonl` | 63 queries, 21 labels | Written by the project author | Development baseline; must not be called a hidden set. |
| `benchmark-p3-challenge` | JSONL | `benchmarks/data/query_benchmark_p3_challenge.jsonl` | Historical P3 challenge set | Written by the project author | For development/error analysis. |
| `benchmark-workflow-v1` | JSONL | `benchmarks/data/workflow_benchmark_v1.jsonl` | 12 planning tasks | Written by the project author | Development baseline, not an independent hidden test. |
| `geodsl-contract-v1` | JSONL | `data/fixtures/geodsl/v1/` | 30 valid + 35 invalid contract fixtures | Hand-made, no external/personal data | Committed; used for strict validation, round-trip, conflict and missing-parameter tests. |
| `dataset-registry-fixtures-v1` | GeoJSON/CSV | `data/fixtures/datasets/v1/` | 3 features + 3 table rows, plus 2 invalid files | Hand-made, no external/personal data | `manifest.json` pins SHA-256, kind, CRS, row count and bbox; used for M02 offline registration/tampering tests. |
| `benchmark-m11a-dev-v1` | JSONL | `benchmarks/data/dev/m11a_*_v1.jsonl` | 24 tasks + 24 calibration predictions; 7 GeoDSL operation types | Hand-made by the project author, no external/personal data | Public dev calibration set; the predictions deliberately contain eight error types, and 15/24 only validates the scorer, not system performance. |
| `m04a-tool-fixtures-v1` | GeoJSON | `data/fixtures/tools/v1/` | 3 files: 3 valid mixed geometries, 1 self-intersecting Polygon, 1 empty collection | Hand-made by the project author, no external/personal data | `manifest.json` pins SHA-256, row count and purpose; used for schema/topology/empty-data and immutability tests. |
| `m04b-spatial-tool-fixtures-v1` | GeoJSON | `data/fixtures/tools/m04b/v1/` | 7 files: projected source points/target points/3 regions/aggregation values, longitude/latitude points, a self-intersecting Polygon, an empty collection | Hand-made by the project author, CC0-1.0, no external/personal data | `manifest.json` SHA-256 `4b164ef0e48477c773604436f118fed6be997e34dc15e9f7d85604cacfb80321`; per-file SHA-256, CRS, row count and purpose pinned. |
| `m04c-tool-composition-reference-v1` | GeoJSON + JSON | `data/fixtures/tools/m04c/v1/` | 6 files: 3 WGS84 facilities, 3 EPSG:3857 regions, empty/invalid inputs, a stored final result and intermediate reference metrics | Hand-made by the project author, CC0-1.0, no external/personal data | `manifest.json` SHA-256 `0a365ec466011a29c859be864b5f64857b8736ded8d57877c0f999453947391b`; per-file SHA-256, CRS, row count and purpose pinned. |
| `scb-population-grid-2025-fixture-v1` | GeoJSON + JSON | `data/fixtures/population/scb/v1/` | 16 EPSG:3006 1 km population cells, 1 project-author POI, 1 expected reference result | Statistics Sweden `stat:befolkning_1km_2025` / CC0-1.0; project-author files under the same licence | WFS 1.1.0 query with a pinned BBOX, attributes and objectid ordering; raw GeoJSON SHA-256 `2a8445da8e4eb41baabab41f18d0fc8018621f21c2338737ace88ac0efe09b56`; manifest SHA-256 `da4c29e1d04b155f0d774b9cf40f9cc5e8ff8e31a8e53a2f96196bc6e533d569`, also recording schema/response hashes, fetch time and empty ETag/Last-Modified. |

## Fixture rules

- New fixtures live in `data/fixtures/<domain>/<version>/` with a note on the source or hand construction, licence, CRS, schema, generation command and checksums.
- Fixtures must be small, deterministic and usable offline; real large downloads must never be renamed and committed.
- Raw downloads, model caches, PBFs and run outputs stay protected by `.gitignore`.
- M02 established the runtime `DatasetRef`; IDs are generated at each registration, while a fixture's stable identity is still its manifest SHA-256. Random run IDs are never written into this catalogue.

## Current gaps

- There are SCB 2025 population-grid and M06a Lund GTFS micro-fixtures; there are no DeSO/PxWeb administrative joins, user uploads or raster fixtures yet, and no runtime backend that can register the full national GTFS directly.
- The Sweden `latest` PBF is a mutable source; real runs must rely on the download receipt, and historical object counts must not be treated as permanent facts.
- The v1 historical knowledge scope is 21 tags; the 100 tags of M03 Phase 1 v2 remain immutable. Phase 2 v3 has published 205 tags, and the complete index rebuilt exactly 7/7 in the original threading environment; M03 and Gate B are DONE. The public queries are not independently labelled by outsiders.

## M03 Phase 1 OSM knowledge assets (2026-10-05)

| ID | Location and size | Source, licence and version | Verification and commit boundaries |
|---|---|---|---|
| m03-candidates-v1 | `data/knowledge/osm/v2/candidates/`; 100 unique key=value tags, including the old 21 | Project-curated frozen candidates; SHA-256 `773aaae4357ff4d97e034013d946528324c1e7ed1c16608fb8d94c2e6148b814` | Frozen before fetching; unique primary quotas for the 8 categories: 14/16/12/10/18/12/10/8. |
| m03-raw-v1 | `data/knowledge/osm/v2/raw/m03_phase1_2026-10-05_v1/`; 100 pages | Official OSM Wiki tag pages at pinned `oldid`s; CC BY-SA 2.0; raw_sources SHA-256 `2daed34309ceea229fdea739909957d0dd784f5557834ab22ee0b33af599ebb4` | manifest SHA-256 `3f1aae54404f0b119c8ff8e83cfba519caf5958303fb982b747d642ce119f8dd`; per-page URL, `oldid`, HTTP time/metadata, body hash and licence exceptions; text only. |
| m03-curation-v2 | `data/knowledge/osm/v2/curation/`; 100 entries | Project curation assisted by Codex; CC BY-SA 2.0 with the knowledge data; SHA-256 `2b0232e0d4440dc8c99c4e28674259551c090e80fd88ad8c3c2e3d07bef4eca8` | The v1 first draft was rejected for unknown hard negatives, with the failure evidence kept; only the v2 sidecar hash may take part in builds. |
| m03-catalog-v2 | `data/knowledge/osm/v2/releases/m03_phase1_2026-10-05_v1/`; 100 entries, 200 en/zh documents | Derived from the official Wiki/project curation; CC BY-SA 2.0; catalog SHA-256 `c624eef08aacbc7d1c5b87e357884039399680b23ba89b8833b0c360191e29d0` | Documents SHA-256 `8cdfb64ce839f527a0e237086997d71c465b10b157e0e248b3bef47afadb0056`; input/output manifests; version directory cannot be overwritten. |
| m03-index-v2 | `faiss_index/m03_phase1_v2_e8f8c211/`; 200×384 FAISS + 200 BM25 documents | Pinned multilingual MiniLM commit `e8f8c211226b894fcb81acc59f3b34ba3efd5f42`; IndexFlatIP/Apache-2.0 model; knowledge documents CC BY-SA 2.0 | index manifest SHA-256 `80da3952335387d5bcdde44b8d8d92bfa36b3c5f167bdd579b60f959df1ab5f2`; model weights outside the repository, with a receipt of 11 model-file hashes. |
| m03-query-v1 | `benchmarks/data/m03/v1/`; dev 200 + public_eval 100 | Written by the project author with Codex assistance; source notes in each entry's `construction_note` | `public_eval` is visible and not hidden; dev/public JSONL SHA-256 `8f97e94dfaeb920f73f7761bbe56da0ca26e007ddba7be1554ed53f42bac4b50` / `6cba7a762dc738f613caadf02c7003210e1ae5d169af9e8f62ca4814cf4b560d`; leakage audit 0. |
| m03-publication-v1 | `data/knowledge/osm/v2/publications/m03_phase1_2026-10-05_v1/` | Final read-only seal; manifest SHA-256 `1312a3456169a9e432321d2223672a77111e745f5e818c35cab17599e1c1b19b` | Verifies 100 raw/catalog, 200 documents/FAISS/BM25, 300 queries, index/evaluation receipts and per-file SHAs; Phase 1 DONE. This row keeps the facts at the time of sealing; M03 as a whole is now DONE. |

The data licence covers only the OSM Wiki text snapshots and the curated knowledge data derived from them; it does not change the software repository licence. M06a has added a small GTFS fixture; the full national GTFS, large knowledge-model weights and third-party OSM tag material are not in the repository. The manifests' 2026-10-05T00:00:00Z is a fixed logical epoch for reproducible builds; raw/model receipts keep the actual fetch times.

## M06a pinned GTFS Sverige 2 source and fixture (2026-10-05)

| ID | Location and size | Source, licence, version | Verification and commit boundaries |
|---|---|---|---|
| gtfs-sverige2-20251004-source | Task cache outside the repository; 47,699,395 bytes | Official Trafiklab/Samtrafiken historical archive `sweden-20251004.zip`; CC0-1.0; feed_version 2025-10-04; Europe/Stockholm | Pinned URL, ETag `"68e1dc39-2d7d5c3"`, Last-Modified Sun, 05 Oct 2025 02:47:21 GMT, SHA-256 `12a4a7169d8423641da7f15125092423f6f9834f3b70725719b1ce147eb48f69`; the original ZIP is not committed. |
| gtfs-sverige2-lund-fixture-v1 | `data/fixtures/gtfs/sverige2/lund_20251004_v1/`; ZIP 3,688 bytes, 5 trips, 42 stops, 128 stop_times | A real row subset of the same official archive, selected by trip ID with complete reference chains; CC0-1.0; fetched 2026-10-05T07:46:44.196389Z | ZIP SHA-256 `bcf104e23e5cd5d73414b50be0de61644c69c96e814d89c4fb451c5a686cf0a7`; README SHA-256 `b30dbd532f7b1ca447535290a6b20099adec23b977141024779ff09d8b8bf138`; manifest SHA-256 `9cd1e7bf6d993447da5c3a04ab677610d26eb69e4c9931a03adbfba19442630a`. The manifest records source/licence/version/time/per-table hashes; repeated builds from the pinned source reproduce all three files byte for byte. |

Official data and licence description: https://www.trafiklab.se/api/gtfs-datasets/gtfs-sverige-2/ ; pinned historical download: https://data.samtrafiken.se/trafiklab/gtfs-sverige-2/2025/10/sweden-20251004.zip . The fixture is only a contract sample, and five trips cannot be used to infer real Lund service frequencies; the full national ZIP exceeds the bounded ingestion limit of M06a.

## M06b hand-made semantic and spatial reference fixture (2026-10-05)

| ID | Location and size | Source, licence, version | Verification and usage boundaries |
|---|---|---|---|
| m06b-stop-coverage-v1 | `data/fixtures/gtfs/m06b/v1/`; 4 stops, 2 routes, 5 trips, 3 population cells, 3 POIs, two reference time windows | Hand-made by the project author, CC0-1.0; GTFS EPSG:4326 / spatial EPSG:3006; service day 2025-10-04 | manifest SHA-256 `e74d95aca7dadcf52ae2668d5de000b196a4fe7dcd2a17ad7d4e7296abe681b6`. schedule.gtfs.zip `9ceb5a457d72d4bd7248e65e67302c992207d7c4788828574c382181e37fb463`; population.geojson `7d827e68a631da91009253445a574d8a1aa1681dad4de408cc6ef28526a5e5d7`; pois.geojson `d3b1ff8b25d5a078a70844fe1279cabf7f012082ab57c57e10fccccec2b7f909`; expected.json `a9f45646765f835336e84576b4baca30d2a2e6b19ad39505b2677708f925dbc5`; README.md `237d1b87874142ac41bdf7e0d37d62a867293720df77f3273cd4deb44ac335bd`. The offline script refuses to overwrite, uses same-disk staging, and rebuilds every file identically. |

The hand-made fixture's morning [08:00,09:00) and night [24:00,25:00) windows serve only as a contract reference: the fully-contained-cell population lower bounds of known values for 250 m buffers are 130/30, and the unique POI counts 2/1; they must not be presented as real Lund service levels. The real official M06a GTFS subset is kept separately and unchanged, and the full national archive is still not in the repository.

## M03 Phase 2 first source snapshot (rejection evidence)

- Non-overwritable candidate list v1 corresponding to the approved classification draft: `data/knowledge/osm/v3/candidates/m03_phase2_candidates_v1.json`, 205 unique tags (the original 100 + 105 new), SHA-256 `374e76afb208264dada25d218a13f82035a4973aba11bf27b9af6137f886eff4`.
- `data/knowledge/osm/v3/raw/m03_phase2_2026-10-05_v1/` stores 205 snapshots of official tag pages at pinned `oldid`s (reusing the old 100, fetching 105 new), only as source-review evidence. `source_review/m03_phase2_v1_review.json` marks it `REJECTED_FOR_CATALOG_BUILD`: four pages are only redirect stubs pointing to a key page. The status of the other 201 pages was reviewed; this raw v1 does not constitute a publishable 205-tag catalog.
- Wiki text and derived knowledge data are CC BY-SA 2.0 with separate attribution; this does not imply that the software repository adopts that licence. The four explicit key-page exceptions approved by the user went into a separate raw v2; Phase 1 and the old 21-tag assets stay where they are.

## M03 Phase 2 v3 assets (2026-10-06)

| ID | Location and size | Source, licence and version | Verification and boundaries |
|---|---|---|---|
| m03-phase2-candidates-v1 | `data/knowledge/osm/v3/candidates/m03_phase2_candidates_v1.json`; 205 unique tags = the original 100 + 105 new | Frozen after the user approved the per-tag classification/role draft; SHA-256 `374e76afb208264dada25d218a13f82035a4973aba11bf27b9af6137f886eff4` | 10 categories; feature 190, attribute 8, land_use 7. |
| m03-phase2-source-map-v2 | `data/knowledge/osm/v3/candidates/m03_phase2_source_map_v2.json`; 205 mappings/203 pages | SHA-256 `26f50e2548dd9b28597bb44772b741339424f7e32b67ee45cfed21b49bad94d8` | 201 tag pages and 2 key pages at pinned `oldid`s approved by the user; the value status of four tags is `unknown`. |
| m03-phase2-raw-v2 | `data/knowledge/osm/v3/raw/m03_phase2_2026-10-06_v2/` | Official OSM Wiki text at pinned `oldid`s, CC BY-SA 2.0; raw SHA-256 `51efa58c3d8333dc9a9cd6b852cc142ab067db2a6991633c89258c4c44a25ab5` | manifest SHA-256 `cdde24fe44a82b09a1c8472f8cbd74b54b57b3448e39a092c8a1fa112b09deea`; 0 per-page licence exceptions; the old raw v1 remains the rejection evidence. |
| m03-phase2-curation-v1 | `data/knowledge/osm/v3/curation/m03_phase2_new_tags_v1.tsv`; 105 new bilingual curated entries | Project author with Codex assistance, SHA-256 `9360349004ca2df1cc494e9ac80b7d2ac98c589ed71a399cf4188793e819bb7a` | The old 100 catalog entries are kept; every new hard negative points to a tag in the catalogue. |
| m03-phase2-catalog-v3 | `data/knowledge/osm/v3/releases/m03_phase2_2026-10-06_v1/`; 205 entries/410 English and Chinese documents | Knowledge data derived from the official Wiki and curation, CC BY-SA 2.0; catalog SHA `35a98117071d4f4f3e74f641504f6c610c43b9e7071e7f943be8bfaac63d0a72` | Documents SHA `f4e1f69b5a89bf2ba02363f0d9cf4cf505c9e3f9f76e864c9de798061c80f75f`; the manifest's 3/3 files and 6/6 input hashes pass. |
| m03-phase2-index-v3 | `faiss_index/m03_phase2_v3_e8f8c211/`; 410×384 | Pinned multilingual MiniLM revision `e8f8c211226b894fcb81acc59f3b34ba3efd5f42`; IndexFlatIP/normalized, CJK BM25 tokenizer v1 | Published FAISS SHA `45dfbfd35d6cf0a9d0a13662900d171844402de6c1edb3226731ef51a1db7b6e`; 7/7 files rebuilt with identical hashes in the original build's default threading environment; in a single-threaded environment one vector row differs only in its last digit, and the original was not overwritten. |
| m03-phase2-query-v1 | `benchmarks/data/m03/phase2_v3_2026-10-06_v1/`; dev 410/public_eval 205 | Project author with Codex assistance; the public questions are not a hidden test | All 205 tags, original 100/new 105; dev SHA `a468718cd9d3965660934ba5e2fbf49dc63320645489abbbebd1836ebc578b0d`, public SHA `5a77e2f27628d0b326b182d6cc100abdb3353fe7338ecdd34feb07a136088c4a`; scenario questions are rewritten from catalog semantics. |

Repeated-build audit evidence `data/knowledge/osm/v3/audits/m03_phase2_reproducibility_v1.json`, SHA-256 `c758575080ea07bcfef868593f6e0af5d2aa40ccc1289324aa0a23baf035e567`; in the original default threading environment the catalog/query/index match byte for byte 4/4, 4/4 and 7/7. Wiki text and derived knowledge data carry separate CC BY-SA 2.0 attribution; the software repository does not adopt that licence because of it. The value-level `unknown` applies only to the four key-page exceptions; `aeroway=airport` is deprecated and kept only for compatibility. Model weights are outside the repository, and no dependencies are added.

## M07 planning evaluation data

- `benchmarks/data/dev/m07_plan_cases_v1.jsonl`: 80 public synthetic cases written by the project author; SHA-256 `81fe96d28953c8ec99d1eb999905b2e13b0854bad2f92a7f6623a39bf5df9ad8`. Only restricted English commands and role metadata, without real location or personal data.
- `benchmarks/data/dev/m07_plan_cases_v2.jsonl`: keeps all v1 cases and adds 10 sum aggregations, 90 in total; SHA-256 `6df293d8c74810e042d7bedb472f89a5e32ed0b7b264e7b1586aade98d6b06ee`. v2 is a later tightening of coverage, not an independent hidden set.
- The v1/v2 JSON results in `benchmarks/results/m07/dev/` cannot be overwritten. The catalog-informed limitation of the M03 Phase 2 retrieval queries is not changed by the new M07 evaluation.

## M08 public fault evaluation data

- `benchmarks/data/dev/m08_fault_cases_v1.jsonl`: 16 synthetic cases written by the project author and frozen before development, including four clean tasks, four recoverable faults and eight guarded faults; SHA-256 `fbd3b3cccea9ef2de5cedb1050c3ac23c5e0237317ae53d2463158f55f76b3eb`. Uses the existing small M04c GeoJSON and runtime injection, with no real personal/transit data.
- `benchmarks/results/m08/dev/m08_fault_eval_v1.json` is an exclusively created public development result, SHA-256 `ff1d14c092d61da1ddfbaa4f42c1441229374e9a62e607f352a0145dab249671`; it cannot be called a hidden set or production reliability. The M03 Phase 1/2 publications and evaluation receipts stay in place, and the Phase 2 queries remain catalog-informed.

## M09 multi-turn evaluation data

- `benchmarks/data/dev/m09_turn_cases_v1.jsonl`: 16 public synthetic multi-turn cases frozen by the project author before production code, SHA-256 `86206699500bfe6b4e64778e2df6653f7f4f8b8653cd9dacb8e8114db8525f19`. Uses the existing small M04c GeoJSON and builds one-feature "new city" data in a test temporary directory. City names are synthetic task labels and imply no real geographic coverage or external source.
- `benchmarks/results/m09/dev/m09_turn_eval_v1.json`: an exclusively created development evaluation receipt, SHA-256 `a9ec2e18f46d5c5cf88eff0f74f66c93839b2924c1df4076baecac2aa5a7af29`, cannot be overwritten. The old M03 Phase 1/2 and M07/M08 results are still in their directories; the source limitation of the M03 Phase 2 queries is unchanged.

## M10 public synthetic result assets

- `benchmarks/data/dev/m10_result_cases_v1.jsonl`: 12 synthetic cases written by the project author and frozen before production code; four each for search, coverage and verified count comparison; SHA-256 `5cea7142f425213039cc485ab291b1532d20e4cd7328a90bedb08fab3b1b651d`. Based on the existing small M04c GeoJSON fixture and offline M07/M08 runs, without real cities or personal data.
- `benchmarks/results/m10/dev/m10_result_eval_v1.json`: an exclusively created public development evaluation receipt, SHA-256 `59769b945cd4abaff02d8216d8bdc603efee5e39c5ba3c0fa90157e2385c4ed4`; all four metrics 12/12. `m10_demo_result_v1/v2.json` and `docs/demo/m10_analysis_result_v1/v2.html` are kept as non-overwritable snapshots, with v2 currently shown. v2 result/HTML SHA-256 are `bb619c6c192f788e69db0d2e144774cf15a3059c9a87cb40ac544585caef8109` and `5e5556786ebbc2c8bfb6213ebe506a9393edd88e35555c3a2a2e035644bc5746`; the script audit checks them byte for byte.
- The M03 Phase 1/2 publications and public evaluation files were not overwritten; the Phase 2 queries remain catalog-informed internal questions. M10 fixture place names are only examples; projected GeoJSON uses `geoai_crs` and must be reprojected according to the consumer's specification.

## M11b versioned benchmark data (2026-10-07)

| ID | Location/size | Source and licence | Verification and boundaries |
|---|---|---|---|
| m11b-public-dev-v1 | `benchmarks/data/dev/m11b_tasks_v1.jsonl`; 160 tasks: 144 executable, 8 unsupported rank, 8 clarification | Synthetic, written by the project author, CC0-1.0; the M04b v1 synthetic fixture under the same licence | Build seed 1107; task SHA-256 `6c7f2ab5d09581642918ccda6f0f7cba90cbcb25ab9b7427d6c68596dc2d94fb`; `m11b_manifest_v1.json` records source hashes/tool versions; can be rebuilt byte for byte with real M04 calls. Public dev, not to be called hidden performance. |
| m11b-private-test-v1 | `benchmarks/data/test/private/`; 40 tasks, private seed/source geometry/answers, git-ignored; only `m11b_private_receipt_v1.json` is public | Synthetic geometry transformed by the project author, CC0-1.0 | The receipt gives only the private task and manifest SHAs, version/count/source, without queries, answers or seed. The private files can be rebuilt in the current workspace; a fresh clone needs a secure handover of the private package before auditing, and they never enter development fixtures/prompts/knowledge aliases. |

The 24 M11a development calibration tasks and their original faulty predictions are kept, and the v1.0 contract and results are unchanged. M11b has 200 tasks in total, but the public and private synthetic variants are related and do not constitute an independent external geographic source. The M03 Phase 2 public queries remain a catalog-informed internal evaluation, and GTFS stop buffers are not transit isochrones.

## M11b real M04 call evidence (supplement)

- `benchmarks/data/dev/m11b_tool_traces_v1.jsonl` is a per-step call manifest for the 160 tasks; the 144 supported tasks record, for every step, the existing M04 tool name/version, input content hashes, parameter hash, output content hash and row count, and the 16 non-executed tasks have empty call lists. Random DatasetRef IDs and timestamps are not in the published file.
- Manifest SHA-256 `6ce6efb14c988e0560ed7db59d2cd45130b544acdfd1d8e7ea3531a794d0c388`; exclusive receipt `benchmarks/data/dev/m11b_tool_traces_receipt_v1.json` SHA-256 `e28ebb6808250fff58fda4b3692473e62ca0a6ccac7620fd0a115f9273587eab`. `scripts.build_m11b_trace_provenance` rebuilds it byte for byte from real calls, and `scripts.audit_m11b_reproducibility` audits this evidence too.

## M11c evaluation artefacts (2026-10-07)

| Version/path | Contents | Notes |
|---|---|---|
| `benchmarks/results/m11c/prefreeze/20261006T203100Z-dev-prefreeze/` | Public dev pre-freeze capability matrix and a single validation run of the two agents | Development evidence, not a formal comparison; no private data read. |
| `benchmarks/results/m11c/freeze/` | `m11c_dev_capability_matrix_v1.jsonl` (`66afad17…40c5`), `m11c_freeze_manifest_v1.json` (`8c512e4b…a15c`) | Frozen exclusively before the formal runs; contains system configuration, seeds, data/catalogue hashes and the source fingerprint. |
| `benchmarks/results/m11c/formal/m11c_comparison_v1/` | report.json/md, deterministic_receipt.json, dev_task_results.jsonl, dev_predictions, m11b_reports | Public dev has per-task results; private is aggregate only, without queries, Feature IDs, per-task scores or seeds. |

- Private data catalogue: regions/sources/targets.geojson match the source-file hashes in the private manifest; the private package declares no CRS, so the evaluator only extracted the EPSG token from the hidden builder and declared EPSG:3857. Public-output leakage scan: 0 hits for private queries, task IDs, Feature IDs unique to the private set and seeds (only tool-derived IDs identical to public dev, such as `nearest-0-0`).

## M11c v2 data and evaluation artefacts (2026-10-07)

| Version/path | Contents | Notes |
|---|---|---|
| Question-writing workspace outside the repository (passed only via `--workspace` or `GEOAI_M11C_PRIVATE_V2_DIR`) | catalogue.json, 8 project-synthetic CC0 GeoJSON files, task_specs.json, `m11c_private_tasks_v2.jsonl` (`23512c03…e9db`), `m11c_private_manifest_v2.json` (`9c491ce8…1e28`), SEALED.json, REVIEW_GUIDE.md, per-task records and raw responses of the private run | Not readable by the development agent; isolation is procedural under the same operating-system user. |
| `benchmarks/data/m11c/m11c_private_v2_receipt.json` | Public receipt of private v2 (`5e474b3a…b13d`) | 48 tasks as 8×6, 36 supported (32 with non-empty feature sets), self-check 48/48, 0 query overlap with public dev/private v1; contains only counts, distributions, checks and hashes; sealed by the user. |
| `benchmarks/data/dev/m11c_v2_dev_paraphrase_probe_v1.jsonl`, `…_v2.jsonl` | 40 paraphrase probes each, derived from public dev and written by the development agent (task_id + query, with the original dev expectations) | Development diagnostics only, not part of the gate; no exact query overlap with public dev or private v1 (M11b audit). |
| `src/agent/prompts/m11c_frontend_v2_prompt.txt` | Frozen prompt (`be9ea50f…a51f`, computed after LF normalization) | Examples come from public dev and dev-derived paraphrases. |
| `benchmarks/results/m11c_v2/dev_iterations/` | iter01, iter01_probe, iter02, iter02_probe, iter03_probe2 | Prompt-development evidence, not formal results. |
| `benchmarks/results/m11c_v2/freeze/m11c_v2_stage2_manifest.json` | Stage-2 freeze (`a9b1686e…6e89`) | Model digest/decoding, prompt, schema, configuration, source fingerprint, stage-1 and receipt hashes. |
| `benchmarks/results/m11c_v2/formal/m11c_v2_dev_formal/` | report.json (`5536b1b7…8766`)/md (`d87bfb8e…68fb`), deterministic_receipt.json (`0c825972…7875`), frontend_calls.jsonl (`713dca7a…21ac`, 480 raw responses), dev_task_results.jsonl (`203969d5…be7e`), dev_predictions, m11b_reports, stage3_dev_gate.json (`66ab4b4b…f0`) | Stage 3; public per task. |
| `benchmarks/results/m11c_v2/private/m11c_v2_private_formal/` | report.json (`f292b5e2…9652`), report.md (`1d2ca78b…a1df`), deterministic_receipt.json (`e1a0bdc7…0a73`) | Aggregate only; no question text, names, fields, values, task numbers or per-task results. |
| `benchmarks/results/m11c_v2/M11C_V2_FINAL_REPORT.md` | Human-readable summary | Numbers taken from the two machine reports above. |

## M12 Lund scenario data (ADR-0024; 2026-10-08)

| ID | Location/size | Source, licence, version | Verification and boundaries |
|---|---|---|---|
| scb-population-grid-2025-lund-20km-v1 | `data/fixtures/population/scb/m12_lund_v1/`; 453 cells of 1 km (intersecting or touching the study-area BBOX 378000,6166000,398000,6186000, not clipped) | SCB WFS `stat:befolkning_1km_2025`, CC0-1.0, Statistics Sweden (SCB); reference date 2025-12-31; raw response bytes unchanged | Data `2acc387c…7998`, download_receipt `9fdc704f…d3aa`, manifest `134a177a…c2cb`. The population total may only be called "the reported population total of the 453 cells returned by the WFS" (205,040; 318 cells entirely inside the study area hold 174,956 and 135 edge cells hold 30,084), not the exact population of the 20×20 km area. |
| osm-poi-lund-m12-v1 | `data/fixtures/osm/m12_lund_poi_v1/`; 227 representative points (132 schools, 66 supermarkets, 29 pharmacies; 111/50/24 inside the study area) | Extracted from the local OSM PBF with Python osmium, ODbL-1.0, © OpenStreetMap contributors; source file `sweden-251214.osm.pbf`, 786,131,673 bytes, SHA-256 `da92cd12c8aee40e4c7d79154e736c09598715b36f7a843257145606893394c9`; file-header timestamp and osmosis_replication_timestamp 2025-12-14T21:21:45Z, sequence 4635, generator osmium/1.14.0 (self-reported by the file, provider unverified) | Data `65640faa…6d2f`, README `aabbcac9…6ba8`, manifest `e393add7…21f6`; study area expanded by 3 km; area features use `representative_point`; nodes and areas are not merged; kept in a separate directory from CC0 data. |
| m12-gtfs-archive (outside the repository) | `_geoai_data/m12/gtfs/sverige2_20251004/source.zip` on drive D, not committed | The pinned M06a archive, CC0, SHA-256 `12a4a716…6f48`, re-downloaded 2026-10-07T16:08:41Z | Used only for the pre-check; m12b builds a single-weekday subset fixture. |
| m12 pre-check reports | `benchmarks/results/m12/precheck/m12_data_precheck_v1.json` (`be57fba9…532f`, passed=false, kept), `m12_data_precheck_v2.json` (`eeebc52b…e23c`, passed=true) | Contain only counts, hashes and file names | The gate v1→v2 revision is in the ADR-0024 revision record and §2.2 of plan v2.1. |
| m12a evaluation assets | `benchmarks/data/dev/m12a_scenario_cases_v1.jsonl` (13 cases, `2f0d9b15…089e`), `m12a_references_v1.json` (`b82799c7…8f92`), freeze manifest `601968b7…06c8` | Written by the project author; independent oracle `benchmarks/m12a_oracle.py` | Public dev, not a hidden set. |
