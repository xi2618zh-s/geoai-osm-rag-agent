"""M12a school coverage gaps: scenario layer (ADR-0021) against the frozen protocol."""
from __future__ import annotations

import ast
import json
import tempfile
import unittest
from pathlib import Path

from pydantic import ValidationError

from src.agent.validation import ValidationPolicy
from src.datasets import LocalDatasetRegistry
from src.evaluation import m12a as evaluation
from src.scenarios import (
    SCENARIO_TOOL_REGISTRY, ScenarioClarification, ScenarioError, ScenarioPlan, ScenarioStep,
    crs_plausible, execute_scenario_plan, reconfirm_source_crs, validate_plan,
)
from src.scenarios.contracts import CrsEvidence
from src.scenarios.html import render_scenario_html
from src.scenarios.m12a import (
    build_m12a_report, interpret_m12a, plan_m12a, resume_m12a, validate_m12a_report,
)
from src.scenarios.report import FORBIDDEN_PHRASES, rendered_report, verify_metric_evidence

ROOT = Path(__file__).resolve().parents[1]
A1 = "find school coverage gaps farther than 1000 m where children aged 5-9 are at least 10 in Lund"
FULL = ["load_population_dataset", "reproject_dataset", "filter_attributes", "buffer_features",
        "nearest_features", "filter_attributes"]


def references():
    return json.loads((ROOT / "benchmarks/data/dev/m12a_references_v1.json").read_text(encoding="utf-8"))["references"]


