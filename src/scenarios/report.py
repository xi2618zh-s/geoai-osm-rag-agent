"""ScenarioReport v1.0: evidence-backed scenario presentation (ADR-0021).

Rules carried over from M10: report sentences are templates without literal digits
and only reference registered metric IDs; every metric names its evidence and can be
recomputed from the current DatasetRegistry; downloads contain the full verified
result. Map previews allow up to 500 features per layer.
"""
from __future__ import annotations

import base64
import csv
import hashlib
import io
import json
import math
import re
from typing import Any, Literal, Optional

from pydantic import Field, StrictBool, StrictStr, model_validator

from src.agent.validation import RecoveryEvent
from src.datasets import DatasetRef, DatasetRegistry, DatasetRegistryError

from .contracts import SCHEMA_VERSION, ScenarioContract, ScenarioError

MAX_PREVIEW_FEATURES = 500
MAX_TABLE_ROWS = 50
MAX_EXPORT_BYTES = 20_000_000
COLORS = ("#9fb7c4", "#2b6cb0", "#f1ad4e", "#d64550", "#19a7a0", "#6f8fe8")
FORBIDDEN_PHRASES = ("isochrone", "walking time", "travel time", "natural language understanding")
EvidenceKind = Literal[
    "dataset_row_count", "dataset_field_sum", "dataset_field_extreme", "dataset_field_null_count",
    "tool_output_value", "plan_parameter", "difference", "ratio",
]


class ReportMetric(ScenarioContract):
    metric_id: StrictStr = Field(pattern=r"^[a-z][a-z0-9_]*$")
    label: StrictStr
    value: int | float
    unit: StrictStr
    evidence_kind: EvidenceKind
    evidence_refs: list[StrictStr] = Field(max_length=4)
    evidence_field: StrictStr


class ClarificationTurn(ScenarioContract):
    reason_code: StrictStr
    question: StrictStr
    answer: StrictStr


class ScenarioMapLayer(ScenarioContract):
    layer_id: StrictStr = Field(pattern=r"^[a-z][a-z0-9_]*$")
    label: StrictStr
    dataset_id: StrictStr
    crs: StrictStr
    color: StrictStr = Field(pattern=r"^#[0-9a-fA-F]{6}$")
    feature_count: int = Field(ge=0)
    preview_count: int = Field(ge=0, le=MAX_PREVIEW_FEATURES)
    geojson: dict[str, Any]
    popup_fields: list[StrictStr] = Field(max_length=8)


class ScenarioTable(ScenarioContract):
    title: StrictStr
    columns: list[StrictStr] = Field(min_length=1, max_length=12)
    rows: list[list[StrictStr]] = Field(max_length=MAX_TABLE_ROWS)
    total_rows: int = Field(ge=0)


class ScenarioChart(ScenarioContract):
    title: StrictStr
    metric_ids: list[StrictStr] = Field(min_length=1, max_length=8)


class SourceEvidence(ScenarioContract):
    dataset_id: StrictStr
    role: StrictStr
    source: StrictStr
    source_version: StrictStr
    content_hash: StrictStr
    crs: StrictStr
    row_count: int = Field(ge=0)


class DataLicense(ScenarioContract):
    role: StrictStr
    source: StrictStr
    license: StrictStr
    attribution: StrictStr
    provider_verified: StrictBool
    temporal_note: StrictStr


class StepEvidence(ScenarioContract):
    step_id: StrictStr
    tool: StrictStr
    input_ids: list[StrictStr]
    output_id: StrictStr
    attempts: int = Field(ge=0, le=2)


class DownloadArtifact(ScenarioContract):
    filename: StrictStr = Field(pattern=r"^[a-z][a-z0-9_-]*\.(?:geojson|csv)$")
    mime_type: Literal["application/json", "text/csv"]
    href: StrictStr
    sha256: StrictStr = Field(pattern=r"^[a-f0-9]{64}$")
    byte_count: int = Field(ge=0)
    feature_count: int = Field(ge=0)


