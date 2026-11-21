"""M10 evidence-backed result contract and deterministic vector presentation."""
from __future__ import annotations

import base64
import csv
import hashlib
import io
import json
import re
from typing import Any, Literal, Optional

from pydantic import ConfigDict, Field, StrictStr, model_validator

from src.agent.dynamic_planner import M07PlanRequest, PlanningResult, PlanError, validate_model_plan_json
from src.agent.validation import ValidationOutcome
from src.contracts import StrictContract
from src.datasets import DatasetRef, DatasetRegistry, DatasetRegistryError

MAX_PREVIEW_FEATURES = 250
MAX_TABLE_ROWS = 50
MAX_EXPORT_BYTES = 20_000_000
_COLORS = ("#19a7a0", "#f1ad4e", "#6f8fe8", "#e87982")


class PresentationError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class M10Contract(StrictContract):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, strict=True)


class VerifiedRun(M10Contract):
    request: M07PlanRequest
    plan: PlanningResult
    outcome: ValidationOutcome


class ResultMetric(M10Contract):
    metric_id: StrictStr = Field(pattern=r"^[a-z][a-z0-9_]*$")
    label: StrictStr
    value: int | float
    unit: StrictStr
    evidence_kind: Literal["dataset_row_count", "plan_parameter", "difference"]
    evidence_refs: list[StrictStr]
    evidence_field: StrictStr


class SourceEvidence(M10Contract):
    dataset_id: StrictStr
    source: StrictStr
    source_version: StrictStr
    content_hash: StrictStr
    crs: StrictStr
    row_count: int = Field(ge=0)


class StepEvidence(M10Contract):
    step_id: StrictStr
    tool: StrictStr
    input_ids: list[StrictStr]
    output_id: StrictStr
    attempts: int = Field(ge=0, le=2)
    reused: bool


class MapLayer(M10Contract):
    layer_id: StrictStr = Field(pattern=r"^[a-z][a-z0-9_]*$")
    label: StrictStr
    dataset_id: StrictStr
    crs: StrictStr
    color: StrictStr = Field(pattern=r"^#[0-9a-fA-F]{6}$")
    feature_count: int = Field(ge=0)
    preview_count: int = Field(ge=0, le=MAX_PREVIEW_FEATURES)
    geojson: dict[str, Any]
    popup_fields: list[StrictStr]


class ResultTable(M10Contract):
    columns: list[StrictStr]
    rows: list[list[StrictStr]]
    total_rows: int = Field(ge=0)


class MetricChart(M10Contract):
    title: StrictStr
    metric_ids: list[StrictStr] = Field(min_length=1, max_length=8)


class DownloadArtifact(M10Contract):
    filename: StrictStr = Field(pattern=r"^[a-z][a-z0-9_-]*\.(?:geojson|csv)$")
    mime_type: Literal["application/json", "text/csv"]
    href: StrictStr
    sha256: StrictStr = Field(pattern=r"^[a-f0-9]{64}$")
    byte_count: int = Field(ge=0)


class AnalysisResult(M10Contract):
    schema_version: Literal["1.0"] = "1.0"
    template: Literal["search", "coverage", "comparison", "analysis"]
    title: StrictStr
    place: StrictStr
    crs: StrictStr
    headline_template: StrictStr
    report_templates: list[StrictStr] = Field(min_length=1, max_length=8)
    metrics: list[ResultMetric] = Field(min_length=1, max_length=16)
    layers: list[MapLayer] = Field(min_length=2, max_length=6)
    chart: MetricChart
    table: ResultTable
    sources: list[SourceEvidence] = Field(min_length=1, max_length=20)
    steps: list[StepEvidence] = Field(min_length=1, max_length=16)
    warnings: list[StrictStr]
    assumptions: list[StrictStr]
    limitations: list[StrictStr]
    downloads: list[DownloadArtifact] = Field(min_length=2, max_length=4)

    @model_validator(mode="after")
    def validate_links_and_claims(self):
        metric_ids = {metric.metric_id for metric in self.metrics}
        if len(metric_ids) != len(self.metrics):
            raise ValueError("duplicate metric ID")
        source_ids = {source.dataset_id for source in self.sources}
        if any(layer.dataset_id not in source_ids for layer in self.layers):
            raise ValueError("map layer has no source evidence")
        if any(metric_id not in metric_ids for metric_id in self.chart.metric_ids):
            raise ValueError("chart references unknown metric")
        for template in [self.headline_template, *self.report_templates]:
            if re.search(r"[0-9]", template):
                raise ValueError("literal numeric report claim is forbidden")
            placeholders = re.findall(r"\{([a-z][a-z0-9_]*)\}", template)
            if any(item not in metric_ids for item in placeholders):
                raise ValueError("report references unknown metric")
            if "{" in re.sub(r"\{[a-z][a-z0-9_]*\}", "", template) or "}" in re.sub(r"\{[a-z][a-z0-9_]*\}", "", template):
                raise ValueError("malformed report placeholder")
        return self


