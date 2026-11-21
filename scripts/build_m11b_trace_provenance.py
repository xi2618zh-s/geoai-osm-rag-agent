"""Publish and audit M04 call-level provenance for the frozen M11b dev tasks."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from scripts.build_m11b_dataset import ROOT, _json_bytes, _sha, _tasks_bytes

TRACES = ROOT / "benchmarks/data/dev/m11b_tool_traces_v1.jsonl"
RECEIPT = ROOT / "benchmarks/data/dev/m11b_tool_traces_receipt_v1.json"
TASKS = ROOT / "benchmarks/data/dev/m11b_tasks_v1.jsonl"
MANIFEST = ROOT / "benchmarks/data/dev/m11b_manifest_v1.json"


def _expected_bytes():
    rows = []
    task_bytes, manifest_bytes = _tasks_bytes(rows)
    if TASKS.read_bytes() != task_bytes or MANIFEST.read_bytes() != manifest_bytes:
        raise ValueError("frozen M11b tasks differ from M04 reference rebuild")
    if len(rows) != 160:
        raise ValueError("M11b tool trace count differs from task count")
    trace_bytes = b"".join(_json_bytes(row) for row in rows)
    receipt = {
        "schema_version": "1.0", "data_version": "m11b-public-dev-v1",
        "task_count": 160, "m04_supported_count": 144,
        "tasks_sha256": _sha(task_bytes), "tool_traces_sha256": _sha(trace_bytes),
        "source": "Actual AnalysisToolRegistry.invoke calls; DatasetRef IDs and timestamps omitted",
        "license": "CC0-1.0",
    }
    return trace_bytes, _json_bytes(receipt)


def build_traces():
    if TRACES.exists() or RECEIPT.exists():
        raise FileExistsError("M11b tool trace release already exists")
    traces, receipt = _expected_bytes()
    with TRACES.open("xb") as stream:
        stream.write(traces)
    with RECEIPT.open("xb") as stream:
        stream.write(receipt)
    return TRACES, RECEIPT


def audit_traces() -> bool:
    traces, receipt = _expected_bytes()
    if TRACES.read_bytes() != traces or RECEIPT.read_bytes() != receipt:
        raise ValueError("M11b tool trace provenance differs from M04 rebuild")
    return True


if __name__ == "__main__":
    traces, receipt = build_traces()
    print(f"Published M11b M04 call hashes: {traces} and {receipt}")
