"""M12a evaluation harness (m12a-protocol-v1): catalogue variants, case runs and scoring.

Cases, references and thresholds are frozen in docs/roadmap/M12A_EVALUATION_FREEZE.md
and benchmarks/results/m12/m12a/freeze/m12a_freeze_manifest_v1.json. Run records carry
no DatasetRef IDs or timestamps so that two runs produce identical receipts.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any, Optional

from src.agent.validation import ValidationPolicy
from src.datasets import DatasetRegistry, LocalDatasetRegistry
from src.scenarios import (
    CatalogueEntry, CrsEvidence, ScenarioCatalogue, ScenarioError, execute_scenario_plan, reconfirm_source_crs,
)
from src.scenarios.html import render_scenario_html
from src.scenarios.m12a import build_m12a_report, interpret_m12a, plan_m12a, resume_m12a, validate_m12a_report
from src.scenarios.report import ClarificationTurn, ScenarioReport, contains_forbidden_phrase, rendered_report

ROOT = Path(__file__).resolve().parents[2]
CASES = ROOT / "benchmarks/data/dev/m12a_scenario_cases_v1.jsonl"
REFERENCES = ROOT / "benchmarks/data/dev/m12a_references_v1.json"
SCB_FILE = ROOT / "data/fixtures/population/scb/m12_lund_v1/scb_population_grid_2025_lund_20km_raw.geojson"
POI_FILE = ROOT / "data/fixtures/osm/m12_lund_poi_v1/lund_poi_points_v1.geojson"
POI_MANIFEST = ROOT / "data/fixtures/osm/m12_lund_poi_v1/manifest.json"
STUDY_BBOX = [378000.0, 6166000.0, 398000.0, 6186000.0]
VARIANTS = ("standard", "poi_crs_mislabeled", "poi_without_schools")
REFERENCE_INT_METRICS = ("gap_cell_count", "gap_children", "total_children", "school_count", "grid_cell_count",
                         "band_not_reported_cells")
SCB_ATTRIBUTION = "Statistics Sweden (SCB)"
OSM_ATTRIBUTION = "© OpenStreetMap contributors"


def load_cases() -> list[dict[str, Any]]:
    return [json.loads(line) for line in CASES.read_text(encoding="utf-8").splitlines() if line.strip()]


def load_references() -> dict[str, Any]:
    return json.loads(REFERENCES.read_text(encoding="utf-8"))["references"]


def build_catalogue(registry: DatasetRegistry, variant: str, *, workdir: Path) -> tuple[ScenarioCatalogue, dict[str, Any]]:
    """Register frozen fixtures for a catalogue variant inside a fresh registry."""
    if variant not in VARIANTS:
        raise ValueError(f"unknown catalogue variant {variant}")
    poi_manifest = json.loads(POI_MANIFEST.read_text(encoding="utf-8"))
    pbf = poi_manifest["source_pbf"]
    poi_source, poi_version = "osm:m12-lund-poi", f"pbf-sha256:{pbf['sha256'][:12]}"
    population = registry.register_file(SCB_FILE, kind="vector", crs="EPSG:3006",
                                        source="scb:befolkning_1km_2025", source_version="2025-12-31")
    extra: dict[str, Any] = {}
    poi_path, poi_crs = POI_FILE, "EPSG:4326"
    if variant == "poi_crs_mislabeled":
        poi_crs = "EPSG:3006"
        extra["alternate_osm_poi"] = registry.register_file(POI_FILE, kind="vector", crs="EPSG:4326",
                                                            source=poi_source, source_version=poi_version)
    elif variant == "poi_without_schools":
        document = json.loads(POI_FILE.read_text(encoding="utf-8"))
        document["features"] = [f for f in document["features"] if f["properties"].get("amenity") != "school"]
        poi_path = Path(workdir) / "poi_without_schools.geojson"
        poi_path.write_bytes((json.dumps(document, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8"))
    poi = registry.register_file(poi_path, kind="vector", crs=poi_crs, source=poi_source, source_version=poi_version)
    entries = {
        "population_grid_raw": CatalogueEntry(
            role="population_grid_raw", semantic_type="scb_population_grid_raw", kind="vector",
            declared_crs="EPSG:3006", dataset=population,
            required_fields=["rutid_inspire", "rutid_scb", "beftotalt", "referenstid"],
            license="CC0-1.0", attribution=SCB_ATTRIBUTION, provider_verified=True,
            temporal_note="SCB 1 km grid reference date 2025-12-31 (WFS stat:befolkning_1km_2025)."),
        "osm_poi": CatalogueEntry(
            role="osm_poi", semantic_type="poi_points", kind="vector", declared_crs=poi_crs, dataset=poi,
            required_fields=["poi_id", "amenity", "name"], license="ODbL-1.0", attribution=OSM_ATTRIBUTION,
            provider_verified=False,
            temporal_note=f"OSM PBF header timestamp {pbf['header']['timestamp']}; local file, provider unverified "
                          f"(SHA-256 {pbf['sha256'][:12]}...)."),
    }
    catalogue = ScenarioCatalogue(catalogue_id=f"m12a-lund-{variant}", place="Lund", study_bbox=list(STUDY_BBOX),
                                  study_crs="EPSG:3006", analysis_crs="EPSG:3006", entries=entries)
    return catalogue, extra


def _policy(alternate: Optional[Any]) -> ValidationPolicy:
    return ValidationPolicy(allow_empty=True, max_features=10000,
                            alternate_sources={"osm_poi": alternate} if alternate is not None else {})


def run_case(case: dict[str, Any], *, workdir: Path) -> tuple[dict[str, Any], Optional[tuple[ScenarioReport, str]]]:
    registry = LocalDatasetRegistry(Path(workdir) / "registry")
    catalogue, extra = build_catalogue(registry, case["catalogue_variant"], workdir=Path(workdir))
    record: dict[str, Any] = {
        "case_id": case["case_id"], "after_resume_status": None, "planned_tools": [], "outcome_status": None,
        "error_code": None, "failed_step": None, "invoked_tools": [], "all_tool_calls": [], "recoveries": [],
        "replan_count": 0, "stage1_error_code": None, "reconfirm_error_code": None, "reconfirmed_crs": None,
        "reconfirm_lineage_ok": None, "analysis_steps": 0, "gap_grid_ids": None, "metrics": None,
        "final_content_hash": None, "report_checks": None,
    }
    front = interpret_m12a(case["command"], catalogue)
    clar = front.clarification
    record.update(frontend_status=front.status, frontend_reason=clar.reason_code if clar else front.reason_code,
                  missing_fields=list(clar.missing_fields) if clar else [], choices=list(clar.choices) if clar else [])
    turns: list[ClarificationTurn] = []
    if front.status == "clarification" and case["resume_answer"]:
        resumed = resume_m12a(case["command"], clar, case["resume_answer"], catalogue)
        turns.append(ClarificationTurn(reason_code=clar.reason_code, question=clar.question, answer=case["resume_answer"]))
        record["after_resume_status"] = resumed.status
        front = resumed
    if front.status != "plan":
        return record, None
    intent = front.intent
    plan = plan_m12a(intent, catalogue)
    record["planned_tools"] = [step.tool for step in plan.steps]
    policy = _policy(extra.get("alternate_osm_poi") if case["alternate_snapshot"] else None)
    replanner = lambda cat: plan_m12a(intent, cat)  # noqa: E731
    outcome = execute_scenario_plan(plan, catalogue, registry, policy=policy, replanner=replanner)
    calls = list(outcome.tool_calls)
    if case["reconfirm_evidence"]:
        record["stage1_error_code"] = outcome.error_code
        try:
            fixed = reconfirm_source_crs(catalogue, "osm_poi", CrsEvidence(**case["reconfirm_evidence"]), registry)
        except ScenarioError as error:
            record["reconfirm_error_code"] = error.code
        else:
            entry, previous = fixed.entries["osm_poi"], catalogue.entries["osm_poi"].dataset
            last = entry.dataset.lineage[-1]
            record["reconfirmed_crs"] = entry.declared_crs
            record["reconfirm_lineage_ok"] = last.operation == "crs_reconfirmed" and last.parent_ids == [previous.id]
            catalogue = fixed
            plan = plan_m12a(intent, catalogue)
            record["planned_tools"] = [step.tool for step in plan.steps]
            outcome = execute_scenario_plan(plan, catalogue, registry, policy=policy, replanner=replanner)
            calls += outcome.tool_calls
    record.update(
        outcome_status=outcome.status, error_code=outcome.error_code, failed_step=outcome.failed_step,
        invoked_tools=outcome.invoked_tools, all_tool_calls=calls, replan_count=outcome.replan_count,
        recoveries=[[event.action, event.status] for event in outcome.recoveries],
    )
    if outcome.status != "success":
        return record, None
    final = outcome.final_dataset
    records = list(registry.read(final.id).records)
    record.update(analysis_steps=len(outcome.invoked_tools), final_content_hash=final.content_hash,
                  gap_grid_ids=sorted(item["properties"]["left_grid_id"] for item in records))
    report = build_m12a_report(intent, outcome, catalogue, registry, clarifications=turns)
    try:
        validate_m12a_report(report, intent, outcome, catalogue, registry, clarifications=turns)
        validated = True
    except ScenarioError:
        validated = False
    page = render_scenario_html(report)
    texts = rendered_report(report) + list(report.limitations) + [report.grammar_note]
    licenses = {(item.license, item.attribution) for item in report.data_licenses}
    record["metrics"] = {item.metric_id: item.value for item in report.metrics}
    record["report_checks"] = {
        "validated": validated,
        "licenses": ("CC0-1.0", SCB_ATTRIBUTION) in licenses and ("ODbL-1.0", OSM_ATTRIBUTION) in licenses,
        "population_statement": "not the exact population" in " ".join(texts).lower(),
        "grammar_note": "restricted command grammar" in report.grammar_note.lower(),
        "forbidden_phrase": contains_forbidden_phrase(texts),
        "layer_count": len(report.layers),
        "download_geojson_features": report.downloads[0].feature_count,
        "html_self_contained": "<script src" not in page.lower() and "<link" not in page.lower(),
        "html_has_layers_and_licenses": all(label in page for label in [l.label for l in report.layers]
                                            + [SCB_ATTRIBUTION, OSM_ATTRIBUTION, "restricted command grammar"]),
    }
    return record, (report, page)


def _share_ok(value: Any, reference: Any) -> bool:
    if reference is None:
        return value is None
    if value is None:
        return False
    if reference == 0:
        return abs(value) <= 1e-12
    return math.isclose(value, reference, rel_tol=1e-6, abs_tol=0.0)


def score_case(case: dict[str, Any], record: dict[str, Any], references: dict[str, Any]) -> dict[str, Any]:
    expected = case["expected"]
    components = {
        "frontend": (record["frontend_status"], record["frontend_reason"], record["missing_fields"], record["choices"],
                     record["after_resume_status"]) == (expected["frontend_status"], expected["frontend_reason"],
                                                        expected["missing_fields"], expected["choices"],
                                                        expected["after_resume_status"]),
        "planned_tools": record["planned_tools"] == expected["planned_tools"],
        "invoked_tools": record["invoked_tools"] == expected["invoked_tools"],
        "forbidden_tools": not any(tool in record["all_tool_calls"] for tool in expected["forbidden_tools"]),
        "outcome": (record["outcome_status"], record["error_code"], record["failed_step"]) ==
                   (expected["outcome_status"], expected["error_code"], expected["failed_step"]),
        "recoveries": record["recoveries"] == expected["recoveries"] and record["replan_count"] == expected["replan_count"],
        "reconfirmation": (record["stage1_error_code"], record["reconfirm_error_code"], record["reconfirmed_crs"]) ==
                          (expected["stage1_error_code"], expected["reconfirm_error_code"], expected["reconfirmed_crs"])
                          and (expected["reconfirmed_crs"] is None or record["reconfirm_lineage_ok"] is True),
    }
    reference = references.get(expected["reference"]) if expected["reference"] else None
    if reference is None:
        components["gap_ids"] = record["gap_grid_ids"] is None
        components["metrics"] = record["metrics"] is None
    else:
        metrics = record["metrics"] or {}
        components["gap_ids"] = record["gap_grid_ids"] == reference["gap_grid_ids"]
        components["metrics"] = (all(metrics.get(key) == reference[key] for key in REFERENCE_INT_METRICS)
                                 and _share_ok(metrics.get("gap_children_share"), reference["gap_children_share"]))
    checks = record["report_checks"]
    if expected["report_expected"]:
        components["report"] = bool(checks) and reference is not None and all([
            checks["validated"], checks["licenses"], checks["population_statement"], checks["grammar_note"],
            checks["forbidden_phrase"] is None, checks["layer_count"] == 4,
            checks["download_geojson_features"] == reference["gap_cell_count"],
            checks["html_self_contained"], checks["html_has_layers_and_licenses"], record["analysis_steps"] >= 5,
        ])
    else:
        components["report"] = checks is None
    return {"case_id": case["case_id"], "components": components, "passed": all(components.values())}


def evaluate(workroot: Path) -> dict[str, Any]:
    """Run every frozen case once in fresh registries; return records, scores and the A1 snapshot."""
    references = load_references()
    records, scores, snapshot = [], [], None
    for case in load_cases():
        workdir = Path(workroot) / case["case_id"]
        workdir.mkdir(parents=True)
        record, presented = run_case(case, workdir=workdir)
        records.append(record)
        scores.append(score_case(case, record, references))
        if case["case_id"] == "m12a_A1" and presented is not None:
            snapshot = presented
    return {"records": records, "scores": scores, "snapshot": snapshot}


def deterministic_receipt(records: list[dict[str, Any]], scores: list[dict[str, Any]]) -> dict[str, Any]:
    return {"schema": "m12a-deterministic-receipt-v1", "protocol": "m12a-protocol-v1",
            "cases": [{"record": record, "score": score} for record, score in zip(records, scores)],
            "cases_passed": sum(1 for score in scores if score["passed"]), "case_count": len(scores)}
