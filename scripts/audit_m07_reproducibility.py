"""Audit frozen M07 planning receipts against deterministic recomputation."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from benchmarks.run_m07_planning import evaluate


def main() -> None:
    for version in ("v1", "v2"):
        cases = Path(f"benchmarks/data/dev/m07_plan_cases_{version}.jsonl")
        receipt = Path(f"benchmarks/results/m07/dev/m07_plan_eval_{version}.json")
        rebuilt = (json.dumps(evaluate(cases), ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")
        published = receipt.read_bytes()
        if rebuilt != published:
            raise SystemExit(f"[FAIL] M07 {version} receipt differs from recomputation")
        print(f"[PASS] M07 {version}: {hashlib.sha256(published).hexdigest()}")


if __name__ == "__main__":
    main()
