"""Build one immutable, standalone M10 coverage demo from verified offline M04c fixtures."""
from __future__ import annotations

import json
from pathlib import Path
import tempfile

from src.agent.dynamic_planner import plan_request
from src.agent.result_presentation import VerifiedRun, build_analysis_result, render_analysis_html
from src.agent.validation import ValidationPolicy, execute_validated_plan
from src.datasets import LocalDatasetRegistry
from src.tools import ANALYSIS_TOOL_REGISTRY, ReprojectDatasetInput

ROOT = Path(__file__).resolve().parents[1]
FIX = ROOT / "data/fixtures/tools/m04c/v1"
RESULT = ROOT / "benchmarks/results/m10/dev/m10_demo_result_v2.json"
HTML = ROOT / "docs/demo/m10_analysis_result_v2.html"


def main() -> None:
    if RESULT.exists() or HTML.exists():
        raise SystemExit("M10 demo release already exists; refusing overwrite")
    with tempfile.TemporaryDirectory(prefix="m10_demo_") as temporary:
        datasets = LocalDatasetRegistry(Path(temporary) / "registry")
        facilities = datasets.register_file(
            FIX / "facilities_wgs84.geojson", kind="vector", crs="EPSG:4326",
            source="fixture:m04c-facilities", source_version="m04c-v1",
        )
        sites = ANALYSIS_TOOL_REGISTRY.invoke(
            "reproject_dataset",
            ReprojectDatasetInput(dataset=facilities, target_crs="EPSG:3857"),
            datasets=datasets,
        ).dataset
        schools = datasets.register_file(
            FIX / "regions_3857.geojson", kind="vector", crs="EPSG:3857",
            source="fixture:m04c-regions", source_version="m04c-v1",
        )
        request = {
            "query": "find sites within 75 m of schools in Lund",
            "datasets": {
                "sites": {"kind": "vector", "source": "fixture:m04c-facilities", "crs": "EPSG:3857", "required_fields": ["name"]},
                "schools": {"kind": "vector", "source": "fixture:m04c-regions", "crs": "EPSG:3857", "required_fields": ["region_id"]},
            },
            "place_crs": "EPSG:4326", "analysis_crs": "EPSG:3857", "output_crs": "EPSG:3857",
        }
        plan = plan_request(request)
        if plan.status != "plan":
            raise SystemExit("M10 demo M07 plan rejected")
        outcome = execute_validated_plan(
            request, plan, {"sites": sites, "schools": schools},
            datasets, policy=ValidationPolicy(allow_empty=False, max_features=100),
        )
        if outcome.status != "success":
            raise SystemExit(f"M10 demo M08 outcome rejected: {outcome.error_code}")
        verified = VerifiedRun(request=request, plan=plan, outcome=outcome)
        result = build_analysis_result(verified, datasets)
        page = render_analysis_html(result, verified, datasets)
    RESULT.parent.mkdir(parents=True, exist_ok=True)
    HTML.parent.mkdir(parents=True, exist_ok=True)
    with RESULT.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(result.model_dump(mode="json"), stream, ensure_ascii=False, sort_keys=True, indent=2)
        stream.write("\n")
    with HTML.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(page)
    print(json.dumps({
        "template": result.template, "layers": len(result.layers),
        "verified_result_count": result.metrics[0].value,
        "downloads": [item.filename for item in result.downloads],
        "result": str(RESULT.relative_to(ROOT)), "html": str(HTML.relative_to(ROOT)),
    }, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()

