"""Catalogue binding checks, coordinate plausibility and trusted CRS reconfirmation.

A wrong CRS declaration is rejected (``SCENARIO_CRS_IMPLAUSIBLE``) and is never
"repaired" by reprojecting from the wrong declaration. Recovery is either an
explicit reconfirmation against a versioned manifest whose file hash matches, or a
verified alternate snapshot handled by the executor (ADR-0021).
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

from pyproj import CRS, Transformer
from pyproj.exceptions import CRSError

from src.datasets import DatasetRef, DatasetRegistry, DatasetRegistryError
from src.datasets.contracts import DatasetLineage

from .contracts import CRS_PATTERN, CatalogueEntry, CrsEvidence, CrsReconfirmation, ScenarioCatalogue, ScenarioError

REPO_ROOT = Path(__file__).resolve().parents[2]
AREA_TOLERANCE = 0.01


class BindingFailure(Exception):
    def __init__(self, code: str, role: str, detail: str):
        super().__init__(detail)
        self.code, self.role, self.detail = code, role, detail


def crs_plausible(ref: DatasetRef, catalogue: ScenarioCatalogue) -> tuple[bool, str]:
    """Coordinates must fit the declared CRS area of use and intersect the study area."""
    if ref.bbox is None or ref.row_count == 0:
        return True, "bbox unavailable; emptiness is handled by the empty-result policy"
    try:
        crs = CRS.from_user_input(ref.crs)
    except CRSError:
        return False, f"{ref.crs} is not a known CRS"
    area = crs.area_of_use
    if area is None:
        return False, f"{ref.crs} has no declared area of use"
    bounds = Transformer.from_crs("EPSG:4326", crs, always_xy=True).transform_bounds(
        area.west, area.south, area.east, area.north, densify_pts=21)
    if not all(math.isfinite(value) for value in bounds):
        return False, f"{ref.crs} area of use cannot be expressed in its own coordinates"
    xmin, ymin, xmax, ymax = bounds
    tx, ty = AREA_TOLERANCE * (xmax - xmin), AREA_TOLERANCE * (ymax - ymin)
    b = ref.bbox
    if not (b[0] >= xmin - tx and b[1] >= ymin - ty and b[2] <= xmax + tx and b[3] <= ymax + ty):
        return False, f"coordinates fall outside the area of use of the declared {ref.crs}"
    study = Transformer.from_crs(catalogue.study_crs, crs, always_xy=True).transform_bounds(
        *catalogue.study_bbox, densify_pts=21)
    if b[2] < study[0] or b[0] > study[2] or b[3] < study[1] or b[1] > study[3]:
        return False, "data extent does not intersect the study area"
    return True, "plausible"


def check_binding(entry: CatalogueEntry, registry: DatasetRegistry, catalogue: ScenarioCatalogue) -> DatasetRef:
    ref = entry.dataset
    try:
        current = registry.get(ref.id, verify=True)
        if current != ref:
            raise ValueError("stale DatasetRef")
        registry.read(ref.id)
    except (DatasetRegistryError, ValueError) as error:
        raise BindingFailure("SCENARIO_DATASET_UNAVAILABLE", entry.role, "DatasetRef is missing, stale or unreadable") from error
    if ref.kind != entry.kind:
        raise BindingFailure("SCENARIO_SCHEMA_MISMATCH", entry.role, "dataset kind differs from the catalogue")
    if ref.crs != entry.declared_crs:
        raise BindingFailure("SCENARIO_CRS_MISMATCH", entry.role, "DatasetRef CRS differs from the catalogue declaration")
    plausible, detail = crs_plausible(ref, catalogue)
    if not plausible:
        raise BindingFailure("SCENARIO_CRS_IMPLAUSIBLE", entry.role, detail)
    present = {field.name for field in ref.fields}
    if not set(entry.required_fields).issubset(present):
        raise BindingFailure("SCENARIO_SCHEMA_MISMATCH", entry.role, "required fields are missing")
    return ref


def _evidence_invalid(message: str) -> ScenarioError:
    return ScenarioError("SCENARIO_CRS_EVIDENCE_INVALID", message)


def reconfirm_source_crs(
    catalogue: ScenarioCatalogue, role: str, evidence: CrsEvidence, registry: DatasetRegistry,
    *, root: Path = REPO_ROOT,
) -> ScenarioCatalogue:
    """Re-register a dataset under the source CRS recorded by a trusted, hash-linked manifest."""
    entry = catalogue.entries.get(role)
    if entry is None:
        raise ScenarioError("SCENARIO_ROLE_UNKNOWN", f"unknown catalogue role {role}")
    relative = PurePosixPath(evidence.manifest)
    if (relative.is_absolute() or "\\" in evidence.manifest or ".." in relative.parts
            or relative.parts[:2] != ("data", "fixtures") or relative.suffix != ".json"):
        raise _evidence_invalid("evidence must be a versioned manifest under data/fixtures/")
    path = Path(root) / relative
    if not path.is_file():
        raise _evidence_invalid("evidence manifest does not exist")
    payload = path.read_bytes()
    try:
        manifest = json.loads(payload)
        crs = manifest["crs"]
        item = manifest["files"][evidence.file]
        recorded = item["sha256"]
    except (ValueError, KeyError, TypeError) as error:
        raise _evidence_invalid("manifest lacks a CRS or the referenced file entry") from error
    if not isinstance(crs, str) or re.fullmatch(CRS_PATTERN, crs) is None:
        raise _evidence_invalid("manifest CRS is not an EPSG code")
    ref = entry.dataset
    if recorded != ref.content_hash.removeprefix("sha256:"):
        raise _evidence_invalid("manifest file hash does not match the dataset content")
    plausible, detail = crs_plausible(ref.model_copy(update={"crs": crs}), catalogue)
    if not plausible:
        raise _evidence_invalid(f"reconfirmed CRS is still implausible: {detail}")
    try:
        if registry.get(ref.id, verify=True) != ref:
            raise ValueError("stale DatasetRef")
        source_path = registry.resolve_path(ref.id)
    except (DatasetRegistryError, ValueError) as error:
        raise ScenarioError("SCENARIO_DATASET_UNAVAILABLE", "dataset to reconfirm is unavailable") from error
    record = CrsReconfirmation(manifest=evidence.manifest, manifest_sha256=hashlib.sha256(payload).hexdigest(),
                               file=evidence.file, crs=crs, previous_dataset_id=ref.id)
    digest = hashlib.sha256(json.dumps(record.model_dump(mode="json"), sort_keys=True,
                                       separators=(",", ":")).encode("utf-8")).hexdigest()
    reconfirmed = registry.register_file(
        source_path, kind=ref.kind, crs=crs, source=ref.source, source_version=ref.source_version,
        lineage=[DatasetLineage(operation="crs_reconfirmed", parent_ids=[ref.id],
                                timestamp=datetime.now(timezone.utc), parameters_hash=f"sha256:{digest}")],
    )
    return catalogue.with_entry(role, dataset=reconfirmed, declared_crs=crs, reconfirmation=record)
