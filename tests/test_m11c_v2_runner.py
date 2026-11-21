"""M11c v2 harness: shared front-end round, call accounting, gate, freeze, replay, private outputs.

A fake Ollama transport answers with frames derived from the v1 rule front-end's own
structured decision (never from reference answers); no real model is called.
"""
import json
from pathlib import Path
import shutil
import tempfile
import unittest

from benchmarks.run_m11c_v2 import (
    STAGE2_NAME, freeze_stage2, replay_receipt, run_dev, run_private,
)
from scripts.build_m11c_private_v2 import build_package, seal
from src.agent.baselines import register_catalogue
from src.agent.benchmark_frontend import interpret
from src.agent.model_frontend import MODEL_DIGEST, MODEL_ID, ModelFrontend, PromptTemplate
from src.datasets import LocalDatasetRegistry
from src.evaluation.m11b import load_m11b_tasks
from src.evaluation.m11c import dev_catalogue
from src.evaluation.m11c_v2 import (
    GATE, MODEL_FREE_IDS, PROTOCOL_VERSION, evaluate_split, frontend_round, gate_check, round_stats,
)

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "data/fixtures/tools/m04b/v1"
TASKS = load_m11b_tasks(ROOT / "benchmarks/data/dev/m11b_tasks_v1.jsonl")
SUBSET = {"m11b_dev_001", "m11b_dev_018", "m11b_dev_025", "m11b_dev_059", "m11b_dev_080",
          "m11b_dev_100", "m11b_dev_130", "m11b_dev_145", "m11b_dev_153"}
SUB_TASKS = [t for t in TASKS if t.task_id in SUBSET]
SCOPE = "Synthetic dev catalogue"
PROMPT = PromptTemplate(text="Datasets:\n{catalogue}\nRequest: {query}\nJSON: ", label="test-prompt")
SYMBOL = {"eq": "=", "ne": "!=", "gt": ">", "gte": ">=", "lt": "<", "lte": "<="}


def oracle_frame(decision):
    if decision.reason_code == "unsupported_rank":
        return {"op": "rank", "data": "regions"}
    if decision.reason_code == "unsupported_buffer":
        return {"op": "buffer", "data": "sources"}
    if decision.status != "request":
        return {"op": "search", "data": None}
    where = [{"field": c["field"], "cmp": SYMBOL[c["operator"]], "value": c["value"]} for c in decision.conditions]
    r, p = decision.roles, decision.parameters
    return {
        "search": lambda: {"op": "search", "data": r["role"], "where": where},
        "nearest": lambda: {"op": "nearest", "data": r["source"], "other": r["target"], "k": p["k"], "where": where},
        "within": lambda: {"op": "within", "data": r["source"], "other": r["container"], "where": where},
        "spatial_join": lambda: {"op": "join", "data": r["right"], "other": r["left"], "relation": p["predicate"],
                                 "subject": r["left"], "where": where},
        "aggregate_by_region": lambda: {"op": "aggregate", "data": r["values"], "other": r["regions"],
                                        "metric": p["metric"], "metric_field": p["metric_field"], "where": where},
    }[decision.operation]()


def body(response):
    return 200, json.dumps({"response": response, "done": True, "prompt_eval_count": 50,
                            "eval_count": 10, "load_duration": 0, "total_duration": 100}).encode("utf-8")


class OracleTransport:
    def __init__(self, answers=None, fail=()):
        with tempfile.TemporaryDirectory(prefix="m11c_v2_oracle_") as temp:  # refs carry metadata only
            self.refs = register_catalogue(dev_catalogue(), LocalDatasetRegistry(Path(temp) / "d"))
        self.answers = answers or {}
        self.fail = set(fail)
        self.generate_calls = 0

    def __call__(self, method, url, payload, timeout):
        if url.endswith("/api/version"):
            return 200, b'{"version":"0.13.5"}'
        if url.endswith("/api/tags"):
            return 200, json.dumps({"models": [{"name": MODEL_ID, "digest": MODEL_DIGEST}]}).encode("utf-8")
        self.generate_calls += 1
        query = payload["prompt"].rsplit("Request: ", 1)[1].rsplit("\nJSON: ", 1)[0]
        if query in self.fail:
            return body("not json")
        if query in self.answers:
            return body(json.dumps(self.answers[query]))
        return body(json.dumps(oracle_frame(interpret(query, self.refs, scope=SCOPE))))


