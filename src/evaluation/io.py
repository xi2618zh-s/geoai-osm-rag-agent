"""Strict JSONL loaders for benchmark inputs."""

from __future__ import annotations

from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from .contracts import BenchmarkPrediction, BenchmarkTask


ModelT = TypeVar("ModelT", bound=BaseModel)


def _load_jsonl(path: Path, model: type[ModelT], id_field: str, label: str) -> list[ModelT]:
    rows: list[ModelT] = []
    seen: set[str] = set()
    with Path(path).open("r", encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, 1):
            if not line.strip():
                continue
            try:
                row = model.model_validate_json(line)
            except ValidationError as error:
                raise ValueError(f"Invalid {label} at line {line_number}: {error}") from error
            identifier = getattr(row, id_field)
            if identifier in seen:
                raise ValueError(f"Duplicate benchmark {id_field}: {identifier}")
            seen.add(identifier)
            rows.append(row)
    if not rows:
        raise ValueError(f"Benchmark {label} file is empty: {path}")
    return rows


def load_tasks(path: Path) -> list[BenchmarkTask]:
    return _load_jsonl(path, BenchmarkTask, "task_id", "task")


def load_predictions(path: Path) -> list[BenchmarkPrediction]:
    return _load_jsonl(path, BenchmarkPrediction, "task_id", "prediction")

