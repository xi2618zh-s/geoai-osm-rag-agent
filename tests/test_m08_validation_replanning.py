"""M08 validator and bounded recovery over real M04 DatasetRefs."""
from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from src.agent.dynamic_planner import plan_request
from src.agent.validation import ValidationPolicy, execute_validated_plan
from src.datasets import LocalDatasetRegistry
from src.tools import ANALYSIS_TOOL_REGISTRY, AnalysisToolError, ReprojectDatasetInput

ROOT=Path(__file__).resolve().parents[1]
FIX=ROOT / "data/fixtures/tools/m04c/v1"
CASES=[json.loads(line) for line in (ROOT/"benchmarks/data/dev/m08_fault_cases_v1.jsonl").read_text(encoding="utf-8").splitlines()]


class InjectedTools:
    def __init__(self, fault):
        self.fault=fault
        self.calls=0
    def get(self,name):
        return ANALYSIS_TOOL_REGISTRY.get(name)
    def invoke(self,name,payload,*,datasets):
        if name=="filter_attributes":
            self.calls+=1
            if self.fault=="transient_timeout" and self.calls==1:
                raise TimeoutError("injected timeout")
            if self.fault in {"tool_unavailable","invalid_replan"}:
                raise AnalysisToolError("ANALYSIS_TOOL_NOT_REGISTERED","injected missing tool")
        result=ANALYSIS_TOOL_REGISTRY.invoke(name,payload,datasets=datasets)
        if name=="filter_attributes" and self.fault=="missing_output":
            datasets.release(result.dataset.id)
        if name=="filter_attributes" and self.fault=="corrupt_output":
            with datasets.resolve_path(result.dataset.id).open("ab") as stream:
                stream.write(b" ")
        return result


