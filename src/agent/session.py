"""M09 bounded in-memory conversation state over M07 plans and M08 outcomes."""
from __future__ import annotations

import re
import secrets
from dataclasses import dataclass
from threading import RLock
from typing import Literal, Optional

from pydantic import ConfigDict, Field, StrictStr

from src.agent.dynamic_planner import M07PlanRequest, PlanningResult, plan_request
from src.agent.validation import (
    ValidationOutcome, ValidationPolicy, execute_validated_plan, step_cache_key,
)
from src.contracts import StrictContract
from src.datasets import DatasetRef, DatasetRegistry, DatasetRegistryError


class SessionError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class M09Contract(StrictContract):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, strict=True)


class CountComparison(M09Contract):
    schema_version: Literal["1.0"] = "1.0"
    baseline_place: StrictStr
    comparison_place: StrictStr
    baseline_count: int = Field(ge=0)
    comparison_count: int = Field(ge=0)
    delta_count: int


class SessionSnapshot(M09Contract):
    schema_version: Literal["1.0"] = "1.0"
    session_id: StrictStr
    version: int = Field(ge=1)
    request: M07PlanRequest
    plan: PlanningResult
    bindings: dict[StrictStr, DatasetRef]
    outcome: ValidationOutcome
    output_format: Literal["summary", "geojson"] = "summary"
    distance_unit: Optional[Literal["m", "km"]] = None
    comparison: Optional[CountComparison] = None


class SessionUpdate(M09Contract):
    schema_version: Literal["1.0"] = "1.0"
    accepted: bool
    action: Literal["start", "change_distance", "add_exclusion", "switch_city", "compare", "change_format"]
    snapshot: SessionSnapshot
    reused_steps: list[StrictStr] = Field(default_factory=list)
    executed_steps: list[StrictStr] = Field(default_factory=list)
    attempt_error_code: Optional[StrictStr] = None
    attempt_outcome: Optional[ValidationOutcome] = None


@dataclass
class _Record:
    snapshot: SessionSnapshot
    cache: dict[str, DatasetRef]
    policy: ValidationPolicy