def frontend(**kwargs):
    return ModelFrontend(PROMPT, transport=OracleTransport(**kwargs))


class SharedRound(unittest.TestCase):
    def test_one_call_per_task_shared_by_every_model_configuration(self):
        fe = frontend()
        round_ = frontend_round(fe, SUB_TASKS, dev_catalogue(), seed=11071)
        self.assertEqual(fe.transport.generate_calls, len(SUB_TASKS))
        stats = round_stats(round_)
        self.assertEqual((stats["logical_calls"], stats["attempts"], stats["failures"], stats["retries"]),
                         (len(SUB_TASKS), len(SUB_TASKS), 0, 0))
        result = evaluate_split(SUB_TASKS, dev_catalogue(), round_, fe.identity(), seed=11071, include_ablations=True)
        self.assertEqual(fe.transport.generate_calls, len(SUB_TASKS))  # systems reuse the round's decisions
        self.assertEqual(set(result), {"fixed_workflow", "dynamic_agent", "validator_replan", "template_workflow",
                                       "validator_no_frontend", "validator_strict_empty", "validator_no_replan"})
        for config, records in result.items():
            for record in records:
                call = round_.calls[record.task.task_id]
                if config in MODEL_FREE_IDS:
                    self.assertEqual(record.prediction.llm_calls, 0)
                else:
                    self.assertEqual(record.prediction.llm_calls, len(call.attempts))
                    self.assertGreaterEqual(record.prediction.latency_ms, call.wall_ms)

    def test_oracle_frames_reproduce_v1_validator_outcomes(self):
        from src.agent.baselines import ValidatorReplanSystem
        from src.evaluation.m11c import run_system
        fe = frontend()
        round_ = frontend_round(fe, SUB_TASKS, dev_catalogue(), seed=11072)
        v2 = evaluate_split(SUB_TASKS, dev_catalogue(), round_, fe.identity(), seed=11072,
                            include_ablations=False)["validator_replan"]
        v1 = run_system(ValidatorReplanSystem(), SUB_TASKS, dev_catalogue(), seed=11072,
                        executable={t.task_id: True for t in SUB_TASKS})
        self.assertEqual([r.score.passed for r in v2], [r.score.passed for r in v1])
        self.assertEqual([r.prediction.reason_code for r in v2], [r.prediction.reason_code for r in v1])

    def test_failed_logical_call_is_counted_and_declined(self):
        failing = next(t for t in SUB_TASKS if t.task_id == "m11b_dev_001")
        fe = frontend(fail={failing.query})
        round_ = frontend_round(fe, SUB_TASKS, dev_catalogue(), seed=11071)
        stats = round_stats(round_)
        self.assertEqual((stats["failures"], stats["retries"], stats["attempts"]),
                         (1, 1, len(SUB_TASKS) + 1))
        records = evaluate_split(SUB_TASKS, dev_catalogue(), round_, fe.identity(), seed=11071,
                                 include_ablations=False)["validator_replan"]
        failed = next(r for r in records if r.task.task_id == "m11b_dev_001")
        self.assertEqual((failed.prediction.status, failed.prediction.reason_code), ("error", "model_frontend_failed"))
        self.assertEqual(failed.prediction.llm_calls, 2)

    def test_gate_counts_follow_the_frozen_thresholds(self):
        self.assertEqual(GATE, {"validator_strict_min": 120, "rank_unsupported_required": 8,
                                "missing_place_min": 7, "model_failures_max": 3})
        fe = frontend()
        round_ = frontend_round(fe, SUB_TASKS, dev_catalogue(), seed=11071)
        records = evaluate_split(SUB_TASKS, dev_catalogue(), round_, fe.identity(), seed=11071,
                                 include_ablations=False)["validator_replan"]
        gate = gate_check(records, round_)
        self.assertEqual(gate["counts"]["rank_unsupported"], 1)
        self.assertEqual(gate["counts"]["missing_place"], 1)
        self.assertEqual(gate["counts"]["model_failures"], 0)
        self.assertFalse(gate["passed"])  # a 9-task subset cannot reach 120 strict passes


