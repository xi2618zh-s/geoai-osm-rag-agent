"""M12a school coverage gaps (m12a-protocol-v1; docs/roadmap/M12A_EVALUATION_FREEZE.md).

The front end is a restricted command grammar (fixed English templates with explicit
slots), not open-ended natural-language input. Missing or out-of-range slots are
clarified, never filled silently.
"""
from __future__ import annotations

import re
from typing import Any, Iterable, Optional

from src.datasets import DatasetRegistry

from .contracts import (
    FrontendResult, ScenarioCatalogue, ScenarioClarification, ScenarioError, ScenarioIntent,
    ScenarioOutcome, ScenarioPlan, ScenarioStep,
)
from .gate import validate_plan
from .report import (
    ClarificationTurn, DataLicense, ScenarioChart, ScenarioReport, StepEvidence, downloads, field_null_count,
    field_sum, layer, metric, source, table, verify_metric_evidence,
)

GRAMMAR_VERSION = "m12a-grammar-v1"
PLAN_VERSION = "m12a-plan-v1"
SCENARIO = "m12a_school_coverage_gaps"
ALLOWED_BANDS = ("0-4", "5-9", "10-14", "15-19")
MAX_DISTANCE_M = 2000
SLOT_ORDER = ("distance", "age_band", "threshold", "place")
GRAMMAR_NOTE = ("Commands use a restricted command grammar: fixed English templates with explicit slots. "
                "It is not open-ended natural-language input; missing or out-of-range slots are asked back.")
_NUM = r"[0-9]+(?:\.[0-9]+)?"
_PLACE = r"[A-Za-z][A-Za-z -]*"
_COMMAND = re.compile(
    rf"find school coverage gaps"
    rf"(?: farther than (?P<dist>{_NUM}) (?P<unit>m|km))?"
    rf"(?: where children aged (?P<band_a>[0-9]+)-(?P<band_b>[0-9]+) are at least (?P<threshold>[0-9]+))?"
    rf"(?: in (?P<place>{_PLACE}))?", re.IGNORECASE)
_ANSWERS = {
    "distance": re.compile(rf"distance (?P<dist>{_NUM}) (?P<unit>m|km)", re.IGNORECASE),
    "age_band": re.compile(r"age band (?P<band_a>[0-9]+)-(?P<band_b>[0-9]+)", re.IGNORECASE),
    "threshold": re.compile(r"threshold (?P<threshold>[0-9]+)", re.IGNORECASE),
    "place": re.compile(rf"place (?P<place>{_PLACE})", re.IGNORECASE),
}


def _normalise(text: str) -> str:
    return " ".join(str(text).split())


def _metres(value: str, unit: str) -> int | float:
    metres = float(value) * (1000 if unit.lower() == "km" else 1)
    return int(metres) if metres.is_integer() else metres


def _slots(match: re.Match) -> dict[str, Any]:
    groups = match.groupdict()
    slots: dict[str, Any] = {name: None for name in SLOT_ORDER}
    if groups.get("dist") is not None:
        slots["distance"] = _metres(groups["dist"], groups["unit"])
    if groups.get("band_a") is not None:
        slots["age_band"] = f"{int(groups['band_a'])}-{int(groups['band_b'])}"
    if groups.get("threshold") is not None:
        slots["threshold"] = int(groups["threshold"])
    if groups.get("place") is not None:
        slots["place"] = groups["place"].strip()
    return slots


def _question(reason: str, missing: list[str]) -> str:
    hints = {"distance": "distance (e.g. 'distance 1000 m', at most 2000 m)",
             "age_band": "age band (one of 0-4, 5-9, 10-14, 15-19)",
             "threshold": "threshold (minimum children in the band per cell, at least 1)",
             "place": "place (data are available for Lund only)"}
    lead = {"missing_parameters": "Please provide", "distance_out_of_range": "The distance must be above 0 and at most 2000 m; provide",
            "age_band_unsupported": "That age band is not an SCB five-year band; provide",
            "threshold_out_of_range": "The threshold must be at least 1; provide",
            "place_not_covered": "That place is not covered by the catalogue; provide"}[reason]
    return f"{lead}: " + "; ".join(hints[item] for item in missing) + "."


