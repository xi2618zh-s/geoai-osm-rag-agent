"""Real public-fixture integration; failures injected only at service boundaries."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from src.scenarios import ScenarioError
from src.web.analysis_service import AnalysisService
from src.web.analysis_routes import create_demo_app

DEFAULT = dict(scenario='school_coverage', place='Lund', distance_m=1000, age_band='5-9', minimum_children=10)


class AnalysisTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.service = AnalysisService(Path(self.temp.name))
        self.client = create_demo_app(self.service).test_client()

    def test_real_execution_parameters_restart_and_isolation(self):
        # Both requests execute concurrently against independent registries.
        changed = dict(DEFAULT, distance_m=1500, minimum_children=20)
        with ThreadPoolExecutor(max_workers=2) as pool:
            a, b = list(pool.map(lambda body: self.service.analyse(body, run=True), [DEFAULT, changed]))
        self.assertNotEqual(a['run_id'], b['run_id'])
        metrics = {m['metric_id']: m['value'] for m in a['report']['metrics']}
        self.assertEqual([metrics[k] for k in ['gap_cell_count','gap_children','school_count','grid_cell_count']], [11,178,132,453])
        for result, request in [(a, DEFAULT), (b, changed)]:
            self.assertIn('nearest_features', result['tool_calls'])
            self.assertGreaterEqual(result['tool_calls'].count('validate_geometry'), 6)
            self.assertEqual(result['report']['parameters']['distance_threshold_m'], request['distance_m'])
            restarted = AnalysisService(Path(self.temp.name))
            self.assertEqual(restarted.load(result['run_id']), result)
            self.assertFalse(list((Path(self.temp.name)/result['run_id']).glob('work-*')))
            data = json.loads(restarted.artifact(result['run_id'], 'coverage_gaps.geojson').read_bytes())
            for f in data['features']:
                self.assertGreater(f['properties']['distance_m'], request['distance_m'])
                self.assertGreaterEqual(f['properties']['left_population_age_5_9'], request['minimum_children'])
            for item in result['files']:
                response = self.client.get(item['url'])
                self.assertEqual(response.status_code, 200)
                self.assertEqual(hashlib.sha256(response.data).hexdigest(), item['sha256'])
        self.assertNotEqual(a['report']['downloads'][0]['sha256'], b['report']['downloads'][0]['sha256'])

    def test_zero_results_are_valid(self):
        result = self.service.analyse(dict(DEFAULT, minimum_children=999999), run=True)
        self.assertEqual(result['status'], 'success')
        self.assertEqual(result['report']['table']['total_rows'], 0)
        self.assertEqual(json.loads(self.service.artifact(result['run_id'], 'coverage_gaps.geojson').read_bytes())['features'], [])

    def test_preview_clarification_and_unsupported_do_not_execute(self):
        with patch('src.web.analysis_service.execute_scenario_plan', side_effect=AssertionError('must not execute')):
            self.assertEqual(self.service.analyse(DEFAULT)['status'], 'plan')
            for body, reason in [(dict(DEFAULT, distance_m=None),'missing_parameters'),
                                 (dict(DEFAULT, place='Stockholm'),'place_not_covered'),
                                 (dict(DEFAULT, age_band='6-12'),'age_band_unsupported'),
                                 (dict(DEFAULT, distance_m=2001),'distance_out_of_range')]:
                response = self.client.post('/api/analyses/run', json=body)
                self.assertEqual(response.json['status'], 'clarification')
                self.assertEqual(response.json['clarification']['reason_code'], reason)
            self.assertEqual(self.service.analyse({'command':'show all cities'})['status'], 'unsupported')
        self.assertFalse(list(Path(self.temp.name).glob('*/result.json')))

    def test_integrity_and_report_validation_block_success(self):
        with patch('src.web.analysis_service.catalogue', side_effect=ScenarioError('DEMO_DATA_INTEGRITY','Restore snapshot')):
            response = self.client.post('/api/analyses/run', json=DEFAULT)
            self.assertEqual(response.status_code, 422)
            self.assertEqual(response.json['reason_code'], 'DEMO_DATA_INTEGRITY')
        with patch('src.web.analysis_service.validate_m12a_report', side_effect=ScenarioError('SCENARIO_REPORT_EVIDENCE_MISMATCH','Mismatch')):
            response = self.client.post('/api/analyses/run', json=DEFAULT)
            self.assertEqual(response.json['reason_code'], 'SCENARIO_REPORT_EVIDENCE_MISMATCH')
        self.assertFalse(list(Path(self.temp.name).glob('*/report.html')))
        self.assertFalse(list(Path(self.temp.name).glob('*/result.json')))

    def test_missing_or_tampered_manifests(self):
        from src.web.analysis_data import catalogue, PINS
        from src.datasets import LocalDatasetRegistry
        root = Path(self.temp.name)/'bad-data'
        root.mkdir()
        for relative in PINS:
            p=root/relative;p.parent.mkdir(parents=True,exist_ok=True);p.write_text('{}')
        with self.assertRaisesRegex(ScenarioError, 'missing or changed'):
            catalogue(LocalDatasetRegistry(Path(self.temp.name)/'registry'), root)

    def test_http_validation_and_path_guards(self):
        for body in [[], dict(DEFAULT, command='x'), dict(DEFAULT, distance_m=True), dict(DEFAULT, steps=[]),dict(DEFAULT, minimum_children=1.5)]:
            self.assertEqual(self.client.post('/api/analyses/run',json=body).status_code,422)
        self.assertEqual(self.client.post('/api/analyses/run',data='oops',content_type='application/json').status_code,400)
        self.assertEqual(self.client.post('/api/analyses/run',json=DEFAULT,headers={'Origin':'https://other.example'}).status_code,403)
        self.assertEqual(self.client.get('/api/analyses/../../README.md').status_code,404)
        self.assertEqual(self.client.get('/api/analyses/'+'a'*32+'/files/README.md').status_code,404)
        self.assertEqual(self.client.get('/explore').status_code,200)
        self.assertEqual(self.client.get('/assets/explore.js').status_code,200)

    def test_tool_failure_does_not_produce_report(self):
        from types import SimpleNamespace
        with patch('src.web.analysis_service.execute_scenario_plan', return_value=SimpleNamespace(status='failed',error_code='SCENARIO_TOOL_FAILED',failed_step='step_2')):
            response=self.client.post('/api/analyses/run',json=DEFAULT)
            self.assertEqual(response.json['reason_code'],'SCENARIO_TOOL_FAILED')
        self.assertFalse(list(Path(self.temp.name).glob('*/report.html')))


if __name__ == '__main__':
    unittest.main()
