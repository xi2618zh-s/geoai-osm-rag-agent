# M07 planning evaluation freeze v1

- Frozen before M07 production code: 2026-10-06 (Asia/Shanghai).
- Scope: public, synthetic, offline contract evaluation. It is neither a hidden set nor evidence of unrestricted natural-language understanding.
- Input grammar: explicit English task commands, named place, declared EPSG CRS and role catalog. Dataset roles resolve only to DatasetRequirement metadata; no file path or live DatasetRef enters a model proposal.
- Eight task classes, ten cases each: search, filtered_search, within_distance, nearest, within, spatial_join, aggregate_by_region, and rank. Rank is expected unsupported because no registered deterministic rank tool exists.
- Add at least 20 adversarial cases covering missing place/distance/target, conflicting values, unsupported query/tool, malformed JSON, unregistered or private tool, code/path injection, future/cyclic dependencies, parameter type and CRS mismatch, and budget excess.
- A supported case passes only when parsed intent, strict GeoDSL plan, registered public tool sequence, role references and business parameters match the frozen expectation. Expected clarifications and unsupported rejections are separately counted.
- Metrics (denominators fixed by fixture labels): plan legality = accepted valid plans / 70 supported cases; tool selection accuracy = exact tool sequence / 70; parameter accuracy = exact business parameters / 70. Threshold for each: at least 0.95. Guardrail: all unsupported and adversarial cases must be rejected or clarified as expected, with zero unauthorized tool/path/code execution.
- Fixed controls: at most 8 tool steps, one model JSON structural repair attempt, at most one retry declared per step, and a 12-unit static plan budget. No execution occurs in M07.
- Results must report exact denominators, failures and version/hash. Later experiments use a new version. M03 Phase 2 public retrieval queries are catalog-informed and cannot serve as independent M07 generalization evidence.


## Coverage addendum v2 (after first implementation)

The first v1 80-case file and its receipt remain unchanged. A coverage audit found that v1 had seven plannable classes plus unsupported rank; to meet the stricter reading of eight plannable classes, v2 appended ten aggregate_sum cases using the already registered aggregate_by_region tool. v2 has 90 cases, 80 supported and ten unsupported rank cases; the same 0.95 three-metric thresholds apply over 80. This addendum was made after initial implementation, so v2 is transparent supplemental regression evidence, not a pre-implementation freeze or hidden evaluation. v1 SHA-256: 81fe96d28953c8ec99d1eb999905b2e13b0854bad2f92a7f6623a39bf5df9ad8. v2 SHA-256: 6df293d8c74810e042d7bedb472f89a5e32ed0b7b264e7b1586aade98d6b06ee.
