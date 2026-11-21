"""M09 conversation state, dependency reuse, and isolation over M04 fixtures."""
from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from src.agent.session import SessionManager, SessionError
from src.agent.validation import ValidationPolicy
from src.datasets import LocalDatasetRegistry
from src.tools import ANALYSIS_TOOL_REGISTRY, ReprojectDatasetInput

ROOT = Path(__file__).resolve().parents[1]
FIX = ROOT / "data/fixtures/tools/m04c/v1"
CASES = [json.loads(line) for line in (ROOT / "benchmarks/data/dev/m09_turn_cases_v1.jsonl").read_text(encoding="utf-8").splitlines()]


class M09SessionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="m09_")
        self.datasets = LocalDatasetRegistry(Path(self.temp.name) / "registry")
        self.manager = SessionManager(self.datasets)
        self.policy = ValidationPolicy(allow_empty=True, max_features=100)

    def tearDown(self):
        self.temp.cleanup()

    def register(self, file_name, crs, source):
        return self.datasets.register_file(
            FIX / file_name, kind="vector", crs=crs, source=source, source_version="m09-v1",
        )

    def base(self, kind):
        if kind == "search":
            sites = self.register("facilities_wgs84.geojson", "EPSG:4326", "fixture:lund")
            request = {
                "query": "search sites in Lund",
                "datasets": {"sites": {"kind": "vector", "source": "fixture:lund", "crs": "EPSG:4326", "required_fields": ["name"]}},
                "place_crs": "EPSG:4326", "analysis_crs": "EPSG:4326", "output_crs": "EPSG:4326",
            }
            return request, {"sites": sites}
        facilities = self.register("facilities_wgs84.geojson", "EPSG:4326", "fixture:lund")
        sites = ANALYSIS_TOOL_REGISTRY.invoke(
            "reproject_dataset",
            ReprojectDatasetInput(dataset=facilities, target_crs="EPSG:3857"),
            datasets=self.datasets,
        ).dataset
        schools = self.register("regions_3857.geojson", "EPSG:3857", "fixture:schools")
        request = {
            "query": "find sites within 75 m of schools in Lund",
            "datasets": {
                "sites": {"kind": "vector", "source": "fixture:lund", "crs": "EPSG:3857", "required_fields": ["name"]},
                "schools": {"kind": "vector", "source": "fixture:schools", "crs": "EPSG:3857", "required_fields": []},
            },
            "place_crs": "EPSG:4326", "analysis_crs": "EPSG:3857", "output_crs": "EPSG:3857",
        }
        return request, {"sites": sites, "schools": schools}

    def city_inputs(self, city):
        document = json.loads((FIX / "facilities_wgs84.geojson").read_text(encoding="utf-8"))
        document["features"] = document["features"][:1]
        city_file = Path(self.temp.name) / (city.lower() + ".geojson")
        city_file.write_text(json.dumps(document, ensure_ascii=False, sort_keys=True), encoding="utf-8")
        ref = self.datasets.register_file(
            city_file, kind="vector", crs="EPSG:4326",
            source=f"fixture:{city.lower()}", source_version="m09-v1",
        )
        request = {
            "query": f"search sites in {city}",
            "datasets": {"sites": {"kind": "vector", "source": f"fixture:{city.lower()}", "crs": "EPSG:4326", "required_fields": ["name"]}},
            "place_crs": "EPSG:4326", "analysis_crs": "EPSG:4326", "output_crs": "EPSG:4326",
        }
        return request, {"sites": ref}

    def run_case(self, row):
        request, bindings = self.base(row["base_class"])
        started = self.manager.start(request, bindings, policy=self.policy)
        before = started.snapshot
        kwargs = {}
        if row["expected_action"] in {"replace_source", "compare_verified_count"}:
            city = row["turn"].split()[-1]
            new_request, new_bindings = self.city_inputs(city)
            kwargs = {"new_request": new_request, "new_bindings": new_bindings}
        changed = self.manager.apply(before.session_id, before.version, row["turn"], **kwargs)
        self.assertTrue(changed.accepted)
        self.assertEqual(changed.action, {
            "recompute_dependents": "change_distance", "append_filter": "add_exclusion",
            "replace_source": "switch_city", "compare_verified_count": "compare",
        }[row["expected_action"]])
        self.assertEqual(len(changed.reused_steps), row["expected_reuse"])
        self.assertEqual(changed.snapshot.version, 2)
        if row["expected_action"] == "append_filter":
            self.assertEqual(changed.reused_steps, ["step_1", "step_2"])
            self.assertEqual(changed.executed_steps, ["step_3"])
        if row["expected_action"] == "recompute_dependents":
            self.assertEqual(changed.executed_steps, ["step_1", "step_2"])
            self.assertEqual(changed.snapshot.bindings, before.bindings)
        if row["expected_action"] == "replace_source":
            self.assertNotEqual(changed.snapshot.bindings["sites"].id, before.bindings["sites"].id)
            self.assertEqual(changed.snapshot.plan.intent.place.name, row["turn"].split()[-1])
        if row["expected_action"] == "compare_verified_count":
            self.assertEqual(changed.snapshot.request, before.request)
            self.assertEqual(changed.snapshot.outcome, before.outcome)
            self.assertEqual(changed.snapshot.comparison.delta_count, -2)
        return changed

    def test_output_format_reuses_result(self):
        request, bindings = self.base("search")
        started = self.manager.start(request, bindings, policy=self.policy)
        changed = self.manager.apply(started.snapshot.session_id, 1, "change output format to geojson")
        self.assertEqual(changed.reused_steps, [])
        self.assertEqual(changed.executed_steps, [])
        self.assertEqual(changed.snapshot.outcome.final_dataset, started.snapshot.outcome.final_dataset)
        self.assertEqual(changed.snapshot.output_format, "geojson")

    def test_session_isolation_and_version_conflict(self):
        request, bindings = self.base("search")
        left = self.manager.start(request, bindings, policy=self.policy).snapshot
        right = self.manager.start(request, bindings, policy=self.policy).snapshot
        self.assertNotEqual(left.session_id, right.session_id)
        self.manager.apply(left.session_id, 1, "change output format to geojson")
        self.assertEqual(self.manager.get(right.session_id).version, 1)
        self.assertEqual(self.manager.get(right.session_id).output_format, "summary")
        with self.assertRaises(SessionError) as context:
            self.manager.apply(left.session_id, 1, "change output format to summary")
        self.assertEqual(context.exception.code, "M09_VERSION_CONFLICT")

    def test_released_cached_result_is_recomputed(self):
        request, bindings = self.base("within_distance")
        started = self.manager.start(request, bindings, policy=self.policy).snapshot
        self.datasets.release(started.outcome.step_records[0].output_id)
        changed = self.manager.apply(started.session_id, 1, "exclude name = Beta")
        self.assertTrue(changed.accepted)
        self.assertEqual(changed.reused_steps, [])
        self.assertEqual(changed.executed_steps, ["step_1", "step_2", "step_3"])

    def test_partial_city_result_does_not_replace_confirmed_state(self):
        request, bindings = self.base("search")
        started = self.manager.start(request, bindings, policy=self.policy).snapshot
        new_request, new_bindings = self.city_inputs("Malmo")
        ref = new_bindings["sites"]
        new_bindings["sites"] = ref.model_copy(update={
            "id": "ds_" + "0" * 32,
            "storage_uri": "registry://datasets/ds_" + "0" * 32 + "/data.geojson",
        })
        changed = self.manager.apply(started.session_id, 1, "switch city to Malmo", new_request=new_request, new_bindings=new_bindings)
        self.assertFalse(changed.accepted)
        self.assertEqual(changed.attempt_error_code, "M08_DATASET_UNAVAILABLE")
        self.assertEqual(changed.attempt_outcome.status, "partial")
        self.assertTrue(changed.attempt_outcome.limitations)
        self.assertEqual(self.manager.get(started.session_id).version, 1)
        self.assertEqual(self.manager.get(started.session_id).outcome, started.outcome)

    def test_city_requires_explicit_new_source(self):
        request, bindings = self.base("search")
        started = self.manager.start(request, bindings, policy=self.policy).snapshot
        with self.assertRaises(SessionError) as context:
            self.manager.apply(started.session_id, 1, "switch city to Malmo")
        self.assertEqual(context.exception.code, "M09_SOURCE_REQUIRED")

    def test_context_is_bounded_and_close(self):
        request, bindings = self.base("search")
        started = self.manager.start(request, bindings, policy=self.policy).snapshot
        context = self.manager.context_summary(started.session_id)
        self.assertLess(len(json.dumps(context)), 1024)
        self.assertNotIn("storage_uri", json.dumps(context))
        self.assertNotIn("features", json.dumps(context))
        self.manager.close(started.session_id)
        with self.assertRaises(SessionError):
            self.manager.get(started.session_id)

    def test_session_limit_and_no_leaked_mutation(self):
        request, bindings = self.base("search")
        limited = SessionManager(self.datasets, max_sessions=1)
        first = limited.start(request, bindings, policy=self.policy).snapshot
        copy = limited.get(first.session_id)
        copy.bindings.clear()
        self.assertEqual(set(limited.get(first.session_id).bindings), {"sites"})
        with self.assertRaises(SessionError) as context:
            limited.start(request, bindings, policy=self.policy)
        self.assertEqual(context.exception.code, "M09_SESSION_LIMIT")
        limited.close(first.session_id)
        self.assertTrue(limited.start(request, bindings, policy=self.policy).accepted)

    def test_stale_final_dataset_rejects_format_reuse(self):
        request, bindings = self.base("search")
        started = self.manager.start(request, bindings, policy=self.policy).snapshot
        self.datasets.release(started.outcome.final_dataset.id)
        with self.assertRaises(SessionError) as context:
            self.manager.apply(started.session_id, 1, "change output format to geojson")
        self.assertEqual(context.exception.code, "M09_DATASET_STALE")
        self.assertEqual(self.manager.get(started.session_id).version, 1)

    def test_unknown_exclusion_field_rejected(self):
        request, bindings = self.base("within_distance")
        started = self.manager.start(request, bindings, policy=self.policy).snapshot
        with self.assertRaises(SessionError) as context:
            self.manager.apply(started.session_id, 1, "exclude missing = Beta")
        self.assertEqual(context.exception.code, "M09_FIELD_UNKNOWN")
        self.assertEqual(self.manager.get(started.session_id).version, 1)
    def test_sequential_edits_recompute_only_changed_dependencies(self):
        request, bindings = self.base("within_distance")
        started = self.manager.start(request, bindings, policy=self.policy).snapshot
        excluded = self.manager.apply(started.session_id, 1, "exclude name = Beta")
        self.assertEqual(excluded.reused_steps, ["step_1", "step_2"])
        moved = self.manager.apply(started.session_id, 2, "change distance to 100 m")
        self.assertTrue(moved.accepted)
        self.assertEqual(moved.reused_steps, [])
        self.assertEqual(moved.executed_steps, ["step_1", "step_2", "step_3"])
        formatted = self.manager.apply(started.session_id, 3, "change output format to geojson")
        self.assertEqual(formatted.snapshot.version, 4)
        self.assertEqual(formatted.executed_steps, [])
        self.assertEqual(formatted.snapshot.plan.business_parameters["distance_m"], 100)
        self.assertEqual(formatted.snapshot.distance_unit, "m")
        self.assertEqual(formatted.snapshot.plan.business_parameters["exclude_value"], "Beta")
    def test_frozen_matrix_shape(self):
        self.assertEqual(len(CASES), 16)
        self.assertEqual({row["expected_action"] for row in CASES}, {
            "recompute_dependents", "append_filter", "replace_source", "compare_verified_count",
        })


def make_case(row):
    def test(self):
        self.run_case(row)
    return test


for row in CASES:
    setattr(M09SessionTests, "test_" + row["case_id"], make_case(row))


if __name__ == "__main__":
    unittest.main()

