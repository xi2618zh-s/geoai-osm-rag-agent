"""M11c private v2 contract and custodian builder (synthetic test workspace only).

The workspace here is built from public M04b fixtures under new names; it is a
tooling test, not the sealed private v2 package, which this test never reads.
"""
import json
from pathlib import Path
import shutil
import tempfile
import unittest

from scripts.build_m11c_private_v2 import build_package, seal, verify_sealed
from src.evaluation.m11b import score_m11b
from src.evaluation.m11c import M11cPrivateTask, load_m11c_private_v2_tasks

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "data/fixtures/tools/m04b/v1"
COPIES = {"projected_sources.geojson": "tool_sites.geojson",
          "projected_targets.geojson": "tool_depots.geojson",
          "projected_regions.geojson": "tool_zones.geojson",
          "aggregate_values.geojson": "tool_readings.geojson"}


def _specs():
    cond = [{"field": "score", "operator": "gte", "value": 5}]
    return {"schema_version": "1.0", "tasks": [
        {"task_id": "m11b_test_201", "family": "search", "query": "tool check one",
         "params": {"dataset": "tool_sites.geojson", "conditions": [
             {"field": "category", "operator": "eq", "value": "clinic"}]}},
        {"task_id": "m11b_test_202", "family": "buffer", "query": "tool check two",
         "params": {"dataset": "tool_sites.geojson", "distance_m": 3}},
        {"task_id": "m11b_test_203", "family": "nearest", "query": "tool check three",
         "params": {"source": "tool_sites.geojson", "target": "tool_depots.geojson", "k": 1, "conditions": cond}},
        {"task_id": "m11b_test_204", "family": "within", "query": "tool check four",
         "params": {"source": "tool_sites.geojson", "container": "tool_zones.geojson", "conditions": cond}},
        {"task_id": "m11b_test_205", "family": "spatial_join", "query": "tool check five",
         "params": {"left": "tool_zones.geojson", "right": "tool_sites.geojson", "predicate": "intersects",
                    "conditions": cond}},
        {"task_id": "m11b_test_206", "family": "aggregate_by_region", "query": "tool check six",
         "params": {"regions": "tool_zones.geojson", "values": "tool_readings.geojson",
                    "region_id_field": "region_id", "values_region_id_field": "joined_region_id",
                    "operation": "sum", "field": "value",
                    "conditions": [{"field": "value", "operator": "gte", "value": 0}]}},
        {"task_id": "m11b_test_207", "family": "rank", "query": "tool check seven", "params": {}},
        {"task_id": "m11b_test_208", "family": "missing_place", "query": "tool check eight", "params": {}},
    ]}


