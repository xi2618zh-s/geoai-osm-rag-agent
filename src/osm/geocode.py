"""Nominatim geocoding with bounded retries and a process-local TTL cache."""

from __future__ import annotations

from dataclasses import dataclass
import threading
import time
from typing import Tuple

import requests

from src.config import (
    GEOCODE_CACHE_TTL_S,
    NOMINATIM_MAX_RETRIES,
    NOMINATIM_TIMEOUT_S,
    NOMINATIM_URL,
    NOMINATIM_USER_AGENT,
    RETRY_BACKOFF_S,
)
from src.observability import log_event, new_trace_id
from src.reliability import retry_call


@dataclass(frozen=True)
class GeocodeResult:
    center: Tuple[float, float]
    bbox: Tuple[float, float, float, float]


_cache: dict[str, tuple[float, GeocodeResult]] = {}
_cache_lock = threading.Lock()


def clear_geocode_cache() -> None:
    with _cache_lock:
        _cache.clear()


def _cache_key(place: str) -> str:
    return " ".join(place.casefold().split())


def _cached(place: str) -> GeocodeResult | None:
    key = _cache_key(place)
    with _cache_lock:
        record = _cache.get(key)
        if record and record[0] > time.monotonic():
            return record[1]
        if record:
            _cache.pop(key, None)
    return None


def _store(place: str, result: GeocodeResult) -> None:
    with _cache_lock:
        _cache[_cache_key(place)] = (
            time.monotonic() + max(0, GEOCODE_CACHE_TTL_S),
            result,
        )


def _retryable(error: Exception) -> bool:
    if isinstance(error, (requests.ConnectionError, requests.Timeout)):
        return True
    if isinstance(error, requests.HTTPError) and error.response is not None:
        return error.response.status_code == 429 or error.response.status_code >= 500
    return False


def _geocode(
    place: str,
    *,
    sleep_s: float,
    timeout: int,
    retries: int,
    backoff_s: float,
    trace_id: str,
) -> GeocodeResult:
    place = str(place).strip()
    if not place:
        raise ValueError("place must be a non-empty string")
    hit = _cached(place)
    if hit is not None:
        log_event("cache_hit", trace_id=trace_id, cache="geocode", place=place)
        return hit

    params = {"q": place, "format": "json", "limit": 1}
    headers = {"User-Agent": NOMINATIM_USER_AGENT}

    def request_once():
        response = requests.get(
            NOMINATIM_URL,
            params=params,
            headers=headers,
            timeout=timeout,
        )
        response.raise_for_status()
        return response

    def on_retry(error: Exception, attempt: int, delay: float) -> None:
        log_event(
            "dependency_retry",
            trace_id=trace_id,
            dependency="nominatim",
            attempt=attempt,
            delay_s=delay,
            exception_type=type(error).__name__,
        )

    response, _attempts = retry_call(
        request_once,
        retries=retries,
        backoff_s=backoff_s,
        should_retry=_retryable,
        on_retry=on_retry,
    )
    data = response.json()
    if not data:
        raise ValueError(f"No geocoding result for place: {place}")

    item = data[0]
    lat = float(item["lat"])
    lon = float(item["lon"])
    raw_bbox = item.get("boundingbox", [])
    if len(raw_bbox) >= 4:
        bbox = (
            float(raw_bbox[2]),
            float(raw_bbox[0]),
            float(raw_bbox[3]),
            float(raw_bbox[1]),
        )
    else:
        delta = 0.05
        bbox = (lon - delta, lat - delta, lon + delta, lat + delta)
    result = GeocodeResult(center=(lon, lat), bbox=bbox)
    _store(place, result)
    log_event("cache_miss", trace_id=trace_id, cache="geocode", place=place)
    if sleep_s:
        time.sleep(max(0.0, sleep_s))
    return result


def geocode_to_bbox(
    place: str,
    sleep_s: float = 1.0,
    timeout: int = NOMINATIM_TIMEOUT_S,
    *,
    retries: int = NOMINATIM_MAX_RETRIES,
    backoff_s: float = RETRY_BACKOFF_S,
    trace_id: str | None = None,
) -> Tuple[float, float, float, float]:
    return _geocode(
        place,
        sleep_s=sleep_s,
        timeout=timeout,
        retries=retries,
        backoff_s=backoff_s,
        trace_id=trace_id or new_trace_id(),
    ).bbox


def geocode_to_center(
    place: str,
    sleep_s: float = 1.0,
    timeout: int = NOMINATIM_TIMEOUT_S,
    *,
    retries: int = NOMINATIM_MAX_RETRIES,
    backoff_s: float = RETRY_BACKOFF_S,
    trace_id: str | None = None,
) -> Tuple[float, float]:
    return _geocode(
        place,
        sleep_s=sleep_s,
        timeout=timeout,
        retries=retries,
        backoff_s=backoff_s,
        trace_id=trace_id or new_trace_id(),
    ).center
