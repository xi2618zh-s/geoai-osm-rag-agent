"""M07 constrained planning preview over registered deterministic analysis tools.

This module never executes tools. The narrow command grammar makes unsupported or
ambiguous business requests explicit rather than inventing parameters.
"""
from __future__ import annotations

import json
import re
from typing import Any, Callable, Literal, Optional

from pydantic import ConfigDict, Field, StrictStr, ValidationError, model_validator
from pyproj import CRS
from pyproj.exceptions import CRSError

from src.geodsl import (
    AttributeFilter, ClarificationRequest, DatasetRequirement, Distance,
    GeoAnalysisPlan, GeoAnalysisStep, GeoTaskIntent, MetricDefinition,
    OutputRequirement, PlaceReference, SpatialPredicate,
)
from src.tools import ANALYSIS_TOOL_REGISTRY
from src.contracts import StrictContract

SCHEMA_VERSION = "1.0"
MAX_STEPS = 8
MAX_RETRIES_PER_STEP = 1
MAX_BUDGET = 12
PUBLIC_TOOLS = frozenset({
    "filter_attributes", "buffer_features", "nearest_features",
    "features_within", "spatial_join", "aggregate_by_region",
})
TOOL_COST = {
    "filter_attributes": 1, "buffer_features": 2,
    "nearest_features": 3, "features_within": 2,
    "spatial_join": 3, "aggregate_by_region": 3,
}
ROLE = r"[a-z][a-z0-9_]*"
FIELD = r"[A-Za-z_][A-Za-z0-9_]*"
PLACE = r"[A-Za-z][A-Za-z -]*"
DANGER = re.compile(r"[/\\]|[A-Za-z]:|\.\.|\b(?:import|exec|eval|select|drop|python|sql)\b", re.I)
# m07.2 (ADR-0018): additive productions for previously unsupported inputs only.
GRAMMAR_VERSION = "m07.1"
EXTENDED_GRAMMAR_VERSION = "m07.2"
MAX_CONDITIONS = 8
VALUE = r"-?[0-9]+(?:\.[0-9]+)?|[A-Za-z_][A-Za-z0-9_]*"
COMPARATOR = r"=|!=|>=|<=|>|<"
COND = rf"{FIELD} (?:{COMPARATOR}) (?:{VALUE})"
CONDS = rf"{COND}(?: and {COND})*"
OPERATORS = {"=": "eq", "!=": "ne", ">": "gt", ">=": "gte", "<": "lt", "<=": "lte"}
EXTENDED_OPERATIONS = {
    "conditional_search": "search", "filtered_nearest": "nearest",
    "filtered_within": "within", "filtered_spatial_join": "spatial_join",
    "filtered_aggregate_by_region": "aggregate_by_region",
}


class PlanError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class M07Contract(StrictContract):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, strict=True)


class DatasetDescriptor(M07Contract):
    kind: Literal["vector", "table", "raster", "network"]
    source: StrictStr = Field(pattern=r"^[a-z][a-z0-9_-]*:[A-Za-z0-9_.:-]{1,100}$")
    crs: StrictStr = Field(pattern=r"^EPSG:[1-9][0-9]{2,5}$")
    required_fields: list[StrictStr] = Field(default_factory=list)


class M07PlanRequest(M07Contract):
    schema_version: Literal["1.0"] = "1.0"
    query: StrictStr = Field(min_length=1, max_length=2000)
    datasets: dict[StrictStr, DatasetDescriptor] = Field(min_length=1, max_length=16)
    place_crs: StrictStr = Field(pattern=r"^EPSG:[1-9][0-9]{2,5}$")
    analysis_crs: StrictStr = Field(pattern=r"^EPSG:[1-9][0-9]{2,5}$")
    output_crs: StrictStr = Field(pattern=r"^EPSG:[1-9][0-9]{2,5}$")

    @model_validator(mode="after")
    def validate_roles(self):
        for role in self.datasets:
            if re.fullmatch(ROLE, role) is None:
                raise ValueError(f"invalid dataset role: {role}")
        return self


class PlannedToolStep(M07Contract):
    step_id: StrictStr = Field(pattern=r"^step_[1-8]$")
    tool: StrictStr = Field(pattern=r"^[a-z][a-z0-9_]*$")
    depends_on: list[StrictStr] = Field(default_factory=list)
    input_roles: list[StrictStr] = Field(min_length=1, max_length=4)
    output_role: StrictStr = Field(pattern=r"^[a-z][a-z0-9_]*$")
    parameters: dict[StrictStr, Any] = Field(default_factory=dict)
    max_retries: int = Field(default=0, ge=0)


