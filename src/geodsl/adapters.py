"""Compatibility adapters from existing contracts into GeoDSL v1."""

from __future__ import annotations

import hashlib

from src.agent.contracts import WorkflowPlan
from src.geodsl.contracts import (
    AttributeFilter,
    DatasetRequirement,
    GeoAnalysisPlan,
    GeoAnalysisStep,
    GeoTaskIntent,
    OutputRequirement,
    PlaceReference,
)


def workflow_plan_to_geodsl(workflow: WorkflowPlan) -> GeoAnalysisPlan:
    """Represent a legacy OSM union/intersection workflow as a GeoDSL search.

    The adapter is intentionally one-way. Existing HTTP and execution contracts
    remain authoritative for the P4 workflow; the new plan is a semantic view for
    future modules and does not mutate the supplied model.
    """

    filters = [
        AttributeFilter(
            dataset_role="osm_features",
            field=f"tags.{item.key}",
            operator="eq",
            value=item.value,
        )
        for item in workflow.intent.filters
    ]
    dataset = DatasetRequirement(
        role="osm_features",
        kind="vector",
        source="osm",
        crs="EPSG:4326",
        required_fields=["osm_type", "osm_id", "geometry", "tags"],
    )
    output = OutputRequirement(
        formats=["map", "geojson", "summary"],
        crs="EPSG:4326",
    )
    intent = GeoTaskIntent(
        task_type="search",
        query=workflow.query,
        place=PlaceReference(name=workflow.intent.place, crs="EPSG:4326"),
        analysis_crs="EPSG:4326",
        datasets=[dataset],
        attribute_filters=filters,
        output=output,
    )
    digest = hashlib.sha256(
        workflow.model_dump_json().encode("utf-8")
    ).hexdigest()[:16]
    step = GeoAnalysisStep(
        step_id="search_osm",
        operation="search",
        input_datasets=[dataset.role],
        output_dataset="selected_features",
        filter_logic="any" if workflow.intent.operation == "union" else "all",
        attribute_filters=[item.model_copy(deep=True) for item in filters],
    )
    return GeoAnalysisPlan(
        plan_id=f"legacy_{digest}",
        source="legacy_workflow",
        source_version=workflow.version,
        intent=intent,
        steps=[step],
    )
