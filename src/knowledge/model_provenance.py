"""Pinned model snapshot verification without persisting runtime paths."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path, PurePosixPath


class ModelProvenanceError(RuntimeError):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


@dataclass(frozen=True)
class ModelVerificationResult:
    checked_files: int


def _hash(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        while True:
            block = handle.read(1024 * 1024)
            if not block:
                break
            digest.update(block)
            size += len(block)
    return digest.hexdigest(), size


def verify_model_snapshot(model_dir: Path, receipt: dict) -> ModelVerificationResult:
    model_dir = Path(model_dir).resolve()
    revision = receipt.get("revision")
    if not isinstance(revision, str) or len(revision) != 40 or any(ch not in "0123456789abcdef" for ch in revision):
        raise ModelProvenanceError("OSM_KB_MODEL_REVISION_INVALID", "model revision must be a full commit SHA")
    if receipt.get("embedding_dimension") != 384 or receipt.get("normalize_embeddings") is not True:
        raise ModelProvenanceError("OSM_KB_MODEL_CONFIG_INVALID", "model receipt configuration differs")
    records = receipt.get("files")
    if not isinstance(records, list) or not records:
        raise ModelProvenanceError("OSM_KB_MODEL_RECEIPT_INVALID", "model receipt has no files")
    seen: set[str] = set()
    for record in records:
        relative = PurePosixPath(str(record.get("path", "")))
        if relative.is_absolute() or ".." in relative.parts or not relative.parts:
            raise ModelProvenanceError("OSM_KB_MODEL_RECEIPT_INVALID", "unsafe model receipt path")
        portable = relative.as_posix()
        if portable in seen:
            raise ModelProvenanceError("OSM_KB_MODEL_RECEIPT_INVALID", "duplicate model receipt path")
        seen.add(portable)
        path = model_dir.joinpath(*relative.parts)
        if not path.is_file():
            raise ModelProvenanceError("OSM_KB_MODEL_FILE_MISSING", portable)
        digest, size = _hash(path)
        if digest != record.get("sha256") or size != record.get("size_bytes"):
            raise ModelProvenanceError("OSM_KB_MODEL_HASH_MISMATCH", portable)
    return ModelVerificationResult(checked_files=len(records))