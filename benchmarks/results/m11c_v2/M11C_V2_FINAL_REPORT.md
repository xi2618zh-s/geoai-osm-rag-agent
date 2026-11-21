# M11c v2 final report (m11c-protocol-v2: local model front end + sealed private v2)

- Date: 2026-10-07. This is a human-readable summary; every number is taken from two machine reports: public dev `formal/m11c_v2_dev_formal/report.json` (SHA-256 `5536b1b7d1c93938fffcdb94c9856320fb4bf1220073b1a114f186e9a2b18766`) and the private v2 aggregate `private/m11c_v2_private_formal/report.json` (`f292b5e2a3bcd3ba6c57e745a090379fa783eeab4062e943d3a70711ea589652`). The private part contains only aggregates: no question text, dataset names, fields, values, task numbers or per-task results.
- Protocol chain: stage 1 `docs/roadmap/M11C_V2_EVALUATION_FREEZE.md` (`9f656487…d102`; English translation in `M11C_V2_EVALUATION_FREEZE.en.md`) → private v2 receipt sealed by the user (`5e474b3a…b13d`) → stage-2 manifest (`a9b1686e…6e89`, prompt `be9ea50f…a51f`, source fingerprint `47118196…ab1a`) → stage-3 gate verdict `stage3_dev_gate.json` (`66ab4b4b…f0`, passed in all three runs) → one formal private v2 evaluation (receipt `e1a0bdc7…0a73`).
- Model: qwen2.5:3b (digest `357c53fb…9e4b`), Ollama 0.13.5, CPU only; temperature 0, seed 1107, num_ctx 4096, num_predict 256, num_thread 4, use_mmap, 120 s per attempt, ≤ 2 attempts per logical call. Cost 0 USD.

## 1. Conclusions

1. The primary metric is always the official M11b v2.0 strict pass; "strict pass excluding required_steps" is only diagnostic.
2. The public dev gate passed in all three runs (validator 136/160, rank 8/8, missing_place 8/8, call failures 0/160). 120/160 is only the gate for entering the sealed private evaluation, not a performance-improvement conclusion.
3. On public dev, v2 and v1 counts are identical for every configuration and category: on dev phrasing the model front end makes decisions equivalent to the rule-based front end, with neither improvement nor regression.
4. Private v2 (48 tasks, 3 runs each, identical across the three runs): strict FW-A 6/48, dynamic 12/48, validator 34/48; the three-way common executable subset has **5 tasks (non-empty)**, strict 0/0/5, diagnostic secondary metric 5/5/5. The private common subset of v1 was 0.
5. Under the frozen DONE criteria, "non-empty private common subset" and all other conditions are met (see section 6), so M11c can be marked DONE; Gate D still needs M12 and M13.

## 2. Public dev (160 tasks × 3 runs, seven configurations)

| Configuration | strict (per run) | Secondary (diagnostic) | coverage | Mean latency | v1 strict |
|---|---|---|---|---|---|
| FW-A fixed_workflow (formal) | 8 | 32 | .150 | 6.52 s | 8 |
| dynamic_agent (formal) | 16 | 136 | .750 | 6.57 s | 16 |
| validator_replan (formal) | 136 | 136 | .750 | 6.66 s | 136 |
| FW-B template_workflow | 8 | 128 | .750 | 6.56 s | 8 |
| A1 validator_no_frontend (no model call) | 8 | 8 | 0 | 0.07 s | 8 |
| A2 validator_strict_empty | 116 | 116 | .750 | 6.66 s | 116 |
| A3 validator_no_replan | 136 | 136 | .750 | 6.65 s | 136 |

Per category (strict, identical across three runs; v1 → v2):

| Task category | FW-A | dynamic | validator |
|---|---|---|---|
| search/supported (24) | 0 → 0 | 0 → 0 | 24 → 24 |
| buffer = within_distance/supported (24, unsupported) | 0 → 0 | 0 → 0 | 0 → 0 |
| nearest/supported (24) | 0 → 0 | 0 → 0 | 24 → 24 |
| within/supported (24) | 0 → 0 | 0 → 0 | 24 → 24 |
| spatial_join/supported (24) | 0 → 0 | 0 → 0 | 24 → 24 |
| aggregate_by_region/supported (24) | 0 → 0 | 0 → 0 | 24 → 24 |
| rank/unsupported (8) | 8 → 8 | 8 → 8 | 8 → 8 |
| missing_place = search/clarification (8) | 0 → 0 | 8 → 8 | 8 → 8 |

Subsets: common_executable 24 (search), validator 24/24, FW-A and dynamic 0/24 (missing validate_result; secondary 24/24); agent_executable 120. Stability: per-task signatures 160/160 for all seven configurations; model decisions identical 160/160 across the three runs. Per run the front end made 160 logical calls and 160 HTTP attempts, with 0 retries/timeouts/failures; 234,917 prompt tokens and 4,712 generated tokens; model wall-clock time 955–1085 s (including about 24 s of loading).

## 3. Private v2 (48 tasks × 3 runs, three formal configurations, aggregates only)

| Configuration | strict (per run) | Secondary (diagnostic) | coverage | Common subset of 5: strict / secondary | Mean latency |
|---|---|---|---|---|---|
| FW-A fixed_workflow | 6 | 11 | .104 | 0 / 5 | 7.01 s |
| dynamic_agent | 12 | 34 | .563 | 0 / 5 | 7.04 s |
| validator_replan | 34 | 34 | .563 | 5 / 5 | 7.09 s |

Strict per category (6 tasks per category; identical across three runs):

