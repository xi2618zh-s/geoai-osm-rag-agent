"""M10 result contract, source audit, exports, and offline browser UI."""
from __future__ import annotations

import base64
import csv
import hashlib
import io
import json
from pathlib import Path
import re
import unittest

from pydantic import ValidationError

from src.agent.dynamic_planner import plan_request
from src.agent.result_presentation import (
    AnalysisResult, PresentationError, VerifiedRun, build_analysis_result,
    render_analysis_html, rendered_report, validate_analysis_result,
)
from src.agent.validation import execute_validated_plan
import tests.test_m09_session_state as m09_fixture
FIX = m09_fixture.FIX

ROOT = Path(__file__).resolve().parents[1]
CASES = [json.loads(line) for line in (ROOT / "benchmarks/data/dev/m10_result_cases_v1.jsonl").read_text(encoding="utf-8").splitlines()]


class M10ResultTests(unittest.TestCase):
    def setUp(self):
        self.fixture = m09_fixture.M09SessionTests("test_frozen_matrix_shape")
        self.fixture.setUp()
        self.registry = self.fixture.datasets
        self.policy = self.fixture.policy

    def tearDown(self):
        self.fixture.tearDown()

    def verified(self, request, bindings):
        plan = plan_request(request)
        self.assertEqual(plan.status, "plan")
        outcome = execute_validated_plan(request, plan, bindings, self.registry, policy=self.policy)
        self.assertEqual(outcome.status, "success")
        return VerifiedRun(request=request, plan=plan, outcome=outcome)

    def case_evidence(self, case):
        if case["template"] == "coverage":
            request, bindings = self.fixture.base("within_distance")
            request["query"] = case["query"]
            return self.verified(request, bindings), None
        request, bindings = self.fixture.base("search")
        request["query"] = case["query"]
        primary = self.verified(request, bindings)
        if case["template"] == "comparison":
            compare_request = dict(request, query=case["compare_query"])
            return primary, self.verified(compare_request, bindings)
        return primary, None

    def run_case(self, case):
        primary, comparison = self.case_evidence(case)
        result = build_analysis_result(primary, self.registry, comparison=comparison)
        self.assertEqual(result.template, case["template"])
        self.assertGreaterEqual(len(result.layers), 2)
        self.assertEqual(len(result.sources), len({source.dataset_id for source in result.sources}))
        self.assertTrue(result.steps)
        self.assertTrue(result.assumptions)
        self.assertTrue(result.limitations)
        validate_analysis_result(result, primary, self.registry, comparison=comparison)
        for line in rendered_report(result):
            numbers = re.findall(r"(?<![A-Za-z])-?[0-9]+(?:\.[0-9]+)?", line)
            values = {str(metric.value) for metric in result.metrics}
            self.assertTrue(all(item in values for item in numbers), (line, values))
        if "expected_count" in case:
            self.assertEqual(result.metrics[0].value, case["expected_count"])
        if case["template"] == "coverage":
            self.assertEqual(result.metrics[2].value, case["distance_m"])
        if case["template"] == "comparison":
            self.assertEqual(result.metrics[0].value, case["baseline_count"])
            self.assertEqual(result.metrics[3].value, result.metrics[2].value - result.metrics[0].value)
        final_count = primary.outcome.final_dataset.row_count
        self.assertGreaterEqual(len(result.downloads), 2)
        geo = result.downloads[0]
        csv_artifact = result.downloads[1]
        geo_bytes = base64.b64decode(geo.href.split(",", 1)[1])
        csv_bytes = base64.b64decode(csv_artifact.href.split(",", 1)[1])
        self.assertEqual(hashlib.sha256(geo_bytes).hexdigest(), geo.sha256)
        self.assertEqual(hashlib.sha256(csv_bytes).hexdigest(), csv_artifact.sha256)
        self.assertEqual(len(json.loads(geo_bytes)["features"]), final_count)
        self.assertEqual(len(list(csv.reader(io.StringIO(csv_bytes.decode("utf-8-sig"))))), final_count + 1)
        html = render_analysis_html(result, primary, self.registry, comparison=comparison)
        self.assertIn('id="map"', html)
        self.assertIn('id="layers"', html)
        self.assertIn('id="chart"', html)
        self.assertIn('id="table"', html)
        self.assertIn('id="downloads"', html)
        self.assertIn('type="application/json"', html)
        self.assertNotIn("https://", html)
        self.assertNotIn("<script src=", html)
        return result, primary, comparison

    def test_rejects_partial_outcome(self):
        request, bindings = self.fixture.base("search")
        good = self.verified(request, bindings)
        ref = bindings["sites"]
        bad = ref.model_copy(update={"id": "ds_" + "0" * 32, "storage_uri": "registry://datasets/ds_" + "0" * 32 + "/data.geojson"})
        outcome = execute_validated_plan(request, good.plan, {"sites": bad}, self.registry, policy=self.policy)
        self.assertEqual(outcome.status, "partial")
        with self.assertRaises(PresentationError) as context:
            build_analysis_result(VerifiedRun(request=request, plan=good.plan, outcome=outcome), self.registry)
        self.assertEqual(context.exception.code, "M10_RESULT_UNVERIFIED")

    def test_rejects_declared_source_mismatch(self):
        request, bindings = self.fixture.base("search")
        request["datasets"]["sites"]["source"] = "fixture:wrong_source"
        run = self.verified(request, bindings)
        with self.assertRaises(PresentationError) as context:
            build_analysis_result(run, self.registry)
        self.assertEqual(context.exception.code, "M10_SOURCE_MISMATCH")
    def test_rejects_stale_output(self):
        request, bindings = self.fixture.base("search")
        run = self.verified(request, bindings)
        self.registry.release(run.outcome.final_dataset.id)
        with self.assertRaises(PresentationError) as context:
            build_analysis_result(run, self.registry)
        self.assertEqual(context.exception.code, "M10_DATASET_STALE")

    def test_rejects_fabricated_metric_and_download(self):
        result, primary, comparison = self.run_case(CASES[0])
        fake_metric = result.metrics[0].model_copy(update={"value": 999})
        tampered = result.model_copy(update={"metrics": [fake_metric, *result.metrics[1:]]})
        with self.assertRaises(PresentationError):
            validate_analysis_result(tampered, primary, self.registry, comparison=comparison)
        with self.assertRaises(PresentationError):
            render_analysis_html(tampered, primary, self.registry, comparison=comparison)
        fake_export = result.downloads[0].model_copy(update={"href": "data:application/json;base64,e30="})
        tampered = result.model_copy(update={"downloads": [fake_export, *result.downloads[1:]]})
        with self.assertRaises(PresentationError):
            validate_analysis_result(tampered, primary, self.registry)

    def test_rejects_unsupported_report_claims(self):
        result, _, _ = self.run_case(CASES[0])
        data = result.model_dump(mode="json")
        data["headline_template"] = "There are 999 verified features."
        with self.assertRaises(ValidationError):
            AnalysisResult.model_validate(data)
        data["headline_template"] = "Found {invented_count} features."
        with self.assertRaises(ValidationError):
            AnalysisResult.model_validate(data)

    def test_malicious_text_is_inert_in_html_and_csv(self):
        document = json.loads((FIX / "facilities_wgs84.geojson").read_text(encoding="utf-8"))
        document["features"] = document["features"][:1]
        document["features"][0]["properties"]["name"] = "</script><script>alert(1)</script>"
        document["features"][0]["properties"]["formula"] = "=2+2"
        path = Path(self.fixture.temp.name) / "malicious.geojson"
        path.write_text(json.dumps(document), encoding="utf-8")
        ref = self.registry.register_file(path, kind="vector", crs="EPSG:4326", source="fixture:malicious", source_version="m10-v1")
        request = {"query": "search sites in Lund", "datasets": {"sites": {"kind": "vector", "source": "fixture:malicious", "crs": "EPSG:4326", "required_fields": ["name"]}}, "place_crs": "EPSG:4326", "analysis_crs": "EPSG:4326", "output_crs": "EPSG:4326"}
        run = self.verified(request, {"sites": ref})
        result = build_analysis_result(run, self.registry)
        html = render_analysis_html(result, run, self.registry)
        self.assertNotIn("</script><script>alert(1)</script>", html)
        self.assertIn("\\u003c", html)
        csv_bytes = base64.b64decode(result.downloads[1].href.split(",", 1)[1])
        self.assertIn("'=2+2", csv_bytes.decode("utf-8-sig"))

    def test_comparison_requires_both_successes(self):
        primary, _ = self.case_evidence(CASES[8])
        request, bindings = self.fixture.base("search")
        request["query"] = "search sites where name = Alpha in Lund"
        good = self.verified(request, bindings)
        bad_ref = bindings["sites"].model_copy(update={"id": "ds_" + "0" * 32, "storage_uri": "registry://datasets/ds_" + "0" * 32 + "/data.geojson"})
        partial = execute_validated_plan(request, good.plan, {"sites": bad_ref}, self.registry, policy=self.policy)
        with self.assertRaises(PresentationError):
            build_analysis_result(primary, self.registry, comparison=VerifiedRun(request=request, plan=good.plan, outcome=partial))

    def test_flask_demo_route_and_released_snapshot(self):
        from app_min import app
        client = app.test_client()
        response = client.get("/analysis-ui")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'Fixed-distance proximity results', response.data)
        self.assertIn(b'id="map"', response.data)
        home = client.get("/").get_json()
        self.assertIn("GET /analysis-ui", home["endpoints"])
        published = json.loads((ROOT / "benchmarks/results/m10/dev/m10_demo_result_v2.json").read_text(encoding="utf-8"))
        html = (ROOT / "docs/demo/m10_analysis_result_v2.html").read_text(encoding="utf-8")
        self.assertEqual(response.data.decode("utf-8"), html)
        self.assertEqual(published["template"], "coverage")
        self.assertEqual(len(published["layers"]), 3)
        response.close()
    def test_successful_m08_replan_trace_can_be_presented(self):
        request, bindings = self.fixture.base("search")
        plan = plan_request(request)
        valid = bindings["sites"]
        invalid = valid.model_copy(update={"id": "ds_" + "0" * 32, "storage_uri": "registry://datasets/ds_" + "0" * 32 + "/data.geojson"})
        outcome = execute_validated_plan(
            request, plan, {"sites": invalid}, self.registry, policy=self.policy,
            replan=lambda req, old_plan, finding: (req, old_plan, {"sites": valid}),
        )
        self.assertEqual(outcome.status, "success")
        self.assertGreater(len(outcome.step_records), len(plan.tool_steps))
        result = build_analysis_result(VerifiedRun(request=request, plan=plan, outcome=outcome), self.registry)
        self.assertEqual(result.metrics[0].value, 3)
        self.assertTrue(any("replan_once" in item for item in result.warnings))
    def test_fixture_and_projected_crs_are_disclosed(self):
        result, _, _ = self.run_case(CASES[4])
        self.assertTrue(any("Synthetic fixture" in item for item in result.limitations))
        self.assertTrue(any("EPSG:4326" in item for item in result.limitations))
        self.assertNotEqual(result.title, "Verified spatial analysis")

    def test_comparison_rejects_mixed_crs_layers(self):
        search, _ = self.case_evidence(CASES[0])
        coverage, _ = self.case_evidence(CASES[4])
        with self.assertRaises(PresentationError) as context:
            build_analysis_result(search, self.registry, comparison=coverage)
        self.assertEqual(context.exception.code, "M10_CRS_MISMATCH")
    def test_frozen_matrix_shape(self):
        self.assertEqual(len(CASES), 12)
        self.assertEqual({kind: sum(item["template"] == kind for item in CASES) for kind in ("search", "coverage", "comparison")}, {"search": 4, "coverage": 4, "comparison": 4})


def make_case(case):
    def test(self):
        self.run_case(case)
    return test


for case in CASES:
    setattr(M10ResultTests, "test_" + case["case_id"], make_case(case))


if __name__ == "__main__":
    unittest.main()