def _decide(original: str, slots: dict[str, Any], catalogue: ScenarioCatalogue) -> FrontendResult:
    def clarify(reason: str, missing: list[str], choices: Iterable[str] = ()) -> FrontendResult:
        return FrontendResult(status="clarification", grammar_version=GRAMMAR_VERSION, command=original,
                              clarification=ScenarioClarification(original_command=original, reason_code=reason,
                                                                  question=_question(reason, missing),
                                                                  missing_fields=missing, choices=list(choices)))

    if slots["distance"] is not None and not 0 < slots["distance"] <= MAX_DISTANCE_M:
        return clarify("distance_out_of_range", ["distance"])
    if slots["age_band"] is not None and slots["age_band"] not in ALLOWED_BANDS:
        return clarify("age_band_unsupported", ["age_band"], ALLOWED_BANDS)
    if slots["threshold"] is not None and slots["threshold"] < 1:
        return clarify("threshold_out_of_range", ["threshold"])
    if slots["place"] is not None and slots["place"].casefold() != catalogue.place.casefold():
        return clarify("place_not_covered", ["place"], [catalogue.place])
    missing = [name for name in SLOT_ORDER if slots[name] is None]
    if missing:
        return clarify("missing_parameters", missing)
    distance, band, threshold = slots["distance"], slots["age_band"], slots["threshold"]
    command = (f"find school coverage gaps farther than {distance} m where children aged {band} "
               f"are at least {threshold} in {catalogue.place}")
    intent = ScenarioIntent(scenario=SCENARIO, command=command, place=catalogue.place,
                            parameters={"distance_m": distance, "age_band": band, "threshold": threshold})
    return FrontendResult(status="plan", grammar_version=GRAMMAR_VERSION, command=original, intent=intent)


def interpret_m12a(command: str, catalogue: ScenarioCatalogue) -> FrontendResult:
    text = _normalise(command)
    match = _COMMAND.fullmatch(text)
    if match is None:
        return FrontendResult(status="unsupported", grammar_version=GRAMMAR_VERSION, command=text,
                              reason_code="command_not_recognized")
    return _decide(text, _slots(match), catalogue)


def resume_m12a(command: str, clarification: ScenarioClarification, answer: str,
                catalogue: ScenarioCatalogue) -> FrontendResult:
    def invalid(message: str) -> ScenarioError:
        return ScenarioError("SCENARIO_CLARIFICATION_INVALID", message)

    text = _normalise(command)
    match = _COMMAND.fullmatch(text)
    if match is None or text != clarification.original_command:
        raise invalid("answer does not belong to this clarification")
    slots = _slots(match)
    answered: set[str] = set()
    items = [_normalise(item) for item in str(answer).split(";")]
    if not items or any(not item for item in items):
        raise invalid("answer must be ITEM (; ITEM)*")
    for item in items:
        slot = next((name for name, pattern in _ANSWERS.items() if pattern.fullmatch(item)), None)
        if slot is None or slot not in clarification.missing_fields or slot in answered:
            raise invalid(f"unexpected answer item: {item}")
        answered.add(slot)
        slots.update({key: value for key, value in _slots(_ANSWERS[slot].fullmatch(item)).items() if key == slot})
    return _decide(text, slots, catalogue)


def _role(catalogue: ScenarioCatalogue, semantic_type: str) -> str:
    roles = [role for role, entry in catalogue.entries.items() if entry.semantic_type == semantic_type]
    if len(roles) != 1:
        raise ScenarioError("SCENARIO_DATASET_UNAVAILABLE", f"catalogue needs exactly one {semantic_type} dataset")
    return roles[0]


def band_field(band: str) -> str:
    low, high = band.split("-")
    return f"population_age_{low}_{high}"


def plan_m12a(intent: ScenarioIntent, catalogue: ScenarioCatalogue) -> ScenarioPlan:
    population_role = _role(catalogue, "scb_population_grid_raw")
    poi_role = _role(catalogue, "poi_points")
    distance, band, threshold = (intent.parameters[key] for key in ("distance_m", "age_band", "threshold"))
    steps: list[dict[str, Any]] = [dict(
        tool="load_population_dataset", input_roles=[population_role], output_role="population_grid",
        parameters={"provider": "scb", "dataset_year": 2025}, empty_policy="reject",
        rationale="Standardise the raw SCB 1 km grid (M05 field mapping and value rules).")]
    poi_source = poi_role
    if catalogue.entries[poi_role].declared_crs != catalogue.analysis_crs:
        steps.append(dict(
            tool="reproject_dataset", input_roles=[poi_role], output_role="poi_3006",
            parameters={"target_crs": catalogue.analysis_crs}, empty_policy="reject",
            rationale=f"POI source CRS {catalogue.entries[poi_role].declared_crs} is recorded by the catalogue; "
                      f"project explicitly to {catalogue.analysis_crs} for metric distances."))
        poi_source = "poi_3006"
    steps += [
        dict(tool="filter_attributes", input_roles=[poi_source], output_role="schools",
             parameters={"conditions": [{"field": "amenity", "operator": "eq", "value": "school"}], "logic": "all"},
             empty_policy="reject", rationale="Keep OSM amenity=school points; an empty school layer is an error."),
        dict(tool="buffer_features", input_roles=["schools"], output_role="school_service_areas",
             parameters={"distance_m": distance}, empty_policy="reject",
             rationale="Straight-line buffer for the map only (not a walking area)."),
        dict(tool="nearest_features", input_roles=["population_grid", "schools"], output_role="grid_nearest_school",
             parameters={"k": 1}, empty_policy="reject",
             rationale="Distance from each grid-cell polygon to its nearest school point."),
        dict(tool="filter_attributes", input_roles=["grid_nearest_school"], output_role="coverage_gaps",
             parameters={"conditions": [
                 {"field": "distance_m", "operator": "gt", "value": distance},
                 {"field": f"left_{band_field(band)}", "operator": "gte", "value": threshold}], "logic": "all"},
             empty_policy="allow", rationale="Whole cell beyond the distance and enough children: a coverage gap."),
    ]
    producers: dict[str, str] = {}
    typed = []
    for index, item in enumerate(steps, 1):
        step_id = f"step_{index}"
        depends = [producers[role] for role in item["input_roles"] if role in producers]
        typed.append(ScenarioStep(step_id=step_id, depends_on=depends, **item))
        producers[item["output_role"]] = step_id
    plan = ScenarioPlan(
        plan_version=PLAN_VERSION, scenario=SCENARIO, intent=intent, steps=typed,
        preview=[f"{s.step_id}: {s.tool}({', '.join(s.input_roles)}) -> {s.output_role}. {s.rationale}" for s in typed],
    )
    validate_plan(plan, catalogue)
    return plan


