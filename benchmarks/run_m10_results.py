"""Score the frozen M10 result-expression matrix; exclusive receipt."""
from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path

from src.agent.result_presentation import rendered_report, validate_analysis_result
from tests.test_m10_analysis_result import CASES, M10ResultTests

CASES_PATH = Path("benchmarks/data/dev/m10_result_cases_v1.jsonl")
OUTPUT = Path("benchmarks/results/m10/dev/m10_result_eval_v1.json")


def evaluate() -> dict:
    if len(CASES) != 12:
        raise ValueError("M10 frozen matrix changed")
    rows = []
    complete = numeric = layers = exports = 0
    template_counts = {"search": 0, "coverage": 0, "comparison": 0}
    for case in CASES:
        fixture = M10ResultTests("test_frozen_matrix_shape")
        fixture.setUp()
        try:
            result, primary, comparison = fixture.run_case(case)
            validate_analysis_result(result, primary, fixture.registry, comparison=comparison)
            is_complete = bool(result.metrics and result.table and result.sources and result.steps and result.assumptions and result.limitations)
            is_numeric = all(
                all(str(metric.value) in line for metric in result.metrics if "{" + metric.metric_id + "}" in template)
                for template, line in zip([result.headline_template, *result.report_templates], rendered_report(result))
            )
            is_layers = len(result.layers) >= 2 and all(layer.dataset_id in {source.dataset_id for source in result.sources} for layer in result.layers)
            is_exports = len(result.downloads) >= 2 and all(
                hashlib.sha256(base64.b64decode(item.href.split(",", 1)[1])).hexdigest() == item.sha256
                for item in result.downloads
            )
        finally:
            fixture.tearDown()
        complete += is_complete
        numeric += is_numeric
        layers += is_layers
        exports += is_exports
        template_counts[case["template"]] += 1
        rows.append({
            "case_id": case["case_id"], "template": result.template,
            "result_count": result.metrics[0].value, "layer_count": len(result.layers),
            "download_count": len(result.downloads),
            "contract_complete": is_complete, "numeric_traceable": is_numeric,
            "map_valid": is_layers, "exports_valid": is_exports,
        })
    if any(count != 4 for count in template_counts.values()):
        raise ValueError("M10 template quota changed")
    metrics = {
        "contract_complete": {"passed": complete, "total": 12, "rate": complete / 12},
        "numeric_traceable": {"passed": numeric, "total": 12, "rate": numeric / 12},
        "multilayer_map": {"passed": layers, "total": 12, "rate": layers / 12},
        "verified_downloads": {"passed": exports, "total": 12, "rate": exports / 12},
    }
    return {
        "schema_version": "1.0", "evaluation_id": "m10_result_eval_v1",
        "source": "public synthetic offline M04c DatasetRefs and M07/M08 verified outcomes",
        "cases_sha256": hashlib.sha256(CASES_PATH.read_bytes()).hexdigest(),
        "case_count": 12, "template_counts": template_counts,
        "metrics": metrics, "cases": rows,
        "thresholds": {key: "12/12" for key in metrics},
        "passed": all(value["passed"] == 12 for value in metrics.values()),
        "limitations": "Visible small fixtures and bounded presentation templates; no live model, hidden set, real city coverage, transport isochrone or production browser evidence. M03 Phase 2 retrieval queries are catalog-informed.",
    }


def main() -> None:
    report = evaluate()
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    with OUTPUT.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(report, stream, ensure_ascii=False, sort_keys=True, indent=2)
        stream.write("\n")
    print(json.dumps(report["metrics"], sort_keys=True))
    if not report["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()

