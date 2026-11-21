"""M11c v2 model front-end: frame schema, prompt, Ollama client accounting and grounding.

No real model is called: every HTTP exchange goes through an injected fake transport.
"""
import hashlib
import inspect
import json
from pathlib import Path
import tempfile
import unittest

from src.agent import model_frontend
from src.agent.baselines import (
    CatalogueFile, DatasetCatalogue, DynamicAgentSystem, FixedWorkflowSystem, ValidatorReplanSystem,
    register_catalogue,
)
from src.agent.benchmark_frontend import interpret
from src.agent.dynamic_planner import plan_request
from src.agent.model_frontend import (
    DECODING_OPTIONS, MAX_ATTEMPTS, MODEL_DIGEST, MODEL_FRONTEND_VERSION, MODEL_ID, ModelFrontend,
    ModelUnavailable, PromptTemplate, SemanticFrame, frame_schema, ground_frame, load_prompt,
    mention_positions, parse_frame, profile_catalogue, render_prompt,
)
from src.datasets import LocalDatasetRegistry
from src.evaluation.m11c import ablation_systems, dev_catalogue, formal_systems

ROOT = Path(__file__).resolve().parents[1]
SCOPE = "Synthetic dev catalogue"
V1_MANIFEST = json.loads((ROOT / "benchmarks/results/m11c/freeze/m11c_freeze_manifest_v1.json").read_text("utf-8"))


def frame(**values):
    return SemanticFrame.model_validate(values)


class FakeTransport:
    """Scripted HTTP exchanges: each item is (status, body) or an exception to raise."""

    def __init__(self, script):
        self.script = list(script)
        self.requests = []

    def __call__(self, method, url, payload, timeout):
        self.requests.append({"method": method, "url": url, "payload": payload, "timeout": timeout})
        item = self.script.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item


def ok(response, prompt_tokens=100, eval_tokens=20):
    body = {"response": response, "done": True, "prompt_eval_count": prompt_tokens,
            "eval_count": eval_tokens, "load_duration": 1000, "total_duration": 5000}
    return 200, json.dumps(body).encode("utf-8")