class FreezeRunReplay(unittest.TestCase):
    def test_freeze_run_dev_replay_and_private_refusal(self):
        with tempfile.TemporaryDirectory(prefix="m11c_v2_runner_") as temp:
            root = Path(temp)
            manifest = freeze_stage2(frontend(), root / "freeze", task_ids=SUBSET)
            self.assertEqual(manifest.name, STAGE2_NAME)
            frozen = json.loads(manifest.read_text("utf-8"))
            self.assertEqual(frozen["protocol_version"], PROTOCOL_VERSION)
            self.assertEqual(frozen["prompt"]["sha256"], PROMPT.sha256)
            self.assertEqual(frozen["model"]["digest"], MODEL_DIGEST)
            with self.assertRaises(FileExistsError):
                freeze_stage2(frontend(), root / "freeze", task_ids=SUBSET)
            drifted = ModelFrontend(PromptTemplate(text=PROMPT.text + " ", label="test-prompt"),
                                    transport=OracleTransport())
            with self.assertRaises(ValueError):
                run_dev(drifted, manifest, root / "runs", "drift", task_ids=SUBSET, seeds=(11071,))
            run_dir = run_dev(frontend(), manifest, root / "runs", "dev", task_ids=SUBSET, seeds=(11071, 11072))
            for name in ("report.json", "report.md", "deterministic_receipt.json", "frontend_calls.jsonl",
                         "dev_task_results.jsonl", "stage3_dev_gate.json"):
                self.assertTrue((run_dir / name).is_file(), name)
            gate = json.loads((run_dir / "stage3_dev_gate.json").read_text("utf-8"))
            self.assertFalse(gate["gate_passed"])
            self.assertEqual(len(gate["runs"]), 2)
            report = json.loads((run_dir / "report.json").read_text("utf-8"))
            self.assertEqual(report["metadata"]["model"]["model_id"], MODEL_ID)
            self.assertEqual(report["splits"]["dev"]["frontend"]["per_run"][0]["logical_calls"], len(SUBSET))
            published = (run_dir / "deterministic_receipt.json").read_bytes()
            self.assertEqual(replay_receipt(run_dir, manifest, task_ids=SUBSET), published)
            with self.assertRaises(FileExistsError):
                run_dev(frontend(), manifest, root / "runs", "dev", task_ids=SUBSET, seeds=(11071,))
            with self.assertRaises(ValueError):  # gate not passed: private run refused
                run_private(frontend(), manifest, run_dir, root / "ws", root / "private_runs", "p",
                            receipt_path=root / "receipt.json")


