# M11c v2 stage-2 freeze (m11c-protocol-v2, prompt and code)

> **English translation.** The authoritative frozen document is the Chinese original [`M11C_V2_STAGE2_FREEZE.md`](M11C_V2_STAGE2_FREEZE.md), which is kept byte for byte as frozen evaluation evidence. This translation is provided for readers and is not part of the frozen evidence; if the two differ, the original applies.

- Date: 2026-10-07. Published before the formal public dev run and before any private v2 run. This file only appends stage-2 content and does not change the stage-1 rules ([M11C_V2_EVALUATION_FREEZE.md](M11C_V2_EVALUATION_FREEZE.md), SHA-256 `9f656487ef943bced4a63606b61652116f24c9962a9b65a746615b4d14c7d102`; [English translation](M11C_V2_EVALUATION_FREEZE.en.md)).
- Machine-readable freeze: `benchmarks/results/m11c_v2/freeze/m11c_v2_stage2_manifest.json`, SHA-256 `a9b1686ede22c7b6558510d9bf15d9b3f99ec5fdb6a467a64239a8fda8b96e89`. Before running, `run-dev` and `run-private` recompute the source fingerprint, prompt SHA, system configuration, dev tasks and catalogue profile, and check the model digest through `/api/version` and `/api/tags`; any mismatch is refused.
- Private v2 has been sealed by the user: public receipt `benchmarks/data/m11c/m11c_private_v2_receipt.json`, SHA-256 `5e474b3afe14c67362899a2fe5744b2ea0b4fa33818a7abb51fffbaf5c43b13d` (48 tasks as 8×6, 36 supported, of which 32 have non-empty feature-set results).

## 1. Frozen content

| Item | Value |
|---|---|
| Model | qwen2.5:3b, Ollama 0.13.5, digest `357c53fb659c5076de1d65ccb0b397446227b71a42be9d1603d46168015c9e4b`, Q4_K_M, CPU only, endpoint `http://127.0.0.1:11434/api/generate`, `stream=false` |
| Decoding | temperature 0, seed 1107, num_ctx 4096, num_predict 256, num_thread 4, use_mmap true, keep_alive 30m; 120 s per attempt; at most 2 attempts per logical call |
| Prompt | `src/agent/prompts/m11c_frontend_v2_prompt.txt`, label `m11c_frontend_v2_prompt`, SHA-256 `be9ea50f10e5cb50c3a9c6af001c2b0fe27b86b1e0596688fef4f80ebfcda51f` (computed after normalizing line endings to LF) |
| Frame schema | Frozen structure: op ∈ {search, buffer, nearest, within, join, aggregate, rank, other}; data/other/subject are catalogue-role enumerations or null; where[{field enumeration, cmp ∈ {=,!=,>,>=,<,<=}, value string or number}]; k; relation ∈ {intersects, within, contains}; metric ∈ {count, sum, mean, min, max}; metric_field; op and data required. Role and field enumerations are generated deterministically from the declared catalogue the system receives (public dev instance SHA-256 `f72182eb4230e9706251bb39d287b01b295adcd61c98384cc93c820d42744fbc`) |
| Front-end version | m11c-frontend-v2 (`src/agent/model_frontend.py`), rendering reuses the rule-based front end's `render_request`; the `interpret` behaviour of the rule-based front end m11c-frontend-v1 is unchanged |
| Source fingerprint | `sha256:47118196a78b701d4654581dd01576e0dff6c29def485043aae2f3ca4c23ab1a` (all `.py` in `src`, `benchmarks/run_m11c_v2.py`, `run_m11c_baselines.py`, `run_m11b_scoring.py`, `scripts/build_m11c_private_v2.py` and the prompt file) |
| Systems | Formal: fixed_workflow, dynamic_agent, validator_replan; dev ablations: template_workflow (FW-B), validator_no_frontend (A1, no model call), validator_strict_empty (A2), validator_no_replan (A3). Apart from front-end identity fields, every configuration is the same as in m11c-protocol-v1 |
| Seeds / gate | 11071/11072/11073; the gate is checked per run according to section 5 of stage 1 |

