"""Trace context, stage timings, and compact JSON logs."""

from __future__ import annotations

from contextlib import contextmanager
import json
import logging
import time
import uuid


LOGGER = logging.getLogger("geoai")
if not LOGGER.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(message)s"))
    LOGGER.addHandler(handler)
LOGGER.setLevel(logging.INFO)
LOGGER.propagate = False


def new_trace_id() -> str:
    return uuid.uuid4().hex


def log_event(event: str, *, trace_id: str, **fields) -> None:
    record = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "event": event,
        "trace_id": trace_id,
        **fields,
    }
    LOGGER.info(json.dumps(record, ensure_ascii=False, default=str, sort_keys=True))


class QueryTrace:
    def __init__(self, trace_id: str | None = None):
        self.trace_id = trace_id or new_trace_id()
        self.started = time.perf_counter()
        self.timings_ms: dict[str, float] = {}

    @contextmanager
    def stage(self, name: str):
        started = time.perf_counter()
        try:
            yield
        except Exception as error:
            elapsed = round((time.perf_counter() - started) * 1000, 2)
            self.timings_ms[name] = elapsed
            log_event(
                "stage_failed",
                trace_id=self.trace_id,
                stage=name,
                elapsed_ms=elapsed,
                exception_type=type(error).__name__,
            )
            raise
        else:
            elapsed = round((time.perf_counter() - started) * 1000, 2)
            self.timings_ms[name] = elapsed
            log_event(
                "stage_completed",
                trace_id=self.trace_id,
                stage=name,
                elapsed_ms=elapsed,
            )

    def snapshot(self) -> dict[str, float]:
        return {
            **self.timings_ms,
            "total": round((time.perf_counter() - self.started) * 1000, 2),
        }