class PlanningResult(M07Contract):
    schema_version: Literal["1.0"] = "1.0"
    status: Literal["plan", "clarification", "unsupported"]
    task_class: StrictStr
    confidence: float = Field(ge=0, le=1)
    evidence: list[StrictStr]
    business_parameters: dict[StrictStr, Any] = Field(default_factory=dict)
    intent: Optional[GeoTaskIntent] = None
    plan: Optional[GeoAnalysisPlan] = None
    tool_steps: list[PlannedToolStep] = Field(default_factory=list)
    plan_preview: list[StrictStr] = Field(default_factory=list)
    clarification: Optional[ClarificationRequest] = None
    error_code: Optional[StrictStr] = None

    @model_validator(mode="after")
    def status_consistency(self):
        if self.status == "plan" and (self.intent is None or self.plan is None or not self.tool_steps):
            raise ValueError("plan status requires intent, plan and tool steps")
        if self.status == "clarification" and self.clarification is None:
            raise ValueError("clarification status requires ClarificationRequest")
        if self.status == "unsupported" and self.error_code is None:
            raise ValueError("unsupported status requires error_code")
        return self


def _match(pattern: str, text: str):
    return re.fullmatch(pattern, text, re.I)


def _clarify(query: str, task_class: str, reason: str, field: str, question: str) -> PlanningResult:
    return PlanningResult(
        status="clarification", task_class=task_class, confidence=0.0,
        evidence=[f"query:{query}"],
        clarification=ClarificationRequest(
            original_query=query, reason_code=reason,
            question=question, missing_fields=[field],
        ),
    )


def _unsupported(task_class: str, code: str, query: str) -> PlanningResult:
    return PlanningResult(
        status="unsupported", task_class=task_class, confidence=0.0,
        evidence=[f"query:{query}"], error_code=code,
    )


def _parse(query: str) -> tuple[str, str, dict[str, Any]] | PlanningResult:
    text = query.strip()
    if _match(rf"find {ROLE} within [0-9]+(?:\.[0-9]+)? (?:m|km) and [0-9]+(?:\.[0-9]+)? (?:m|km) of {ROLE} in {PLACE}", text):
        return _clarify(text, "within_distance", "conflicting_constraints", "distance", "Which distance should be used?")
    if _match(rf"find {ROLE} within of {ROLE} in {PLACE}", text):
        return _clarify(text, "within_distance", "missing_distance", "distance", "What distance and unit should be used?")
    patterns = [
        ("filtered_search", rf"search (?P<role>{ROLE}) where (?P<field>{FIELD}) = (?P<value>{FIELD}) in (?P<place>{PLACE})"),
        ("search", rf"search (?P<role>{ROLE}) in (?P<place>{PLACE})"),
        ("within_distance", rf"find (?P<target>{ROLE}) within (?P<distance>[0-9]+(?:\.[0-9]+)?) (?P<unit>m|km) of (?P<anchor>{ROLE}) excluding (?P<exclude_field>{FIELD}) = (?P<exclude_value>{FIELD}) in (?P<place>{PLACE})"),
        ("within_distance", rf"find (?P<target>{ROLE}) within (?P<distance>[0-9]+(?:\.[0-9]+)?) (?P<unit>m|km) of (?P<anchor>{ROLE}) in (?P<place>{PLACE})"),
        ("nearest", rf"find (?P<k>[1-9][0-9]*) nearest (?P<target>{ROLE}) to (?P<source>{ROLE}) in (?P<place>{PLACE})"),
        ("within", rf"find (?P<source>{ROLE}) within (?P<container>{ROLE}) in (?P<place>{PLACE})"),
        ("spatial_join", rf"join (?P<left>{ROLE}) with (?P<right>{ROLE}) on (?P<predicate>intersects|within|contains) in (?P<place>{PLACE})"),
        ("aggregate_by_region", rf"aggregate (?P<operation>count|sum|mean|min|max)(?: (?P<field>{FIELD}))? by (?P<region_id_field>{FIELD}) from (?P<values>{ROLE}) into (?P<regions>{ROLE}) using (?P<values_region_id_field>{FIELD}) in (?P<place>{PLACE})"),
        ("rank", rf"rank (?P<role>{ROLE}) by (?P<field>{FIELD}) (?P<direction>asc|desc) in (?P<place>{PLACE})"),
        # m07.2 productions are tried only after every m07.1 production failed.
        ("conditional_search", rf"search (?P<role>{ROLE}) where (?P<conditions>{CONDS}) in (?P<place>{PLACE})"),
        ("filtered_nearest", rf"find (?P<k>[1-9][0-9]*) nearest (?P<target>{ROLE}) to (?P<source>{ROLE}) where (?P<conditions>{CONDS}) in (?P<place>{PLACE})"),
        ("filtered_within", rf"find (?P<source>{ROLE}) where (?P<conditions>{CONDS}) within (?P<container>{ROLE}) in (?P<place>{PLACE})"),
        ("filtered_spatial_join", rf"join (?P<left>{ROLE}) with (?P<right>{ROLE}) where (?P<conditions>{CONDS}) on (?P<predicate>intersects|within|contains) in (?P<place>{PLACE})"),
        ("filtered_aggregate_by_region", rf"aggregate (?P<operation>count|sum|mean|min|max)(?: (?P<field>{FIELD}))? by (?P<region_id_field>{FIELD}) from (?P<values>{ROLE}) where (?P<conditions>{CONDS}) into (?P<regions>{ROLE}) using (?P<values_region_id_field>{FIELD}) in (?P<place>{PLACE})"),
    ]
    for task_class, pattern in patterns:
        found = _match(pattern, text)
        if found:
            params = {key: value for key, value in found.groupdict().items() if value is not None}
            return task_class, params.pop("place"), params
    if re.match(r"^(?:search|find|join|aggregate|rank)\b", text, re.I) and not re.search(r"\bin\s+" + PLACE + r"$", text, re.I):
        return _clarify(text, "unknown", "missing_place", "place", "Which place should be used?")
    return _unsupported("unknown", "M07_UNSUPPORTED_TASK", text)


