"""Static gate for typed scenario plans (ADR-0021)."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from pydantic import ValidationError

from src.datasets import DatasetRef
from src.datasets.contracts import DatasetLineage

from .contracts import MAX_PLAN_STEPS, ScenarioCatalogue, ScenarioError, ScenarioPlan, ScenarioStep
from .registry import SCENARIO_TOOL_REGISTRY

# Tools a scenario plan may contain, with their DatasetRef input fields (in role order)
# and scalar parameter keys. Quality/repair tools are internal or not admitted.
PLANNABLE_TOOLS: dict[str, tuple[tuple[str, ...], frozenset[str]]] = {
    "load_population_dataset": (("dataset",), frozenset({"provider", "dataset_year"})),
    "reproject_dataset": (("dataset",), frozenset({"target_crs"})),
    "filter_attributes": (("dataset",), frozenset({"conditions", "logic"})),
    "buffer_features": (("dataset",), frozenset({"distance_m"})),
    "nearest_features": (("dataset", "target_dataset"), frozenset({"k"})),
    "features_within": (("dataset", "container_dataset"), frozenset()),
    "spatial_join": (("dataset", "right_dataset"), frozenset({"predicate"})),
    "aggregate_by_region": (("dataset", "values_dataset"),
                            frozenset({"region_id_field", "values_region_id_field", "metrics"})),
}


def tool_payload(step: ScenarioStep, refs: dict[str, DatasetRef]) -> dict[str, Any]:
    fields, _ = PLANNABLE_TOOLS[step.tool]
    payload: dict[str, Any] = {field: refs[role] for field, role in zip(fields, step.input_roles)}
    payload.update(step.parameters)
    return payload


def _placeholder(index: int) -> DatasetRef:
    dataset_id = f"ds_{index:032x}"
    stamp = datetime(2026, 1, 1, tzinfo=timezone.utc)
    return DatasetRef(
        id=dataset_id, kind="vector", format="geojson",
        storage_uri=f"registry://datasets/{dataset_id}/data.geojson", crs="EPSG:3006",
        row_count=0, source="scenario:static-gate", source_version="1", created_at=stamp,
        content_hash="sha256:" + "0" * 64, size_bytes=0, lifecycle="persistent",
        lineage=[DatasetLineage(operation="register", timestamp=stamp)],
    )


def validate_plan(plan: ScenarioPlan, catalogue: ScenarioCatalogue, *, tools=SCENARIO_TOOL_REGISTRY) -> None:
    def reject(message: str) -> None:
        raise ScenarioError("SCENARIO_PLAN_INVALID", message)

    if not 1 <= len(plan.steps) <= MAX_PLAN_STEPS or len(plan.preview) != len(plan.steps):
        reject("plan step count or preview length is invalid")
    available = set(catalogue.entries)
    producers: dict[str, str] = {}
    for index, step in enumerate(plan.steps, 1):
        if step.step_id != f"step_{index}":
            reject(f"{step.step_id}: step IDs must be sequential")
        if step.tool not in PLANNABLE_TOOLS:
            reject(f"{step.step_id}: tool {step.tool} is not admitted in scenario plans")
        try:
            registration = tools.get(step.tool)
        except Exception:
            reject(f"{step.step_id}: tool {step.tool} is not registered")
        fields, keys = PLANNABLE_TOOLS[step.tool]
        if len(step.input_roles) != len(fields) or len(set(step.input_roles)) != len(step.input_roles):
            reject(f"{step.step_id}: wrong number of distinct input roles")
        if any(role not in available for role in step.input_roles):
            reject(f"{step.step_id}: input role is unavailable at this step")
        if step.output_role in available:
            reject(f"{step.step_id}: output role reuses an existing role")
        expected = {producers[role] for role in step.input_roles if role in producers}
        if len(step.depends_on) != len(set(step.depends_on)) or set(step.depends_on) != expected:
            reject(f"{step.step_id}: dependencies do not match input lineage")
        if set(step.parameters) != keys:
            reject(f"{step.step_id}: parameters must be exactly {sorted(keys)}")
        placeholders = {role: _placeholder(position + 1) for position, role in enumerate(step.input_roles)}
        try:
            registration.input_model(**tool_payload(step, placeholders))
        except (ValidationError, TypeError, ValueError) as error:
            reject(f"{step.step_id}: parameters fail the {step.tool} input contract: {error.__class__.__name__}")
        available.add(step.output_role)
        producers[step.output_role] = step.step_id
