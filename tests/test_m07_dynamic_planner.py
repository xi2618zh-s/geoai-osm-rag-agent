"""M07 public offline planner contract and frozen-case evaluation."""
import json
from pathlib import Path
import unittest

from src.agent.dynamic_planner import (
    M07PlanRequest, PlanError, plan_request, resume_clarification,
    validate_model_plan_json, plan_with_model,
)

ROOT = Path(__file__).resolve().parents[1]
CASES = [json.loads(line) for line in (ROOT / "benchmarks/data/dev/m07_plan_cases_v1.jsonl").read_text(encoding="utf-8").splitlines()]

class FrozenPlanCases(unittest.TestCase):
    pass

def make_case(row):
    def test(self):
        result = plan_request(row["request"])
        expected = row["expected"]
        self.assertEqual(result.status, expected["status"])
        self.assertEqual(result.task_class, "aggregate_by_region" if row["task_class"] == "aggregate_sum" else row["task_class"])
        if expected["status"] == "plan":
            self.assertIsNotNone(result.plan)
            self.assertEqual([s.tool for s in result.tool_steps], expected["tools"])
            self.assertEqual(result.business_parameters, expected["parameters"])
            self.assertGreaterEqual(result.confidence, 0)
            self.assertLessEqual(result.confidence, 1)
            self.assertTrue(result.evidence)
        else:
            self.assertEqual(result.error_code, "M07_UNSUPPORTED_TASK")
            self.assertEqual(result.tool_steps, [])
    return test

for row in CASES:
    setattr(FrozenPlanCases, "test_" + row["case_id"], make_case(row))

V2_CASES = [json.loads(line) for line in (ROOT / "benchmarks/data/dev/m07_plan_cases_v2.jsonl").read_text(encoding="utf-8").splitlines()]
class SupplementalSupportedCases(unittest.TestCase):
    pass
for row in V2_CASES:
    if row["task_class"] == "aggregate_sum":
        setattr(SupplementalSupportedCases, "test_" + row["case_id"], make_case(row))

