"""Thin request → verified scenario → persistent report service. No reference answers."""
import base64
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import subprocess
import tempfile
from uuid import uuid4

from src.agent.validation import ValidationPolicy
from src.datasets import LocalDatasetRegistry
from src.scenarios import ScenarioError, execute_scenario_plan
from src.scenarios.html import render_scenario_html
from src.scenarios.m12a import interpret_m12a, plan_m12a, build_m12a_report, validate_m12a_report
from .analysis_data import ROOT, catalogue


def command_for(body):
    if not isinstance(body, dict):
        raise ScenarioError('DEMO_REQUEST_INVALID', 'Send a JSON object.')
    allowed = {'scenario', 'place', 'distance_m', 'age_band', 'minimum_children', 'command'}
    if set(body) - allowed:
        raise ScenarioError('DEMO_REQUEST_INVALID', 'Unknown request fields; client plans and file paths are not accepted.')
    if 'command' in body:
        if set(body) != {'command'} or not isinstance(body['command'], str) or len(body['command']) > 500:
            raise ScenarioError('DEMO_REQUEST_INVALID', 'Send either one command or structured fields, not both.')
        return body['command']
    if body.get('scenario', 'school_coverage') != 'school_coverage':
        raise ScenarioError('DEMO_UNSUPPORTED', 'Only the included Lund school coverage analysis is supported.')
    parts = ['find school coverage gaps']
    if body.get('distance_m') is not None:
        distance = body['distance_m']
        if isinstance(distance, bool) or not isinstance(distance, (int, float)) or not math.isfinite(distance) or distance < 0:
            raise ScenarioError('DEMO_REQUEST_INVALID', 'Distance must be a finite nonnegative number, above zero to run.')
        parts.append(f'farther than {distance} m')
    band, threshold = body.get('age_band'), body.get('minimum_children')
    if band is not None or threshold is not None:
        if band is None or threshold is None:
            raise ScenarioError('DEMO_MISSING_PARAMETERS', 'Provide both an age band and minimum children.')
        if not isinstance(band, str) or not re.fullmatch(r'[0-9]+-[0-9]+', band):
            raise ScenarioError('DEMO_REQUEST_INVALID', 'Use an SCB age band: 0-4, 5-9, 10-14 or 15-19.')
        if type(threshold) is not int or threshold < 0:
            raise ScenarioError('DEMO_REQUEST_INVALID', 'Minimum children must be a positive integer.')
        parts.append(f'where children aged {band} are at least {threshold}')
    if body.get('place') is not None:
        place = body['place']
        if not isinstance(place, str) or not re.fullmatch(r'[A-Za-z][A-Za-z -]*', place):
            raise ScenarioError('DEMO_REQUEST_INVALID', 'Place must be Lund.')
        parts.append('in ' + place)
    return ' '.join(parts)


class AnalysisService:
    def __init__(self, output_root=None, data_root=ROOT):
        self.output_root = Path(output_root or ROOT / 'output/demo_runs')
        self.data_root = Path(data_root)

    def analyse(self, body, *, run=False):
        command = command_for(body)
        # Registration/verification is temporary. Preview invokes no analysis tool.
        self.output_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='preview-', dir=self.output_root) as temp:
            registry = LocalDatasetRegistry(Path(temp) / 'registry')
            cat = catalogue(registry, self.data_root)
            front = interpret_m12a(command, cat)
            if front.status != 'plan':
                return front.model_dump(mode='json')
            plan = plan_m12a(front.intent, cat)  # includes existing static gate
            if not run:
                return {'status': 'plan', 'command': front.intent.command, 'parameters': front.intent.parameters,
                        'steps': [s.model_dump(mode='json') for s in plan.steps],
                        'data_sources': [{'attribution': e.attribution, 'license': e.license,
                                          'temporal_note': e.temporal_note} for e in cat.entries.values()]}
        run_id = uuid4().hex
        folder = self.output_root / run_id
        folder.mkdir(exist_ok=False)
        try:
            with tempfile.TemporaryDirectory(prefix='work-', dir=folder) as temp:
                registry = LocalDatasetRegistry(Path(temp) / 'registry')
                cat = catalogue(registry, self.data_root)
                front = interpret_m12a(command, cat)
                plan = plan_m12a(front.intent, cat)
                outcome = execute_scenario_plan(plan, cat, registry, policy=ValidationPolicy(allow_empty=True, max_features=10000))
                if outcome.status != 'success':
                    raise ScenarioError(outcome.error_code or 'DEMO_EXECUTION_FAILED', f'GIS execution stopped at {outcome.failed_step or "data binding"}.')
                report = build_m12a_report(front.intent, outcome, cat, registry)
                validate_m12a_report(report, front.intent, outcome, cat, registry)
                # Persist exports only after evidence validation, before registry cleanup.
                artifacts = []
                for item in report.downloads:
                    data = base64.b64decode(item.href.split(',', 1)[1], validate=True)
                    if hashlib.sha256(data).hexdigest() != item.sha256 or len(data) != item.byte_count:
                        raise ScenarioError('DEMO_EXPORT_INVALID', 'Download integrity check failed.')
                    (folder / item.filename).write_bytes(data)
                    artifacts.append(item.filename)
                (folder / 'report.html').write_text(render_scenario_html(report), encoding='utf-8')
                artifacts.append('report.html')
                code = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=ROOT, capture_output=True, text=True).stdout.strip()
                # Hash active adapter code as well; HEAD may predate uncommitted implementation.
                code_hashes = {p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                               for p in sorted((ROOT / 'src/web').glob('*.py'))}
                base = f'/api/analyses/{run_id}'
                result = {'status': 'success', 'run_id': run_id, 'command': front.intent.command,
                          'parameters': front.intent.parameters, 'created_at': datetime.now(timezone.utc).isoformat(),
                          'code_version': code, 'adapter_sha256': code_hashes,
                          'report': report.model_dump(mode='json'),
                          'executed_steps': [s.model_dump(mode='json') for s in outcome.step_records],
                          'tool_calls': outcome.tool_calls, 'checks': ['Snapshot manifests and hashes checked',
                              'Static plan gate passed', 'Each tool output read, CRS and geometry checked',
                              'Report rebuilt and metric evidence checked', 'Export hashes checked'],
                          'report_url': base + '/report', 'record_url': base,
                          'files': [{'name': name, 'url': base + '/files/' + name,
                                     'sha256': hashlib.sha256((folder/name).read_bytes()).hexdigest()} for name in artifacts]}
            # Work registry is now gone; the calculation record is the completion marker.
            (folder / 'result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
            return result
        except Exception:
            (folder / 'failure.json').write_text(json.dumps({'status': 'failed', 'run_id': run_id}), encoding='utf-8')
            raise

    def load(self, run_id):
        if not re.fullmatch(r'[a-f0-9]{32}', run_id):
            raise FileNotFoundError(run_id)
        path = self.output_root / run_id / 'result.json'
        if not path.is_file():
            raise FileNotFoundError(run_id)
        return json.loads(path.read_text(encoding='utf-8'))

    def artifact(self, run_id, name):
        result = self.load(run_id)
        item = next((f for f in result['files'] if f['name'] == name), None)
        if item is None:
            raise FileNotFoundError(name)
        path = self.output_root / run_id / name
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != item['sha256']:
            raise FileNotFoundError(name)
        return path
