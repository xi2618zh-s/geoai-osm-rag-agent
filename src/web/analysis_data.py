"""Public school snapshot catalogue, independent of evaluation/oracle code."""
import hashlib
import json
from pathlib import Path

from src.scenarios import CatalogueEntry, ScenarioCatalogue, ScenarioError

ROOT = Path(__file__).resolve().parents[2]
SCB = 'data/fixtures/population/scb/m12_lund_v1'
POI = 'data/fixtures/osm/m12_lund_poi_v1'
# Pin the manifests as well as their data files: changing both cannot bypass verification.
PINS = {'data/fixtures/population/scb/m12_lund_v1/manifest.json': '134a177aa715eca0ef37eb32c33b5978f0b87bab0fcd77d5481c628f6fcac2cb', 'data/fixtures/osm/m12_lund_poi_v1/manifest.json': 'e393add766314a47c54c501390e26d27a1a99617ef8a4fd11a9a756b7f2a21f6'}


def catalogue(registry, root=ROOT):
    root = Path(root)
    manifests = {}
    for relative, digest in PINS.items():
        path = root / relative
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise ScenarioError('DEMO_DATA_INTEGRITY', 'Included data or manifest is missing or changed. Restore the tracked snapshot.')
    for folder in (SCB, POI):
        manifest = json.loads((root / folder / 'manifest.json').read_bytes())
        for name, spec in manifest['files'].items():
            path = root / folder / name
            if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != spec['sha256']:
                raise ScenarioError('DEMO_DATA_INTEGRITY', 'Snapshot does not match its manifest. Restore the tracked snapshot.')
        manifests[folder] = manifest
    scb, poi = manifests[SCB], manifests[POI]
    population = registry.register_file(root / SCB / 'scb_population_grid_2025_lund_20km_raw.geojson',
        kind='vector', crs='EPSG:3006', source='scb:befolkning_1km_2025', source_version='2025-12-31')
    pbf = poi['source_pbf']
    schools = registry.register_file(root / POI / 'lund_poi_points_v1.geojson', kind='vector', crs='EPSG:4326',
        source='osm:m12-lund-poi', source_version='pbf-sha256:' + pbf['sha256'][:12])
    entries = {
        'population_grid_raw': CatalogueEntry(role='population_grid_raw', semantic_type='scb_population_grid_raw',
            kind='vector', declared_crs='EPSG:3006', dataset=population,
            required_fields=['rutid_inspire', 'rutid_scb', 'beftotalt', 'referenstid'], license=scb['license'],
            attribution='Statistics Sweden (SCB)', provider_verified=True,
            temporal_note='SCB 1 km grid reference date 2025-12-31 (WFS stat:befolkning_1km_2025).'),
        'osm_poi': CatalogueEntry(role='osm_poi', semantic_type='poi_points', kind='vector', declared_crs='EPSG:4326',
            dataset=schools, required_fields=['poi_id', 'amenity', 'name'], license=poi['license'],
            attribution=poi['attribution'], provider_verified=False,
            temporal_note=f"OSM PBF header timestamp {pbf['header']['timestamp']}; local file, provider unverified (SHA-256 {pbf['sha256'][:12]}...)."),
    }
    return ScenarioCatalogue(catalogue_id='demo-lund-school', place='Lund', study_bbox=poi['study_bbox_3006'],
                             study_crs='EPSG:3006', analysis_crs='EPSG:3006', entries=entries)