class SessionManager:
    """Small process-local store. A session token is an opaque bearer capability."""

    def __init__(self, datasets: DatasetRegistry, *, max_sessions: int = 32):
        if type(max_sessions) is not int or not 1 <= max_sessions <= 256:
            raise ValueError("max_sessions must be 1..256")
        self.datasets = datasets
        self.max_sessions = max_sessions
        self._sessions: dict[str, _Record] = {}
        self._lock = RLock()

    def _current(self, ref: DatasetRef) -> DatasetRef:
        try:
            current = self.datasets.get(ref.id, verify=True)
            if current != ref:
                raise ValueError("stale DatasetRef")
            self.datasets.read(ref.id)
            return current
        except (DatasetRegistryError, ValueError) as error:
            raise SessionError("M09_DATASET_STALE", "Session DatasetRef is no longer current") from error

    def _cache(self, plan: PlanningResult, outcome: ValidationOutcome) -> dict[str, DatasetRef]:
        cache: dict[str, DatasetRef] = {}
        if outcome.status != "success":
            return cache
        records = {item.step_id: item for item in outcome.step_records if item.status == "succeeded"}
        for step in plan.tool_steps:
            record = records.get(step.step_id)
            if record is None or record.output_id is None or len(record.input_ids) != len(step.input_roles):
                continue
            try:
                inputs = [self.datasets.get(item, verify=True) for item in record.input_ids]
                output = self.datasets.get(record.output_id, verify=True)
            except DatasetRegistryError:
                continue
            cache[step_cache_key(step, inputs)] = output
        return cache

    @staticmethod
    def _steps(outcome: ValidationOutcome) -> tuple[list[str], list[str]]:
        return (
            [item.step_id for item in outcome.step_records if item.status == "succeeded" and item.attempts == 0],
            [item.step_id for item in outcome.step_records if item.status == "succeeded" and item.attempts > 0],
        )

    @staticmethod
    def _planned(request: M07PlanRequest) -> PlanningResult:
        plan = plan_request(request)
        if plan.status != "plan":
            raise SessionError(plan.error_code or "M09_PLAN_INCOMPLETE", "Turn has no valid M07 plan")
        return plan

    def start(
        self, request: M07PlanRequest | dict, bindings: dict[str, DatasetRef],
        *, policy: ValidationPolicy,
    ) -> SessionUpdate:
        parsed = request if isinstance(request, M07PlanRequest) else M07PlanRequest.model_validate(request)
        plan = self._planned(parsed)
        if set(bindings) != set(parsed.datasets):
            raise SessionError("M09_BINDINGS_INVALID", "Bindings must exactly match declared roles")
        with self._lock:
            if len(self._sessions) >= self.max_sessions:
                raise SessionError("M09_SESSION_LIMIT", "Active session limit reached")
            outcome = execute_validated_plan(parsed, plan, bindings, self.datasets, policy=policy)
            if outcome.status != "success":
                raise SessionError("M09_INITIAL_RESULT_INVALID", outcome.error_code or "Initial analysis failed")
            token = secrets.token_urlsafe(24)
            while token in self._sessions:
                token = secrets.token_urlsafe(24)
            snapshot = SessionSnapshot(
                session_id=token, version=1, request=parsed, plan=plan,
                bindings=dict(bindings), outcome=outcome,
                distance_unit=self._distance_unit(parsed.query),
            )
            self._sessions[token] = _Record(snapshot, self._cache(plan, outcome), policy)
            reused, executed = self._steps(outcome)
            return SessionUpdate(accepted=True, action="start", snapshot=snapshot, reused_steps=reused, executed_steps=executed)

    def get(self, session_id: str) -> SessionSnapshot:
        with self._lock:
            record = self._sessions.get(session_id)
            if record is None:
                raise SessionError("M09_SESSION_UNKNOWN", "Unknown or closed session")
            return record.snapshot.model_copy(deep=True)

    def close(self, session_id: str) -> None:
        with self._lock:
            if self._sessions.pop(session_id, None) is None:
                raise SessionError("M09_SESSION_UNKNOWN", "Unknown or closed session")

    def context_summary(self, session_id: str) -> dict:
        """Bounded metadata for a possible later model call; no rows or chat history."""
        snap = self.get(session_id)
        return {
            "schema_version": "1.0", "version": snap.version,
            "task_class": snap.plan.task_class,
            "place": snap.plan.intent.place.name,
            "parameters": snap.plan.business_parameters,
            "dataset_roles": sorted(snap.bindings),
            "result_count": snap.outcome.final_dataset.row_count,
            "output_format": snap.output_format,
            "distance_unit": snap.distance_unit,
        }

    @staticmethod
    def _distance_unit(query: str) -> str | None:
        matched = re.search(r"\bwithin [0-9]+(?:\.[0-9]+)? (m|km) of\b", query, re.I)
        return matched.group(1).lower() if matched else None

    @staticmethod
    def _parse_turn(turn: str) -> tuple[str, re.Match[str]]:
        if not isinstance(turn, str) or len(turn) > 200:
            raise SessionError("M09_TURN_UNSUPPORTED", "Turn must be a short text command")
        patterns = (
            ("change_distance", r"change distance to (?P<distance>[0-9]+(?:\.[0-9]+)?) (?P<unit>m|km)"),
            ("add_exclusion", r"exclude (?P<field>[A-Za-z_][A-Za-z0-9_]*) = (?P<value>[A-Za-z_][A-Za-z0-9_]*)"),
            ("switch_city", r"switch city to (?P<city>[A-Za-z][A-Za-z -]*)"),
            ("compare", r"compare with (?P<city>[A-Za-z][A-Za-z -]*)"),
            ("change_format", r"change output format to (?P<format>summary|geojson)"),
        )
        for action, pattern in patterns:
            matched = re.fullmatch(pattern, turn.strip(), re.I)
            if matched is not None:
                return action, matched
        raise SessionError("M09_TURN_UNSUPPORTED", "Turn is outside the bounded M09 grammar")

    @staticmethod
    def _with_query(request: M07PlanRequest, query: str, *, fields: tuple[str, str] | None = None) -> M07PlanRequest:
        data = request.model_dump(mode="json")
        data["query"] = query
        if fields is not None:
            role, field = fields
            required = data["datasets"][role]["required_fields"]
            if field not in required:
                required.append(field)
        return M07PlanRequest.model_validate(data)

    @staticmethod
    def _city_request(
        old: SessionSnapshot, city: str, supplied: M07PlanRequest | dict | None,
        bindings: dict[str, DatasetRef] | None,
    ) -> M07PlanRequest:
        if supplied is None or bindings is None:
            raise SessionError("M09_SOURCE_REQUIRED", "City change requires explicit request and bindings")
        candidate = supplied if isinstance(supplied, M07PlanRequest) else M07PlanRequest.model_validate(supplied)
        old_place = old.plan.intent.place.name
        suffix = " in " + old_place
        if not old.request.query.lower().endswith(suffix.lower()):
            raise SessionError("M09_PLAN_INCOMPATIBLE", "Current query has no replaceable place")
        expected_query = old.request.query[:-len(suffix)] + " in " + city
        if candidate.query != expected_query or set(candidate.datasets) != set(old.request.datasets) or set(bindings) != set(candidate.datasets):
            raise SessionError("M09_SOURCE_REQUIRED", "New request must name the city and bind every role")
        for role, descriptor in candidate.datasets.items():
            previous = old.request.datasets[role]
            ref = bindings[role]
            if descriptor.source == previous.source or ref.id == old.bindings[role].id or ref.source != descriptor.source:
                raise SessionError("M09_SOURCE_REQUIRED", "City requires new source identity and DatasetRef per role")
        return candidate

    def apply(
        self, session_id: str, expected_version: int, turn: str, *,
        new_request: M07PlanRequest | dict | None = None,
        new_bindings: dict[str, DatasetRef] | None = None,
    ) -> SessionUpdate:
        action, match = self._parse_turn(turn)
        with self._lock:
            record = self._sessions.get(session_id)
            if record is None:
                raise SessionError("M09_SESSION_UNKNOWN", "Unknown or closed session")
            old = record.snapshot
            if type(expected_version) is not int or expected_version != old.version:
                raise SessionError("M09_VERSION_CONFLICT", "Session version changed")
            if old.outcome.status != "success" or old.outcome.final_dataset is None:
                raise SessionError("M09_RESULT_UNCONFIRMED", "No confirmed result to modify")
            if action == "change_format":
                if new_request is not None or new_bindings is not None:
                    raise SessionError("M09_PARAMETER_CONFLICT", "Format change cannot change source")
                self._current(old.outcome.final_dataset)
                updated = old.model_copy(update={
                    "version": old.version + 1, "output_format": match.group("format").lower(),
                    "comparison": None,
                })
                record.snapshot = updated
                return SessionUpdate(accepted=True, action=action, snapshot=updated)

            bindings = dict(old.bindings)
            if action == "change_distance":
                if old.plan.task_class != "within_distance" or new_request is not None or new_bindings is not None:
                    raise SessionError("M09_TURN_UNSUPPORTED", "Distance edit requires current distance task")
                query = re.sub(
                    r"\bwithin [0-9]+(?:\.[0-9]+)? (?:m|km) of\b",
                    f'within {match.group("distance")} {match.group("unit").lower()} of',
                    old.request.query, count=1, flags=re.I,
                )
                if query == old.request.query:
                    raise SessionError("M09_PARAMETER_CONFLICT", "Distance did not change")
                request = self._with_query(old.request, query)
            elif action == "add_exclusion":
                if old.plan.task_class != "within_distance" or new_request is not None or new_bindings is not None:
                    raise SessionError("M09_TURN_UNSUPPORTED", "Exclusion requires current distance task")
                role = old.plan.business_parameters["target"]
                field = match.group("field")
                ref = self._current(bindings[role])
                if field not in {item.name for item in ref.fields}:
                    raise SessionError("M09_FIELD_UNKNOWN", "Exclusion field absent from verified source")
                suffix = " in " + old.plan.intent.place.name
                if not old.request.query.lower().endswith(suffix.lower()):
                    raise SessionError("M09_PLAN_INCOMPATIBLE", "Current query has no replaceable place")
                stem = old.request.query[:-len(suffix)]
                stem = re.sub(r" excluding [A-Za-z_][A-Za-z0-9_]* = [A-Za-z_][A-Za-z0-9_]*$", "", stem, flags=re.I)
                query = stem + f' excluding {field} = {match.group("value")}' + suffix
                if query == old.request.query:
                    raise SessionError("M09_PARAMETER_CONFLICT", "Exclusion did not change")
                request = self._with_query(old.request, query, fields=(role, field))
            elif action in {"switch_city", "compare"}:
                request = self._city_request(old, match.group("city").strip(), new_request, new_bindings)
                bindings = dict(new_bindings)
            else:
                raise SessionError("M09_TURN_UNSUPPORTED", "Unsupported edit")

            plan = self._planned(request)
            cache = record.cache if action in {"change_distance", "add_exclusion"} else {}
            outcome = execute_validated_plan(request, plan, bindings, self.datasets, policy=record.policy, cached_outputs=cache)
            if outcome.status != "success":
                return SessionUpdate(accepted=False, action=action, snapshot=old, attempt_error_code=outcome.error_code, attempt_outcome=outcome)
            reused, executed = self._steps(outcome)
            if action == "compare":
                self._current(old.outcome.final_dataset)
                comparison = CountComparison(
                    baseline_place=old.plan.intent.place.name,
                    comparison_place=plan.intent.place.name,
                    baseline_count=old.outcome.final_dataset.row_count,
                    comparison_count=outcome.final_dataset.row_count,
                    delta_count=outcome.final_dataset.row_count - old.outcome.final_dataset.row_count,
                )
                updated = old.model_copy(update={"version": old.version + 1, "comparison": comparison})
            else:
                updated = old.model_copy(update={
                    "version": old.version + 1, "request": request, "plan": plan,
                    "bindings": bindings, "outcome": outcome, "comparison": None,
                    "distance_unit": self._distance_unit(request.query),
                })
                record.cache = self._cache(plan, outcome)
            record.snapshot = updated
            return SessionUpdate(
                accepted=True, action=action, snapshot=updated,
                reused_steps=reused, executed_steps=executed,
            )