class Profiles(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.profile = profile_catalogue(dev_catalogue())

    def test_profile_lists_roles_fields_and_small_value_domains(self):
        self.assertEqual(self.profile.roles(), ["empty", "points", "regions", "sources", "targets", "tie", "values"])
        sources = self.profile.dataset("sources")
        self.assertEqual(sources.geometry, "points")
        self.assertEqual({f.name: f.type for f in sources.fields},
                         {"category": "string", "name": "string", "score": "integer"})
        self.assertEqual(sources.field("category").listed, ("clinic", "school"))
        self.assertIsNone(sources.field("score").listed)
        self.assertEqual(self.profile.dataset("regions").geometry, "polygons")
        self.assertIn("- sources: points, 4 rows; fields: category (text: clinic, school)", self.profile.text())

    def test_profile_and_schema_are_deterministic(self):
        again = profile_catalogue(dev_catalogue())
        self.assertEqual(self.profile.sha256(), again.sha256())
        self.assertEqual(frame_schema(self.profile), frame_schema(again))

    def test_schema_constrains_roles_fields_and_operations(self):
        schema = frame_schema(self.profile)
        props = schema["properties"]
        self.assertEqual(props["op"]["enum"], ["search", "buffer", "nearest", "within", "join",
                                               "aggregate", "rank", "other"])
        self.assertEqual(props["data"]["anyOf"][0]["enum"], self.profile.roles())
        self.assertEqual(props["data"]["anyOf"][1], {"type": "null"})
        field_enum = props["where"]["items"]["properties"]["field"]["enum"]
        self.assertEqual(field_enum, sorted({f.name for d in self.profile.datasets for f in d.fields}))
        self.assertEqual(schema["required"], ["op", "data"])

    def test_prompt_shares_a_static_prefix_and_ends_with_the_query(self):
        template = PromptTemplate(text="Intro\n{catalogue}\nRequest: {query}\nJSON: ", label="t")
        first = render_prompt(template, self.profile, "Search sources: score gte 1")
        second = render_prompt(template, self.profile, "Find 1 nearest targets for sources")
        prefix = first[:first.index("Request: ")]
        self.assertTrue(second.startswith(prefix))
        self.assertTrue(first.endswith("Request: Search sources: score gte 1\nJSON: "))
        self.assertIn(self.profile.text(), first)

    def test_versioned_prompt_file_has_both_placeholders(self):
        prompt = load_prompt()
        self.assertIn("{catalogue}", prompt.text)
        self.assertIn("{query}", prompt.text)
        self.assertEqual(len(prompt.sha256), 64)


class FrameParsing(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.profile = profile_catalogue(dev_catalogue())

    def test_valid_invalid_and_out_of_catalogue_frames(self):
        outcome, parsed = parse_frame('{"op":"search","data":"sources","where":[]}', self.profile)
        self.assertEqual((outcome, parsed.op, parsed.data), ("ok", "search", "sources"))
        self.assertEqual(parse_frame("not json", self.profile), ("non_json", None))
        self.assertEqual(parse_frame('{"op":"search","data":"hospitals"}', self.profile)[0], "schema_invalid")
        self.assertEqual(parse_frame('{"op":"search","data":"sources","where":[{"field":"x","cmp":"=",'
                                     '"value":1}]}', self.profile)[0], "schema_invalid")
        self.assertEqual(parse_frame('{"op":"teleport","data":null}', self.profile)[0], "schema_invalid")
        self.assertEqual(parse_frame('[1,2]', self.profile)[0], "schema_invalid")


class Grounding(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix="m11c_v2_ground_")
        cls.refs = register_catalogue(dev_catalogue(), LocalDatasetRegistry(Path(cls.temp.name) / "d"))
        cls.profile = profile_catalogue(dev_catalogue())

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def ground(self, query, **values):
        return ground_frame(frame(**values), self.refs, self.profile, SCOPE, query)

    def test_frames_render_the_same_m07_requests_as_the_rule_frontend(self):
        cases = {
            "Search synthetic sources: category clinic, score gte 0":
                dict(op="search", data="sources", where=[{"field": "category", "cmp": "=", "value": "clinic"},
                                                         {"field": "score", "cmp": ">=", "value": 0}]),
            "Find 2 nearest synthetic targets for sources with score at least 3":
                dict(op="nearest", data="sources", other="targets", k=2,
                     where=[{"field": "score", "cmp": ">=", "value": 3}]),
            "Find synthetic sources with score at least 4 strictly within regions":
                dict(op="within", data="sources", other="regions", where=[{"field": "score", "cmp": ">=", "value": 4}]),
            "Join synthetic regions to sources with score at least 2 by contains":
                dict(op="join", data="sources", other="regions", relation="contains", subject="regions",
                     where=[{"field": "score", "cmp": ">=", "value": 2}]),
            "Aggregate synthetic region values by sum after value at least 5":
                dict(op="aggregate", data="values", other="regions", metric="sum", metric_field="value",
                     where=[{"field": "value", "cmp": ">=", "value": 5}]),
        }
        for query, values in cases.items():
            rule = interpret(query, self.refs, scope=SCOPE)
            model = self.ground(query, **values)
            self.assertEqual(model.frontend_version, MODEL_FRONTEND_VERSION)
            self.assertEqual((model.status, model.operation, model.command), (rule.status, rule.operation, rule.command))
            self.assertEqual(model.request, rule.request, query)
            self.assertEqual((model.roles, model.conditions, model.parameters),
                             (rule.roles, rule.conditions, rule.parameters), query)
            self.assertEqual(plan_request(model.request).status, "plan")

    def test_rank_buffer_and_missing_dataset_rules(self):
        rank = self.ground("Rank the regions by size", op="rank", data="regions")
        self.assertEqual((rank.status, rank.operation, rank.reason_code), ("unsupported", "rank", "unsupported_rank"))
        buffer = self.ground("Buffer the sources by 5 m", op="buffer", data="sources")
        self.assertEqual((buffer.status, buffer.reason_code), ("unsupported", "unsupported_buffer"))
        for op in ("search", "buffer", "nearest", "join"):
            missing = self.ground("Find all facilities", op=op, data=None, where=[{"field": "category", "cmp": "=", "value": "clinic"}])
            self.assertEqual((missing.status, missing.reason_code), ("clarification", "missing_place"), op)
        partial = self.ground("Nearest things to the sources", op="nearest", data="sources", other=None)
        self.assertEqual((partial.status, partial.reason_code), ("clarification", "unknown_dataset"))
        other = self.ground("Teleport the sources", op="other", data="sources")
        self.assertEqual((other.status, other.reason_code), ("unsupported", "unsupported_operation"))

    def test_failed_model_call_is_an_explicit_unsupported_decision(self):
        decision = ground_frame(None, self.refs, self.profile, SCOPE, "Search sources")
        self.assertEqual((decision.status, decision.reason_code), ("unsupported", "model_frontend_failed"))
        self.assertEqual(decision.frontend_version, MODEL_FRONTEND_VERSION)

    def test_condition_fields_choose_the_filtered_dataset(self):
        swapped = self.ground("Find sources with score above 3 within regions", op="within", data="regions", other="sources",
                              where=[{"field": "score", "cmp": ">", "value": 3}])
        self.assertEqual(swapped.roles, {"source": "sources", "container": "regions"})
        self.assertIn("filtered_dataset_from_condition_fields", swapped.evidence)

    def test_join_relation_is_read_from_its_subject(self):
        converse = self.ground("Which sources with score at least 2 lie within regions?", op="join", data="sources",
                               other="regions", relation="within", subject="regions",
                               where=[{"field": "score", "cmp": ">=", "value": 2}])
        self.assertEqual(converse.parameters, {"predicate": "contains"})
        self.assertEqual(converse.roles, {"left": "regions", "right": "sources"})
        default = self.ground("Join sources with score at least 2 and regions", op="join", data="sources", other="regions", where=[{"field": "score", "cmp": ">=", "value": 2}])
        self.assertEqual(default.parameters, {"predicate": "intersects"})

    def test_defaults_and_value_normalisation(self):
        nearest = self.ground("Nearest target to sources with score at least 1", op="nearest", data="sources", other="targets", where=[{"field": "score", "cmp": ">=", "value": 1}])
        self.assertEqual(nearest.parameters, {"k": 1})
        for spelled in ("Clinic", "clinics", "CLINIC"):
            search = self.ground(f"Find {spelled} sources", op="search", data="sources", where=[{"field": "category", "cmp": "=", "value": spelled}])
            self.assertEqual(search.conditions, [{"field": "category", "operator": "eq", "value": "clinic"}], spelled)
        numeric_text = self.ground("Sources with score above 5", op="search", data="sources", where=[{"field": "score", "cmp": ">", "value": "5"}])
        self.assertEqual(numeric_text.conditions, [{"field": "score", "operator": "gt", "value": 5}])
        count = self.ground("Count values per region", op="aggregate", data="values", other="regions", metric="count",
                            metric_field="value",
                            where=[{"field": "value", "cmp": ">=", "value": 1}])
        self.assertEqual(count.parameters["metric_field"], None)

    def test_unknown_or_inexpressible_conditions_are_declined(self):
        unknown = self.ground("Targets with score above 1", op="search", data="targets", where=[{"field": "score", "cmp": ">", "value": 1}])
        self.assertEqual((unknown.status, unknown.reason_code), ("unsupported", "unknown_field"))
        spaced = self.ground("Day care sources", op="search", data="sources", where=[{"field": "category", "cmp": "=", "value": "day care"}])
        self.assertEqual((spaced.status, spaced.reason_code), ("unsupported", "unexpressible_condition"))
        wrong_type = self.ground("Sources with a high score", op="search", data="sources", where=[{"field": "score", "cmp": ">", "value": "high"}])
        self.assertEqual(wrong_type.reason_code, "unexpressible_condition")

    def test_binding_needs_a_lexical_reference_to_a_bound_dataset(self):
        where = [{"field": "score", "cmp": ">=", "value": 3}]
        vetoed = self.ground("Find all facilities rated at least 3.", op="search", data="sources", where=where)
        self.assertEqual((vetoed.status, vetoed.reason_code), ("clarification", "missing_place"))
        by_value = self.ground("List the clinics whose score is at least 10.", op="search", data="sources",
                               where=[{"field": "score", "cmp": ">=", "value": 10}])
        self.assertEqual(by_value.status, "request")
        unique_field = self.ground("Show everything with a weight above 2", op="search", data="targets",
                                   where=[{"field": "weight", "cmp": ">", "value": 2}])
        self.assertEqual(unique_field.status, "request")
        shared_field = self.ground("Find everything that has a name", op="search", data="sources")
        self.assertEqual(shared_field.reason_code, "missing_place")
        one_side = self.ground("For each clinic find the nearest depot", op="nearest", data="sources", other="targets",
                               where=[{"field": "category", "cmp": "=", "value": "clinic"}])
        self.assertEqual(one_side.status, "request")

    def test_mention_positions_cover_roles_unique_fields_and_values(self):
        positions = mention_positions("Join regions to clinics by weight", self.profile)
        self.assertEqual(positions["regions"], 5)
        self.assertGreater(positions["sources"], positions["regions"])
        self.assertIsNotNone(positions["targets"])
        self.assertIsNone(positions["values"])

    def test_single_named_listed_value_becomes_a_condition(self):
        added = self.ground("Which clinic sources have a score of 0 or more?", op="search", data="sources",
                            where=[{"field": "score", "cmp": ">=", "value": 0}])
        self.assertEqual(added.conditions, [{"field": "score", "operator": "gte", "value": 0},
                                            {"field": "category", "operator": "eq", "value": "clinic"}])
        self.assertIn("condition_from_value_mention:category", added.evidence)
        both = self.ground("Compare clinic and school sources with score at least 1", op="search", data="sources",
                           where=[{"field": "score", "cmp": ">=", "value": 1}])
        self.assertEqual(len(both.conditions), 1)  # two named values: ambiguous, nothing added
        kept = self.ground("Sources that are not clinics", op="search", data="sources",
                           where=[{"field": "category", "cmp": "!=", "value": "clinic"}])
        self.assertEqual(kept.conditions, [{"field": "category", "operator": "ne", "value": "clinic"}])

    def test_pairing_verb_turns_a_within_frame_into_a_join(self):
        where = [{"field": "score", "cmp": ">=", "value": 0}]
        joined = self.ground("Match regions that are within sources having a score of at least 0.", op="within",
                             data="sources", other="regions", where=where)
        self.assertEqual((joined.operation, joined.parameters), ("spatial_join", {"predicate": "within"}))
        self.assertEqual(joined.roles, {"left": "regions", "right": "sources"})
        plain = self.ground("Find sources with score at least 0 inside regions", op="within", data="sources",
                            other="regions", where=where)
        self.assertEqual(plain.operation, "within")
        matching = self.ground("Find sources matching a score of at least 0 inside regions", op="within",
                               data="sources", other="regions", where=where)
        self.assertEqual(matching.operation, "within")

    def test_join_subject_is_the_first_mentioned_dataset(self):
        where = [{"field": "score", "cmp": ">=", "value": 2}]
        contains = self.ground("Join synthetic regions to sources with score at least 2 by contains", op="join",
                               data="sources", other="regions", relation="contains", subject="sources", where=where)
        self.assertEqual(contains.parameters, {"predicate": "contains"})
        self.assertIn("relation_subject:first_mention", contains.evidence)

    def test_the_datasets_own_name_is_not_a_value_condition(self):
        with tempfile.TemporaryDirectory(prefix="m11c_v2_role_value_") as temp:
            root = Path(temp)
            path = root / "demo_schools.geojson"
            path.write_text(json.dumps({"type": "FeatureCollection", "features": [
                {"type": "Feature", "id": f"s{i}", "properties": {"type": kind, "rating": i},
                 "geometry": {"type": "Point", "coordinates": [i, i]}}
                for i, kind in enumerate(("school", "college", "school"))]}), "utf-8")
            catalogue = DatasetCatalogue(name="demo", scope="Demo catalogue", files=(CatalogueFile(
                role="schools", path=path, crs="EPSG:3857", source="demo", source_version="1",
                sha256=hashlib.sha256(path.read_bytes()).hexdigest()),))
            refs = register_catalogue(catalogue, LocalDatasetRegistry(root / "d"))
            decision = ground_frame(frame(op="search", data="schools",
                                          where=[{"field": "rating", "cmp": ">=", "value": 1}]),
                                    refs, profile_catalogue(catalogue), "Demo catalogue",
                                    "Find schools with a rating of at least 1")
            self.assertEqual(decision.conditions, [{"field": "rating", "operator": "gte", "value": 1}])

    def test_join_keys_fall_back_to_value_overlap_when_names_differ(self):
        with tempfile.TemporaryDirectory(prefix="m11c_v2_keys_") as temp:
            root = Path(temp)
            square = {"type": "Polygon", "coordinates": [[[0, 0], [0, 10], [10, 10], [10, 0], [0, 0]]]}
            areas = {"type": "FeatureCollection", "features": [
                {"type": "Feature", "id": f"a{i}", "properties": {"code": f"z{i}", "title": f"Zone {i}"},
                 "geometry": square} for i in range(3)]}
            shops = {"type": "FeatureCollection", "features": [
                {"type": "Feature", "id": f"s{i}", "properties": {"zone": f"z{i % 3}", "sales": i * 10},
                 "geometry": {"type": "Point", "coordinates": [1, 1]}} for i in range(5)]}
            files = []
            for name, payload in (("demo_areas.geojson", areas), ("demo_shops.geojson", shops)):
                path = root / name
                path.write_text(json.dumps(payload), "utf-8")
                files.append(CatalogueFile(role=name.split("_")[1].split(".")[0], path=path, crs="EPSG:3857",
                                           source="demo", source_version="1",
                                           sha256=hashlib.sha256(path.read_bytes()).hexdigest()))
            catalogue = DatasetCatalogue(name="demo", scope="Demo catalogue", files=tuple(files))
            refs = register_catalogue(catalogue, LocalDatasetRegistry(root / "d"))
            profile = profile_catalogue(catalogue)
            decision = ground_frame(frame(op="aggregate", data="shops", other="areas", metric="sum",
                                          metric_field="sales", where=[{"field": "sales", "cmp": ">=", "value": 10}]),
                                    refs, profile, "Demo catalogue", "Sum shop sales of at least 10 per area")
            self.assertEqual(decision.status, "request", decision.evidence)
            self.assertEqual(decision.parameters["region_id_field"], "code")
            self.assertEqual(decision.parameters["values_region_id_field"], "zone")
            self.assertEqual(plan_request(decision.request).status, "plan")

    def test_grounding_reads_no_task_metadata(self):
        source = inspect.getsource(model_frontend)
        for forbidden in ("expectation", "capability", "task_type", "feature_ids", "m11b_tasks", "source_fixtures"):
            self.assertNotIn(forbidden, source)


class Client(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.profile = profile_catalogue(dev_catalogue())
        cls.prompt = PromptTemplate(text="Catalogue:\n{catalogue}\nRequest: {query}\nJSON: ", label="test")

    def client(self, script):
        transport = FakeTransport(script)
        return ModelFrontend(self.prompt, transport=transport), transport

    def test_single_successful_attempt_uses_frozen_request(self):
        frontend, transport = self.client([ok('{"op":"rank","data":"regions"}')])
        call = frontend.call("Rank the regions", self.profile)
        self.assertEqual((call.failed, len(call.attempts), call.retries, call.timeouts), (False, 1, 0, 0))
        self.assertEqual(call.frame.op, "rank")
        request = transport.requests[0]
        self.assertEqual(request["url"], "http://127.0.0.1:11434/api/generate")
        self.assertEqual(request["timeout"], 120.0)
        payload = request["payload"]
        self.assertEqual(payload["model"], MODEL_ID)
        self.assertIs(payload["stream"], False)
        self.assertEqual(payload["keep_alive"], "30m")
        self.assertEqual(payload["options"], DECODING_OPTIONS)
        self.assertEqual(DECODING_OPTIONS, {"temperature": 0, "seed": 1107, "num_ctx": 4096, "num_predict": 256,
                                            "num_thread": 4, "use_mmap": True})
        self.assertEqual(payload["format"], frame_schema(self.profile))
        self.assertTrue(payload["prompt"].endswith("Request: Rank the regions\nJSON: "))
        self.assertEqual((call.prompt_tokens, call.eval_tokens), (100, 20))

    def test_timeout_then_success_is_a_retry_not_a_failure(self):
        frontend, _ = self.client([TimeoutError("slow"), ok('{"op":"search","data":null}')])
        call = frontend.call("Find something", self.profile)
        self.assertEqual((call.failed, len(call.attempts), call.retries, call.timeouts), (False, 2, 1, 1))
        self.assertEqual([a.outcome for a in call.attempts], ["timeout", "ok"])

    def test_two_bad_attempts_fail_the_logical_call(self):
        frontend, transport = self.client([(500, b"oops"), ok("not json")])
        call = frontend.call("Find something", self.profile)
        self.assertEqual((call.failed, len(call.attempts), call.retries), (True, 2, 1))
        self.assertEqual([a.outcome for a in call.attempts], ["http_error", "non_json"])
        self.assertIsNone(call.frame)
        self.assertEqual(len(transport.requests), MAX_ATTEMPTS)
        frontend, _ = self.client([ConnectionRefusedError("down"), ok('{"op":"search","data":"nowhere"}')])
        call = frontend.call("Find something", self.profile)
        self.assertEqual([a.outcome for a in call.attempts], ["connection_error", "schema_invalid"])
        self.assertTrue(call.failed)

    def test_raw_responses_are_kept_for_replay(self):
        frontend, _ = self.client([ok('{"op":"rank","data":"regions"}')])
        call = frontend.call("Rank the regions", self.profile)
        record = call.record()
        self.assertEqual(record["attempts"][0]["raw_response"], '{"op":"rank","data":"regions"}')
        replayed = model_frontend.LogicalCall.from_record(record, self.profile)
        self.assertEqual(replayed.frame, call.frame)
        self.assertEqual(replayed.record(), record)

    def test_model_identity_is_checked_against_the_frozen_digest(self):
        tags = {"models": [{"name": MODEL_ID, "digest": MODEL_DIGEST}]}
        frontend, _ = self.client([(200, b'{"version":"0.13.5"}'), (200, json.dumps(tags).encode())])
        self.assertEqual(frontend.verify_model()["digest"], MODEL_DIGEST)
        wrong = {"models": [{"name": MODEL_ID, "digest": "0" * 64}]}
        frontend, _ = self.client([(200, b'{"version":"0.13.5"}'), (200, json.dumps(wrong).encode())])
        with self.assertRaises(ModelUnavailable):
            frontend.verify_model()
        frontend, _ = self.client([ConnectionRefusedError("down")])
        with self.assertRaises(ModelUnavailable):
            frontend.verify_model()


class SystemInjection(unittest.TestCase):
    def test_v1_configurations_are_unchanged_without_injection(self):
        self.assertEqual([s.config() for s in formal_systems()], V1_MANIFEST["formal_systems"])
        self.assertEqual([s.config() for s in ablation_systems()], V1_MANIFEST["ablation_systems"])

    def test_injected_decision_source_replaces_the_rule_frontend(self):
        calls = []

        class Source:
            def __call__(self, query, refs, catalogue):
                calls.append(query)
                return ground_frame(frame(op="rank", data="regions"), refs, profile_catalogue(catalogue),
                                    catalogue.scope, query)

            def identity(self):
                return {"frontend_version": MODEL_FRONTEND_VERSION, "model_id": MODEL_ID,
                        "prompt_version": "test@0"}

        from src.evaluation.m11c import system_input
        from src.evaluation.m11b import load_m11b_tasks
        task = next(t for t in load_m11b_tasks(ROOT / "benchmarks/data/dev/m11b_tasks_v1.jsonl")
                    if t.task_id == "m11b_dev_001")
        for system in (FixedWorkflowSystem(), DynamicAgentSystem(), ValidatorReplanSystem()):
            system.frontend = Source()
            run = system.run(system_input(task), dev_catalogue())
            self.assertEqual(run.prediction.reason_code, "unsupported_rank", system.system_id)
            config = system.config()
            self.assertEqual((config["frontend_version"], config["model_id"], config["prompt_version"]),
                             (MODEL_FRONTEND_VERSION, MODEL_ID, "test@0"))
        self.assertEqual(len(calls), 3)


if __name__ == "__main__":
    unittest.main()
