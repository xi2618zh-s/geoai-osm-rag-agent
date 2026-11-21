"""Explicit, fixed-version download boundary for official GTFS Sverige 2."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
import uuid
import zipfile


SOURCE_URL = (
    "https://data.samtrafiken.se/trafiklab/gtfs-sverige-2/2025/10/"
    "sweden-20251004.zip"
)
SOURCE_VERSION = "gtfs-sverige-2-20251004"
SOURCE_LICENSE = "CC0-1.0"
SOURCE_LICENSE_PAGE = "https://www.trafiklab.se/api/gtfs-datasets/gtfs-sverige-2/"
EXPECTED_SIZE_BYTES = 47_699_395
EXPECTED_ETAG = '"68e1dc39-2d7d5c3"'
EXPECTED_SHA256 = "12a4a7169d8423641da7f15125092423f6f9834f3b70725719b1ce147eb48f69"
MAX_DOWNLOAD_BYTES = 80_000_000


class GtfsSourceError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def download_frozen_source(
    output_dir: Path,
    *,
    expected_sha256: str | None = None,
    opener=None,
) -> dict:
    """Fetch once into a sibling staging directory, then publish atomically."""
    output = Path(output_dir).resolve()
    if output.exists():
        raise GtfsSourceError("GTFS_SOURCE_EXISTS", "source output already exists")
    output.parent.mkdir(parents=True, exist_ok=True)
    staging = output.parent / f".staging-{output.name}-{uuid.uuid4().hex[:10]}"
    staging.mkdir()
    if expected_sha256 is not None and expected_sha256 != EXPECTED_SHA256:
        raise GtfsSourceError("GTFS_SOURCE_HASH_MISMATCH", "expected SHA-256 differs from frozen version")
    opener = opener or urlopen
    try:
        request = Request(SOURCE_URL, headers={"User-Agent": "GeoAI-M06a/1.0"})
        try:
            response = opener(request, timeout=60)
        except HTTPError as error:
            raise GtfsSourceError(
                "GTFS_HTTP_RESPONSE_INVALID", f"official source returned HTTP {error.code}"
            ) from error
        except (URLError, TimeoutError, OSError) as error:
            raise GtfsSourceError("GTFS_DOWNLOAD_FAILED", str(error)) from error

        with response:
            status = getattr(response, "status", None)
            if status != 200:
                raise GtfsSourceError("GTFS_HTTP_RESPONSE_INVALID", f"HTTP {status}")
            final_url = response.geturl()
            if final_url != SOURCE_URL:
                raise GtfsSourceError("GTFS_HTTP_RESPONSE_INVALID", "unexpected redirect target")
            content_type = response.headers.get("Content-Type", "")
            if content_type.split(";", 1)[0].strip().lower() != "application/zip":
                raise GtfsSourceError("GTFS_HTTP_RESPONSE_INVALID", "unexpected Content-Type")
            etag = response.headers.get("ETag")
            if etag is not None and etag != EXPECTED_ETAG:
                raise GtfsSourceError("GTFS_HTTP_RESPONSE_INVALID", "historical archive ETag changed")
            declared_size = response.headers.get("Content-Length")
            if declared_size is not None and not declared_size.isdecimal():
                raise GtfsSourceError("GTFS_HTTP_RESPONSE_INVALID", "invalid Content-Length")
            if declared_size is not None and int(declared_size) != EXPECTED_SIZE_BYTES:
                raise GtfsSourceError("GTFS_HTTP_RESPONSE_INVALID", "historical archive size changed")
            archive_path = staging / "source.zip"
            digest = hashlib.sha256()
            total = 0
            with archive_path.open("xb") as target:
                while True:
                    try:
                        block = response.read(1024 * 1024)
                    except (URLError, TimeoutError, OSError) as error:
                        raise GtfsSourceError("GTFS_DOWNLOAD_FAILED", str(error)) from error
                    if not block:
                        break
                    total += len(block)
                    if total > MAX_DOWNLOAD_BYTES:
                        raise GtfsSourceError("GTFS_HTTP_RESPONSE_INVALID", "response too large")
                    target.write(block)
                    digest.update(block)
            if total != EXPECTED_SIZE_BYTES:
                raise GtfsSourceError("GTFS_HTTP_RESPONSE_INVALID", "response byte count changed")
            actual_hash = digest.hexdigest()
            if actual_hash != EXPECTED_SHA256:
                raise GtfsSourceError("GTFS_SOURCE_HASH_MISMATCH", "download SHA-256 mismatch")
            try:
                with zipfile.ZipFile(archive_path) as archive:
                    if not archive.namelist():
                        raise zipfile.BadZipFile("empty archive")
            except zipfile.BadZipFile as error:
                raise GtfsSourceError("GTFS_ARCHIVE_INVALID", str(error)) from error
            receipt = {
                "schema_version": "1.0",
                "source_url": SOURCE_URL,
                "final_url": final_url,
                "source_version": SOURCE_VERSION,
                "license_id": SOURCE_LICENSE,
                "license_page": SOURCE_LICENSE_PAGE,
                "retrieved_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                "http_status": status,
                "content_type": content_type,
                "etag": etag,
                "last_modified": response.headers.get("Last-Modified"),
                "content_length": total,
                "sha256": actual_hash,
                "archive_file": "source.zip",
            }
            (staging / "receipt.json").write_text(
                json.dumps(receipt, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n",
                encoding="utf-8",
            )
        os.replace(staging, output)
        return receipt
    finally:
        if staging.exists():
            shutil.rmtree(staging)

