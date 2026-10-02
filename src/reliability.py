"""Reusable retry and persistent clip-cache primitives."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import threading
import time
import uuid
from typing import Callable, TypeVar


T = TypeVar("T")


def retry_call(
    operation: Callable[[], T],
    *,
    retries: int,
    backoff_s: float,
    should_retry: Callable[[Exception], bool],
    on_retry: Callable[[Exception, int, float], None] | None = None,
) -> tuple[T, int]:
    """Run an operation with bounded exponential backoff.

    Returns ``(result, attempts)`` and never retries exceptions rejected by the
    supplied predicate.
    """

    attempts = 0
    while True:
        attempts += 1
        try:
            return operation(), attempts
        except Exception as error:
            if attempts > max(0, retries) or not should_retry(error):
                raise
            delay = max(0.0, backoff_s) * (2 ** (attempts - 1))
            if on_retry:
                on_retry(error, attempts, delay)
            if delay:
                time.sleep(delay)


_clip_locks: dict[str, threading.Lock] = {}
_clip_locks_guard = threading.Lock()


def clip_cache_key(source_pbf: Path, bbox, strategy: str) -> str:
    source = Path(source_pbf).resolve()
    stat = source.stat()
    payload = {
        "source": str(source),
        "source_size": stat.st_size,
        "source_mtime_ns": stat.st_mtime_ns,
        "bbox": [round(float(item), 7) for item in bbox],
        "strategy": strategy,
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True).encode("utf-8")
    ).hexdigest()[:24]


def get_or_create_clip(
    source_pbf: Path,
    bbox,
    *,
    strategy: str,
    cache_dir: Path,
    enabled: bool,
    creator: Callable[[Path], None],
) -> tuple[Path, bool]:
    """Return a valid cached clip, building it atomically on a cache miss."""

    if not enabled:
        raise ValueError("get_or_create_clip requires caching to be enabled")
    key = clip_cache_key(source_pbf, bbox, strategy)
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    destination = cache_dir / f"{key}.osm.pbf"

    with _clip_locks_guard:
        lock = _clip_locks.setdefault(key, threading.Lock())
    with lock:
        if destination.is_file() and destination.stat().st_size > 0:
            return destination, True
        temporary = cache_dir / f".{key}.{uuid.uuid4().hex}.tmp.osm.pbf"
        try:
            creator(temporary)
            if not temporary.is_file() or temporary.stat().st_size == 0:
                raise RuntimeError("clip creator did not produce a non-empty file")
            os.replace(temporary, destination)
        finally:
            temporary.unlink(missing_ok=True)
        return destination, False
