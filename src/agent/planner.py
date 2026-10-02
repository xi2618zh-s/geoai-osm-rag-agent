"""Deterministic, evidence-backed compiler for constrained workflow plans."""

from __future__ import annotations

from functools import lru_cache
import re
from typing import Any

from src.agent.contracts import (
    FilterDecision,
    WorkflowIntent,
    WorkflowPlan,
    WorkflowStep,
)
from src.pipeline import simple_place_heuristic
from src.rag.hybrid_retriever import build_retriever


OR_RE = re.compile(r"\s+or\s+", re.IGNORECASE)
AND_RE = re.compile(r"\s+and\s+", re.IGNORECASE)


class PlanCompilationError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@lru_cache(maxsize=1)
def _retriever():
    return build_retriever()


def _without_terminal_place(query: str, place: str) -> str:
    pattern = re.compile(
        rf"\s+\b(?:in|at|near|around|within|from)\s+{re.escape(place)}[?.!]*$",
        re.IGNORECASE,
    )
    return pattern.sub("", query).strip()


def _candidate_dict(hit) -> dict[str, Any]:
    return {
        "key": hit.key,
        "value": hit.value,
        "score": round(float(hit.score), 6),
        "retrieval_method": hit.retrieval_method,
    }


def compile_natural_language_intent(
    query: str,
    *,
    retriever=None,
) -> tuple[WorkflowIntent, list[FilterDecision]]:
    """Compile an explicit English OR query into a grounded two-to-five tag intent.

    P4 deliberately supports one unambiguous natural-language operation. Free-form
    AND is rejected because ordinary English "cafes and restaurants" often means
    a union, while an OSM intersection means the same object carries every tag.
    Structured callers can request the exact intersection operation explicitly.
    """

    query = str(query).strip()
    if not query:
        raise PlanCompilationError("PLAN_QUERY_EMPTY", "Workflow query is empty")
    place = simple_place_heuristic(query)
    if not place:
        raise PlanCompilationError(
            "PLAN_PLACE_MISSING",
            "A workflow query must end with an explicit place, for example 'in Lund'",
        )
    without_place = _without_terminal_place(query, place)
    if AND_RE.search(without_place) and not OR_RE.search(without_place):
        raise PlanCompilationError(
            "PLAN_OPERATION_AMBIGUOUS",
            "Natural-language AND is ambiguous; use explicit_plan intersection semantics",
        )
    clauses = [item.strip(" ,;:-") for item in OR_RE.split(without_place)]
    clauses = [item for item in clauses if item]
    if len(clauses) < 2:
        raise PlanCompilationError(
            "PLAN_OPERATION_UNSUPPORTED",
            "P4 natural-language workflow requires two to five alternatives joined by 'or'",
        )
    if len(clauses) > 5:
        raise PlanCompilationError(
            "PLAN_TOO_MANY_FILTERS", "A workflow supports at most five filters"
        )

    search = retriever or _retriever()
    decisions: list[FilterDecision] = []
    selected: list[dict[str, str]] = []
    for clause in clauses:
        hits = search.retrieve(clause, k=3)
        hits = [item for item in hits if item.key and item.value]
        if not hits:
            raise PlanCompilationError(
                "PLAN_TAG_NOT_FOUND", f"No grounded OSM tag found for clause: {clause}"
            )
        best = hits[0]
        tag = {"key": str(best.key).strip(), "value": str(best.value).strip()}
        selected.append(tag)
        decisions.append(
            FilterDecision(
                clause=clause,
                selected_tag=tag,
                score=float(best.score),
                retrieval_method=best.retrieval_method,
                candidates=[_candidate_dict(item) for item in hits],
            )
        )

    try:
        intent = WorkflowIntent(
            place=place,
            operation="union",
            filters=selected,
            source="deterministic_compiler",
        )
    except ValueError as error:
        raise PlanCompilationError("PLAN_FILTERS_INVALID", str(error)) from error
    return intent, decisions


def build_workflow_plan(
    query: str,
    intent: WorkflowIntent,
    *,
    decisions: list[FilterDecision] | None = None,
) -> WorkflowPlan:
    steps = [
        WorkflowStep(
            step_id="geocode_place",
            tool="geocode_place",
            arguments={"place": intent.place, "trace_id": "$trace_id"},
            recovery="none",
        ),
        WorkflowStep(
            step_id="clip_city",
            tool="clip_osm",
            depends_on=["geocode_place"],
            arguments={
                "bbox": "$steps.geocode_place.bbox",
                "temp_dir": "$temp_dir",
            },
            recovery="retry_once",
        ),
    ]
    extract_ids: list[str] = []
    for index, tag in enumerate(intent.filters, start=1):
        step_id = f"extract_tag_{index}"
        extract_ids.append(step_id)
        steps.append(
            WorkflowStep(
                step_id=step_id,
                tool="extract_tag",
                depends_on=["clip_city"],
                arguments={
                    "sub_pbf": "$steps.clip_city.sub_pbf",
                    "tag": tag.model_dump(),
                    "temp_dir": "$temp_dir",
                    "step_id": step_id,
                },
            )
        )
    steps.append(
        WorkflowStep(
            step_id="combine_features",
            tool="combine_features",
            depends_on=extract_ids,
            arguments={
                "operation": intent.operation,
                "filters": [item.model_dump() for item in intent.filters],
                "feature_sets": [f"$steps.{step_id}.features" for step_id in extract_ids],
            },
        )
    )
    steps.append(
        WorkflowStep(
            step_id="write_geojson",
            tool="write_geojson",
            depends_on=["combine_features"],
            arguments={
                "combined": "$steps.combine_features.combined",
                "counts_by_filter": "$steps.combine_features.counts_by_filter",
                "operation": intent.operation,
                "filters": [item.model_dump() for item in intent.filters],
                "place": intent.place,
                "query": query,
            },
        )
    )
    return WorkflowPlan(
        query=query,
        intent=intent,
        decisions=decisions or [],
        steps=steps,
    )


def compile_natural_language_plan(query: str, *, retriever=None) -> WorkflowPlan:
    intent, decisions = compile_natural_language_intent(query, retriever=retriever)
    return build_workflow_plan(query, intent, decisions=decisions)