class PrivateV2Builder(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="m11c_custody_test_")
        self.ws = Path(self.temp.name) / "workspace"
        self.ws.mkdir()
        manifest = json.loads((FIXTURES / "manifest.json").read_text("utf-8"))
        files = {}
        for source, target in COPIES.items():
            shutil.copyfile(FIXTURES / source, self.ws / target)
            files[target] = {"crs": manifest["fixtures"][source]["crs"]}
        (self.ws / "catalogue.json").write_text(json.dumps(
            {"schema_version": "1.0", "license": "CC0-1.0", "author": "test", "files": files}), "utf-8")
        self.write_specs(_specs())
        self.receipt = Path(self.temp.name) / "receipt.json"

    def tearDown(self):
        self.temp.cleanup()

    def write_specs(self, specs):
        (self.ws / "task_specs.json").write_text(json.dumps(specs), "utf-8")

    def test_contract_accepts_only_v2_identity(self):
        task = next(iter(load_m11c_private_v2_tasks_built(self)))
        self.assertEqual((task.split, task.data_version), ("test", "m11c-private-test-v2"))
        with self.assertRaises(ValueError):
            M11cPrivateTask.model_validate({**task.model_dump(mode="json"), "task_id": "m11b_test_001"})
        with self.assertRaises(ValueError):
            M11cPrivateTask.model_validate({**task.model_dump(mode="json"), "data_version": "m11b-private-test-v1"})

    def test_build_checks_and_reference_self_check(self):
        summary = build_package(self.ws, per_family=1)
        self.assertEqual(summary["task_count"], 8)
        self.assertEqual(set(summary["family_counts"].values()), {1})
        self.assertEqual(summary["checks"]["reference_self_check"], "8/8")
        self.assertEqual(summary["checks"]["public_dev_query_overlap"], 0)
        text = json.dumps(summary)
        for leaked in ("tool check", "tool_sites", "clinic"):
            self.assertNotIn(leaked, text)
        tasks = load_m11c_private_v2_tasks(self.ws / "m11c_private_tasks_v2.jsonl")
        by_family = {t.category: t for t in tasks}
        self.assertEqual(by_family["private_v2_rank"].expectation.reason_code, "unsupported_rank")
        self.assertEqual(by_family["private_v2_buffer"].task_type, "within_distance")
        self.assertEqual(by_family["private_v2_nearest"].expectation.tool_sequence,
                         ["filter_attributes", "nearest_features"])
        again = build_package(self.ws, per_family=1)
        self.assertEqual(summary["tasks_sha256"], again["tasks_sha256"])

    def test_builder_rejects_invalid_specs(self):
        bad_cases = []
        specs = _specs(); specs["tasks"][1]["query"] = specs["tasks"][0]["query"]; bad_cases.append(specs)
        specs = _specs(); specs["tasks"][2]["params"]["conditions"] = []; bad_cases.append(specs)
        specs = _specs(); specs["tasks"][0]["query"] = "Search synthetic sources: category clinic, score gte 0"; bad_cases.append(specs)
        specs = _specs(); specs["tasks"][7]["task_id"] = "m11b_test_299"; bad_cases.append(specs)
        specs = _specs(); specs["tasks"][2]["params"]["source"] = "missing.geojson"; bad_cases.append(specs)
        specs = _specs(); specs["tasks"].pop(); bad_cases.append(specs)
        for specs in bad_cases:
            self.write_specs(specs)
            with self.assertRaises(ValueError):
                build_package(self.ws, per_family=1)

    def test_builder_refuses_workspace_inside_repository(self):
        with self.assertRaises(ValueError):
            build_package(ROOT / "benchmarks", per_family=1)

    def test_seal_is_exclusive_payload_free_and_verified(self):
        build_package(self.ws, per_family=1)
        receipt = seal(self.ws, receipt_path=self.receipt, per_family=1)
        text = self.receipt.read_text("utf-8")
        for leaked in ("tool check", "tool_sites", "tool_zones", "clinic", "m11b_test_201"):
            self.assertNotIn(leaked, text)
        self.assertEqual(receipt["task_count"], 8)
        with self.assertRaises(FileExistsError):
            seal(self.ws, receipt_path=self.receipt, per_family=1)
        with self.assertRaises(FileExistsError):
            build_package(self.ws, per_family=1)
        tasks, catalogue = verify_sealed(self.ws, self.receipt)
        self.assertEqual(len(tasks), 8)
        self.assertEqual({item.role for item in catalogue.files}, {"sites", "depots", "zones", "readings"})
        (self.ws / "tool_sites.geojson").write_bytes(b"{}")
        with self.assertRaises(ValueError):
            verify_sealed(self.ws, self.receipt)

    def test_seal_requires_current_build(self):
        build_package(self.ws, per_family=1)
        specs = _specs(); specs["tasks"][0]["query"] = "tool check changed"; self.write_specs(specs)
        with self.assertRaises(ValueError):
            seal(self.ws, receipt_path=self.receipt, per_family=1)


def load_m11c_private_v2_tasks_built(case):
    build_package(case.ws, per_family=1)
    return load_m11c_private_v2_tasks(case.ws / "m11c_private_tasks_v2.jsonl")


if __name__ == "__main__":
    unittest.main()
