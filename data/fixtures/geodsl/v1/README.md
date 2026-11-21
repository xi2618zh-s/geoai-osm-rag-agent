# GeoDSL v1 fixtures

These JSONL files are hand-authored, deterministic contract fixtures for M01.
They contain no external or personal data and require no network access.

- `valid.jsonl`: 30 valid model payloads that must validate and round-trip.
- `invalid.jsonl`: 35 missing, conflicting, unsupported, or structurally invalid payloads that must fail validation.

Each line contains a stable `id`, a public model name, and a JSON `payload`.
