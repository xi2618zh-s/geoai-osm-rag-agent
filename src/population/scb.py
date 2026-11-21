"""Network-isolated downloader for the allow-listed SCB population grid."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from typing import Callable, Mapping
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen
import xml.etree.ElementTree as ET

from pyproj import CRS
from pyproj.exceptions import CRSError, ProjError

from .contracts import (
    PopulationDownloadReceipt,
    SCB_CRS,
    SCB_PROPERTY_NAMES,
    SCB_SCHEMA_URL,
    SCB_WFS_BASE_URL,
    ScbPopulationGridRequest,
)


class PopulationAdapterError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class HttpResponse:
    status_code: int
    headers: Mapping[str, str]
    body: bytes


@dataclass(frozen=True)
class PopulationDownload:
    content: bytes
    receipt: PopulationDownloadReceipt


HttpGet = Callable[[str], HttpResponse]


def _format_coordinate(value: float) -> str:
    return format(value, ".15g")


def build_scb_population_grid_url(request: ScbPopulationGridRequest) -> str:
    bbox = ",".join(_format_coordinate(item) for item in request.bbox)
    params = [
        ("service", "WFS"),
        ("version", request.wfs_version),
        ("request", "GetFeature"),
        ("typeName", request.type_name),
        ("srsName", request.srs_name),
        ("bbox", f"{bbox},{request.srs_name}"),
        ("outputFormat", request.output_format),
        ("sortBy", request.sort_by),
        ("propertyName", ",".join(SCB_PROPERTY_NAMES)),
    ]
    return f"{SCB_WFS_BASE_URL}?{urlencode(params, quote_via=quote)}"


def _default_http_get(url: str) -> HttpResponse:
    request = Request(url, headers={"Accept": "application/json, text/xml"})
    with urlopen(request, timeout=60) as response:
        return HttpResponse(
            status_code=int(response.status),
            headers={str(key): str(value) for key, value in response.headers.items()},
            body=response.read(),
        )


def _header(headers: Mapping[str, str], name: str) -> str | None:
    target = name.lower()
    for key, value in headers.items():
        if key.lower() == target:
            return value
    return None


def _download(url: str, http_get: HttpGet) -> HttpResponse:
    try:
        response = http_get(url)
    except PopulationAdapterError:
        raise
    except Exception as error:
        raise PopulationAdapterError(
            "POPULATION_ADAPTER_DOWNLOAD_FAILED",
            f"SCB download failed: {error}",
        ) from error
    if response.status_code != 200:
        raise PopulationAdapterError(
            "POPULATION_ADAPTER_DOWNLOAD_FAILED",
            f"SCB returned HTTP {response.status_code}",
        )
    return response


def _validate_schema(body: bytes) -> None:
    required = {"objectid", "rutid_inspire", "beftotalt", "referenstid", "sp_geometry"}
    try:
        root = ET.fromstring(body)
        names = {
            element.attrib["name"]
            for element in root.iter()
            if "name" in element.attrib
        }
    except (ET.ParseError, UnicodeError) as error:
        raise PopulationAdapterError(
            "POPULATION_ADAPTER_RESPONSE_INVALID",
            f"SCB schema response is invalid XML: {error}",
        ) from error
    if not required.issubset(names):
        raise PopulationAdapterError(
            "POPULATION_ADAPTER_RESPONSE_INVALID",
            f"SCB schema is missing required fields: {sorted(required - names)}",
        )


def _validate_geojson(body: bytes, content_type: str | None) -> tuple[int, list[str]]:
    if content_type is None or "application/json" not in content_type.lower():
        raise PopulationAdapterError(
            "POPULATION_ADAPTER_RESPONSE_INVALID",
            "SCB feature response must use application/json",
        )
    try:
        document = json.loads(body.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as error:
        raise PopulationAdapterError(
            "POPULATION_ADAPTER_RESPONSE_INVALID",
            f"SCB feature response is invalid JSON: {error}",
        ) from error
    if not isinstance(document, dict) or document.get("type") != "FeatureCollection":
        raise PopulationAdapterError(
            "POPULATION_ADAPTER_RESPONSE_INVALID",
            "SCB feature response must be a GeoJSON FeatureCollection",
        )
    features = document.get("features")
    if not isinstance(features, list):
        raise PopulationAdapterError(
            "POPULATION_ADAPTER_RESPONSE_INVALID", "SCB features must be a list"
        )
    try:
        declared_name = document["crs"]["properties"]["name"]
        declared = CRS.from_user_input(declared_name)
        expected = CRS.from_user_input(SCB_CRS)
    except (KeyError, TypeError, CRSError, ProjError, ValueError) as error:
        raise PopulationAdapterError(
            "POPULATION_ADAPTER_RESPONSE_INVALID",
            f"SCB response CRS is missing or invalid: {error}",
        ) from error
    if not declared.equals(expected):
        raise PopulationAdapterError(
            "POPULATION_ADAPTER_RESPONSE_INVALID",
            f"SCB response CRS must be {SCB_CRS}",
        )
    object_ids: list[int] = []
    reference_dates: set[str] = set()
    for feature in features:
        if not isinstance(feature, dict) or feature.get("type") != "Feature":
            raise PopulationAdapterError(
                "POPULATION_ADAPTER_RESPONSE_INVALID", "SCB response contains a non-Feature"
            )
        properties = feature.get("properties")
        if not isinstance(properties, dict):
            raise PopulationAdapterError(
                "POPULATION_ADAPTER_RESPONSE_INVALID", "SCB feature properties are invalid"
            )
        object_id = properties.get("objectid")
        if isinstance(object_id, bool) or not isinstance(object_id, int):
            raise PopulationAdapterError(
                "POPULATION_ADAPTER_RESPONSE_INVALID", "SCB objectid must be an integer"
            )
        object_ids.append(object_id)
        reference_date = properties.get("referenstid")
        if isinstance(reference_date, str):
            reference_dates.add(reference_date)
    if object_ids != sorted(object_ids) or len(object_ids) != len(set(object_ids)):
        raise PopulationAdapterError(
            "POPULATION_ADAPTER_RESPONSE_INVALID",
            "SCB response must be uniquely sorted by objectid",
        )
    return len(features), sorted(reference_dates)


def download_scb_population_grid(
    request: ScbPopulationGridRequest,
    *,
    http_get: HttpGet = _default_http_get,
    now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
) -> PopulationDownload:
    canonical_url = build_scb_population_grid_url(request)
    schema_response = _download(SCB_SCHEMA_URL, http_get)
    _validate_schema(schema_response.body)
    feature_response = _download(canonical_url, http_get)
    content_type = _header(feature_response.headers, "Content-Type")
    feature_count, reference_dates = _validate_geojson(
        feature_response.body, content_type
    )
    receipt = PopulationDownloadReceipt(
        canonical_url=canonical_url,
        schema_url=SCB_SCHEMA_URL,
        retrieved_at=now(),
        content_type=content_type or "",
        last_modified=_header(feature_response.headers, "Last-Modified"),
        etag=_header(feature_response.headers, "ETag"),
        feature_count=feature_count,
        reference_dates=reference_dates,
        content_sha256=hashlib.sha256(feature_response.body).hexdigest(),
        schema_sha256=hashlib.sha256(schema_response.body).hexdigest(),
    )
    return PopulationDownload(content=feature_response.body, receipt=receipt)
