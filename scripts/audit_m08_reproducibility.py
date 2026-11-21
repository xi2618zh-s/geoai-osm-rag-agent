"""Audit the frozen M08 fault receipt by deterministic recomputation."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from benchmarks.run_m08_faults import evaluate

RECEIPT = Path("benchmarks/results/m08/dev/m08_fault_eval_v1.json")


def main() -> None:
    expected = (json.dumps(evaluate(), ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")
    actual = RECEIPT.read_bytes()
    if expected != actual:
        raise SystemExit("[FAIL] M08 receipt differs from recomputation")
    print(f"[PASS] M08 v1: {hashlib.sha256(actual).hexdigest()}")


if __name__ == "__main__":
    main()
