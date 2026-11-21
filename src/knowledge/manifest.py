"""Hash-manifest verification for immutable M03 artifacts."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path, PurePosixPath


@dataclass(frozen=True)
class ManifestValidationResult:
    valid: bool
    checked_files: int


def _safe_relative_path(value: str) -> PurePosixPath:
    relative = PurePosixPath(value)
    if relative.is_absolute() or ".." in relative.parts or not relative.parts:
        raise ValueError(f"unsafe manifest path: {value!r}")
    return relative


def validate_manifest_files(manifest_path: Path) -> ManifestValidationResult:
    manifest_path = Path(manifest_path)
    payload = json.loads(manifest_path.read_text("utf-8"))
    files = payload.get("files")
    if not isinstance(files, list):
        raise ValueError("manifest files must be a list")
    seen: set[str] = set()
    for record in files:
        if not isinstance(record, dict):
            raise ValueError("manifest file records must be objects")
        relative = _safe_relative_path(str(record.get("path", "")))
        portable = relative.as_posix()
        if portable in seen:
            raise ValueError(f"duplicate manifest path: {portable}")
        seen.add(portable)
        path = manifest_path.parent.joinpath(*relative.parts)
        content = path.read_bytes()
        if len(content) != record.get("size_bytes"):
            raise ValueError(f"size mismatch: {portable}")
        if hashlib.sha256(content).hexdigest() != record.get("sha256"):
            raise ValueError(f"sha256 mismatch: {portable}")
    return ManifestValidationResult(valid=True, checked_files=len(files))