class ScenarioReport(ScenarioContract):
    schema_version: Literal["1.0"] = SCHEMA_VERSION
    scenario: StrictStr
    title: StrictStr
    place: StrictStr
    crs: StrictStr
    command: StrictStr
    grammar_note: StrictStr
    parameters: dict[StrictStr, int | float]
    clarifications: list[ClarificationTurn] = Field(default_factory=list, max_length=8)
    plan_preview: list[StrictStr] = Field(min_length=1, max_length=12)
    headline_template: StrictStr
    report_templates: list[StrictStr] = Field(min_length=1, max_length=8)
    metrics: list[ReportMetric] = Field(min_length=1, max_length=16)
    layers: list[ScenarioMapLayer] = Field(min_length=2, max_length=6)
    chart: ScenarioChart
    table: ScenarioTable
    sources: list[SourceEvidence] = Field(min_length=1, max_length=32)
    data_licenses: list[DataLicense] = Field(min_length=1, max_length=8)
    steps: list[StepEvidence] = Field(min_length=1, max_length=12)
    recoveries: list[RecoveryEvent] = Field(default_factory=list)
    warnings: list[StrictStr]
    assumptions: list[StrictStr]
    limitations: list[StrictStr]
    downloads: list[DownloadArtifact] = Field(min_length=2, max_length=4)

    @model_validator(mode="after")
    def links_and_claims(self):
        ids = [metric.metric_id for metric in self.metrics]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate metric ID")
        sources = {item.dataset_id for item in self.sources}
        if any(layer.dataset_id not in sources for layer in self.layers):
            raise ValueError("map layer has no source evidence")
        if any(item not in ids for item in self.chart.metric_ids):
            raise ValueError("chart references an unknown metric")
        for template in [self.headline_template, *self.report_templates]:
            if re.search(r"[0-9]", template):
                raise ValueError("literal numeric report claim is forbidden")
            stripped = re.sub(r"\{[a-z][a-z0-9_]*\}", "", template)
            if "{" in stripped or "}" in stripped:
                raise ValueError("malformed report placeholder")
            if any(item not in ids for item in re.findall(r"\{([a-z][a-z0-9_]*)\}", template)):
                raise ValueError("report references an unknown metric")
        return self


# ---------------------------------------------------------------------- evidence

def _mismatch(message: str) -> ScenarioError:
    return ScenarioError("SCENARIO_REPORT_EVIDENCE_MISMATCH", message)


def _records(registry: DatasetRegistry, dataset_id: str) -> list[dict[str, Any]]:
    try:
        registry.get(dataset_id, verify=True)
        return list(registry.read(dataset_id).records)
    except DatasetRegistryError as error:
        raise _mismatch(f"evidence dataset {dataset_id} is unavailable") from error


def _number(value: Any) -> bool:
    return not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(float(value))


def field_sum(records: list[dict[str, Any]], field: str) -> int | float:
    return sum(r["properties"][field] for r in records if _number(r["properties"].get(field)))


def field_null_count(records: list[dict[str, Any]], field: str) -> int:
    return sum(1 for r in records if r["properties"].get(field) is None)


def evidence_value(metric: ReportMetric, registry: DatasetRegistry, values: dict[str, Any],
                   parameters: dict[str, Any]) -> int | float:
    kind, refs, field = metric.evidence_kind, metric.evidence_refs, metric.evidence_field
    if kind == "dataset_row_count":
        return len(_records(registry, refs[0]))
    if kind == "dataset_field_sum":
        return field_sum(_records(registry, refs[0]), field)
    if kind == "dataset_field_null_count":
        return field_null_count(_records(registry, refs[0]), field)
    if kind == "dataset_field_extreme":
        operation, name = field.split(":", 1)
        numbers = [r["properties"][name] for r in _records(registry, refs[0]) if _number(r["properties"].get(name))]
        if not numbers:
            raise _mismatch(f"{metric.metric_id}: no numeric values")
        return max(numbers) if operation == "max" else min(numbers)
    if kind == "plan_parameter":
        if field not in parameters:
            raise _mismatch(f"{metric.metric_id}: unknown plan parameter")
        return parameters[field]
    if kind in {"difference", "ratio"}:
        if any(item not in values for item in refs[:2]):
            raise _mismatch(f"{metric.metric_id}: derived metric references unknown metrics")
        left, right = values[refs[0]], values[refs[1]]
        if kind == "difference":
            return left - right
        if right == 0:
            raise _mismatch(f"{metric.metric_id}: ratio denominator is zero")
        return left / right
    raise _mismatch(f"{metric.metric_id}: evidence kind {kind} needs tool-output context")


def _same(left: Any, right: Any) -> bool:
    if isinstance(left, float) or isinstance(right, float):
        return math.isclose(float(left), float(right), rel_tol=1e-12, abs_tol=1e-12)
    return left == right


def verify_metric_evidence(report: ScenarioReport, registry: DatasetRegistry) -> None:
    """Recompute every metric from its declared evidence; raise on any difference."""
    values: dict[str, Any] = {}
    for metric in report.metrics:
        expected = evidence_value(metric, registry, values, report.parameters)
        if not _same(expected, metric.value):
            raise _mismatch(f"{metric.metric_id}: reported value differs from evidence")
        values[metric.metric_id] = metric.value


