"""M11c deterministic front-end: benchmark English -> M07 constrained commands.

Offline stand-in for the semantic-parsing role a language model would play. Its
only inputs are the query text, a declared dataset catalogue (role -> DatasetRef)
and the catalogue scope label. Recognisers were written from public M11b dev
phrasing only; they bind dataset roles and fields from the query and the
catalogue schema, then hand an M07 command to the planner, which still decides
whether the request is plannable. Version: m11c-frontend-v1.
"""
from __future__ import annotations

import re
from typing import Any, Literal, Mapping, Optional

from pydantic import ConfigDict, Field, StrictStr

from src.agent.dynamic_planner import OPERATORS, M07PlanRequest
from src.contracts import StrictContract
from src.datasets import DatasetRef
from src.tools import ANALYSIS_TOOL_REGISTRY

FRONTEND_VERSION = "m11c-frontend-v1"
# m11c-protocol-v2 model front-end (src.agent.model_frontend) renders through render_request below.
MODEL_FRONTEND_VERSION = "m11c-frontend-v2"
NUMBER = r"-?[0-9]+(?:\.[0-9]+)?"
IDENT = r"[A-Za-z_][A-Za-z0-9_]*"
NOUN = r"[A-Za-z][A-Za-z0-9_]*"
VERBS = frozenset({"search", "find", "join", "aggregate", "make", "rank",
                   "list", "show", "count", "buffer"})
WORD_OPERATORS = {"eq": "=", "ne": "!=", "gt": ">", "gte": ">=", "lt": "<", "lte": "<="}
PHRASE_OPERATORS = {"at least": ">=", "at most": "<=", "greater than": ">",
                    "more than": ">", "less than": "<", "fewer than": "<",
                    "equal to": "="}
RECOGNISERS = (
    ("rank", re.compile(r"^rank\b", re.I)),
    ("search", re.compile(rf"^search (?:synthetic )?(?P<role>{NOUN}): (?P<conditions>.+)$", re.I)),
    ("buffer", re.compile(rf"^make an? (?:fixed )?(?P<distance>{NUMBER}) (?:metres?|meters?|m) buffer of (?:synthetic )?(?P<role>{NOUN})$", re.I)),
    ("nearest", re.compile(rf"^find (?P<k>[1-9][0-9]*) nearest (?:synthetic )?(?P<target>{NOUN}) (?:for|to) (?P<source>{NOUN})(?: with (?P<conditions>.+))?$", re.I)),
    ("within", re.compile(rf"^find (?:synthetic )?(?P<source>{NOUN})(?: with (?P<conditions>.+?))? (?:strictly )?within (?P<container>{NOUN})$", re.I)),
    ("spatial_join", re.compile(rf"^join (?:synthetic )?(?P<left>{NOUN}) (?:to|with) (?P<right>{NOUN})(?: with (?P<conditions>.+?))? (?:by|on) (?P<predicate>intersects|within|contains)$", re.I)),
    ("aggregate_by_region", re.compile(rf"^aggregate (?:synthetic )?(?P<regions>{NOUN}) (?P<values>{NOUN}) by (?P<metric>count|sum|mean|min|max)(?: after (?P<conditions>.+))?$", re.I)),
)
# Operations with no planner production are declined before dataset grounding.
NO_PRODUCTION = {"buffer": "single-dataset buffer has no m07.2 production (GeoDSL v1 within_distance needs two datasets)"}
ROLE_SLOTS = {
    "search": ("role",), "nearest": ("source", "target"), "within": ("source", "container"),
    "spatial_join": ("left", "right"), "aggregate_by_region": ("regions", "values"),
}
FILTER_SLOT = {"search": "role", "nearest": "source", "within": "source",
               "spatial_join": "right", "aggregate_by_region": "values"}


