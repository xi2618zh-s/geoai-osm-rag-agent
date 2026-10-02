# Five-minute demo playbook

## Preparation

1. Run `scripts\run_local.cmd`.
2. Open `http://127.0.0.1:8000/ui`.
3. Keep the P4 step list and map visible; do not open unrelated personal windows.

## Demo 1: grounded single-tag query

Query: `Find all cafes in Lund`

Explain:

- hybrid retrieval produces evidence-backed OSM tags;
- Qwen returns a strict decision or the validator chooses ranked fallback;
- Nominatim/osmium/pyosmium execute against real data;
- output includes node/way/relation counts and a unique GeoJSON URL.

## Demo 2: constrained multi-tag workflow

Switch mode to `Multi-tag OR workflow`.

Query: `Find cafes or restaurants in Lund`

Explain the six visible steps: geocode, one shared clip, two tag extractions,
union/dedup and write. Show `matched_filters` in a map popup. The P4 acceptance
snapshot returned 173 objects, but current OSM data may change that number.

## Demo 3: failure boundary

Query: `Find cafes and restaurants in Lund` in workflow mode.

The system rejects natural-language AND as ambiguous instead of guessing whether
the user means category union or same-object intersection. Explicit API callers
can request object-level intersection.

## Honest interpretation

The key result is deliberately split by stage: retrieval reached 100% on the internal 63-query set, but
Qwen final decision was 85.7% on 21 queries. The project therefore demonstrates
measurement, failure analysis and constrained execution—not a production 100%
accuracy claim.

The visually accepted release screenshot is
[`docs/assets/p5_workflow_result.png`](assets/p5_workflow_result.png). It records
the real 173-feature P4 workflow result, six succeeded steps and rendered Leaflet
markers. Browser console inspection reported no errors or warnings.