def _current(registry: DatasetRegistry, ref: DatasetRef) -> DatasetRef:
    try:
        actual = registry.get(ref.id, verify=True)
        if actual != ref:
            raise ValueError("stale reference")
        registry.read(ref.id)
    except (DatasetRegistryError, ValueError) as error:
        raise PresentationError("M10_DATASET_STALE", "DatasetRef is stale, corrupt or unreadable") from error
    return actual


def _lookup(registry: DatasetRegistry, dataset_id: str) -> DatasetRef:
    try:
        return _current(registry, registry.get(dataset_id, verify=True))
    except DatasetRegistryError as error:
        raise PresentationError("M10_DATASET_STALE", "DatasetRef is missing or corrupt") from error

def _has_declared_source(registry: DatasetRegistry, ref: DatasetRef, source: str) -> bool:
    """Follow bounded, current DatasetRef lineage for preprocessed input roles."""
    pending = [ref]
    seen: set[str] = set()
    while pending:
        current = pending.pop()
        if current.id in seen:
            continue
        seen.add(current.id)
        if len(seen) > 32:
            raise PresentationError("M10_SOURCE_MISMATCH", "Source lineage exceeds audit bound")
        if current.source == source:
            return True
        for event in current.lineage:
            for parent_id in event.parent_ids:
                pending.append(_lookup(registry, parent_id))
    return False

def _verified(run: VerifiedRun, registry: DatasetRegistry) -> tuple[DatasetRef, dict[str, DatasetRef], list[StepEvidence]]:
    if run.outcome.status != "success" or run.outcome.final_dataset is None:
        raise PresentationError("M10_RESULT_UNVERIFIED", "Only M08 success outcomes may be presented")
    try:
        validate_model_plan_json(run.plan.model_dump_json(), run.request)
    except (PlanError, ValueError) as error:
        raise PresentationError("M10_PLAN_INVALID", "Plan does not pass the M07 static gate") from error
    records = run.outcome.step_records[-len(run.plan.tool_steps):]
    if len(records) != len(run.plan.tool_steps) or any(item.status != "succeeded" for item in records):
        raise PresentationError("M10_PLAN_TRACE_MISMATCH", "Verified step trace does not match plan")
    refs: dict[str, DatasetRef] = {}
    steps: list[StepEvidence] = []
    for step, record in zip(run.plan.tool_steps, records):
        if step.step_id != record.step_id or step.tool != record.tool or len(step.input_roles) != len(record.input_ids) or record.output_id is None:
            raise PresentationError("M10_PLAN_TRACE_MISMATCH", "Step trace differs from the accepted plan")
        for role, dataset_id in zip(step.input_roles, record.input_ids):
            actual = _lookup(registry, dataset_id)
            if role in run.request.datasets:
                declared = run.request.datasets[role]
                if actual.kind != declared.kind or not _has_declared_source(registry, actual, declared.source) or actual.crs != declared.crs or not set(declared.required_fields).issubset({field.name for field in actual.fields}):
                    raise PresentationError("M10_SOURCE_MISMATCH", "Step source differs from declared role contract")
            if role in refs and refs[role].id != actual.id:
                raise PresentationError("M10_PLAN_TRACE_MISMATCH", "Step input lineage changed")
            refs[role] = actual
        output = _lookup(registry, record.output_id)
        refs[step.output_role] = output
        steps.append(StepEvidence(
            step_id=step.step_id, tool=step.tool, input_ids=record.input_ids,
            output_id=output.id, attempts=record.attempts, reused=record.attempts == 0,
        ))
    final = _current(registry, run.outcome.final_dataset)
    if final.id != steps[-1].output_id:
        raise PresentationError("M10_PLAN_TRACE_MISMATCH", "Final DatasetRef differs from final step")
    return final, refs, steps


def _source(ref: DatasetRef) -> SourceEvidence:
    if re.search(r"[/\\]|^[A-Za-z]:", ref.source):
        raise PresentationError("M10_SOURCE_UNSAFE", "Source identifier contains a local path")
    return SourceEvidence(
        dataset_id=ref.id, source=ref.source, source_version=ref.source_version,
        content_hash=ref.content_hash, crs=ref.crs, row_count=ref.row_count,
    )