def rendered_report(report: ScenarioReport) -> list[str]:
    values = {m.metric_id: format(m.value, ".4g") if isinstance(m.value, float) else str(m.value) for m in report.metrics}
    return [template.format_map(values) for template in [report.headline_template, *report.report_templates]]


def contains_forbidden_phrase(texts: list[str]) -> Optional[str]:
    joined = " ".join(texts).lower()
    return next((phrase for phrase in FORBIDDEN_PHRASES if phrase in joined), None)


# ---------------------------------------------------------------------- builders

def metric(metric_id: str, label: str, value: int | float, unit: str, kind: str,
           refs: list[str], field: str) -> ReportMetric:
    return ReportMetric(metric_id=metric_id, label=label, value=value, unit=unit,
                        evidence_kind=kind, evidence_refs=refs, evidence_field=field)


def source(ref: DatasetRef, role: str) -> SourceEvidence:
    if re.search(r"[/\\]|^[A-Za-z]:", ref.source):
        raise ScenarioError("SCENARIO_SOURCE_UNSAFE", "source identifier contains a local path")
    return SourceEvidence(dataset_id=ref.id, role=role, source=ref.source, source_version=ref.source_version,
                          content_hash=ref.content_hash, crs=ref.crs, row_count=ref.row_count)


def layer(registry: DatasetRegistry, ref: DatasetRef, label: str, index: int, popup_fields: list[str]) -> ScenarioMapLayer:
    features = _records(registry, ref.id)
    preview = features[:MAX_PREVIEW_FEATURES]
    return ScenarioMapLayer(
        layer_id=f"layer_{index}", label=label, dataset_id=ref.id, crs=ref.crs,
        color=COLORS[(index - 1) % len(COLORS)], feature_count=len(features), preview_count=len(preview),
        geojson={"type": "FeatureCollection", "features": preview, "geoai_crs": ref.crs},
        popup_fields=popup_fields,
    )


def csv_safe(value: Any, *, display: bool = False) -> str:
    """Spreadsheet-safe text; full precision unless ``display`` rounds floats for tables."""
    if value is None:
        return "—" if display else ""
    if display and isinstance(value, float):
        rendered = format(value, ".1f")
    else:
        rendered = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, sort_keys=True)
    return "'" + rendered if rendered.lstrip().startswith(("=", "+", "-", "@")) else rendered


def table(registry: DatasetRegistry, ref: DatasetRef, title: str, columns: list[tuple[str, str]],
          sort_field: str, derived: Optional[list[tuple[str, Any]]] = None) -> ScenarioTable:
    """Display table; ``derived`` adds (header, callable(properties) -> str) columns."""
    features = sorted(_records(registry, ref.id), key=lambda item: str(item["properties"].get(sort_field)))
    rows = [[csv_safe(item["properties"].get(prop), display=True) for _, prop in columns]
            + [str(func(item["properties"])) for _, func in (derived or [])]
            for item in features[:MAX_TABLE_ROWS]]
    headers = [header for header, _ in columns] + [header for header, _ in (derived or [])]
    return ScenarioTable(title=title, columns=headers, rows=rows, total_rows=len(features))


def downloads(registry: DatasetRegistry, ref: DatasetRef, stem: str) -> list[DownloadArtifact]:
    features = _records(registry, ref.id)
    document = {"type": "FeatureCollection", "features": features, "geoai_crs": ref.crs}
    geojson = (json.dumps(document, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
    fields = sorted({str(key) for item in features for key in item.get("properties", {})})
    stream = io.StringIO(newline="")
    writer = csv.writer(stream, lineterminator="\n")
    writer.writerow(["feature_id", *fields])
    for index, item in enumerate(features, 1):
        props = item.get("properties", {})
        writer.writerow([csv_safe(item.get("id", f"row-{index}")), *[csv_safe(props.get(f)) for f in fields]])
    csv_bytes = stream.getvalue().encode("utf-8-sig")
    if max(len(geojson), len(csv_bytes)) > MAX_EXPORT_BYTES:
        raise ScenarioError("SCENARIO_EXPORT_LIMIT", "verified export exceeds the size cap")
    return [
        DownloadArtifact(filename=f"{stem}.geojson", mime_type="application/json",
                         href="data:application/json;base64," + base64.b64encode(geojson).decode("ascii"),
                         sha256=hashlib.sha256(geojson).hexdigest(), byte_count=len(geojson), feature_count=len(features)),
        DownloadArtifact(filename=f"{stem}.csv", mime_type="text/csv",
                         href="data:text/csv;base64," + base64.b64encode(csv_bytes).decode("ascii"),
                         sha256=hashlib.sha256(csv_bytes).hexdigest(), byte_count=len(csv_bytes), feature_count=len(features)),
    ]