## 2. The model only produces a semantic frame; everything else is deterministic

Model input: the prompt (instructions and examples on the public dev catalogue, including dev-derived paraphrases written by the development agent), a profile of the current declared catalogue (roles, geometry types, row counts, field names/types, value lists for text fields with ≤12 values) and the query. Deterministic grounding rules (`ground_frame`):

1. No frame (no valid frame after 2 attempts) → `unsupported/model_frontend_failed`.
2. op=rank → `unsupported_rank` (the registry has no ranking tool); op=other → `unsupported_operation`.
3. Neither data nor other is bound to a catalogue role → `clarification/missing_place`; none of the bound datasets has lexical evidence in the query (role name, a field name belonging only to that dataset, or a text value of 3+ characters from that dataset) → `missing_place`.
4. The model says within but the query contains a join/pair/match verb → spatial_join. op=buffer → `unsupported_buffer` (GeoDSL is not upgraded).
5. A two-dataset operation missing one side → `unknown_dataset`; when a condition field belongs only to the other dataset, the filtered side is swapped (the filtered side is the source for nearest/within, the right for join and the values for aggregate).
6. If exactly one value from a value-listed field of the filtered dataset appears in the query and the frame has no condition on that field → add an equality condition (except for a field with the same name as the role). The field must exist; numeric fields accept numbers or numeric text; text values are normalized for case/singular-plural against observed values; values containing spaces or of the wrong type → `unexpressible_condition`.
7. nearest without k → 1; join without a relation → intersects; when the subject of a join relation has lexical positions on both sides, the first-mentioned one is used, otherwise the model's subject; if the subject is the filtered side, within/contains are inverted.
8. aggregate: no metric → count; for non-count metrics the field must be a numeric non-key field of values, otherwise the unique numeric field, otherwise `ambiguous_metric_field`; the join key is chosen first by value overlap (unique region key, value overlap ≥ 0.5, a unique best candidate), otherwise falling back to the v1 field-name rule, and otherwise `ambiguous_join_key`.
9. The M07 command is rendered through `render_request`, then planned by M07 (m07.2) and validated/executed by M08; the model never generates spatial results.

## 3. Development evidence (public dev only, recorded in the engineering log)

- Iteration 1 (prompt `81728663…`): dev validator 125/160; iteration 2 (`be9ea50f…`): 136/160 (everything except buffer), rank 8/8, missing_place 8/8, call failures 0/160.
- Dev-derived paraphrase probes (development diagnostics, not part of the gate): v1 with 40 queries, 31/40 with the iteration-1 prompt → 37/40 after rule adjustments; v2 with 40 newly written queries after the rules were fixed was run only once, 38/40 (the failures are all unsupported buffer). The probes share the dev catalogue and task types and cannot represent the wording of the private set.

## 4. Subsequent runs

- Stage 3: `python -m benchmarks.run_m11c_v2 run-dev` exclusively writes `benchmarks/results/m11c_v2/formal/m11c_v2_dev_formal/` (report, deterministic_receipt, frontend_calls raw responses, dev per-task results, 21 official M11b reports, `stage3_dev_gate.json`). The gate is checked for each of the three runs; if any run fails, stop: do not run private v2, do not switch models and do not fall back to the rule-based front end.
- After the gate passes, a fresh evaluator subagent runs `run-private` once (each of the three formal configurations 3 times): only when stage 3 has passed, the dev result hashes are unchanged and the private v2 receipt matches this freeze, and it refuses if the output directory already contains a private run. Public output is aggregate only; per-task records and raw model responses are written to the question-writing workspace. Afterwards `scan-leaks` prints only hit counts, and `replay-private` re-checks by replaying the records.
- Audit: `scripts/audit_m11c_v2_reproducibility.py` replays the public dev run from the recorded raw responses and compares the deterministic receipt byte for byte.
- Any change to the prompt, code or configuration after this freeze requires a new version; nothing may be adjusted based on the private v2 results.