def _require_role(request: M07PlanRequest, role: str) -> DatasetRequirement:
    descriptor = request.datasets.get(role)
    if descriptor is None:
        raise PlanError("M07_DATASET_UNKNOWN", f"Unknown dataset role: {role}")
    if descriptor.kind != "vector":
        raise PlanError("M07_DATASET_KIND_UNSUPPORTED", f"{role} is not a vector dataset")
    return DatasetRequirement(
        role=role, kind=descriptor.kind, source=descriptor.source,
        crs=descriptor.crs, required_fields=descriptor.required_fields,
    )


def _require_field(request: M07PlanRequest, role: str, field: str) -> None:
    if field not in request.datasets[role].required_fields:
        raise PlanError("M07_FIELD_UNKNOWN", f"{role}.{field} is not declared")


def _require_crs(request: M07PlanRequest, roles: list[str], *, projected: bool) -> None:
    try:
        CRS.from_user_input(request.place_crs)
        analysis = CRS.from_user_input(request.analysis_crs)
        CRS.from_user_input(request.output_crs)
        for role in roles:
            CRS.from_user_input(request.datasets[role].crs)
    except CRSError as error:
        raise PlanError("M07_CRS_INCOMPATIBLE", "Unknown or invalid declared CRS") from error
    for role in roles:
        if request.datasets[role].crs != request.analysis_crs:
            raise PlanError("M07_CRS_INCOMPATIBLE", f"{role} CRS differs from analysis CRS; explicit reprojection required")
    if request.output_crs != request.analysis_crs:
        raise PlanError("M07_CRS_INCOMPATIBLE", "Output CRS needs an explicit registered reprojection step")
    if projected and not analysis.is_projected:
        raise PlanError("M07_CRS_INCOMPATIBLE", "Distance operations require a declared projected CRS")


def _step(index: int, tool: str, inputs: list[str], output: str, params: dict[str, Any], deps: list[str] | None = None) -> PlannedToolStep:
    return PlannedToolStep(
        step_id=f"step_{index}", tool=tool, depends_on=deps or [],
        input_roles=inputs, output_role=output, parameters=params,
    )


def _conditions(request: M07PlanRequest, role: str, raw: str) -> list[dict[str, Any]]:
    """Parse an m07.2 `FIELD OP VALUE (and ...)` clause against declared fields."""
    _require_role(request, role)
    parts = re.findall(rf"({FIELD}) ({COMPARATOR}) ({VALUE})", raw)
    rebuilt = " and ".join(" ".join(part) for part in parts)
    if not parts or rebuilt.casefold() != raw.casefold():
        raise PlanError("M07_PARAMETER_INVALID", "Condition clause is not an exact conjunction")
    if len(parts) > MAX_CONDITIONS:
        raise PlanError("M07_PARAMETER_INVALID", "Too many filter conditions")
    conditions = []
    for field, symbol, text in parts:
        _require_field(request, role, field)
        if re.fullmatch(r"-?[0-9]+", text):
            value: Any = int(text)
        elif re.fullmatch(r"-?[0-9]+\.[0-9]+", text):
            value = float(text)
        else:
            value = text
        conditions.append({"field": field, "operator": OPERATORS[symbol], "value": value})
    return conditions


