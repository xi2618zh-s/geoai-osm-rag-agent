# ADR-0003: DatasetRef uses opaque URIs, controlled copies and an explicit lifecycle

- Status: Accepted
- Date: 2026-10-05
- Decision owner/Session: M02 dataset registry
- Affected modules: M02, M04–M11

## Context

Later spatial tools need to exchange data, but putting large Python objects or arbitrary user paths into agent state causes memory pressure, path traversal, content drift and untraceable results. M02 needs a unified reference that is testable locally and whose storage backend can be replaced later, without introducing cloud dependencies.

## Decision

1. `DatasetRef` v1 uses a random, opaque `ds_<uuid hex>` ID; the ID never maps to or is concatenated with an arbitrary user-supplied path.
2. Only the canonical `registry://datasets/<id>/data.<format>` URI is exposed. The registry resolves the real path inside a controlled root directory.
3. Registration copies the source file into staging, validates format, schema, CRS, bbox, row count, fields, byte size and SHA-256, and then publishes atomically; the original upload path is not referenced afterwards.
4. Reads validate metadata, storage URI, file existence, byte size and SHA-256 by default. Tampering, missing files and format problems use stable `DATASET_*` error codes.
5. The `DatasetRegistry` Protocol is the backend-neutral boundary; this stage implements only `LocalDatasetRegistry`.
6. The v1 contract can express `vector/geojson`, `table/csv`, `raster/geotiff` and `network/gtfs`; the local implementation currently registers and reads only the first two, and must not pretend the other two are available.
7. Temporary data must declare an explicit TTL (1 second to 7 days) and supports manual release and expiry cleanup; persistent data cannot be deleted through the temporary-release interface.
8. UUIDs plus separate staging directories ensure concurrent registrations never overwrite each other; identical content may keep different sources and lineage records.

## Alternatives

- Store the absolute path of the source file: exposes machine paths, allows content drift and widens the path-attack surface. Rejected.
- Use the content hash as the only dataset ID: deduplicates naturally, but merges different sources, versions and lifecycles. Not adopted in M02.
- Put whole GeoJSON/CSV payloads into agent state: works for small samples but does not scale to real data. Rejected.
- Introduce object storage or a database now: outside the scope of local offline acceptance. Rejected; the Protocol keeps room for replacement.

## Consequences

- Positive: tools pass only small references; content integrity, lineage and lifecycle are verifiable; local tests need no network.
- Cost: registration needs one file copy and one hash computation; format support is currently limited.
- Testing and observability: 20 module tests cover GeoJSON/CSV, fixture manifests, cleanup on failure, tampering, path traversal, lifecycle and concurrency.

## Migration and rollback

The existing workflow does not use `DatasetRef` yet, so no runtime migration is needed. Future backends must implement the `DatasetRegistry` Protocol and keep the `DatasetRef` v1 semantics. Deleting `src/datasets/` rolls back M02 without affecting the old API.