class FrontendDecision(StrictContract):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, strict=True)
    schema_version: Literal["1.0"] = "1.0"
    frontend_version: Literal["m11c-frontend-v1", "m11c-frontend-v2"] = FRONTEND_VERSION
    status: Literal["request", "clarification", "unsupported"]
    operation: Optional[StrictStr] = None
    reason_code: Optional[StrictStr] = None
    command: Optional[StrictStr] = None
    request: Optional[M07PlanRequest] = None
    # Structured grounding of the same interpretation, for systems without a planner.
    roles: dict[StrictStr, StrictStr] = Field(default_factory=dict)
    conditions: list[dict[StrictStr, Any]] = Field(default_factory=list)
    parameters: dict[StrictStr, Any] = Field(default_factory=dict)
    evidence: list[StrictStr] = Field(default_factory=list)


def _singular(word: str) -> str:
    word = word.casefold()
    return word[:-1] if len(word) > 3 and word.endswith("s") else word


def _bind(noun: str, catalogue: Mapping[str, DatasetRef]) -> Optional[str]:
    matches = [role for role in catalogue if _singular(role) == _singular(noun)]
    return matches[0] if len(matches) == 1 else None


def _conditions(text: str) -> Optional[list[tuple[str, str, str]]]:
    parsed = []
    for clause in re.split(r", | and ", text.strip(), flags=re.I):
        words = re.fullmatch(rf"(?P<field>{IDENT})(?: (?P<op>eq|ne|gt|gte|lt|lte))? (?P<value>{NUMBER}|{IDENT})", clause, re.I)
        phrase = re.fullmatch(rf"(?P<field>{IDENT}) (?P<op>{'|'.join(PHRASE_OPERATORS)}) (?P<value>{NUMBER})", clause, re.I)
        if phrase:
            parsed.append((phrase["field"], PHRASE_OPERATORS[phrase["op"].casefold()], phrase["value"]))
        elif words:
            parsed.append((words["field"], WORD_OPERATORS[(words["op"] or "eq").casefold()], words["value"]))
        else:
            return None
    return parsed


def _typed(text: str) -> Any:
    """Same literal typing as the M07 condition parser."""
    if re.fullmatch(r"-?[0-9]+", text):
        return int(text)
    if re.fullmatch(r"-?[0-9]+\.[0-9]+", text):
        return float(text)
    return text


def _numeric_fields(ref: DatasetRef) -> list[str]:
    return [item.name for item in ref.fields if item.type in {"integer", "number"}]


def _join_keys(regions: DatasetRef, values: DatasetRef) -> Optional[tuple[str, str]]:
    pairs = [(left.name, right.name) for left in regions.fields for right in values.fields
             if right.name == left.name or right.name.endswith("_" + left.name)]
    return pairs[0] if len(pairs) == 1 else None


def _decline(status: str, operation: Optional[str], reason: str, evidence: list[str],
             version: str = FRONTEND_VERSION) -> FrontendDecision:
    return FrontendDecision(status=status, operation=operation, reason_code=reason, evidence=evidence,
                            frontend_version=version)


def render_request(operation: str, roles: dict[str, str], conditions: list[tuple[str, str, str]],
                   catalogue: Mapping[str, DatasetRef], scope: str, evidence: list[str], *,
                   k: Optional[int] = None, predicate: Optional[str] = None, metric: Optional[str] = None,
                   metric_field: Optional[str] = None, keys: Optional[tuple[str, str]] = None,
                   version: str = FRONTEND_VERSION) -> FrontendDecision:
    """Render a grounded interpretation (slot -> role, `FIELD OP VALUE` text triples) as an M07 command."""
    filter_role = roles[FILTER_SLOT[operation]]
    fields: dict[str, set[str]] = {role: set() for role in roles.values()}
    present = {role: {item.name for item in catalogue[role].fields} for role in fields}
    fields[filter_role].update(f for f, _, _ in conditions if f in present[filter_role])
    where = " and ".join(f"{f} {op} {v}" for f, op, v in conditions)
    where = f" where {where}" if where else ""
    parameters: dict[str, Any] = {}
    if operation == "search":
        command = f"search {filter_role}{where}"
    elif operation == "nearest":
        command = f"find {k} nearest {roles['target']} to {roles['source']}{where}"
        parameters = {"k": k}
    elif operation == "within":
        command = f"find {roles['source']}{where} within {roles['container']}"
    elif operation == "spatial_join":
        command = f"join {roles['left']} with {roles['right']}{where} on {predicate}"
        parameters = {"predicate": predicate}
    else:
        regions, values = roles["regions"], roles["values"]
        field = f" {metric_field}" if metric_field else ""
        if metric_field:
            fields[values].add(metric_field)
        fields[regions].add(keys[0])
        fields[values].add(keys[1])
        evidence.append(f"join_key:{regions}.{keys[0]}={values}.{keys[1]}")
        command = f"aggregate {metric}{field} by {keys[0]} from {values}{where} into {regions} using {keys[1]}"
        parameters = {"metric": metric, "metric_field": metric_field,
                      "region_id_field": keys[0], "values_region_id_field": keys[1]}
    command = f"{command} in {scope}"
    crs = catalogue[next(iter(fields))].crs
    request = M07PlanRequest(
        query=command,
        datasets={role: {"kind": catalogue[role].kind, "source": f"{catalogue[role].source}:{role}",
                         "crs": catalogue[role].crs, "required_fields": sorted(names)}
                  for role, names in fields.items()},
        place_crs=crs, analysis_crs=crs, output_crs=crs,
    )
    return FrontendDecision(
        status="request", operation=operation, command=command, request=request,
        roles=dict(roles),
        conditions=[{"field": f, "operator": OPERATORS[op], "value": _typed(v)} for f, op, v in conditions],
        parameters=parameters, evidence=evidence, frontend_version=version)