| Category | FW-A | dynamic | validator |
|---|---|---|---|
| search/supported | 0 | 0 | 5 |
| buffer (within_distance, unsupported, kept in the denominator) | 0 | 0 | 0 |
| nearest | 0 | 0 | 5 |
| within | 0 | 0 | 6 |
| spatial_join | 0 | 0 | 1 |
| aggregate_by_region | 0 | 0 | 5 |
| rank (unsupported_rank) | 6 | 6 | 6 |
| missing_place (clarification) | 0 | 6 | 6 |

- Subsets: full 48, common_executable 5, agent_executable 27, decision 12, supported_unexecutable_by_formal 9 (6 buffer + 3 rejected by the front end). Dynamic secondary 34 = validator 34: the whole difference comes from the validate_result step.
- Error categories without question content (validator, run 1): 21 rejected — missing_place 6, unsupported_rank 6, unsupported_buffer 6, plus 3 supported tasks rejected (reasons unexpressible_condition, unknown_dataset and unknown_field, one each; categories search, spatial_join and aggregate, one each; the mapping between reasons and categories is not published); 5 executed with mismatching results (4 spatial_join tasks with a tool sequence different from the reference and a mismatching feature set, 1 nearest task with a mismatching feature set); no execution exceptions; 0 replans, 0 first-round failures.
- Front end: per run 48 logical calls and 48 attempts, with 0 retries/timeouts/failures; 95,213 prompt tokens and 1,508 generated tokens; model wall-clock time 319–359 s (including about 5 s of loading); model decisions identical 48/48 across the three runs.
- All 208 tasks (dev 160 + private v2 48, formal configurations, per run): FW-A 14, dynamic 28, validator 170.

## 4. Relative to v1

- Public dev: identical for every configuration and category (section 2). The v1 rule-based front end was written from dev phrasing in the first place, so v2 shows no measurable improvement on dev.
- Private: on v1 private (40 tasks, the old holdout) all three configurations scored 0/40 with a common subset of 0 (the rule-based front end parsed none of the private queries); on v2 private (48 tasks, a new independently written holdout) 6/12/34 with a common subset of 5. The two differ in task set, author, size and category composition, so this is not a like-for-like comparison; the v1 private set has already been used, and the optional "reuse diagnostic" on it (allowed by stage 1) was not run.

## 5. Limitations that must be disclosed

- Procedural isolation rather than technical access control: the question-writing and evaluator subagents and the development agent ran under the same operating-system user and belong to the same Claude model family; the user reviewed and personally sealed the set. The development agent once learned the category of `m11b_test_223` and the nature of its problem ("one threshold missing"), without its wording. The data conventions (snake_case fields, categories as lowercase codes without spaces) were carried over from the M11b style by the question writer.
- Legal empty results: private v2 contains 4 legal empty sets (one each for search, nearest, within and spatial_join, with the within/spatial_join ones produced by spatial relations) and 1 aggregate with a zero-match count; the report gives only category aggregates and does not report these 5 tasks separately (not pre-registered); the A2 strict-empty ablation ran only on dev.
- M11b v2.0 references and the scorer neither record nor check per-region aggregate values; aggregate tasks only check the tool path, status, region set, geometry and CRS, so a zero-match task cannot check that the count is 0 (the same holds for the 24 public dev tasks).
- Single-dataset buffer (24 dev, 6 private) is unsupported and stays in the denominator; rank is unsupported, and no ranking capability is claimed.
- Clean data cannot demonstrate the recovery benefit of retries/replanning (no replanning was triggered on dev or private).
- Prompt examples include public dev tasks and dev-derived paraphrases written by the development agent; the two paraphrase probes (40 each) are only development diagnostics; the grounding rules (lexical evidence, adding conditions from mentioned values, pairing verbs, first-mentioned relation subject and so on) were derived from dev and probe evidence. spatial_join at only 1/6 is the main weakness on private v2; under the protocol nothing may be adjusted because of it.
- The local 3B model runs on CPU only, at about 6.5–7 s per task, almost all of it model time.
- Leak scan: the raw `scan-leaks` results on the public output of the private run were queries 0, task_ids 0, roles 0, fields 1, text_values 2, and the raw hits are kept as is. The evaluator subagent's de-identified location step was blocked twice by the auto-mode permission classifier; the user authorized, outside this session, running the same read-only de-identified location script (`scripts/locate_m11c_v2_leak_hits.py`), with the result that all 3 hits are in fixed template text identical to the public dev report (1 in `metadata.definitions.strict_pass` of report.json and 2 in the fixed notes of report.md), and 0 elsewhere.
- M03 Phase 2 public queries remain a catalog-informed internal evaluation; fixed GTFS stop buffers are not transit isochrones.

## 6. DONE criteria check (rules unchanged)

| Condition | Evidence |
|---|---|
| Protocol and configuration frozen before the formal runs and checked by the runner | Stage-1 document, stage-2 manifest; run-dev/run-private check the fingerprint, prompt, configuration, model digest and receipt before running |
| Three formal configurations complete dev and private evaluation 3 times each; four dev ablations 3 times each; exclusive outputs | formal/m11c_v2_dev_formal, private/m11c_v2_private_formal |
| Non-empty private three-way common executable subset with a like-for-like comparison | 5 tasks: strict 0/0/5, secondary 5/5/5 |
| Complete full-set coverage/unsupported/failure reporting (buffer included) | Sections 2 and 3 |
| Ablations, stability, error analysis, latency/calls/cost, code and working-tree fingerprints | Sections 2–3; report metadata |
| Existing offline tests all kept; M11b and M07–M10 audits, the M11c v1 receipt and protected hashes unchanged; v2 replayable byte for byte | Final verification entry in the engineering log; `scripts.audit_m11c_v2_reproducibility`, `replay-private` |