class M07AdversarialTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.base = CASES[0]["request"]

    def test_frozen_case_count(self):
        self.assertEqual(len(CASES), 80)
        self.assertEqual({x["task_class"] for x in CASES}, {"search","filtered_search","within_distance","nearest","within","spatial_join","aggregate_by_region","rank"})

    def test_missing_place_clarifies(self):
        request = dict(self.base, query="search sites")
        result = plan_request(request)
        self.assertEqual(result.status, "clarification")
        self.assertEqual(result.clarification.reason_code, "missing_place")
        resumed = resume_clarification(request, result.clarification, "Lund")
        self.assertEqual(resumed.status, "plan")
        self.assertEqual(resumed.intent.query, "search sites in Lund")

    def test_missing_distance_clarifies(self):
        request = dict(self.base, query="find sites within of schools in Lund")
        result = plan_request(request)
        self.assertEqual(result.clarification.reason_code, "missing_distance")
        resumed = resume_clarification(request, result.clarification, "250 m")
        self.assertEqual(resumed.business_parameters["distance_m"], 250)

    def test_conflicting_distance_clarifies(self):
        request = dict(self.base, query="find sites within 100 m and 200 m of schools in Lund")
        result = plan_request(request)
        self.assertEqual(result.clarification.reason_code, "conflicting_constraints")

    def test_unknown_role_rejected(self):
        result = plan_request(dict(self.base, query="search secret in Lund"))
        self.assertEqual(result.error_code, "M07_DATASET_UNKNOWN")

    def test_unsupported_task_rejected(self):
        result = plan_request(dict(self.base, query="forecast traffic in Lund"))
        self.assertEqual(result.error_code, "M07_UNSUPPORTED_TASK")

    def test_missing_critical_crs_rejected(self):
        request = dict(self.base)
        request.pop("analysis_crs")
        with self.assertRaises(Exception):
            M07PlanRequest.model_validate(request)

    def test_injected_model_proposer_receives_public_contract(self):
        canonical = plan_request(self.base)
        seen = []
        def propose(prompt):
            seen.append(json.loads(prompt))
            return canonical.model_dump_json()
        result = plan_with_model(self.base, propose)
        self.assertEqual(result.tool_steps, canonical.tool_steps)
        self.assertEqual(len(seen), 1)
        self.assertEqual(seen[0]["contract"], "PlanningResult.v1.0")
        self.assertNotIn("repair_geometry", [item["name"] for item in seen[0]["public_tools"]])

    def test_model_proposer_failure_classified(self):
        def propose(prompt):
            raise RuntimeError("service failed")
        with self.assertRaises(PlanError) as ctx:
            plan_with_model(self.base, propose)
        self.assertEqual(ctx.exception.code, "M07_MODEL_CALL_FAILED")

    def test_model_repair_failure_classified(self):
        def repair(raw, reason):
            raise RuntimeError("repair failed")
        with self.assertRaises(PlanError) as ctx:
            validate_model_plan_json("{", self.base, repair=repair)
        self.assertEqual(ctx.exception.code, "M07_MODEL_OUTPUT_INVALID")

    def test_injected_model_not_called_on_clarification(self):
        request = dict(self.base, query="search sites")
        result = plan_with_model(request, lambda prompt: self.fail("model must not run"))
        self.assertEqual(result.status, "clarification")

    def test_model_invalid_json_one_repair(self):
        result = plan_request(self.base)
        calls=[]
        def repair(raw, reason):
            calls.append((raw,reason))
            return result.model_dump_json()
        parsed = validate_model_plan_json("{", self.base, repair=repair)
        self.assertEqual(parsed.status, "plan")
        self.assertEqual(len(calls), 1)

    def test_model_invalid_json_repair_exhausted(self):
        with self.assertRaises(PlanError) as ctx:
            validate_model_plan_json("{", self.base, repair=lambda raw, reason: "{")
        self.assertEqual(ctx.exception.code, "M07_MODEL_OUTPUT_INVALID")

    def test_model_unregistered_tool_rejected(self):
        result=plan_request(self.base).model_dump(mode="json")
        result["tool_steps"][0]["tool"]="run_python"
        with self.assertRaises(PlanError) as ctx:
            validate_model_plan_json(json.dumps(result), self.base)
        self.assertEqual(ctx.exception.code, "M07_TOOL_FORBIDDEN")

    def test_model_private_registered_tool_rejected(self):
        result=plan_request(self.base).model_dump(mode="json")
        result["tool_steps"][0]["tool"]="repair_geometry"
        with self.assertRaises(PlanError) as ctx:
            validate_model_plan_json(json.dumps(result), self.base)
        self.assertEqual(ctx.exception.code, "M07_TOOL_FORBIDDEN")

    def test_model_path_rejected(self):
        result=plan_request(next(x for x in CASES if x["task_class"]=="filtered_search")["request"]).model_dump(mode="json")
        result["tool_steps"][0]["parameters"]["conditions"][0]["value"]="C:\\secret\\file"
        with self.assertRaises(PlanError) as ctx:
            validate_model_plan_json(json.dumps(result), next(x for x in CASES if x["task_class"]=="filtered_search")["request"])
        self.assertEqual(ctx.exception.code, "M07_PARAMETER_INVALID")

    def test_model_future_dependency_rejected(self):
        case=next(x for x in CASES if x["task_class"]=="within_distance")
        result=plan_request(case["request"]).model_dump(mode="json")
        result["tool_steps"][0]["depends_on"]=["step_2"]
        with self.assertRaises(PlanError) as ctx:
            validate_model_plan_json(json.dumps(result), case["request"])
        self.assertEqual(ctx.exception.code, "M07_DEPENDENCY_INVALID")

    def test_model_cycle_rejected(self):
        case=next(x for x in CASES if x["task_class"]=="within_distance")
        result=plan_request(case["request"]).model_dump(mode="json")
        result["tool_steps"][0]["depends_on"]=["step_2"]
        result["tool_steps"][1]["depends_on"]=["step_1"]
        with self.assertRaises(PlanError):
            validate_model_plan_json(json.dumps(result), case["request"])

    def test_model_conflicting_parameter_rejected(self):
        case=next(x for x in CASES if x["task_class"]=="within_distance")
        result=plan_request(case["request"]).model_dump(mode="json")
        result["tool_steps"][0]["parameters"]["distance_m"]=999
        with self.assertRaises(PlanError) as ctx:
            validate_model_plan_json(json.dumps(result), case["request"])
        self.assertEqual(ctx.exception.code, "M07_PARAMETER_CONFLICT")

    def test_model_budget_rejected(self):
        result=plan_request(self.base).model_dump(mode="json")
        result["tool_steps"] *= 9
        with self.assertRaises(PlanError) as ctx:
            validate_model_plan_json(json.dumps(result), self.base)
        self.assertEqual(ctx.exception.code, "M07_BUDGET_EXCEEDED")

    def test_model_retry_cap_rejected(self):
        result=plan_request(self.base).model_dump(mode="json")
        result["tool_steps"][0]["max_retries"]=2
        with self.assertRaises(PlanError) as ctx:
            validate_model_plan_json(json.dumps(result), self.base)
        self.assertEqual(ctx.exception.code, "M07_BUDGET_EXCEEDED")

    def test_model_wrong_parameter_type_rejected(self):
        case=next(x for x in CASES if x["task_class"]=="nearest")
        result=plan_request(case["request"]).model_dump(mode="json")
        result["tool_steps"][0]["parameters"]["k"]="two"
        with self.assertRaises(PlanError) as ctx:
            validate_model_plan_json(json.dumps(result), case["request"])
        self.assertEqual(ctx.exception.code, "M07_PARAMETER_INVALID")

    def test_geographic_distance_rejected(self):
        case=next(x for x in CASES if x["task_class"]=="within_distance")
        request=json.loads(json.dumps(case["request"]))
        request["datasets"]["schools"]["crs"]="EPSG:4326"
        result=plan_request(request)
        self.assertEqual(result.error_code, "M07_CRS_INCOMPATIBLE")

    def test_model_false_evidence_rejected(self):
        result=plan_request(self.base).model_dump(mode="json")
        result["evidence"]=["made up"]
        with self.assertRaises(PlanError) as ctx:
            validate_model_plan_json(json.dumps(result), self.base)
        self.assertEqual(ctx.exception.code, "M07_PARAMETER_CONFLICT")

    def test_invalid_analysis_crs_rejected(self):
        result=plan_request(dict(self.base, analysis_crs="EPSG:999999", output_crs="EPSG:999999"))
        self.assertEqual(result.error_code, "M07_CRS_INCOMPATIBLE")

    def test_invalid_place_crs_rejected(self):
        result=plan_request(dict(self.base, place_crs="EPSG:999999"))
        self.assertEqual(result.error_code, "M07_CRS_INCOMPATIBLE")

    def test_output_crs_without_reprojection_rejected(self):
        result=plan_request(dict(self.base, output_crs="EPSG:4326"))
        self.assertEqual(result.error_code, "M07_CRS_INCOMPATIBLE")

    def test_missing_declared_filter_field_rejected(self):
        result=plan_request(dict(self.base, query="search sites where missing = cafe in Lund"))
        self.assertEqual(result.error_code, "M07_FIELD_UNKNOWN")

    def test_gtfs_isochrone_unsupported(self):
        result=plan_request(dict(self.base, query="show transit isochrone in Lund"))
        self.assertEqual(result.error_code, "M07_UNSUPPORTED_TASK")

    def test_output_preserves_original_query(self):
        result=plan_request(self.base)
        self.assertEqual(result.intent.query, self.base["query"])
        self.assertEqual(result.plan.intent, result.intent)

