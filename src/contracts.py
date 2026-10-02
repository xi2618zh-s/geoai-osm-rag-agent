"""Strict request and model-output contracts for the GeoAI service."""

from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, StrictStr, field_validator

from src.config import DEFAULT_PLACE, OLLAMA_MODEL


class StrictContract(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class OSMTag(StrictContract):
    key: StrictStr = Field(min_length=1, max_length=128)
    value: StrictStr = Field(min_length=1, max_length=256)


class LLMDecision(StrictContract):
    place: Optional[StrictStr]
    tag: OSMTag
    confidence: float = Field(ge=0.0, le=1.0)
    explanation: StrictStr = Field(max_length=2000)


class ChatRequest(StrictContract):
    query: StrictStr = Field(min_length=1, max_length=2000)
    model: StrictStr = Field(default=OLLAMA_MODEL, min_length=1, max_length=200)

    @field_validator("query", "model")
    @classmethod
    def reject_whitespace_only(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must not be blank")
        return value.strip()


class SimpleChatRequest(StrictContract):
    query: StrictStr = Field(default="", max_length=2000)
    place: StrictStr = Field(default=DEFAULT_PLACE, min_length=1, max_length=300)
    key: StrictStr = Field(min_length=1, max_length=128)
    value: StrictStr = Field(min_length=1, max_length=256)

    @field_validator("place", "key", "value")
    @classmethod
    def reject_whitespace_only(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must not be blank")
        return value.strip()


class ErrorResponse(StrictContract):
    status: Literal["error"] = "error"
    error_code: StrictStr
    message: StrictStr
    trace_id: StrictStr
    query: Optional[StrictStr] = None
    timings_ms: dict[str, float] = Field(default_factory=dict)
    details: list[dict[str, Any]] = Field(default_factory=list)


class ChatSuccessResponse(StrictContract):
    status: Literal["success"] = "success"
    message: StrictStr
    query: StrictStr
    place: StrictStr
    chosen_tag: StrictStr
    count: int = Field(ge=0)
    geojson_url: StrictStr
    evidence: list[dict[str, Any]]
    llm_ok: bool
    llm_confidence: float = Field(ge=0.0, le=1.0)
    counts_by_type: dict[str, int]
    decision_source: StrictStr
    clip_cache_hit: bool
    trace_id: StrictStr
    timings_ms: dict[str, float]


class SimpleChatSuccessResponse(StrictContract):
    status: Literal["success"] = "success"
    message: StrictStr
    geojson_url: StrictStr
    count: int = Field(ge=0)
    counts_by_type: dict[str, int]
    decision_source: StrictStr
    clip_cache_hit: bool
    trace_id: StrictStr
    timings_ms: dict[str, float]


def llm_decision_is_valid(data, evidence=None) -> bool:
    """Validate the complete schema and optionally require an evidence tag."""

    try:
        decision = LLMDecision.model_validate(data, strict=True)
    except (TypeError, ValueError):
        return False
    if evidence is None:
        return True
    evidence_tags = {
        (str(item.key).strip(), str(item.value).strip())
        for item in evidence
        if getattr(item, "key", None) and getattr(item, "value", None)
    }
    return (decision.tag.key, decision.tag.value) in evidence_tags