class M08FaultCases(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix="m08_")
        self.datasets=LocalDatasetRegistry(Path(self.temp.name)/"registry")
    def tearDown(self):
        self.temp.cleanup()
    def register(self,filename):
        crs="EPSG:3857" if filename in {"regions_3857.geojson","expected_final.geojson"} else "EPSG:4326"
        return self.datasets.register_file(FIX/filename,kind="vector",crs=crs,source="m08-fixture",source_version="m08-v1")
    def setup_task(self,task,fault):
        if task in {"search","filtered_search"}:
            if fault=="empty_result":
                ref=self.register("empty_wgs84.geojson")
            elif fault in {"invalid_geometry","invalid_geometry_no_repair"}:
                ref=self.register("invalid_wgs84.geojson")
            else:
                ref=self.register("facilities_wgs84.geojson")
            expected_crs="EPSG:3857" if fault=="wrong_crs" else "EPSG:4326"
            query="search sites in Lund" if task=="search" else "search sites where name = Alpha in Lund"
            request={"query":query,"datasets":{"sites":{"kind":"vector","source":"fixture:sites","crs":expected_crs,"required_fields":["name"] if task=="filtered_search" else []}},"place_crs":"EPSG:4326","analysis_crs":expected_crs,"output_crs":expected_crs}
            return request,{"sites":ref}
        if task=="within_distance":
            facilities=self.register("facilities_wgs84.geojson")
            sites=ANALYSIS_TOOL_REGISTRY.invoke("reproject_dataset",ReprojectDatasetInput(dataset=facilities,target_crs="EPSG:3857"),datasets=self.datasets).dataset
            schools=self.register("regions_3857.geojson")
            request={"query":"find sites within 75 m of schools in Lund","datasets":{"sites":{"kind":"vector","source":"fixture:sites","crs":"EPSG:3857","required_fields":[]},"schools":{"kind":"vector","source":"fixture:schools","crs":"EPSG:3857","required_fields":[]}},"place_crs":"EPSG:4326","analysis_crs":"EPSG:3857","output_crs":"EPSG:3857"}
            return request,{"sites":sites,"schools":schools}
        if task=="aggregate_by_region":
            regions=self.register("regions_3857.geojson")
            values=self.register("regions_3857.geojson")
            request={"query":"aggregate count by region_id from values into regions using region_id in Lund","datasets":{"regions":{"kind":"vector","source":"fixture:regions","crs":"EPSG:3857","required_fields":["region_id"]},"values":{"kind":"vector","source":"fixture:values","crs":"EPSG:3857","required_fields":["region_id"]}},"place_crs":"EPSG:4326","analysis_crs":"EPSG:3857","output_crs":"EPSG:3857"}
            return request,{"regions":regions,"values":values}
        raise AssertionError(task)
    def run_case(self,row):
        task,fault=row["task"],row["fault"]
        request,bindings=self.setup_task(task,fault)
        policy=ValidationPolicy(allow_empty=False,max_features=2 if fault=="feature_cap" else 100)
        if fault=="wrong_crs":
            policy=policy.model_copy(update={"allow_crs_repair":True})
        if fault=="invalid_geometry":
            policy=policy.model_copy(update={"allow_geometry_repair":True})
        if fault=="missing_primary_with_alternate":
            original=bindings["sites"]
            bad=original.model_copy(update={"id":"ds_"+"0"*32,"storage_uri":"registry://datasets/ds_"+"0"*32+"/data.geojson"})
            bindings["sites"]=bad
            policy=policy.model_copy(update={"alternate_sources":{"sites":original}})
        conclusion="Found 999 features." if fault=="false_conclusion" else None
        replan=(lambda request,plan,finding: plan_request(dict(request.model_dump(mode="json"),query="forecast traffic in Lund"))) if fault=="invalid_replan" else None
        result=execute_validated_plan(request,plan_request(request),bindings,self.datasets,policy=policy,tools=InjectedTools(fault),replan=replan,conclusion=conclusion)
        self.assertEqual(result.status,row["expected_status"])
        if fault=="none":
            self.assertTrue(result.first_pass_success)
            self.assertEqual(result.recoveries,[])
            self.assertIsNotNone(result.final_dataset)
        else:
            self.assertFalse(result.first_pass_success)
            self.assertIn(row["expected_error"],[finding.code for finding in result.findings])
            if result.status=="success":
                self.assertTrue(any(event.status=="succeeded" for event in result.recoveries))
            else:
                self.assertEqual(result.error_code,row["expected_error"])
                self.assertTrue(result.limitations)
        self.assertLessEqual(result.tool_call_count,24)
        self.assertLessEqual(result.replan_count,1)
        return result
    def test_input_failure_records_step_without_tool_call(self):
        request,bindings=self.setup_task("search","none")
        valid=bindings["sites"]
        bindings["sites"]=valid.model_copy(update={"id":"ds_"+"0"*32,"storage_uri":"registry://datasets/ds_"+"0"*32+"/data.geojson"})
        result=execute_validated_plan(request,plan_request(request),bindings,self.datasets,
            policy=ValidationPolicy(allow_empty=False,max_features=100))
        self.assertEqual(result.error_code,"M08_DATASET_UNAVAILABLE")
        self.assertEqual(result.step_records[-1].status,"failed")
        self.assertEqual(result.step_records[-1].attempts,0)
        self.assertEqual(result.tool_call_count,0)

    def test_retry_output_corruption_records_failed_recovery(self):
        request,bindings=self.setup_task("search","none")
        class TimeoutThenCorrupt(InjectedTools):
            def invoke(self,name,payload,*,datasets):
                if name=="filter_attributes" and self.calls==0:
                    self.calls+=1
                    raise TimeoutError("first call")
                result=super().invoke(name,payload,datasets=datasets)
                if name=="filter_attributes":
                    with datasets.resolve_path(result.dataset.id).open("ab") as stream:
                        stream.write(b" ")
                return result
        result=execute_validated_plan(request,plan_request(request),bindings,self.datasets,
            policy=ValidationPolicy(allow_empty=False,max_features=100),tools=TimeoutThenCorrupt("none"))
        self.assertEqual(result.status,"partial")
        self.assertEqual(result.error_code,"M08_OUTPUT_UNREADABLE")
        self.assertEqual(result.recoveries[-1].action,"retry_same_parameters")
        self.assertEqual(result.recoveries[-1].status,"failed")

    def test_second_timeout_records_failed_recovery(self):
        request,bindings=self.setup_task("search","none")
        class AlwaysTimeout(InjectedTools):
            def invoke(self,name,payload,*,datasets):
                if name=="filter_attributes":
                    raise TimeoutError("still unavailable")
                return super().invoke(name,payload,datasets=datasets)
        result=execute_validated_plan(request,plan_request(request),bindings,self.datasets,
            policy=ValidationPolicy(allow_empty=False,max_features=100),tools=AlwaysTimeout("none"))
        self.assertEqual(result.status,"partial")
        self.assertEqual(result.error_code,"M08_TOOL_TIMEOUT")
        self.assertEqual([event.status for event in result.recoveries],["failed"])
        self.assertEqual(result.step_records[-1].attempts,2)

    def test_one_valid_replan_changes_explicit_binding(self):
        request,bindings=self.setup_task("search","none")
        valid=bindings["sites"]
        bindings["sites"]=valid.model_copy(update={"id":"ds_"+"0"*32,"storage_uri":"registry://datasets/ds_"+"0"*32+"/data.geojson"})
        def replan(req,plan,finding):
            return req,plan,{"sites":valid}
        result=execute_validated_plan(request,plan_request(request),bindings,self.datasets,
            policy=ValidationPolicy(allow_empty=False,max_features=100),replan=replan)
        self.assertEqual(result.status,"success")
        self.assertEqual(result.replan_count,1)
        self.assertFalse(result.first_pass_success)
        self.assertEqual(result.recoveries[-1].action,"replan_once")

    def test_replan_cannot_change_dataset_contract(self):
        request,bindings=self.setup_task("search","none")
        valid=bindings["sites"]
        bindings["sites"]=valid.model_copy(update={"id":"ds_"+"0"*32,"storage_uri":"registry://datasets/ds_"+"0"*32+"/data.geojson"})
        def replan(req,plan,finding):
            altered=req.model_copy(update={"analysis_crs":"EPSG:3857"})
            return altered,plan,{"sites":valid}
        result=execute_validated_plan(request,plan_request(request),bindings,self.datasets,
            policy=ValidationPolicy(allow_empty=False,max_features=100),replan=replan)
        self.assertEqual(result.status,"partial")
        self.assertEqual(result.error_code,"M08_REPLAN_INVALID")
        self.assertEqual(result.replan_count,1)
        self.assertEqual(result.tool_call_count,0)
    def test_invalid_static_plan_never_invokes_tool(self):
        request,bindings=self.setup_task("search","none")
        plan=plan_request(request)
        invalid=plan.model_copy(update={"tool_steps":[plan.tool_steps[0].model_copy(update={"tool":"run_python"})]})
        class NeverCall(InjectedTools):
            def invoke(self,name,payload,*,datasets):
                self.fail("unvalidated tool invoked")
        tools=InjectedTools("none")
        result=execute_validated_plan(request,invalid,bindings,self.datasets,
            policy=ValidationPolicy(allow_empty=False,max_features=100),tools=tools)
        self.assertEqual(result.error_code,"M08_PLAN_INVALID")
        self.assertEqual(result.tool_call_count,0)

    def test_explicit_count_constraint_and_correct_claim(self):
        request,bindings=self.setup_task("search","none")
        result=execute_validated_plan(request,plan_request(request),bindings,self.datasets,
            policy=ValidationPolicy(allow_empty=False,max_features=100,expected_min_count=4))
        self.assertEqual(result.error_code,"M08_CONSTRAINT_UNMET")
        ok=execute_validated_plan(request,plan_request(request),bindings,self.datasets,
            policy=ValidationPolicy(allow_empty=False,max_features=100),conclusion="Found 3 features.")
        self.assertEqual(ok.status,"success")

    def test_explicit_empty_policy(self):
        request,bindings=self.setup_task("search","empty_result")
        result=execute_validated_plan(request,plan_request(request),bindings,self.datasets,
            policy=ValidationPolicy(allow_empty=True,max_features=100))
        self.assertEqual(result.status,"success")
        self.assertEqual(result.final_dataset.row_count,0)

    def test_frozen_matrix(self):
        self.assertEqual(len(CASES),16)
        self.assertEqual(sum(row["fault"]=="none" for row in CASES),4)
        self.assertEqual(sum(row["expected_status"]=="success" for row in CASES),8)


def make_case(row):
    def test(self):
        self.run_case(row)
    return test
for row in CASES:
    setattr(M08FaultCases,"test_"+row["case_id"],make_case(row))

if __name__=="__main__":
    unittest.main()

