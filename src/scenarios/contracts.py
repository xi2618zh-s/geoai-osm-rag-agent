"""M12 scenario-layer contracts v1.0 (ADR-0021).

Strict, ``extra="forbid"`` models for catalogues, restricted-command clarifications,
typed scenario plans and execution outcomes. M08 ``RecoveryEvent`` and
``ValidationFinding`` are reused unchanged; GeoDSL ``ClarificationRequest`` is not
reused because its reason codes are a closed enumeration.
"""
from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import ConfigDict, Field, StrictBool, StrictFloat, StrictStr, field_validator, model_validator

from src.agent.validation import RecoveryEvent, ValidationFinding
from src.contracts import StrictContract
from src.datasets import DatasetRef

SCHEMA_VERSION = "1.0"
MAX_PLAN_STEPS = 12
MAX_TOOL_CALLS = 64
CRS_PATTERN = r"^EPSG:[1-9][0-9]{2,5}$"
ROLE_PATTERN = r"^[a-z][a-z0-9_]{0,63}$"
INTERNAL_CHECK_TOOLS = frozenset({"validate_geometry"})


class ScenarioError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class ScenarioContract(StrictContract):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, strict=True)


class CrsEvidence(ScenarioContract):
    """Pointer to a versioned manifest entry that records a file's source CRS."""
    manifest: StrictStr = Field(min_length=1, max_length=512)
    file: StrictStr = Field(min_length=1, max_length=256)


class CrsReconfirmation(ScenarioContract):
    manifest: StrictStr
    manifest_sha256: StrictStr = Field(pattern=r"^[a-f0-9]{64}$")
    file: StrictStr
    crs: StrictStr = Field(pattern=CRS_PATTERN)
    previous_dataset_id: StrictStr


class CatalogueEntry(ScenarioContract):
    role: StrictStr = Field(pattern=ROLE_PATTERN)
    semantic_type: Literal["scb_population_grid_raw", "poi_points"]
    kind: Literal["vector", "network"]
    declared_crs: StrictStr = Field(pattern=CRS_PATTERN)
    dataset: DatasetRef
    required_fields: list[StrictStr] = Field(default_factory=list, max_length=64)
    license: StrictStr = Field(min_length=1, max_length=64)
    attribution: StrictStr = Field(min_length=1, max_length=200)
    provider_verified: StrictBool
    temporal_note: StrictStr = Field(min_length=1, max_length=300)
    reconfirmation: Optional[CrsReconfirmation] = None


class ScenarioCatalogue(ScenarioContract):
    schema_version: Literal["1.0"] = SCHEMA_VERSION
    catalogue_id: StrictStr = Field(min_length=1, max_length=128)
    place: StrictStr = Field(min_length=1, max_length=100)
    study_bbox: list[StrictFloat] = Field(min_length=4, max_length=4)
    study_crs: StrictStr = Field(pattern=CRS_PATTERN)
    analysis_crs: StrictStr = Field(pattern=CRS_PATTERN)
    entries: dict[StrictStr, CatalogueEntry] = Field(min_length=1, max_length=16)

    @model_validator(mode="after")
    def check_entries(self):
        if any(role != entry.role for role, entry in self.entries.items()):
            raise ValueError("catalogue keys must equal entry roles")
        x0, y0, x1, y1 = self.study_bbox
        if not (x0 < x1 and y0 < y1):
            raise ValueError("study_bbox must have positive extent")
        return self

    def with_entry(self, role: str, **updates: Any) -> "ScenarioCatalogue":
        entries = dict(self.entries)
        entries[role] = entries[role].model_copy(update=updates)
        return self.model_copy(update={"entries": entries})


ClarificationReason = Literal[
    "missing_parameters", "distance_out_of_range", "age_band_unsupported",
    "threshold_out_of_range", "place_not_covered",
]