def interpret(query: str, catalogue: Mapping[str, DatasetRef], scope: str) -> FrontendDecision:
    text = " ".join(str(query).split()).rstrip(".?!")
    evidence = [f"frontend:{FRONTEND_VERSION}"]
    for operation, pattern in RECOGNISERS:
        found = pattern.fullmatch(text) if operation != "rank" else pattern.match(text)
        if found:
            break
    else:
        tokens = re.findall(NOUN, text)
        bound = [token for token in tokens if _bind(token, catalogue)]
        if tokens and tokens[0].casefold() in VERBS and not bound:
            return _decline("clarification", None, "missing_place", evidence + ["no catalogue dataset named"])
        return _decline("unsupported", None, "unparsed_query", evidence + ["no recogniser matched"])
    evidence.append(f"operation:{operation}")
    if operation == "rank":
        tools = [spec.name for spec in ANALYSIS_TOOL_REGISTRY.describe()]
        if not any("rank" in name or "sort" in name for name in tools):
            return _decline("unsupported", "rank", "unsupported_rank", evidence + ["no registered ranking tool"])
        return _decline("unsupported", "rank", "unparsed_query", evidence)
    if operation in NO_PRODUCTION:
        return _decline("unsupported", operation, f"unsupported_{operation}", evidence + [NO_PRODUCTION[operation]])
    slots = ROLE_SLOTS[operation]
    roles = {slot: _bind(found[slot], catalogue) for slot in slots}
    if not any(roles.values()):
        return _decline("clarification", operation, "missing_place", evidence + ["no catalogue dataset named"])
    if not all(roles.values()):
        return _decline("clarification", operation, "unknown_dataset",
                        evidence + [f"unbound:{found[s]}" for s in slots if roles[s] is None])
    for slot in slots:
        evidence.append(f"role:{found[slot]}->{roles[slot]}@{catalogue[roles[slot]].source}")
    conditions = _conditions(found["conditions"]) if found["conditions"] else []
    if conditions is None:
        return _decline("unsupported", operation, "unexpressible_condition", evidence)
    extra: dict[str, Any] = {}
    if operation == "nearest":
        extra = {"k": int(found["k"])}
    elif operation == "spatial_join":
        extra = {"predicate": found["predicate"].lower()}
    elif operation == "aggregate_by_region":
        keys = _join_keys(catalogue[roles["regions"]], catalogue[roles["values"]])
        if keys is None:
            return _decline("clarification", operation, "ambiguous_join_key", evidence)
        metric = found["metric"].lower()
        metric_field = None
        if metric != "count":
            numeric = [f for f in _numeric_fields(catalogue[roles["values"]]) if f not in keys]
            if len(numeric) == 1:
                metric_field = numeric[0]
        extra = {"metric": metric, "metric_field": metric_field, "keys": keys}
    return render_request(operation, roles, conditions, catalogue, scope, evidence, **extra)