def _layer(registry: DatasetRegistry, ref: DatasetRef, label: str, index: int) -> MapLayer:
    features = list(registry.read(ref.id).records)
    preview = features[:MAX_PREVIEW_FEATURES]
    popup = sorted({str(key) for item in preview[:10] for key in item.get("properties", {})})[:4]
    return MapLayer(
        layer_id=f"layer_{index}", label=label, dataset_id=ref.id, crs=ref.crs,
        color=_COLORS[(index - 1) % len(_COLORS)], feature_count=len(features),
        preview_count=len(preview), geojson={"type": "FeatureCollection", "features": preview, "geoai_crs": ref.crs},
        popup_fields=popup,
    )


def _csv_safe(value: Any) -> str:
    if value is None:
        return ""
    rendered = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, sort_keys=True)
    if rendered.lstrip().startswith(("=", "+", "-", "@")):
        return "'" + rendered
    return rendered


def _table(registry: DatasetRegistry, ref: DatasetRef) -> ResultTable:
    features = list(registry.read(ref.id).records)
    fields = sorted({str(key) for item in features for key in item.get("properties", {})})
    columns = ["feature_id", *fields]
    rows = []
    for index, item in enumerate(features[:MAX_TABLE_ROWS], 1):
        props = item.get("properties", {})
        rows.append([_csv_safe(item.get("id", f"row-{index}")), *[_csv_safe(props.get(field)) for field in fields]])
    return ResultTable(columns=columns, rows=rows, total_rows=len(features))