def _attribute_filters(role: str, conditions: list[dict[str, Any]]) -> list[AttributeFilter]:
    return [AttributeFilter(dataset_role=role, **item) for item in conditions]


def _filter_step(role: str, output: str, conditions: list[dict[str, Any]]) -> PlannedToolStep:
    return _step(1, "filter_attributes", [role], output,
                 {"conditions": [dict(item) for item in conditions], "logic": "all"})


def _build(request: M07PlanRequest, task_class: str, place: str, parsed: dict[str, Any]) -> PlanningResult:
    if task_class == "rank":
        return _unsupported(task_class, "M07_UNSUPPORTED_TASK", request.query)
    roles: list[str]
    steps: list[PlannedToolStep]
    filters: list[AttributeFilter] = []
    predicates: list[SpatialPredicate] = []
    metrics: list[MetricDefinition] = []
    business: dict[str, Any]
    operation = EXTENDED_OPERATIONS.get(task_class, "search" if task_class == "filtered_search" else task_class)
    if task_class in {"search", "filtered_search"}:
        role = parsed["role"]
        roles = [role]
        business = {"role": role}
        conditions: list[dict[str, Any]] = []
        if task_class == "filtered_search":
            _require_role(request, role)
            _require_field(request, role, parsed["field"])
            filters = [AttributeFilter(dataset_role=role, field=parsed["field"], operator="eq", value=parsed["value"])]
            conditions = [{"field": parsed["field"], "operator": "eq", "value": parsed["value"]}]
            business.update(field=parsed["field"], operator="eq", value=parsed["value"])
        steps = [_step(1, "filter_attributes", roles, "result", {"conditions": conditions, "logic": "all"})]
    elif task_class == "within_distance":
        target, anchor = parsed["target"], parsed["anchor"]
        distance = float(parsed["distance"]) * (1000 if parsed["unit"].lower() == "km" else 1)
        if distance <= 0:
            raise PlanError("M07_PARAMETER_INVALID", "Distance must be positive")
        roles = [target, anchor]
        business = {"target": target, "anchor": anchor, "distance_m": int(distance) if distance.is_integer() else distance}
        predicates = [SpatialPredicate(relation="within_distance", left_dataset=target, right_dataset=anchor, distance=Distance(value=float(parsed["distance"]), unit=parsed["unit"].lower()))]
        steps = [
            _step(1, "buffer_features", [anchor], "buffered_anchor", {"distance_m": distance}),
            _step(2, "spatial_join", [target, "buffered_anchor"], "result", {"predicate": "intersects"}, ["step_1"]),
        ]
        if "exclude_field" in parsed:
            _require_role(request, target)
            _require_field(request, target, parsed["exclude_field"])
            filters = [AttributeFilter(dataset_role=target, field=parsed["exclude_field"], operator="ne", value=parsed["exclude_value"])]
            business.update(exclude_field=parsed["exclude_field"], exclude_value=parsed["exclude_value"])
            steps.append(_step(3, "filter_attributes", ["result"], "filtered_result", {"conditions": [{"field": "left_" + parsed["exclude_field"], "operator": "ne", "value": parsed["exclude_value"]}], "logic": "all"}, ["step_2"]))
    elif task_class == "nearest":
        source, target, k = parsed["source"], parsed["target"], int(parsed["k"])
        if k > 1000:
            raise PlanError("M07_PARAMETER_INVALID", "k exceeds tool limit")
        roles = [source, target]
        business = {"source": source, "target": target, "k": k}
        predicates = [SpatialPredicate(relation="nearest", left_dataset=source, right_dataset=target, max_results=k)]
        steps = [_step(1, "nearest_features", roles, "result", {"k": k})]
    elif task_class == "within":
        source, container = parsed["source"], parsed["container"]
        roles = [source, container]
        business = {"source": source, "container": container}
        predicates = [SpatialPredicate(relation="within", left_dataset=source, right_dataset=container)]
        steps = [_step(1, "features_within", roles, "result", {})]
    elif task_class == "spatial_join":
        left, right, predicate = parsed["left"], parsed["right"], parsed["predicate"].lower()
        roles = [left, right]
        business = {"left": left, "right": right, "predicate": predicate}
        predicates = [SpatialPredicate(relation=predicate, left_dataset=left, right_dataset=right)]
        steps = [_step(1, "spatial_join", roles, "result", {"predicate": predicate})]
    elif task_class == "aggregate_by_region":
        regions, values = parsed["regions"], parsed["values"]
        op = parsed["operation"].lower()
        field = parsed.get("field")
        if op != "count" and field is None:
            return _clarify(request.query, task_class, "ambiguous_target", "field", "Which numeric field should be aggregated?")
        if op == "count" and field is not None:
            raise PlanError("M07_PARAMETER_CONFLICT", "count does not accept a value field")
        roles = [regions, values]
        _require_role(request, regions)
        _require_role(request, values)
        _require_field(request, regions, parsed["region_id_field"])
        _require_field(request, values, parsed["values_region_id_field"])
        if field is not None:
            _require_field(request, values, field)
        business = {
            "regions": regions, "values": values,
            "region_id_field": parsed["region_id_field"],
            "values_region_id_field": parsed["values_region_id_field"],
            "operation": op,
        }
        if field is not None:
            business["field"] = field
        metrics = [MetricDefinition(name="metric", operation=op, dataset_role=values, field=field)]
        steps = [_step(1, "aggregate_by_region", roles, "result", {
            "region_id_field": parsed["region_id_field"],
            "values_region_id_field": parsed["values_region_id_field"],
            "metrics": [{"operation": op, "field": field}],
        })]
    elif task_class == "conditional_search":
        role = parsed["role"]
        roles = [role]
        conditions = _conditions(request, role, parsed["conditions"])
        filters = _attribute_filters(role, conditions)
        business = {"role": role, "conditions": conditions}
        steps = [_filter_step(role, "result", conditions)]
    elif task_class == "filtered_nearest":
        source, target, k = parsed["source"], parsed["target"], int(parsed["k"])
        if k > 1000:
            raise PlanError("M07_PARAMETER_INVALID", "k exceeds tool limit")
        conditions = _conditions(request, source, parsed["conditions"])
        roles = [source, target]
        filters = _attribute_filters(source, conditions)
        business = {"source": source, "target": target, "k": k,
                    "filter_role": source, "conditions": conditions}
        predicates = [SpatialPredicate(relation="nearest", left_dataset=source, right_dataset=target, max_results=k)]
        steps = [
            _filter_step(source, "filtered_" + source, conditions),
            _step(2, "nearest_features", ["filtered_" + source, target], "result", {"k": k}, ["step_1"]),
        ]
    elif task_class == "filtered_within":
        source, container = parsed["source"], parsed["container"]
        conditions = _conditions(request, source, parsed["conditions"])
        roles = [source, container]
        filters = _attribute_filters(source, conditions)
        business = {"source": source, "container": container,
                    "filter_role": source, "conditions": conditions}
        predicates = [SpatialPredicate(relation="within", left_dataset=source, right_dataset=container)]
        steps = [
            _filter_step(source, "filtered_" + source, conditions),
            _step(2, "features_within", ["filtered_" + source, container], "result", {}, ["step_1"]),
        ]
    elif task_class == "filtered_spatial_join":
        left, right, predicate = parsed["left"], parsed["right"], parsed["predicate"].lower()
        conditions = _conditions(request, right, parsed["conditions"])
        roles = [left, right]
        filters = _attribute_filters(right, conditions)
        business = {"left": left, "right": right, "predicate": predicate,
                    "filter_role": right, "conditions": conditions}
        predicates = [SpatialPredicate(relation=predicate, left_dataset=left, right_dataset=right)]
        steps = [
            _filter_step(right, "filtered_" + right, conditions),
            _step(2, "spatial_join", [left, "filtered_" + right], "result", {"predicate": predicate}, ["step_1"]),
        ]
    elif task_class == "filtered_aggregate_by_region":
        regions, values = parsed["regions"], parsed["values"]
        op = parsed["operation"].lower()
        field = parsed.get("field")
        if op != "count" and field is None:
            return _clarify(request.query, task_class, "ambiguous_target", "field", "Which numeric field should be aggregated?")
        if op == "count" and field is not None:
            raise PlanError("M07_PARAMETER_CONFLICT", "count does not accept a value field")
        roles = [regions, values]
        _require_role(request, regions)
        _require_role(request, values)
        _require_field(request, regions, parsed["region_id_field"])
        _require_field(request, values, parsed["values_region_id_field"])
        if field is not None:
            _require_field(request, values, field)
        conditions = _conditions(request, values, parsed["conditions"])
        filters = _attribute_filters(values, conditions)
        business = {
            "regions": regions, "values": values,
            "region_id_field": parsed["region_id_field"],
            "values_region_id_field": parsed["values_region_id_field"],
            "operation": op, "filter_role": values, "conditions": conditions,
        }
        if field is not None:
            business["field"] = field
        metrics = [MetricDefinition(name="metric", operation=op, dataset_role=values, field=field)]
        steps = [
            _filter_step(values, "filtered_" + values, conditions),
            _step(2, "aggregate_by_region", [regions, "filtered_" + values], "result", {
                "region_id_field": parsed["region_id_field"],
                "values_region_id_field": parsed["values_region_id_field"],
                "metrics": [{"operation": op, "field": field}],
            }, ["step_1"]),
        ]
    else:
        return _unsupported(task_class, "M07_UNSUPPORTED_TASK", request.query)
    if len(roles) != len(set(roles)):
        raise PlanError("M07_PARAMETER_CONFLICT", "Two-input operation requires distinct dataset roles")
    datasets = [_require_role(request, role) for role in roles]
    _require_crs(request, roles, projected=task_class in {"within_distance", "nearest", "filtered_nearest"})
    intent = GeoTaskIntent(
        task_type=operation, query=request.query, place=PlaceReference(name=place, crs=request.place_crs),
        analysis_crs=request.analysis_crs, datasets=datasets, attribute_filters=filters,
        spatial_predicates=predicates, metrics=metrics,
        output=OutputRequirement(formats=["summary"], crs=request.output_crs),
    )
    semantic = GeoAnalysisStep(
        step_id="analysis", operation=operation, input_datasets=roles,
        output_dataset="result", attribute_filters=filters,
        spatial_predicate=predicates[0] if predicates else None, metrics=metrics,
    )
    plan = GeoAnalysisPlan(
        plan_id="m07_plan", source="planner",
        source_version=EXTENDED_GRAMMAR_VERSION if task_class in EXTENDED_OPERATIONS else GRAMMAR_VERSION,
        intent=intent, steps=[semantic],
    )
    result = PlanningResult(
        status="plan", task_class=task_class, confidence=1.0,
        evidence=[f"query:{request.query}", f"place:{place}"] +
                 [f"dataset:{r}@{request.datasets[r].source}" for r in roles] +
                 [f"tool:{s.tool}@{ANALYSIS_TOOL_REGISTRY.get(s.tool).spec.version}" for s in steps],
        business_parameters=business, intent=intent, plan=plan,
        tool_steps=steps,
        plan_preview=[f"{s.step_id}: {s.tool}({', '.join(s.input_roles)}) -> {s.output_role}" for s in steps],
    )
    _validate_steps(result, request)
    return result


