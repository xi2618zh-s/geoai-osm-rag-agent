"""Secure local-file implementation of the dataset registry interface."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import tempfile
import threading
from typing import Any, Optional, Protocol, runtime_checkable
import uuid

from pydantic import ValidationError

from src.gtfs.ingest import GtfsValidationError, load_gtfs_feed

from src.datasets.contracts import (
    CRS_PATTERN,
    DATASET_ID_PATTERN,
    DatasetField,
    DatasetLineage,
    DatasetRef,
)


class DatasetRegistryError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class LoadedDataset:
    ref: DatasetRef
    records: tuple[dict[str, Any], ...]


@runtime_checkable
class DatasetRegistry(Protocol):
    """Storage-neutral operations consumed by future tools and executors."""

    def register_file(
        self,
        source_path: Path,
        *,
        kind: str,
        crs: str,
        source: str,
        source_version: str,
        temporary: bool = False,
        ttl_seconds: Optional[int] = None,
        lineage: Optional[list[DatasetLineage]] = None,
    ) -> DatasetRef: ...

    def get(self, dataset_id: str, *, verify: bool = True) -> DatasetRef: ...

    def resolve_path(self, dataset_id: str, *, verify: bool = True) -> Path: ...

    def read(self, dataset_id: str) -> LoadedDataset: ...

    def list(
        self, *, kind: Optional[str] = None, source: Optional[str] = None
    ) -> list[DatasetRef]: ...

    def release(self, dataset_id: str) -> bool: ...

    def cleanup_expired(self, *, now: Optional[datetime] = None) -> list[str]: ...


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _value_type(value: Any) -> str:
    if value is None or value == "":
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return "string"


def _csv_value_type(value: str) -> str:
    text = value.strip()
    if not text:
        return "null"
    if text.lower() in {"true", "false"}:
        return "boolean"
    try:
        int(text)
        return "integer"
    except ValueError:
        pass
    try:
        number = float(text)
        return "number" if math.isfinite(number) else "string"
    except ValueError:
        return "string"


def _field_contracts(records: list[dict[str, Any]], *, csv_values: bool) -> list[DatasetField]:
    names = sorted({str(key) for record in records for key in record})
    fields: list[DatasetField] = []
    for name in names:
        observed: set[str] = set()
        nullable = False
        for record in records:
            if name not in record or record[name] is None or record[name] == "":
                nullable = True
            value_type = (
                _csv_value_type(str(record.get(name, "")))
                if csv_values
                else _value_type(record.get(name))
            )
            observed.add(value_type)
        non_null = observed.difference({"null"})
        if not non_null:
            field_type = "null"
        elif len(non_null) == 1:
            field_type = next(iter(non_null))
        elif non_null == {"integer", "number"}:
            field_type = "number"
        else:
            field_type = "mixed"
        fields.append(DatasetField(name=name, type=field_type, nullable=nullable))
    return fields


def _coordinate_pairs(coordinates: Any):
    if not isinstance(coordinates, list) or not coordinates:
        raise ValueError("geometry coordinates must be non-empty arrays")
    if (
        len(coordinates) >= 2
        and isinstance(coordinates[0], (int, float))
        and not isinstance(coordinates[0], bool)
        and isinstance(coordinates[1], (int, float))
        and not isinstance(coordinates[1], bool)
    ):
        x, y = float(coordinates[0]), float(coordinates[1])
        if not math.isfinite(x) or not math.isfinite(y):
            raise ValueError("geometry coordinates must be finite")
        yield x, y
        return
    for item in coordinates:
        yield from _coordinate_pairs(item)


def _geometry_pairs(geometry: dict[str, Any]):
    if not isinstance(geometry, dict):
        raise ValueError("feature geometry must be an object")
    geometry_type = geometry.get("type")
    if geometry_type == "GeometryCollection":
        geometries = geometry.get("geometries")
        if not isinstance(geometries, list) or not geometries:
            raise ValueError("GeometryCollection must contain geometries")
        for item in geometries:
            yield from _geometry_pairs(item)
        return
    allowed = {
        "Point",
        "MultiPoint",
        "LineString",
        "MultiLineString",
        "Polygon",
        "MultiPolygon",
    }
    if geometry_type not in allowed:
        raise ValueError(f"unsupported geometry type: {geometry_type}")
    yield from _coordinate_pairs(geometry.get("coordinates"))


def _inspect_geojson(path: Path) -> tuple[list[dict[str, Any]], list[DatasetField], list[float]]:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise DatasetRegistryError("DATASET_FORMAT_INVALID", f"Invalid GeoJSON: {error}") from error
    if not isinstance(document, dict) or document.get("type") != "FeatureCollection":
        raise DatasetRegistryError(
            "DATASET_FORMAT_INVALID", "GeoJSON root must be a FeatureCollection"
        )
    features = document.get("features")
    if not isinstance(features, list):
        raise DatasetRegistryError("DATASET_FORMAT_INVALID", "GeoJSON features must be a list")
    properties: list[dict[str, Any]] = []
    pairs: list[tuple[float, float]] = []
    try:
        for feature in features:
            if not isinstance(feature, dict) or feature.get("type") != "Feature":
                raise ValueError("every GeoJSON item must be a Feature")
            item_properties = feature.get("properties")
            if not isinstance(item_properties, dict):
                raise ValueError("feature properties must be an object")
            properties.append(item_properties)
            pairs.extend(_geometry_pairs(feature.get("geometry")))
    except ValueError as error:
        raise DatasetRegistryError("DATASET_FORMAT_INVALID", f"Invalid GeoJSON: {error}") from error
    bbox = (
        [
            min(item[0] for item in pairs),
            min(item[1] for item in pairs),
            max(item[0] for item in pairs),
            max(item[1] for item in pairs),
        ]
        if pairs
        else None
    )
    return features, _field_contracts(properties, csv_values=False), bbox


def _inspect_csv(path: Path) -> tuple[list[dict[str, str]], list[DatasetField], None]:
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as stream:
            reader = csv.DictReader(stream)
            headers = reader.fieldnames
            if not headers or any(header is None or not header.strip() for header in headers):
                raise ValueError("CSV requires non-empty headers")
            if len(headers) != len(set(headers)):
                raise ValueError("CSV headers must be unique")
            rows = list(reader)
            if any(None in row or any(value is None for value in row.values()) for row in rows):
                raise ValueError("CSV rows must match the header width")
    except (OSError, UnicodeError, csv.Error, ValueError) as error:
        raise DatasetRegistryError("DATASET_FORMAT_INVALID", f"Invalid CSV: {error}") from error
    return rows, _field_contracts(rows, csv_values=True), None


class LocalDatasetRegistry:
    """A registry whose opaque IDs resolve only inside its controlled root."""

    def __init__(self, root: Path):
        self.root = Path(root).expanduser().resolve()
        self.datasets_dir = self.root / "datasets"
        self.staging_dir = self.root / ".staging"
        self.datasets_dir.mkdir(parents=True, exist_ok=True)
        self.staging_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()

    @staticmethod
    def _validate_id(dataset_id: str) -> str:
        if not isinstance(dataset_id, str) or re.fullmatch(DATASET_ID_PATTERN, dataset_id) is None:
            raise DatasetRegistryError("DATASET_ID_INVALID", "Invalid dataset ID")
        return dataset_id

    def _dataset_dir(self, dataset_id: str) -> Path:
        dataset_id = self._validate_id(dataset_id)
        path = (self.datasets_dir / dataset_id).resolve()
        if path.parent != self.datasets_dir:
            raise DatasetRegistryError("DATASET_STORAGE_VIOLATION", "Dataset path escaped registry root")
        return path

    @staticmethod
    def _storage_uri(dataset_id: str, format_name: str) -> str:
        return f"registry://datasets/{dataset_id}/data.{format_name}"

    @staticmethod
    def _inspect(path: Path, format_name: str):
        if format_name == "geojson":
            return _inspect_geojson(path)
        if format_name == "csv":
            return _inspect_csv(path)
        if format_name == "gtfs":
            feed = load_gtfs_feed(path)
            records = list(feed.stops)
            return records, _field_contracts(records, csv_values=True), list(feed.bbox)
        raise DatasetRegistryError("DATASET_FORMAT_UNSUPPORTED", f"Unsupported format: {format_name}")

    def register_file(
        self,
        source_path: Path,
        *,
        kind: str,
        crs: str,
        source: str,
        source_version: str,
        temporary: bool = False,
        ttl_seconds: Optional[int] = None,
        lineage: Optional[list[DatasetLineage]] = None,
    ) -> DatasetRef:
        path = Path(source_path).expanduser().resolve()
        if not path.is_file():
            raise DatasetRegistryError("DATASET_SOURCE_MISSING", f"Source file not found: {path.name}")
        if not isinstance(crs, str) or not crs.strip():
            raise DatasetRegistryError("DATASET_CRS_MISSING", "Dataset CRS is required")
        if re.fullmatch(CRS_PATTERN, crs.strip()) is None:
            raise DatasetRegistryError("DATASET_CRS_INVALID", "CRS must use EPSG:<code>")
        suffix = path.suffix.lower()
        formats = {".geojson": "geojson", ".csv": "csv", ".zip": "gtfs", ".gtfs": "gtfs"}
        if suffix not in formats:
            raise DatasetRegistryError("DATASET_FORMAT_UNSUPPORTED", f"Unsupported file extension: {suffix}")
        format_name = formats[suffix]
        if (kind, format_name) not in {("vector", "geojson"), ("table", "csv"), ("network", "gtfs")}:
            raise DatasetRegistryError(
                "DATASET_KIND_FORMAT_MISMATCH",
                f"{format_name} cannot be registered as {kind}",
            )
        if format_name == "gtfs" and crs.strip() != "EPSG:4326":
            raise DatasetRegistryError("DATASET_CRS_INVALID", "GTFS stops require EPSG:4326")
        if temporary:
            if not isinstance(ttl_seconds, int) or isinstance(ttl_seconds, bool) or not 1 <= ttl_seconds <= 604800:
                raise DatasetRegistryError(
                    "DATASET_TTL_INVALID", "Temporary datasets require ttl_seconds in 1..604800"
                )
        elif ttl_seconds is not None:
            raise DatasetRegistryError(
                "DATASET_TTL_INVALID", "Persistent datasets must not define ttl_seconds"
            )

        with self._lock:
            dataset_id = self._new_id()
            stage = Path(tempfile.mkdtemp(prefix=f"{dataset_id}_", dir=self.staging_dir))
            final_dir = self._dataset_dir(dataset_id)
            try:
                data_path = stage / f"data.{format_name}"
                shutil.copyfile(path, data_path)
                records, fields, bbox = self._inspect(data_path, format_name)
                created_at = datetime.now(timezone.utc)
                expires_at = (
                    created_at + timedelta(seconds=ttl_seconds)
                    if temporary and ttl_seconds is not None
                    else None
                )
                ref = DatasetRef(
                    id=dataset_id,
                    kind=kind,
                    format=format_name,
                    storage_uri=self._storage_uri(dataset_id, format_name),
                    crs=crs.strip(),
                    bbox=bbox,
                    row_count=len(records),
                    source=source,
                    source_version=source_version,
                    created_at=created_at,
                    content_hash=f"sha256:{_sha256(data_path)}",
                    size_bytes=data_path.stat().st_size,
                    fields=fields,
                    lineage=(
                        list(lineage)
                        if lineage is not None
                        else [
                            DatasetLineage(
                                operation="register",
                                timestamp=created_at,
                            )
                        ]
                    ),
                    lifecycle="temporary" if temporary else "persistent",
                    expires_at=expires_at,
                )
                (stage / "metadata.json").write_text(
                    ref.model_dump_json(indent=2), encoding="utf-8"
                )
                os.replace(stage, final_dir)
                return ref
            except DatasetRegistryError:
                raise
            except GtfsValidationError as error:
                raise DatasetRegistryError(error.code, str(error)) from error
            except (OSError, ValidationError, ValueError) as error:
                raise DatasetRegistryError(
                    "DATASET_METADATA_INVALID", f"Could not register dataset: {error}"
                ) from error
            finally:
                if stage.exists():
                    shutil.rmtree(stage)

    def _new_id(self) -> str:
        for _attempt in range(10):
            dataset_id = f"ds_{uuid.uuid4().hex}"
            if not (self.datasets_dir / dataset_id).exists():
                return dataset_id
        raise DatasetRegistryError("DATASET_ID_COLLISION", "Could not allocate a unique dataset ID")

    def _load_ref(self, dataset_id: str) -> DatasetRef:
        directory = self._dataset_dir(dataset_id)
        if not directory.is_dir():
            raise DatasetRegistryError("DATASET_NOT_FOUND", f"Dataset not found: {dataset_id}")
        metadata_path = directory / "metadata.json"
        try:
            metadata_text = metadata_path.read_text(encoding="utf-8")
            raw = json.loads(metadata_text)
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            raise DatasetRegistryError(
                "DATASET_METADATA_INVALID", f"Dataset metadata is unreadable: {dataset_id}"
            ) from error
        if not isinstance(raw, dict) or raw.get("id") != dataset_id:
            raise DatasetRegistryError("DATASET_METADATA_INVALID", "Dataset metadata ID mismatch")
        expected_uri = self._storage_uri(dataset_id, str(raw.get("format", "")))
        if raw.get("storage_uri") != expected_uri:
            raise DatasetRegistryError(
                "DATASET_STORAGE_VIOLATION", "Dataset storage URI is not canonical"
            )
        try:
            return DatasetRef.model_validate_json(metadata_text)
        except ValidationError as error:
            raise DatasetRegistryError(
                "DATASET_METADATA_INVALID", f"Dataset metadata is invalid: {dataset_id}"
            ) from error

    def _content_path(self, ref: DatasetRef) -> Path:
        path = (self._dataset_dir(ref.id) / f"data.{ref.format}").resolve()
        if path.parent != self._dataset_dir(ref.id):
            raise DatasetRegistryError("DATASET_STORAGE_VIOLATION", "Dataset content escaped registry root")
        return path

    def get(self, dataset_id: str, *, verify: bool = True) -> DatasetRef:
        with self._lock:
            ref = self._load_ref(dataset_id)
            path = self._content_path(ref)
            if not path.is_file():
                raise DatasetRegistryError(
                    "DATASET_CONTENT_MISSING", f"Dataset content is missing: {dataset_id}"
                )
            if verify:
                actual_hash = f"sha256:{_sha256(path)}"
                if actual_hash != ref.content_hash or path.stat().st_size != ref.size_bytes:
                    raise DatasetRegistryError(
                        "DATASET_CONTENT_HASH_MISMATCH",
                        f"Dataset content does not match metadata: {dataset_id}",
                    )
            return ref

    def resolve_path(self, dataset_id: str, *, verify: bool = True) -> Path:
        ref = self.get(dataset_id, verify=verify)
        return self._content_path(ref)

    def read(self, dataset_id: str) -> LoadedDataset:
        with self._lock:
            ref = self.get(dataset_id, verify=True)
            records, _fields, _bbox = self._inspect(self._content_path(ref), ref.format)
            return LoadedDataset(ref=ref, records=tuple(records))

    def list(self, *, kind: Optional[str] = None, source: Optional[str] = None) -> list[DatasetRef]:
        with self._lock:
            refs = [
                self.get(path.name)
                for path in self.datasets_dir.iterdir()
                if path.is_dir() and re.fullmatch(DATASET_ID_PATTERN, path.name)
            ]
        if kind is not None:
            refs = [item for item in refs if item.kind == kind]
        if source is not None:
            refs = [item for item in refs if item.source == source]
        return sorted(refs, key=lambda item: (item.created_at, item.id))

    def release(self, dataset_id: str) -> bool:
        with self._lock:
            ref = self.get(dataset_id)
            if ref.lifecycle != "temporary":
                raise DatasetRegistryError(
                    "DATASET_NOT_TEMPORARY", "Only temporary datasets can be released"
                )
            shutil.rmtree(self._dataset_dir(dataset_id))
            return True

    def cleanup_expired(self, *, now: Optional[datetime] = None) -> list[str]:
        current = now or datetime.now(timezone.utc)
        if current.tzinfo is None or current.utcoffset() is None:
            raise DatasetRegistryError("DATASET_TIME_INVALID", "Cleanup time must be timezone-aware")
        removed: list[str] = []
        with self._lock:
            for ref in self.list():
                if (
                    ref.lifecycle == "temporary"
                    and ref.expires_at is not None
                    and ref.expires_at <= current
                ):
                    self.release(ref.id)
                    removed.append(ref.id)
        return sorted(removed)
