"""Score the frozen M08 offline fault matrix; exclusive-create result receipt."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from tests.test_m08_validation_replanning import CASES, M08FaultCases

CASES_PATH=Path("benchmarks/data/dev/m08_fault_cases_v1.jsonl")
OUTPUT=Path("benchmarks/results/m08/dev/m08_fault_eval_v1.json")
RECOVERABLE={"recover_timeout","recover_crs","recover_geometry","recover_alternate"}


def evaluate() -> dict:
    data=CASES_PATH.read_bytes()
    if len(CASES)!=16 or {row["case_id"] for row in CASES if row["fault"]=="none"} != {
        "clean_search","clean_filtered","clean_distance","clean_aggregate"
    }:
        raise ValueError("M08 frozen matrix changed")
    rows=[]
    first_pass=clean_pass=recovered=classified=guarded=0
    error_counts={}
    for case in CASES:
        fixture=M08FaultCases("test_frozen_matrix")
        fixture.setUp()
        try:
            outcome=fixture.run_case(case)
        finally:
            fixture.tearDown()
        is_clean=case["fault"]=="none"
        is_recoverable=case["case_id"] in RECOVERABLE
        is_classified=is_clean or case["expected_error"] in [x.code for x in outcome.findings]
        first_pass += outcome.first_pass_success
        clean_pass += is_clean and outcome.first_pass_success
        recovered += is_recoverable and outcome.status=="success"
        classified += (not is_clean) and is_classified
        guarded += (not is_clean and not is_recoverable and outcome.status=="partial")
        if not is_clean:
            code=case["expected_error"]
            error_counts[code]=error_counts.get(code,0)+1
        rows.append({
            "case_id":case["case_id"],"fault":case["fault"],"status":outcome.status,
            "first_pass_success":outcome.first_pass_success,"error_code":outcome.error_code,
            "finding_codes":[f.code for f in outcome.findings],
            "actions":[{"action":r.action,"status":r.status,"reason_code":r.reason_code} for r in outcome.recoveries],
            "tool_call_count":outcome.tool_call_count,"replan_count":outcome.replan_count,
        })
    metrics={
        "first_pass_success":{"passed":first_pass,"total":16,"rate":first_pass/16},
        "clean_first_pass":{"passed":clean_pass,"total":4,"rate":clean_pass/4},
        "recovered_success":{"passed":recovered,"total":4,"rate":recovered/4},
        "error_classification":{"passed":classified,"total":12,"rate":classified/12},
        "guarded_rejection":{"passed":guarded,"total":8,"rate":guarded/8},
    }
    passed=clean_pass==4 and recovered>=3 and classified>=11 and guarded==8
    return {
        "schema_version":"1.0","evaluation_id":"m08_fault_eval_v1",
        "source":"public synthetic offline M04c fixtures with injected failures",
        "cases_sha256":hashlib.sha256(data).hexdigest(),
        "case_count":16,"metrics":metrics,"error_counts":error_counts,
        "cases":rows,"thresholds":{"clean_first_pass":"4/4","recovered_success":">=3/4","error_classification":">=11/12","guarded_rejection":"8/8"},
        "passed":passed,
        "limitations":"Visible small fixtures and mocked faults; no real model, network, hidden set, full transport isochrone or production recovery evidence. M03 Phase 2 queries are catalog-informed.",
    }


def main() -> None:
    report=evaluate()
    OUTPUT.parent.mkdir(parents=True,exist_ok=True)
    with OUTPUT.open("x",encoding="utf-8",newline="\n") as stream:
        json.dump(report,stream,ensure_ascii=False,sort_keys=True,indent=2)
        stream.write("\n")
    print(json.dumps(report["metrics"],sort_keys=True))
    if not report["passed"]:
        raise SystemExit(1)


if __name__=="__main__":
    main()
