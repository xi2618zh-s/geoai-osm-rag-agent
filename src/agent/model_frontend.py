"""M11c v2 model front-end: local qwen2.5:3b semantic frame plus deterministic grounding.

The model only fills a small JSON semantic frame: operation, dataset roles, attribute
conditions, k, spatial relation and aggregation metric. Deterministic code then checks
roles and fields against the declared catalogue, requires a lexical reference in the query
to a bound dataset (otherwise missing_place), applies the rank/buffer decline rules,
chooses the filtered side from condition fields, reads a join relation from the
first-mentioned dataset, infers join keys and renders the M07 command through the rule
front-end's renderer; M07/M08 still plan and execute. The
model never produces spatial results. Its only inputs are the query text and a profile
of the declared catalogue (roles, geometry kinds, field names/types and short lists of
text values). Version: m11c-frontend-v2 (m11c-protocol-v2, ADR-0020).
"""
from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass, field
import hashlib
import http.client
import json
from pathlib import Path
import re
import socket
import tempfile
import time
from typing import Any, Callable, Literal, Mapping, Optional, Union
import urllib.error
import urllib.request

from pydantic import BaseModel, ConfigDict, StrictFloat, StrictInt, StrictStr, ValidationError

from src.agent.baselines import DatasetCatalogue, register_catalogue
from src.agent.benchmark_frontend import (
    FILTER_SLOT, MODEL_FRONTEND_VERSION, NO_PRODUCTION, ROLE_SLOTS, FrontendDecision, _decline,
    _join_keys, _numeric_fields, _singular, render_request,
)
from src.datasets import DatasetRef, LocalDatasetRegistry
from src.tools import ANALYSIS_TOOL_REGISTRY

MODEL_ID = "qwen2.5:3b"
MODEL_DIGEST = "357c53fb659c5076de1d65ccb0b397446227b71a42be9d1603d46168015c9e4b"
OLLAMA_VERSION = "0.13.5"
ENDPOINT = "http://127.0.0.1:11434"
DECODING_OPTIONS = {"temperature": 0, "seed": 1107, "num_ctx": 4096, "num_predict": 256,
                    "num_thread": 4, "use_mmap": True}
KEEP_ALIVE = "30m"
ATTEMPT_TIMEOUT_S = 120.0
MAX_ATTEMPTS = 2
SERVICE_TIMEOUT_S = 10.0
PROMPT_PATH = Path(__file__).resolve().parent / "prompts" / "m11c_frontend_v2_prompt.txt"
MAX_LISTED_VALUES = 12
OPERATIONS = ("search", "buffer", "nearest", "within", "join", "aggregate", "rank", "other")
COMPARATORS = ("=", "!=", ">", ">=", "<", "<=")
RELATIONS = ("intersects", "within", "contains")
METRICS = ("count", "sum", "mean", "min", "max")
OPERATION_NAMES = {"join": "spatial_join", "aggregate": "aggregate_by_region"}
CONVERSE = {"intersects": "intersects", "within": "contains", "contains": "within"}
PAIRING_VERBS = frozenset({"join", "joined", "pair", "paired", "match", "matched"})  # "joins" -> "join"
IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
NUMBER = re.compile(r"-?[0-9]+(?:\.[0-9]+)?")
GEOMETRY_KINDS = {"Point": "points", "MultiPoint": "points", "LineString": "lines",
                  "MultiLineString": "lines", "Polygon": "polygons", "MultiPolygon": "polygons"}
TYPE_NAMES = {"integer": "number", "number": "number", "string": "text", "boolean": "true/false"}


class ModelUnavailable(RuntimeError):
    """The frozen local model service or model digest is not available: stop, never substitute."""


# ---------------------------------------------------------------- prompt and catalogue profile

@dataclass(frozen=True)
class PromptTemplate:
    text: str
    label: str

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.text.encode("utf-8")).hexdigest()


def load_prompt(path: Path = PROMPT_PATH, label: Optional[str] = None) -> PromptTemplate:
    """Versioned prompt file; line endings are normalised so the hash is checkout-independent."""
    text = Path(path).read_bytes().decode("utf-8").replace("\r\n", "\n")
    return PromptTemplate(text=text, label=label or Path(path).stem)


