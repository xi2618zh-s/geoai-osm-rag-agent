"""Canonical JSON and SHA-256 helpers for M03 artifacts."""

from __future__ import annotations

from enum import Enum
import hashlib
import json
from typing import Any, Iterable

from pydantic import BaseModel


def _json_value(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, Enum):
        return value.value
    return value


def canonical_json_bytes(value: Any) -> bytes:
    payload = json.dumps(
        _json_value(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return (payload + "\n").encode("utf-8")


def canonical_jsonl_bytes(values: Iterable[Any]) -> bytes:
    rows = []
    for value in values:
        rows.append(
            json.dumps(
                _json_value(value),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
        )
    return (("\n".join(rows) + "\n") if rows else "").encode("utf-8")


def sha256_hex(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()