def plan_request(payload: M07PlanRequest | dict[str, Any]) -> PlanningResult:
    request = payload if isinstance(payload, M07PlanRequest) else M07PlanRequest.model_validate(payload)
    parsed = _parse(request.query)
    if isinstance(parsed, PlanningResult):
        return parsed
    task_class, place, parameters = parsed
    try:
        return _build(request, task_class, place, parameters)
    except PlanError as error:
        return _unsupported(task_class, error.code, request.query)


def resume_clarification(payload: M07PlanRequest | dict[str, Any], clarification: ClarificationRequest, answer: str) -> PlanningResult:
    request = payload if isinstance(payload, M07PlanRequest) else M07PlanRequest.model_validate(payload)
    if not isinstance(answer, str) or clarification.original_query != request.query or not answer.strip():
        raise PlanError("M07_CLARIFICATION_INVALID", "Clarification does not match original query")
    if clarification.reason_code == "missing_place":
        if not re.fullmatch(PLACE, answer.strip()):
            raise PlanError("M07_CLARIFICATION_INVALID", "Invalid place answer")
        query = request.query.strip() + " in " + answer.strip()
    elif clarification.reason_code == "missing_distance":
        if not re.fullmatch(r"[0-9]+(?:\.[0-9]+)? (?:m|km)", answer.strip()):
            raise PlanError("M07_CLARIFICATION_INVALID", "Distance requires value and unit")
        query = re.sub(r"\bwithin of\b", "within " + answer.strip() + " of", request.query, count=1, flags=re.I)
    else:
        raise PlanError("M07_CLARIFICATION_INVALID", "This clarification reason requires a new explicit request")
    return plan_request(request.model_copy(update={"query": query}))