@dataclass(frozen=True)
class FieldProfile:
    name: str
    type: str
    listed: Optional[tuple[str, ...]]          # short text value list shown to the model
    distinct: tuple[tuple[str, str], ...]       # all non-null values as (type, json), for grounding
    non_null: int


@dataclass(frozen=True)
class DatasetProfile:
    role: str
    geometry: str
    rows: int
    fields: tuple[FieldProfile, ...]

    def field(self, name: str) -> Optional[FieldProfile]:
        return next((item for item in self.fields if item.name == name), None)

    def names(self) -> set[str]:
        return {item.name for item in self.fields}

    def text(self) -> str:
        if not self.rows:
            return f"- {self.role}: no rows"
        parts = []
        for item in self.fields:
            kind = TYPE_NAMES.get(item.type, item.type)
            parts.append(f"{item.name} ({kind}: {', '.join(item.listed)})" if item.listed else f"{item.name} ({kind})")
        listed = ", ".join(parts) if parts else "none"
        return f"- {self.role}: {self.geometry}, {self.rows} rows; fields: {listed}"


@dataclass(frozen=True)
class CatalogueProfile:
    scope: str
    datasets: tuple[DatasetProfile, ...]

    def roles(self) -> list[str]:
        return [item.role for item in self.datasets]

    def dataset(self, role: str) -> DatasetProfile:
        return next(item for item in self.datasets if item.role == role)

    def field_names(self) -> list[str]:
        return sorted({f.name for item in self.datasets for f in item.fields})

    def text(self) -> str:
        return "\n".join(item.text() for item in self.datasets)

    def sha256(self) -> str:
        payload = json.dumps(asdict(self), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _geometry_kind(records: list[dict[str, Any]]) -> str:
    kinds = {GEOMETRY_KINDS.get((item.get("geometry") or {}).get("type"), "other") for item in records}
    return next(iter(kinds)) if len(kinds) == 1 else "mixed geometries"


def profile_catalogue(catalogue: DatasetCatalogue) -> CatalogueProfile:
    """Schema and value profile of every declared dataset (hash-checked on load)."""
    datasets = []
    with tempfile.TemporaryDirectory(prefix="m11c_v2_profile_") as temp:
        registry = LocalDatasetRegistry(Path(temp) / "datasets")
        refs = register_catalogue(catalogue, registry)
        for role in sorted(refs):
            records = list(registry.read(refs[role].id).records)
            fields = []
            for item in refs[role].fields:
                values = [record.get("properties", {}).get(item.name) for record in records]
                values = [value for value in values if value is not None]
                distinct = tuple(sorted({(type(v).__name__, json.dumps(v, sort_keys=True, ensure_ascii=False))
                                         for v in values}))
                texts = sorted({v for v in values if isinstance(v, str)})
                listed = tuple(texts) if item.type == "string" and 0 < len(texts) <= MAX_LISTED_VALUES else None
                fields.append(FieldProfile(item.name, item.type, listed, distinct, len(values)))
            datasets.append(DatasetProfile(role, _geometry_kind(records) if records else "no rows",
                                           len(records), tuple(fields)))
    return CatalogueProfile(catalogue.scope, tuple(datasets))


def _nullable_enum(values) -> dict[str, Any]:
    return {"anyOf": [{"type": "string", "enum": list(values)}, {"type": "null"}]}


def frame_schema(profile: CatalogueProfile) -> dict[str, Any]:
    """Frozen frame structure; role and field enums come from the declared catalogue."""
    fields = profile.field_names()
    field_type = {"type": "string", "enum": fields} if fields else {"type": "string"}
    role = _nullable_enum(profile.roles())
    return {
        "type": "object",
        "properties": {
            "op": {"type": "string", "enum": list(OPERATIONS)},
            "data": role,
            "other": role,
            "where": {"type": "array", "items": {
                "type": "object",
                "properties": {"field": field_type, "cmp": {"type": "string", "enum": list(COMPARATORS)},
                               "value": {"type": ["string", "number"]}},
                "required": ["field", "cmp", "value"], "additionalProperties": False}},
            "k": {"type": ["integer", "null"]},
            "relation": _nullable_enum(RELATIONS),
            "subject": role,
            "metric": _nullable_enum(METRICS),
            "metric_field": {"anyOf": [field_type, {"type": "null"}]},
        },
        "required": ["op", "data"],
        "additionalProperties": False,
    }


def render_prompt(template: PromptTemplate, profile: CatalogueProfile, query: str) -> str:
    text = template.text.replace("{catalogue}", profile.text())
    return text.replace("{query}", " ".join(str(query).split()))


# ---------------------------------------------------------------- semantic frame

class FrameCondition(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    field: StrictStr
    cmp: Literal["=", "!=", ">", ">=", "<", "<="]
    value: Union[StrictStr, StrictInt, StrictFloat]


class SemanticFrame(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    op: Literal["search", "buffer", "nearest", "within", "join", "aggregate", "rank", "other"]
    data: Optional[StrictStr] = None
    other: Optional[StrictStr] = None
    where: list[FrameCondition] = []
    k: Optional[StrictInt] = None
    relation: Optional[Literal["intersects", "within", "contains"]] = None
    subject: Optional[StrictStr] = None
    metric: Optional[Literal["count", "sum", "mean", "min", "max"]] = None
    metric_field: Optional[StrictStr] = None


def parse_frame(raw: str, profile: CatalogueProfile) -> tuple[str, Optional[SemanticFrame]]:
    """Return (outcome, frame); outcome is ok, non_json or schema_invalid."""
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return "non_json", None
    if not isinstance(data, dict):
        return "schema_invalid", None
    try:
        frame = SemanticFrame.model_validate(data)
    except ValidationError:
        return "schema_invalid", None
    roles, fields = set(profile.roles()), set(profile.field_names())
    if any(role is not None and role not in roles for role in (frame.data, frame.other, frame.subject)):
        return "schema_invalid", None
    if any(item.field not in fields for item in frame.where) or \
            (frame.metric_field is not None and frame.metric_field not in fields):
        return "schema_invalid", None
    return "ok", frame


# ---------------------------------------------------------------- Ollama client and call accounting

Transport = Callable[[str, str, Optional[dict], float], tuple[int, bytes]]


def urllib_transport(method: str, url: str, payload: Optional[dict], timeout: float) -> tuple[int, bytes]:
    """Plain HTTP to the local service; system proxies are bypassed."""
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(url, data=data, method=method, headers={"Content-Type": "application/json"})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(request, timeout=timeout) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as error:
        return error.code, error.read()
    except urllib.error.URLError as error:
        if isinstance(error.reason, (TimeoutError, socket.timeout)):
            raise TimeoutError(str(error.reason)) from error
        raise ConnectionError(str(error.reason)) from error


@dataclass
class Attempt:
    outcome: str                      # ok | timeout | connection_error | http_error | non_json | schema_invalid
    duration_ms: float
    raw_response: Optional[str] = None
    http_status: Optional[int] = None
    error: Optional[str] = None
    prompt_tokens: Optional[int] = None
    eval_tokens: Optional[int] = None
    load_ms: Optional[float] = None
    total_ms: Optional[float] = None


@dataclass
class LogicalCall:
    """One logical front-end call: at most MAX_ATTEMPTS HTTP attempts."""

    attempts: list[Attempt]
    frame: Optional[SemanticFrame]
    wall_ms: float

    @property
    def failed(self) -> bool:
        return self.frame is None

    @property
    def retries(self) -> int:
        return len(self.attempts) - 1

    @property
    def timeouts(self) -> int:
        return sum(item.outcome == "timeout" for item in self.attempts)

    @property
    def prompt_tokens(self) -> int:
        return sum(item.prompt_tokens or 0 for item in self.attempts)

    @property
    def eval_tokens(self) -> int:
        return sum(item.eval_tokens or 0 for item in self.attempts)

    def record(self) -> dict[str, Any]:
        return {"attempts": [asdict(item) for item in self.attempts], "wall_ms": self.wall_ms,
                "frame": None if self.frame is None else self.frame.model_dump(mode="json"),
                "failed": self.failed}

    @classmethod
    def from_record(cls, record: dict[str, Any], profile: CatalogueProfile) -> "LogicalCall":
        """Replay: re-derive the frame from recorded raw responses with the frozen parser."""
        attempts = [Attempt(**item) for item in record["attempts"]]
        frame = None
        for item in attempts:
            if item.raw_response is None:
                continue
            outcome, parsed = parse_frame(item.raw_response, profile)
            if outcome != item.outcome:
                raise ValueError("recorded attempt outcome differs from replayed parse")
            if parsed is not None:
                frame = parsed
                break
        return cls(attempts, frame, record["wall_ms"])


def _ns_to_ms(value: Any) -> Optional[float]:
    return round(value / 1e6, 3) if isinstance(value, (int, float)) else None


class ModelFrontend:
    """Frozen-configuration Ollama client that returns one LogicalCall per query."""

    def __init__(self, prompt: PromptTemplate, *, transport: Transport = urllib_transport,
                 endpoint: str = ENDPOINT, clock: Callable[[], float] = time.perf_counter):
        self.prompt = prompt
        self.transport = transport
        self.endpoint = endpoint
        self.clock = clock

    def identity(self) -> dict[str, Any]:
        return {"frontend_version": MODEL_FRONTEND_VERSION, "model_id": MODEL_ID, "model_digest": MODEL_DIGEST,
                "prompt_version": self.prompt.label, "prompt_sha256": self.prompt.sha256}

    def request_payload(self, query: str, profile: CatalogueProfile) -> dict[str, Any]:
        return {"model": MODEL_ID, "prompt": render_prompt(self.prompt, profile, query), "stream": False,
                "format": frame_schema(profile), "options": dict(DECODING_OPTIONS), "keep_alive": KEEP_ALIVE}

    def verify_model(self) -> dict[str, Any]:
        try:
            status, body = self.transport("GET", f"{self.endpoint}/api/version", None, SERVICE_TIMEOUT_S)
            version = json.loads(body).get("version") if status == 200 else None
            status, body = self.transport("GET", f"{self.endpoint}/api/tags", None, SERVICE_TIMEOUT_S)
            models = json.loads(body).get("models", []) if status == 200 else []
        except (OSError, ValueError, AttributeError, http.client.HTTPException) as error:
            raise ModelUnavailable(f"local model service unavailable: {type(error).__name__}") from error
        if version != OLLAMA_VERSION:
            raise ModelUnavailable(f"Ollama version {version!r} differs from frozen {OLLAMA_VERSION}")
        match = [item for item in models if isinstance(item, dict) and item.get("name") == MODEL_ID]
        if not match or match[0].get("digest") != MODEL_DIGEST:
            raise ModelUnavailable("frozen model digest not available")
        return {"ollama_version": version, "model_id": MODEL_ID, "digest": MODEL_DIGEST}

    def _attempt(self, payload: dict[str, Any], profile: CatalogueProfile) -> tuple[Attempt, Optional[SemanticFrame]]:
        started = self.clock()

        def elapsed() -> float:
            return round((self.clock() - started) * 1000, 3)

        try:
            status, body = self.transport("POST", f"{self.endpoint}/api/generate", payload, ATTEMPT_TIMEOUT_S)
        except (TimeoutError, socket.timeout) as error:
            return Attempt("timeout", elapsed(), error=type(error).__name__), None
        except (OSError, http.client.HTTPException) as error:
            return Attempt("connection_error", elapsed(), error=type(error).__name__), None
        duration = elapsed()
        if status != 200:
            return Attempt("http_error", duration, http_status=status), None
        try:
            data = json.loads(body)
            text = data["response"]
            if not isinstance(text, str):
                raise TypeError("response is not text")
        except (ValueError, KeyError, TypeError) as error:
            return Attempt("http_error", duration, http_status=status, error=type(error).__name__), None
        outcome, frame = parse_frame(text, profile)
        return Attempt(outcome, duration, raw_response=text, http_status=status,
                       prompt_tokens=data.get("prompt_eval_count"), eval_tokens=data.get("eval_count"),
                       load_ms=_ns_to_ms(data.get("load_duration")),
                       total_ms=_ns_to_ms(data.get("total_duration"))), frame

    def call(self, query: str, profile: CatalogueProfile) -> LogicalCall:
        payload = self.request_payload(query, profile)
        started = self.clock()
        attempts: list[Attempt] = []
        frame = None
        for _ in range(MAX_ATTEMPTS):  # retry once on timeout, connection/HTTP error, non-JSON or schema failure
            attempt, frame = self._attempt(payload, profile)
            attempts.append(attempt)
            if frame is not None:
                break
        return LogicalCall(attempts, frame, round((self.clock() - started) * 1000, 3))


# ---------------------------------------------------------------- deterministic grounding

def _number_text(value: Union[int, float]) -> Optional[str]:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return str(value)
    if value != value or value in (float("inf"), float("-inf")):
        return None
    text = repr(float(value))
    if "e" in text:
        text = format(value, ".12f").rstrip("0")
        text = text + "0" if text.endswith(".") else text
    return text if NUMBER.fullmatch(text) else None


def _key(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", _singular(text.casefold()))


def _normalise_text(value: str, profile: FieldProfile) -> str:
    """Map a model-spelled text value onto the field's observed values when unambiguous."""
    domain = [json.loads(raw) for kind, raw in profile.distinct if kind == "str"]
    if value in domain:
        return value
    for match in (lambda d: d.casefold() == value.casefold(), lambda d: _key(d) == _key(value)):
        found = [item for item in domain if match(item)]
        if len(found) == 1:
            return found[0]
    return value


def _condition_text(profile: FieldProfile, value: Union[str, int, float]) -> Optional[str]:
    if profile.type in ("integer", "number"):
        if isinstance(value, str):
            value = value.strip()
            return value if NUMBER.fullmatch(value) else None
        return _number_text(value)
    if not isinstance(value, str):
        return None if profile.type == "string" else _number_text(value)
    value = _normalise_text(value.strip(), profile)
    return value if IDENT.fullmatch(value) else None


def infer_join_keys(regions: DatasetRef, values: DatasetRef, regions_profile: DatasetProfile,
                    values_profile: DatasetProfile) -> Optional[tuple[str, str]]:
    """Region key field and values key field: value overlap first, field-name rule as fallback."""
    candidates = []
    rows = regions_profile.rows
    for region_field in regions_profile.fields:
        if not rows or region_field.non_null != rows or len(region_field.distinct) != rows:
            continue  # a region key must be present and unique on every region
        region_values = set(region_field.distinct)
        for value_field in values_profile.fields:
            observed = set(value_field.distinct)
            if not observed or value_field.type not in ("string", "integer"):
                continue
            overlap = len(observed & region_values) / len(observed)
            if overlap >= 0.5:
                candidates.append((overlap, region_field.name, value_field.name))
    if candidates:
        best = max(item[0] for item in candidates)
        top = [item for item in candidates if item[0] == best]
        if len(top) == 1:
            return top[0][1], top[0][2]
    return _join_keys(regions, values)


def _phrase(text: str) -> str:
    return " ".join(_singular(word) for word in re.findall(r"[a-z0-9]+", str(text).casefold()))


def mention_positions(query: str, profile: CatalogueProfile) -> dict[str, Optional[int]]:
    """Earliest lexical reference to each dataset in the query: its role name, a field name
    found in no other dataset, or one of its text values (3+ characters); None if absent."""
    text = f" {_phrase(query)} "
    owners = Counter(f.name for item in profile.datasets for f in item.fields)
    positions: dict[str, Optional[int]] = {}
    for item in profile.datasets:
        terms = [item.role] + [f.name for f in item.fields if owners[f.name] == 1]
        terms += [json.loads(raw) for f in item.fields for kind, raw in f.distinct
                  if kind == "str" and len(json.loads(raw)) >= 3]
        found = [text.find(f" {_phrase(term)} ") for term in terms if _phrase(term)]
        found = [index for index in found if index >= 0]
        positions[item.role] = min(found) if found else None
    return positions


def ground_frame(frame: Optional[SemanticFrame], refs: Mapping[str, DatasetRef],
                 profile: CatalogueProfile, scope: str, query: str) -> FrontendDecision:
    """Deterministically turn a semantic frame into a FrontendDecision (M07 command)."""
    evidence = [f"frontend:{MODEL_FRONTEND_VERSION}", f"model:{MODEL_ID}"]

    def decline(status: str, operation: Optional[str], reason: str, *more: str) -> FrontendDecision:
        return _decline(status, operation, reason, evidence + list(more), MODEL_FRONTEND_VERSION)

    if frame is None:
        return decline("unsupported", None, "model_frontend_failed", "no schema-valid frame")
    evidence.append(f"frame_op:{frame.op}")
    if frame.op == "rank":
        tools = [spec.name for spec in ANALYSIS_TOOL_REGISTRY.describe()]
        if not any("rank" in name or "sort" in name for name in tools):
            return decline("unsupported", "rank", "unsupported_rank", "no registered ranking tool")
        return decline("unsupported", "rank", "unsupported_operation")
    if frame.op == "other":
        return decline("unsupported", None, "unsupported_operation")
    operation = OPERATION_NAMES.get(frame.op, frame.op)
    data = frame.data if frame.data in refs else None
    other = frame.other if frame.other in refs else None
    if data is None and other is None:
        return decline("clarification", operation, "missing_place", "no catalogue dataset named")
    positions = mention_positions(query, profile)
    if all(positions.get(role) is None for role in (data, other) if role is not None):
        return decline("clarification", operation, "missing_place", "no lexical reference to a bound dataset")
    words = set(_phrase(query).split())
    relation_hint = frame.relation
    if operation == "within" and words & PAIRING_VERBS:
        operation, relation_hint = "spatial_join", frame.relation or "within"
        evidence.append("operation_join_from_pairing_verb")
    if operation == "buffer":
        return decline("unsupported", "buffer", "unsupported_buffer", NO_PRODUCTION["buffer"])
    if operation == "search":
        assignment = {"role": data or other}
    else:
        if data is None or other is None or data == other:
            return decline("clarification", operation, "unknown_dataset")
        fields = {c.field for c in frame.where}
        if fields and not fields <= profile.dataset(data).names() and fields <= profile.dataset(other).names():
            data, other = other, data
            evidence.append("filtered_dataset_from_condition_fields")
        assignment = {"nearest": {"source": data, "target": other},
                      "within": {"source": data, "container": other},
                      "spatial_join": {"left": other, "right": data},
                      "aggregate_by_region": {"regions": other, "values": data}}[operation]
    roles = {slot: assignment[slot] for slot in ROLE_SLOTS[operation]}
    for slot, role in roles.items():
        evidence.append(f"role:{slot}->{role}@{refs[role].source}")
    filtered = profile.dataset(roles[FILTER_SLOT[operation]])
    where = list(frame.where)
    constrained = {item.field for item in where}
    text = f" {_phrase(query)} "
    for field_profile in filtered.fields:  # a single listed value named in the query is a condition
        if field_profile.listed and field_profile.name not in constrained:
            named = [value for value in field_profile.listed if f" {_phrase(value)} " in text
                     and _phrase(value) != _phrase(filtered.role)]  # the dataset's own name is no condition
            if len(named) == 1:
                where.append(FrameCondition(field=field_profile.name, cmp="=", value=named[0]))
                evidence.append(f"condition_from_value_mention:{field_profile.name}")
    conditions = []
    for item in where:
        field_profile = filtered.field(item.field)
        if field_profile is None:
            return decline("unsupported", operation, "unknown_field")
        text = _condition_text(field_profile, item.value)
        if text is None:
            return decline("unsupported", operation, "unexpressible_condition")
        conditions.append((item.field, item.cmp, text))
    extra: dict[str, Any] = {}
    if operation == "nearest":
        k = 1 if frame.k is None else frame.k
        if frame.k is None:
            evidence.append("k_default:1")
        if not 1 <= k <= 1000:
            return decline("unsupported", operation, "unexpressible_condition")
        extra = {"k": k}
    elif operation == "spatial_join":
        relation = relation_hint or "intersects"
        if relation_hint is None:
            evidence.append("relation_default:intersects")
        left, right = positions.get(roles["left"]), positions.get(roles["right"])
        if left is not None and right is not None:
            subject = roles["left"] if left <= right else roles["right"]
            evidence.append("relation_subject:first_mention")
        else:
            subject = frame.subject
        predicate = CONVERSE[relation] if subject == roles["right"] else relation
        extra = {"predicate": predicate}
    elif operation == "aggregate_by_region":
        regions, values = roles["regions"], roles["values"]
        keys = infer_join_keys(refs[regions], refs[values], profile.dataset(regions), profile.dataset(values))
        if keys is None:
            return decline("clarification", operation, "ambiguous_join_key")
        metric = frame.metric or "count"
        metric_field = None
        if metric != "count":
            numeric = [name for name in _numeric_fields(refs[values]) if name not in keys]
            if frame.metric_field in numeric:
                metric_field = frame.metric_field
            elif len(numeric) == 1:
                metric_field = numeric[0]
                evidence.append("metric_field_single_numeric")
            else:
                return decline("clarification", operation, "ambiguous_metric_field")
        extra = {"metric": metric, "metric_field": metric_field, "keys": keys}
    return render_request(operation, roles, conditions, refs, scope, evidence,
                          version=MODEL_FRONTEND_VERSION, **extra)