class PrivateOutputs(unittest.TestCase):
    def test_private_split_publishes_aggregates_and_keeps_details_in_workspace(self):
        copies = {"projected_sources.geojson": "tool_sites.geojson", "projected_targets.geojson": "tool_depots.geojson",
                  "projected_regions.geojson": "tool_zones.geojson", "aggregate_values.geojson": "tool_readings.geojson"}
        cond = [{"field": "score", "operator": "gte", "value": 5}]
        specs = {"schema_version": "1.0", "tasks": [
            {"task_id": "m11b_test_201", "family": "search", "query": "tool check one: clinic sites",
             "params": {"dataset": "tool_sites.geojson", "conditions": [{"field": "category", "operator": "eq",
                                                                         "value": "clinic"}]}},
            {"task_id": "m11b_test_202", "family": "buffer", "query": "tool check two: buffer sites",
             "params": {"dataset": "tool_sites.geojson", "distance_m": 3}},
            {"task_id": "m11b_test_203", "family": "nearest", "query": "tool check three: sites to depots",
             "params": {"source": "tool_sites.geojson", "target": "tool_depots.geojson", "k": 1, "conditions": cond}},
            {"task_id": "m11b_test_204", "family": "within", "query": "tool check four: sites in zones",
             "params": {"source": "tool_sites.geojson", "container": "tool_zones.geojson", "conditions": cond}},
            {"task_id": "m11b_test_205", "family": "spatial_join", "query": "tool check five: zones with sites",
             "params": {"left": "tool_zones.geojson", "right": "tool_sites.geojson", "predicate": "intersects",
                        "conditions": cond}},
            {"task_id": "m11b_test_206", "family": "aggregate_by_region", "query": "tool check six: readings per zone",
             "params": {"regions": "tool_zones.geojson", "values": "tool_readings.geojson",
                        "region_id_field": "region_id", "values_region_id_field": "joined_region_id",
                        "operation": "count", "conditions": [{"field": "value", "operator": "gte", "value": 0}]}},
            {"task_id": "m11b_test_207", "family": "rank", "query": "tool check seven: rank zones", "params": {}},
            {"task_id": "m11b_test_208", "family": "missing_place", "query": "tool check eight", "params": {}},
        ]}
        answers = {
            "tool check one: clinic sites": {"op": "search", "data": "sites", "where": [{"field": "category", "cmp": "=", "value": "clinic"}]},
            "tool check two: buffer sites": {"op": "buffer", "data": "sites"},
            "tool check three: sites to depots": {"op": "nearest", "data": "sites", "other": "depots", "k": 1,
                                 "where": [{"field": "score", "cmp": ">=", "value": 5}]},
            "tool check four: sites in zones": {"op": "within", "data": "sites", "other": "zones",
                                "where": [{"field": "score", "cmp": ">=", "value": 5}]},
            "tool check five: zones with sites": {"op": "join", "data": "sites", "other": "zones", "relation": "intersects",
                                "where": [{"field": "score", "cmp": ">=", "value": 5}]},
            "tool check six: readings per zone": {"op": "aggregate", "data": "readings", "other": "zones", "metric": "count",
                               "where": [{"field": "value", "cmp": ">=", "value": 0}]},
            "tool check seven: rank zones": {"op": "rank", "data": "zones"},
            "tool check eight": {"op": "search", "data": None},
        }
        with tempfile.TemporaryDirectory(prefix="m11c_v2_private_") as temp:
            ws = Path(temp) / "workspace"
            ws.mkdir()
            manifest = json.loads((FIXTURES / "manifest.json").read_text("utf-8"))
            files = {}
            for source, target in copies.items():
                shutil.copyfile(FIXTURES / source, ws / target)
                files[target] = {"crs": manifest["fixtures"][source]["crs"]}
            (ws / "catalogue.json").write_text(json.dumps({"schema_version": "1.0", "license": "CC0-1.0",
                                                           "author": "test", "files": files}), "utf-8")
            (ws / "task_specs.json").write_text(json.dumps(specs), "utf-8")
            build_package(ws, per_family=1)
            receipt = Path(temp) / "receipt.json"
            seal(ws, receipt_path=receipt, per_family=1)
            from benchmarks.run_m11c_v2 import (
                PRIVATE_RUN_DIR, private_evaluation, replay_private, write_private_outputs,
            )
            public, private = private_evaluation(frontend(answers=answers), ws, seeds=(11071, 11072),
                                                 receipt_path=receipt)
            text = json.dumps(public)
            for leaked in ("tool check", "m11b_test_2", "sites", "zones", "clinic", "depots", "readings"):
                self.assertNotIn(leaked, text)
            validator = public["configs"]["validator_replan"]["subsets"]["full"]["across_runs"]
            self.assertEqual(validator["strict_pass_per_run"], [7, 7])  # buffer stays in the denominator
            self.assertEqual(public["frontend"]["per_run"][0]["logical_calls"], 8)
            self.assertIn("frontend_calls", private)
            self.assertIn("tool check one", json.dumps(private))
            manifest_path = freeze_stage2(frontend(), Path(temp) / "freeze")  # full-dev manifest, as in formal use
            manifest = json.loads(manifest_path.read_text("utf-8"))
            public_dir = write_private_outputs(Path(temp) / "public" / "run", ws / PRIVATE_RUN_DIR / "run", manifest,
                                               "sha256:" + "0" * 64, "sha256:" + "1" * 64, (11071, 11072),
                                               public, private)
            published = "".join(p.read_text("utf-8") for p in sorted(public_dir.iterdir()))
            for leaked in ("tool check", "m11b_test_2", "sites", "zones", "clinic", "depots", "readings"):
                self.assertNotIn(leaked, published)
            self.assertTrue(replay_private(public_dir, manifest_path, ws, receipt_path=receipt)["verified"])
            from benchmarks.run_m11c_v2 import scan_leaks
            from scripts.locate_m11c_v2_leak_hits import locate
            located = locate(ws, public_dir=public_dir, dev_dir=Path(temp) / "no_dev", receipt_path=receipt)
            self.assertEqual(located["summary"]["total_hits"],
                             sum(scan_leaks([public_dir], ws, receipt_path=receipt).values()))
            text = json.dumps(located)
            for leaked in ("tool check", "m11b_test_2", "sites", "zones", "clinic", "depots", "readings"):
                self.assertNotIn(leaked, text)


if __name__ == "__main__":
    unittest.main()
