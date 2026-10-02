# P2 Runtime Validation

Generated: 2026-08-04T13:46:37.577682+00:00
Model: `qwen2.5:3b`
Query: `Find all cafes in Lund`

## Checks

| Check | Result |
|---|---:|
| both_success | PASS |
| both_llm_grounded | PASS |
| both_tags_correct | PASS |
| trace_ids_unique | PASS |
| all_stage_timings_present | PASS |
| second_clip_cache_hit | PASS |

## Runs

```json
{
  "first_run": {
    "success": true,
    "query": "Find all cafes in Lund",
    "place": "Lund",
    "chosen_tag": "amenity=cafe",
    "count": 50,
    "counts_by_type": {
      "node": 38,
      "way": 12,
      "relation": 0
    },
    "llm_ok": true,
    "llm_error_code": null,
    "llm_attempts": 2,
    "decision_source": "llm",
    "clip_cache_hit": false,
    "trace_id": "7aaf3b60467c44efb93cf3ab79d8c831",
    "timings_ms": {
      "rag_retrieval": 2408.0,
      "llm_parse": 135029.77,
      "decision": 0.0,
      "geocode": 1389.35,
      "osm_extract": 12006.46,
      "total": 150836.94
    }
  },
  "second_run": {
    "success": true,
    "query": "Find all cafes in Lund",
    "place": "Lund",
    "chosen_tag": "amenity=cafe",
    "count": 50,
    "counts_by_type": {
      "node": 38,
      "way": 12,
      "relation": 0
    },
    "llm_ok": true,
    "llm_error_code": null,
    "llm_attempts": 1,
    "decision_source": "llm",
    "clip_cache_hit": true,
    "trace_id": "a6d3233b6e6e409cb29b8ed5d72670e2",
    "timings_ms": {
      "rag_retrieval": 13.96,
      "llm_parse": 15783.55,
      "decision": 0.0,
      "geocode": 0.18,
      "osm_extract": 4643.63,
      "total": 20442.72
    }
  }
}
```