# ---------------------------------------------------------------------- report

def build_m12a_report(intent: ScenarioIntent, outcome: ScenarioOutcome, catalogue: ScenarioCatalogue,
                      registry: DatasetRegistry, *, clarifications: Iterable[ClarificationTurn] = ()) -> ScenarioReport:
    if outcome.status != "success" or outcome.final_dataset is None:
        raise ScenarioError("SCENARIO_RESULT_UNVERIFIED", "only successful scenario outcomes are reported")
    out = outcome.outputs
    grid, schools, areas, gaps = (out[key] for key in ("population_grid", "schools", "school_service_areas", "coverage_gaps"))
    if gaps.id != outcome.final_dataset.id:
        raise ScenarioError("SCENARIO_RESULT_UNVERIFIED", "final dataset differs from the coverage-gap output")
    params = intent.parameters
    field = band_field(params["age_band"])
    low, high = (int(part) for part in params["age_band"].split("-"))
    parameters = {"distance_threshold_m": params["distance_m"], "children_threshold": params["threshold"],
                  "age_band_min": low, "age_band_max": high}
    grid_records = list(registry.read(grid.id).records)
    gap_records = list(registry.read(gaps.id).records)
    gap_children = field_sum(gap_records, f"left_{field}")
    total_children = field_sum(grid_records, field)
    metrics = [
        metric("gap_cell_count", "Coverage gap cells", gaps.row_count, "cells", "dataset_row_count", [gaps.id], "row_count"),
        metric("gap_children", "Children in gap cells", gap_children, "people", "dataset_field_sum", [gaps.id], f"left_{field}"),
        metric("total_children", "Children in returned cells", total_children, "people", "dataset_field_sum", [grid.id], field),
    ]
    if total_children:
        metrics.append(metric("gap_children_share", "Share of children in gap cells", gap_children / total_children,
                              "fraction", "ratio", ["gap_children", "total_children"], "gap_children/total_children"))
    metrics += [
        metric("school_count", "Extracted school locations", schools.row_count, "points", "dataset_row_count", [schools.id], "row_count"),
        metric("grid_cell_count", "Returned SCB grid cells", grid.row_count, "cells", "dataset_row_count", [grid.id], "row_count"),
        metric("band_not_reported_cells", "Cells without a reported band value", field_null_count(grid_records, field),
               "cells", "dataset_field_null_count", [grid.id], field),
        metric("distance_threshold_m", "Distance threshold", parameters["distance_threshold_m"], "m", "plan_parameter", [], "distance_threshold_m"),
        metric("children_threshold", "Minimum children per cell", parameters["children_threshold"], "people", "plan_parameter", [], "children_threshold"),
        metric("age_band_min", "Age band from", low, "years", "plan_parameter", [], "age_band_min"),
        metric("age_band_max", "Age band to", high, "years", "plan_parameter", [], "age_band_max"),
    ]
    if gaps.row_count:
        headline = ("{gap_cell_count} populated grid cells lie entirely farther than {distance_threshold_m} m from the nearest "
                    "school and have at least {children_threshold} children aged {age_band_min} to {age_band_max}.")
    else:
        headline = ("No populated grid cell lies entirely farther than {distance_threshold_m} m from the nearest school with "
                    "at least {children_threshold} children aged {age_band_min} to {age_band_max}.")
    templates = []
    if total_children:
        templates.append("These cells hold {gap_children} of {total_children} reported children in the band, "
                         "a share of {gap_children_share}.")
    templates += [
        "The search used {school_count} school locations from OpenStreetMap inside the study rectangle plus a "
        "three-kilometre margin.",
        "Population and child counts are sums over the {grid_cell_count} one-kilometre SCB cells returned for the study "
        "rectangle, including edge cells that intersect or touch its boundary, unclipped; they are not the exact "
        "population of the rectangle.",
        "{band_not_reported_cells} cells have no reported value for the requested age band and cannot qualify.",
    ]
    roles = {ref.id: role for role, ref in outcome.bindings.items()}
    roles.update({ref.id: role for role, ref in outcome.outputs.items()})
    all_refs = {ref.id: ref for ref in [*outcome.bindings.values(), *outcome.outputs.values()]}
    entries = [catalogue.entries[role] for role in outcome.bindings if role in catalogue.entries]
    warnings = sorted({warning for record in outcome.step_records for warning in record.warnings})
    report = ScenarioReport(
        scenario=SCENARIO, title="School coverage gaps", place=intent.place, crs=gaps.crs, command=intent.command,
        grammar_note=GRAMMAR_NOTE, parameters=parameters, clarifications=list(clarifications),
        plan_preview=list(outcome.plan.preview), headline_template=headline, report_templates=templates,
        metrics=metrics,
        layers=[
            layer(registry, grid, "SCB 1 km grid cells returned for the study rectangle", 1,
                  ["grid_id", "population_total", field]),
            layer(registry, schools, "Schools (OpenStreetMap)", 2, ["name", "poi_id"]),
            layer(registry, areas, "Straight-line school buffers (not walking areas)", 3, ["name"]),
            layer(registry, gaps, "Coverage gap cells", 4, ["left_grid_id", f"left_{field}", "distance_m", "right_name"]),
        ],
        chart=ScenarioChart(title="Children in the requested age band", metric_ids=["gap_children", "total_children"]),
        table=table(registry, gaps, "Coverage gap cells", [
            ("grid_id", "left_grid_id"), ("children_in_band", f"left_{field}"),
            ("population_total", "left_population_total"),
            ("distance_to_nearest_extracted_school_m", "distance_m"), ("nearest_school", "right_name")],
            sort_field="left_grid_id",
            derived=[("distance_is_upper_bound", lambda props: "yes" if props.get("distance_m", 0) > MAX_DISTANCE_M else "no")]),
        sources=[source(all_refs[item], roles[item]) for item in sorted(all_refs)],
        data_licenses=[DataLicense(role=entry.role, source=entry.dataset.source, license=entry.license,
                                   attribution=entry.attribution, provider_verified=entry.provider_verified,
                                   temporal_note=entry.temporal_note) for entry in entries],
        steps=[StepEvidence(step_id=r.step_id, tool=r.tool, input_ids=r.input_ids, output_id=r.output_id,
                            attempts=r.attempts) for r in outcome.step_records if r.status == "succeeded"],
        recoveries=list(outcome.recoveries), warnings=warnings,
        assumptions=[
            "Distances are straight-line distances in EPSG:3006 from each grid-cell polygon to the nearest extracted school point.",
            "A cell is a gap only if the whole cell lies beyond the distance threshold from every extracted school.",
            "The requested SCB age band is a proxy for school-age children; cells without a reported value cannot qualify.",
        ],
        limitations=[
            "Population sums cover the SCB cells returned by the WFS BBOX filter (cells intersecting or touching the 20x20 km "
            "study rectangle, unclipped); they are not the exact population of that rectangle.",
            "Distances above 2000 m are upper bounds: schools were extracted only up to 3 km beyond the study rectangle and "
            "edge cells extend up to 1 km beyond it.",
            "Straight-line distance ignores the street network and is not a walking or transport measure.",
            "OpenStreetMap completeness, school type and capacity are unknown; node and area features of one school are not merged.",
            *[f"{entry.role}: {entry.temporal_note}" for entry in entries],
            "Projected GeoJSON carries geoai_crs EPSG:3006; reproject to EPSG:4326 for RFC 7946 consumers.",
        ],
        downloads=downloads(registry, gaps, "coverage_gaps"),
    )
    verify_metric_evidence(report, registry)
    return report


def validate_m12a_report(report: ScenarioReport, intent: ScenarioIntent, outcome: ScenarioOutcome,
                         catalogue: ScenarioCatalogue, registry: DatasetRegistry, *,
                         clarifications: Iterable[ClarificationTurn] = ()) -> None:
    """Canonical rebuild from the verified outcome; any difference is an evidence mismatch."""
    canonical = build_m12a_report(intent, outcome, catalogue, registry, clarifications=clarifications)
    if report != canonical:
        raise ScenarioError("SCENARIO_REPORT_EVIDENCE_MISMATCH", "report differs from verified evidence")
    verify_metric_evidence(report, registry)