def _scan_danger(value: Any) -> bool:
    if isinstance(value, str):
        return bool(DANGER.search(value))
    if isinstance(value, list):
        return any(_scan_danger(x) for x in value)
    if isinstance(value, dict):
        return any(_scan_danger(k) or _scan_danger(v) for k, v in value.items())
    return False


def _valid_parameters(step: PlannedToolStep) -> bool:
    p = step.parameters
    if step.tool == "filter_attributes":
        return set(p) == {"conditions", "logic"} and p["logic"] in {"all", "any"} and isinstance(p["conditions"], list) and all(
            isinstance(c, dict) and set(c) == {"field", "operator", "value"} and
            isinstance(c["field"], str) and isinstance(c["operator"], str) and
            isinstance(c["value"], (str, int, float, bool)) for c in p["conditions"])
    if step.tool == "buffer_features":
        return set(p) == {"distance_m"} and type(p["distance_m"]) in {int, float} and p["distance_m"] > 0
    if step.tool == "nearest_features":
        return set(p) == {"k"} and type(p["k"]) is int and 1 <= p["k"] <= 1000
    if step.tool == "features_within":
        return not p
    if step.tool == "spatial_join":
        return set(p) == {"predicate"} and p["predicate"] in {"intersects", "within", "contains"}
    if step.tool == "aggregate_by_region":
        return set(p) == {"region_id_field", "values_region_id_field", "metrics"} and all(
            isinstance(p[k], str) and re.fullmatch(FIELD, p[k]) for k in ("region_id_field", "values_region_id_field")
        ) and isinstance(p["metrics"], list) and 1 <= len(p["metrics"]) <= 32 and all(
            isinstance(m, dict) and set(m) == {"operation", "field"} and
            m["operation"] in {"count", "sum", "mean", "min", "max"} and
            (m["field"] is None if m["operation"] == "count" else isinstance(m["field"], str))
            for m in p["metrics"])
    return False


