# ADR-0024: M12 Lund scenario data, licence separation and a data pre-check gate

- Status: Accepted (conditionally approved by the user, D2-B, 2026-10-08)
- Date: 2026-10-08
- Decision owner/Session: the user; Claude Code M12 session
- Affected modules: M05, M06, M12

## Context

The existing SCB fixture has only 16 cells and the GTFS subset only 5 trips, which is not enough for three scenarios. The user approved only two downloads and required a pre-check first, licence separation, and no unverified claims about the provenance of the local PBF.

## Decision

1. **Only two network accesses are allowed**: (1) the existing `scripts/fetch_scb_population_grid.py` (SCB WFS 1.1.0, `stat:befolkning_1km_2025`, CC0) downloading with the EPSG:3006 BBOX `378000,6166000,398000,6186000`; (2) the existing `scripts/fetch_gtfs_sverige2.py` re-downloading the GTFS Sverige 2 archive pinned in M06a (URL, 47,699,395 bytes, ETag, SHA-256 `12a4a7169d8423641da7f15125092423f6f9834f3b70725719b1ce147eb48f69`, CC0). Downloads and intermediate artefacts are stored outside the repository in `<local-workspace>\_geoai_data\m12\`.
2. **The local OSM PBF** (`data/osm/sweden-251214.osm.pbf`, git-ignored) is read-only: record its full SHA-256, byte size, PBF header fields (including the raw values of timestamp-like fields) and "provider unverified"; the provider or version date must not be asserted from the file name alone. Extraction uses only the Python `osmium` package (pyosmium), not the osmium command-line tool.
3. **Data pre-check gate** (gate v1: the table in §2.1 of implementation plan v2, frozen before any download result was seen; the two SCB revisions of gate v2 are in the revision record below and in §2.2 of plan v2.1): SCB features 100–400 (v2: 100–484), all attributes present, non-null total population ≥ 50,000, non-null `ald5_9` ≥ 50%, response ≤ 5 MB; inside the study area at least 10 OSM schools, 10 supermarkets and 5 pharmacies; m12a non-triviality; matching GTFS hash, ≥ 200 valid trips in the study area on 2025-10-07, ≥ 10 routes, ≥ 10 regular-boarding departures from `Lund Centralstation` in [07:30,08:30), and an estimated uncompressed full stop-chain size ≤ 20 MB. If any check fails, stop and report; nothing is invented. The pre-check report contains only counts, hashes and file names, never local absolute paths.
4. **Licence and attribution separation**: CC0 data (SCB, GTFS) and ODbL 1.0 derived data (OSM POIs, "© OpenStreetMap contributors", the derived database shared under ODbL) are stored in separate directories, each with a manifest recording licence, attribution, source and hashes; reports and HTML list licences and attributions separately. The software repository licence does not change because of this.
5. **Fixture scope**: m12a builds an extended SCB grid fixture (raw WFS GeoJSON + download receipt + manifest) and an OSM POI fixture (representative points of `amenity=school`, `shop=supermarket` and `amenity=pharmacy` within the study area expanded by 3 km, EPSG:4326; area features use Shapely `representative_point`; nodes and areas may duplicate each other and are not merged); m12b builds a single-weekday GTFS subset fixture (uncompressed ≤ 20 MB, and reported first if the compressed size exceeds 2 MB). All build scripts are offline, deterministic and refuse to overwrite.
6. Existing M05/M06a/M06b fixtures and their hashes are unchanged.

## Alternatives

- Use only the existing tiny fixtures without network access (D2-A) or fully synthetic data (D2-C): too little demonstration value. Not chosen.
- Extract with the osmium command-line tool: its availability was not confirmed. Not chosen.

## Consequences

- Positive: the three scenarios are based on real public data with auditable sources and licences.
- Cost/risk: the three sources have different dates (SCB 2025-12-31, the GTFS 2025-10-04 version, the OSM file-header timestamp); OSM completeness is unknown; reports must disclose this.

## Migration and rollback

New fixture directories and scripts; deleting them rolls back without affecting existing fixtures.

## Revision record

### 2026-10-08 pre-check gate v2 (approved by the user)

- Gate v1 result: `benchmarks/results/m12/precheck/m12_data_precheck_v1.json` (SHA-256 `be57fba9a82ab9b219bd03ba59d12907fc1fa950c1fc73a099f063fc05db532f`, `passed=false`) is kept unchanged. Two checks failed: 453 SCB features > 400, and the attribute check reported a missing `sp_geometry`.
- **The original upper bound of 400 was a geometric estimation error**: a WFS 1.1.0 BBOX filter returns grid cells that intersect or touch the BBOX; a 20×20 km BBOX whose edges lie on whole-kilometre lines can touch at most 22×22 = 484 cells of 1 km. Gate v2 raises the upper bound to 484, with the lower bound and all other conditions unchanged; the downloaded raw response with 453 cells (file SHA-256 `2acc387c186a97e3a3e5481fad388f15882ed5c3d9510bd53b5befebae807998`) is kept, neither clipped nor downloaded again.
- **Attribute-check defect fixed**: `sp_geometry` is the geometry element in the WFS DescribeFeatureType. Gate v2 checks that the top-level GeoJSON `geometry` is a valid, non-empty Polygon/MultiPolygon. The other 29 attribute names must appear as keys in every feature, with values following the established M05 rules (identity-field types/values, population fields null/suppression token/non-negative integer, unique `rutid_inspire`, `referenstid` a 2025 date); the gate passes only when per-key absences and per-rule violations are all 0. Completeness is no longer judged by a field appearing in any single feature. The M05 public contracts and existing assets are unchanged.
- **Edge-cell inclusion rule**: the analysis units are all 1 km cells returned by the WFS that intersect or touch the study-area BBOX, included as is without clipping. A population total may only be described as "the reported population total of the N cells returned by the WFS (intersecting or touching the study-area BBOX)", never as the exact population within the 20×20 km area. Edge cells extend at most 1 km beyond it, so the m12a distance threshold is capped at 2000 m (≤ the 3 km POI expansion − 1 km).
- The v2 result is written to a separate report `m12_data_precheck_v2.json` with accompanying tests; fixtures are built only after v2 passes in full; no additional downloads are approved.