def _artifact(registry: DatasetRegistry, ref: DatasetRef, stem: str) -> list[DownloadArtifact]:
    features = list(registry.read(ref.id).records)
    document = {"type": "FeatureCollection", "features": features, "geoai_crs": ref.crs}
    geojson = (json.dumps(document, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
    if len(geojson) > MAX_EXPORT_BYTES:
        raise PresentationError("M10_EXPORT_LIMIT", "Full verified GeoJSON exceeds export cap")
    fields = sorted({str(key) for item in features for key in item.get("properties", {})})
    stream = io.StringIO(newline="")
    writer = csv.writer(stream, lineterminator="\n")
    writer.writerow([_csv_safe("feature_id"), *[_csv_safe(field) for field in fields]])
    for index, item in enumerate(features, 1):
        props = item.get("properties", {})
        writer.writerow([_csv_safe(item.get("id", f"row-{index}")), *[_csv_safe(props.get(field)) for field in fields]])
    csv_bytes = stream.getvalue().encode("utf-8-sig")
    if len(csv_bytes) > MAX_EXPORT_BYTES:
        raise PresentationError("M10_EXPORT_LIMIT", "Full verified CSV exceeds export cap")
    return [
        DownloadArtifact(filename=f"{stem}.geojson", mime_type="application/json",
                         href="data:application/json;base64," + base64.b64encode(geojson).decode("ascii"),
                         sha256=hashlib.sha256(geojson).hexdigest(), byte_count=len(geojson)),
        DownloadArtifact(filename=f"{stem}.csv", mime_type="text/csv",
                         href="data:text/csv;base64," + base64.b64encode(csv_bytes).decode("ascii"),
                         sha256=hashlib.sha256(csv_bytes).hexdigest(), byte_count=len(csv_bytes)),
    ]


def _metric(metric_id: str, label: str, value: int | float, unit: str, kind: str, refs: list[str], field: str) -> ResultMetric:
    return ResultMetric(metric_id=metric_id, label=label, value=value, unit=unit,
                        evidence_kind=kind, evidence_refs=refs, evidence_field=field)


def build_analysis_result(
    primary: VerifiedRun, registry: DatasetRegistry, *, comparison: Optional[VerifiedRun] = None,
) -> AnalysisResult:
    """Create a presentation solely from current M07/M08 evidence and DatasetRefs."""
    final, refs, steps = _verified(primary, registry)
    other = _verified(comparison, registry) if comparison is not None else None
    if other is not None and other[0].crs != final.crs:
        raise PresentationError("M10_CRS_MISMATCH", "Comparison layers require the same CRS")
    template = "comparison" if other else (
        "coverage" if primary.plan.task_class == "within_distance" else
        "search" if primary.plan.task_class in {"search", "filtered_search"} else "analysis"
    )
    source_roles = [role for role in primary.request.datasets if role in refs]
    if not source_roles:
        raise PresentationError("M10_PLAN_TRACE_MISMATCH", "No initial dataset role in step trace")
    source = refs[source_roles[0]]
    metrics = [
        _metric("result_count", "Verified result", final.row_count, "features", "dataset_row_count", [final.id], "row_count"),
        _metric("source_count", "Source features", source.row_count, "features", "dataset_row_count", [source.id], "row_count"),
    ]
    layers = []
    layer_refs: list[tuple[DatasetRef, str]] = []
    if template == "comparison":
        other_final = other[0]
        metrics.extend([
            _metric("comparison_count", "Comparison result", other_final.row_count, "features", "dataset_row_count", [other_final.id], "row_count"),
            _metric("delta_count", "Difference", other_final.row_count - final.row_count, "features", "difference", [other_final.id, final.id], "comparison_count-result_count"),
        ])
        layer_refs = [(final, "Baseline result"), (other_final, "Comparison result")]
        chart = MetricChart(title="Verified feature counts", metric_ids=["result_count", "comparison_count"])
        table = ResultTable(columns=["group", "count"], rows=[["Baseline", str(final.row_count)], ["Comparison", str(other_final.row_count)]], total_rows=2)
        report = ["The baseline has {result_count} features.", "The comparison has {comparison_count} features.", "The comparison minus baseline is {delta_count} features."]
    elif template == "coverage":
        distance = primary.plan.business_parameters["distance_m"]
        metrics.append(_metric("distance_m", "Fixed buffer distance", distance, "m", "plan_parameter", [], "distance_m"))
        layer_refs = [(refs[role], role) for role in source_roles[:2]] + [(final, "Verified result")]
        chart = MetricChart(title="Input and result counts", metric_ids=["source_count", "result_count"])
        table = _table(registry, final)
        report = ["The fixed geometric buffer is {distance_m} m.", "The verified output contains {result_count} features."]
    else:
        layer_refs = [(source, "Source"), (final, "Verified result")]
        chart = MetricChart(title="Input and result counts", metric_ids=["source_count", "result_count"])
        table = _table(registry, final)
        report = ["The source contains {source_count} features.", "The verified output contains {result_count} features."]
    for index, (ref, label) in enumerate(layer_refs, 1):
        layers.append(_layer(registry, ref, label, index))
    all_refs = {item.id: item for item in refs.values()}
    if other:
        all_refs.update({item.id: item for item in other[1].values()})
    source_evidence = [_source(all_refs[item]) for item in sorted(all_refs)]
    step_evidence = steps + (other[2] if other else [])
    warnings = [f"Recovery recorded: {event.action}" for event in primary.outcome.recoveries]
    if other:
        warnings += [f"Comparison recovery recorded: {event.action}" for event in comparison.outcome.recoveries]
    limitations = ["Source identity is declared by the data provider and is not independently authenticated."]
    if any(item.source.startswith("fixture:") for item in source_evidence):
        limitations.append("Synthetic fixture data; the named place is an example and no city boundary was verified.")
    if final.crs != "EPSG:4326":
        limitations.append("Projected-coordinate GeoJSON carries geoai_crs; reproject to EPSG:4326 for RFC 7946 consumers.")
    if template == "coverage":
        limitations.append("Fixed geometric buffers are not travel-time or transit isochrones.")
    if any(layer.preview_count < layer.feature_count for layer in layers):
        limitations.append("Map preview is truncated; downloads contain the complete verified result.")
    downloads = _artifact(registry, final, "result")
    if other:
        downloads += _artifact(registry, other[0], "comparison")
    return AnalysisResult(
        template=template, title={"search": "Feature search results", "coverage": "Fixed-distance proximity results", "comparison": "Verified count comparison", "analysis": "Spatial analysis results"}[template], place=primary.plan.intent.place.name,
        crs=final.crs, headline_template="Verified {result_count} result features.",
        report_templates=report, metrics=metrics, layers=layers, chart=chart, table=table,
        sources=source_evidence, steps=step_evidence, warnings=warnings,
        assumptions=["Counts refer to current, hash-verified DatasetRef rows."],
        limitations=limitations, downloads=downloads,
    )


def validate_analysis_result(
    result: AnalysisResult, primary: VerifiedRun, registry: DatasetRegistry, *,
    comparison: Optional[VerifiedRun] = None,
) -> None:
    """Reject changed numeric claims, layers, downloads or provenance before display."""
    canonical = build_analysis_result(primary, registry, comparison=comparison)
    if result != canonical:
        raise PresentationError("M10_EVIDENCE_MISMATCH", "Presentation differs from verified evidence")


def rendered_report(result: AnalysisResult) -> list[str]:
    values = {item.metric_id: format(item.value, "g") if isinstance(item.value, float) else str(item.value) for item in result.metrics}
    return [template.format_map(values) for template in [result.headline_template, *result.report_templates]]


from src.agent.result_html import render_analysis_html  # noqa: E402