class Workspace:
    def __init__(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.registry = LocalDatasetRegistry(Path(self.tmp.name) / "registry")

    def catalogue(self, variant="standard"):
        return evaluation.build_catalogue(self.registry, variant, workdir=Path(self.tmp.name))

    def close(self):
        self.tmp.cleanup()


def policy(**extra):
    return ValidationPolicy(allow_empty=True, max_features=10000, **extra)


class GrammarTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ws = Workspace()
        cls.cat, _ = cls.ws.catalogue()

    @classmethod
    def tearDownClass(cls):
        cls.ws.close()

    def test_full_command_and_normalisation(self):
        result = interpret_m12a("  FIND school coverage   gaps farther than 1.5 km where children aged 10-14 are at least 10 in lund ", self.cat)
        self.assertEqual(result.status, "plan")
        self.assertEqual(result.grammar_version, "m12a-grammar-v1")
        self.assertEqual(result.intent.parameters, {"distance_m": 1500, "age_band": "10-14", "threshold": 10})
        self.assertEqual(result.intent.place, "Lund")

    def test_clarification_reasons_follow_frozen_order(self):
        cases = {
            "find school coverage gaps in Lund": ("missing_parameters", ["distance", "age_band", "threshold"], []),
            "find school coverage gaps farther than 2500 m where children aged 6-12 are at least 10 in Lund": ("distance_out_of_range", ["distance"], []),
            "find school coverage gaps farther than 1000 m where children aged 6-12 are at least 10 in Lund": ("age_band_unsupported", ["age_band"], ["0-4", "5-9", "10-14", "15-19"]),
            "find school coverage gaps farther than 1000 m where children aged 5-9 are at least 0 in Lund": ("threshold_out_of_range", ["threshold"], []),
            "find school coverage gaps farther than 1000 m where children aged 5-9 are at least 10 in Malmo": ("place_not_covered", ["place"], ["Lund"]),
            "find school coverage gaps farther than 1000 m": ("missing_parameters", ["age_band", "threshold", "place"], []),
        }
        for command, (reason, missing, choices) in cases.items():
            result = interpret_m12a(command, self.cat)
            self.assertEqual(result.status, "clarification", command)
            self.assertEqual(result.clarification.reason_code, reason, command)
            self.assertEqual(result.clarification.missing_fields, missing, command)
            self.assertEqual(result.clarification.choices, choices, command)

    def test_unrecognised_command_is_unsupported(self):
        result = interpret_m12a("rank schools by pupils in Lund", self.cat)
        self.assertEqual((result.status, result.reason_code), ("unsupported", "command_not_recognized"))

    def test_resume_only_answers_requested_slots_once(self):
        first = interpret_m12a("find school coverage gaps in Lund", self.cat)
        resumed = resume_m12a("find school coverage gaps in Lund", first.clarification,
                              "distance 1000 m; age band 5-9; threshold 10", self.cat)
        self.assertEqual(resumed.status, "plan")
        self.assertEqual(resumed.intent.command, A1)
        for answer in ("place Lund", "distance 1000 m; distance 900 m; age band 5-9; threshold 10", "distance one km"):
            with self.assertRaises(ScenarioError) as raised:
                resume_m12a("find school coverage gaps in Lund", first.clarification, answer, self.cat)
            self.assertEqual(raised.exception.code, "SCENARIO_CLARIFICATION_INVALID")
        with self.assertRaises(ScenarioError):
            resume_m12a("find school coverage gaps in Malmo", first.clarification, "distance 1000 m", self.cat)

    def test_clarification_contract_is_closed(self):
        with self.assertRaises(ValidationError):
            ScenarioClarification(original_command="x", reason_code="guess", question="q", missing_fields=["distance"])


class PlanTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ws = Workspace()
        cls.cat, _ = cls.ws.catalogue()
        cls.mislabeled, _ = Workspace.catalogue(cls.ws, "poi_crs_mislabeled")

    @classmethod
    def tearDownClass(cls):
        cls.ws.close()

    def test_plan_inserts_reprojection_only_for_declared_foreign_crs(self):
        intent = interpret_m12a(A1, self.cat).intent
        plan = plan_m12a(intent, self.cat)
        self.assertEqual([s.tool for s in plan.steps], FULL)
        self.assertEqual(plan.steps[2].parameters, {"conditions": [{"field": "amenity", "operator": "eq", "value": "school"}], "logic": "all"})
        self.assertEqual(plan.steps[5].parameters["conditions"], [
            {"field": "distance_m", "operator": "gt", "value": 1000},
            {"field": "left_population_age_5_9", "operator": "gte", "value": 10}])
        self.assertEqual([s.empty_policy for s in plan.steps], ["reject"] * 5 + ["allow"])
        self.assertEqual(len(plan.preview), 6)
        mislabeled = plan_m12a(interpret_m12a(A1, self.mislabeled).intent, self.mislabeled)
        self.assertNotIn("reproject_dataset", [s.tool for s in mislabeled.steps])

    def test_static_gate_rejects_bad_plans(self):
        plan = plan_m12a(interpret_m12a(A1, self.cat).intent, self.cat)
        steps = [s.model_dump() for s in plan.steps]
        bad = {
            "unknown tool": [{**steps[0], "tool": "run_python"}] + steps[1:],
            "future dependency": [steps[0], {**steps[1], "depends_on": ["step_3"]}] + steps[2:],
            "invalid parameter": steps[:3] + [{**steps[3], "parameters": {"distance_m": -5}}] + steps[4:],
            "unknown role": [{**steps[0], "input_roles": ["missing_role"]}] + steps[1:],
            "reused output": steps[:2] + [{**steps[2], "output_role": "population_grid"}] + steps[3:],
        }
        for label, payload in bad.items():
            candidate = plan.model_copy(update={"steps": [ScenarioStep.model_validate(item) for item in payload]})
            with self.assertRaises(ScenarioError, msg=label) as raised:
                validate_plan(candidate, self.cat)
            self.assertEqual(raised.exception.code, "SCENARIO_PLAN_INVALID")
        with self.assertRaises(ValidationError):
            ScenarioPlan.model_validate({**plan.model_dump(), "steps": steps * 3})


class PlausibilityAndRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.ws = Workspace()

    def tearDown(self):
        self.ws.close()

    def test_coordinates_must_fit_declared_crs_and_study_area(self):
        cat, _ = self.ws.catalogue()
        bad, _ = self.ws.catalogue("poi_crs_mislabeled")
        self.assertTrue(crs_plausible(cat.entries["osm_poi"].dataset, cat)[0])
        self.assertTrue(crs_plausible(cat.entries["population_grid_raw"].dataset, cat)[0])
        self.assertFalse(crs_plausible(bad.entries["osm_poi"].dataset, bad)[0])

    def test_mislabeled_crs_is_rejected_without_any_tool_call(self):
        cat, _ = self.ws.catalogue("poi_crs_mislabeled")
        plan = plan_m12a(interpret_m12a(A1, cat).intent, cat)
        outcome = execute_scenario_plan(plan, cat, self.ws.registry, policy=policy())
        self.assertEqual((outcome.status, outcome.error_code), ("partial", "SCENARIO_CRS_IMPLAUSIBLE"))
        self.assertEqual(outcome.tool_calls, [])
        self.assertEqual(outcome.recoveries, [])

    def test_crs_repair_policy_is_refused(self):
        cat, _ = self.ws.catalogue("poi_crs_mislabeled")
        plan = plan_m12a(interpret_m12a(A1, cat).intent, cat)
        outcome = execute_scenario_plan(plan, cat, self.ws.registry, policy=policy(allow_crs_repair=True))
        self.assertEqual(outcome.error_code, "SCENARIO_POLICY_INVALID")
        self.assertEqual(outcome.tool_calls, [])

    def test_verified_alternate_snapshot_and_single_replan(self):
        cat, extra = self.ws.catalogue("poi_crs_mislabeled")
        intent = interpret_m12a(A1, cat).intent
        plan = plan_m12a(intent, cat)
        outcome = execute_scenario_plan(
            plan, cat, self.ws.registry, policy=policy(alternate_sources={"osm_poi": extra["alternate_osm_poi"]}),
            replanner=lambda catalogue: plan_m12a(intent, catalogue))
        self.assertEqual(outcome.status, "success")
        self.assertEqual([(e.action, e.status) for e in outcome.recoveries], [("alternate_source", "succeeded"), ("replan_once", "succeeded")])
        self.assertEqual(outcome.replan_count, 1)
        self.assertFalse(outcome.first_pass_success)
        self.assertEqual(outcome.initial_plan_tools, [s.tool for s in plan.steps])
        self.assertEqual(outcome.invoked_tools, FULL)

    def test_reconfirmation_needs_trusted_matching_manifest(self):
        cat, _ = self.ws.catalogue("poi_crs_mislabeled")
        wrong = cat.entries["osm_poi"].dataset
        fixed = reconfirm_source_crs(cat, "osm_poi", CrsEvidence(
            manifest="data/fixtures/osm/m12_lund_poi_v1/manifest.json", file="lund_poi_points_v1.geojson"), self.ws.registry)
        entry = fixed.entries["osm_poi"]
        self.assertEqual((entry.declared_crs, entry.dataset.crs), ("EPSG:4326", "EPSG:4326"))
        self.assertEqual(entry.dataset.lineage[-1].operation, "crs_reconfirmed")
        self.assertEqual(entry.dataset.lineage[-1].parent_ids, [wrong.id])
        self.assertEqual(entry.dataset.content_hash, wrong.content_hash)
        invalid = [
            CrsEvidence(manifest="data/fixtures/population/scb/m12_lund_v1/manifest.json", file="scb_population_grid_2025_lund_20km_raw.geojson"),
            CrsEvidence(manifest="data/fixtures/osm/m12_lund_poi_v1/manifest.json", file="missing.geojson"),
            CrsEvidence(manifest="data/fixtures/../fixtures/osm/m12_lund_poi_v1/manifest.json", file="lund_poi_points_v1.geojson"),
            CrsEvidence(manifest="benchmarks/data/dev/m12a_references_v1.json", file="lund_poi_points_v1.geojson"),
        ]
        for evidence in invalid:
            with self.assertRaises(ScenarioError) as raised:
                reconfirm_source_crs(cat, "osm_poi", evidence, self.ws.registry)
            self.assertEqual(raised.exception.code, "SCENARIO_CRS_EVIDENCE_INVALID")

    def test_empty_school_layer_is_not_reported_as_no_gaps(self):
        cat, _ = self.ws.catalogue("poi_without_schools")
        outcome = execute_scenario_plan(plan_m12a(interpret_m12a(A1, cat).intent, cat), cat, self.ws.registry, policy=policy())
        self.assertEqual((outcome.status, outcome.error_code, outcome.failed_step), ("partial", "SCENARIO_EMPTY_UNEXPECTED", "step_3"))
        self.assertEqual(outcome.invoked_tools, ["load_population_dataset", "reproject_dataset", "filter_attributes"])


class ExecutionAndReportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ws = Workspace()
        cls.cat, _ = cls.ws.catalogue()
        cls.intent = interpret_m12a(A1, cls.cat).intent
        cls.plan = plan_m12a(cls.intent, cls.cat)
        cls.outcome = execute_scenario_plan(cls.plan, cls.cat, cls.ws.registry, policy=policy())
        cls.report = build_m12a_report(cls.intent, cls.outcome, cls.cat, cls.ws.registry)

    @classmethod
    def tearDownClass(cls):
        cls.ws.close()

    def test_success_matches_independent_oracle(self):
        ref = references()["d1000_b5-9_n10"]
        self.assertEqual(self.outcome.status, "success")
        self.assertTrue(self.outcome.first_pass_success)
        self.assertEqual(self.outcome.invoked_tools, FULL)
        self.assertLessEqual(self.outcome.tool_call_count, 64)
        gaps = self.ws.registry.read(self.outcome.final_dataset.id).records
        self.assertEqual(sorted(f["properties"]["left_grid_id"] for f in gaps), ref["gap_grid_ids"])
        values = {m.metric_id: m.value for m in self.report.metrics}
        for key in ("gap_cell_count", "gap_children", "total_children", "school_count", "grid_cell_count", "band_not_reported_cells"):
            self.assertEqual(values[key], ref[key], key)
        self.assertAlmostEqual(values["gap_children_share"], ref["gap_children_share"], delta=1e-12)
        self.assertNotIn("max_gap_distance_m", values)

    def test_report_evidence_licenses_and_wording(self):
        validate_m12a_report(self.report, self.intent, self.outcome, self.cat, self.ws.registry)
        verify_metric_evidence(self.report, self.ws.registry)
        self.assertEqual(len(self.report.layers), 4)
        licenses = {(item.license, item.attribution) for item in self.report.data_licenses}
        self.assertIn(("CC0-1.0", "Statistics Sweden (SCB)"), licenses)
        self.assertIn(("ODbL-1.0", "© OpenStreetMap contributors"), licenses)
        text = " ".join(rendered_report(self.report) + self.report.limitations + [self.report.grammar_note]).lower()
        self.assertIn("restricted command grammar", text)
        self.assertIn("not the exact population", text)
        for phrase in FORBIDDEN_PHRASES:
            self.assertNotIn(phrase, text)
        tampered = self.report.model_copy(update={"metrics": [
            m.model_copy(update={"value": m.value + 1}) if m.metric_id == "gap_children" else m for m in self.report.metrics]})
        with self.assertRaises(ScenarioError) as raised:
            validate_m12a_report(tampered, self.intent, self.outcome, self.cat, self.ws.registry)
        self.assertEqual(raised.exception.code, "SCENARIO_REPORT_EVIDENCE_MISMATCH")
        with self.assertRaises(ScenarioError):
            verify_metric_evidence(tampered, self.ws.registry)

    def test_report_templates_reject_literal_numbers(self):
        with self.assertRaises(ValidationError):
            self.report.model_validate({**self.report.model_dump(mode="json"), "headline_template": "Found 11 gaps."})

    def test_html_is_self_contained(self):
        page = render_scenario_html(self.report)
        self.assertNotIn("<script src", page.lower())
        self.assertNotIn("<link", page.lower())
        for label in [layer.label for layer in self.report.layers] + ["restricted command grammar"]:
            self.assertIn(json.dumps(label, ensure_ascii=False)[1:-1].lower(), page.lower())

    def test_legit_empty_result_still_reports_and_downloads(self):
        intent = interpret_m12a("find school coverage gaps farther than 2000 m where children aged 5-9 are at least 20 in Lund", self.cat).intent
        outcome = execute_scenario_plan(plan_m12a(intent, self.cat), self.cat, self.ws.registry, policy=policy())
        self.assertEqual((outcome.status, outcome.final_dataset.row_count), ("success", 0))
        report = build_m12a_report(intent, outcome, self.cat, self.ws.registry)
        self.assertEqual({m.metric_id: m.value for m in report.metrics}["gap_cell_count"], 0)
        self.assertEqual(len(report.downloads), 2)


class FaultInjectionTests(unittest.TestCase):
    def setUp(self):
        self.ws = Workspace()
        self.cat, _ = self.ws.catalogue()
        self.plan = plan_m12a(interpret_m12a(A1, self.cat).intent, self.cat)

    def tearDown(self):
        self.ws.close()

    def flaky(self, failures):
        class Tools:
            def __init__(self, inner, remaining):
                self.inner, self.remaining = inner, remaining

            def get(self, name):
                return self.inner.get(name)

            def invoke(self, name, payload, *, datasets):
                if name == "buffer_features" and self.remaining:
                    self.remaining -= 1
                    raise TimeoutError("injected")
                return self.inner.invoke(name, payload, datasets=datasets)
        return Tools(SCENARIO_TOOL_REGISTRY, failures)

    def test_timeout_is_retried_once(self):
        outcome = execute_scenario_plan(self.plan, self.cat, self.ws.registry, policy=policy(), tools=self.flaky(1))
        self.assertEqual(outcome.status, "success")
        self.assertEqual(next(r for r in outcome.step_records if r.tool == "buffer_features").attempts, 2)
        outcome = execute_scenario_plan(self.plan, self.cat, self.ws.registry, policy=policy(), tools=self.flaky(2))
        self.assertEqual((outcome.status, outcome.error_code, outcome.failed_step), ("partial", "SCENARIO_TOOL_TIMEOUT", "step_4"))

    def test_tool_call_cap(self):
        outcome = execute_scenario_plan(self.plan, self.cat, self.ws.registry, policy=policy(), max_tool_calls=5)
        self.assertEqual((outcome.status, outcome.error_code), ("partial", "SCENARIO_BUDGET_EXCEEDED"))
        self.assertLessEqual(outcome.tool_call_count, 5)
        with self.assertRaises(ValueError):
            execute_scenario_plan(self.plan, self.cat, self.ws.registry, policy=policy(), max_tool_calls=65)


class IsolationTests(unittest.TestCase):
    def test_existing_registries_and_agent_whitelist_unchanged(self):
        from src.agent.dynamic_planner import PUBLIC_TOOLS
        from src.tools import ACCESSIBILITY_TOOL_REGISTRY, ANALYSIS_TOOL_REGISTRY, POPULATION_TOOL_REGISTRY
        self.assertEqual(len(PUBLIC_TOOLS), 6)
        self.assertEqual(len(ANALYSIS_TOOL_REGISTRY.describe()), 10)
        self.assertEqual(len(POPULATION_TOOL_REGISTRY.describe()), 11)
        self.assertEqual(len(ACCESSIBILITY_TOOL_REGISTRY.describe()), 12)
        self.assertEqual({s.name for s in SCENARIO_TOOL_REGISTRY.describe()}, {s.name for s in ACCESSIBILITY_TOOL_REGISTRY.describe()})

    def test_oracle_is_independent_of_the_system(self):
        tree = ast.parse((ROOT / "benchmarks/m12a_oracle.py").read_text(encoding="utf-8"))
        modules = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)} | {
            a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
        self.assertFalse({m for m in modules if m and m.split(".")[0] == "src"})

    def test_freeze_manifest_verifies_and_detects_changes(self):
        from scripts import freeze_m12a
        freeze_m12a.verify()
        with tempfile.TemporaryDirectory() as tmp:
            manifest = json.loads(freeze_m12a.MANIFEST.read_text(encoding="utf-8"))
            manifest["files"]["benchmarks/m12a_oracle.py"] = "0" * 64
            path = Path(tmp) / "manifest.json"
            path.write_text(json.dumps(manifest), encoding="utf-8")
            with self.assertRaises(SystemExit):
                freeze_m12a.verify(path)


if __name__ == "__main__":
    unittest.main()