def _validate_steps(result: PlanningResult, request: M07PlanRequest) -> None:
    steps = result.tool_steps
    if len(steps) > MAX_STEPS or any(s.max_retries > MAX_RETRIES_PER_STEP for s in steps):
        raise PlanError("M07_BUDGET_EXCEEDED", "Step or retry cap exceeded")
    if any(s.tool not in PUBLIC_TOOLS for s in steps):
        raise PlanError("M07_TOOL_FORBIDDEN", "Tool is not agent-public")
    if sum(TOOL_COST.get(s.tool, MAX_BUDGET + 1) for s in steps) > MAX_BUDGET:
        raise PlanError("M07_BUDGET_EXCEEDED", "Plan cost exceeds static budget")
    available = {r: request.datasets[r].kind for r in request.datasets}
    seen: set[str] = set()
    for index, step in enumerate(steps, 1):
        if step.tool not in PUBLIC_TOOLS:
            raise PlanError("M07_TOOL_FORBIDDEN", f"Tool is not agent-public: {step.tool}")
        try:
            spec = ANALYSIS_TOOL_REGISTRY.get(step.tool).spec
        except Exception as error:
            raise PlanError("M07_TOOL_FORBIDDEN", f"Tool is not registered: {step.tool}") from error
        if step.step_id != f"step_{index}" or any(dep not in seen for dep in step.depends_on):
            raise PlanError("M07_DEPENDENCY_INVALID", "Step dependency is cyclic, missing or future")
        if any(role not in available for role in step.input_roles) or step.output_role in available:
            raise PlanError("M07_DEPENDENCY_INVALID", "Input role unavailable or output role reused")
        for role in step.input_roles:
            if available[role] not in spec.supported_dataset_kinds:
                raise PlanError("M07_DATASET_KIND_UNSUPPORTED", "Tool and input kind incompatible")
        if _scan_danger(step.parameters) or not _valid_parameters(step):
            raise PlanError("M07_PARAMETER_INVALID", "Unsafe or invalid tool parameters")
        if step.tool in {"nearest_features", "features_within", "spatial_join", "aggregate_by_region"} and len(step.input_roles) != 2:
            raise PlanError("M07_PARAMETER_INVALID", "Tool requires two input roles")
        if step.tool in {"filter_attributes", "buffer_features"} and len(step.input_roles) != 1:
            raise PlanError("M07_PARAMETER_INVALID", "Tool requires one input role")
        producer_ids = {prior.step_id for prior in steps[:index-1] if prior.output_role in step.input_roles}
        if set(step.depends_on) != producer_ids:
            raise PlanError("M07_DEPENDENCY_INVALID", "Dependency closure does not match input lineage")
        seen.add(step.step_id)
        available[step.output_role] = "vector"


