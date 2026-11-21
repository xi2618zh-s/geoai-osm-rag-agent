# M03 Phase 1 curation provenance

- `m03_phase1_curation_v1.json` is a rejected pre-build draft. It mapped `shop=florist` to `shop=garden_centre`, which was absent from the frozen 100-tag candidate set. It is retained solely as failure evidence and must not be used for a release.
- `m03_phase1_curation_v2.json` is the accepted, frozen input. Its SHA-256 is `2b0232e0d4440dc8c99c4e28674259551c090e80fd88ad8c3c2e3d07bef4eca8`; the adjacent `.sha256` sidecar is checked before every build.
- The accepted mapping uses `shop=florist` → `leisure=garden` as a reviewed semantic confusion. Every entry has one non-self, in-catalog hard negative with English and Chinese reasons, marked `manual_semantic_review`.
- English and Chinese names, concise descriptions, aliases, and hard-negative rationales are Codex-assisted project curation. They are published with the derived knowledge data under CC BY-SA 2.0; this does not set the software license.
- Curation is based on the 100 fixed-revision official OSM Wiki Tag text snapshots in the raw manifest. No benchmark query text was used as a curation source.