class M07ApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from app_min import app
        cls.client = app.test_client()

    def test_preview_plan(self):
        response = self.client.post("/plan-preview", json={"request": CASES[0]["request"]})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["status"], "plan")
        self.assertTrue(response.json["plan_preview"])
        self.assertEqual(response.json["confidence"], 1.0)

    def test_preview_clarification(self):
        request = dict(CASES[0]["request"], query="search sites")
        response = self.client.post("/plan-preview", json={"request": request})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["status"], "clarification")

    def test_preview_invalid_request(self):
        response = self.client.post("/plan-preview", json={"request": {"query": "search sites in Lund"}})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json["error_code"], "INVALID_REQUEST")

    def test_preview_clarification_resume(self):
        request = dict(CASES[0]["request"], query="search sites")
        first = self.client.post("/plan-preview", json={"request": request})
        response = self.client.post("/plan-preview", json={
            "request": request, "clarification": first.json["clarification"], "answer": "Lund",
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["status"], "plan")

    def test_preview_rejects_nonstring_answer(self):
        request = dict(CASES[0]["request"], query="search sites")
        first = self.client.post("/plan-preview", json={"request": request})
        response = self.client.post("/plan-preview", json={
            "request": request, "clarification": first.json["clarification"], "answer": 4,
        })
        self.assertEqual(response.status_code, 400)

    def test_plan_ui(self):
        response = self.client.get("/plan-ui")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Plan preview", response.data)
        response.close()

if __name__ == "__main__":
    unittest.main()