def _parse_model(raw: str) -> PlanningResult:
    try:
        value = json.loads(raw)
        if not isinstance(value, dict):
            raise ValueError("expected JSON object")
        steps = value.get("tool_steps", [])
        if isinstance(steps, list):
            if len(steps) > MAX_STEPS or any(isinstance(s, dict) and s.get("max_retries", 0) > MAX_RETRIES_PER_STEP for s in steps if isinstance(s, dict)):
                raise PlanError("M07_BUDGET_EXCEEDED", "Step or retry cap exceeded")
        return PlanningResult.model_validate(value)
    except PlanError:
        raise
    except (ValueError, TypeError, ValidationError) as error:
        raise PlanError("M07_MODEL_OUTPUT_INVALID", "Model output is not a strict M07 JSON plan") from error


def validate_model_plan_json(
    raw: str, payload: M07PlanRequest | dict[str, Any],
    *, repair: Optional[Callable[[str, str], str]] = None,
) -> PlanningResult:
    request = payload if isinstance(payload, M07PlanRequest) else M07PlanRequest.model_validate(payload)
    try:
        proposed = _parse_model(raw)
    except PlanError as error:
        if error.code != "M07_MODEL_OUTPUT_INVALID" or repair is None:
            raise
        try:
            repaired = repair(raw, str(error))
        except Exception as repair_error:
            raise PlanError("M07_MODEL_OUTPUT_INVALID", "Model structural repair failed") from repair_error
        proposed = _parse_model(repaired)
    expected = plan_request(request)
    if expected.status != "plan" or proposed.status != "plan":
        raise PlanError("M07_UNSUPPORTED_TASK", "Model plan cannot bypass unsupported or incomplete intent")
    _validate_steps(proposed, request)
    if proposed.intent != expected.intent or proposed.plan != expected.plan:
        raise PlanError("M07_PARAMETER_CONFLICT", "Model semantic intent differs from grounded request")
    if (proposed.tool_steps != expected.tool_steps or proposed.business_parameters != expected.business_parameters
            or proposed.task_class != expected.task_class or proposed.evidence != expected.evidence
            or proposed.plan_preview != expected.plan_preview):
        raise PlanError("M07_PARAMETER_CONFLICT", "Model proposal differs from grounded plan preview")
    return proposed



def plan_with_model(
    payload: M07PlanRequest | dict[str, Any],
    propose: Callable[[str], str],
    *,
    repair: Optional[Callable[[str, str], str]] = None,
) -> PlanningResult:
    """Call an injected JSON-only proposer once; malformed structure gets one repair."""
    request = payload if isinstance(payload, M07PlanRequest) else M07PlanRequest.model_validate(payload)
    grounded = plan_request(request)
    if grounded.status != "plan":
        return grounded
    tools = [
        {
            "name": name,
            "version": ANALYSIS_TOOL_REGISTRY.get(name).spec.version,
            "description": ANALYSIS_TOOL_REGISTRY.get(name).spec.description,
            "input_contract": ANALYSIS_TOOL_REGISTRY.get(name).spec.input_contract,
        }
        for name in sorted(PUBLIC_TOOLS)
    ]
    prompt = json.dumps(
        {
            "contract": "PlanningResult.v1.0",
            "instruction": "Return one strict JSON object only. Use the declared public tools and grounded intent. Never include executable Python, SQL or a local file path.",
            "limits": {"max_steps": MAX_STEPS, "max_retries_per_step": MAX_RETRIES_PER_STEP, "max_budget": MAX_BUDGET},
            "request": request.model_dump(mode="json"),
            "grounded_intent": grounded.intent.model_dump(mode="json"),
            "public_tools": tools,
            "json_schema": PlanningResult.model_json_schema(),
        },
        ensure_ascii=False, sort_keys=True,
    )
    try:
        raw = propose(prompt)
    except Exception as error:
        raise PlanError("M07_MODEL_CALL_FAILED", "Model proposer failed") from error
    if not isinstance(raw, str):
        raise PlanError("M07_MODEL_OUTPUT_INVALID", "Model proposer must return JSON text")
    return validate_model_plan_json(raw, request, repair=repair)
