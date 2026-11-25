"""Build only explicitly allowlisted public resources; never scan output or private data."""
import base64
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEST = ROOT / 'build/public_demo'
SNAPSHOT = 'benchmarks/results/m12/m12a/formal_v1/scenario_report_m12a_A1.json'
HISTORY = 'docs/demo/m12a_school_coverage_gaps_v1.html'
ASSETS = ('index.html', 'guide.html', 'assets/explore.css', 'assets/explore.js')


def build():
    DEST.mkdir(parents=True, exist_ok=True)
    entries = []
    def write(name, data, source, method):
        path = DEST / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        entries.append({'path': name, 'sha256': hashlib.sha256(data).hexdigest(), 'source': source,
                        'source_sha256': hashlib.sha256((ROOT / source).read_bytes()).hexdigest(), 'method': method})
    for name in ASSETS:
        write(name, (ROOT / 'site' / name).read_bytes(), 'site/' + name, 'byte copy')
    report = json.loads((ROOT / SNAPSHOT).read_bytes())
    files = []
    for item in report['downloads']:
        data = base64.b64decode(item['href'].split(',', 1)[1], validate=True)
        if hashlib.sha256(data).hexdigest() != item['sha256'] or len(data) != item['byte_count']:
            raise ValueError('Saved download integrity check failed')
        write('examples/school/' + item['filename'], data, SNAPSHOT, 'decode embedded download; hash verified')
        files.append({'name': item['filename'], 'url': 'examples/school/' + item['filename']})
    write('examples/school/report.html', (ROOT / HISTORY).read_bytes(), HISTORY, 'byte copy; historical report')
    files.append({'name': 'report.html', 'url': 'examples/school/report.html'})
    result = {'status': 'success', 'run_id': 'saved-school-snapshot', 'created_at': '2026-10-08 (archived snapshot)',
              'code_version': 'historical report; see source manifest', 'report': report, 'files': files,
              'checks': ['Saved report snapshot', 'Download SHA-256 verified at build', 'Historical report retained byte for byte'],
              'record_url': 'examples/school/result.json'}
    write('examples/school/result.json', (json.dumps(result, ensure_ascii=False, indent=2)+'\n').encode(), SNAPSHOT,
          'presentation envelope around unmodified saved report')
    manifests = ('data/fixtures/osm/m12_lund_poi_v1/manifest.json', 'data/fixtures/population/scb/m12_lund_v1/manifest.json')
    provenance = {'files': entries, 'command': report['command'], 'parameters': report['parameters'],
                  'data_sources': report['data_licenses'], 'data_manifests': {p: hashlib.sha256((ROOT/p).read_bytes()).hexdigest() for p in manifests}}
    (DEST / 'source_manifest.json').write_text(json.dumps(provenance, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    allowed = {e['path'] for e in entries} | {'source_manifest.json'}
    actual = {p.relative_to(DEST).as_posix() for p in DEST.rglob('*') if p.is_file()}
    if actual != allowed:
        raise ValueError('Unexpected files in public build: ' + repr(sorted(actual - allowed)))
    print(f'Built {len(allowed)} explicitly listed files in build/public_demo')


if __name__ == '__main__':
    build()