class ScenarioClarification(ScenarioContract):
    schema_version: Literal["1.0"] = SCHEMA_VERSION
    original_command: StrictStr = Field(min_length=1, max_length=2000)
    reason_code: ClarificationReason
    question: StrictStr = Field(min_length=1, max_length=1000)
    missing_fields: list[StrictStr] = Field(min_length=1, max_length=8)
    choices: list[StrictStr] = Field(default_factory=list, max_length=16)

    @field_validator("missing_fields", "choices")
    @classmethod
    def unique(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("clarification items must be unique")
        return value


class ScenarioIntent(ScenarioContract):
    schema_version: Literal["1.0"] = SCHEMA_VERSION
    scenario: Literal["m12a_school_coverage_gaps"]
    command: StrictStr = Field(min_length=1, max_length=2000)
    place: StrictStr = Field(min_length=1, max_length=100)
    parameters: dict[StrictStr, Any]


class FrontendResult(ScenarioContract):
    schema_version: Literal["1.0"] = SCHEMA_VERSION
    status: Literal["plan", "clarification", "unsupported"]
    grammar_version: StrictStr
    command: StrictStr
    intent: Optional[ScenarioIntent] = None
    clarification: Optional[ScenarioClarification] = None
    reason_code: Optional[StrictStr] = None

    @model_validator(mode="after")
    def consistent(self):
        if self.status == "plan" and self.intent is None:
            raise ValueError("plan requires intent")
        if self.status == "clarification" and self.clarification is None:
            raise ValueError("clarification requires ScenarioClarification")
        if self.status == "unsupported" and self.reason_code is None:
            raise ValueError("unsupported requires reason_code")
        return self


class ScenarioStep(ScenarioContract):
    step_id: StrictStr = Field(pattern=r"^step_(?:[1-9]|1[0-2])$")
    tool: StrictStr = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    depends_on: list[StrictStr] = Field(default_factory=list, max_length=12)
    input_roles: list[StrictStr] = Field(min_length=1, max_length=4)
    output_role: StrictStr = Field(pattern=ROLE_PATTERN)
    parameters: dict[StrictStr, Any] = Field(default_factory=dict)
    empty_policy: Literal["allow", "reject"]
    rationale: StrictStr = Field(min_length=1, max_length=300)


class ScenarioPlan(ScenarioContract):
    schema_version: Literal["1.0"] = SCHEMA_VERSION
    plan_version: StrictStr
    scenario: StrictStr
    intent: ScenarioIntent
    steps: list[ScenarioStep] = Field(min_length=1, max_length=MAX_PLAN_STEPS)
    preview: list[StrictStr] = Field(min_length=1, max_length=MAX_PLAN_STEPS)


class StepRecord(ScenarioContract):
    step_id: StrictStr
    tool: StrictStr
    status: Literal["succeeded", "failed"]
    attempts: int = Field(ge=0, le=2)
    input_ids: list[StrictStr]
    output_id: Optional[StrictStr] = None
    error_code: Optional[StrictStr] = None
    warnings: list[StrictStr] = Field(default_factory=list)


class ScenarioOutcome(ScenarioContract):
    schema_version: Literal["1.0"] = SCHEMA_VERSION
    status: Literal["success", "partial"]
    first_pass_success: StrictBool
    plan: ScenarioPlan
    initial_plan_tools: list[StrictStr]
    bindings: dict[StrictStr, DatasetRef] = Field(default_factory=dict)
    outputs: dict[StrictStr, DatasetRef] = Field(default_factory=dict)
    final_dataset: Optional[DatasetRef] = None
    error_code: Optional[StrictStr] = None
    failed_step: Optional[StrictStr] = None
    findings: list[ValidationFinding] = Field(default_factory=list)
    recoveries: list[RecoveryEvent] = Field(default_factory=list)
    step_records: list[StepRecord] = Field(default_factory=list)
    tool_calls: list[StrictStr] = Field(default_factory=list, max_length=MAX_TOOL_CALLS)
    replan_count: int = Field(ge=0, le=1)
    limitations: list[StrictStr] = Field(default_factory=list)

    @model_validator(mode="after")
    def consistent(self):
        if self.status == "success" and (self.final_dataset is None or self.error_code is not None):
            raise ValueError("success needs a final dataset and no error")
        if self.status == "partial" and (self.error_code is None or not self.limitations):
            raise ValueError("partial needs an error code and a limitation")
        return self

    @property
    def invoked_tools(self) -> list[str]:
        """Analysis tools in invocation order, excluding internal validation checks."""
        return [name for name in self.tool_calls if name not in INTERNAL_CHECK_TOOLS]

    @property
    def tool_call_count(self) -> int:
        return len(self.tool_calls)
